"""Old console links → the v2 console (0.7.8, task D).

`web.console: v2` (the default since 0.7.8) makes `/v2/` the entry point. Bookmarks
and external links written against 0.6/0.7 still name the old shell's paths: the
eleven `/panel/<id>` pages plus `/setup`, `/secrets`, `/diagnostics` and
`/channels` — 15 paths, measured as still answering the old shell in
`docs/audit/console-functional.md` §8.2. Each one must answer a 302 to its v2
counterpart instead of silently rendering the deprecated old shell.

The mapping is a table, not a rule: only pages that really exist in v2 are targets.
A panel v2 does not have yet (`providers`, `system`, `channels`, or an unknown
panel id) lands on `/v2/` with `?from=<old path>`, so the hop is visible and names
the gap instead of pretending the feature moved.

With `web.console: legacy` nothing here fires: every path above keeps serving the
old shell (200), byte for byte as 0.7.7 behaved, so the rollback stays one config
edit. `/legacy/**` always serves the old shell in both modes — the deprecation
banner tells the operator it goes away in 0.8.0.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response

V2_HOME = "/v2/"

# The eleven v1 panel ids (`_build_panels` in app.py) that have a v2 destination.
# `providers` and `system` are deliberately absent: v2 has no provider cards/
# audition and no system/web/security editor yet (`console-functional.md` §8.3,
# rows 6-7/19), so they take the `?from=` landing rather than a wrong page.
PANEL_TARGETS: dict[str, str] = {
    "overview": "/v2/run",
    "devices": "/v2/devices",
    "turns": "/v2/conversation",
    "voice-assistant": "/v2/voice-assistant",
    "integrations": "/v2/tools",
    "credentials": "/v2/credentials",
    "logs": "/v2/logs",
    "memory": "/v2/memory",
    "music": "/v2/music",
}

# The hand-written v1 routes (web/src/router.ts) beside the panel registry.
# `/channels` has no v2 page (channels are a known 0.7.8 gap), so it is not here.
PATH_TARGETS: dict[str, str] = {
    "/setup": "/v2/setup",
    "/secrets": "/v2/credentials",
    "/diagnostics": "/v2/health",
}

# Every old path the 0.7.8 acceptance list covers, in the audit's order. Tests and
# the docs table are generated from this tuple so a new panel cannot be forgotten.
LEGACY_PATHS: tuple[str, ...] = (
    *(f"/panel/{panel_id}" for panel_id in (
        "overview", "devices", "turns", "voice-assistant", "providers",
        "integrations", "system", "credentials", "logs", "memory", "music",
    )),
    "/setup",
    "/secrets",
    "/diagnostics",
    "/channels",
)


def panel_target(panel_id: str) -> str | None:
    """The v2 page for a v1 panel id, or None when v2 has no counterpart."""
    return PANEL_TARGETS.get(panel_id)


def legacy_target(path: str) -> str | None:
    """The v2 URL an old console path maps to, without the query string.

    `None` means "v2 has no page for this one"; the caller turns that into the
    `/v2/?from=<old>` landing. Sub-paths (`/panel/devices/manage`) resolve by
    panel id: v2 pages have no tabs, and dropping the tab beats a 404.
    """
    if path in PATH_TARGETS:
        return PATH_TARGETS[path]
    if path.startswith("/panel/"):
        panel_id = path[len("/panel/"):].split("/", 1)[0]
        return PANEL_TARGETS.get(panel_id)
    return None


def redirect_location(path: str, query: str = "") -> str:
    """Where an old link goes in v2 mode; the original query wins over nothing.

    A mapped page keeps the query (`/panel/logs?level=warn` → `/v2/logs?level=warn`);
    the `?from=` landing keeps it too, after the marker (`…&level=warn`).
    """
    target = legacy_target(path)
    if target is None:
        landing = f"{V2_HOME}?from={quote(path, safe='/')}"
        return f"{landing}&{query}" if query else landing
    return f"{target}?{query}" if query else target


def install_legacy_links(
    app: FastAPI,
    *,
    console_mode: Callable[[], str],
    legacy_shell: Callable[[], Response],
) -> None:
    """Register the 302s (v2) / old shell (legacy) for the 15 old console links.

    Call this from `create_app` *after* the `/` and `/legacy` handlers and *before*
    the SPA catch-all: a mapped path must win over `/{full_path:path}`, and the
    mode is read per request so a config edit + restart is the whole rollback.
    """

    def _answer(request: Request) -> Response:
        if console_mode() != "v2":
            return legacy_shell()
        location = redirect_location(request.url.path, request.url.query)
        return RedirectResponse(location, status_code=302)

    @app.get("/setup")
    @app.get("/secrets")
    @app.get("/diagnostics")
    @app.get("/channels")
    async def legacy_top_level(request: Request) -> Response:
        """`/setup` etc. are v1's hand-written routes, not panel ids."""
        return _answer(request)

    @app.get("/panel/{panel_id}")
    @app.get("/panel/{panel_id}/{rest:path}")
    async def legacy_panel(request: Request, panel_id: str, rest: str = "") -> Response:
        """A v1 panel deep link, with or without a tab, never lands on the old shell."""
        return _answer(request)
