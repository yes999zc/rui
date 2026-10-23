"""Control-plane API. The browser never sees a secret."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from ... import __version__, secrets
from ...audio.ack import ack_wav_path
from ...audio.ack_render import render_ack_wav, render_filler_pcm
from ...audio.client import AudioPlaneError, AudioPlaneSupervisor
from ...audio.kws import keyword_meta
from ...audio.protocol import PlayFile, StopAudio
from ...config.schema import CONFIG_VERSION, LED_STATES, Config
from ...config.store import ConfigError, load_config, redacted, save_config
from ...device.registry import build_adapters
from ...doctor import run_doctor
from ...music import MusicService, NavidromeSource, PlayHistory
from ...music.tools import build_music_tools
from ...session import phrases, rules
from ...session.ack import ensure_ack_wav
from ...session.text import strip_wake
from ...session.turn import TurnRunner
from ...session.vendor import VendorWindow
from ...tools import build_local_tools, build_scheduler
from ...tools.contract import InteractionSpec, Permission, ToolResult, ToolSpec
from ...tools.ha import HaBridge
from ...tools.registry import ToolRegistry
from ...trace import TraceLog, TurnTrace

START = time.time()
log = logging.getLogger(__name__)
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
                 "item_title": "$.id", "item_sub": "$.adapter · $.model · $.status"},
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
    app.state.last_turn = {
        "transcript": clean, "reply": line, "audio_bytes": spoken, "error": "",
        "tools": [f"music_{action}"], "stages": {},
    }
    traces.add(trace)
    log.info("music transport: %r -> %s", clean, action)
    return line


# Seconds of quiet after a music transport command, so 「暂停」->「下一首」 works
# without repeating the wake word. The song is ducked for this window.
MUSIC_COMMAND_WINDOW_S = 2.0


def _turn_light(result: object, default: str = "listening") -> str:
    """The ring after a turn: a silent music answer leaves it on the song."""
    if getattr(result, "audio_bytes", 0) == 0 and _is_music_turn(result):
        return "music"
    return default


def _is_music_turn(result: object) -> bool:
    tools = getattr(result, "tools", None) or []
    return any(str(n).rsplit(".", 1)[-1].startswith("music") for n in tools)


def _turn_hold(result: object) -> float | None:
    """0 when this turn's tools were about the song itself (music/volume)."""
    tools = getattr(result, "tools", None) or []
    for name in tools:
        tool = str(name).rsplit(".", 1)[-1]
        if tool.startswith("music") or tool == "volume":
            return 0.0
    return None


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
    app.state.last_turn = {
        "transcript": clean, "reply": line, "audio_bytes": spoken, "error": "",
        "tools": ["volume"], "stages": {},
    }
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
    registry = ToolRegistry()
    # One assembly for everything local: information tools and reminders. Reminders used
    # to be reachable only through tools.reminders.build_tools, which nothing imported -
    # the tools existed, the scheduler existed, and the model could never call them.
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
        music_service = MusicService(source, history, _never_sender, cfg.music)
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
                runner.extra_context = _ha_context(ha)
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
            except AudioPlaneError as exc:
                supervisor.error = str(exc)
                log.warning("audio plane did not start: %s", exc)
            app.state.audio = supervisor
            client = supervisor.client
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
                    if rules.stop_intent(clean, cfg.session.end_words):
                        # A stop word with a song owning the speaker stops the sound;
                        # 「退下吧」 only dismisses the conversation and the song keeps
                        # playing - the old build split these between the conversation
                        # stop and the music tools. Measured 21:10: 「停止」 answered
                        # 好。 and the music played on. Through the service so the
                        # queue state dies with the sound.
                        # Decide by the music service, not by who owns the speaker:
                        # the wake's duck has already released the output by the time
                        # this runs, so `output_owner == "music"` was never true and
                        # 「停止」 left the song playing (measured 08:35).
                        # Stop words stop the song, dismissal included: the old build's
                        # stop_words path did MUSIC.stop + farewell, and 9哥 heard the
                        # song come back 12 s after 「退下吧」 as a bug (measured 09:17).
                        service = getattr(app.state, "music", None)
                        music_active = service is not None and service.state in (
                            "playing", "paused")
                        if music_active:
                            try:
                                await service.stop()
                            except Exception as exc:  # noqa: BLE001 - never crash the app
                                log.info("stop music failed: %s", exc)
                        elif not music_active and client.metrics is not None \
                                and client.metrics.output_owner == "music":
                            with contextlib.suppress(Exception):
                                await client.send(StopAudio())
                        # dismiss() closes the session before the reply: the idle clock
                        # used to keep running behind it and the farewell said goodbye a
                        # second time 15 s later (measured 20:42:16 -> 20:42:34).
                        spoken = await runner.dismiss(_speaking(app, client), trace)
                        # No reply_finished here: the session is closing, so the plane
                        # must not open a follow-up window after the goodbye.
                        _light_after_audio(app, spoken, cfg.audio.voice.rate, "off")
                        app.state.last_turn = {
                            "transcript": clean, "reply": "", "session_action": "stop",
                            "audio_bytes": spoken, "error": "", "tools": [], "stages": {},
                        }
                        traces.add(trace)
                        log.info("stop intent: %r -> session closed", clean)
                        return
                    _light(app, "thinking")
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
                    if asr_report is not None:
                        result.stages.setdefault("asr", asr_report)
                    app.state.last_turn = result.to_dict()
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
                    # A wake opens a conversation window; the countdown to the farewell
                    # starts again from here (and from the end of every turn).
                    runner.idle.start()
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
            "devices": len(adapters),
            "device_errors": len(adapters.errors),
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
        for fast in (_music_transport, _volume_command):
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
        app.state.last_turn = result.to_dict()
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
    async def last_turn() -> dict:
        """The last turn as it ran: transcript, reply, per-stage provider and timing."""
        return getattr(app.state, "last_turn", None) or {"error": "no turn yet"}

    @app.get("/api/v1/traces", dependencies=[Depends(auth)])
    async def recent_traces(limit: int = 20) -> dict:
        """Latency marks for recent turns. Empty until a turn runs through the plane."""
        return {"limit": max(1, min(limit, 200)), "turns": traces.recent(limit)}

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:
            return FALLBACK

    return app
