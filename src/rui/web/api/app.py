"""Control-plane API. The browser never sees a secret."""
from __future__ import annotations

import asyncio
import base64
import collections
import contextlib
import hmac
import ipaddress
import json
import logging
import os
import re
import socket
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from ... import __version__, billing, secrets
from ... import setup_state as setup_store
from ...audio.ack import ack_wav_path
from ...audio.ack_render import render_ack_wav, render_filler_pcm
from ...audio.client import AudioPlaneSupervisor
from ...audio.kws import KwsUnavailable, keyword_meta, model_files, write_keywords_file
from ...audio.probe import safe_probe
from ...audio.protocol import PlayFile, StopAudio
from ...channels.supervisor import ChannelSupervisor
from ...channels.weixin import WeixinAdapter
from ...config.catalog import (
    coerce,
    field_specs,
    get_path,
    keyword_file,
    keyword_settings,
    migrate_legacy_music_credentials,
    music_credentials,
    music_credentials_path,
    music_credentials_state,
    set_path,
    write_music_credentials,
)
from ...config.catalog import (
    document as config_catalog,
)
from ...config.recovery import RecoveryInfo, clear_marker, remember_last_good
from ...config.recovery import restore as restore_config
from ...config.schema import (
    CONFIG_VERSION,
    LED_STATES,
    STAGES,
    Config,
    DeviceConfig,
    McpServerConfig,
    ProviderConfig,
    StageConfig,
)
from ...config.store import ConfigError, load_config, redacted, save_config
from ...device.loader import load_adapter_plugins
from ...device.registry import build_adapters
from ...doctor import run_doctor
from ...env import get as env_get
from ...log_setup import LOG_FILE_NAME, tail_lines
from ...music import MusicService, PlayHistory, build_source, get_preset
from ...music.names import AliasStore, NameResolver
from ...music.sources.presets import (
    missing_credentials,
    preset_document,
    preset_ids,
)
from ...music.sources.presets import (
    presets as music_presets,
)
from ...music.tools import build_music_tools
from ...plugins import ParamSpec, ParamType, PlugCatalog, ResolvedPlug
from ...providers import keycheck, presets
from ...providers.chain import build_stage
from ...providers.volc_voices import voices_for
from ...security.netguard import OutboundPolicy
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
from ...tools.mcp_manager import MCPManager
from ...tools.mcp_manager import test_server as test_generic_mcp_server
from ...tools.memory import build_memory_tools
from ...tools.registry import ToolRegistry
from ...trace import TraceLog, TurnTrace
from .device_rules import (
    DEFAULT_LISTEN_PORT,
    conflict_detail,
    effective_listen_port,
    is_inbound,
    port_conflict,
)
from .security import install_security_headers
from .setup_api import install_setup_routes

START = time.time()
log = logging.getLogger(__name__)
# Keys the wizard's "fill the keys" step requires. 讯飞 TTS is optional: without
# XFYUN_* the TTS chain falls back to SiliconFlow, which needs RUI_TTS_KEY only.

_DEFAULT_WEB_DIST = Path(__file__).resolve().parents[3].parent / "web" / "dist"
WEB_DIST = Path(env_get("RUI_WEB_DIR", _DEFAULT_WEB_DIST))

FALLBACK = """<!doctype html><html><head><meta charset="utf-8"><title>rui</title>
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;max-width:640px;margin:12vh auto;padding:0 24px;color:#111}
code{background:#f4f4f5;padding:2px 6px;border-radius:4px}</style></head>
<body><h1>rui</h1><p>Control plane is running. The console is not built yet:</p>
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
    text = output.speech_text
    # The turn synthesises everything before it returns (30 s budget measured 13:41:
    # a 607-char briefing timed out mid-read). Read the opening, keep the whole script
    # in history, and say why in the log rather than dying silently.
    if len(text) > DIRECT_SPEECH_MAX_CHARS:
        head = text[:DIRECT_SPEECH_MAX_CHARS]
        cut = max(head.rfind("。"), head.rfind("！"), head.rfind("？"))
        text = head[: cut + 1] if cut > 0 else head
        log.info("direct speech: truncated script %d -> %d chars for the turn budget",
                 len(output.speech_text), len(text))
    return DirectSpeech(
        text=text,
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


# A verbatim script longer than this is cut at a sentence boundary: the turn queues
# all of its TTS before returning, and the turn timeout is 30 s. 420 chars is roughly
# the 260-char 60s digest plus headroom; history keeps the untruncated script.
DIRECT_SPEECH_MAX_CHARS = 420


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


def _outbound_policy(cfg: Config) -> OutboundPolicy:
    """The single conversion from `security.outbound` to the runtime guard policy."""
    return OutboundPolicy.from_config(cfg.security)


# Sources that actually have a runtime builder. The list lives in the preset registry
# (`music/sources/presets.py`); `music.source` may still hold another name from a
# hand-edited YAML, but until an adapter exists the service cannot start and the panel
# below must not claim otherwise (IA audit §2.1/§5 Phase 1 + Phase 3).
# A change here is a change to the preset registry only.
MUSIC_REBUILD_FIELDS = frozenset({
    "music.enabled", "music.source", "music.secrets_path", "music.cache_dir",
})


def _music_source_supported(source: str) -> bool:
    preset = get_preset(source)
    return bool(preset is not None and preset.supported)


def _music_runtime_key(cfg: Config) -> tuple[str, ...]:
    """What a running service was assembled from; a change means rebuild it."""
    return (
        cfg.music.source,
        str(music_credentials_path(cfg)),
        str(Path(cfg.music.cache_dir).expanduser()),
        str(cfg.music.cache_tracks),
    )


def _new_music_source(cfg: Config, source_id: str | None = None):
    """The runtime source for one preset in file mode (credentials read per call)."""
    preset = get_preset(source_id or cfg.music.source)
    if preset is None or not preset.supported:
        return None
    return build_source(
        preset.id,
        music_credentials_path(cfg),
        Path(cfg.music.cache_dir), cfg.music.cache_tracks,
        policy=_outbound_policy(cfg),
    )


def _new_music_service(cfg: Config, sender: object) -> MusicService | None:
    """Assemble the music service for the configured source.

    One place for both the startup path and a console `music.enabled` toggle: the
    service owns the source, history, aliases and name resolver, and registering the
    nine tools is what makes the model able to play anything.
    """
    source = _new_music_source(cfg)
    if source is None:
        log.warning("music: source %r has no runtime builder; leaving music off",
                    cfg.music.source)
        return None
    history = PlayHistory(
        Path(cfg.system.data_dir) / "music_history.json", keep=cfg.music.history_max)
    aliases = AliasStore(Path(cfg.system.data_dir) / "music_aliases.json")
    names = NameResolver(aliases, timeout_s=6.0, policy=_outbound_policy(cfg))
    return MusicService(source, history, sender, cfg.music, names=names)  # type: ignore[arg-type]


def _flat_errors(exc: ValidationError, supplied: list[str] | None = None) -> list[dict]:
    """Pydantic errors as `[{loc: ["dotted.path"], msg}]` - the console's field red boxes.

    A dotted path is what the catalog talks in, so a 422 can mark the exact input that
    caused it even when the failure came from a whole-config validator. `supplied` holds
    the paths the request actually wrote: a model validator reports `devices.0.led`, but
    the person edited `devices.0.led.colors.off`, and that is the box to redden.
    """
    out: list[dict] = []
    candidates = list(supplied or [])
    for error in exc.errors(include_url=False):
        loc = [str(part) for part in error.get("loc", ())]
        message = str(error.get("msg", ""))
        if message.startswith("Value error, "):
            message = message[len("Value error, "):]
        loc_path = ".".join(loc)
        # Most specific supplied path that covers (or is covered by) the failing location.
        match = ""
        for candidate in candidates:
            if (loc_path == candidate or loc_path.startswith(candidate + ".")
                    or candidate.startswith(loc_path + ".")) and len(candidate) > len(match):
                match = candidate
        out.append({"loc": [match or loc_path or "config"], "msg": message})
    return out


def _autofill_music_paths(merged: dict, source: str) -> bool:
    """Point credentials/cache at the data dir for a supported source.

    Every preset in the Subsonic family uses the same pair, which is what keeps an
    existing `navidrome` deployment working unchanged. Returns False for an unknown
    source so the caller can warn instead of inventing a path.
    """
    if not _music_source_supported(source):
        return False
    data_dir = Path(str(merged.get("system", {}).get("data_dir") or "~")).expanduser()
    music = merged.setdefault("music", {})
    music["secrets_path"] = str(data_dir / "lan_media.json")
    music["cache_dir"] = str(data_dir / "music_cache")
    return True


def _build_panels(music_enabled: bool, music_source: str) -> list[dict]:
    """The PanelSpec table (docs/console.md, docs/config-catalog-contract.md §2).

    Expressions are deliberately tiny: `$.path` lookups, `map` for enum wording,
    `enabled_when: "$.x=v"` equality. The configuration pages are not blocks - they all
    use the `config-center` escape hatch with `component_args.groups`, so the same
    catalog drives five panels and a new field needs no frontend change.
    """
    # "Enabled" must mean "the service can actually be assembled": a hand-typed
    # source with no adapter used to leave the music panel green while the runtime
    # refused to start (audit §2.1). The page still opens: it is where you fix it.
    music_source_ok = _music_source_supported(music_source)
    music_panel_enabled = music_enabled and music_source_ok
    if not music_source_ok:
        # The invalid source is the actionable problem even when the feature is off:
        # the page must say so instead of only "enable music".
        available = ", ".join(item.id for item in music_presets() if item.supported)
        music_reason = (
            f"音乐源 {music_source!r} 不受支持：服务不会启动（可选：{available}）")
    elif not music_enabled:
        music_reason = f"music.enabled 未开启（配置的源：{music_source}）"
    else:
        music_reason = ""
    return [
        {
            "id": "overview", "title": "概览", "title_en": "Overview",
            "icon": "◎", "group": "status", "order": 10,
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
            # Phase 2 merge (docs/console-ia-audit.md §3/§5): live status and volume,
            # device lifecycle and speaker/audio parameters are one task. The three
            # legacy ids (`devices`, `devices-admin`, `speaker`) stay declared and
            # their routes redirect here for one release.
            "id": "devices", "title": "设备与音频", "title_en": "Devices & audio",
            "icon": "⌸", "group": "config", "order": 20,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/devices", "refresh_s": 10,
            "tabs": [
                {"id": "status", "title": {"zh": "状态与音量", "en": "Status & volume"}},
                {"id": "manage", "title": {"zh": "设备管理", "en": "Device manager"}},
                {"id": "audio", "title": {"zh": "音频与唤醒", "en": "Audio & wake"}},
            ],
            "blocks": [
                {"type": "list", "tab": "status", "title": "在线设备", "items": "$",
                 "item_title": "$.room · $.id",
                 "item_sub": "$.host · $.adapter · $.model · $.status"},
                {"type": "volume", "tab": "status", "title": "设备音量",
                 "href": "/api/v1/device/volume",
                 "label": "mysoftvol（设备原生刻度 0–255）", "min": 1, "max": 255,
                 "note": "读的是设备自己的混音器，不是配置里的 devices[].volume；厂商会自己改这个值，"
                         "所以这里显示的永远是设备实际音量。"},
                {"type": "component", "tab": "manage", "component": "device-manager",
                 "component_args": {}},
                # Everything that follows the speaker hardware: the device list, the
                # DSP/segmentation/playback numbers and the wake acknowledgement. These
                # are tier 2 in the catalog, so the tab and the directory agree.
                {"type": "config", "tab": "audio", "tier": 2,
                 "groups": ["devices", "audio.capture", "audio.voice",
                            "audio.playback", "audio.aec", "ack"],
                 "section_title": {"zh": "跟随音箱硬件的参数", "en": "Speaker hardware"},
                 "warning": {
                     "zh": "这些参数跟随音箱硬件：换另一款音箱要重新量。改完重启音频面。",
                     "en": "These follow the speaker hardware; re-measure for another "
                           "model. Restart the audio plane after saving.",
                 }},
            ],
        },
        {
            "id": "turns", "title": "对话", "title_en": "Conversation",
            "icon": "☰", "group": "status", "order": 25,
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
        # ---------------------------------------------------------- config center
        {
            "id": "assistant-session", "title": "助手与会话", "title_en": "Assistant & session",
            "icon": "☺", "group": "config", "order": 40,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/config/catalog", "refresh_s": 0, "blocks": [],
            "component": "config-center",
            "component_args": {"groups": ["assistant", "session"], "tier": 1},
        },
        {
            # Phase 2 merge: assistant reply/session behaviour and the phrasebook are
            # one task. `/panel/assistant-session` and `/panel/phrases` are aliases
            # for one release (docs/console-ia-audit.md §5 Phase 2).
            "id": "voice-assistant", "title": "语音助手", "title_en": "Voice assistant",
            "icon": "☺", "group": "config", "order": 40,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/config/catalog", "refresh_s": 0,
            "tabs": [
                {"id": "reply", "title": {"zh": "助手与回复", "en": "Assistant & replies"}},
                {"id": "phrases", "title": {"zh": "话术", "en": "Phrasing"}},
            ],
            "blocks": [
                {"type": "config", "tab": "reply", "tier": 1,
                 "groups": ["assistant", "session"]},
                {"type": "component", "tab": "phrases",
                 "component": "phrasebook-editor", "component_args": {}},
            ],
        },
        {
            "id": "speaker", "title": "音箱参数", "title_en": "Speaker",
            "icon": "⌸", "group": "config", "order": 41,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/config/catalog", "refresh_s": 0, "blocks": [],
            "component": "config-center",
            "component_args": {
                # Everything that follows the speaker hardware: the device list, the
                # DSP/segmentation/playback numbers and the wake acknowledgement. These
                # are tier 2 in the catalog, so the panel and the directory agree.
                "groups": ["devices", "audio.capture", "audio.voice",
                           "audio.playback", "audio.aec", "ack"],
                "tier": 2,
                "links": [{"label": {"zh": "设备管理", "en": "Device manager"},
                           "href": "/panel/devices-admin"}],
                "note": {
                    "zh": "这些参数跟随音箱硬件：换另一款音箱要重新量。改完重启音频面。",
                    "en": "These follow the speaker hardware; re-measure for another "
                          "model. Restart the audio plane after saving.",
                },
            },
        },
        {
            "id": "devices-admin", "title": "设备管理", "title_en": "Device manager",
            "icon": "⚙", "group": "config", "order": 42, "enabled": True,
            "enabled_reason": "",
            "source": "/api/v1/devices", "refresh_s": 0, "blocks": [],
            "component": "device-manager", "component_args": {},
        },
        {
            "id": "providers", "title": "外部资源", "title_en": "Providers",
            "icon": "⇄", "group": "config", "order": 43,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/config/catalog", "refresh_s": 0, "blocks": [],
            # Dedicated page: three stage chains, provider cards, voice audition.
            # The generic catalog editor was too dense for this page (9哥 review).
            "component": "providers-v2",
            "component_args": {},
        },
        {
            "id": "tools", "title": "工具", "title_en": "Tools",
            "icon": "⚒", "group": "config", "order": 44,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/config/catalog", "refresh_s": 0, "blocks": [],
            "component": "config-center",
            "component_args": {
                # Legacy page (redirects to /panel/integrations). Music moved to the
                # music page in Phase 2, so this list no longer contains `music`.
                "groups": ["tools.weather", "tools.news", "tools.search", "tools.ha",
                           "tools.mcp", "tools.exposure"],
                "tier": 4,
            },
        },
        {
            # Phase 2 merge: built-in tools, MCP/HA, the read-only registry and the
            # exposure policy are one destination. `tools.ha`/`tools.mcp` are owned
            # by McpManager (the only hot-write path) instead of being re-rendered
            # by ConfigCenter, so the old duplicate writer is gone.
            "id": "integrations", "title": "工具与集成", "title_en": "Tools & integrations",
            "icon": "⚒", "group": "config", "order": 44,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/config/catalog", "refresh_s": 0,
            "tabs": [
                {"id": "builtin", "title": {"zh": "内置工具", "en": "Built-in tools"}},
                {"id": "mcp", "title": {"zh": "MCP 与家庭助理", "en": "MCP & Home Assistant"}},
                {"id": "registry", "title": {"zh": "注册工具", "en": "Registry"}},
                {"id": "exposure", "title": {"zh": "暴露策略", "en": "Exposure policy"}},
            ],
            "blocks": [
                {"type": "config", "tab": "builtin", "tier": 4,
                 "groups": ["tools.weather", "tools.news", "tools.search"]},
                {"type": "component", "tab": "mcp",
                 "component": "mcp-manager", "component_args": {}},
                {"type": "component", "tab": "registry",
                 "component": "tools-view", "component_args": {"embedded": True}},
                {"type": "config", "tab": "exposure", "tier": 4,
                 "groups": ["tools.exposure"]},
            ],
        },
        {
            "id": "phrases", "title": "话术", "title_en": "Phrasing",
            "icon": "Aa", "group": "config", "order": 45,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/phrases", "refresh_s": 0,
            "blocks": [],
            # The escape hatch (docs/console.md §2.5): editing 49 templates with slot
            # chips, dirty tracking and per-key reset is a sequence, not a block set.
            "component": "phrasebook-editor", "component_args": {},
        },
        {
            "id": "mcp", "title": "MCP / HA", "title_en": "MCP / HA",
            "icon": "⌗", "group": "config", "order": 46,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/mcp", "refresh_s": 5, "blocks": [],
            "component": "mcp-manager", "component_args": {},
        },
        {
            "id": "foundation", "title": "底子·高级", "title_en": "Foundation (advanced)",
            "icon": "⚙", "group": "config", "order": 47,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/config/catalog", "refresh_s": 0, "blocks": [],
            "component": "config-center",
            "component_args": {
                # Tier 1 foundation only: the data directory, the log level, the clock
                # zone, the console's own bind address and the outbound SSRF policy.
                # Audio/speaker parameters moved to the speaker panel so a warning here
                # is about the process, not DSP.
                "groups": ["system", "web", "security"],
                "tier": 1,
                "warning": {
                    "zh": "底子参数平时不要动：数据目录决定日志/记忆/密钥放哪，控制台端口"
                          "改错会打不开页面。改完要重启服务（docker compose restart）。",
                    "en": "Do not touch these unless you must: the data directory decides "
                          "where logs/memory/secrets live, and a wrong console port hides "
                          "the UI. Restart the service afterwards (`docker compose restart`).",
                },
            },
        },
        {
            "id": "logs", "title": "日志", "title_en": "Logs",
            "icon": "≡", "group": "system", "order": 60,
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
            "id": "memory", "title": "记忆", "title_en": "Memory",
            "icon": "◇", "group": "living", "order": 35,
            "enabled": True, "enabled_reason": "",
            "source": "/api/v1/memory", "refresh_s": 0,
            "blocks": [
                {"type": "memory", "title": "记住的事",
                 "empty": "还没有记住任何事。语音说「记住……」，或在这里添加。"},
            ],
        },
        {
            "id": "music", "title": "音乐", "title_en": "Music",
            "icon": "♪", "group": "living", "order": 30,
            "enabled": music_panel_enabled,
            "enabled_reason": music_reason,
            "source": "/api/v1/music", "refresh_s": 4,
            "blocks": [
                # One page, three sections (docs/console-ia-audit.md §3): playback,
                # write-only source credentials, and the catalog fields that tune the
                # source. A section is only a heading — blocks keep rendering through
                # the same components, so there is still one sticky save per page.
                {"type": "stats", "section_title": {"zh": "正在播放", "en": "Now playing"},
                 "cards": [
                    {"label": "状态", "value": "$.state",
                     "map": {"playing": "播放中", "paused": "已暂停", "idle": "空闲"},
                     "tone": {"playing": "ok", "paused": "warn"}},
                    {"label": "队列剩余", "value": "$.queue_left", "unit": "首"},
                ]},
                {"type": "now", "section_title": {"zh": "正在播放", "en": "Now playing"},
                 "title": "$.track.title", "sub": "$.track.artist",
                 "empty": "没有在播的歌"},
                {"type": "actions", "section_title": {"zh": "正在播放", "en": "Now playing"},
                 "buttons": [
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
                {"type": "list", "section_title": {"zh": "正在播放", "en": "Now playing"},
                 "title": "最近爱听", "items": "$.top",
                 "item_title": "$.artist", "item_sub": "$.plays 次",
                 "empty": "还没有播放记录", "max": 5},
                # Source + credentials are one write-only section: pick a preset, the
                # block shows only that preset's slots, and Test calls
                # POST /api/v1/music/test. `sources_href` is the registry the block
                # renders; the static fields below are the safe fallback if that fetch
                # fails. Never renders a stored value.
                {"type": "credentials",
                 "section_title": {"zh": "音源与凭据", "en": "Source & credentials"},
                 "title": {"zh": "音乐音源与凭据", "en": "Music source & credentials"},
                 "status_key": "credentials_ready",
                 "sources_href": "/api/v1/music/sources",
                 "note": {"zh": "先选音源预设，再填它要求的字段。密码/token 只写不读，"
                                "保存后 0600 存到 data_dir/lan_media.json，接口永不回显；"
                                "空值删除该字段。",
                          "en": "Pick a source preset, then fill the fields it asks for. "
                                "Password/token are write-only: stored 0600 at "
                                "data_dir/lan_media.json, never returned; empty deletes."},
                 "fields": [
                     {"key": "url", "kind": "text",
                      "label": {"zh": "服务器地址", "en": "Server URL"},
                      "hint": {"zh": "含协议和端口，指向服务根路径（Subsonic 兼容）",
                               "en": "Scheme and port, pointing at the server root "
                                     "(Subsonic-compatible)"}},
                     {"key": "user", "kind": "text",
                      "label": {"zh": "用户名", "en": "Username"},
                      "hint": {"zh": "曲库登录用户名",
                               "en": "Library login user"}},
                     {"key": "password", "kind": "password",
                      "label": {"zh": "密码", "en": "Password"},
                      "hint": {"zh": "只写不读；与 API token 二选一",
                               "en": "Write-only; use a password or an API token"}},
                     {"key": "token", "kind": "password",
                      "label": {"zh": "API token（可选）", "en": "API token (optional)"},
                      "hint": {"zh": "用 token 代替密码时填写；留空就用密码",
                               "en": "Fill when a token replaces the password; blank = password"}},
                 ],
                 "save": {"href": "/api/v1/music/credentials", "method": "PUT"},
                 "test": {"href": "/api/v1/music/test", "method": "POST"}},
                # The enable toggle and source live in the catalog, not in a music-only
                # API. Keeping that group on this page means the page that shows "off"
                # is also where music is turned on (audit §2.1: no dead end). Advanced
                # fields stay behind ConfigCenter's own "show advanced" toggle.
                {"type": "config",
                 "section_title": {"zh": "高级", "en": "Advanced"},
                 "title": {"zh": "音乐配置", "en": "Music settings"},
                 "groups": ["music"], "tier": 4},
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


# The old build capped the injected memory at 800 characters; without a budget a long
# list quietly eats the room the prompt needs for the actual turn. The cap is on the
# text handed to the model, not on what the store keeps.
MAX_MEMORY_CHARS = 800


def _memory_context(app: FastAPI) -> str:
    """Confirmed memory only - candidates wait for the console (old build's rule).

    Newest first, and the block is capped at `MAX_MEMORY_CHARS`: the model should see
    what was just confirmed rather than a truncated pile of old facts.
    """
    store = getattr(app.state, "memory", None)
    if store is None:
        return ""
    texts = store.confirmed_texts(limit=20)
    if not texts:
        return ""
    lines: list[str] = []
    used = 0
    for text in reversed(texts):
        line = f"- {text}"
        if lines and used + len(line) + 1 > MAX_MEMORY_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    lines.reverse()
    return "（你记得这些事，用户说过：\n" + "\n".join(lines) + "）"


def _plane_snapshot(app: FastAPI) -> dict:
    """What the audio plane says right now: the only source of "the room is connected".

    The control plane builds its own adapter objects for `probe()`, but the inbound
    socket lives in the audio-plane process; its metrics are the honest answer.
    """
    supervisor = getattr(app.state, "audio", None)
    status: dict = {}
    if supervisor is not None:
        with contextlib.suppress(Exception):
            status = supervisor.status()
    metrics = status.get("metrics") or {}
    connected = bool(metrics.get("speaker_connected"))
    if connected:
        app.state.speaker_last_seen = time.time()
    return {
        "running": bool(status.get("running")),
        "speaker_connected": connected,
        # Refused links since the plane started: the setup wizard's token check.
        "speaker_rejected": int(metrics.get("speaker_rejected") or 0),
        "speaker_device": str(status.get("device_id") or ""),
        "last_seen": getattr(app.state, "speaker_last_seen", 0.0) or None,
        "error": str(status.get("error") or ""),
    }


def _adapter_health(adapter: object) -> dict:
    """An adapter's link report, if it has one (xiaoai does; mock has no link)."""
    fn = getattr(adapter, "health", None)
    if not callable(fn):
        return {}
    try:
        return dict(fn() or {})
    except Exception:  # noqa: BLE001 - status display must not 500 the page
        return {}


def _device_link(info: object, adapter: object, plane: dict) -> dict:
    """`status/connected/last_seen/latency_ms` for one device, never an optimistic guess.

    Three sources, in order: the adapter's own health() (works when the adapter runs in
    this process), the audio plane's metrics (the real inbound link), and finally "the
    control plane cannot see a link because no plane is running" -> unknown.
    """
    health = _adapter_health(adapter)
    device_id = str(getattr(info, "id", ""))
    if health.get("connected") is True:
        connected: bool | None = True
    elif plane["speaker_connected"] and plane["speaker_device"] in ("", device_id):
        connected = True
    elif not plane["running"]:
        connected = None
    else:
        connected = False
    latency = health.get("latency_ms")
    last_seen = health.get("connected_at") or plane["last_seen"]
    return {
        "status": "ready" if connected else ("unknown" if connected is None else "disconnected"),
        "connected": connected,
        "last_seen": float(last_seen) if last_seen else None,
        "latency_ms": round(float(latency), 1) if latency else None,
    }


def _device_public_params(device: object | None, resolved: object | None) -> dict:
    """Stored params minus manifest-declared secret values (write-only contract).

    Secrets are normally written to ``secrets.yaml`` and never land in ``params``; the
    filter also protects a config hand-edited before that rule existed.
    """
    params = dict(getattr(device, "params", None) or {})
    if resolved is not None:
        hidden = {spec.key for spec in resolved.device.params
                  if spec.type is ParamType.SECRET}
        for key in hidden:
            params.pop(key, None)
    return params


def _device_plug_id(device: object | None, resolved: object | None) -> str | None:
    """The resolved plug id, falling back to the stored value so the UI can show it."""
    if resolved is not None:
        return resolved.plug_id
    params = getattr(device, "params", None)
    stored = str(params.get("plug_id") or "").strip() if isinstance(params, dict) else ""
    return stored or None


def _token_env(device: object) -> str:
    """The secret name a device's token lives under (never the value)."""
    name = str(getattr(device, "token_env", "") or "").strip()
    return name or "RUI_DEVICE_TOKEN"


def _token_configured(device: object) -> bool:
    """Whether a client can authenticate: a stored token, or the deliberate no-token mode."""
    return bool(secrets.resolve(_token_env(device))) or bool(
        getattr(device, "allow_no_token", False))


def _active_device_id(app: FastAPI, config: Config) -> str:
    """The device the one supported audio plane is actually driving.

    The supervisor picks the first drivable enabled adapter; asking it keeps the
    warning truthful instead of assuming config order. Without a supervisor (tests,
    setup-only boot) the first enabled device is the honest fallback.
    """
    supervisor = getattr(app.state, "audio", None)
    pick = getattr(supervisor, "_pick_device", None)
    if callable(pick):
        try:
            picked = pick()
        except Exception:  # noqa: BLE001 - a warning must never break the list
            picked = None
        if picked:
            return str(picked)
    for device in config.devices:
        if device.enabled:
            return device.id
    return ""


def _device_warnings(config: Config, active_id: str) -> list[dict]:
    """Explicit "more than one inbound speaker, only one can run" warning (P3.4).

    0.3 has no multi-device arbitration: one audio plane drives one adapter. Rather
    than silently pretending every enabled device is live, the list/status endpoints
    say which one is active and which ones are only configured.
    """
    catalog = PlugCatalog(config.system.data_dir)
    inbound = [device for device in config.devices
               if device.enabled and is_inbound(device, catalog)]
    if len(inbound) <= 1:
        return []
    ignored = [device.id for device in inbound if device.id != active_id]
    return [{
        "code": "multiple_enabled_inbound",
        "message": (
            "0.3 supports one active inbound audio plane; only "
            f"{active_id or 'none'} is driven. Disable the other device or wait for "
            "multi-device arbitration."
        ),
        "details": {
            "active_device": active_id,
            "enabled_inbound": [device.id for device in inbound],
            "ignored_devices": ignored,
            "max_active_inbound": 1,
        },
    }]


async def _device_status(app: FastAPI, device: DeviceConfig, config: Config,
                         warnings: list[dict] | None = None) -> dict:
    """One device's management status: evidence only, and never a secret value.

    ``ready`` is not "configured": it means enabled, authenticated, and the plane's
    live link says connected. Every reason it is false is listed in ``errors`` as a
    stable code so the console can translate it.
    """
    catalog = PlugCatalog(config.system.data_dir)
    resolved = catalog.resolve(
        plug_id=_device_plug_id(device, None), adapter=device.adapter)
    plane = _plane_snapshot(app)
    token_env = _token_env(device)
    token_ok = _token_configured(device)
    listen_port = effective_listen_port(device)
    capabilities = {str(capability) for capability in (resolved.capabilities
                                                       if resolved is not None else [])}
    errors: list[str] = []
    link: dict = {"status": "unavailable", "connected": False,
                  "last_seen": None, "latency_ms": None}
    adapters_now = getattr(app.state, "adapters", None)
    adapter = adapters_now.adapters.get(device.id) if adapters_now is not None else None
    if not device.enabled:
        link = {"status": "disabled", "connected": False,
                "last_seen": None, "latency_ms": None}
    elif adapter is not None:
        probe = await safe_probe(adapter, device_id=device.id, adapter_name=device.adapter)
        info = probe.info
        link = _device_link(info, adapter, plane)
        capabilities.update(str(capability) for capability in info.capabilities)
        if probe.error:
            # P1-4: one raising probe must not 500 the whole status page. The row says
            # `probe_failed` and stays actionable (disable/remove) instead of vanishing.
            link["status"] = "error"
            link["error"] = probe.error
            errors.append(probe.error)
    else:
        errors.append("adapter_unavailable")
    if not device.enabled:
        errors.append("device_disabled")
    if not token_ok:
        errors.append("token_missing")
    if resolved is None:
        errors.append("plug_unresolved")
    if device.enabled and not plane["running"]:
        errors.append("audio_plane_stopped")
    if plane["error"]:
        errors.append("audio_plane_error")
    if (device.enabled and token_ok and plane["running"]
            and link["connected"] is False):
        errors.append("not_connected")
    setup = setup_store.read(config.system.data_dir)
    stored_test = setup.get("test", {}).get("device", {}).get("test", {})
    if not isinstance(stored_test, dict):
        stored_test = {}
    ready = bool(device.enabled and token_ok and link["status"] == "ready")
    return {
        "id": device.id,
        "enabled": bool(device.enabled),
        "ready": ready,
        "token_configured": token_ok,
        "token_env": token_env,
        "listen_port": listen_port,
        "plane": {
            "speaker_connected": bool(plane["speaker_connected"]),
            "last_seen": plane["last_seen"],
            "audio_plane": bool(plane["running"]),
        },
        "capabilities": sorted(capabilities),
        "setup_state": {
            "configured": bool(device.host and device.wake_words and resolved is not None),
            "wake_words": list(device.wake_words),
            "last_test_ok": bool(stored_test.get("ok")),
            "last_test_at": str(stored_test.get("at") or ""),
        },
        "errors": errors,
        "warnings": list(warnings or []),
    }


_SECRET_QUERY_RE = re.compile(
    r"([?&](?:p|t|password|token|api_key|key|u)=)[^&\s]+", re.IGNORECASE)


def _redact_error(exc: BaseException) -> str:
    """An exception sentence with credentials in query strings masked.

    Navidrome puts the SUBsonic password in the URL query (`&p=`), and httpx errors
    quote the URL; echoing that to the browser would leak the secret through the very
    endpoint that tests it.
    """
    text = f"{type(exc).__name__}: {exc}"
    return _SECRET_QUERY_RE.sub(r"\1***", text)



def _write_keywords_preserving(cfg_now: Config, *, threshold: float | None = None,
                               boost: float | None = None) -> bool:
    """Rewrite keywords.txt from the wake words, keeping the configured gate.

    A device save used to reset a hand-tuned 0.20 to the default 0.15 silently (audit
    #8). The gate is read from the existing file first (or taken from an explicit
    override) and written back, so editing a speaker never changes wake sensitivity.
    """
    path = keyword_file(cfg_now)
    words = [word for device in cfg_now.devices for word in device.wake_words]
    if path is None or not words:
        return False
    settings = keyword_settings(cfg_now)
    try:
        write_keywords_file(
            path, words,
            threshold=float(threshold if threshold is not None else settings["threshold"]),
            boost=float(boost if boost is not None else settings["boost"]),
        )
    except (OSError, ValueError) as exc:  # noqa: BLE001 - the state reports what is missing
        log.warning("keywords: rewrite failed: %s", exc)
        return False
    return True


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
    ring.append({**turn, "ts": time.strftime("%H:%M:%S"), "ts_epoch": time.time()})


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


# Audition WAVs are written for the audio plane to read, so they cannot be deleted the
# instant PlayFile is queued: the plane holds one pending file and may start it minutes
# later, behind a long reply. The cache is bounded instead (TTL + hard count, swept on
# every audition) and always lives under <data_dir>/cache/auditions.
AUDITION_PREFIX = "audition-"
AUDITION_TTL_S = 1800.0      # 30 min: long enough for any queued reply
AUDITION_MAX_FILES = 20      # one more may exist between sweeps


def audition_cache_dir(data_dir: str | Path) -> Path:
    """Resolve <data_dir>/cache/auditions and prove it stays inside data_dir.

    The audition text/voice come from the browser, but the path never does. Resolving
    both sides also catches a symlinked `cache/` pointing outside the data volume.
    """
    raw = str(data_dir or "").strip()
    if not raw:
        raise ValueError("system.data_dir is not configured")
    root = Path(raw).expanduser().resolve()
    target = (root / "cache" / "auditions").resolve()
    if target != root and root not in target.parents:
        raise ValueError("audition cache would escape system.data_dir")
    return target


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def sweep_audition_files(directory: Path, *, ttl_s: float = AUDITION_TTL_S,
                         keep: int = AUDITION_MAX_FILES) -> int:
    """Delete expired/extra audition files; returns how many were removed.

    Concurrent auditions can race the sweep, so every filesystem step is best-effort:
    a file that disappears under us must not turn a preview into a 500.
    """
    removed = 0
    now = time.time()
    survivors: list[Path] = []
    for path in directory.glob(f"{AUDITION_PREFIX}*.wav"):
        mtime = _mtime(path)
        if ttl_s > 0 and now - mtime > ttl_s:
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
        else:
            survivors.append(path)
    # A crash between write and replace leaves a .tmp; it is never playable.
    for path in directory.glob(f"{AUDITION_PREFIX}*.wav.tmp"):
        if now - _mtime(path) > 60.0:
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
    survivors.sort(key=_mtime)
    if keep > 0 and len(survivors) > keep:
        for path in survivors[: len(survivors) - keep]:
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
    return removed


# The inbound WS port an LX06 client dials when devices[].listen_port is unset. Kept
# vendor-neutral here with a test pinning it to device/xiaoai/adapter.py's constant.
DEVICE_DEFAULT_LISTEN_PORT = DEFAULT_LISTEN_PORT
# Token rotation changes the credential a speaker dials in with, so a stuck button (or
# a script) must not be able to lock the room out in a loop. One rotation per device
# per window is enough for the console; the value is intentionally short.
TOKEN_ROTATE_INTERVAL_S = 10.0


def _host_without_port(value: str) -> str:
    """`nas.local:8091` -> nas.local; `[::1]:8091` -> [::1] (brackets kept)."""
    host = (value or "").strip()
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end > 0 else host
    return host.split(":")[0]


def request_console_host(request: Request) -> str:
    """The host a speaker on the LAN should dial back, preserving reverse-proxy hosts.

    X-Forwarded-Host wins (Tailscale/nginx/Cloudflare setups advertise the public name
    there), then the request's own Host header, then the URL host. Only the host is
    taken: the console port is replaced with the device's inbound port.
    """
    forwarded = (request.headers.get("x-forwarded-host") or "").split(",")[0]
    candidate = _host_without_port(forwarded) or _host_without_port(
        request.headers.get("host") or "")
    if candidate:
        return candidate
    hostname = request.url.hostname or "127.0.0.1"
    return f"[{hostname}]" if ":" in hostname else hostname


def is_loopback_host(host: str) -> bool:
    """True for a host a speaker on the LAN cannot dial back to.

    ``localhost`` and any ``*.localhost`` name count, as do IPv4/IPv6 loopback and
    unspecified addresses (``127.0.0.1``, ``0.0.0.0``, ``::1``, ``::``).
    """
    text = (host or "").strip().lower()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text:
        return False
    if text == "localhost" or text.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def server_lan_ipv4() -> str:
    """The first private, non-loopback IPv4 of this host; "" when none is found.

    Read-only and best-effort: the hostname's addresses first, then the route address
    the kernel would use (a UDP connect sends no packet). A container's bridge address
    can be returned by getaddrinfo, so the route address is preferred when it is private.
    """
    candidates: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(str(info[4][0]))
    except OSError:
        pass
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        candidates.insert(0, str(sock.getsockname()[0]))
    except OSError:
        pass
    finally:
        sock.close()
    for text in candidates:
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            continue
        if (address.version == 4 and address.is_private
                and not address.is_loopback and not address.is_link_local
                and not address.is_unspecified):
            return str(address)
    return ""


def resolve_connect_host(request: Request) -> tuple[str, str, bool]:
    """(host a speaker should dial, "request"|"server", loopback was detected).

    A reverse-proxy/request host is preserved unless it is a loopback name: those are
    reachable from the console's browser only, never from the speaker, so the server's
    LAN IPv4 is substituted when one exists.
    """
    requested = request_console_host(request)
    if not is_loopback_host(requested):
        return requested, "request", False
    lan = server_lan_ipv4()
    if lan:
        return lan, "server", True
    return requested, "request", True


def request_ws_scheme(request: Request) -> str:
    """`ws`, or `wss` when a TLS-terminating proxy is in front of the console."""
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if not proto:
        proto = request.url.scheme
    return "wss" if proto in ("https", "wss") else "ws"


def create_app(
    cfg: Config,
    config_path: Path | None = None,
    start_audio: bool = False,
    recovery: RecoveryInfo | None = None,
) -> FastAPI:
    secrets.configure(cfg.system.data_dir)
    # A config from the inline-credentials era still carries music.url/user/password.
    # Move them into the write-only 0600 JSON once; the schema excludes them from every
    # dump, so the next save writes a config without plaintext.
    try:
        if migrate_legacy_music_credentials(cfg):
            log.info("music: legacy credentials moved to %s",
                     music_credentials_path(cfg))
            # Rewrite the config once without the inline plaintext (the fields are
            # excluded from every dump). The usual atomic writer keeps a backup.
            if config_path is not None:
                save_config(Path(config_path), cfg)
    except OSError as exc:
        log.warning("music: could not migrate legacy credentials: %s", exc)
    # The injected clock line ("现在几点") follows `system.timezone`: a container inherits
    # no TZ from the NAS, so without this the assistant answers in UTC.
    prompts.set_timezone(cfg.system.timezone)
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
        music_service = _new_music_service(cfg, _never_sender)
        if music_service is not None:
            for spec, handler in build_music_tools(cfg, music_service):
                registry.register(spec)
                local_tools.append((spec, handler))
            log.info("music: source=%s tools=%d", cfg.music.source, len(local_tools))
    # Generic MCP servers (`tools.mcp_servers`): one task per server, tools registered
    # as `mcp.<server>_<tool>`. The manager only owns connections/status; registration
    # stays here so the runner's tool list remains the single wiring point. HA keeps its
    # own bridge and naming (`local.ha_*`) - this is additive, not a replacement.
    mcp_manager = MCPManager(cfg.tools.mcp_servers,
                            allow_stdio=cfg.tools.mcp_allow_stdio,
                            policy=_outbound_policy(cfg))

    def _sync_generic_mcp() -> None:
        """Register/replace the generic MCP tools from the manager's live servers.

        Called on startup, after a config save, and whenever a server connects late
        (the manager's `on_change` hook). Idempotent: it always unregisters the old
        `mcp.*` set first, so late connects and hot re-applies cannot duplicate tools.
        """
        registry.unregister([name for name in registry.names()
                             if name.startswith("mcp.")])
        local_tools[:] = [(spec, handler) for spec, handler in local_tools
                          if not spec.name.startswith("mcp.")]
        for spec, handler in mcp_manager.tool_pairs():
            try:
                registry.register(spec, handler)
            except Exception as exc:  # noqa: BLE001 - one bad tool must not drop the rest
                log.warning("mcp: cannot register %s: %s", spec.name, exc)
                continue
            local_tools.append((spec, handler))
        runner = getattr(app.state, "runner", None)
        if runner is not None:
            runner.set_tools(list(local_tools))

    mcp_manager.on_change = _sync_generic_mcp
    # Downstream adapters (issue #7 Q1): env/entry points only, imported before the
    # first build_adapters so a console-only configuration sees the new name. Fail-soft:
    # a bad plugin is a warning in app.state, never an exception out of startup.
    adapter_plugins = load_adapter_plugins()
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
        # Text channels keep their own history; a Telegram chat must not overwrite the
        # living-room context. The runner itself is single-session, so the endpoint swaps
        # this list in and restores it under a lock. The dict/lock were created in
        # create_app for clients that never enter the lifespan.
        app.state.last_turn = None
        vendor = VendorWindow(cfg.session.vendor_quiet_s)
        app.state.vendor = vendor
        # HA over MCP (V2's first application): connect, register the whitelisted
        # tools, inject the entity list per turn. A dead HA degrades its tools -
        # the rest of the assistant never notices (old build's rule).
        ha: HaBridge | None = None
        if cfg.tools.ha.base_url:
            ha = HaBridge(cfg.tools.ha, Path(cfg.system.data_dir),
                          policy=_outbound_policy(cfg))
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
        # Generic MCP servers start concurrently and are allowed to fail individually:
        # a dead server degrades only its own tools, exactly like a dead HA. Tools are
        # registered through the same path a late connect uses (`on_change`).
        await mcp_manager.apply(cfg.tools.mcp_servers)
        _sync_generic_mcp()
        generic = mcp_manager.status()
        log.info("mcp generic: %d/%d server(s) connected, %d tool(s)",
                 generic["connected"], generic["enabled"], generic["tools"])
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
                        trace.note_wake(client.last_wake)
                        # Consume it: a follow-up utterance has no new wake event, and
                        # reusing this timestamp made first_response_ms 20-35 s.
                        client.last_wake = None
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
                    claimed_device = rules.claims_device_success(clean, result.reply)
                    claimed_music = rules.claims_music_success(clean, result.reply)
                    if not result.tools and (claimed_device or claimed_music):
                        # The model said it did something without calling a tool:
                        # retry once with an explicit instruction, then be honest.
                        log.info("clarify: claimed success with no tool call: %r", result.reply)
                        hint = ("上一轮你没有成功调用任何工具，不要用文字回答。"
                                "如果是点歌就调用 music_play；做不到就如实说没做成，"
                                "不要谎称已经放/已经执行。")
                        try:
                            result = await runner.run(pcm, _speaking(app, client), trace,
                                                      transcript=clean, hint=hint)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("retry after a false claim failed: %s", exc)
                        if not result.tools:
                            key = "music.ask_play" if claimed_music else "device.not_done"
                            line = phrases.book.render(key)
                            spoken = await runner.say(line, _speaking(app, client), trace)
                            result.reply = line
                            result.audio_bytes = spoken
                    if asr_report is not None:
                        result.stages.setdefault("asr", asr_report)
                    _record_turn(app, result.to_dict())
                    trace.note_playback_metrics(getattr(client, "metrics", None))
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
                    # Unified idle rule (9哥, 16:1x): ANY interaction in a session ends
                    # with 「我先下啦」 after session.timeout_s of silence - a wake that
                    # heard nothing, a finished answer, a finished song. The clock is
                    # started here, kept alive while a turn/playback/gate is active, and
                    # closed by the single goodbye in idle_watch.
                    runner.idle.start()
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

                def on_wake_idle(msg: object) -> None:
                    # The listening gate closed with nothing said. Do NOT close the
                    # session here: the unified rule says a silent wake still gets the
                    # 「我先下啦」 goodbye after session.timeout_s (9哥, 16:1x). The idle
                    # clock was started in on_wake and keeps running; only the light is
                    # turned off because nobody is being listened to any more.
                    _light(app, "off")
                    log.info("wake without speech (%.1f s); idle goodbye in %.0f s",
                             float(getattr(msg, "waited_ms", 0.0)) / 1000.0,
                             max(0.0, runner.idle.remaining()))

                client.on_wake = on_wake
                client.on_wake_idle = on_wake_idle
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
                    # Someone is speaking right now: a long sentence must not be cut in
                    # half. Merely waiting (gate armed for a follow-up) is NOT activity -
                    # counting it made the 15 s goodbye arrive after 30 s.
                    if metrics is not None and metrics.gate_speaking:
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
        # Managed channel gateway: auto-start when a channel is enabled, then watchdog.
        # The console's Start/Stop buttons are the only other owners of this switch.
        await app.state.channel_supervisor.start()
        app.state.channel_watch = asyncio.create_task(
            app.state.channel_supervisor.watch(), name="channel-watch")
        if recovery is None and config_path is not None:
            remember_last_good(config_path)
            with contextlib.suppress(Exception):
                clear_marker(cfg)

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
        # MCP sessions first: an HTTP session gets its own connection closed, a stdio
        # server is a child process that must not outlive the control plane.
        await mcp_manager.stop()
        login_task = getattr(app.state, "weixin_login_task", None)
        if login_task is not None and not login_task.done():
            login_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await login_task
        channel_watch = getattr(app.state, "channel_watch", None)
        if channel_watch is not None and not channel_watch.done():
            channel_watch.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await channel_watch
        channel_supervisor = getattr(app.state, "channel_supervisor", None)
        if channel_supervisor is not None:
            await channel_supervisor.stop()
        supervisor = getattr(app.state, "audio", None)
        if supervisor is not None:
            await supervisor.stop()

    app = FastAPI(title="rui", version=__version__, lifespan=lifespan)
    install_security_headers(app)
    # The live config and the file it came from: voice commands that edit a
    # setting (「关闭上下文」) persist through the same file the console writes.
    app.state.cfg = cfg
    # Channel endpoints must work in a TestClient without entering the lifespan (and in
    # production the lifespan keeps them); create the containers eagerly.
    app.state.chat_histories: dict[str, list[dict]] = {}
    app.state.chat_lock = asyncio.Lock()
    app.state.channel_supervisor = ChannelSupervisor(cfg, Path(cfg.system.data_dir))
    # The console's WeChat QR flow is a control-plane task, not a gateway task: the
    # account is saved here so the gateway can use it after Start.
    app.state.weixin_login: dict[str, object] = {
        "status": "idle", "qr_url": "", "qr_svg": "", "error": "", "account_id": "",
    }
    app.state.weixin_login_task: asyncio.Task | None = None
    app.state.config_path = config_path
    app.state.config_recovery = recovery
    # The wizard can add a device at runtime; endpoints read this attribute, not a
    # startup closure, so a fresh probe does not need a control-plane restart.
    app.state.adapters = adapters
    # Names/count only: warnings can contain module paths or exception text, and
    # /health is unauthenticated.
    app.state.adapter_plugins = adapter_plugins
    app.state.memory = memory
    app.state.tools = registry  # the console and tests read the same registry turns use
    app.state.mcp_manager = mcp_manager  # generic MCP sessions/status (console reads it)
    app.state.plane_error = ""  # last device-write audio-plane restart outcome
    # One lock per wizard step: two tabs must not race the same file/config write.
    app.state.setup_locks = {step: asyncio.Lock() for step in setup_store.STEPS}
    app.state.trace_log = traces  # scripts and tests read the same log the API serves
    if music_service is not None:
        music_service.sender = _music_sender(app)
        app.state.music = music_service
        app.state.music_runtime = _music_runtime_key(cfg)
    else:
        app.state.music = None
        app.state.music_runtime = None
    # The console registry (docs/console.md): panels are declared where their feature
    # is assembled, the frontend renders them and never asks "does this exist".
    app.state.panels = _build_panels(cfg.music.enabled, cfg.music.source)
    app.state.panels_revision = f"{__version__}-{int(time.time())}"

    _auth_failures: dict[str, list[float]] = {}

    def auth(request: Request,
             x_auth_token: str | None = Header(default=None)) -> None:
        """Constant-time token check with a small per-IP failure budget.

        Security audit 2026-10-23: the old plaintext `!=` was a LAN-open console;
        with a token set the console is the only control surface, so a brute-force
        loop needs a cost too.
        """
        token = getattr(app.state, "cfg", cfg).web.auth_token
        if not token:
            return
        if x_auth_token and hmac.compare_digest(str(x_auth_token), str(token)):
            return
        ip = getattr(getattr(request, "client", None), "host", "?") or "?"
        now = time.time()
        recent = [seen for seen in _auth_failures.get(ip, []) if now - seen < 60.0]
        recent.append(now)
        _auth_failures[ip] = recent[-20:]
        log.warning("console auth failed from %s", ip)
        if len(recent) >= 10:
            raise HTTPException(status_code=429, detail="too many bad tokens; wait a minute")
        raise HTTPException(status_code=401, detail="bad or missing X-Auth-Token")

    @app.get("/health")
    async def health() -> dict:
        plane = _plane_snapshot(app)
        cfg_now = getattr(app.state, "cfg", cfg)
        return {
            "status": "ok",
            "version": __version__,
            "config_version": cfg.version,
            "config_version_supported": CONFIG_VERSION,
            "uptime_s": round(time.time() - START, 1),
            "devices": len(getattr(app.state, "adapters", adapters).adapters),
            # The probe's two counts must come from the same place the console reads:
            # a disabled device is configured but is not supposed to open a plane.
            "enabled_devices": sum(1 for device in cfg_now.devices if device.enabled),
            "device_errors": len(getattr(app.state, "adapters", adapters).errors),
            # Downstream adapter hook (issue #7 Q1): names and warning count only.
            "adapter_plugins": {
                "registered": sorted(getattr(app.state, "adapter_plugins", adapter_plugins).registered),
                "warnings": len(getattr(app.state, "adapter_plugins", adapter_plugins).warnings),
            },
            "audio_plane": plane["running"],
            # "Up" is not "the room can hear": this is the plane's own link report.
            "speaker_connected": plane["speaker_connected"],
            "speaker_device": plane["speaker_device"],
            "speaker_last_seen": plane["last_seen"],
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

    @app.get("/api/v1/config/recovery", dependencies=[Depends(auth)])
    async def config_recovery() -> dict:
        info = getattr(app.state, "config_recovery", None)
        from ...config.recovery import last_good_path
        path = getattr(app.state, "config_path", None)
        last_good = last_good_path(Path(path)) if path is not None else None
        return {
            "active": info is not None,
            "mode": getattr(info, "mode", ""),
            "bad_path": getattr(info, "bad_path", ""),
            "last_good_path": getattr(info, "last_good_path", ""),
            "last_good_exists": bool(last_good and last_good.is_file()),
            "at": getattr(info, "at", 0.0),
            "note": getattr(info, "note", ""),
            "config_path": str(path) if path is not None else "",
        }

    @app.post("/api/v1/config/recovery/restore", dependencies=[Depends(auth)])
    async def config_recovery_restore(patch: Annotated[dict, Body()]) -> dict:
        path = getattr(app.state, "config_path", None)
        if path is None:
            raise HTTPException(status_code=409, detail="no config path is known")
        mode = str(patch.get("mode") or "")
        try:
            message = restore_config(Path(path), getattr(app.state, "cfg", cfg), mode)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        app.state.config_recovery = None
        return {"ok": True, "message": message, "restart_required": True}

    @app.get("/api/v1/config/recovery/bad", dependencies=[Depends(auth)])
    async def config_recovery_bad() -> FileResponse:
        info = getattr(app.state, "config_recovery", None)
        bad = getattr(info, "bad_path", "") if info is not None else ""
        if not bad:
            raise HTTPException(status_code=404, detail="no failed config recorded")
        path = Path(bad)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="failed config no longer exists")
        return FileResponse(path, filename=path.name, media_type="text/yaml")

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

    # ---------------------------------------------------------------- config center
    @app.get("/api/v1/config/catalog", dependencies=[Depends(auth)])
    async def get_config_catalog() -> dict:
        """The configuration directory the console renders (docs/config-catalog-contract.md).

        Every editable field with its tier, a hint that says what changing it achieves,
        and save-vs-restart. Secret *values* never appear: only `{name: {has_value}}`.
        """
        return config_catalog(cfg)

    def _read_config_or_409() -> Config:
        """Re-read the file before writing: a page that never saw `providers` must not
        write a stale copy back over the real one."""
        if config_path is None or not Path(config_path).exists():
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        try:
            loaded = load_config(config_path)
        except ConfigError as exc:
            raise HTTPException(
                status_code=409, detail=f"config on disk is not readable: {exc}"
            ) from exc
        # A re-read must not resurrect inline music credentials and then write them
        # back: migrate once here as well, so the returned model is plaintext-free.
        try:
            migrate_legacy_music_credentials(loaded)
        except OSError as exc:
            log.warning("music: could not migrate legacy credentials: %s", exc)
        return loaded

    def _refresh_panels() -> None:
        """The music panel follows `music.enabled`; everything else is static."""
        app.state.panels = _build_panels(cfg.music.enabled, cfg.music.source)

    async def _stop_music_service() -> None:
        """Stop and unregister the running music layer, if any."""
        service = getattr(app.state, "music", None)
        if service is None:
            return
        with contextlib.suppress(Exception):
            await service.stop()
        names = [name for name in registry.names() if name.startswith("local.music_")]
        registry.unregister(names)
        local_tools[:] = [(spec, handler) for spec, handler in local_tools
                          if not spec.name.startswith("local.music_")]
        app.state.music = None
        app.state.music_runtime = None
        runner = getattr(app.state, "runner", None)
        if runner is not None:
            runner.set_tools(list(local_tools))
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is not None:
            # The old service's callback must not advance a queue that no longer exists.
            client.on_playback_finished = None

    def _start_music_service(updated: Config) -> bool:
        """Build, bind and register the service for `updated`; False if unsupported."""
        service = _new_music_service(updated, _music_sender(app))
        if service is None:
            return False
        app.state.music = service
        app.state.music_runtime = _music_runtime_key(updated)
        # Runtime enable must bind the natural-end callback that the startup path
        # binds at :1359. Without it the radio queue never advances after the first
        # song (audit round 2 P1-5).
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is not None:
            client.on_playback_finished = service.on_finished
        for spec, handler in build_music_tools(updated, service):
            registry.register(spec, handler)
            local_tools.append((spec, handler))
        runner = getattr(app.state, "runner", None)
        if runner is not None:
            runner.set_tools(list(local_tools))
        log.info("music: enabled at runtime (source=%s)", updated.music.source)
        return True

    async def _apply_music(updated: Config) -> bool:
        """Assemble, rebuild or tear down the music layer at runtime.

        Called for `music.enabled` and for the source/path fields that change what the
        service is assembled from. Returns False when the source has no runtime builder,
        so the endpoint can report "restart required" instead of claiming a live change
        that did not happen.
        """
        service = getattr(app.state, "music", None)
        if not updated.music.enabled:
            if service is not None:
                await _stop_music_service()
                log.info("music: disabled at runtime")
            return True
        runtime = getattr(app.state, "music_runtime", None)
        desired = _music_runtime_key(updated)
        if service is not None and runtime == desired:
            return True
        if service is not None:
            await _stop_music_service()
        return _start_music_service(updated)

    async def _apply_live(updated: Config, changed: list[str]) -> tuple[list[str], list[str]]:
        """Apply what can be applied without a restart; report the rest.

        Each effect in the directory is a promise: `save` means "after this call the
        running process behaves differently". Fields marked `restart` are written to
        disk, and the response says so - claiming otherwise is the "假旋钮" failure this
        project already hit once (`audio.voice.kws_threshold`).
        """
        specs = {field["path"]: field for field in field_specs(updated)}
        applied: list[str] = []
        restart_fields: list[str] = []
        for path in changed:
            spec = specs.get(path)
            (restart_fields if spec is not None and spec["effect"] == "restart"
             else applied).append(path)

        runner = getattr(app.state, "runner", None)
        if runner is not None and any(
                path.startswith(("assistant.", "session.")) for path in applied):
            runner.assistant = updated.assistant
            runner.history_turns = updated.session.history_rounds
            runner.apply_session(updated.session)
        if "system.timezone" in applied:
            prompts.set_timezone(updated.system.timezone)
        if any(path.startswith("ack.") for path in changed):
            # A new acknowledgement text (and its cap) must exist as audio before the
            # next wake; the plane re-reads ack.wav by mtime/size, so no restart.
            previous = getattr(app.state, "ack_task", None)
            if previous is not None and not previous.done():
                previous.cancel()
            app.state.ack_task = asyncio.create_task(render_ack_wav(updated), name="ack-render")
        if any(path.startswith("tools.ha.") for path in applied):
            await _apply_ha(updated)
        if "tools.mcp_allow_stdio" in changed:
            # Declared `restart`, but turning the gate OFF must take effect now: a running
            # stdio child must not keep executing until the next restart. Turning it on
            # re-applies immediately too, so a saved local server can start.
            await _apply_generic_mcp(updated)
        if MUSIC_REBUILD_FIELDS & set(changed):
            # Source/path changes rebuild the service with the same single assembly
            # path as an enable toggle. If the source has no adapter, every claimed
            # music change moves to restart_fields (and the panel says why).
            if not await _apply_music(updated):
                for path in [p for p in applied if p.startswith("music.")]:
                    applied.remove(path)
                    restart_fields.append(path)
        return sorted(applied), sorted(restart_fields)

    @app.put("/api/v1/config/values", dependencies=[Depends(auth)])
    async def put_config_values(payload: Annotated[dict, Body()]) -> dict:
        """Write dotted-path values: the config-center's only write path.

        Validated against the catalog (unknown path / type / range -> 422 per field),
        then against the whole `Config`, then written with the same atomic backup the
        other write paths use. `applied` lists what took effect now; `restart_fields`
        what needs `docker compose restart` (audio fields can use the plane restart).
        """
        values = payload.get("values")
        if not isinstance(values, dict) or not values:
            raise HTTPException(
                status_code=422,
                detail=[{"loc": ["values"], "msg": "values must be a non-empty object"}],
            )
        current = _read_config_or_409()
        specs = {field["path"]: field for field in field_specs(current)}
        before_keywords = keyword_settings(current)
        errors: list[dict] = []
        checked: dict[str, Any] = {}
        for raw_path, value in values.items():
            path = str(raw_path)
            spec = specs.get(path)
            if spec is None:
                errors.append({"loc": [path],
                               "msg": "unknown path (not in /api/v1/config/catalog)"})
                continue
            if not spec["writable"]:
                errors.append({"loc": [path],
                               "msg": "read-only field; edit it on its own page"})
                continue
            try:
                checked[path] = coerce(spec, value)
            except ValueError as exc:
                errors.append({"loc": [path], "msg": str(exc)})
        if errors:
            raise HTTPException(status_code=422, detail=errors)

        before = current.model_dump(mode="json")
        merged = current.model_dump(mode="json")
        warnings: list[str] = []
        # Virtual fields do not live in Config: the wake gate rewrites keywords.txt.
        keyword_values = {
            path.rsplit(".", 1)[-1]: float(value)
            for path, value in checked.items() if specs[path].get("virtual")
        }
        keyword_changed = {
            key: value for key, value in keyword_values.items()
            if float(before_keywords.get(key) or 0) != float(value)
        }
        for path, value in checked.items():
            if not specs[path].get("virtual"):
                set_path(merged, path, value)
        if keyword_values:
            words = [word for device in merged.get("devices") or []
                     for word in (device.get("wake_words") or [])]
            if not words:
                raise HTTPException(status_code=422, detail=[
                    {"loc": [f"audio.voice.keywords.{key}"],
                     "msg": "no wake words configured; add a speaker first"}
                    for key in keyword_values
                ])
        # 9哥: 音源定了以后路径要自动更新. The source decides where its credentials and
        # PCM cache live; keeping a path from the previous source is how a speaker ends
        # up "enabled but silent".
        music = merged.setdefault("music", {})
        if "music.source" in checked:
            source = str(music.get("source") or "")
            if _autofill_music_paths(merged, source):
                warnings.append(
                    f"music.source={source}：secrets_path / cache_dir 已按音源自动更新")
            else:
                warnings.append(
                    f"未知音源 {source!r}：secrets_path / cache_dir 保持手动值，请自行确认")
        elif music.get("enabled") and not music.get("secrets_path"):
            source = str(music.get("source") or "")
            if _autofill_music_paths(merged, source):
                warnings.append(f"启用音乐：已自动补上 {source} 的 secrets_path / cache_dir")
        if any(path.startswith("devices.") for path in checked):
            # A device edit through the catalog must leave a keywords file behind even
            # when the config came from schema defaults (no path). Same rule as the wizard.
            voice = merged.setdefault("audio", {}).setdefault("voice", {})
            if not voice.get("kws_keywords_file") and config_path is not None:
                voice["kws_keywords_file"] = str(
                    Path(config_path).resolve().parent / "keywords.txt")

        try:
            updated = Config.model_validate(merged)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail=_flat_errors(exc, list(checked))) from exc
        save_config(Path(config_path), updated)  # type: ignore[arg-type]
        _apply_config(updated)
        if any(path.startswith("devices.") for path in checked):
            # Adapters, wake words and keywords.txt are refreshed now; the plane itself
            # still has to restart, which `restart_fields` reports (the response offers
            # the audio-plane restart button for exactly this).
            _sync_devices(updated)
        if keyword_changed:
            if not _write_keywords_preserving(
                    updated,
                    threshold=keyword_changed.get("threshold"),
                    boost=keyword_changed.get("boost")):
                warnings.append("门限已记录，但 keywords.txt 重写失败：检查目录权限与唤醒词路径")
        after = updated.model_dump(mode="json")
        changed: list[str] = []
        config_paths = {path for path, spec in specs.items() if not spec.get("virtual")}
        for path in sorted((set(checked) & config_paths)
                           | {"music.secrets_path", "music.cache_dir"}):
            try:
                if get_path(before, path) != get_path(after, path):
                    changed.append(path)
            except (KeyError, IndexError):
                changed.append(path)
        changed.extend(f"audio.voice.keywords.{key}" for key in sorted(keyword_changed))
        applied, restart_fields = await _apply_live(updated, changed)
        _refresh_panels()
        log.info("config values written from the console: applied=%s restart=%s",
                 applied, restart_fields)
        return {
            "config": redacted(updated),
            "applied": applied,
            "restart_required": bool(restart_fields),
            "restart_fields": restart_fields,
            "warnings": warnings,
        }

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

        `rui debug-play` spawns its own plane and cannot be used while a server is
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
        resolved = Path(path).resolve()
        # Audit 2026-10-23: this endpoint is a file-existence oracle for the whole
        # host. Only media the assistant itself owns may be played back.
        cfg_now = getattr(app.state, "cfg", cfg)
        roots = [Path(cfg_now.system.data_dir).resolve()]
        cache = str(getattr(cfg_now.music, "cache_dir", "") or "")
        if cache:
            roots.append(Path(cache).resolve())
        if (not resolved.is_file()
                or not any(resolved == root or root in resolved.parents for root in roots)):
            raise HTTPException(status_code=404, detail="no such file")
        await client.send(PlayFile(path=path, loop=bool(patch.get("loop", False)),
                                   gain=float(patch.get("gain", 1.0))))
        return {"playing": path}

    async def _run_debug_say(text: str) -> dict:
        """One fresh text turn. Shared by ``/debug/say`` and the wizard's try step."""
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
        trace.note_playback_metrics(getattr(client, "metrics", None))
        traces.add(trace)
        log.info("debug say: %r -> %r", text, result.reply)
        return {
            "reply": result.reply,
            "error": result.error,
            "tools": result.tools,
            "stages": result.stages,
            "audio_bytes": result.audio_bytes,
        }

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
        return await _run_debug_say(text)

    def _channels_path() -> Path:
        cfg_now = getattr(app.state, "cfg", cfg)
        return Path(cfg_now.system.data_dir) / "channels.json"

    def _channels_defaults() -> dict:
        cfg_now = getattr(app.state, "cfg", cfg)
        data_dir = Path(cfg_now.system.data_dir)
        return {
            "telegram": {
                "enabled": False,
                "allowed_chat_ids": [],
                "reply_mode": "text",
            },
            "weixin": {
                "enabled": False,
                "allowed_user_ids": [],
                "reply_mode": "text",
                "data_dir": str(data_dir / "channels" / "weixin"),
            },
        }

    def _channels_state() -> dict:
        path = _channels_path()
        state = _channels_defaults()
        with contextlib.suppress(Exception):
            saved = json.loads(path.read_text())
            if isinstance(saved, dict):
                for name in ("telegram", "weixin"):
                    if isinstance(saved.get(name), dict):
                        state[name].update(saved[name])
        token = bool(secrets.has("TELEGRAM_BOT_TOKEN")
                     or env_get("TELEGRAM_BOT_TOKEN")
                     or env_get("RUI_TELEGRAM_TOKEN"))
        state["telegram"]["token_configured"] = token
        wx_dir = Path(state["weixin"].get("data_dir") or "")
        account = {}
        with contextlib.suppress(Exception):
            account = json.loads((wx_dir / "account.json").read_text())
        state["weixin"]["logged_in"] = bool(account.get("token"))
        state["weixin"]["account_id"] = str(account.get("accountId") or "")
        state["weixin"]["user_id"] = str(account.get("userId") or "")
        supervisor = getattr(app.state, "channel_supervisor", None)
        state["gateway"] = {
            "command": "rui-channels",
            "config_note": "The gateway reads this state at startup; restart it after changes.",
            "config_path": str(path),
            "status": supervisor.status() if supervisor is not None else None,
        }
        return state

    @app.get("/api/v1/channels", dependencies=[Depends(auth)])
    async def get_channels() -> dict:
        return _channels_state()

    @app.put("/api/v1/channels", dependencies=[Depends(auth)])
    async def put_channels(patch: Annotated[dict, Body()]) -> dict:
        allowed = {"telegram", "weixin"}
        if not isinstance(patch, dict) or not set(patch) <= allowed:
            raise HTTPException(status_code=422, detail="expected telegram and/or weixin")
        state = _channels_state()
        for name in allowed:
            if name not in patch:
                continue
            incoming = patch.get(name)
            if not isinstance(incoming, dict):
                raise HTTPException(status_code=422, detail=f"{name} must be an object")
            reply_mode = str(incoming.get("reply_mode", state[name].get("reply_mode", "text")))
            if reply_mode not in {"text", "voice", "both"}:
                raise HTTPException(status_code=422, detail="reply_mode must be text/voice/both")
            ids_key = "allowed_chat_ids" if name == "telegram" else "allowed_user_ids"
            ids = incoming.get(ids_key, state[name].get(ids_key, []))
            if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
                raise HTTPException(status_code=422, detail=f"{ids_key} must be a list of strings")
            state[name]["enabled"] = bool(incoming.get("enabled", state[name].get("enabled", False)))
            state[name]["reply_mode"] = reply_mode
            state[name][ids_key] = [x.strip() for x in ids if x.strip()]
        path = _channels_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: state[name] for name in ("telegram", "weixin")}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        return _channels_state()

    @app.post("/api/v1/channels/gateway/{action}", dependencies=[Depends(auth)])
    async def channel_gateway_action(action: str) -> dict:
        supervisor = getattr(app.state, "channel_supervisor", None)
        if supervisor is None:
            raise HTTPException(status_code=503, detail="channel supervisor is not ready")
        if action == "start":
            return await supervisor.start()
        if action == "stop":
            return await supervisor.stop()
        if action == "restart":
            return await supervisor.restart()
        raise HTTPException(status_code=404, detail="action must be start/stop/restart")

    @app.put("/api/v1/channels/telegram/token", dependencies=[Depends(auth)])
    async def set_telegram_token(patch: Annotated[dict, Body()]) -> dict:
        token = str(patch.get("token") or "").strip()
        if not token:
            secrets.put("TELEGRAM_BOT_TOKEN", "")
            return {"ok": True, "username": "", "token_configured": False}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        if response.status_code != 200:
            raise HTTPException(status_code=422, detail="Telegram rejected this bot token")
        data = response.json()
        if not data.get("ok"):
            raise HTTPException(status_code=422, detail="Telegram rejected this bot token")
        secrets.put("TELEGRAM_BOT_TOKEN", token)
        result = data.get("result") or {}
        return {"ok": True, "username": str(result.get("username") or ""),
                "token_configured": True}

    def _qr_svg(url: str) -> str:
        if not url:
            return ""
        try:
            import io

            import segno
            qr = segno.make(url)
            buf = io.BytesIO()
            qr.save(buf, kind="svg", scale=6, border=2)
            return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:  # noqa: BLE001 - the URL still works without an inline QR
            return ""

    @app.post("/api/v1/channels/weixin/login", dependencies=[Depends(auth)])
    async def start_weixin_login() -> dict:
        state = app.state.weixin_login
        task = app.state.weixin_login_task
        if task is not None and not task.done():
            return dict(state)
        state.update({"status": "starting", "qr_url": "", "qr_svg": "", "error": "", "account_id": ""})

        async def run() -> None:
            channel = _channels_state()["weixin"]
            data_dir = Path(str(channel.get("data_dir") or ""))
            adapter = WeixinAdapter(data_dir)
            try:
                def on_qr(qrcode: str, image: str) -> None:
                    url = image or qrcode
                    state.update({"status": "waiting", "qr_url": url, "qr_svg": _qr_svg(url)})

                account = await adapter.qr_login(on_qr=on_qr)
                adapter.save_login(account)
                state.update({"status": "confirmed",
                              "account_id": str(account.get("accountId") or ""),
                              "error": ""})
            except asyncio.CancelledError:
                state.update({"status": "idle"})
                raise
            except Exception as exc:  # noqa: BLE001
                state.update({"status": "error", "error": str(exc)[:300]})
            finally:
                await adapter.stop()

        app.state.weixin_login_task = asyncio.create_task(run(), name="weixin-login")
        return dict(state)

    @app.get("/api/v1/channels/weixin/login", dependencies=[Depends(auth)])
    async def get_weixin_login() -> dict:
        return dict(app.state.weixin_login)

    @app.post("/api/v1/chat", dependencies=[Depends(auth)])
    async def channel_chat(patch: Annotated[dict, Body()]) -> dict:
        """One text turn from a channel adapter (Telegram/WeChat/console).

        No audio plane, no wake word, no device: the caller gets the same runner, tools,
        history and (optionally) TTS audio. Channel sessions are isolated from the
        living-room runner history by swapping a per-session list under one lock.
        """
        text = str(patch.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="text is required")
        runner = getattr(app.state, "runner", None)
        lock = getattr(app.state, "chat_lock", None)
        histories = getattr(app.state, "chat_histories", None)
        if runner is None or lock is None or histories is None:
            raise HTTPException(status_code=503, detail="control plane is not ready")
        channel = str(patch.get("channel") or "console").strip()[:32] or "console"
        peer = str(patch.get("peer_id") or "local").strip()[:128] or "local"
        session_id = str(patch.get("session_id") or f"{channel}:{peer}")[:200]
        want_audio = bool(patch.get("want_audio", False))
        reset = bool(patch.get("reset", False))
        chunks: list[bytes] = []

        async def collect(pcm: bytes, **_kwargs: object) -> None:
            chunks.append(pcm)

        async with lock:
            if runner.busy:
                raise HTTPException(status_code=409, detail="a turn is already running")
            original = runner.history
            history = [] if reset else histories.setdefault(session_id, [])
            runner.history = history
            trace = TurnTrace(device_id=f"channel:{channel}")
            try:
                result = await runner.run(b"", collect, trace, transcript=text,
                                          audio=want_audio, touch_idle=False)
                histories[session_id] = runner.history
            finally:
                runner.history = original

        audio = b"".join(chunks)
        payload: dict[str, object] = {
            "reply": result.reply,
            "error": result.error,
            "tools": result.tools,
            "stages": {
                name: {"used": r.used, "elapsed_ms": r.elapsed_ms, "attempts": r.attempts}
                for name, r in result.stages.items()
            },
            "session_id": session_id,
            "audio_bytes": len(audio),
            "audio_format": "pcm_s16le",
            "sample_rate": cfg.audio.voice.rate,
        }
        if want_audio and audio:
            payload["audio_b64"] = base64.b64encode(audio).decode("ascii")
        return payload

    @app.post("/api/v1/asr", dependencies=[Depends(auth)])
    async def channel_asr(patch: Annotated[dict, Body()]) -> dict:
        """Transcribe a channel voice note. Accepts raw s16le PCM base64 only."""
        runner = getattr(app.state, "runner", None)
        if runner is None:
            raise HTTPException(status_code=503, detail="control plane is not ready")
        raw = str(patch.get("audio_b64") or "")
        if not raw:
            raise HTTPException(status_code=422, detail="audio_b64 is required")
        try:
            pcm = base64.b64decode(raw, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"bad audio_b64: {exc}") from exc
        if not pcm or len(pcm) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413 if pcm else 422,
                                detail="audio must be 1 byte..10 MB")
        trace = TurnTrace(device_id="channel:asr")
        text, report = await runner.transcribe(pcm, trace)
        return {
            "text": text,
            "provider": report.used if report is not None else "",
            "error": "" if text else "empty transcript",
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

    @app.get("/api/v1/music/sources", dependencies=[Depends(auth)])
    async def music_sources() -> dict:
        """The source registry the console renders plus presence-only credential state.

        The list is the same `PRESETS` table that validates `music.source` on save, so
        the UI cannot offer a source the runtime cannot build. No credential value is
        returned: only whether each slot exists and whether the active source is usable.
        """
        preset = get_preset(cfg.music.source)
        service = getattr(app.state, "music", None)
        running = getattr(getattr(service, "source", None), "source_id", "")
        state = music_credentials_state(cfg)
        return {
            "current": cfg.music.source,
            "source_known": preset is not None,
            "current_preset": preset_document(preset) if preset is not None else None,
            "sources": [preset_document(item) for item in music_presets()],
            "credentials": state,
            "enabled": service is not None,
            "running_source": running or "",
            "restart_required": bool(service is not None and running
                                     and running != cfg.music.source),
        }

    @app.get("/api/v1/music", dependencies=[Depends(auth)])
    async def music_status() -> dict:
        """What is playing and what was played: the console page and the self-tests.

        `credentials_present` is a boolean only - whether the credentials json exists at
        `music.secrets_path` (or data_dir/lan_media.json when unset). `credentials_ready`
        is whether the active preset's required slots are all present. Values are
        write-only through PUT /api/v1/music/credentials and never appear here.
        """
        preset = get_preset(cfg.music.source)
        state = music_credentials_state(cfg)
        service = getattr(app.state, "music", None)
        running = getattr(getattr(service, "source", None), "source_id", "")
        base = {
            "enabled": service is not None,
            "credentials_present": state["present"],
            "credentials_ready": state["ready"],
            "credentials_missing": state["missing"],
            "source": cfg.music.source,
            "source_known": preset is not None,
            "source_name": ({"zh": preset.name_zh, "en": preset.name_en}
                            if preset is not None else None),
            "running_source": running or "",
        }
        if service is None:
            return base
        return {
            **base,
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
        # A console-started song is an interaction too: arm the unified idle clock so it
        # ends with the same goodbye when the music stops.
        app.state.runner.idle.touch()
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

    # ------------------------------------------------------------ music credentials
    @app.put("/api/v1/music/credentials", dependencies=[Depends(auth)])
    async def music_credentials_update(payload: Annotated[dict, Body()]) -> dict:
        """Write the active source's credential slots to data_dir/lan_media.json.

        The body may include `source` (a preset id) together with any of that preset's
        credential slots. A source switch is validated against the registry and applied
        at runtime; an unknown id is a 422, never a saved typo. Values are write-only:
        the response lists which slots exist and never their content. An empty string
        deletes that slot; when nothing remains the file is removed. The file is 0600
        and atomic, and it is never part of config.yaml or the log.
        """
        source_given = "source" in payload
        source_id = cfg.music.source
        if source_given:
            raw_source = payload.get("source")
            if not isinstance(raw_source, str):
                raise HTTPException(status_code=422, detail=[
                    {"loc": ["source"], "msg": "must be a string preset id"}])
            source_id = raw_source.strip()
            if get_preset(source_id) is None:
                raise HTTPException(status_code=422, detail=[{
                    "loc": ["source"],
                    "msg": f"unknown music source {source_id!r}; choose one of "
                           f"{', '.join(preset_ids())}",
                }])
        preset = get_preset(source_id)
        if preset is None:
            raise HTTPException(status_code=422, detail=[{
                "loc": ["source"],
                "msg": f"configured music source {source_id!r} is unknown; choose one of "
                       f"{', '.join(preset_ids())}",
            }])
        if not preset.supported:
            raise HTTPException(status_code=422, detail=[{
                "loc": ["source"],
                "msg": f"music source {preset.id!r} has no runtime adapter yet",
            }])
        fields = {item.key: item for item in preset.credential_fields}
        unknown = sorted(key for key in payload if key != "source" and key not in fields)
        if unknown:
            raise HTTPException(status_code=422, detail=[
                {"loc": [key],
                 "msg": f"unknown credential field for {preset.id}; expected "
                        f"{', '.join(fields)}"}
                for key in unknown
            ])
        if not source_given and not any(key in payload for key in fields):
            raise HTTPException(status_code=422, detail=[{
                "loc": ["values"],
                "msg": f"provide source and/or one of: {', '.join(fields)}",
            }])
        path = music_credentials_path(cfg)
        data = dict(music_credentials(cfg))
        errors: list[dict] = []
        for key, field in fields.items():
            if key not in payload:
                continue
            raw = payload.get(key)
            if raw is None:
                raw = ""
            if not isinstance(raw, str):
                errors.append({"loc": [key], "msg": "must be a string"})
                continue
            value = raw.strip()
            if field.kind == "url" and value and not value.startswith(
                    ("http://", "https://")):
                errors.append({"loc": [key],
                               "msg": "url must start with http:// or https://"})
                continue
            if value:
                data[key] = value
            else:
                data.pop(key, None)
        if errors:
            raise HTTPException(status_code=422, detail=errors)
        if data:
            write_music_credentials(path, data)
        else:
            path.unlink(missing_ok=True)
        if source_given and source_id != cfg.music.source:
            current = (_read_config_or_409() if config_path is not None and
                       Path(config_path).exists() else cfg)
            merged = current.model_dump(mode="json")
            merged.setdefault("music", {})["source"] = preset.id
            _autofill_music_paths(merged, preset.id)
            updated = Config.model_validate(merged)
            if config_path is not None:
                save_config(Path(config_path), updated)
            _apply_config(updated)
            _refresh_panels()
            await _apply_music(updated)
            log.info("music: source changed to %s from the console", preset.id)
        state = music_credentials_state(cfg)
        log.info("music credentials updated (source=%s fields=%s)",
                 cfg.music.source, sorted(data))
        return {
            "has_credentials": bool(state["ready"]),
            "has_url": bool(data.get("url")),
            "has_user": bool(data.get("user")),
            "has_password": bool(data.get("password")),
        }

    @app.post("/api/v1/music/test", dependencies=[Depends(auth)])
    async def music_test(patch: Annotated[dict | None, Body()] = None) -> dict:
        """Ping a source and report latency, never the secret.

        Body fields are optional: `source` selects a preset (otherwise the configured
        one) and any credential slots present test those unsaved values without writing
        anything. `ping.view` is the same authenticated round trip a song request uses;
        no download happens. The connection is built in fixed mode, so an unsaved draft
        never touches disk.
        """
        body = patch or {}
        source_raw = body.get("source")
        source_id = (str(source_raw).strip() if source_raw is not None
                     else cfg.music.source)
        preset = get_preset(source_id)
        if preset is None:
            raise HTTPException(status_code=422, detail=[{
                "loc": ["source"],
                "msg": f"unknown music source {source_id!r}; choose one of "
                       f"{', '.join(preset_ids())}",
            }])
        if not preset.supported:
            raise HTTPException(status_code=422, detail=[{
                "loc": ["source"],
                "msg": f"music source {preset.id!r} has no runtime adapter yet",
            }])
        fields = {item.key for item in preset.credential_fields}
        data = dict(music_credentials(cfg))
        overrides = {key: str(body[key]).strip() for key in fields
                     if isinstance(body.get(key), str) and str(body[key]).strip()}
        data.update(overrides)
        missing = missing_credentials(preset, data)
        if missing:
            return {
                "ok": False, "latency_ms": None,
                "source": preset.id,
                "error": "缺少凭据字段：" + ", ".join(missing)
                         + "；先在「音源与凭据」保存或填写后再测试",
            }
        source = None
        started = time.perf_counter()
        try:
            source = build_source(
                preset.id, None, Path(cfg.music.cache_dir), 1,
                transport=getattr(app.state, "music_test_transport", None),
                policy=_outbound_policy(cfg),
                base_url=str(data.get("url") or ""),
                user=str(data.get("user") or ""),
                password=str(data.get("password") or ""),
                token=str(data.get("token") or ""),
            )
            await asyncio.wait_for(source.ping(), timeout=8.0)
            error = ""
        except Exception as exc:  # noqa: BLE001 - the point of the endpoint is to report it
            error = _redact_error(exc)
        finally:
            if source is not None:
                with contextlib.suppress(Exception):
                    await source.close()
        latency_ms = round((time.perf_counter() - started) * 1000)
        log.info("music test: source=%s ok=%s latency=%sms",
                 preset.id, not error, latency_ms)
        return {"ok": not error, "latency_ms": latency_ms, "source": preset.id,
                "error": error}

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

    # ---------------------------------------------------------------- provider presets
    # Creating a provider used to be impossible from the console: the catalog fields
    # only exist for blocks already in config.yaml, so the first step required hand
    # editing YAML. These endpoints create the *product object* (catalog entry + chain
    # entry) atomically, with preset-controlled environment names and write-only keys.
    def _provider_error(status: int, code: str, message: str,
                        details: dict | None = None) -> HTTPException:
        return HTTPException(
            status_code=status,
            detail={"code": code, "message": message, "details": details or {}},
        )

    def _provider_text(value: object) -> str:
        return str(value or "").strip()

    def _provider_name_checked(name: str, current: Config) -> str:
        if not presets.valid_provider_name(name):
            raise _provider_error(
                422, "invalid_provider_name",
                "provider name must be lowercase letters, digits, '-' or '_' (max 32)",
                {"name": name},
            )
        if name in current.providers.catalog:
            raise _provider_error(
                422, "duplicate_provider",
                f"a provider named {name!r} already exists",
                {"name": name},
            )
        return name

    def _provider_keys_checked(payload: object, allowed: set[str]) -> dict[str, str]:
        """Write-only credential values keyed by a declared environment name."""
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise _provider_error(422, "invalid_keys",
                                  "keys must be an object of ENV name -> value")
        values: dict[str, str] = {}
        for raw_name, raw_value in payload.items():
            name = _provider_text(raw_name)
            if name not in allowed:
                raise _provider_error(
                    422, "invalid_key",
                    f"{name!r} is not a credential of this provider",
                    {"key": name, "allowed": sorted(allowed)},
                )
            if not isinstance(raw_value, str):
                raise _provider_error(
                    422, "invalid_key", f"secret {name} must be a string",
                    {"key": name},
                )
            if raw_value:
                values[name] = raw_value
        return values

    def _provider_draft(payload: dict, current: Config) -> dict:
        """Validate a create/probe body without writing config or secrets.

        Returns the catalog block, the provider name/stage, and the write-only key
        values. Environment names are never taken from the client for presets, and a
        custom provider may only use ``RUI_*`` names.
        """
        preset_id = _provider_text(payload.get("preset_id"))
        custom = payload.get("custom")
        has_custom = isinstance(custom, dict)
        if bool(preset_id) == has_custom:
            raise _provider_error(
                422, "invalid_request",
                "provide exactly one of preset_id or custom",
                {"preset_id": preset_id, "custom": has_custom},
            )

        preset = None
        if preset_id:
            preset = presets.get_preset(preset_id)
            if preset is None:
                raise _provider_error(
                    422, "unknown_preset",
                    f"unknown provider preset {preset_id!r}",
                    {"preset_id": preset_id,
                     "available": [item.id for item in presets.PRESETS]},
                )
            stage = preset.stage
            requested_stage = _provider_text(payload.get("stage"))
            if requested_stage and requested_stage != stage:
                raise _provider_error(
                    422, "stage_mismatch",
                    f"preset {preset.id!r} is for {stage}, not {requested_stage!r}",
                    {"preset_id": preset.id, "stage": stage,
                     "requested": requested_stage},
                )
            name = _provider_name_checked(
                _provider_text(payload.get("name")) or preset.default_name, current)
            base_url = _provider_text(payload.get("base_url")) or preset.base_url
            model = _provider_text(payload.get("model")) or preset.default_model
            if preset.kind == "openai_compat" and not base_url.startswith(
                    ("http://", "https://")):
                raise _provider_error(
                    422, "invalid_base_url",
                    "base_url must start with http:// or https://",
                    {"base_url": base_url},
                )
            block = {
                "kind": preset.kind,
                "base_url": base_url,
                "model": model,
                "api_key_env": preset.api_key_env,
                "app_id_env": preset.app_id_env,
                "api_secret_env": preset.api_secret_env,
                "voice": preset.default_voice,
                "timeout_s": preset.timeout_s,
            }
            allowed_env = set(preset.required_env)
        else:
            assert isinstance(custom, dict)  # has_custom above
            stage = _provider_text(custom.get("stage")).lower()
            if stage not in STAGES:
                raise _provider_error(
                    422, "invalid_stage",
                    f"stage must be one of {', '.join(STAGES)}",
                    {"stage": stage, "stages": list(STAGES)},
                )
            kind = (_provider_text(custom.get("kind")) or "openai_compat").lower()
            if not presets.kind_allowed(stage, kind):
                raise _provider_error(
                    422, "invalid_kind",
                    f"kind {kind!r} cannot serve {stage}",
                    {"stage": stage, "kind": kind,
                     "allowed": list(presets.STAGE_KINDS.get(stage, ()))},
                )
            name = _provider_name_checked(_provider_text(custom.get("name")), current)
            base_url = _provider_text(custom.get("base_url"))
            model = _provider_text(custom.get("model"))
            if not base_url and kind in ("openai_compat", "volc_asr", "volc_tts"):
                # Built-in vendor endpoints have defaults; an OpenAI-compatible one
                # does not, so an empty value would create a permanently unready entry.
                if kind == "openai_compat":
                    raise _provider_error(
                        422, "invalid_base_url",
                        "base_url is required for a custom OpenAI-compatible provider",
                        {"kind": kind},
                    )
            if base_url and not base_url.startswith(("http://", "https://")):
                raise _provider_error(
                    422, "invalid_base_url",
                    "base_url must start with http:// or https://",
                    {"base_url": base_url},
                )
            if kind == "sherpa" and not model:
                raise _provider_error(
                    422, "invalid_model",
                    "a local sherpa provider needs a model directory path",
                    {"kind": kind},
                )
            env_fields = ("api_key_env", "app_id_env", "api_secret_env")
            env_values: dict[str, str] = {}
            for field_name in env_fields:
                value = _provider_text(custom.get(field_name))
                if value and not presets.valid_env_name(
                        value, prefix=presets.CUSTOM_ENV_PREFIX):
                    raise _provider_error(
                        422, "invalid_env",
                        f"{field_name} must be an uppercase {presets.CUSTOM_ENV_PREFIX}* "
                        "environment name",
                        {"field": field_name, "value": value,
                         "prefix": presets.CUSTOM_ENV_PREFIX},
                    )
                env_values[field_name] = value
            if kind == "xfyun" and not all(env_values.values()):
                raise _provider_error(
                    422, "missing_env",
                    "an xfyun provider needs api_key_env, app_id_env and api_secret_env",
                    {"kind": kind, "fields": list(env_fields)},
                )
            if kind in ("volc_asr", "volc_tts") and not env_values["api_key_env"]:
                raise _provider_error(
                    422, "missing_env",
                    f"a {kind} provider needs api_key_env",
                    {"kind": kind, "field": "api_key_env"},
                )
            block = {
                "kind": kind,
                "base_url": base_url,
                "model": model,
                "api_key_env": env_values["api_key_env"],
                "app_id_env": env_values["app_id_env"],
                "api_secret_env": env_values["api_secret_env"],
                "voice": _provider_text(custom.get("voice")),
                "timeout_s": 30.0 if stage == "llm" else 15.0,
            }
            allowed_env = {value for value in env_values.values() if value}

        keys = _provider_keys_checked(payload.get("keys"), allowed_env)
        return {
            "name": name,
            "stage": stage,
            "kind": block["kind"],
            "block": block,
            "keys": keys,
            "preset": preset,
        }

    def _provider_status(current: Config) -> dict:
        """Ready/missing/reason/probe for every configured chain entry.

        Readiness is the same predicate the runtime uses, so a green badge cannot
        disagree with the chain that actually serves a turn. This is a read: no network
        probe is started here; the UI's test button calls the probe endpoint.
        """
        by_stage: dict[str, dict] = {}
        flat: list[dict] = []
        for stage in STAGES:
            chain = list(getattr(current.providers, stage).chain)
            rows: list[dict] = []
            for index, name in enumerate(chain):
                provider = current.providers.catalog.get(name)
                if provider is None:
                    continue
                ready, missing, reason = keycheck.provider_readiness(
                    current.providers, stage, name)
                label_zh, label_en = keycheck.display_names(name, provider)
                preset = presets.preset_for(stage, provider.kind, provider.base_url)
                if preset is not None:
                    label_zh, label_en = preset.name_zh, preset.name_en
                check = presets.probe_for(stage, provider.kind)
                docs_url = (preset.docs_url if preset is not None
                            else keycheck.provider_docs_url(stage, provider))
                row = {
                    "name": name,
                    "stage": stage,
                    "kind": provider.kind,
                    "display_name": label_zh,
                    "display_name_en": label_en,
                    "base_url": provider.base_url,
                    "model": provider.model,
                    "api_key_env": provider.api_key_env,
                    "app_id_env": provider.app_id_env,
                    "api_secret_env": provider.api_secret_env,
                    "keyless": not any((provider.api_key_env, provider.app_id_env,
                                        provider.api_secret_env)),
                    "docs_url": docs_url,
                    "configured": not missing,
                    "ready": ready,
                    "missing": missing,
                    "reason": reason,
                    "primary": index == 0,
                    "chain_index": index,
                    "in_chain": True,
                    "probe": {"supported": check != "none", "check": check},
                }
                rows.append(row)
                flat.append(row)
            satisfied, missing_all = keycheck.stage_satisfied(current, stage)
            by_stage[stage] = {
                "chain": chain,
                "primary": chain[0] if chain else "",
                "ready": [row["name"] for row in rows if row["ready"]],
                "not_ready": [row["name"] for row in rows if not row["ready"]],
                "satisfied": satisfied,
                "missing": missing_all,
                "providers": rows,
            }
        catalog = []
        for name, provider in current.providers.catalog.items():
            stages = [stage for stage in STAGES
                      if name in getattr(current.providers, stage).chain]
            catalog.append({
                "name": name,
                "kind": provider.kind,
                "stages": stages,
                "in_chain": bool(stages),
                "api_key_env": provider.api_key_env,
                "base_url": provider.base_url,
                "model": provider.model,
            })
        return {"providers": flat, "stages": by_stage, "catalog": catalog}

    @app.get("/api/v1/providers/presets", dependencies=[Depends(auth)])
    async def provider_presets(stage: str | None = None) -> dict:
        """Available presets (display names, docs, required fields, probe support).

        Values never appear here: a credential is only an environment name. The
        response also declares what custom mode accepts (RUI_* names only).
        """
        try:
            return presets.document(stage)
        except ValueError as exc:
            raise _provider_error(422, "invalid_stage", str(exc),
                                  {"stage": stage, "stages": list(STAGES)}) from exc

    @app.get("/api/v1/providers/status", dependencies=[Depends(auth)])
    async def provider_status() -> dict:
        cfg_now = getattr(app.state, "cfg", cfg)
        return _provider_status(cfg_now)

    @app.post("/api/v1/providers", dependencies=[Depends(auth)])
    async def provider_create(payload: Annotated[dict, Body()]) -> dict:
        """Create a provider: catalog entry + chain position, with write-only keys.

        The endpoint re-reads config from disk first (no stale page can drop another
        provider), validates the whole `Config`, writes through the atomic `save_config`
        path, and only then stores the keys. It returns the created provider summary
        (ready/missing/reason/probe), never a key value.
        """
        current = _read_config_or_409()
        draft = _provider_draft(payload, current)
        name = draft["name"]
        stage = draft["stage"]
        set_primary = payload.get("set_primary", False)
        if not isinstance(set_primary, bool):
            raise _provider_error(422, "invalid_request",
                                  "set_primary must be true or false",
                                  {"set_primary": set_primary})
        merged = current.model_dump(mode="json")
        merged["providers"]["catalog"][name] = draft["block"]
        previous_chain = list(getattr(current.providers, stage).chain)
        rest = [item for item in previous_chain if item != name]
        merged["providers"][stage]["chain"] = (
            [name, *rest] if set_primary else [*rest, name])
        try:
            updated = Config.model_validate(merged)
        except ValidationError as exc:
            raise _provider_error(
                422, "invalid_config", "the resulting config is invalid",
                {"errors": [
                    {"loc": list(error.get("loc", ())),
                     "msg": str(error.get("msg", ""))}
                    for error in exc.errors(include_url=False)
                ]},
            ) from exc
        await _write_config(updated)
        # Keys are written after the config validates. Nothing above echoes them.
        for env_name, value in draft["keys"].items():
            try:
                secrets.put(env_name, value)
            except ValueError as exc:
                raise _provider_error(
                    422, "secret_rejected", str(exc), {"key": env_name}) from exc
        log.info("providers: created %s (%s/%s, keys=%d, primary=%s)",
                 name, stage, draft["kind"], len(draft["keys"]), set_primary)
        live = getattr(app.state, "cfg", updated)
        status = _provider_status(live)
        created = next((row for row in status["stages"][stage]["providers"]
                        if row["name"] == name), None)
        return {
            "provider": created,
            "stage": status["stages"][stage],
            "status": status,
            "restart_required": True,
            "ok": True,
        }

    @app.post("/api/v1/providers/probe", dependencies=[Depends(auth)])
    async def provider_probe(payload: Annotated[dict, Body()]) -> dict:
        """Test a not-yet-created provider with the same probe as the keys step.

        The draft is assembled in memory only; config and secrets are untouched. Unsaved
        keys are ephemeral, exactly like `keys.test`, and are never echoed.
        """
        current = _read_config_or_409()
        draft = _provider_draft(payload, current)
        temp = current.model_copy(deep=True)
        temp.providers.catalog[draft["name"]] = ProviderConfig.model_validate(
            draft["block"])
        stage = draft["stage"]
        chain = [draft["name"], *[
            item for item in getattr(temp.providers, stage).chain
            if item != draft["name"]]]
        setattr(temp.providers, stage, StageConfig(chain=chain))
        try:
            result = await keycheck.test_provider(
                temp, stage, draft["name"], keys=draft["keys"] or None)
        except keycheck.KeyCheckError as exc:
            raise _provider_error(422, exc.code, str(exc), exc.details) from exc
        log.info("providers: probe %s (%s) ok=%s", draft["name"], stage,
                 result.get("ok"))
        return {**result, "draft": True, "name": draft["name"]}

    def _wav_bytes(pcm: bytes, rate: int) -> bytes:
        import struct

        header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
        header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        header += b"data" + struct.pack("<I", len(pcm))
        return header + pcm

    @app.get("/api/v1/providers/voices", dependencies=[Depends(auth)])
    async def provider_voices() -> dict:
        """Voices a provider can speak with, for the console's select and audition."""
        cfg_now = getattr(app.state, "cfg", cfg)
        tts: dict[str, list[dict]] = {}
        for name in cfg_now.providers.tts.chain:
            provider = cfg_now.providers.catalog.get(name)
            kind = str(getattr(provider, "kind", "")) if provider is not None else ""
            voices = voices_for(kind)
            if voices:
                tts[name] = voices
        return {"tts": tts, "asr": {}}

    def _speaker_client_or_503() -> object:
        """The live plane client, or the sentence that tells the console to fall back."""
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if (supervisor is None or not bool(getattr(supervisor, "running", False))
                or client is None):
            raise HTTPException(
                status_code=503,
                detail="audio plane is not running; retry with output=browser",
            )
        metrics = getattr(client, "metrics", None)
        if metrics is not None and not bool(getattr(metrics, "speaker_connected", False)):
            raise HTTPException(
                status_code=503,
                detail="speaker is not connected; retry with output=browser",
            )
        return client

    @app.post("/api/v1/providers/{stage}/{name}/audition", dependencies=[Depends(auth)])
    async def provider_audition(stage: str, name: str,
                                patch: Annotated[dict, Body()] | None = None,
                                output: str | None = None) -> Response:
        """Synthesise one short line with a provider + voice; browser wav or speaker.

        `output` comes from the query string or the JSON body (`body.output` wins), and
        is `browser` (default, returns `audio/wav` exactly as before) or `speaker`:

        - `speaker` synthesises the PCM, writes one bounded WAV under
          `<data_dir>/cache/auditions/`, and queues it on the running audio plane via
          `PlayFile`. The plane keeps one pending file behind whatever currently owns the
          speaker, so an audition never interrupts a reply - it starts when the reply (or
          cue/song) releases the line. No plane / no connected speaker / a send failure is
          `503` with a "retry with output=browser" sentence, so the console can fall back.
          Response: `{"played":"speaker","duration_ms":N,"provider":name,"voice":...}`.
        - `browser` returns the wav and never touches disk or the audio plane.

        It never uses a caller-supplied path, never saves config, and is bounded to ~15 s
        of audio so a stuck vendor cannot hold the request forever.
        """
        cfg_now = getattr(app.state, "cfg", cfg)
        if stage != "tts":
            raise HTTPException(status_code=422, detail="only tts can be auditioned")
        body = patch or {}
        # Body wins over query (the console sends both while the toggle is being wired).
        selected = str(body.get("output") or output or "browser").strip().lower()
        if selected not in ("browser", "speaker"):
            raise HTTPException(status_code=422,
                                detail="output must be 'browser' or 'speaker'")
        # Check the speaker before paying for synthesis: if it cannot be heard the
        # console needs the fallback answer immediately, not after a TTS call.
        speaker_client = _speaker_client_or_503() if selected == "speaker" else None
        # Preview the draft prosody too: the console may not have saved yet, and these
        # three fields only apply at provider construction (restart) otherwise.
        providers_now = cfg_now.providers.model_copy(deep=True)
        entry = providers_now.catalog.get(name)
        if entry is not None:
            updates: dict[str, Any] = {}
            if body.get("speech_rate") not in (None, ""):
                updates["speech_rate"] = int(body["speech_rate"])
            if "emotion" in body:
                updates["emotion"] = str(body.get("emotion") or "")
            if body.get("emotion_scale") not in (None, ""):
                updates["emotion_scale"] = int(body["emotion_scale"])
            if updates:
                providers_now.catalog[name] = entry.model_copy(update=updates)
        try:
            chain = build_stage(providers_now, "tts",
                                sample_rate=cfg_now.audio.voice.rate)
        except Exception as exc:  # noqa: BLE001 - a bad chain is a user-visible config error
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        provider = next((candidate for provider_name, candidate in chain
                         if provider_name == name), None)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"no tts provider named {name!r}")
        text = str(body.get("text") or "你好，我是小瑞。这是音色试听。")
        voice = str(body.get("voice") or "")
        pcm = bytearray()
        try:
            async for chunk in provider.synthesize(text, voice):
                pcm.extend(chunk)
                if len(pcm) >= 16000 * 2 * 20:
                    break
        except Exception as exc:  # noqa: BLE001 - the UI shows the reason
            raise HTTPException(
                status_code=502,
                detail=f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        if not pcm:
            raise HTTPException(status_code=502, detail="provider returned no audio")
        rate = int(cfg_now.audio.voice.rate)
        if selected == "browser":
            return Response(
                content=_wav_bytes(bytes(pcm), rate),
                media_type="audio/wav",
                headers={"Content-Disposition": "inline; filename=audition.wav",
                         "Cache-Control": "no-store"},
            )

        duration_ms = round(len(pcm) / 2 / max(1, rate) * 1000)
        try:
            directory = audition_cache_dir(cfg_now.system.data_dir)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        try:
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            sweep_audition_files(directory)
            target = (directory / f"{AUDITION_PREFIX}{uuid.uuid4().hex}.wav").resolve()
            if directory not in target.parents:
                raise ValueError("audition path escaped the cache directory")
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_bytes(_wav_bytes(bytes(pcm), rate))
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except (OSError, ValueError) as exc:  # noqa: BLE001 - the UI shows the reason
            raise HTTPException(
                status_code=500,
                detail=f"cannot write the audition file: {type(exc).__name__}: {exc}",
            ) from exc
        assert speaker_client is not None  # set above when output == "speaker"
        try:
            await asyncio.wait_for(speaker_client.send(PlayFile(path=str(target))),
                                   timeout=5.0)
        except Exception as exc:  # noqa: BLE001 - the console falls back to the browser
            raise HTTPException(
                status_code=503,
                detail=f"speaker playback failed ({type(exc).__name__}); "
                       "retry with output=browser",
            ) from exc
        log.info("audition: %s voice=%r %.0f ms queued on the speaker",
                 name, voice, duration_ms)
        return JSONResponse({
            "played": "speaker",
            "duration_ms": duration_ms,
            "provider": name,
            "voice": voice,
        })

    @app.get("/api/v1/billing/quota", dependencies=[Depends(auth)])
    async def billing_quota(force: bool = False) -> dict:
        """Volcano balance, TTS character usage and resource packs (cached 300 s)."""
        cached = getattr(app.state, "quota_cache", None)
        now = time.time()
        if not force and cached and now - cached[0] < 300:
            return cached[1]
        try:
            data = await billing.quota_summary()
        except Exception as exc:  # noqa: BLE001 - the card shows errors instead of 500
            data = {"available_balance": None, "currency": "CNY",
                    "tts_chars_today": None, "tts_chars_7d": None, "packs": [],
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "errors": [f"{type(exc).__name__}: {str(exc)[:120]}"]}
        app.state.quota_cache = (now, data)
        return data

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
        """Every configured device, with the link state it actually has.

        `status` used to be the literal "ready" whether or not the speaker was on the
        network (audit #12). It now comes from the adapter's health report and the audio
        plane's `speaker_connected` metric; `connected` is null when no plane is running,
        because the control plane genuinely cannot see the inbound socket then.

        P3.4 adds the management summary (`enabled`, `token_configured`, effective
        `listen_port`) and an explicit warning when more than one enabled inbound device
        exists: this build drives exactly one of them.
        """
        out: list[dict] = []
        cfg_now = getattr(app.state, "cfg", cfg)
        catalog = PlugCatalog(cfg_now.system.data_dir)
        adapters = app.state.adapters
        plane = _plane_snapshot(app)
        active_id = _active_device_id(app, cfg_now)
        warnings = _device_warnings(cfg_now, active_id)
        for dev in cfg_now.devices:
            resolved = catalog.resolve(
                plug_id=_device_plug_id(dev, None), adapter=dev.adapter)
            token_ok = _token_configured(dev)
            capabilities = {str(capability) for capability in (resolved.capabilities
                                                               if resolved is not None else [])}
            adapter = adapters.adapters.get(dev.id)
            if adapter is not None:
                probe = await safe_probe(
                    adapter, device_id=dev.id, adapter_name=dev.adapter
                )
                info = probe.info
                link = _device_link(info, adapter, plane)
                capabilities.update(str(capability) for capability in info.capabilities)
                if probe.error:
                    # P1-4: a broken adapter renders as a degraded row with a stable
                    # code; the other devices and the enable/disable controls stay usable.
                    link["status"] = "error"
                row = {
                    "id": info.id,
                    "adapter": info.adapter,
                    "model": info.model,
                    "room": info.room,
                    "host": info.host,
                    "transport": info.transport.value,
                    **link,
                }
                if probe.error:
                    row["error"] = probe.error
            elif not dev.enabled:
                # A disabled device is still configured: the console must show it so it
                # can be enabled again. It is not an error and has no live link.
                row = {
                    "id": dev.id,
                    "adapter": dev.adapter,
                    "model": "",
                    "room": dev.room,
                    "host": dev.host,
                    "transport": (str(resolved.device.transport)
                                  if resolved is not None else "unknown"),
                    "status": "disabled",
                    "connected": False,
                    "last_seen": None,
                    "latency_ms": None,
                }
            else:
                row = {
                    "id": dev.id,
                    "adapter": dev.adapter,
                    "model": "",
                    "room": dev.room,
                    "host": dev.host,
                    "transport": (str(resolved.device.transport)
                                  if resolved is not None else "unknown"),
                    "status": "unavailable",
                    "connected": False,
                    "last_seen": None,
                    "latency_ms": None,
                    "error": adapters.errors.get(dev.id, "adapter not available"),
                }
            row.update({
                "enabled": bool(dev.enabled),
                "token_configured": token_ok,
                "token_env": _token_env(dev),
                "listen_port": effective_listen_port(dev),
                "params": _device_public_params(dev, resolved),
                "plug_id": _device_plug_id(dev, resolved),
                "capabilities": sorted(capabilities),
                "missing_for_barge_in": ([] if adapter is None
                                         else [c.value for c in info.missing_for_barge_in()]),
                "active": bool(dev.enabled and dev.id == active_id),
                "warnings": warnings,
            })
            out.append(row)
        return out

    # ---------------------------------------------------------------- setup wizard
    # A fresh install has to become a talking speaker without editing YAML by hand
    # (C-UI P4). These endpoints expose the same steps the wizard shows: which keys
    # exist, whether the models are on disk, and writing the first device - which
    # also regenerates the keywords file and restarts the audio plane, because the
    # wake model reads the keywords file once at process start.
    def _wizard_state() -> dict:
        cfg_now = getattr(app.state, "cfg", cfg)

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
        stage_ok: dict[str, bool] = {}
        missing_keys: set[str] = set()
        missing_by_stage: dict[str, list[str]] = {}
        stage_states = keycheck.stage_state(cfg_now)
        for stage in ("asr", "tts", "llm"):
            # Any usable provider in the chain can satisfy a use (a local ASR needs
            # model files rather than a key); the wizard shows the first usable one.
            key_ok, stage_missing = keycheck.stage_satisfied(cfg_now, stage)
            stage_ok[stage] = key_ok
            missing_keys.update(stage_missing)
            if not stage_ok[stage]:
                missing_by_stage[stage] = sorted(stage_missing)
        keys = {
            "stages": stage_ok,
            "missing": sorted(missing_keys),
            "missing_by_stage": missing_by_stage,
            "selected": {stage: stage_states[stage]["selected"]
                         for stage in ("asr", "tts", "llm")},
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
        plane = _plane_snapshot(app)
        enabled_devices = sum(1 for item in cfg_now.devices if item.enabled)
        # The last device-write restart outcome. A configured speaker whose plane failed
        # to start is not "done": the room is still deaf (functional audit P1-6).
        plane_error = str(getattr(app.state, "plane_error", "") or "")
        steps = [
            {"id": "keys", "title": "填 key", "done": all(stage_ok.values()), "detail": keys},
            {"id": "models", "title": "本地模型", "done": all(models.values()), "detail": models},
            {"id": "device", "title": "添加音箱",
             "done": device is not None and not plane_error,
             "detail": ({"id": device.id, "host": device.host, "room": device.room,
                         "enabled": device.enabled,
                         "connected": plane["speaker_connected"],
                         "plane_error": plane_error}
                        if device else {"plane_error": plane_error})},
            {"id": "wake", "title": "唤醒词",
             "done": bool(device and device.wake_words and keywords and keywords.is_file()),
             "detail": {"words": list(device.wake_words) if device else [],
                        "path": str(keywords) if keywords else ""}},
            # "Try a sentence" can succeed through the text path while the microphone
            # side is dead; the detail keeps the two apart (audit #16).
            {"id": "verify", "title": "试一句", "done": verified,
             "detail": {"text_tested": verified,
                        "speaker_connected": plane["speaker_connected"]}},
        ]
        return {
            "next_step": next((step["id"] for step in steps if not step["done"]), "done"),
            # The first four steps are "can it work at all"; the try-a-sentence step is a
            # nudge, not a blocker (a working speaker should not open the wizard forever).
            "complete": all(step["done"] for step in steps[:4]),
            "steps": steps,
            "config_path": str(config_path) if config_path else "",
            "config_exists": bool(config_path and Path(config_path).exists()),
            "audio_plane": plane["running"],
            # Same sentence the device step carries: a failed restart is not hidden.
            "plane_error": plane_error,
            # Process-up is not room-hearing: the wizard shows both numbers.
            "speaker_connected": plane["speaker_connected"],
            "enabled_devices": enabled_devices,
            "device": ({"id": device.id, "host": device.host, "room": device.room,
                        "wake_words": list(device.wake_words),
                        "enabled": device.enabled,
                        "connected": plane["speaker_connected"]} if device else None),
            "keywords_file": str(keywords) if keywords else "",
            "keywords_exists": bool(keywords and keywords.is_file()),
            "models_command": "rui fetch-models --dest /data/models/sensevoice "
                              "--vad-dest /data/models/silero_vad.onnx",
        }

    def _apply_config(updated: Config) -> None:
        """Replace the live config object in place, so every closure sees the new values."""
        for name in Config.model_fields:
            setattr(cfg, name, getattr(updated, name))
        app.state.cfg = cfg

    async def _restart_audio(supervisor: AudioPlaneSupervisor, updated: Config) -> str:
        """Restart the audio plane with `updated`; returns an error string (`""` = ok).

        Three callers share it: adding/removing a device, the wizard's wake-word step and
        the console's "restart only the audio plane" button for audio-tier saves.
        """
        supervisor.cfg = updated
        error = ""
        try:
            await supervisor.stop()
            await supervisor.start()
        except Exception as exc:  # noqa: BLE001 - a device problem must not kill the console
            error = f"{type(exc).__name__}: {exc}"
            supervisor.error = error
            log.warning("audio plane restart failed: %s", exc)
        finally:
            supervisor.watch()
        return error

    @app.post("/api/v1/system/audio/restart", dependencies=[Depends(auth)])
    async def system_audio_restart() -> dict:
        """Restart only the audio plane, not the whole service.

        Audio-tier fields (tier 2) are read by the plane process at startup, so the
        console offers this instead of a `docker compose restart` that would also drop
        the HTTP session.
        """
        supervisor = getattr(app.state, "audio", None)
        if supervisor is None:
            return {"restarted": False, "error": "audio plane not started"}
        error = await _restart_audio(supervisor, cfg)
        log.info("audio plane restart from the console: %s", error or "ok")
        return {"restarted": not error, "error": error}

    def _sync_devices(updated: Config) -> None:
        """Refresh everything a device edit changes *without* restarting a process.

        The adapters are rebuilt (so `/api/v1/devices` reads the new config), the runner
        gets the new wake words, and keywords.txt is regenerated - otherwise a plane
        restart after a wake-word edit would load the old file and the new word would
        never fire.
        """
        app.state.adapters = build_adapters(updated)
        words = [word for device in updated.devices for word in device.wake_words]
        runner = getattr(app.state, "runner", None)
        if runner is not None:
            runner.wake_words = tuple(words)
        # Rewrite the words but keep the threshold/boost already in the file: a device
        # save must not silently reset a hand-tuned gate (audit #8).
        if not _write_keywords_preserving(updated) and words:
            log.warning("devices: could not write keywords file for %d wake word(s)", len(words))

    async def _write_devices(updated: Config) -> str:
        """Persist devices[], regenerate the wake words, restart the plane.

        Returns the audio-plane restart error ("" = the plane came back). The caller must
        surface it: a device save that leaves the room deaf must not look like success
        (functional audit round 2, P1-6).
        """
        if config_path is None:
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        if not updated.audio.voice.kws_keywords_file:
            # A config that came from schema defaults has no keywords path; the wizard
            # still has to leave one behind, next to the config it just wrote.
            updated.audio.voice.kws_keywords_file = str(
                Path(config_path).resolve().parent / "keywords.txt")
        save_config(Path(config_path), updated)
        _apply_config(updated)
        _sync_devices(updated)
        supervisor = getattr(app.state, "audio", None)
        error = ""
        if supervisor is not None and not any(item.enabled for item in updated.devices):
            # Removing the last enabled speaker leaves nothing to drive: restarting
            # would only raise "no device this build can drive". Stop cleanly and let
            # the wizard show an empty device step instead of a plane error the user
            # cannot fix. A disabled leftover is kept in config but still not drivable.
            supervisor.cfg = updated
            try:
                await supervisor.stop()
            except Exception as exc:  # noqa: BLE001 - a dead plane is not a delete failure
                error = f"{type(exc).__name__}: {exc}"
                log.warning("audio plane stop failed after the last device was removed: %s", exc)
            finally:
                supervisor.watch()
        elif supervisor is not None:
            # The plane holds the device list, the wake model and the keywords file
            # from its own startup: a new speaker or a new wake word needs a restart.
            error = await _restart_audio(supervisor, updated)
        app.state.plane_error = error
        if error:
            log.warning("device write saved, but the audio plane did not come back: %s", error)
        return error

    async def _write_config(updated: Config) -> str:
        """Persist a non-device config edit and reload it into the live object.

        Provider choices do not touch the audio plane, so this deliberately does not
        call `_write_devices`: that would restart the plane and rewrite keywords for a
        change that only affects the next turn. `save_config` is the same atomic
        0600-replace writer; the reload proves the file this process will serve on the
        next restart matches the object it is using now.
        """
        if config_path is None:
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        save_config(Path(config_path), updated)
        _apply_config(load_config(Path(config_path)))
        return ""

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
        conflict = port_conflict(updated.devices, updated.system.data_dir)
        if conflict is not None:
            raise HTTPException(status_code=422, detail=conflict_detail(conflict))
        plane_error = await _write_devices(updated)
        log.info("setup: device %s written (host=%s, wake=%s, plane_error=%r)",
                 device.id, device.host, device.wake_words, plane_error)
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
        plane_error = await _write_devices(updated)
        log.info("setup: wake words written (plane_error=%r)", plane_error)
        return _wizard_state()

    @app.post("/api/v1/setup/probe", dependencies=[Depends(auth)])
    async def setup_probe(payload: Annotated[dict, Body()]) -> dict:
        """Is the host a reachable speaker? TCP + SSH banner, no login, no commands."""
        return await _probe_host(_host(payload.get("host")))

    # ---------------------------------------------------------------- plugs + setup steps
    # The feature lives in its own module; these closures are the few pieces it needs
    # from this factory (the live config, the writer that restarts the plane, the probe
    # and the real turn path), so app.py stays a wiring file.
    install_setup_routes(
        app,
        auth,
        config_path=config_path,
        current=lambda: getattr(app.state, "cfg", cfg),
        plane_snapshot=_plane_snapshot,
        write_devices=_write_devices,
        write_config=_write_config,
        probe_host=lambda host: _probe_host(host),
        run_debug_say=_run_debug_say,
        host_value=_host,
        room_value=_room,
        wake_words_value=_wake_words,
    )

    # ---------------------------------------------------------------- device admin
    def _device_or_404(device_id: str, config: Config) -> DeviceConfig:
        for device in config.devices:
            if device.id == device_id:
                return device
        raise HTTPException(status_code=404, detail=f"no such device: {device_id}")

    async def _device_detail(device_id: str) -> dict:
        cfg_now = getattr(app.state, "cfg", cfg)
        device = _device_or_404(device_id, cfg_now)
        catalog = PlugCatalog(cfg_now.system.data_dir)
        resolved = catalog.resolve(
            plug_id=_device_plug_id(device, None), adapter=device.adapter)
        params = _device_public_params(device, resolved)
        live: dict = {"status": "unavailable", "connected": False,
                      "last_seen": None, "latency_ms": None}
        adapters_now = getattr(app.state, "adapters", None)
        adapter = adapters_now.adapters.get(device_id) if adapters_now is not None else None
        if not device.enabled:
            live = {"status": "disabled", "connected": False,
                    "last_seen": None, "latency_ms": None}
        elif adapter is not None:
            probe = await safe_probe(
                adapter, device_id=device.id, adapter_name=device.adapter
            )
            info = probe.info
            plane = _plane_snapshot(app)
            capabilities = {str(capability) for capability in info.capabilities}
            if resolved is not None:
                capabilities.update(str(capability) for capability in resolved.device.capabilities)
            live = {"model": info.model, "transport": info.transport.value,
                    **_device_link(info, adapter, plane),
                    "capabilities": sorted(capabilities)}
            if probe.error:
                live["status"] = "error"
                live["error"] = probe.error
        payload = device.model_dump(mode="json")
        payload["params"] = params
        warnings = _device_warnings(cfg_now, _active_device_id(app, cfg_now))
        return {"device": payload, "live": live,
                "plug_id": _device_plug_id(device, resolved),
                "params": params,
                "enabled": bool(device.enabled),
                "token_configured": _token_configured(device),
                "token_env": _token_env(device),
                "listen_port": effective_listen_port(device),
                "warnings": warnings,
                # The last restart outcome belongs to the page: editing a device and
                # leaving the plane dead must be visible, not inferred from logs.
                "plane_error": str(getattr(app.state, "plane_error", "") or "")}

    @app.get("/api/v1/devices/{device_id}", dependencies=[Depends(auth)])
    async def device_detail(device_id: str) -> dict:
        """One device's editable config plus what the adapter reports right now."""
        return await _device_detail(device_id)

    @app.get("/api/v1/devices/{device_id}/status", dependencies=[Depends(auth)])
    async def device_status(device_id: str) -> dict:
        """The P3.4 management status: config + live evidence + why it is not ready.

        Deliberately no token value: ``token_configured``/``token_env`` are the whole
        story a page needs. ``ready`` is true only when the device is enabled, its
        credentials are usable, and the plane's live link says connected.
        """
        cfg_now = getattr(app.state, "cfg", cfg)
        device = _device_or_404(device_id, cfg_now)
        warnings = _device_warnings(cfg_now, _active_device_id(app, cfg_now))
        return await _device_status(app, device, cfg_now, warnings)

    def _device_ws_url(device: DeviceConfig, request: Request,
                       token: str) -> tuple[str, str, str, bool]:
        """The one client URL builder shared by ``/connect`` and token rotation.

        ``ws_url`` keeps a reverse proxy's host (X-Forwarded-Host / Host), swaps the
        console port for the device's inbound port, and carries the token as
        ``?token=``. The token is quoted: a generated one is hex, but a hand-set one may
        contain characters that would otherwise split the query string.
        """
        port = effective_listen_port(device)
        host, host_source, loopback = resolve_connect_host(request)
        scheme = request_ws_scheme(request)
        url = f"{scheme}://{host}:{port}/?token={quote(token, safe='')}"
        return url, host, host_source, loopback

    @app.post("/api/v1/devices/{device_id}/enabled", dependencies=[Depends(auth)])
    async def device_enabled(device_id: str, payload: Annotated[dict, Body()]) -> dict:
        """Enable or disable one device, starting/stopping the audio plane accordingly.

        Disabling the last enabled device stops the plane cleanly (same path as delete);
        enabling refreshes adapters and restarts the plane. Enabling is port-checked so
        two enabled inbound devices cannot silently fight for one listener.
        """
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=422, detail={
                "code": "invalid_values",
                "message": "enabled must be a boolean",
                "details": {"field": "enabled"},
            })
        if config_path is None or not Path(config_path).exists():
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        try:
            current = load_config(Path(config_path))
        except ConfigError as exc:
            raise HTTPException(status_code=409, detail=f"config is not readable: {exc}") from exc
        device = _device_or_404(device_id, current)
        if device.enabled == enabled:
            detail = await _device_detail(device_id)
            detail["changed"] = False
            return detail
        changed = device.model_copy(update={"enabled": enabled})
        updated = current.model_copy(update={"devices": [
            changed if item.id == device_id else item for item in current.devices]})
        if enabled:
            conflict = port_conflict(updated.devices, updated.system.data_dir)
            if conflict is not None:
                raise HTTPException(status_code=422, detail=conflict_detail(conflict))
        plane_error = await _write_devices(updated)
        # Audit without secrets: the device id and the resulting state are enough.
        log.info("audit: device %s %s from the console (plane_error=%r)",
                 device_id, "enabled" if enabled else "disabled", plane_error)
        detail = await _device_detail(device_id)
        detail["plane_error"] = plane_error
        detail["changed"] = True
        return detail

    @app.post("/api/v1/devices/{device_id}/token/rotate", dependencies=[Depends(auth)])
    async def device_token_rotate(device_id: str, request: Request) -> dict:
        """Replace the device token and return the new value exactly once.

        The new secret goes to ``device.token_env`` through ``secrets.put`` (0600
        secrets.yaml). The response is the only place the new value appears; the log
        line names the env var, never the token. If the token currently comes from the
        process environment, rotation would be a lie (env still wins over the file), so
        that case is a 409 with the fix in the message. A per-device cooldown keeps a
        stuck button from locking a speaker out repeatedly.
        """
        cfg_now = getattr(app.state, "cfg", cfg)
        device = _device_or_404(device_id, cfg_now)
        token_env = _token_env(device)
        resolution = secrets.resolve(token_env)
        if resolution is not None and resolution.source == "environment":
            raise HTTPException(status_code=409, detail={
                "code": "token_from_environment",
                "message": (
                    f"{token_env} is provided by the process environment; a rotated "
                    "value in secrets.yaml would not take effect. Update the environment "
                    "and restart, or remove it there first."
                ),
                "details": {"token_env": token_env},
            })
        now = time.monotonic()
        rotations = getattr(app.state, "token_rotations", None)
        if not isinstance(rotations, dict):
            rotations = {}
            app.state.token_rotations = rotations
        last = rotations.get(device_id)
        retry_after = TOKEN_ROTATE_INTERVAL_S - (now - float(last)) if last else 0.0
        if retry_after > 0:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "rate_limited",
                    "message": "token rotation was requested too recently; wait and retry",
                    "details": {"device_id": device_id,
                                "retry_after_s": round(retry_after, 1)},
                },
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        token = os.urandom(32).hex()
        try:
            secrets.put(token_env, token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={
                "code": "secret_rejected",
                "message": str(exc),
                "details": {"token_env": token_env},
            }) from exc
        rotations[device_id] = now
        ws_url, _host, _host_source, _loopback = _device_ws_url(device, request, token)
        log.info("audit: device %s token rotated (token_env=%s)", device_id, token_env)
        return {"token": token, "ws_url": ws_url}

    @app.get("/api/v1/devices/{device_id}/connect", dependencies=[Depends(auth)])
    async def device_connect(device_id: str, request: Request) -> dict:
        """The exact client URL + token an LX06 must dial in with (cold-install step).

        This is deliberately the ONLY GET endpoint that returns the token value: the
        speaker's boot script needs it in the ws URL, and typing it by hand from a
        `secrets.yaml` on the NAS is the step that made onboarding painful. The response
        is authenticated (same token middleware as the rest of the console) and the token
        is never logged. Masking stays in place everywhere else: `GET /api/v1/config`
        still reports only `has_device_token`, and the P3.4 status/list endpoints report
        only `token_configured`.

        `ws_url` keeps a reverse proxy's host (X-Forwarded-Host / Host), swaps the console
        port for the device's inbound port, and carries the token as `?token=`. If no
        token is provisioned, `configured` is false (unless `allow_no_token`) and the hint
        says how to fix it.
        """
        cfg_now = getattr(app.state, "cfg", cfg)
        device = _device_or_404(device_id, cfg_now)
        token = secrets.get(device.token_env) if device.token_env else ""
        ws_url, host, host_source, loopback = _device_ws_url(device, request, token)
        allow_no_token = bool(device.allow_no_token)
        configured = bool(token) or allow_no_token
        token_env = device.token_env or "RUI_DEVICE_TOKEN"
        if token:
            hint = (
                "在音箱客户端里把服务器地址填成这个 ws_url（token 已包含）。"
                "这个地址等于设备密钥，不要发到公开渠道；换了 token 要重新填。"
            )
        elif allow_no_token:
            hint = (
                f"当前允许无 token 连接（allow_no_token=true）：音箱可以直接拨入，"
                f"但同一网络里任何人都能打开麦克风。建议在密钥页设置 {token_env} "
                "后关闭 allow_no_token，再重新生成这个地址。"
            )
        else:
            hint = (
                f"还没有设备 token，音箱连不上。请在密钥页或环境变量里设置 {token_env}，"
                "或重新运行安装向导生成；设置好之后这里会给出带 token 的完整 ws_url。"
            )
        host_hint: dict[str, str] | None = None
        if loopback:
            if host_source == "server":
                host_hint = {
                    "zh": f"控制台是通过本机回环地址打开的；已自动改用服务端局域网地址 "
                          f"{host}。音箱必须能访问这个地址。",
                    "en": f"The console was opened through loopback; the server's LAN "
                          f"address {host} is used instead. The speaker must reach it.",
                }
            else:
                host_hint = {
                    "zh": "控制台是通过本机回环地址打开的，但没有找到可用的局域网 IPv4。"
                          "请改用 NAS 的局域网 IP 打开控制台，或把 ws_url 里的主机改成"
                          "音箱能访问的地址。",
                    "en": "The console was opened through loopback and no LAN IPv4 was "
                          "found. Open the console via the NAS's LAN IP, or replace the "
                          "host in ws_url with an address the speaker can reach.",
                }
        log.info("connect info issued for device %s (configured=%s, allow_no_token=%s, "
                 "host_source=%s)", device.id, configured, allow_no_token, host_source)
        payload = {
            "device_id": device.id,
            "room": device.room,
            "host": device.host,
            "ws_url": ws_url,
            "token": token,
            "token_env": device.token_env,
            "configured": configured,
            "allow_no_token": allow_no_token,
            "host_source": host_source,
            "hint": hint,
        }
        if host_hint is not None:
            payload["host_hint"] = host_hint
        return payload

    def _param_error(message: str, **details: object) -> HTTPException:
        """Delete/update validation errors keep the object shape the wizard uses."""
        return HTTPException(status_code=422, detail={
            "code": "invalid_values", "message": message, "details": details})

    def _unknown_plug_error(plug_id: str, catalog: PlugCatalog) -> HTTPException:
        return HTTPException(status_code=422, detail={
            "code": "unknown_plug",
            "message": f"no such plug: {plug_id}",
            "details": {"plug_id": plug_id, "available": sorted(catalog.entries)},
        })

    def _param_value(spec: ParamSpec | None, key: str, value: object) -> object:
        """Type/choice/range check one non-secret value; a legacy key stays loose."""
        if spec is None:
            if isinstance(value, (bool, int, float, str)):
                return value
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                return value
            raise _param_error(f"param {key!r} must be a string, number, bool or string list",
                               param=key)
        kind = spec.type
        if kind is ParamType.STRING:
            if not isinstance(value, str):
                raise _param_error(f"param {key!r} must be a string", param=key, expected="string")
            return value.strip()
        if kind is ParamType.INT:
            if isinstance(value, bool) or not isinstance(value, int):
                raise _param_error(f"param {key!r} must be an integer", param=key, expected="int")
            return value
        if kind is ParamType.BOOL:
            if not isinstance(value, bool):
                raise _param_error(f"param {key!r} must be a boolean", param=key, expected="bool")
            return value
        if kind is ParamType.LIST:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise _param_error(f"param {key!r} must be a list of strings",
                                   param=key, expected="list")
            items = [item.strip() for item in value]
            if spec.max is not None and len(items) > spec.max:
                raise _param_error(f"param {key!r} allows at most {spec.max} items",
                                   param=key, max=spec.max)
            return items
        if kind is ParamType.CHOICE:
            if not isinstance(value, str) or value not in spec.options:
                raise _param_error(
                    f"param {key!r} must be one of {spec.options}",
                    param=key, options=list(spec.options))
            return value
        raise _param_error(f"param {key!r} has an unsupported type {kind!r}", param=key)

    def _merge_params(base: dict, submitted: dict, resolved: ResolvedPlug | None) -> dict:
        """Merge ``payload.params`` into the device, validating against the manifest.

        Secret params are written to ``secrets.yaml`` and never stored in ``params`` or
        returned. A device whose plug is unknown keeps the old loose behaviour, which is
        what makes an adapter registered by a future build still editable here.
        """
        params = dict(base.get("params") or {})
        specs = ({spec.key: spec for spec in resolved.device.params}
                 if resolved is not None else {})
        for raw_key, value in submitted.items():
            key = str(raw_key)
            if key == "plug_id":
                continue
            if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
                raise _param_error(f"invalid param key: {key!r}", param=key)
            spec = specs.get(key)
            if spec is not None and spec.type is ParamType.SECRET:
                if value is None:
                    params.pop(key, None)
                    continue
                if not isinstance(value, str):
                    raise _param_error(f"secret param {key!r} must be a string",
                                       param=key, expected="string")
                try:
                    secrets.put(key.upper(), value)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail={
                        "code": "secret_rejected", "message": str(exc),
                        "details": {"param": key},
                    }) from exc
                params.pop(key, None)
                continue
            if value is None:
                params.pop(key, None)
                continue
            checked = _param_value(spec, key, value)
            if spec is not None and key in DeviceConfig.model_fields:
                # A manifest may declare config-level fields (host/room/wake_words).
                # Writing them through the normal validators keeps one source of truth.
                if key == "host":
                    base["host"] = _host(checked)
                elif key == "room":
                    base["room"] = _room(checked)
                elif key == "wake_words":
                    base["wake_words"] = _wake_words({"wake_words": checked})
                else:
                    base[key] = checked
            else:
                params[key] = checked
        if resolved is not None:
            missing = [
                spec.key for spec in resolved.device.params
                if spec.required and spec.type is not ParamType.SECRET
                and params.get(spec.key, base.get(spec.key)) in (None, "", [])
            ]
            if missing:
                raise _param_error("missing required params", missing=missing)
        return params

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
        submitted_params = payload.get("params")
        catalog = PlugCatalog(current.system.data_dir)
        if submitted_params is not None:
            if not isinstance(submitted_params, dict):
                raise _param_error("params must be an object")
            if "plug_id" in submitted_params:
                raw_plug = submitted_params.get("plug_id")
                explicit = "" if raw_plug is None else str(raw_plug).strip()
                if explicit and catalog.get(explicit) is None:
                    raise _unknown_plug_error(explicit, catalog)
                params = dict(base.get("params") or {})
                if explicit:
                    params["plug_id"] = explicit
                else:
                    params.pop("plug_id", None)
                base["params"] = params
        stored_plug = str((base.get("params") or {}).get("plug_id") or "").strip() or None
        resolved = catalog.resolve(plug_id=stored_plug, adapter=str(base.get("adapter") or ""))
        if submitted_params is not None:
            base["params"] = _merge_params(base, submitted_params, resolved)
        try:
            updated_device = DeviceConfig.model_validate(base)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=[
                {"loc": list(error.get("loc", ())), "msg": str(error.get("msg", ""))}
                for error in exc.errors(include_url=False)
            ]) from exc
        others = [item for item in current.devices if item.id != device_id]
        updated = current.model_copy(update={"devices": [updated_device, *others]})
        conflict = port_conflict(updated.devices, updated.system.data_dir)
        if conflict is not None:
            raise HTTPException(status_code=422, detail=conflict_detail(conflict))
        plane_error = await _write_devices(updated)
        log.info("device %s updated from the console (plane_error=%r)", device_id, plane_error)
        detail = await _device_detail(device_id)
        detail["plane_error"] = plane_error
        return detail

    @app.post("/api/v1/devices/{device_id}/test", dependencies=[Depends(auth)])
    async def device_test(device_id: str) -> dict:
        """Connection test for one device: evidence only, no config write, no playback.

        TCP/banner is the physical check (the same probe the wizard uses), the plane's
        ``speaker_connected`` is the live link, the token is reported as configured or
        not (never its value), and the manifest summarizes declared capabilities.
        """
        cfg_now = getattr(app.state, "cfg", cfg)
        device = _device_or_404(device_id, cfg_now)
        catalog = PlugCatalog(cfg_now.system.data_dir)
        resolved = catalog.resolve(
            plug_id=_device_plug_id(device, None), adapter=device.adapter)
        probe = await _probe_host(device.host)
        plane = _plane_snapshot(app)
        plane_ok = bool(plane["speaker_connected"]) and plane["speaker_device"] in ("", device.id)
        token_ok = bool(device.token_env and secrets.get(device.token_env)) or bool(
            device.allow_no_token)
        outbound = bool(resolved is not None and str(resolved.device.transport) == "outbound")
        capabilities = resolved.capabilities if resolved is not None else []
        missing = [name for name in ("reference", "playback") if name not in capabilities]
        if outbound:
            connection_message = "not_applicable"
        elif plane_ok:
            connection_message = "connected"
        elif int(plane.get("speaker_rejected") or 0) > 0:
            connection_message = "token_mismatch"
        elif not token_ok:
            connection_message = "token_mismatch"
        elif not plane["running"]:
            connection_message = "port_blocked"
        else:
            connection_message = "waiting_for_client"
        checks = [
            {"name": "host", "ok": bool(probe.get("usable")),
             "status": "ok" if probe.get("usable") else "fail",
             "detail": {"ssh": probe.get("ssh") or {}, "hint": probe.get("hint") or ""}},
            {"name": "plane", "ok": plane_ok,
             "status": ("ok" if plane_ok else
                        "not_applicable" if outbound else "fail"),
             "detail": {"speaker_connected": bool(plane["speaker_connected"]),
                        "running": bool(plane["running"]),
                        "device_id": plane["speaker_device"],
                        "error": plane["error"]}},
            {"name": "token", "ok": token_ok,
             "status": "ok" if token_ok else "fail",
             "detail": {"configured": token_ok, "token_env": device.token_env,
                        "allow_no_token": bool(device.allow_no_token)}},
            {"name": "capabilities", "ok": resolved is not None,
             "status": "ok" if resolved is not None else "fail",
             "detail": {"plug_id": _device_plug_id(device, resolved),
                        "capabilities": capabilities,
                        "missing_for_barge_in": missing if resolved is not None else []}},
        ]
        # An inbound speaker is green only on the live link; SSH is diagnostics. An
        # outbound speaker has no inbound link, so its checks (minus plane) are the test.
        gate = [check for check in checks if not (outbound and check["name"] == "plane")]
        ok = all(check["ok"] for check in gate)
        failed = [check["name"] for check in gate if not check["ok"]]
        return {"ok": ok, "connection_message": connection_message,
                "transport": "outbound" if outbound else "inbound",
                "checks": checks,
                "error": "" if ok else f"failed checks: {', '.join(failed)}"}

    @app.delete("/api/v1/devices/{device_id}", dependencies=[Depends(auth)])
    async def device_delete(device_id: str) -> dict:
        """Remove one device; the plane restarts with whatever remains."""
        if config_path is None or not Path(config_path).exists():
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        current = load_config(Path(config_path))
        _device_or_404(device_id, current)
        updated = current.model_copy(
            update={"devices": [item for item in current.devices if item.id != device_id]})
        plane_error = await _write_devices(updated)
        log.info("device %s removed from the console (plane_error=%r)", device_id, plane_error)
        return {"removed": device_id, "devices": [item.id for item in updated.devices],
                "plane_error": plane_error}

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
            bridge = HaBridge(updated.tools.ha, Path(updated.system.data_dir),
                              policy=_outbound_policy(updated))
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

    async def _apply_generic_mcp(updated: Config) -> None:
        """Re-apply `tools.mcp_servers` at runtime (hot, per-server isolation).

        `MCPManager.apply` keeps unchanged sessions alive, reconnects only changed ones
        and starts/ stops the rest; `_sync_generic_mcp` then replaces the `mcp.*` tools
        so the next turn sees exactly the new set. A server that fails to connect stays
        in the list with its error and keeps retrying - it never removes another's tools.
        `tools.mcp_allow_stdio` is applied first: turning it off stops any stdio child
        immediately instead of waiting for the declared restart.
        """
        mcp_manager.allow_stdio = bool(updated.tools.mcp_allow_stdio)
        # Servers started by this apply use the policy from the config being applied;
        # already-running sessions keep their old policy until the restart the UI asks
        # for (security fields are `restart` effects).
        mcp_manager.policy = _outbound_policy(updated)
        await mcp_manager.apply(updated.tools.mcp_servers)
        _sync_generic_mcp()
        generic = mcp_manager.status()
        log.info("mcp generic: re-applied, %d/%d connected, %d tool(s), stdio=%s",
                 generic["connected"], generic["enabled"], generic["tools"],
                 generic["stdio_enabled"])

    def _mcp_state() -> dict:
        cfg_now = getattr(app.state, "cfg", cfg)
        ha = cfg_now.tools.ha
        bridge = getattr(app.state, "ha", None)
        status = bridge.status if bridge is not None else {
            "connected": False, "latency_ms": None, "tools": 0, "entities": 0}
        generic = mcp_manager.status()
        # Configured order is the user's order; status comes from the live manager.
        by_name = {row["name"]: row for row in generic["servers"]}
        servers: list[dict] = []
        for server in cfg_now.tools.mcp_servers:
            row = server.model_dump(mode="json")
            row.update(by_name.get(server.name) or {
                "connected": False, "tools": 0, "tool_names": [],
                "latency_ms": None, "error": "", "last_seen": None,
            })
            servers.append(row)
        return {
            "ha": {
                "base_url": ha.base_url,
                "token_env": ha.token_env,
                "has_token": secrets.has(ha.token_env),
                "wired": bool(ha.base_url),
                **status,
                "error": getattr(getattr(bridge, "client", None), "last_error", "") or "",
            },
            # Per-server generic status: the console renders add/edit/remove and the
            # connected/tools/latency/error of each entry from this list.
            "servers": servers,
            "generic_wired": True,
            # Security gate for local (stdio) servers; the console shows a switch and
            # explains that it means code execution on the host.
            "stdio_enabled": bool(cfg_now.tools.mcp_allow_stdio),
            "generic": {
                "enabled": generic["enabled"],
                "connected": generic["connected"],
                "tools": generic["tools"],
                "stdio_enabled": generic["stdio_enabled"],
            },
        }

    @app.get("/api/v1/mcp", dependencies=[Depends(auth)])
    async def mcp_state() -> dict:
        return _mcp_state()

    @app.put("/api/v1/mcp", dependencies=[Depends(auth)])
    async def mcp_update(payload: Annotated[dict, Body()]) -> dict:
        """Write HA and/or generic servers, then reconnect what changed.

        Keeping the old HA shape; `{"servers": [...]}` replaces `tools.mcp_servers`.
        A stdio server is refused while `tools.mcp_allow_stdio` is false (security audit
        round 2, P0-1: it is arbitrary command execution on this host).
        """
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
            gate = bool(merged["tools"].get("mcp_allow_stdio", False))
            local = [index for index, server in enumerate(payload["servers"])
                     if isinstance(server, dict)
                     and str(server.get("transport") or "http") == "stdio"]
            if local and not gate:
                raise HTTPException(status_code=422, detail=[
                    {"loc": ["servers", index, "transport"],
                     "msg": "stdio MCP is disabled (tools.mcp_allow_stdio=false); "
                            "enable the gate before adding a local server"}
                    for index in local
                ])
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
        await _apply_generic_mcp(updated)
        log.info("mcp: config written from the console (ha=%s, %d generic server(s))",
                 updated.tools.ha.base_url, len(updated.tools.mcp_servers))
        return _mcp_state()

    @app.post("/api/v1/mcp/test", dependencies=[Depends(auth)])
    async def mcp_test(payload: Annotated[dict, Body()]) -> dict:
        """Probe one server without saving: HA ({base_url}) or generic ({transport,url|command}).

        HA keeps its old shape (`base_url` -> HaBridge). A payload carrying `transport`,
        `url` or `command` is a generic server probe: the manager opens one throwaway
        session, lists its tools and disconnects.
        """
        if any(key in payload for key in ("transport", "url", "command")):
            # `env` travels too: an env-dependent stdio server (tokens in its own
            # environment) can only be tested with the environment it will run with.
            raw = {key: payload[key] for key in
                   ("name", "transport", "url", "command", "env", "enabled")
                   if key in payload}
            raw.setdefault("name", "probe")
            raw.setdefault("enabled", True)
            try:
                probe_server = McpServerConfig.model_validate(raw)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=[
                    {"loc": list(error.get("loc", ())), "msg": str(error.get("msg", ""))}
                    for error in exc.errors(include_url=False)
                ]) from exc
            result = await test_generic_mcp_server(
                probe_server, allow_stdio=bool(cfg.tools.mcp_allow_stdio),
                policy=_outbound_policy(cfg))
            log.info("mcp test: %s %s ok=%s attempted=%s tools=%d",
                     probe_server.transport, probe_server.name, result["ok"],
                     result.get("attempted", True), result["tools"])
            return result
        base_url = str(payload.get("base_url") or cfg.tools.ha.base_url or "").strip()
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422,
                                detail="base_url must start with http:// or https://")
        probe_cfg = cfg.tools.ha.model_copy(update={"base_url": base_url})
        bridge = HaBridge(probe_cfg, Path(cfg.system.data_dir),
                          policy=_outbound_policy(cfg))
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
        names = secrets.stored_names()
        # The console's own auth token lives in config.yaml (it is read before any request
        # can be authenticated), but the catalog advertises it as a write-only secret so
        # the same page can set it. Report its presence here too.
        if cfg.web.auth_token:
            names = sorted({*names, "web.auth_token"})
        return {"names": names}

    @app.post("/api/v1/secrets", dependencies=[Depends(auth)])
    async def put_secret(payload: Annotated[dict, Body()]) -> dict:
        """Store a key typed in the console. An empty value deletes it.

        The value is never echoed, never logged, and never written into config.yaml: it
        goes to `secrets.yaml` (0600) next to the runtime state, which the providers and the
        search tool read through `secrets.get()`.

        `web.auth_token` is the one exception: it must be in config.yaml because it is
        read before a request can authenticate, but it is write-only from the API all the
        same (GET /api/v1/config returns `auth_required`, never the value).
        """
        name = str(payload.get("name") or "").strip()
        value = str(payload.get("value") or "").strip()
        if name == "web.auth_token":
            current = _read_config_or_409()
            updated = current.model_copy(update={
                "web": current.web.model_copy(update={"auth_token": value})})
            save_config(Path(config_path), updated)  # type: ignore[arg-type]
            _apply_config(updated)
            log.info("secrets: web.auth_token %s from the console",
                     "set" if value else "cleared")
            return {"name": name, "has_key": bool(value)}
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
