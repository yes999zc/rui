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
from ...config.schema import CONFIG_VERSION, Config
from ...config.store import ConfigError, load_config, redacted, save_config
from ...device.registry import build_adapters
from ...doctor import run_doctor
from ...session import rules
from ...session.ack import ensure_ack_wav
from ...session.text import strip_wake
from ...session.turn import TurnRunner
from ...session.vendor import VendorWindow
from ...tools.info import build_tools
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


def create_app(
    cfg: Config,
    config_path: Path | None = None,
    start_audio: bool = False,
) -> FastAPI:
    secrets.configure(cfg.system.data_dir)
    registry = ToolRegistry()
    # The information tools exist here, once: the console lists them from the registry and
    # the turn loop gets the same specs plus their handlers.
    info_tools = build_tools(cfg)
    for spec, _handler in info_tools:
        registry.register(spec)
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
            tools=info_tools,
            spoken=spoken,
        )
        app.state.spoken = spoken
        # Render the spoken wake cue now, not on the wake: synthesising it inside the wake
        # path cost the old build 1.5 s and swallowed the first request (\`ack_warmer\`).
        app.state.ack_task = asyncio.create_task(render_ack_wav(cfg), name="ack-render")

        async def _warm_filler() -> None:
            pcm = await render_filler_pcm(cfg)
            runner.filler_pcm = pcm
            app.state.filler_pcm = pcm

        # Same reason as the ack: a filler that has to be synthesised when it is needed
        # costs the latency it exists to hide.
        app.state.filler_task = asyncio.create_task(_warm_filler(), name="filler-render")
        app.state.runner = runner
        app.state.last_turn = None
        vendor = VendorWindow(cfg.session.vendor_quiet_s)
        app.state.vendor = vendor
        # Warm the instances the turns will use. This used to build its own chain and
        # warm that, which loaded the ASR model twice and left the real one cold.
        await runner.warm()
        app.state.ack_note = ""
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
                    if rules.stop_intent(clean):
                        spoken = await runner.say(rules.stop_reply(), client.speak, trace)
                        app.state.last_turn = {
                            "transcript": clean, "reply": "", "session_action": "stop",
                            "audio_bytes": spoken, "error": "", "tools": [], "stages": {},
                        }
                        traces.add(trace)
                        log.info("stop intent: %r -> session closed", clean)
                        return
                    try:
                        result = await runner.run(pcm, client.speak, trace, transcript=clean)
                    except Exception as exc:  # noqa: BLE001 - a broken turn must not kill the app
                        log.warning("turn failed: %s", exc)
                        return
                    if asr_report is not None:
                        result.stages.setdefault("asr", asr_report)
                    app.state.last_turn = result.to_dict()
                    traces.add(trace)
                    log.info("turn: %s", result.to_dict())

                client.on_utterance = on_utterance
                client.on_vendor = vendor.vendor_spoke
                client.on_wake = lambda _msg: vendor.ours_spoke()
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
        supervisor = getattr(app.state, "audio", None)
        if supervisor is not None:
            await supervisor.stop()

    app = FastAPI(title="house-ear", version=__version__, lifespan=lifespan)
    app.state.trace_log = traces  # scripts and tests read the same log the API serves

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
        return redacted(updated)

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
