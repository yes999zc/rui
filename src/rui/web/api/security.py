"""Response headers for the console/API.

Deliberately conservative and dependency-free. CSP is *not* set here: the built console
uses inline style attributes and injected module URLs, so a strict policy needs its own
audit; the headers below are safe for every current page and close the obvious sniffing /
framing / referrer leaks.
"""
from __future__ import annotations

from fastapi import FastAPI, Request


def install_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def _headers(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
