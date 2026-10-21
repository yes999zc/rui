"""Control-plane API. The browser never sees a secret."""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ... import __version__
from ...config.schema import Config
from ...config.store import redacted
from ...device.registry import build_adapters
from ...doctor import run_doctor
from ...tools.registry import ToolRegistry

START = time.time()
_DEFAULT_WEB_DIST = Path(__file__).resolve().parents[3].parent / "web" / "dist"
WEB_DIST = Path(os.environ.get("HOUSE_EAR_WEB_DIR", _DEFAULT_WEB_DIST))

FALLBACK = """<!doctype html><html><head><meta charset="utf-8"><title>house-ear</title>
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;max-width:640px;margin:12vh auto;padding:0 24px;color:#111}
code{background:#f4f4f5;padding:2px 6px;border-radius:4px}</style></head>
<body><h1>house-ear</h1><p>Control plane is running. The console is not built yet:</p>
<p><code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code></p>
<p>API: <code>/health</code>, <code>/api/v1/doctor</code>, <code>/api/v1/config</code></p></body></html>"""


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="house-ear", version=__version__)
    registry = ToolRegistry()
    adapters = build_adapters(cfg)

    def auth(x_auth_token: str | None = Header(default=None)) -> None:
        if not cfg.web.auth_token:
            return
        if x_auth_token != cfg.web.auth_token:
            raise HTTPException(status_code=401, detail="bad or missing X-Auth-Token")

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "uptime_s": round(time.time() - START, 1),
            "devices": len(adapters),
        }

    @app.get("/api/v1/doctor", dependencies=[Depends(auth)])
    async def doctor() -> dict:
        return run_doctor(cfg).to_dict()

    @app.get("/api/v1/config", dependencies=[Depends(auth)])
    async def get_config() -> dict:
        return redacted(cfg)

    @app.get("/api/v1/devices", dependencies=[Depends(auth)])
    async def devices() -> list[dict]:
        out = []
        for adapter in adapters.values():
            info = await adapter.probe()
            out.append(
                {
                    "id": info.id,
                    "adapter": info.adapter,
                    "model": info.model,
                    "room": info.room,
                    "host": info.host,
                    "capabilities": sorted(info.capabilities),
                    "missing_for_barge_in": [c.value for c in info.missing_for_barge_in()],
                }
            )
        return out

    @app.get("/api/v1/tools", dependencies=[Depends(auth)])
    async def tools() -> dict:
        return {"count": len(registry.names()), "names": registry.names()}

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:
            return FALLBACK

    return app
