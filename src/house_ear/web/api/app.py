"""Control-plane API. The browser never sees a secret."""
from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from ... import __version__, secrets
from ...audio.ack import ack_wav_path
from ...audio.ack_render import render_ack_wav, render_filler_pcm
from ...audio.client import AudioPlaneSupervisor
from ...audio.kws import KwsUnavailable, keyword_meta, model_files, write_keywords_file
from ...audio.protocol import PlayFile, StopAudio
from ...config.schema import CONFIG_VERSION, LED_STATES, Config, DeviceConfig
from ...config.store import ConfigError, load_config, redacted, save_config
from ...device.registry import build_adapters
from ...doctor import run_doctor
from ...log_setup import LOG_FILE_NAME, tail_lines
from ...music import MusicService, NavidromeSource, PlayHistory
from ...music.names import AliasStore, NameResolver
from ...music.tools import build_music_tools
from ...session import phrasebook, phrases, prompts, rules
from ...session.ack import ensure_ack_wav
from ...session.briefing import Briefing
from ...session.memory import MemoryStore
from ...session.text import strip_wake
from ...session.turn import DirectSpeech, TurnRunner
from ...session.vendor import VendorWindow
from ...tools import build_local_tools, build_scheduler
from ...tools.contract import InteractionSpec, Permission, ToolResult, ToolSpec
from ...tools.ha import HaBridge
from ...tools.memory import build_memory_tools
from ...tools.registry import ToolRegistry
from ...trace import TraceLog, TurnTrace

START = time.time()
log = logging.getLogger(__name__)
# Keys the wizard's "fill the keys" step requires. 讯飞 TTS is optional: without
# XFYUN_* the TTS chain falls back to SiliconFlow, which needs HOUSE_EAR_TTS_KEY only.

_DEFAULT_WEB_DIST = Path(__file__).resolve().parents[3].parent / "web" / "dist"
WEB_DIST = Path(os.environ.get("HOUSE_EAR_WEB_DIR", _DEFAULT_WEB_DIST))

FALLBACK = """<!doctype html><html><head><meta charset="utf-8"><title>house-ear</title>
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;max-width:640px;margin:12vh auto;padding:0 24px;color:#111}
code{background:#f4f4f5;padding:2px 6px;border-radius:4px}</style></head>
<body><h1>house-ear</h1><p>Control plane is running. The console is not built yet:</p>
<p><code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code></p>
<p>API: <code>/health</code>, <code>/api/v1/doctor</code>, <code>/api/v1/config</code></p></body></html>"""


def _light(app: FastAPI, effect: str, repaint: bool = False) -> None:
    """Paint the ring without blocking the caller.

    The shell request shares the websocket with the audio stream; the old build measured
    a 3 s resume delay from awaiting one animation, so this is always a task.
    """
    supervisor = getattr(app.state, "audio", None)
    live = supervisor.client if supervisor is not None else None
    if live is None:
        return
    log.info("led: %s%s", effect, " (repaint)" if repaint else "")
    task = asyncio.create_task(live.set_led(effect, repaint=repaint), name="led")
    tasks = getattr(app.state, "led_tasks", None)
    if tasks is not None:
        tasks.add(task)
        task.add_done_callback(tasks.discard)


def _light_after_audio(app: FastAPI, audio_bytes: int, rate: int, effect: str = "listening") -> None:
    """Paint `effect` when the audio just queued has finished playing.

    run() returns when the last chunk is queued, not played; output_owner is released
    at the same moment (the plane frees the speaker when its queue empties), so neither
    is a "the answer is over" signal. The duration is exact: PCM bytes / 2 / rate. The
    three repaints after it match what the vendor LED service needs to stay repainted.
    """
    seconds = max(0.0, audio_bytes / 2 / max(1, rate))

    async def run() -> None:
        await asyncio.sleep(seconds + 0.6)
        _light(app, effect)
        if effect != "listening":
            return
        for _ in range(3):
            await asyncio.sleep(1.2)
            _light(app, effect, repaint=True)

    asyncio.create_task(run(), name="led-after-audio")


def _speaking(app: FastAPI, live: object):
    """A speak callable that turns the ring cyan on the first block of a turn."""
    turned = {"on": False}

    async def say(pcm: bytes, **kwargs: object) -> None:
        if not turned["on"]:
            turned["on"] = True
            _light(app, "speaking")
        await live.speak(pcm, **kwargs)  # type: ignore[attr-defined]

    return say


def _direct_speech(name: str, transcript: str, output: object) -> DirectSpeech | None:
    """Tool results that are read out verbatim instead of being re-answered (D1c ④).

    Today that is the daily briefing: `tools/info.py:news()` returns a `Briefing`,
    the runner speaks `speech_text` and skips the second model round, and history
    gets the user line plus the script (old build `@@BRIEF`, docs/d1c-notes.md §7.2).
    The model only ever sees `model_notice()`, never the script.
    """
    if not isinstance(output, Briefing):
        return None
    return DirectSpeech(
        text=output.speech_text,
        notice=output.model_notice(),
        history=output.history_messages(transcript),
    )


async def _probe_host(host: str) -> dict:
    """TCP connect + SSH banner. A VPN/TUN answers the connect for any address, so the
    banner is what separates "a speaker is there" from "something answered"."""
    started = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, 22), timeout=2.0)
    except (TimeoutError, OSError) as exc:
        return {"host": host, "usable": False,
                "ssh": {"port_open": False, "banner": False, "error": type(exc).__name__},
                "hint": "SSH 端口不通：确认音箱在同一网段、已开 SSH"}
    banner = False
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        banner = line.startswith(b"SSH-")
    except (TimeoutError, OSError):
        banner = False
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    ssh = {"port_open": True, "banner": banner,
           "ms": round((time.perf_counter() - started) * 1000)}
    if banner:
        return {"host": host, "usable": True, "ssh": ssh, "hint": ""}
    return {"host": host, "usable": False, "ssh": ssh,
            "hint": "端口通了但没有 SSH banner（VPN/代理可能假阳性），换一个地址登记吧"}


def _context_provider(app: FastAPI, ha: HaBridge | None):
    """Per-turn prompt extras: the entity list and, over music, the short-answer rule."""
    ha_ctx = _ha_context(ha) if ha is not None else (lambda: "")

    def provider() -> str:
        parts = [ha_ctx(), _memory_context(app)]
        service = getattr(app.state, "music", None)
        if service is not None and service.state in ("playing", "paused"):
            parts.append(prompts.MUSIC_HINT)
        return "\n".join(p for p in parts if p)

    return provider


def _ha_context(ha: HaBridge):
    """The runner's hook is sync; the fetch is async. A background task keeps
    the entity block warm (5 min cadence), the hook hands over the text."""
    import asyncio as _aio

    state = {"text": ""}

    async def _refresh() -> None:
        while True:
            with contextlib.suppress(Exception):
                state["text"] = await ha.entity_context()
            await _aio.sleep(300)

    _aio.get_running_loop().create_task(_refresh(), name="ha-context")
    return lambda: state["text"]


def _music_sender(app: FastAPI):
    """The music service's way out: always the *current* plane client.

    The plane restarts and reconnects; a sender that captured the first client
    would send music into a dead socket forever.
    """

    async def send(msg: object) -> None:
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise RuntimeError("no audio plane connected")
        await client.send(msg)  # type: ignore[arg-type]

    return send


async def _never_sender(_msg: object) -> None:
    """Placeholder until the app exists; replaced by `_music_sender` right after
    FastAPI() is built. A call before that would be a wiring bug worth an exception."""

    class _NotWired(RuntimeError):
        pass

    raise _NotWired("music sender used before the app was built")


def _build_panels(music_enabled: bool, music_source: str) -> list[dict]:
    """The PanelSpec table (docs/console.md). Expressions are deliberately tiny:
    `$.path` lookups, `map` for enum wording, `enabled_when: "$.x=v"` equality."""
    return [
        {
            "id": "overview", "title": "概览", "icon": "◎", "group": "status", "order": 10,
            "enabled": True, "enabled_reason": "",
            "source": "/health", "extra_sources": {"audio": "/api/v1/audio"},
            "refresh_s": 5,
            "blocks": [
                {"type": "say", "title": "试一试", "href": "/api/v1/debug/say",
                 "placeholder": "现在几点？",
                 "hint": "打字等于说一句：同一套回合、工具、TTS 和播放。每次都是新的一句（不带上下文）。"},
                {"type": "stats", "cards": [
                    {"label": "服务", "value": "$.status", "tone": {"ok": "ok"}},
                    {"label": "运行", "value": "$.uptime_s", "unit": "s"},
                    {"label": "设备", "value": "$.devices"},
                    {"label": "音频面", "value": "$.audio_plane",
                     "map": {"true": "在线", "false": "离线"},
                     "tone": {"true": "ok", "false": "warn"}},
                ]},
                {"type": "stats", "source": "audio", "title": "音频面", "cards": [
                    {"label": "唤醒次数", "value": "$.metrics.wake_count"},
                    {"label": "当前占用", "value": "$.metrics.output_owner",
                     "map": {"": "空闲", "cue": "提示音", "dialogue": "对话",
                             "reminder": "提醒", "music": "音乐"}},
                    {"label": "回声抑制", "value": "$.metrics.aec_erle_db", "unit": "dB",
                     "map": {"null": "—"}},
                ]},
                {"type": "actions", "title": "操作", "buttons": [
                    {"id": "stop", "label": "停止出声", "method": "POST",
                     "href": "/api/v1/debug/play", "body": {"stop": True}, "refresh": True},
                ]},
            ],
        },
        {
            "id": "devices", "title": "设备", "icon": "⌸", "group": "status", "order": 20,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/devices", "refresh_s": 10,
            "blocks": [
                {"type": "list", "title": "在线设备", "items": "$",
                 "item_title": "$.room · $.id",
                 "item_sub": "$.host · $.adapter · $.model · $.status"},
                {"type": "volume", "title": "设备音量", "href": "/api/v1/device/volume",
                 "label": "mysoftvol（设备原生刻度 0–255）", "min": 1, "max": 255,
                 "note": "读的是设备自己的混音器，不是配置里的 devices[].volume；厂商会自己改这个值，"
                         "所以这里显示的永远是设备实际音量。"},
            ],
        },
        {
            "id": "turns", "title": "对话", "icon": "☰", "group": "status", "order": 25,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/turns?limit=20", "refresh_s": 5,
            "blocks": [
                {"type": "table", "title": "最近对话", "items": "$.turns",
                 "empty": "还没有对话",
                 "columns": [
                    {"key": "ts", "label": "时间", "width": "70px"},
                    {"key": "transcript", "label": "听到的"},
                    {"key": "reply", "label": "回答", "truncate": 60},
                    {"key": "tools", "label": "工具", "width": "120px", "join": True},
                 ]},
            ],
        },
        {
            "id": "devices-admin", "title": "设备管理", "icon": "⚙", "group": "config",
            "order": 42, "enabled": True, "enabled_reason": "",
            "source": "/api/v1/devices", "refresh_s": 0, "blocks": [],
            "component": "device-manager",
        },
        {
            "id": "phrases", "title": "话术", "icon": "Aa", "group": "config", "order": 45,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/phrases", "refresh_s": 0,
            "blocks": [],
            # The escape hatch (docs/console.md §2.5): editing 49 templates with slot
            # chips, dirty tracking and per-key reset is a sequence, not a block set.
            "component": "phrasebook-editor",
        },
        {
            "id": "mcp", "title": "MCP / HA", "icon": "⌗", "group": "config", "order": 46,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/mcp", "refresh_s": 5, "blocks": [],
            "component": "mcp-manager",
        },
        {
            "id": "logs", "title": "日志", "icon": "≡", "group": "system", "order": 60,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/logs?lines=300", "refresh_s": 2,
            "blocks": [
                # The plane's level line lands every 2 s and would drown the 300-line
                # tail; the reader can reveal it with one checkbox.
                {"type": "log", "follow": True, "filter": True,
                 "hide": "[level] voice peak", "hide_label": "隐藏 level 噪声"},
            ],
        },
        {
            "id": "memory", "title": "记忆", "icon": "◇", "group": "living", "order": 35,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/memory", "refresh_s": 0,
            "blocks": [
                {"type": "memory", "title": "记住的事",
                 "empty": "还没有记住任何事。语音说「记住……」，或在这里添加。"},
            ],
        },
        {
            "id": "settings", "title": "设置", "icon": "⚙", "group": "config", "order": 40,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/config", "refresh_s": 0,
            "blocks": [
                {"type": "form", "section": "assistant", "title": "助手",
                 "apply": "下一轮生效", "fields": [
                    {"key": "name", "label": "名字", "kind": "text", "hint": "1–12 字"},
                    {"key": "persona", "label": "人设", "kind": "textarea",
                     "hint": "追加在内置提示词后面。只写身份，别写「说话要简短」这类规则。"},
                    {"key": "system_prompt", "label": "系统提示词", "kind": "textarea",
                     "hint": "整段替换内置提示词。留空 = 用内置的（推荐留空）。"},
                    {"key": "reply_max_chars", "label": "日常回答上限", "kind": "number",
                     "min": 10, "max": 300, "unit": "字"},
                    {"key": "reply_max_chars_search", "label": "查资料时上限", "kind": "number",
                     "min": 40, "max": 600, "unit": "字"},
                    {"key": "reply_max_chars_long", "label": "要求展开时上限", "kind": "number",
                     "min": 100, "max": 2000, "unit": "字"},
                ]},
                {"type": "form", "section": "session", "title": "会话",
                 "apply": "下一轮生效", "fields": [
                    {"key": "timeout_s", "label": "空闲多久告别", "kind": "number",
                     "min": 0, "max": 300, "unit": "秒"},
                    {"key": "farewell", "label": "告别语", "kind": "select",
                     "options": ["off", "short", "full"]},
                    {"key": "farewell_text", "label": "短告别语", "kind": "text"},
                    {"key": "keep_context", "label": "保留多轮上下文", "kind": "bool",
                     "hint": "关掉 = 每句都当新的一句，不带上文"},
                    {"key": "history_rounds", "label": "上下文轮数", "kind": "number",
                     "min": 0, "max": 12, "unit": "轮"},
                    {"key": "vendor_quiet_s", "label": "让位静默窗口", "kind": "number",
                     "min": 0, "max": 60, "unit": "秒"},
                ]},
            ],
        },
        {
            "id": "music", "title": "音乐", "icon": "♪", "group": "living", "order": 30,
            "enabled": music_enabled,
            "enabled_reason": "" if music_enabled
            else f"music.enabled 未开启（配置的源：{music_source}）",
            "source": "/api/v1/music", "refresh_s": 4,
            "blocks": [
                {"type": "stats", "cards": [
                    {"label": "状态", "value": "$.state",
                     "map": {"playing": "播放中", "paused": "已暂停", "idle": "空闲"},
                     "tone": {"playing": "ok", "paused": "warn"}},
                    {"label": "队列剩余", "value": "$.queue_left", "unit": "首"},
                ]},
                {"type": "now", "title": "$.track.title", "sub": "$.track.artist",
                 "empty": "没有在播的歌"},
                {"type": "actions", "buttons": [
                    {"id": "prev", "label": "上一首", "method": "POST",
                     "href": "/api/v1/music", "body": {"action": "prev"}, "refresh": True},
                    {"id": "pause", "label": "暂停", "method": "POST",
                     "href": "/api/v1/music", "body": {"action": "pause"}, "refresh": True,
                     "enabled_when": "$.state=playing"},
                    {"id": "resume", "label": "继续", "method": "POST",
                     "href": "/api/v1/music", "body": {"action": "resume"}, "refresh": True,
                     "enabled_when": "$.state=paused"},
                    {"id": "next", "label": "下一首", "method": "POST",
                     "href": "/api/v1/music", "body": {"action": "next"}, "refresh": True},
                    {"id": "play", "label": "随便放点", "method": "POST",
                     "href": "/api/v1/music", "body": {"action": "play"}, "refresh": True},
                    {"id": "stop", "label": "停止", "method": "POST",
                     "href": "/api/v1/music", "body": {"action": "stop"}, "refresh": True,
                     "confirm": "停止并清空队列？", "danger": True},
                ]},
                {"type": "list", "title": "最近爱听", "items": "$.top",
                 "item_title": "$.artist", "item_sub": "$.plays 次",
                 "empty": "还没有播放记录", "max": 5},
            ],
        },
    ]


async def _music_transport(app: FastAPI, clean: str, runner, client, trace, traces) -> str | None:
    """Deterministic music transport words: pause/resume/next/prev, no model.

    The model agreed with 「下一首」 in words and never called the tool (measured
    22:06) - a transport that only *sounds* executed is worse than none, and these
    four words never need a round trip. Returns the spoken line, or None when the
    request is not a transport word (the turn proceeds normally).
    """
    service = getattr(app.state, "music", None)
    if service is None:
        return None
    if rules.is_music_random(clean):
        # 「放首歌」 is deterministic, like the transports: the model refused the
        # identical prompt twice tonight out of pure context while the library was
        # up (22:12, 22:24). A named ask (「放周杰伦的晴天」) still goes to the model.
        from ...music.source import SourceError
        from ...music.tools import _started
        try:
            tracks = await service.source.random(service.cfg.queue_batch)
        except SourceError as exc:
            log.info("music random failed: %s", exc)
            tracks = []
        started = await service.play(tracks) if tracks else None
        result = _started(tracks, started) if tracks else \
            ToolResult(status="unreachable")
        key = {"ok": "music.started", "ok_one": "music.started_one",
               "unresolved": "music.unresolved",
               "unreachable": "music.unreachable"}[result.status]
        # The song starting IS the answer (9哥: no talk over it); only a failure
        # gets words.
        line = "" if result.status in ("ok", "ok_one") else \
            phrases.book.render(key, **result.payload)
        runner.idle.touch()
        spoken = await runner.say(line, _speaking(app, client), trace) if line else 0
        _light_after_audio(app, spoken, _voice_rate(client),
                           "music" if tracks else "off")
        with contextlib.suppress(Exception):
            await client.reply_finished(hold_music_s=MUSIC_COMMAND_WINDOW_S)
        app.state.last_turn = {
            "transcript": clean, "reply": line, "audio_bytes": spoken, "error": "",
            "tools": ["music_play"], "stages": {},
        }
        traces.add(trace)
        log.info("music transport: %r -> random (%s)", clean, key)
        return line
    action = rules.music_transport(clean)
    if not action:
        return None      # not a transport word: the turn (or the dismiss list) owns it
    if service.state == "idle":
        # No song at all: 「暂停」/「停一下」 is the dismiss list's job ("stop
        # talking"), not a player that does not exist. Fall through.
        return None
    # A transport word with nothing playing still deserves words, not silence.
    fn = {"pause": service.pause, "resume": service.resume,
          "next": service.next, "prev": service.prev}[action]
    try:
        track_or_ok = await fn()
    except Exception as exc:  # noqa: BLE001 - a broken transport must not kill the app
        log.info("music transport %s failed: %s", action, exc)
        track_or_ok = False
    if action in ("next", "prev"):
        ok = bool(track_or_ok) and not isinstance(track_or_ok, bool)
        key = ("music.next" if ok
               else ("music.last_track" if action == "next" else "music.first_track"))
        slots = {"title": track_or_ok.title} if ok else {}
    else:
        ok = bool(track_or_ok)
        key = ({"pause": "music.paused", "resume": "music.resumed"}[action] if ok
               else ("music.not_playing" if action == "pause" else "music.not_paused"))
        slots = {}
    # Success is silent: the song changing / resuming / stopping is the answer.
    # Speaking 「接着放。」 made the music start, stop for the sentence, and start
    # again (measured 09:38:27-30). Only an edge (last track, nothing playing)
    # gets words, because silence there would be a lie.
    line = "" if ok else phrases.book.render(key, **slots)
    runner.idle.touch()
    spoken = await runner.say(line, _speaking(app, client), trace) if line else 0
    # The ring must answer 「暂停」 with amber and stay there: the reply's own
    # speaking/listening states were overriding the park's LED within 300 ms.
    _light_after_audio(app, spoken, _voice_rate(client), _music_light(action, service))
    # A command window: keep the song down just long enough to chain the next
    # command without the wake word (over playing music the gate must stay shut,
    # or lyrics become commands). 2 s, then the song returns.
    with contextlib.suppress(Exception):
        await client.reply_finished(hold_music_s=MUSIC_COMMAND_WINDOW_S)
    _record_turn(app, {
        "transcript": clean, "reply": line, "audio_bytes": spoken, "error": "",
        "tools": [f"music_{action}"], "stages": {},
    })
    traces.add(trace)
    log.info("music transport: %r -> %s", clean, action)
    return line


# No command window: 9哥 is fine repeating the wake word over music, and the
# ducked gap only delayed the song. 0 = the song returns the moment it can.
MUSIC_COMMAND_WINDOW_S = 0.0


def _turn_light(result: object, default: str = "listening") -> str:
    """The ring after a turn: a silent music answer leaves it on the song."""
    if getattr(result, "audio_bytes", 0) == 0 and _is_music_turn(result):
        return "music"
    return default


def _is_music_turn(result: object) -> bool:
    tools = getattr(result, "tools", None) or []
    return any(str(n).rsplit(".", 1)[-1].startswith("music") for n in tools)


async def _stop_command(app: FastAPI, clean: str, runner, client, trace,
                        traces) -> bool:
    """D1c ③: stop words split by whether a song is playing. True when handled.

    * media_stop (a song is playing): stop the queue, say 「好，不放了。」 through the
      PhraseBook, and KEEP the session - 「停止播放」 over music is not goodbye.
    * bare_stop (nothing playing): there is nothing to stop, so the stop word means
      end the conversation (runner.dismiss, the old farewell/LED semantics).
    * explicit dismissals are bare_stop even mid-song, but must still silence the
      music: 9哥 heard the song come back 12 s after 「退下吧」 (measured 09:17).

    Returns False for a non-stop transcript so the ordinary turn proceeds.
    """
    cfg = app.state.cfg
    if not rules.stop_intent(clean, cfg.session.end_words):
        return False
    service = getattr(app.state, "music", None)
    music_active = service is not None and service.state in ("playing", "paused")
    if rules.stop_scope(clean, music_active, cfg.session.end_words) == rules.MEDIA_STOP:
        try:
            await service.stop()
        except Exception as exc:  # noqa: BLE001 - never crash the app
            log.info("stop music failed: %s", exc)
        line = phrases.book.render("music.stopped")
        runner.idle.touch()
        spoken = await runner.say(line, _speaking(app, client), trace)
        with contextlib.suppress(Exception):
            await client.reply_finished()   # the session stays open
        _light_after_audio(app, spoken, cfg.audio.voice.rate, "off")
        _record_turn(app, {
            "transcript": clean, "reply": line, "session_action": "media_stop",
            "audio_bytes": spoken, "error": "", "tools": [], "stages": {},
        })
        traces.add(trace)
        log.info("stop intent: %r -> media stopped, session open", clean)
        return True
    # bare_stop (散场): silence any song first, then dismiss.
    if music_active:
        try:
            await service.stop()
        except Exception as exc:  # noqa: BLE001 - never crash the app
            log.info("stop music failed: %s", exc)
    elif client.metrics is not None and client.metrics.output_owner == "music":
        with contextlib.suppress(Exception):
            await client.send(StopAudio())
    # dismiss() closes the session before the reply: the idle clock used to keep
    # running behind it and the farewell said goodbye a second time (measured 20:42).
    spoken = await runner.dismiss(_speaking(app, client), trace)
    # No reply_finished here: the session is closing, so the plane must not open a
    # follow-up window after the goodbye.
    _light_after_audio(app, spoken, cfg.audio.voice.rate, "off")
    _record_turn(app, {
        "transcript": clean, "reply": "", "session_action": "bare_stop",
        "audio_bytes": spoken, "error": "", "tools": [], "stages": {},
    })
    traces.add(trace)
    log.info("stop intent: %r -> session closed", clean)
    return True


async def _context_command(app: FastAPI, clean: str, runner, client, trace,
                           traces) -> str | None:
    """「关闭上下文」/「打开上下文」: a config edit, persisted, no model."""
    action = rules.context_command(clean)
    if not action:
        return None
    cfg = app.state.cfg
    cfg.session.keep_context = action == "on"
    runner.apply_session(cfg.session)
    path = getattr(app.state, "config_path", None)
    if path is not None:
        with contextlib.suppress(Exception):
            save_config(path, cfg)
    line = phrases.book.render("context.on" if action == "on" else "context.off")
    runner.idle.touch()
    spoken = await runner.say(line, _speaking(app, client), trace)
    _record_turn(app, {"transcript": clean, "reply": line, "audio_bytes": spoken,
                       "error": "", "tools": [f"context.{action}"], "stages": {}})
    log.info("context: %s (keep_context=%s)", action, cfg.session.keep_context)
    return line


def _memory_context(app: FastAPI) -> str:
    """Confirmed memory only - candidates wait for the console (old build's rule)."""
    store = getattr(app.state, "memory", None)
    if store is None:
        return ""
    texts = store.confirmed_texts(limit=20)
    if not texts:
        return ""
    return "（你记得这些事，用户说过：\n" + "\n".join(f"- {t}" for t in texts) + "）"


def _turn_hold(result: object) -> float | None:
    """0 when this turn's tools were about the song itself (music/volume)."""
    tools = getattr(result, "tools", None) or []
    for name in tools:
        tool = str(name).rsplit(".", 1)[-1]
        if tool.startswith("music") or tool == "volume":
            return 0.0
    return None


def _record_turn(app: FastAPI, turn: dict) -> None:
    """Keep the last N turns for the console's conversation panel."""
    app.state.last_turn = turn
    ring = getattr(app.state, "recent_turns", None)
    if ring is None:
        ring = collections.deque(maxlen=50)
        app.state.recent_turns = ring
    ring.append({**turn, "ts": time.strftime("%H:%M:%S")})


def _voice_rate(client: object) -> int:
    ready = getattr(client, "ready", None)
    return int(getattr(ready, "voice_rate", 0) or 16000)


async def _volume_command(app: FastAPI, clean: str, runner, client, trace,
                          traces) -> str | None:
    """Volume words reach the mixer without a model round trip (measured 09:03)."""
    level = rules.volume_level(clean)
    handler = getattr(app.state, "volume_tool", None)
    if level is None or handler is None:
        return None
    result = await handler({"level": level})
    spec_key = ({"ok": "volume.set", "ok_read": "volume.read",
                 "unknown": "volume.unknown", "unreachable": "volume.failed"}
                .get(getattr(result, "status", "")))
    if not spec_key:
        return None
    line = phrases.book.render(spec_key, **getattr(result, "payload", {}))
    runner.idle.touch()
    spoken = await runner.say(line, _speaking(app, client), trace)
    _light_after_audio(app, spoken, _voice_rate(client), "listening")
    # Volume is the one command whose answer is the music itself: the plane brings
    # it back the moment this reply drains (hold 0), not after the 6 s window
    # (measured 09:12: 「音量调到60%了」 then several seconds of silence).
    with contextlib.suppress(Exception):
        await client.reply_finished(hold_music_s=0)
    _record_turn(app, {
        "transcript": clean, "reply": line, "audio_bytes": spoken, "error": "",
        "tools": ["volume"], "stages": {},
    })
    traces.add(trace)
    log.info("volume command: %r -> %s", clean, line)
    return line


def _music_light(action: str, service: object) -> str:
    """Where the ring belongs after a transport word, in music terms."""
    if action == "pause":
        return "music_paused"
    if action in ("resume", "next", "prev"):
        return "music"
    return "off"


def create_app(
    cfg: Config,
    config_path: Path | None = None,
    start_audio: bool = False,
) -> FastAPI:
    secrets.configure(cfg.system.data_dir)
    # The console's phrase overrides live in data_dir/phrases.yaml; a broken file keeps
    # the defaults and only warns (an unreadable file must not stop the assistant).
    try:
        loaded = phrasebook.load(Path(cfg.system.data_dir))
        if loaded.error or loaded.errors:
            log.warning("phrasebook: overrides not applied (%s)", loaded.error or loaded.errors)
        elif loaded.overrides:
            log.info("phrasebook: %d phrase override(s) applied", len(loaded.overrides))
    except Exception as exc:  # noqa: BLE001 - defaults are a valid answer
        log.warning("phrasebook: load failed: %s", exc)
    registry = ToolRegistry()
    # One assembly for everything local: information tools and reminders. Reminders used
    # to be reachable only through tools.reminders.build_tools, which nothing imported -
    # the tools existed, the scheduler existed, and the model could never call them.
    # Memory is independent of the music layer: curated entries plus candidates.
    memory = MemoryStore(Path(cfg.system.data_dir) / "memory.json")
    local_tools = build_local_tools(cfg)
    for spec, _handler in local_tools:
        registry.register(spec)
    # Music: the tools exist only when the source is configured. The service sends
    # through whatever client is live at call time - the plane reconnects, the
    # sender must not capture the first one forever. Kept in a local until the app
    # object exists; `app.state.music` is set right after FastAPI() is built.
    music_service: MusicService | None = None
    if cfg.music.enabled:
        source = NavidromeSource(
            Path(cfg.music.secrets_path).expanduser(),
            Path(cfg.music.cache_dir), cfg.music.cache_tracks,
        )
        history = PlayHistory(
            Path(cfg.system.data_dir) / "music_history.json", keep=cfg.music.history_max)
        aliases = AliasStore(Path(cfg.system.data_dir) / "music_aliases.json")
        # Beats the literal search for every nickname the library does not hold.
        names = NameResolver(aliases, timeout_s=6.0)
        music_service = MusicService(source, history, _never_sender, cfg.music,
                                     names=names)
        for spec, handler in build_music_tools(cfg, music_service):
            registry.register(spec)
            local_tools.append((spec, handler))
        log.info("music: source=%s tools=%d", cfg.music.source, len(local_tools))
    adapters = build_adapters(cfg)
    traces = TraceLog()
    for dev_id, err in adapters.errors.items():
        log.warning("device %s is configured but not drivable: %s", dev_id, err)

    async def _render_cue() -> None:
        try:
            app.state.ack_note = await asyncio.wait_for(ensure_ack_wav(cfg), timeout=15)
        except TimeoutError:
            app.state.ack_note = "tts timed out: the plane plays whatever ack.wav holds"
        except Exception as exc:  # noqa: BLE001 - a cue is never worth an outage
            app.state.ack_note = f"{type(exc).__name__}: {exc}; the plane will use a tone"
        log.info("ack: %s", app.state.ack_note)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        spoken = rules.SpokenMemory()
        runner = TurnRunner(
            cfg.providers,
            sample_rate=cfg.audio.voice.rate,
            assistant=cfg.assistant,
            history_turns=cfg.session.history_rounds,
            wake_words=[w for d in cfg.devices for w in d.wake_words],
            tools=local_tools,
            spoken=spoken,
            session=cfg.session,
            direct_hook=_direct_speech,
        )
        app.state.spoken = spoken
        # Render the spoken wake cue now, not on the wake: synthesising it inside the wake
        # path cost the old build 1.5 s and swallowed the first request (\`ack_warmer\`).
        app.state.ack_task = asyncio.create_task(render_ack_wav(cfg), name="ack-render")

        async def _warm_filler() -> None:
            # Three phrases, not one: 9哥's expected flow is 「我查一下」 for a lookup,
            # 「我想想」 for thinking, 「好的，我看看」 for a device - each pre-rendered so
            # the choice costs nothing at the moment it is spoken.
            from ...session.filler import DEVICE_TEXT, LOOK_TEXT, THINK_TEXT

            rendered: dict[str, bytes] = {}
            for line in (LOOK_TEXT, THINK_TEXT, DEVICE_TEXT):
                pcm = await render_filler_pcm(cfg, line)
                if pcm:
                    rendered[line] = pcm
            runner.filler_pcm = rendered
            app.state.filler_pcm = rendered
            log.info("filler: %d phrases ready", len(rendered))

        # Same reason as the ack: a filler that has to be synthesised when it is needed
        # costs the latency it exists to hide.
        app.state.filler_task = asyncio.create_task(_warm_filler(), name="filler-render")
        app.state.runner = runner
        app.state.last_turn = None
        vendor = VendorWindow(cfg.session.vendor_quiet_s)
        app.state.vendor = vendor
        # HA over MCP (V2's first application): connect, register the whitelisted
        # tools, inject the entity list per turn. A dead HA degrades its tools -
        # the rest of the assistant never notices (old build's rule).
        ha: HaBridge | None = None
        if cfg.tools.ha.base_url:
            ha = HaBridge(cfg.tools.ha, Path(cfg.system.data_dir))
            if await ha.start():
                for spec, handler in ha.tool_specs():
                    registry.register(spec)
                    local_tools.append((spec, handler))
                runner.set_tools(local_tools)
                runner.extra_context = _context_provider(app, ha)
                app.state.ha = ha
                log.info("ha: %d tools registered", len(ha.tool_specs()))
            else:
                log.warning("ha: MCP connect failed (%s) - device tools degrade",
                            ha.client.last_error)
                app.state.ha = ha
        # Warm the instances the turns will use. This used to build its own chain and
        # warm that, which loaded the real one cold.
        await runner.warm()
        app.state.ack_note = ""
        app.state.led_tasks = set()

        if start_audio:
            supervisor = AudioPlaneSupervisor(cfg, config_path=config_path)
            try:
                await supervisor.start()
                ready = supervisor.client.ready if supervisor.client else None
                log.info(
                    "audio plane up: pid=%s device=%s capture=%s Hz/%s ch",
                    supervisor.proc.pid if supervisor.proc else "?",
                    ready.device_id if ready else "?",
                    ready.capture_rate if ready else "?",
                    ready.capture_channels if ready else "?",
                )
            except Exception as exc:  # noqa: BLE001 - a device problem must not kill the console
                supervisor.error = f"{type(exc).__name__}: {exc}"
                log.warning("audio plane did not start: %s", exc)
            # Armed even when the first launch failed: the watchdog is what turns
            # "plane crashed" into a few seconds of silence instead of a manual
            # restart on the NAS (Docker does not restart on an unhealthy check).
            supervisor.watch()
            app.state.audio = supervisor
            # The handle stays valid across restarts (callbacks are re-applied on
            # every bind), so the turn path is wired once, not once per launch.
            client = supervisor.handle
            if client is not None:
                async def on_utterance(pcm: bytes) -> None:
                    if vendor.muted():
                        # The speaker's own assistant is answering; answering too would
                        # double-reply to one sentence.
                        log.info("vendor dialog, dropping our turn (%.1fs left)",
                                 vendor.remaining())
                        return
                    trace = TurnTrace(device_id=client.ready.device_id if client.ready else "")
                    if client.last_wake is not None:
                        trace.mark("wake", at_ms=client.last_wake.ts_ms)
                    text, asr_report = await runner.transcribe(pcm, trace)
                    clean = strip_wake(text, runner.wake_words)
                    if "小爱同学" in clean or clean.strip() in {"小爱", "小爱小爱"}:
                        # Text-level backstop for the vendor wake: its StartCapture signal
                        # may be missing on some client builds, and its final transcript
                        # arrives after our own ASR has already decided to answer.
                        log.info("vendor wake word in our transcript; yielding: %r", clean)
                        vendor.vendor_spoke("wake", clean)
                        return
                    if not clean.strip():
                        # Only the wake word arrived: the wake residue case, not a request.
                        log.info("utterance was wake residue only; no turn")
                        return
                    if app.state.spoken.is_echo(clean):
                        # Our own answer came back through the arrays: measured 2026-10-22,
                        # the speaker answered itself once per weather question.
                        log.info("own voice heard back, no turn: %r", clean)
                        return
                    if rules.is_noise(clean):
                        # 「嗯。」 after the wake word: measured on the real speaker, and it
                        # used to become a whole turn ("嗯，你说。").
                        log.info("noise, no turn: %r", clean)
                        return
                    # Transport words before the dismiss list: 「暂停」 and 「停一下」
                    # are both, and with a song playing they mean the player, not
                    # the session (old build: VENDOR_PAUSE_WORDS checked first).
                    if await _music_transport(app, clean, runner, client, trace,
                                              traces) is not None:
                        return
                    if await _volume_command(app, clean, runner, client, trace,
                                             traces) is not None:
                        return
                    if await _context_command(app, clean, runner, client, trace,
                                              traces) is not None:
                        return
                    if await _stop_command(app, clean, runner, client, trace, traces):
                        return
                    # A bare device name is a question, not a command (old build,
                    # measured 5/5 guessing the direction): ask which way.
                    if rules.verbless_device_name(clean):
                        line = phrases.book.render("device.ask_verb")
                        runner.idle.touch()
                        spoken = await runner.say(line, _speaking(app, client), trace)
                        _record_turn(app, {
                            "transcript": clean, "reply": line, "audio_bytes": spoken,
                            "error": "", "tools": ["clarify.verb"],
                            "stages": {},
                        })
                        _light_after_audio(app, spoken, cfg.audio.voice.rate)
                        log.info("clarify: bare device name %r -> asking the verb", clean)
                        return
                    _light(app, "thinking")
                    # An utterance that asks to be remembered leaves a candidate
                    # for the console; it is not injected until confirmed.
                    with contextlib.suppress(Exception):
                        memory.extract(clean)
                    try:
                        result = await runner.run(pcm, _speaking(app, client), trace, transcript=clean)
                    except Exception as exc:  # noqa: BLE001 - a broken turn must not kill the app
                        log.warning("turn failed: %s", exc)
                        # Turn-level degrade (V1): silence after a wake reads as
                        # "it ignored me"; one system-layer line, best effort.
                        with contextlib.suppress(Exception):
                            await runner.say(phrases.book.render("turn.failed"),
                                             _speaking(app, client), trace)
                        # The plane's drain needs the flag or it falls back to the
                        # no-reply grace; a failed turn is still a finished turn.
                        with contextlib.suppress(Exception):
                            await client.reply_finished()
                        return
                    if (not result.tools
                            and rules.claims_device_success(clean, result.reply)):
                        # The model said it did something without calling a tool:
                        # retry once with an explicit instruction, then be honest.
                        log.info("clarify: claimed success with no tool call: %r", result.reply)
                        forced = (clean + "\n（上一轮你没有调用任何工具，这次必须直接调用工具"
                                        "完成操作，不要用文字回答。）")
                        try:
                            result = await runner.run(pcm, _speaking(app, client), trace,
                                                      transcript=forced)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("retry after a false claim failed: %s", exc)
                        if not result.tools:
                            line = phrases.book.render("device.not_done")
                            spoken = await runner.say(line, _speaking(app, client), trace)
                            result.reply = line
                            result.audio_bytes = spoken
                    if asr_report is not None:
                        result.stages.setdefault("asr", asr_report)
                    _record_turn(app, result.to_dict())
                    traces.add(trace)
                    # A music/volume turn wants the song back the moment the reply
                    # drains, not after the follow-up window (measured 09:21: ~10 s).
                    await client.reply_finished(hold_music_s=_turn_hold(result))
                    _light_after_audio(app, result.audio_bytes, cfg.audio.voice.rate,
                                       _turn_light(result))
                    log.info("turn: %s", result.to_dict())

                client.on_utterance = on_utterance
                async def stop_ours() -> None:
                    try:
                        await client.send(StopAudio())
                    except Exception as exc:  # noqa: BLE001 - never crash the app
                        log.info("vendor yield: stop failed: %s", exc)

                # The vendor's playing state as the CONTROL plane sees it. The
                # adapter's own `vendor_playing` lives inside the audio plane's
                # process; reading it from here always looked False, so the
                # watchdog gave the line back mid-answer (measured 08:49:17).
                vendor_state = {"playing": False, "seen": False}

                async def vendor_park_and_watch() -> None:
                    """The vendor took the line while our song played (V4/V5).

                    Park the song; hand it back when the vendor's dialog ends
                    (Dialog.Finish, or playing idle). The 4 s watchdog only covers
                    a wake that never starts speaking (old build's rule) - once
                    the vendor has sounded, its dialog decides.
                    """
                    service = getattr(app.state, "music", None)
                    if service is None or not await service.park_for_vendor():
                        return
                    await asyncio.sleep(4.0)
                    if not vendor_state["playing"] and not vendor_state["seen"]:
                        await service.give_back_if_parked(delay_s=0.0)
                        return
                    # Sound started but its end event may be lost: bounded fallback.
                    for _ in range(36):          # up to 3 more minutes
                        await asyncio.sleep(5.0)
                        if not service.vendor_parked:
                            return
                        if not vendor_state["playing"]:
                            await service.give_back_if_parked(delay_s=0.0)
                            return

                def on_vendor(kind: str, text: str) -> None:
                    """The vendor's assistant woke: it owns the next utterance.

                    Opening a quiet window is not enough - if our turn already started
                    (continuous follow-up listening means it often has), the two answers
                    overlap. The old build barge-in'd on this signal; so do we: abort the
                    synthesis and stop the speaker, then stay quiet for 12 s.

                    With our music playing the stop becomes a park: the song waits out
                    the vendor's dialog and comes back (its `idle` event, or the 4 s
                    watchdog above). Key 114 starting the vendor's own player lands on
                    the same `playing` event, so V5 needs no separate path.
                    """
                    service = getattr(app.state, "music", None)
                    if kind == "playing":
                        vendor_state["playing"] = text == "playing"
                        vendor_state["seen"] = vendor_state["seen"] or vendor_state["playing"]
                        if service is not None:
                            if text == "playing" and service.state == "playing":
                                asyncio.create_task(service.park_for_vendor(),
                                                    name="vendor-park")
                            elif text == "idle" and service.vendor_parked:
                                # The old build's 0.6 s tail: let the TTS ring out.
                                asyncio.create_task(service.give_back_if_parked(0.6),
                                                    name="vendor-giveback")
                    elif kind == "vendor_listening":
                        # The vendor's recognizer is hearing someone: get the song
                        # out of the way now (only music, no quiet window).
                        if service is not None and service.state == "playing":
                            asyncio.create_task(service.park_for_vendor(),
                                                name="vendor-park")
                    elif kind == "wake":
                        # A new vendor dialog: the silent-wake watchdog starts fresh.
                        vendor_state["playing"] = False
                        vendor_state["seen"] = False
                        if service is not None and service.vendor_parked:
                            asyncio.create_task(service.give_back_if_parked(0.6),
                                                name="vendor-giveback")
                    if not vendor.vendor_spoke(kind, text):
                        return
                    runner.abort()
                    if service is not None and service.state == "playing":
                        asyncio.create_task(vendor_park_and_watch(), name="vendor-park")
                    asyncio.create_task(stop_ours(), name="vendor-stop")
                    _light(app, "off")

                client.on_vendor = on_vendor
                def on_wake(msg: object) -> None:
                    vendor.ours_spoke()
                    _light(app, "wake")
                    # No idle clock here: a wake that hears nothing must not become a
                    # goodbye 15 s later (measured 10:22:17 - the user woke the box,
                    # said nothing, and got 「我先下啦」). The session opens with the
                    # first turn; its end starts the countdown.
                    # A wake is also a new conversation: the previous session's turns
                    # must not bleed in (10:41:26: a weather answer ended by asking
                    # which 周杰伦 song to play). Old build: begin_session().
                    runner.reset_history()

                    if getattr(msg, "barge", False):
                        # The plane already stopped the speaker and dropped its queue;
                        # this stops the answer that is still being synthesised from
                        # refilling it.
                        log.info("barge-in: %.1f s of queued speech dropped",
                                 getattr(msg, "barged_s", 0.0))
                        runner.abort()

                client.on_wake = on_wake
                music = getattr(app.state, "music", None)
                if music is not None:
                    # The queue advances only on a file's natural end (the plane's
                    # PlayFinished); a pause or a barge must not look like a finish.
                    client.on_playback_finished = music.on_finished

                # Volume: the speaker's own mixer, never a claim without a call.
                # Measured 08:53: 「音量80%」 was answered 「音量调到80%了。」 with
                # zero tool calls - the old build's false-claim failure mode.
                async def volume_tool(args: dict) -> ToolResult:
                    level = str(args.get("level") or "").strip().lower()
                    try:
                        now = await client.device_volume()
                    except Exception:  # noqa: BLE001 - a dead mixer is a sentence
                        return ToolResult(status="unreachable")
                    if now is None:
                        return ToolResult(status="unreachable")
                    pct = round(now * 100 / 255)
                    if level in ("", "read", "how", "now"):
                        return ToolResult(status="ok_read").slot(pct=pct)
                    if level in ("up", "down"):
                        pct = max(0, min(100, pct + (10 if level == "up" else -10)))
                    elif level in ("mute", "静音"):
                        pct = 0
                    else:
                        digits = "".join(ch for ch in level if ch.isdigit())
                        if not digits:
                            return ToolResult(status="unknown").slot(pct=pct)
                        pct = max(0, min(100, int(digits)))
                    target = round(pct * 255 / 100)
                    try:
                        got = await client.set_device_volume(target)
                    except Exception:  # noqa: BLE001
                        return ToolResult(status="unreachable")
                    applied = round((got if got is not None else target) * 100 / 255)
                    # Learn it as the target: the reconciler would otherwise put the
                    # configured value back within 16 s (old build: adopt_device_volume).
                    dev_id = client.ready.device_id if client.ready else ""
                    dev = next((d for d in cfg.devices if d.id == dev_id),
                               cfg.devices[0] if cfg.devices else None)
                    if dev is not None and config_path is not None:
                        dev.volume = applied
                        with contextlib.suppress(Exception):
                            save_config(config_path, cfg)
                    return ToolResult(status="ok").slot(pct=applied)

                volume_spec = ToolSpec(
                    name="local.volume", permission=Permission.WRITE,
                    description="调音量（「音量大一点/小一点/调到80%/静音/现在音量多少」）",
                    input_schema={"type": "object", "properties": {
                        "level": {"type": "string",
                                  "description": "up | down | mute | 0-100 | 空=查询"}}},
                    interaction=InteractionSpec(exec={
                        "ok": "volume.set", "ok_read": "volume.read",
                        "unknown": "volume.unknown",
                        "unreachable": "volume.failed"}),
                )
                # Lifespan can run twice (TestClient, restarts): register once.
                if "local.volume" not in registry.names():
                    registry.register(volume_spec)
                    local_tools.append((volume_spec, volume_tool))
                mem_specs = build_memory_tools(memory)
                for spec, handler in mem_specs:
                    if spec.name not in registry.names():
                        registry.register(spec)
                        local_tools.append((spec, handler))
                # Make every tool registered in this lifespan visible to the model.
                runner.set_tools(local_tools)
                app.state.volume_tool = volume_tool
        # Session end. Polled rather than timed: one loop, 1 s resolution, and the
        # goodbye can never fire mid-turn (check_idle refuses while a turn is open).
        async def idle_watch() -> None:
            live = getattr(app.state, "audio", None)
            while True:
                await asyncio.sleep(1.0)
                client = getattr(live, "client", None) if live is not None else None
                if client is None:
                    continue
                # Playback is not silence: a 15 s news read or a song must not be
                # interrupted by the goodbye (the old build hit this and fixed it).
                def busy(state=live) -> bool:
                    metrics = getattr(getattr(state, "client", None), "metrics", None)
                    if metrics is not None and metrics.output_owner:
                        return True
                    # A song parked for the vendor is music about to come back:
                    # the farewell must not talk over the give-back (old build's
                    # music_due rule).
                    service = getattr(app.state, "music", None)
                    return bool(service is not None and service.vendor_parked)

                try:
                    line = await runner.check_idle(
                        _speaking(app, client), TurnTrace(device_id="session"), busy=busy
                    )
                except Exception as exc:  # noqa: BLE001 - a watchdog must not die
                    log.warning("idle watch: %s", exc)
                    continue
                if line:
                    _light(app, "off")
                    log.info("session closed: %r", line)

        app.state.idle_task = asyncio.create_task(idle_watch(), name="idle-watch")

        # P10. The reminders were a complete island: tools with 44 tests, a scheduler
        # that walks due items, and no caller anywhere. One tick fires them, one queue
        # speaks them. A due reminder must not talk over a turn or over whatever already
        # owns the speaker (a cue, an answer, a song): it waits, because its fired state
        # is already on disk and waiting loses nothing.
        reminder_queue: asyncio.Queue[str] = asyncio.Queue()
        scheduler = build_scheduler(cfg)
        scheduler.add_listener(lambda item: reminder_queue.put_nowait(item.text))
        app.state.reminders = scheduler

        async def reminder_tick() -> None:
            while True:
                await asyncio.sleep(1.0)
                try:
                    for item in scheduler.collect_due():
                        log.info("reminder due: %s", item.text)
                        reminder_queue.put_nowait(item.text)
                except Exception as exc:  # noqa: BLE001 - a tick must not die
                    log.exception("reminders: tick failed: %s", exc)

        async def reminder_speak() -> None:
            while True:
                text = await reminder_queue.get()
                waited = 0.0
                spoken = False
                while waited < 120.0:
                    supervisor = getattr(app.state, "audio", None)
                    client = supervisor.client if supervisor is not None else None
                    metrics = client.metrics if client is not None else None
                    owner = metrics.output_owner if metrics is not None else ""
                    if client is not None and not runner.busy and not owner:
                        await runner.say(
                            text, client.speak, TurnTrace(device_id="reminder")
                        )
                        spoken = True
                        break
                    await asyncio.sleep(0.5)
                    waited += 0.5
                if not spoken:
                    log.warning("reminder not spoken (no free speaker): %s", text)

        app.state.reminder_tick = asyncio.create_task(reminder_tick(), name="reminder-tick")
        app.state.reminder_task = asyncio.create_task(reminder_speak(), name="reminder-speak")

        # The cue is rendered *after* the plane is up and in the background: it is a nicety,
        # and a vendor that hangs must not delay /health or the speaker's reconnect. The
        # plane re-reads ack.wav on every wake, so a file that lands a second later is
        # picked up without a restart.
        if cfg.ack.mode == "tts":
            app.state.ack_task = asyncio.create_task(_render_cue(), name="ack-cue")
        yield
        cue_task = getattr(app.state, "ack_task", None)
        if cue_task is not None and not cue_task.done():
            cue_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await cue_task
        for name in ("reminder_tick", "reminder_task"):
            task = getattr(app.state, name, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        supervisor = getattr(app.state, "audio", None)
        if supervisor is not None:
            await supervisor.stop()

    app = FastAPI(title="house-ear", version=__version__, lifespan=lifespan)
    # The live config and the file it came from: voice commands that edit a
    # setting (「关闭上下文」) persist through the same file the console writes.
    app.state.cfg = cfg
    app.state.config_path = config_path
    # The wizard can add a device at runtime; endpoints read this attribute, not a
    # startup closure, so a fresh probe does not need a control-plane restart.
    app.state.adapters = adapters
    app.state.memory = memory
    app.state.tools = registry  # the console and tests read the same registry turns use
    app.state.trace_log = traces  # scripts and tests read the same log the API serves
    if music_service is not None:
        music_service.sender = _music_sender(app)
        app.state.music = music_service
    else:
        app.state.music = None
    # The console registry (docs/console.md): panels are declared where their feature
    # is assembled, the frontend renders them and never asks "does this exist".
    app.state.panels = _build_panels(cfg.music.enabled, cfg.music.source)
    app.state.panels_revision = f"{__version__}-{int(time.time())}"

    def auth(x_auth_token: str | None = Header(default=None)) -> None:
        if not cfg.web.auth_token:
            return
        if x_auth_token != cfg.web.auth_token:
            raise HTTPException(status_code=401, detail="bad or missing X-Auth-Token")

    @app.get("/health")
    async def health() -> dict:
        supervisor = getattr(app.state, "audio", None)
        return {
            "status": "ok",
            "version": __version__,
            "config_version": cfg.version,
            "config_version_supported": CONFIG_VERSION,
            "uptime_s": round(time.time() - START, 1),
            "devices": len(getattr(app.state, "adapters", adapters).adapters),
            "device_errors": len(getattr(app.state, "adapters", adapters).errors),
            "audio_plane": bool(supervisor is not None and supervisor.running),
            "vendor": {
                "quiet_s": cfg.session.vendor_quiet_s,
                "muted_for": round(getattr(app.state, "vendor", None).remaining(), 1)
                if getattr(app.state, "vendor", None) else 0.0,
                "vendor_wakes": getattr(getattr(app.state, "vendor", None), "vendor_wakes", 0),
                "yielded_turns": getattr(getattr(app.state, "vendor", None), "yielded_turns", 0),
            },
            # What the plane will actually play on a wake, and why, if it was downgraded.
            "ack": {
                "mode": cfg.ack.mode,
                "note": getattr(app.state, "ack_note", ""),
                "wav": str(ack_wav_path(cfg)) if cfg.ack.mode == "tts" else "",
                "wav_exists": ack_wav_path(cfg).is_file() if cfg.ack.mode == "tts" else False,
            },
        }

    @app.get("/api/v1/doctor", dependencies=[Depends(auth)])
    async def doctor() -> dict:
        return run_doctor(cfg).to_dict()

    @app.get("/api/v1/config", dependencies=[Depends(auth)])
    async def get_config() -> dict:
        return redacted(cfg)

    # The two conversational sections are the ones a person tunes from the console. Audio
    # and providers stay on disk: changing them under a running audio plane is a different
    # problem (restart, device reconnect), not a text field.
    WRITABLE = ("assistant", "session")

    @app.put("/api/v1/config", dependencies=[Depends(auth)])
    async def put_config(patch: Annotated[dict, Body()]) -> dict:
        """Merge a patch into the sections people edit, then write it.

        Three rules, each of them a bug if dropped:
        - the file is re-read first, so a console page that never saw `providers` cannot
          write a stale copy of it back over the real one;
        - the merged result is validated as a whole `Config` before anything is written;
        - the write is `save_config` (timestamped backup + atomic replace), never a
          hand-rolled open/write.
        """
        unknown = sorted(set(patch) - set(WRITABLE))
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"only {list(WRITABLE)} can be written from the console, got {unknown}",
            )
        if config_path is None or not Path(config_path).exists():
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        try:
            current = load_config(config_path)
        except ConfigError as exc:
            raise HTTPException(
                status_code=409, detail=f"config on disk is not readable: {exc}"
            ) from exc
        merged = current.model_dump(mode="json")
        for section, values in patch.items():
            if not isinstance(values, dict):
                raise HTTPException(status_code=400, detail=f"{section} must be an object")
            merged[section] = {**merged.get(section, {}), **values}
        try:
            updated = Config.model_validate(merged)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail=exc.errors(include_url=False)
            ) from exc
        save_config(config_path, updated)
        log.info("config written from the console: %s", sorted(patch))
        # Applied live, in two places that both matter: the app's own copy (otherwise the
        # next GET /api/v1/config - and a page reload - shows the value from startup) and
        # the runner (the prompt is rebuilt per turn, so an edited persona or band takes
        # effect on the next turn without a restart).
        cfg.assistant = updated.assistant
        cfg.session = updated.session
        # A new acknowledgement text or voice has to exist as audio before the next wake.
        previous = getattr(app.state, "ack_task", None)
        if previous is not None and not previous.done():
            previous.cancel()
        app.state.ack_task = asyncio.create_task(render_ack_wav(updated), name="ack-render")
        runner = getattr(app.state, "runner", None)
        if runner is not None:
            runner.assistant = updated.assistant
            runner.history_turns = updated.session.history_rounds
            # timeout / farewell / history live in `session`, and an edited window must
            # take effect without a restart - the console writes that section.
            runner.apply_session(updated.session)
        return redacted(updated)

    @app.get("/api/v1/device/volume", dependencies=[Depends(auth)])
    async def device_volume() -> dict:
        """The speaker's own mixer value (mysoftvol, 0-255).

        Not a duplicate of the config: `devices[].volume = 0` means the vendor owns it,
        and the vendor rewrites it (old deployment: 160 -> 255 -> 160 within minutes). The
        only way to know what the room is actually doing is to ask the device.
        """
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise HTTPException(status_code=409, detail="no audio plane connected")
        try:
            value = await client.device_volume()
        except Exception as exc:  # noqa: BLE001 - the device may be gone
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc
        if value is None:
            raise HTTPException(status_code=502, detail="the device did not report a mixer value")
        return {"volume": value, "range": "0-255"}

    @app.put("/api/v1/device/volume", dependencies=[Depends(auth)])
    async def set_device_volume(patch: Annotated[dict, Body()]) -> dict:
        """Write the mixer. Used by the tests that must know the volume they ran at."""
        value = patch.get("value")
        if not isinstance(value, int) or not 0 < value <= 255:
            raise HTTPException(status_code=422, detail="value must be an int in 1..255")
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise HTTPException(status_code=409, detail="no audio plane connected")
        try:
            applied = await client.set_device_volume(value)
        except Exception as exc:  # noqa: BLE001 - the device may be gone
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc
        log.info("device volume set to %s from the API", applied)
        return {"volume": applied}

    @app.post("/api/v1/debug/play", dependencies=[Depends(auth)])
    async def debug_play(patch: Annotated[dict, Body()]) -> dict:
        """Play a wav through the running plane (music for AEC/barge tests).

        `house-ear debug-play` spawns its own plane and cannot be used while a server is
        up, which is exactly when these tests run.
        """
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise HTTPException(status_code=409, detail="no audio plane connected")
        if patch.get("stop"):
            await client.send(StopAudio())
            return {"playing": None}
        path = str(patch.get("file") or "")
        if not Path(path).is_file():
            raise HTTPException(status_code=404, detail=f"no such file: {path}")
        await client.send(PlayFile(path=path, loop=bool(patch.get("loop", False)),
                                   gain=float(patch.get("gain", 1.0))))
        return {"playing": path}

    @app.post("/api/v1/debug/say", dependencies=[Depends(auth)])
    async def debug_say(patch: Annotated[dict, Body()]) -> dict:
        """Run one text turn as if the person had said it (the console's 试一试).

        Typing a request is the cheapest way to test a turn - no microphone, no wake
        word, no standing next to the speaker - and it exercises the same runner, tools,
        TTS and playback path as a spoken turn.
        """
        text = str(patch.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="text is required")
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise HTTPException(status_code=409, detail="no audio plane connected")
        runner = app.state.runner
        if runner.busy:
            raise HTTPException(status_code=409, detail="a turn is already running")
        trace = TurnTrace(device_id="debug")
        _light(app, "thinking")
        # The console's 试一试 is one fresh utterance, as on the speaker: a wake
        # clears history, so a typed try must not inherit the previous try either
        # (measured 10:45:21: a weather answer added "周杰伦的歌没放上").
        runner.reset_history()
        if rules.verbless_device_name(text):
            # Same rule as the spoken path: a bare device name asks which way.
            line = phrases.book.render("device.ask_verb")
            runner.idle.touch()
            spoken = await runner.say(line, _speaking(app, client), trace)
            _record_turn(app, {"transcript": text, "reply": line, "audio_bytes": spoken,
                               "error": "", "tools": ["clarify.verb"], "stages": {}})
            _light_after_audio(app, spoken, cfg.audio.voice.rate)
            return {"reply": line, "error": "", "tools": ["clarify.verb"],
                    "stages": {}, "audio_bytes": spoken}
        for fast in (_music_transport, _volume_command, _context_command):
            if await fast(app, text, runner, client, trace, traces) is not None:
                _light_after_audio(app, app.state.last_turn["audio_bytes"] or 0,
                                   cfg.audio.voice.rate)
                return {
                    "reply": app.state.last_turn["reply"], "error": "", "tools":
                        app.state.last_turn["tools"], "stages": {}, "audio_bytes":
                        app.state.last_turn["audio_bytes"],
                }
        result = await runner.run(b"", _speaking(app, client), trace, transcript=text)
        await client.reply_finished(hold_music_s=_turn_hold(result))
        _light_after_audio(app, result.audio_bytes, cfg.audio.voice.rate,
                           _turn_light(result))
        _record_turn(app, result.to_dict())
        traces.add(trace)
        log.info("debug say: %r -> %r", text, result.reply)
        return {
            "reply": result.reply,
            "error": result.error,
            "tools": result.tools,
            "stages": result.stages,
            "audio_bytes": result.audio_bytes,
        }

    @app.post("/api/v1/debug/led", dependencies=[Depends(auth)])
    async def debug_led(patch: Annotated[dict, Body()]) -> dict:
        """Paint the ring directly. The only way to check colours without waking the box."""
        effect = str(patch.get("effect") or "")
        if effect not in LED_STATES:
            raise HTTPException(status_code=422, detail=f"effect must be one of {list(LED_STATES)}")
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise HTTPException(status_code=409, detail="no audio plane connected")
        await client.set_led(effect)
        return {"effect": effect}

    @app.get("/api/v1/music", dependencies=[Depends(auth)])
    async def music_status() -> dict:
        """What is playing and what was played: the console page and the self-tests."""
        service = getattr(app.state, "music", None)
        if service is None:
            return {"enabled": False}
        return {
            "enabled": True,
            **service.status(),
            "top": [{"artist": a, "plays": n} for a, n in service.history.top(5)],
            "last": service.history.last(),
        }

    @app.post("/api/v1/music", dependencies=[Depends(auth)])
    async def music_control(patch: Annotated[dict, Body()]) -> dict:
        """The console's buttons take the same path the voice transport words do.

        `play` with no query starts the radio batch; with a query it goes through
        the same intent pipeline the model's music_play tool uses.
        """
        service = getattr(app.state, "music", None)
        if service is None:
            raise HTTPException(status_code=409, detail="music is not enabled")
        action = str(patch.get("action") or "")
        if action == "play":
            query = str(patch.get("query") or "").strip()
            if query:
                from ...music import query as q
                from ...music.tools import _search
                tracks = await _search(service.source, q.plan(query))
            else:
                tracks = await service.source.random(service.cfg.queue_batch)
            started = await service.play(tracks)
            if started is None:
                raise HTTPException(status_code=502, detail="the library did not answer")
        elif action in {"pause", "resume", "stop", "next", "prev"}:
            await getattr(service, action)()
        else:
            raise HTTPException(
                status_code=422, detail="action must be play/pause/resume/stop/next/prev")
        return await music_status()

    @app.post("/api/v1/debug/tool", dependencies=[Depends(auth)])
    async def debug_tool(patch: Annotated[dict, Body()]) -> dict:
        """Call one registered tool and render it - silently.

        The console's dry-run (docs/console.md mirror table) and the silent
        self-tests both need the exact path a model's call takes, minus the
        speaker: handler -> ToolResult -> InteractionSpec -> PhraseBook.
        """
        runner = app.state.runner
        name = str(patch.get("name") or "")
        args = patch.get("args") or {}
        handler = runner._handlers.get(name)
        if handler is None:
            raise HTTPException(status_code=404, detail=f"no such tool: {name}")
        from ...tools.contract import ToolResult
        result = await handler(args if isinstance(args, dict) else {})
        rendered = runner._render_tool_result(name, result) \
            if isinstance(result, ToolResult) else str(result)
        return {
            "name": name,
            "status": result.status if isinstance(result, ToolResult) else "str",
            "payload": result.payload if isinstance(result, ToolResult) else {},
            "rendered": rendered,
        }

    @app.get("/api/v1/music/search", dependencies=[Depends(auth)])
    async def music_search(q: str = "") -> dict:
        """Search without playing: the console's picker. Same pipeline as music_play."""
        service = getattr(app.state, "music", None)
        if service is None:
            raise HTTPException(status_code=409, detail="music is not enabled")
        from ...music import query as mq
        from ...music.tools import _search
        plan = mq.plan(q or "")
        tracks = await service.source.random(20) if plan.random \
            else await _search(service.source, plan)
        return {"query": q, "tracks": [
            {"id": t.id, "title": t.title, "artist": t.artist, "duration": t.duration}
            for t in tracks[:20]]}

    @app.get("/api/v1/debug/prompt", dependencies=[Depends(auth)])
    async def debug_prompt() -> dict:
        """The system prompt as the next turn will receive it (console preview)."""
        runner = app.state.runner
        return {"prompt": runner.build_prompt()}

    @app.get("/api/v1/memory", dependencies=[Depends(auth)])
    async def memory_list() -> dict:
        store = getattr(app.state, "memory", None)
        if store is None:
            return {"items": []}
        return {"items": [i.as_dict() for i in store.items()]}

    @app.post("/api/v1/memory", dependencies=[Depends(auth)])
    async def memory_edit(patch: Annotated[dict, Body()]) -> dict:
        """add / confirm / remove from the console. Candidates only become memory
        here: the injector reads confirmed entries and nothing else."""
        store = getattr(app.state, "memory", None)
        if store is None:
            raise HTTPException(status_code=409, detail="memory is not enabled")
        action = str(patch.get("action") or "")
        item_id = str(patch.get("id") or "")
        text = str(patch.get("text") or "")
        if action == "add":
            item = store.add(text, source="console", confirmed=True)
        elif action == "confirm":
            item = store.confirm(item_id, text or None)
            if item is None:
                raise HTTPException(status_code=404, detail="no such memory item")
        elif action == "remove":
            if not store.remove(item_id):
                raise HTTPException(status_code=404, detail="no such memory item")
            item = None
        else:
            raise HTTPException(status_code=422, detail="action must be add/confirm/remove")
        return {"items": [i.as_dict() for i in store.items()],
                "item": item.as_dict() if item else None}

    # ---------------------------------------------------------------- phrasebook
    @app.get("/api/v1/phrases", dependencies=[Depends(auth)])
    async def phrases_state() -> dict:
        """Every phrase template + the override file state (P6)."""
        return phrasebook.document(Path(cfg.system.data_dir))

    @app.put("/api/v1/phrases", dependencies=[Depends(auth)])
    async def phrases_save(payload: Annotated[dict, Body()]) -> dict:
        """Replace the override set. All-or-nothing: an invalid key writes nothing."""
        overrides = payload.get("overrides")
        if not isinstance(overrides, dict):
            raise HTTPException(status_code=422, detail="overrides must be an object")
        try:
            phrasebook.save(Path(cfg.system.data_dir), overrides)
        except phrases.PhraseOverrideError as exc:
            raise HTTPException(status_code=422, detail=exc.errors) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"cannot write phrases.yaml: {exc}") from exc
        log.info("phrasebook: %d override(s) saved from the console", len(overrides))
        return phrasebook.document(Path(cfg.system.data_dir))

    @app.post("/api/v1/phrases/reset", dependencies=[Depends(auth)])
    async def phrases_reset(payload: Annotated[dict, Body()]) -> dict:
        """Reset all overrides, or the named keys."""
        keys = payload.get("keys")
        if keys is not None and not isinstance(keys, list):
            raise HTTPException(status_code=422, detail="keys must be a list of phrase keys")
        phrasebook.clear(Path(cfg.system.data_dir),
                         [str(key) for key in keys] if keys is not None else None)
        log.info("phrasebook: reset %s", "all" if keys is None else len(keys))
        return phrasebook.document(Path(cfg.system.data_dir))

    @app.get("/api/v1/panels", dependencies=[Depends(auth)])
    async def panels() -> dict:
        """The console's nav and pages, declared here (docs/console.md).

        A new feature registers its PanelSpec where it is assembled and the console
        grows a panel with no frontend change; `enabled: false` greys it out with
        the reason instead of hiding it.
        """
        return {"revision": app.state.panels_revision, "panels": app.state.panels}

    @app.get("/api/v1/voice/threshold", dependencies=[Depends(auth)])
    async def voice_threshold() -> dict:
        """The threshold the wake model actually uses, read from the keywords file.

        `audio.voice.kws_threshold` is inert; showing the real file (and what it says)
        is the only way a person can tell whether changing a number did anything.
        """
        voice = cfg.audio.voice
        path = (
            Path(voice.kws_keywords_file)
            if voice.kws_keywords_file
            else Path(voice.kws_model_dir) / "keywords.txt"
        )
        return keyword_meta(path)

    @app.get("/api/v1/audio", dependencies=[Depends(auth)])
    async def audio_status() -> dict:
        """Live numbers from the audio plane: frames, drops, jitter, rtf.

        Asks for a fresh snapshot first - the periodic message is up to 2 s old, and a
        stale counter is exactly the kind of number this project refuses to print.
        """
        supervisor = getattr(app.state, "audio", None)
        if supervisor is None:
            return {"running": False, "error": "audio plane not started"}
        if supervisor.client is not None:
            try:
                await supervisor.client.request_metrics(timeout_s=0.5)
            except (ConnectionError, TimeoutError):
                pass
        return supervisor.status()

    @app.get("/api/v1/devices", dependencies=[Depends(auth)])
    async def devices() -> list[dict]:
        """Every configured device, including the ones this build cannot drive yet."""
        out: list[dict] = []
        seen: set[str] = set()
        adapters = app.state.adapters
        for adapter in adapters.adapters.values():
            info = await adapter.probe()
            seen.add(info.id)
            out.append(
                {
                    "id": info.id,
                    "adapter": info.adapter,
                    "model": info.model,
                    "room": info.room,
                    "host": info.host,
                    "transport": info.transport.value,
                    "status": "ready",
                    "capabilities": sorted(info.capabilities),
                    "missing_for_barge_in": [c.value for c in info.missing_for_barge_in()],
                }
            )
        for dev in cfg.devices:
            if not dev.enabled or dev.id in seen:
                continue
            out.append(
                {
                    "id": dev.id,
                    "adapter": dev.adapter,
                    "model": "",
                    "room": dev.room,
                    "host": dev.host,
                    "transport": "unknown",
                    "status": "unavailable",
                    "error": adapters.errors.get(dev.id, "adapter not available"),
                    "capabilities": [],
                    "missing_for_barge_in": [],
                }
            )
        return out

    # ---------------------------------------------------------------- setup wizard
    # A fresh install has to become a talking speaker without editing YAML by hand
    # (C-UI P4). These endpoints expose the same steps the wizard shows: which keys
    # exist, whether the models are on disk, and writing the first device - which
    # also regenerates the keywords file and restarts the audio plane, because the
    # wake model reads the keywords file once at process start.
    def _wizard_state() -> dict:
        cfg_now = getattr(app.state, "cfg", cfg)
        supervisor = getattr(app.state, "audio", None)

        def sherpa_model() -> Path | None:
            """The local ASR dir, found through the chain - provider names differ per config."""
            for name in cfg_now.providers.asr.chain:
                provider = cfg_now.providers.catalog.get(name)
                if provider is None or str(getattr(provider, "kind", "")) != "sherpa":
                    continue
                model = str(getattr(provider, "model", "") or "")
                if model:
                    return Path(model).expanduser()
            return None

        def dir_has(directory: Path | None, *names: str) -> bool:
            return bool(directory is not None and directory.is_dir()
                        and all((directory / name).is_file() for name in names))

        asr = sherpa_model()
        asr_ok = bool(asr is not None and asr.is_dir()
                      and (asr / "tokens.txt").is_file()
                      and any((asr / name).is_file() for name in ("model.int8.onnx", "model.onnx")))
        voice = cfg_now.audio.voice
        kws = Path(voice.kws_model_dir).expanduser() if voice.kws_model_dir else None
        vad = Path(voice.vad_model_path).expanduser() if voice.vad_model_path else None
        keywords = Path(voice.kws_keywords_file).expanduser() if voice.kws_keywords_file else None
        def provider_usable(provider: object) -> bool:
            """Can this chain entry actually answer right now?"""
            kind = str(getattr(provider, "kind", ""))
            names = [str(n) for n in (getattr(provider, "api_key_env", ""),
                                      getattr(provider, "app_id_env", ""),
                                      getattr(provider, "api_secret_env", "")) if n]
            if not all(secrets.has(name) for name in names):
                return False
            if kind == "sherpa":
                model = str(getattr(provider, "model", "") or "")
                return bool(model and Path(model).expanduser().exists())
            return bool(getattr(provider, "base_url", ""))
        stage_ok: dict[str, bool] = {}
        missing_keys: set[str] = set()
        for stage in ("asr", "tts", "llm"):
            chain = list(getattr(cfg_now.providers, stage).chain)
            usable = [name for name in chain
                      if provider_usable(cfg_now.providers.catalog.get(name))]
            stage_ok[stage] = bool(usable)
            if not usable:
                for name in chain:
                    provider = cfg_now.providers.catalog.get(name)
                    if provider is None:
                        continue
                    missing_keys.update(str(n) for n in (
                        getattr(provider, "api_key_env", ""),
                        getattr(provider, "app_id_env", ""),
                        getattr(provider, "api_secret_env", "")) if n and not secrets.has(n))
        keys = {
            "stages": stage_ok,
            "missing": sorted(missing_keys),
            "missing_by_stage": {
                stage: sorted({str(name) for provider_name in
                               getattr(cfg_now.providers, stage).chain
                               for name in (
                                   getattr(cfg_now.providers.catalog.get(provider_name),
                                           "api_key_env", ""),
                                   getattr(cfg_now.providers.catalog.get(provider_name),
                                           "app_id_env", ""),
                                   getattr(cfg_now.providers.catalog.get(provider_name),
                                           "api_secret_env", ""))
                               if name and not secrets.has(str(name))})
                for stage in ("asr", "tts", "llm")
                if not stage_ok[stage]
            },
        }
        def kws_ok(directory: Path | None) -> bool:
            if directory is None:
                return False
            try:
                model_files(directory)
            except KwsUnavailable:
                return False
            return True

        models = {
            "asr": asr_ok,
            "vad": bool(vad and vad.is_file()),
            "kws": kws_ok(kws),
        }
        device = cfg_now.devices[0] if cfg_now.devices else None
        verified = bool((getattr(app.state, "last_turn", None) or {}).get("audio_bytes"))
        steps = [
            {"id": "keys", "title": "填 key", "done": all(stage_ok.values()), "detail": keys},
            {"id": "models", "title": "本地模型", "done": all(models.values()), "detail": models},
            {"id": "device", "title": "添加音箱", "done": device is not None,
             "detail": ({"id": device.id, "host": device.host, "room": device.room}
                        if device else {})},
            {"id": "wake", "title": "唤醒词",
             "done": bool(device and device.wake_words and keywords and keywords.is_file()),
             "detail": {"words": list(device.wake_words) if device else [],
                        "path": str(keywords) if keywords else ""}},
            {"id": "verify", "title": "试一句", "done": verified},
        ]
        return {
            "next_step": next((step["id"] for step in steps if not step["done"]), "done"),
            # The first four steps are "can it work at all"; the try-a-sentence step is a
            # nudge, not a blocker (a working speaker should not open the wizard forever).
            "complete": all(step["done"] for step in steps[:4]),
            "steps": steps,
            "config_path": str(config_path) if config_path else "",
            "config_exists": bool(config_path and Path(config_path).exists()),
            "audio_plane": bool(supervisor is not None and supervisor.running),
            "device": ({"id": device.id, "host": device.host, "room": device.room,
                        "wake_words": list(device.wake_words)} if device else None),
            "keywords_file": str(keywords) if keywords else "",
            "keywords_exists": bool(keywords and keywords.is_file()),
            "models_command": "house-ear fetch-models --dest /data/models/sensevoice "
                              "--vad-dest /data/models/silero_vad.onnx",
        }

    def _apply_config(updated: Config) -> None:
        """Replace the live config object in place, so every closure sees the new values."""
        for name in Config.model_fields:
            setattr(cfg, name, getattr(updated, name))
        app.state.cfg = cfg

    async def _write_devices(updated: Config) -> None:
        """Persist devices[], regenerate the wake words, restart the plane."""
        if config_path is None:
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        if not updated.audio.voice.kws_keywords_file:
            # A config that came from schema defaults has no keywords path; the wizard
            # still has to leave one behind, next to the config it just wrote.
            updated.audio.voice.kws_keywords_file = str(
                Path(config_path).resolve().parent / "keywords.txt")
        save_config(Path(config_path), updated)
        _apply_config(updated)
        words = [word for device in updated.devices for word in device.wake_words]
        keywords = updated.audio.voice.kws_keywords_file
        if keywords and words:
            try:
                write_keywords_file(Path(keywords).expanduser(), words)
            except OSError as exc:  # noqa: BLE001 - the state reports what is missing
                log.warning("setup: could not write keywords file: %s", exc)
        app.state.adapters = build_adapters(updated)
        runner = getattr(app.state, "runner", None)
        if runner is not None:
            runner.wake_words = tuple(words)
        supervisor = getattr(app.state, "audio", None)
        if supervisor is not None:
            # The plane holds the device list, the wake model and the keywords file
            # from its own startup: a new speaker or a new wake word needs a restart.
            supervisor.cfg = updated
            try:
                await supervisor.stop()
                await supervisor.start()
            except Exception as exc:  # noqa: BLE001 - the wizard shows this sentence
                supervisor.error = f"{type(exc).__name__}: {exc}"
                log.warning("audio plane restart after setup failed: %s", exc)
            supervisor.watch()

    @app.get("/api/v1/setup", dependencies=[Depends(auth)])
    async def setup_state() -> dict:
        """What the install wizard still needs; computed from files and keys, not claims."""
        return _wizard_state()

    @app.post("/api/v1/setup/keys", dependencies=[Depends(auth)])
    async def setup_keys(payload: Annotated[dict, Body()]) -> dict:
        """Store provider keys from the wizard. Values are write-only, as everywhere."""
        values = payload.get("values")
        if not isinstance(values, dict) or not values:
            raise HTTPException(status_code=422, detail="values must be a non-empty object")
        for name, value in values.items():
            if not isinstance(value, str):
                raise HTTPException(status_code=422,
                                    detail=f"secret {name} must be a string")
            try:
                secrets.put(str(name), value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _wizard_state()

    def _wake_words(payload: dict) -> list[str]:
        """Validated wake words: the file is parsed by sherpa, so a newline breaks it."""
        raw = payload.get("wake_words")
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise HTTPException(status_code=422, detail="wake_words must be a list of strings")
        words: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                raise HTTPException(status_code=422, detail="every wake word must be a string")
            word = item.strip()
            if not word:
                continue
            if len(word) > 12:
                raise HTTPException(status_code=422, detail="唤醒词最多 12 个字")
            if any(char.isspace() or ord(char) < 32 for char in word):
                raise HTTPException(status_code=422, detail="唤醒词不能包含空格或换行")
            words.append(word)
        if len(words) > 3:
            raise HTTPException(status_code=422, detail="最多 3 个唤醒词")
        return words

    def _host(value: object) -> str:
        host = str(value or "").strip()
        if not host:
            raise HTTPException(status_code=422, detail="host is required")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]           # [::1] -> ::1, which is what connect() wants
        if "://" in host:
            raise HTTPException(status_code=422, detail="host 只填地址，不要带 http://")
        if len(host) > 64 or any(char.isspace() for char in host):
            raise HTTPException(status_code=422, detail="host 不能含空格，最长 64 字符")
        if host.startswith("-") or host.endswith("-"):
            raise HTTPException(status_code=422, detail="host 不能以连字符开头或结尾")
        return host

    def _room(value: object) -> str:
        room = str(value or "").strip()
        if len(room) > 32 or any(ord(char) < 32 for char in room):
            raise HTTPException(status_code=422, detail="房间名最长 32 字，不能含控制字符")
        return room

    @app.post("/api/v1/setup/device", dependencies=[Depends(auth)])
    async def setup_device(payload: Annotated[dict, Body()]) -> dict:
        """Add (or replace) the speaker, write the wake words, restart the plane."""
        host = _host(payload.get("host"))
        room = _room(payload.get("room"))
        words = _wake_words(payload)
        if config_path is None or not Path(config_path).exists():
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        try:
            current = load_config(Path(config_path))
        except ConfigError as exc:
            raise HTTPException(status_code=409, detail=f"config is not readable: {exc}") from exc
        device_id = (str(payload.get("id") or "living").strip() or "living").lower()
        existing = next((dev for dev in current.devices if dev.id == device_id), None)
        # Merge onto the device that is already there: a wizard edit must not silently
        # drop fields it does not show (listen_port, LED colours, volume target).
        base = existing.model_dump(mode="json") if existing is not None else {}
        base.update({
            "id": device_id,
            "adapter": str(payload.get("adapter") or base.get("adapter") or "xiaoai").strip()
            or "xiaoai",
            "host": host,
            "room": room,
            "wake_words": words or ["你好小瑞"],
            "enabled": True,
        })
        try:
            device = DeviceConfig.model_validate(base)
        except ValidationError as exc:
            # pydantic's raw errors carry exception objects in `ctx`; only the readable
            # parts travel over HTTP.
            raise HTTPException(status_code=422, detail=[
                {"loc": list(error.get("loc", ())), "msg": str(error.get("msg", ""))}
                for error in exc.errors(include_url=False)
            ]) from exc
        others = [dev for dev in current.devices if dev.id != device.id]
        merged = current.model_copy(update={"devices": [device, *others]})
        try:
            updated = Config.model_validate(merged.model_dump(mode="json"))
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
        await _write_devices(updated)
        log.info("setup: device %s written (host=%s, wake=%s)", device.id, device.host,
                 device.wake_words)
        return _wizard_state()

    @app.post("/api/v1/setup/wake", dependencies=[Depends(auth)])
    async def setup_wake(payload: Annotated[dict, Body()]) -> dict:
        """Change the wake words of the configured speaker and regenerate the file."""
        words = _wake_words(payload)
        if not words:
            raise HTTPException(status_code=422, detail="wake_words is required")
        if config_path is None or not Path(config_path).exists():
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        current = load_config(Path(config_path))
        if not current.devices:
            raise HTTPException(status_code=409, detail="add a speaker first")
        first = current.devices[0].model_copy(update={"wake_words": words})
        updated = current.model_copy(update={"devices": [first, *current.devices[1:]]})
        await _write_devices(updated)
        return _wizard_state()

    @app.post("/api/v1/setup/probe", dependencies=[Depends(auth)])
    async def setup_probe(payload: Annotated[dict, Body()]) -> dict:
        """Is the host a reachable speaker? TCP + SSH banner, no login, no commands."""
        return await _probe_host(_host(payload.get("host")))

    # ---------------------------------------------------------------- device admin
    def _device_or_404(device_id: str, config: Config) -> DeviceConfig:
        for device in config.devices:
            if device.id == device_id:
                return device
        raise HTTPException(status_code=404, detail=f"no such device: {device_id}")

    async def _device_detail(device_id: str) -> dict:
        device = _device_or_404(device_id, app.state.cfg)
        live: dict = {}
        adapters_now = getattr(app.state, "adapters", None)
        adapter = adapters_now.adapters.get(device_id) if adapters_now is not None else None
        if adapter is not None:
            info = await adapter.probe()
            live = {"model": info.model, "transport": info.transport.value,
                    "status": "ready", "capabilities": sorted(info.capabilities)}
        return {"device": device.model_dump(mode="json"), "live": live}

    @app.get("/api/v1/devices/{device_id}", dependencies=[Depends(auth)])
    async def device_detail(device_id: str) -> dict:
        """One device's editable config plus what the adapter reports right now."""
        return await _device_detail(device_id)

    @app.put("/api/v1/devices/{device_id}", dependencies=[Depends(auth)])
    async def device_update(device_id: str, payload: Annotated[dict, Body()]) -> dict:
        """Edit one device. Fields not sent are preserved (LED colours, volume target...)."""
        if config_path is None or not Path(config_path).exists():
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        try:
            current = load_config(Path(config_path))
        except ConfigError as exc:
            raise HTTPException(status_code=409, detail=f"config is not readable: {exc}") from exc
        device = _device_or_404(device_id, current)
        base = device.model_dump(mode="json")
        if "host" in payload:
            base["host"] = _host(payload.get("host"))
        if "room" in payload:
            base["room"] = _room(payload.get("room"))
        if "wake_words" in payload:
            base["wake_words"] = _wake_words(payload) or list(device.wake_words)
        if "adapter" in payload:
            adapter = str(payload.get("adapter") or "").strip()
            if not adapter:
                raise HTTPException(status_code=422, detail="adapter cannot be empty")
            base["adapter"] = adapter
        if "enabled" in payload:
            base["enabled"] = bool(payload.get("enabled"))
        if "volume" in payload:
            try:
                volume = int(payload.get("volume"))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="volume must be an int 0..255") from exc
            if not 0 <= volume <= 255:
                raise HTTPException(status_code=422, detail="volume must be an int 0..255")
            base["volume"] = volume
        if "listen_port" in payload:
            port = payload.get("listen_port")
            if port is None:
                base["listen_port"] = None
            elif isinstance(port, int) and 1 <= port <= 65535:
                base["listen_port"] = port
            else:
                raise HTTPException(status_code=422, detail="listen_port must be an int 1..65535")
        try:
            updated_device = DeviceConfig.model_validate(base)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=[
                {"loc": list(error.get("loc", ())), "msg": str(error.get("msg", ""))}
                for error in exc.errors(include_url=False)
            ]) from exc
        others = [item for item in current.devices if item.id != device_id]
        updated = current.model_copy(update={"devices": [updated_device, *others]})
        await _write_devices(updated)
        log.info("device %s updated from the console", device_id)
        return await _device_detail(device_id)

    @app.delete("/api/v1/devices/{device_id}", dependencies=[Depends(auth)])
    async def device_delete(device_id: str) -> dict:
        """Remove one device; the plane restarts with whatever remains."""
        if config_path is None or not Path(config_path).exists():
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        current = load_config(Path(config_path))
        _device_or_404(device_id, current)
        updated = current.model_copy(
            update={"devices": [item for item in current.devices if item.id != device_id]})
        await _write_devices(updated)
        log.info("device %s removed from the console", device_id)
        return {"removed": device_id, "devices": [item.id for item in updated.devices]}

    @app.post("/api/v1/devices/probe", dependencies=[Depends(auth)])
    async def devices_probe(payload: Annotated[dict, Body()]) -> dict:
        """Same SSH-banner probe as the wizard, for the edit form."""
        return await _probe_host(_host(payload.get("host")))

    # ---------------------------------------------------------------- MCP / HA
    async def _apply_ha(updated: Config) -> None:
        """Re-apply the HA MCP bridge at runtime: stop, drop its tools, reconnect.

        The HA tools are named local.ha_*; they are removed by name (the registry can
        only drop a whole source prefix otherwise) and re-registered from the new
        bridge. A failed connect leaves the bridge object in place so the page can
        show its last_error, and the model simply has no HA tools until it works.
        """
        old = getattr(app.state, "ha", None)
        if old is not None:
            with contextlib.suppress(Exception):
                await old.stop()
        ha_names = [spec.name for spec, _ in local_tools if spec.name.startswith("local.ha_")]
        registry.unregister(ha_names)
        local_tools[:] = [(spec, handler) for spec, handler in local_tools
                          if not spec.name.startswith("local.ha_")]
        bridge: HaBridge | None = None
        if updated.tools.ha.base_url:
            bridge = HaBridge(updated.tools.ha, Path(updated.system.data_dir))
            if await bridge.start():
                for spec, handler in bridge.tool_specs():
                    registry.register(spec, handler)
                    local_tools.append((spec, handler))
                log.info("ha: re-applied, %d tools", len(bridge.tool_specs()))
            else:
                log.warning("ha: re-apply failed (%s) - HA tools are gone",
                            getattr(bridge.client, "last_error", ""))
        app.state.ha = bridge
        runner = getattr(app.state, "runner", None)
        if runner is not None:
            runner.set_tools(list(local_tools))
            runner.extra_context = _context_provider(app, bridge)

    def _mcp_state() -> dict:
        ha = cfg.tools.ha
        bridge = getattr(app.state, "ha", None)
        status = bridge.status if bridge is not None else {
            "connected": False, "latency_ms": None, "tools": 0, "entities": 0}
        return {
            "ha": {
                "base_url": ha.base_url,
                "token_env": ha.token_env,
                "has_token": secrets.has(ha.token_env),
                "wired": bool(ha.base_url),
                **status,
                "error": getattr(getattr(bridge, "client", None), "last_error", "") or "",
            },
            "servers": [server.model_dump(mode="json") for server in cfg.tools.mcp_servers],
            # tools.mcp_servers is a reserved shape: the generic MCP client exists, but
            # only HA is wired end to end. Say so instead of pretending.
            "generic_wired": False,
        }

    @app.get("/api/v1/mcp", dependencies=[Depends(auth)])
    async def mcp_state() -> dict:
        return _mcp_state()

    @app.put("/api/v1/mcp", dependencies=[Depends(auth)])
    async def mcp_update(payload: Annotated[dict, Body()]) -> dict:
        """Write the HA endpoint (and optionally its token), then reconnect live."""
        if config_path is None or not Path(config_path).exists():
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        try:
            current = load_config(Path(config_path))
        except ConfigError as exc:
            raise HTTPException(status_code=409, detail=f"config is not readable: {exc}") from exc
        merged = current.model_dump(mode="json")
        patch = payload.get("ha")
        if patch is not None:
            if not isinstance(patch, dict):
                raise HTTPException(status_code=422, detail="ha must be an object")
            base_url = str(patch.get("base_url", merged["tools"]["ha"]["base_url"]) or "").strip()
            if base_url and not base_url.startswith(("http://", "https://")):
                raise HTTPException(status_code=422,
                                    detail="base_url must start with http:// or https://")
            merged["tools"]["ha"]["base_url"] = base_url.rstrip("/")
            token = patch.get("token")
            if isinstance(token, str) and token.strip():
                try:
                    secrets.put(str(merged["tools"]["ha"]["token_env"]), token.strip())
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
        if "servers" in payload:
            if not isinstance(payload["servers"], list):
                raise HTTPException(status_code=422, detail="servers must be a list")
            merged["tools"]["mcp_servers"] = payload["servers"]
        try:
            updated = Config.model_validate(merged)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=[
                {"loc": list(error.get("loc", ())), "msg": str(error.get("msg", ""))}
                for error in exc.errors(include_url=False)
            ]) from exc
        save_config(Path(config_path), updated)
        _apply_config(updated)
        await _apply_ha(updated)
        log.info("mcp: config written from the console (ha=%s)", updated.tools.ha.base_url)
        return _mcp_state()

    @app.post("/api/v1/mcp/test", dependencies=[Depends(auth)])
    async def mcp_test(payload: Annotated[dict, Body()]) -> dict:
        """Connect a throwaway HA bridge and report what it lists; nothing is saved."""
        base_url = str(payload.get("base_url") or cfg.tools.ha.base_url or "").strip()
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422,
                                detail="base_url must start with http:// or https://")
        probe_cfg = cfg.tools.ha.model_copy(update={"base_url": base_url})
        bridge = HaBridge(probe_cfg, Path(cfg.system.data_dir))
        try:
            ok = await asyncio.wait_for(bridge.start(), timeout=10.0)
            result = {"ok": ok, **bridge.status,
                      "error": "" if ok else (getattr(bridge.client, "last_error", "") or "")}
            return result
        except TimeoutError:
            return {"ok": False, "connected": False, "tools": 0, "entities": 0,
                    "error": "timeout after 10 s"}
        finally:
            with contextlib.suppress(Exception):
                await bridge.stop()

    # ---------------------------------------------------------------- secrets
    @app.get("/api/v1/secrets", dependencies=[Depends(auth)])
    async def list_secrets() -> dict:
        """Which keys are configured. Names only - there is no endpoint that returns a value."""
        return {"names": secrets.stored_names()}

    @app.post("/api/v1/secrets", dependencies=[Depends(auth)])
    async def put_secret(payload: Annotated[dict, Body()]) -> dict:
        """Store a key typed in the console. An empty value deletes it.

        The value is never echoed, never logged, and never written into config.yaml: it
        goes to `secrets.yaml` (0600) next to the runtime state, which the providers and the
        search tool read through `secrets.get()`.
        """
        name = str(payload.get("name") or "").strip()
        value = str(payload.get("value") or "").strip()
        try:
            secrets.put(name, value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Providers read the key at call time, so nothing needs rebuilding: the next search
        # or LLM request sees it.
        return {"name": name, "has_key": secrets.has(name)}

    @app.get("/api/v1/tools", dependencies=[Depends(auth)])
    async def tools() -> dict:
        return {"count": len(registry.names()), "names": registry.names()}

    @app.get("/api/v1/turns", dependencies=[Depends(auth)])
    async def turns(limit: int = 20) -> dict:
        """Recent turns, newest first - the console's 对话 panel.

        `/api/v1/turns` without a limit used to mean "the last turn only"; the
        conversation panel reads the ring, and the single-turn shape is still
        available as `turns[0]`.
        """
        ring = getattr(app.state, "recent_turns", None) or []
        recent = list(ring)[-max(1, min(limit, 200)):]
        recent.reverse()
        return {"turns": recent, "last": getattr(app.state, "last_turn", None)}

    @app.get("/api/v1/logs", dependencies=[Depends(auth)])
    async def logs(lines: int = 200) -> dict:
        """The tail of the service log. Polled by the console's 日志 panel."""
        path = Path(cfg.system.data_dir) / LOG_FILE_NAME
        return {"lines": tail_lines(path, max(1, min(lines, 2000))),
                "path": str(path), "exists": path.is_file()}

    @app.get("/api/v1/traces", dependencies=[Depends(auth)])
    async def recent_traces(limit: int = 20) -> dict:
        """Latency marks for recent turns. Empty until a turn runs through the plane."""
        return {"limit": max(1, min(limit, 200)), "turns": traces.recent(limit)}

    # The console is a Vue SPA with history routing: /panel/music is not a file on
    # disk, yet a reload, a bookmark or a link from the NAS dashboard must still
    # land on the app. So: mount the hashed asset directory, then answer every
    # remaining non-API path with index.html. Registered after all API routes, a
    # real endpoint always wins, and an /api/* typo stays a JSON 404 instead of
    # silently becoming the HTML shell.
    if WEB_DIST.is_dir():
        assets = WEB_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def console(full_path: str) -> HTMLResponse:
        """Serve the SPA shell for console routes; never for API paths."""
        if full_path.startswith(("api/", "ws")):
            raise HTTPException(status_code=404, detail="Not Found")
        if WEB_DIST.is_dir():
            root = WEB_DIST.resolve()
            candidate = (root / full_path).resolve()
            if candidate.is_file() and root in candidate.parents:
                return FileResponse(candidate)
            index = root / "index.html"
            if index.is_file():
                return FileResponse(index)
        return HTMLResponse(FALLBACK)

    return app
