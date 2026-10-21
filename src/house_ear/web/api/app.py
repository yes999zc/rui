"""Control-plane API. The browser never sees a secret."""
from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ... import __version__
from ...audio.client import AudioPlaneError, AudioPlaneSupervisor
from ...config.schema import CONFIG_VERSION, Config
from ...config.store import redacted
from ...device.registry import build_adapters
from ...doctor import run_doctor
from ...providers.chain import build_stage
from ...session.turn import DEFAULT_PROMPT, TurnRunner
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
    registry = ToolRegistry()
    adapters = build_adapters(cfg)
    traces = TraceLog()
    for dev_id, err in adapters.errors.items():
        log.warning("device %s is configured but not drivable: %s", dev_id, err)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runner = TurnRunner(
            cfg.providers, sample_rate=cfg.audio.voice.rate, system_prompt=DEFAULT_PROMPT
        )
        app.state.runner = runner
        app.state.last_turn = None
        for stage in ("asr", "tts", "llm"):
            for _name, provider in build_stage(cfg.providers, stage):
                warm = getattr(provider, "warm", None)
                if warm is not None:
                    await warm()
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
                    trace = TurnTrace(device_id=client.ready.device_id if client.ready else "")
                    if client.last_wake is not None:
                        trace.mark("wake", at_ms=client.last_wake.ts_ms)
                    try:
                        result = await runner.run(pcm, client.speak, trace)
                    except Exception as exc:  # noqa: BLE001 - a broken turn must not kill the app
                        log.warning("turn failed: %s", exc)
                        return
                    app.state.last_turn = result.to_dict()
                    traces.add(trace)
                    log.info("turn: %s", result.to_dict())

                client.on_utterance = on_utterance
        yield
        supervisor = getattr(app.state, "audio", None)
        if supervisor is not None:
            await supervisor.stop()

    app = FastAPI(title="house-ear", version=__version__, lifespan=lifespan)

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
        }

    @app.get("/api/v1/doctor", dependencies=[Depends(auth)])
    async def doctor() -> dict:
        return run_doctor(cfg).to_dict()

    @app.get("/api/v1/config", dependencies=[Depends(auth)])
    async def get_config() -> dict:
        return redacted(cfg)

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
