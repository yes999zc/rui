"""rui#18: an ASGI request-body limit in front of every route.

The v1 endpoints carry ``Depends(auth)``, but FastAPI reads and validates a JSON
body before the handler runs; a caller without a token could therefore make the
process buffer an arbitrarily large body. Moving parsing into 31 handlers would
change the 422 contract the console relies on, so the limit lives at the ASGI
layer instead:

* a declared ``Content-Length`` above the cap is rejected with **413** without
  reading anything;
* a streamed/chunked body is counted as it arrives and rejected with **413**
  once the cumulative size crosses the cap;
* small bodies still reach auth first, so 401 (bad/missing token) and 422 (bad
  JSON with a valid token) keep their current semantics.

Default: 1 MiB. Override with ``RUI_MAX_REQUEST_BYTES`` (the legacy
``HOUSE_EAR_`` prefix is accepted through :func:`rui.env.get`).
"""
from __future__ import annotations

from rui.env import get as env_get

DEFAULT_MAX_BODY_BYTES = 1024 * 1024
_BODY_TOO_LARGE = b'{"detail":"request body too large"}'


def max_body_bytes() -> int:
    """The configured cap; a bad or non-positive value falls back to 1 MiB."""
    raw = env_get("RUI_MAX_REQUEST_BYTES").strip()
    if not raw:
        return DEFAULT_MAX_BODY_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_BODY_BYTES
    return value if value > 0 else DEFAULT_MAX_BODY_BYTES


class BodySizeLimitMiddleware:
    """Pure ASGI middleware: bounded body, unchanged 401/422 behaviour."""

    def __init__(self, app) -> None:  # noqa: ANN001 - ASGI callable
        self.app = app
        self.max_bytes = max_body_bytes()

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        # Only methods that can carry a body need the streaming path; GET/HEAD serve
        # static files and other streaming responses which a replayed receive would
        # disturb.
        if scope.get("method", "GET") not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, send)
            return

        declared = headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                pass  # a malformed header is the downstream parser's business
            await self.app(scope, receive, send)
            return

        # No Content-Length: buffer the stream up to the cap, then replay it. Reading
        # it here (bounded) keeps a chunked body from reaching the parser at all;
        # FastAPI would otherwise turn our exception into a generic 400. A GET/HEAD
        # with no body may surface as http.disconnect - replay the (empty) buffer
        # instead of returning an empty response.
        buffered: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                await self._reject(send)
                return
            buffered.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(buffered)
        replayed = False

        async def replay_receive():  # noqa: ANN202
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(send) -> None:  # noqa: ANN001
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_BODY_TOO_LARGE)).encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _BODY_TOO_LARGE})
