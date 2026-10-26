"""C-002 §1.2 read-only v1 exception: `GET /api/v1/mcp`.

The v2 tools page shows MCP/HA connection state through the legacy v1 endpoint
until a v2 equivalent exists. C-002 allows the read but never the write, so these
tests pin the two properties that make the exception safe: the endpoint stays
behind `X-Auth-Token`, and the state JSON reports presence (`has_token`) instead
of secret values. The last test is the §2.3 source guard: nothing under
`web/src/v2/**` may name the v1 write endpoints.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui.config import Config, save_config
from rui.web.api.app import create_app

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}
HA_SECRET = "fake-ha-token-do-not-leak"

REPO_ROOT = Path(__file__).resolve().parents[1]
V2_SRC = REPO_ROOT / "web" / "src" / "v2"

# States for `_strip_comments`: code, the three string forms, and the three
# comment forms v2 sources use. Strings are copied verbatim and only comments are
# blanked, so a `//` inside `'a//b'` cannot swallow the call that follows it.
_SINGLE, _DOUBLE, _TEMPLATE = "'", '"', "`"
_LINE, _BLOCK, _HTML = "line", "block", "html"

_WRITE_VERBS = ("put", "post")
_WINDOW_BEFORE = 40
_WINDOW_AFTER = 160


def _strip_comments(text: str) -> str:
    """Blank out comments while preserving every string literal and line break.

    A character state machine instead of a regex: `//` inside a quoted string is
    data, not a comment. Newlines are kept so the proximity window below still maps
    to the original lines. Template literals are copied whole; a write hidden in a
    `${...}` interpolation therefore remains visible to the detector.
    """
    out: list[str] = []
    state = "code"
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if char in (_SINGLE, _DOUBLE, _TEMPLATE):
                state = char
                out.append(char)
            elif char == "/" and nxt == "/":
                state = _LINE
                out.append("  ")
                i += 2
                continue
            elif char == "/" and nxt == "*":
                state = _BLOCK
                out.append("  ")
                i += 2
                continue
            elif char == "<" and text.startswith("<!--", i):
                state = _HTML
                out.append("    ")
                i += 4
                continue
            else:
                out.append(char)
            i += 1
            continue
        if state == _LINE:
            if char == "\n":
                state = "code"
                out.append("\n")
            else:
                out.append(" ")
            i += 1
            continue
        if state == _BLOCK:
            if char == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
                continue
            out.append("\n" if char == "\n" else " ")
            i += 1
            continue
        if state == _HTML:
            if text.startswith("-->", i):
                state = "code"
                out.append("   ")
                i += 3
                continue
            out.append("\n" if char == "\n" else " ")
            i += 1
            continue
        # Inside a string literal: copy verbatim and honour backslash escapes.
        out.append(char)
        if char == "\\" and i + 1 < n:
            out.append(nxt)
            i += 2
            continue
        if char == state:
            state = "code"
        i += 1
    return "".join(out)


def _violations(text: str) -> list[str]:
    """Return one message per v2 snippet that writes the v1 MCP endpoints.

    C-002 §1.2 allows the v2 console to read `GET /api/v1/mcp` only. The URL alone
    is not a write - the GET wrapper names it - so every match inspects a window for
    `method` plus `put`/`post` (this repo writes `{ method: 'PUT' }`, verb after the
    path). `/api/v1/mcp/test` is a violation on its own. Non-MCP paths never match.
    """
    code = _strip_comments(text)
    lowered = code.lower()
    found: list[str] = []
    for match in re.finditer(r"/api/v1/mcp", lowered):
        if lowered.startswith("/test", match.end()):
            found.append(f"write endpoint {code[match.start():match.end() + 5]!r}")
            continue
        window = lowered[max(0, match.start() - _WINDOW_BEFORE):match.end() + _WINDOW_AFTER]
        if "method" not in window:
            continue
        verbs = [verb.upper() for verb in _WRITE_VERBS if re.search(rf"\b{verb}\b", window)]
        if verbs:
            found.append(f"{'/'.join(verbs)} method near {code[match.start():match.end()]!r}")
    return found


# The guard is only as good as its detector: `{ method: 'PUT' }` puts the verb
# after the path, so each case here pins one shape the earlier literal check missed.
_SYNTHETIC = (
    ("request('/api/v1/mcp', { method: 'PUT' })", True),
    ("fetch('/api/v1/mcp', {method:'PUT'})", True),
    ("request('/api/v1/mcp')", False),
    ("request<McpStatus>('/api/v1/mcp')", False),
    ("request('/api/v1/mcp/test', {method:'POST'})", True),
    ("const s='a//b'; fetch('/api/v1/mcp',{method:'PUT'})", True),
    ("/* PUT /api/v1/mcp */\nrequest('/api/v1/mcp')", False),
    ("// PUT /api/v1/mcp\nrequest('/api/v1/mcp')", False),
    ("request('/api/v1/config/values', { method: 'PUT' })", False),
)


@pytest.fixture()
def console(tmp_path, monkeypatch):
    monkeypatch.delenv("RUI_HA_TOKEN", raising=False)
    config = Config(web={"auth_token": TOKEN})
    config.system.data_dir = str(tmp_path / "data")
    path = tmp_path / "config.yaml"
    save_config(path, config)
    client = TestClient(create_app(config, config_path=path))
    yield client
    client.close()


def test_mcp_state_requires_the_console_token(console: TestClient) -> None:
    """C-002 §1.2: the exception reuses `X-Auth-Token`; a missing one is still a 401."""
    response = console.get("/api/v1/mcp")
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "bad or missing X-Auth-Token"


def test_mcp_state_is_json_for_the_console_token(console: TestClient) -> None:
    response = console.get("/api/v1/mcp", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert isinstance(body, dict)
    assert set(body) >= {"ha", "first_party", "servers", "generic", "stdio_enabled"}
    assert body["servers"] == []
    # An unconfigured bridge reports presence only; the value never crosses the wire.
    assert body["ha"]["has_token"] is False


def test_mcp_state_never_echoes_the_ha_token(console: TestClient,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """C-002 §1.2 (`no secrets in the response`): only `has_token` is reported."""
    monkeypatch.setenv("RUI_HA_TOKEN", HA_SECRET)
    response = console.get("/api/v1/mcp", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["ha"]["has_token"] is True
    assert HA_SECRET not in response.text


def test_v2_sources_never_reference_the_v1_mcp_write_endpoints() -> None:
    """C-002 §2.3 regression guard: the read-only exception cannot grow a write.

    The v2 tools page may GET `/api/v1/mcp`, but no v2 source may issue a write
    against it. The detector is checked on synthetic snippets first - a guard that
    silently misses `{ method: 'PUT' }` would promise more than it delivers - then
    run over every `.ts`/`.vue` file under `web/src/v2`.
    """
    for snippet, expected in _SYNTHETIC:
        found = _violations(snippet)
        assert bool(found) is expected, f"{snippet!r} -> {found}"
    sources = sorted([*V2_SRC.rglob("*.ts"), *V2_SRC.rglob("*.vue")])
    assert sources, f"no v2 sources under {V2_SRC}"
    violations = [
        f"{path.relative_to(REPO_ROOT)}: {item}"
        for path in sources
        for item in _violations(path.read_text(encoding="utf-8"))
    ]
    assert violations == []
