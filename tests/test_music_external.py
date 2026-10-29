"""The external music-source hook: `RUI_MUSIC_SOURCES`.

The hook is an explicit allowlist (`id=module:factory`) for private sources that
never enter the preset registry. These tests pin the 0.8.x compatibility contract:

* parsing (whitespace, dotted factories, errors quoting the raw entry, unset = built-ins only);
* presets win over an env entry with the same id, unknown ids keep the old ValueError;
* the five-method interface, missing/non-callable methods named exactly;
* `music_sources_compat`: required, parsed with the plugin range grammar, mismatch
  reports the declared window and this build's version;
* import/factory failures are ValueError with id + original exception (`from exc`);
* runtime: app startup assembles a registered source, panel/status tell the truth,
  and a broken source degrades music to off with one warning instead of crashing;
* config save accepts registered ids and still rejects unknown ones;
* `/music/test` answers honestly (200, ok=false) for an external id.

The example source (`examples/music_sources/stub_source.py`) is used as the happy
path; failure modes get synthetic modules in pytest's tmp_path.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui import __version__
from rui.config import Config, load_config, save_config
from rui.music import build_source
from rui.music.sources.external import (
    ENV_VAR,
    SourceContext,
    external_source_ids,
    is_external_source,
    lookup_external,
    parse_entry,
    parse_registry,
)
from rui.music.sources.presets import preset_ids
from rui.web.api.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

STUB_ENTRY = "stub=examples.music_sources.stub_source:make"
LEGACY_ENV = "HOUSE_EAR_MUSIC_SOURCES"
COMPAT = 'music_sources_compat = ">=0.8.6 <0.9.0"'


@pytest.fixture(autouse=True)
def _clean_music_source_env(monkeypatch: pytest.MonkeyPatch):
    """No test may inherit an external source from the developer's shell."""
    for name in (ENV_VAR, LEGACY_ENV):
        monkeypatch.delenv(name, raising=False)


def _module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
            body: str) -> str:
    """Write a throwaway external-source module and put it on sys.path."""
    root = tmp_path / "extmods"
    root.mkdir(exist_ok=True)
    (root / f"{name}.py").write_text(body, encoding="utf-8")
    monkeypatch.syspath_prepend(str(root))
    return name


def _app(tmp_path: Path, source: str, *, enabled: bool = True,
         secrets: bool = True) -> tuple[TestClient, Config, Path]:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.music.enabled = enabled
    cfg.music.source = source
    if secrets:
        cfg.music.secrets_path = str(tmp_path / "data" / "lan_media.json")
    cfg.music.cache_dir = str(tmp_path / "cache")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(create_app(cfg, config_path=path)), cfg, path


# ----------------------------------------------------------------------- parsing

def test_unset_env_is_the_086_behaviour() -> None:
    assert parse_registry() == {}
    assert external_source_ids() == ()
    assert is_external_source("stub") is False
    assert lookup_external("stub") is None
    with pytest.raises(ValueError, match="unknown music source") as caught:
        build_source("stub", None, Path("/tmp"), 1)
    assert "'stub'" in str(caught.value)
    # Built-in presets keep working with no env at all.
    source = build_source("navidrome", None, Path("/tmp"), 1)
    assert source.source_id == "navidrome"


def test_registry_allows_whitespace_and_dotted_factories() -> None:
    specs = parse_registry(
        " first = examples.music_sources.stub_source:make ,"
        " second=some.module:build.make ")
    assert set(specs) == {"first", "second"}
    assert specs["first"].module == "examples.music_sources.stub_source"
    assert specs["first"].factory == "make"
    assert specs["second"].factory == "build.make"
    # ids are matched case-insensitively, like presets.
    assert parse_registry("MyID=some.module:make")["myid"].source_id == "MyID"


@pytest.mark.parametrize("entry", [
    "oops",
    "=some.module:make",
    "id=some.module",
    "id=:make",
    "id=some.module:",
    "id=some module:make",
    "id=some.module:build!",
])
def test_invalid_entries_quote_the_raw_entry(entry: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_entry(entry)
    assert repr(entry) in str(caught.value)
    assert ENV_VAR in str(caught.value)


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValueError) as caught:
        parse_registry("dup=a.b:make, DUP=c.d:make")
    assert "dup" in str(caught.value).lower()
    assert "DUP=c.d:make" in str(caught.value)


def test_legacy_env_prefix_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LEGACY_ENV, STUB_ENTRY)
    assert external_source_ids() == ("stub",)


# ---------------------------------------------------------------- loading a source

def test_stub_source_loads_through_the_hook(monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_VAR, STUB_ENTRY)
    source = build_source("stub", tmp_path / "creds.json", tmp_path / "cache", 2)
    assert type(source).__name__ == "StubSource"
    assert source.source_id == "stub"
    context = source.context
    assert isinstance(context, SourceContext)
    assert context.cache_dir == (tmp_path / "cache").expanduser()
    assert context.cache_tracks == 2
    assert context.secrets_path == (tmp_path / "creds.json").expanduser()

    async def run() -> None:
        assert await source.search("晴天", 5) == []
        assert await source.random(5) == []
        assert await source.favorites() == []
        assert await source.star("x:1", True) is False
        assert (await source.resolve(None)).name == "stub-silence.pcm"  # type: ignore[arg-type]

    asyncio.run(run())


def test_builtin_preset_wins_over_an_env_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "navidrome=examples.music_sources.stub_source:make")
    source = build_source("navidrome", None, Path("/tmp"), 1)
    assert type(source).__name__ == "SubsonicSource"
    assert source.source_id == "navidrome"


def test_ids_are_case_insensitive(monkeypatch: pytest.MonkeyPatch,
                                  tmp_path: Path) -> None:
    """`music.source` is matched like a preset (lowercase); the context sees that id."""
    monkeypatch.setenv(ENV_VAR, "Stub=examples.music_sources.stub_source:make")
    source = build_source("STUB", None, tmp_path / "cache", 1)
    assert source.source_id == "stub"


def test_missing_method_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = _module(tmp_path, monkeypatch, "ext_missing_star", f'''
{COMPAT}

class Source:
    name = "broken"

    async def search(self, term, limit):
        return []

    async def random(self, limit):
        return []

    async def favorites(self):
        return []

    async def resolve(self, track):
        return __file__


def make(context):
    return Source()
''')
    monkeypatch.setenv(ENV_VAR, f"broken={name}:make")
    with pytest.raises(ValueError) as caught:
        build_source("broken", None, tmp_path / "cache", 1)
    message = str(caught.value)
    assert "missing MusicSource method: star" in message
    assert "broken" in message
    assert not isinstance(caught.value, AttributeError)


def test_non_callable_method_is_named(tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    name = _module(tmp_path, monkeypatch, "ext_star_not_callable", f'''
{COMPAT}

class Source:
    name = "broken"
    star = 42

    async def search(self, term, limit):
        return []

    async def random(self, limit):
        return []

    async def favorites(self):
        return []

    async def resolve(self, track):
        return __file__


def make(context):
    return Source()
''')
    monkeypatch.setenv(ENV_VAR, f"broken={name}:make")
    with pytest.raises(ValueError) as caught:
        build_source("broken", None, tmp_path / "cache", 1)
    message = str(caught.value)
    assert "MusicSource method 'star' is not callable" in message
    assert "broken" in message


def test_factory_returning_none_is_rejected(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    name = _module(tmp_path, monkeypatch, "ext_returns_none", f'''
{COMPAT}


def make(context):
    return None
''')
    monkeypatch.setenv(ENV_VAR, f"none={name}:make")
    with pytest.raises(ValueError, match="returned None") as caught:
        build_source("none", None, tmp_path / "cache", 1)
    assert "none" in str(caught.value)


# -------------------------------------------------------------------- compat gate

def test_compat_is_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = _module(tmp_path, monkeypatch, "ext_no_compat", '''
class Source:
    name = "x"
    async def search(self, term, limit): return []
    async def random(self, limit): return []
    async def favorites(self): return []
    async def star(self, track_id, on): return True
    async def resolve(self, track): return __file__


def make(context):
    return Source()
''')
    monkeypatch.setenv(ENV_VAR, f"nocompat={name}:make")
    with pytest.raises(ValueError) as caught:
        build_source("nocompat", None, tmp_path / "cache", 1)
    assert "music_sources_compat" in str(caught.value)
    assert "does not declare" in str(caught.value)


def test_compat_mismatch_reports_range_and_this_build(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = _module(tmp_path, monkeypatch, "ext_future_compat", '''
music_sources_compat = ">=0.9.0 <1.0.0"
''')
    monkeypatch.setenv(ENV_VAR, f"future={name}:make")
    with pytest.raises(ValueError) as caught:
        build_source("future", None, tmp_path / "cache", 1)
    message = str(caught.value)
    assert ">=0.9.0 <1.0.0" in message
    assert __version__ in message


def test_compat_that_is_not_a_range_names_the_declaration(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = _module(tmp_path, monkeypatch, "ext_bad_compat", '''
music_sources_compat = "^0.8"
''')
    monkeypatch.setenv(ENV_VAR, f"badcompat={name}:make")
    with pytest.raises(ValueError) as caught:
        build_source("badcompat", None, tmp_path / "cache", 1)
    message = str(caught.value)
    assert "music_sources_compat" in message and "^0.8" in message
    assert "version range" in message


# ------------------------------------------------------- import/factory failures

def test_import_failure_keeps_the_original_exception(monkeypatch: pytest.MonkeyPatch,
                                                     tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_VAR, "gone=rui_no_such_external_module_xyz:make")
    with pytest.raises(ValueError) as caught:
        build_source("gone", None, tmp_path / "cache", 1)
    message = str(caught.value)
    assert "gone" in message
    assert "cannot import module" in message
    assert "ModuleNotFoundError" in message
    assert isinstance(caught.value.__cause__, ModuleNotFoundError)


def test_factory_failure_names_id_type_and_message(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    name = _module(tmp_path, monkeypatch, "ext_factory_raises", f'''
{COMPAT}


def make(context):
    raise RuntimeError("kaboom: no credentials")
''')
    monkeypatch.setenv(ENV_VAR, f"kaboom={name}:make")
    with pytest.raises(ValueError) as caught:
        build_source("kaboom", None, tmp_path / "cache", 1)
    message = str(caught.value)
    assert "kaboom" in message
    assert "RuntimeError" in message and "no credentials" in message
    assert isinstance(caught.value.__cause__, RuntimeError)


# --------------------------------------------------------------- runtime assembly

def test_app_assembles_a_registered_external_source(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, STUB_ENTRY)
    client, _cfg, _path = _app(tmp_path, "stub")
    with client:
        assert client.app.state.music is not None
        status = client.get("/api/v1/music").json()
        assert status["enabled"] is True
        assert status["source"] == "stub"
        assert status["source_known"] is True
        assert status["source_name"]["en"] == "External source"
        assert status["running_source"] == "stub"
        # An external source reads its own credentials: no false "missing url/user".
        assert status["credentials_ready"] is True
        assert status["credentials_missing"] == []

        sources = client.get("/api/v1/music/sources").json()
        assert sources["source_known"] is True
        assert sources["current_preset"] is None
        assert sources["credentials"]["ready"] is True
        assert sources["credentials"]["missing"] == []

        panels = {panel["id"]: panel for panel in
                  client.get("/api/v1/panels").json()["panels"]}
        assert panels["music"]["enabled"] is True
        assert panels["music"]["enabled_reason"] == ""
        names = client.get("/api/v1/tools").json()["names"]
        assert any(name.startswith("local.music_") for name in names)


def test_music_test_endpoint_is_honest_for_external_ids(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, STUB_ENTRY)
    client, _cfg, _path = _app(tmp_path, "stub")
    with client:
        response = client.post("/api/v1/music/test", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["source"] == "stub"
        assert body["latency_ms"] is None
        assert "外部音源不支持控制台测试连接" in body["error"]
        # A genuinely unknown id keeps the old 422 contract.
        assert client.post("/api/v1/music/test",
                           json={"source": "plex"}).status_code == 422


def test_broken_external_source_degrades_to_off_with_one_warning(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    name = _module(tmp_path, monkeypatch, "ext_app_factory_fails", f'''
{COMPAT}


def make(context):
    raise RuntimeError("library is down")
''')
    monkeypatch.setenv(ENV_VAR, f"down={name}:make")
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.music.enabled = True
    cfg.music.source = "down"
    cfg.music.secrets_path = str(tmp_path / "data" / "lan_media.json")
    cfg.music.cache_dir = str(tmp_path / "cache")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    caplog.set_level("WARNING")
    with TestClient(create_app(cfg, config_path=path)) as client:
        assert client.app.state.music is None
        assert client.get("/api/v1/music").json()["enabled"] is False
        panels = {panel["id"]: panel for panel in
                  client.get("/api/v1/panels").json()["panels"]}
        # Registered but never assembled: the panel must not stay green.
        assert panels["music"]["enabled"] is False
        reason = panels["music"]["enabled_reason"]
        assert "装配失败" in reason
        assert "cannot be assembled" in reason
        assert "library is down" in reason
        assert "down" in client.app.state.music_assembly_error
    assembly_warnings = [record for record in caplog.records
                         if "cannot be assembled" in record.getMessage()]
    assert len(assembly_warnings) == 1
    message = assembly_warnings[0].getMessage()
    assert "'down'" in message and "RuntimeError" in message and "library is down" in message


def test_runtime_switch_to_broken_external_source_turns_the_panel_off(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """A save that rebuilds onto a failing factory must update the panel, not just logs."""
    name = _module(tmp_path, monkeypatch, "ext_runtime_factory_fails", f'''
{COMPAT}


def make(context):
    raise RuntimeError("library is down")
''')
    monkeypatch.setenv(ENV_VAR, f"{STUB_ENTRY},down={name}:make")
    client, _cfg, _path = _app(tmp_path, "stub")
    with client:
        panels = {panel["id"]: panel for panel in
                  client.get("/api/v1/panels").json()["panels"]}
        assert panels["music"]["enabled"] is True
        caplog.set_level("WARNING")
        response = client.put("/api/v1/config/values",
                              json={"values": {"music.source": "down"}})
        assert response.status_code == 200, response.text
        # The save cannot apply live: the endpoint says a restart is needed...
        assert "music.source" in response.json()["restart_fields"]
        # ...and the runtime + panel agree that music is off, with the reason.
        assert client.app.state.music is None
        assert "library is down" in client.app.state.music_assembly_error
        panels = {panel["id"]: panel for panel in
                  client.get("/api/v1/panels").json()["panels"]}
        assert panels["music"]["enabled"] is False
        assert "装配失败" in panels["music"]["enabled_reason"]
        assert client.get("/api/v1/music").json()["enabled"] is False
    warnings = [record for record in caplog.records
                if "cannot be assembled" in record.getMessage()]
    assert len(warnings) == 1


# ------------------------------------------------------------------- config saves

def test_config_save_accepts_a_registered_external_id(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, STUB_ENTRY)
    client, cfg, path = _app(tmp_path, "navidrome", enabled=False)
    with client:
        field = next(item for item in
                     client.get("/api/v1/config/catalog").json()["fields"]
                     if item["path"] == "music.source")
        assert field["options"][:len(preset_ids())] == list(preset_ids())
        assert "stub" in field["options"]

        response = client.put("/api/v1/config/values",
                              json={"values": {"music.source": "stub"}})
        assert response.status_code == 200, response.text
        body = response.json()
        assert "music.source" in body["applied"]
        assert any("自动更新" in warning for warning in body["warnings"])
        saved = load_config(path)
        data_dir = Path(cfg.system.data_dir)
        assert saved.music.source == "stub"
        assert saved.music.secrets_path == str(data_dir / "lan_media.json")
        assert saved.music.cache_dir == str(data_dir / "music_cache")

        # Unknown ids are still the existing 422, with no write.
        response = client.put("/api/v1/config/values",
                              json={"values": {"music.source": "plex"}})
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["music.source"]
        assert load_config(path).music.source == "stub"


def test_credentials_endpoint_switches_to_external_without_touching_its_file(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, STUB_ENTRY)
    client, cfg, path = _app(tmp_path, "navidrome", enabled=False)
    with client:
        response = client.put("/api/v1/music/credentials",
                              json={"source": "stub"})
        assert response.status_code == 200, response.text
        body = response.json()
        # An external source manages its own credentials: "ready" is true, while the
        # built-in slots are honestly reported absent.
        assert body["has_credentials"] is True
        assert body["has_url"] is False
        assert body["has_user"] is False
        assert body["has_password"] is False
        assert load_config(path).music.source == "stub"
        status = client.get("/api/v1/music").json()
        assert status["source_known"] is True
        # rui does not write credential slots it cannot interpret.
        fake = client.put("/api/v1/music/credentials",
                          json={"source": "stub", "url": "http://example.invalid"})
        assert fake.status_code == 422
        assert "external" in fake.json()["detail"][0]["msg"]
        assert not (Path(cfg.system.data_dir) / "lan_media.json").exists()


# ------------------------------------------------------------ docs + example file

def test_docs_and_stub_cover_the_contract() -> None:
    doc = (REPO_ROOT / "docs" / "music-private-sources.md").read_text(encoding="utf-8")
    for needle in ("RUI_MUSIC_SOURCES", "music_sources_compat", "SourceContext",
                   "resolve", "QQ", "网易", "VIP", "真实实现永不进 git",
                   "import 期副作用", "容错 JSON 读取"):
        assert needle in doc, needle
    stub = (REPO_ROOT / "examples" / "music_sources" / "stub_source.py").read_text(
        encoding="utf-8")
    assert "music_sources_compat" in stub
    assert "EXAMPLE ONLY" in stub
