"""Downstream adapter hook (issue #7 Q1): env + entry points, fail-soft.

These tests prove the promise the official image makes to an out-of-tree plug:

* ``RUI_ADAPTER_PLUGINS`` registers a builder the config can select, with no
  ``sitecustomize`` and no edit to ``src/rui/device/registry.py``;
* the ``rui.adapters`` entry-point group works the same way, including a
  zero-argument factory that returns the builder;
* bad specs, missing attributes and duplicate names warn and keep going;
* with nothing configured the registry is byte-for-byte unchanged.
"""
from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path

import pytest
from _plugin_compat import fixture_compat_range

from rui.config import Config
from rui.config.schema import DeviceConfig
from rui.device import loader, registry
from rui.plugins.resolve import clear_adapter_plugins


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch):
    """Every test starts from the shipped table and an empty plugin environment."""
    before = dict(registry._BUILDERS)
    monkeypatch.delenv("RUI_ADAPTER_PLUGINS", raising=False)
    monkeypatch.delenv("HOUSE_EAR_ADAPTER_PLUGINS", raising=False)
    monkeypatch.delenv(loader.DATA_DIR_ENV, raising=False)
    clear_adapter_plugins()
    yield
    registry._BUILDERS.clear()
    registry._BUILDERS.update(before)
    registry.RUNTIME_DEFAULTS.clear()
    clear_adapter_plugins()


@pytest.fixture(autouse=True)
def _no_installed_entry_points(monkeypatch: pytest.MonkeyPatch):
    """Hermetic by default: an installed distribution must not leak into a test."""
    monkeypatch.setattr(loader.metadata, "entry_points", lambda **_: [])


def _write_manifest(root, *, trust: str = "trusted", device_id: str = "test-device") -> None:
    """Write the minimal builtin/trusted manifest every in-process entry now needs."""
    if (root / "plugin.yaml").exists():
        return
    (root / "devices").mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-plugin/v0
            id: test-plug
            name: "Test plug"
            version: 1.0.0
            rui_compat: "{fixture_compat_range()}"
            license: MIT
            maintainer: "Test <test@example.invalid>"
            trust: {trust}
            devices:
              - devices/test.yaml
            """
        ),
        encoding="utf-8",
    )
    (root / "devices" / "test.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-device/v1
            id: {device_id}
            name: "Test speaker"
            vendor: "Test"
            class: A
            transport: inbound
            protocol: {{kind: ws, version: "1"}}
            capabilities: [playback]
            params: []
            """
        ),
        encoding="utf-8",
    )


def _write_module(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    source: str,
    *,
    trust: str | None = "trusted",
) -> str:
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    if trust is not None:
        _write_manifest(tmp_path, trust=trust)
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)
    return name


def _cfg(adapter: str) -> Config:
    cfg = Config()
    cfg.devices = [DeviceConfig(id="d1", host="127.0.0.1", adapter=adapter)]
    return cfg


BUILDER = """
def build(dev, cfg):
    return {"device": dev.id, "host": dev.host}
"""


def test_env_builder_registers_under_the_module_name(tmp_path, monkeypatch) -> None:
    module = _write_module(tmp_path, monkeypatch, "rui_test_env_plugin", BUILDER)
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"{module}:build")

    report = loader.load_adapter_plugins()

    assert report.warnings == []
    assert report.registered == {module: f"{loader.ENV_VAR} {module}:build"}
    assert module in registry.available()
    built = registry.build_adapters(_cfg(module))
    assert built.errors == {}
    assert built.adapters["d1"] == {"device": "d1", "host": "127.0.0.1"}


def test_env_name_prefix_sets_the_devices_adapter_name(tmp_path, monkeypatch) -> None:
    module = _write_module(tmp_path, monkeypatch, "rui_test_named_plugin", BUILDER)
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"dot2={module}:build")

    report = loader.load_adapter_plugins()

    assert report.warnings == []
    assert "dot2" in report.registered
    assert registry.build_adapters(_cfg("dot2")).adapters["d1"]["device"] == "d1"


def test_bare_module_defaults_to_build(tmp_path, monkeypatch) -> None:
    module = _write_module(tmp_path, monkeypatch, "rui_test_bare_plugin", BUILDER)
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", module)

    report = loader.load_adapter_plugins()

    assert report.warnings == []
    assert registry.build_adapters(_cfg(module)).adapters["d1"]["device"] == "d1"


def test_register_hook_can_call_the_registry_itself(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_hook_plugin",
        """
        def build(dev, cfg):
            return {"device": dev.id}

        def register(registry):
            registry.register("hooked", __name__ + ":build")
        """,
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"{module}:register")

    report = loader.load_adapter_plugins()

    assert report.warnings == []
    assert "hooked" in report.registered
    assert "register hook" in report.registered["hooked"]
    assert registry.build_adapters(_cfg("hooked")).adapters["d1"]["device"] == "d1"


def test_zero_arg_factory_returning_a_builder_and_metadata(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_factory_plugin",
        """
        def build(dev, cfg):
            return {"device": dev.id}

        def factory():
            return {"name": "dot2-meta", "build": build}
        """,
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"{module}:factory")

    report = loader.load_adapter_plugins()

    assert report.warnings == []
    # No name= prefix: the mapping's metadata names the adapter.
    assert "dot2-meta" in report.registered
    assert registry.build_adapters(_cfg("dot2-meta")).adapters["d1"]["device"] == "d1"


def test_explicit_name_wins_over_returned_metadata(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_named_factory_plugin",
        """
        def build(dev, cfg):
            return {"device": dev.id}

        def factory():
            return {"name": "from-metadata", "build": build}
        """,
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"explicit={module}:factory")

    report = loader.load_adapter_plugins()

    assert report.warnings == []
    assert "explicit" in report.registered
    assert "from-metadata" not in registry.available()


class _Point:
    """Minimal importlib.metadata.EntryPoint stand-in (name/value/load)."""

    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value

    def load(self):
        module_name, _, attribute = self.value.partition(":")
        return getattr(importlib.import_module(module_name), attribute)


def test_entry_point_registers_and_can_return_a_builder_factory(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_ep_plugin",
        """
        def build(dev, cfg):
            return {"device": dev.id}

        def factory():
            return build
        """,
    )
    points = [
        _Point("dot2-direct", f"{module}:build"),
        _Point("dot2-factory", f"{module}:factory"),
    ]
    monkeypatch.setattr(loader.metadata, "entry_points", lambda **_: points)

    report = loader.load_adapter_plugins()

    assert report.warnings == []
    assert {"dot2-direct", "dot2-factory"} <= set(report.registered)
    built = registry.build_adapters(_cfg("dot2-factory"))
    assert built.adapters["d1"]["device"] == "d1"


def test_entry_points_dedupe_identical_entries(tmp_path, monkeypatch) -> None:
    module = _write_module(tmp_path, monkeypatch, "rui_test_ep_dupe_plugin", BUILDER)
    same = _Point("dot2", f"{module}:build")
    monkeypatch.setattr(loader.metadata, "entry_points", lambda **_: [same, same, same])

    report = loader.load_adapter_plugins()

    assert report.warnings == []
    assert list(report.registered) == ["dot2"]


def test_entry_point_does_not_shadow_env_or_builtin(tmp_path, monkeypatch) -> None:
    module = _write_module(tmp_path, monkeypatch, "rui_test_ep_shadow_plugin", BUILDER)
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"dot2={module}:build")
    points = [_Point("dot2", f"{module}:build"), _Point("mock", f"{module}:build")]
    monkeypatch.setattr(loader.metadata, "entry_points", lambda **_: points)

    report = loader.load_adapter_plugins()

    assert "dot2" in report.registered
    assert registry._BUILDERS["mock"] == "rui.device.mock:build"
    # One warning for each skipped entry point, none for the env entry.
    assert sum("already registered" in warning for warning in report.warnings) == 2


def test_duplicate_env_name_last_entry_wins_with_one_warning(tmp_path, monkeypatch) -> None:
    first = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_dupe_one",
        "def build(dev, cfg):\n    return {'which': 'first'}\n",
    )
    second = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_dupe_two",
        "def build(dev, cfg):\n    return {'which': 'second'}\n",
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"dup={first}:build,dup={second}:build")

    report = loader.load_adapter_plugins()

    assert any("replaces" in warning for warning in report.warnings)
    assert report.registered["dup"].endswith(f"{second}:build")
    assert registry.build_adapters(_cfg("dup")).adapters["d1"]["which"] == "second"


def test_bad_specs_warn_and_do_not_stop_a_good_plugin(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_bad_plugin",
        """
        NOT_CALLABLE = 42

        def build(dev, cfg):
            return {"device": dev.id}
        """,
    )
    monkeypatch.setenv(
        "RUI_ADAPTER_PLUGINS",
        ",".join(
            [
                "no_such_module_xyz:build",
                "=missing-name",
                f"missing={module}:nope",
                f"noncallable={module}:NOT_CALLABLE",
                f"trailing={module}:",
                f"good={module}:build",
            ]
        ),
    )

    report = loader.load_adapter_plugins()

    assert "good" in report.registered
    assert len(report.warnings) >= 5
    assert all("\n" not in warning for warning in report.warnings)
    assert registry.build_adapters(_cfg("good")).adapters["d1"]["device"] == "d1"


def test_factory_that_raises_is_one_warning(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_raising_plugin",
        "def factory():\n    raise RuntimeError('boom')\n",
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"bad={module}:factory")

    report = loader.load_adapter_plugins()

    assert len(report.warnings) == 1
    assert "boom" in report.warnings[0]
    assert "bad" not in registry.available()


def test_nothing_configured_leaves_the_registry_unchanged() -> None:
    before = dict(registry._BUILDERS)

    report = loader.load_adapter_plugins()

    assert report.registered == {}
    assert report.warnings == []
    assert registry._BUILDERS == before


# --- impl audit P1-1: defaulted builders are builders, not factories -------------


DEFAULTED_BUILDER = """
CALLS = []

def build(dev=None, cfg=None):
    CALLS.append("build")
    return {"device": getattr(dev, "id", None), "called_with": dev is not None}
"""


def test_defaulted_builder_is_not_called_at_startup(tmp_path, monkeypatch) -> None:
    module = _write_module(tmp_path, monkeypatch, "rui_test_defaulted_plugin", DEFAULTED_BUILDER)
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"lxref={module}:build")

    report = loader.load_adapter_plugins()

    assert report.warnings == [], report.warnings
    assert sys.modules[module].CALLS == []
    assert "lxref" in report.registered
    built = registry.build_adapters(_cfg("lxref"))
    assert built.errors == {}
    assert built.adapters["d1"] == {"device": "d1", "called_with": True}


def test_defaulted_builder_via_entry_point_is_deferred(tmp_path, monkeypatch) -> None:
    module = _write_module(tmp_path, monkeypatch, "rui_test_ep_defaulted", DEFAULTED_BUILDER)
    monkeypatch.setattr(
        loader.metadata, "entry_points", lambda **_: [_Point("dot2", f"{module}:build")]
    )

    report = loader.load_adapter_plugins()

    assert report.warnings == [], report.warnings
    assert sys.modules[module].CALLS == [], "an entry point must not build at startup"
    assert registry.build_adapters(_cfg("dot2")).adapters["d1"]["device"] == "d1"


def test_zero_arg_build_is_still_a_factory(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_zero_arg_build",
        """
        def real_builder(dev, cfg):
            return {"device": dev.id}

        def build():
            return real_builder
        """,
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"zero={module}:build")

    report = loader.load_adapter_plugins()

    assert report.warnings == [], report.warnings
    assert registry.build_adapters(_cfg("zero")).adapters["d1"]["device"] == "d1"


def test_the_image_plug_registers_through_the_env_hook(tmp_path, monkeypatch) -> None:
    """A downstream image can also name the shipped plug explicitly (RUI_ADAPTER_PLUGINS)."""
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo / "plugs" / "xiaoai"))
    monkeypatch.setenv(
        "RUI_ADAPTER_PLUGINS", "lxref=xiaoai_plug.adapter:build"
    )

    report = loader.load_adapter_plugins()

    assert report.warnings == [], report.warnings
    assert "lxref" in report.registered
    built = registry.build_adapters(_cfg("lxref"))
    assert built.errors == {}
    assert built.adapters["d1"].kit_live_device_required is True


def test_the_example_plugin_registers_through_the_env_hook(tmp_path, monkeypatch) -> None:
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo / "examples" / "plugin-example"))
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", "ex=rui_plugin_example:build")
    sys.modules.pop("rui_plugin_example", None)

    report = loader.load_adapter_plugins()

    assert report.warnings == [], report.warnings
    assert "ex" in report.registered
    assert registry.build_adapters(_cfg("ex")).adapters["d1"].info.id == "d1"


# --- P0-1 / P1-9: trust gate and override guard ---------------------------------


def test_env_entry_requires_a_manifest(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path, monkeypatch, "rui_test_no_manifest", BUILDER, trust=None
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"unbacked={module}:build")

    report = loader.load_adapter_plugins()

    assert "unbacked" not in report.registered
    assert any("no plug manifest backs" in warning for warning in report.warnings)


def test_third_party_manifest_is_refused_in_process(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path, monkeypatch, "rui_test_third_party", BUILDER, trust="third-party"
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"dot2={module}:build")

    report = loader.load_adapter_plugins()

    assert "dot2" not in report.registered
    assert any("trust='third-party'" in warning for warning in report.warnings)


def test_env_cannot_replace_a_builtin_adapter_name(tmp_path, monkeypatch) -> None:
    module = _write_module(tmp_path, monkeypatch, "rui_test_builtin_shadow", BUILDER)
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"mock={module}:build")

    report = loader.load_adapter_plugins()

    assert registry._BUILDERS["mock"] == "rui.device.mock:build"
    assert "mock" not in report.registered
    assert any("built into this image" in warning for warning in report.warnings)


def test_register_hook_cannot_shadow_a_builtin_through_the_facade(
    tmp_path, monkeypatch
) -> None:
    module = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_hook_shadow",
        """
        def build(dev, cfg):
            return {"device": dev.id}

        def register(registry):
            registry.register("mock", __name__ + ":build")
            registry.register("hooked", __name__ + ":build")
        """,
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"{module}:register")

    report = loader.load_adapter_plugins()

    assert registry._BUILDERS["mock"] == "rui.device.mock:build"
    assert "hooked" in report.registered
    assert any("built into this image" in warning for warning in report.warnings)


def test_entry_point_requires_a_manifest(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path, monkeypatch, "rui_test_ep_no_manifest", BUILDER, trust=None
    )
    monkeypatch.setattr(
        loader.metadata, "entry_points", lambda **_: [_Point("dot2", f"{module}:build")]
    )

    report = loader.load_adapter_plugins()

    assert "dot2" not in report.registered
    assert any("no plug manifest backs" in warning for warning in report.warnings)


def test_user_manifest_under_data_dir_backs_the_adapter(tmp_path, monkeypatch) -> None:
    module = _write_module(tmp_path, monkeypatch, "rui_test_catalog_plugin", BUILDER, trust=None)
    data_dir = tmp_path / "data"
    plug = data_dir / "plugs" / "catalog-plug"
    plug.mkdir(parents=True)
    _write_manifest(plug, device_id="catalog-device")
    # The env name matches the plug id (explicit catalog resolution).
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"catalog-device={module}:build")

    report = loader.load_adapter_plugins(data_dir=data_dir)

    assert report.warnings == [], report.warnings
    assert "catalog-device" in report.registered


def test_create_app_starts_without_plugin_configuration(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from rui.web.api.app import create_app

    client = TestClient(create_app(Config()))
    health = client.get("/health")

    assert health.status_code == 200
    assert health.json()["adapter_plugins"] == {"registered": [], "warnings": 0}


def test_create_app_loads_the_env_hook_before_building_adapters(
    tmp_path, monkeypatch
) -> None:
    from fastapi.testclient import TestClient

    from rui.web.api.app import create_app

    module = _write_module(tmp_path, monkeypatch, "rui_test_app_plugin", BUILDER)
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"dot2={module}:build")

    client = TestClient(create_app(Config()))
    health = client.get("/health").json()

    assert health["adapter_plugins"] == {"registered": ["dot2"], "warnings": 0}



# --- P1-7 / P1-9: manifest runtime defaults and the sidecar error -----------------


def _write_runtime_manifest(
    root,
    *,
    device_id: str,
    listen_port: int = 8870,
    token_env: str = "DOT2_TOKEN",
    mode: str = "in-process",
    image: str = "ghcr.io/example/dot2:1.0.0",
) -> None:
    plug = root / "plugs" / "runtime-plug"
    plug.mkdir(parents=True, exist_ok=True)
    _write_manifest(plug)
    (plug / "plugin.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-plugin/v0
            id: runtime-plug
            name: "Runtime plug"
            version: 1.0.0
            rui_compat: "{fixture_compat_range()}"
            license: MIT
            maintainer: "Test <test@example.invalid>"
            trust: trusted
            devices:
              - devices/runtime.yaml
            """
        ),
        encoding="utf-8",
    )
    runtime = (
        f"runtime: {{mode: sidecar, image: \"{image}\", listen_port: {listen_port}}}"
        if mode == "sidecar"
        else f"runtime: {{mode: in-process, entrypoint: \"fake:build\", "
        f"listen_port: {listen_port}, token_env: {token_env}}}"
    )
    (plug / "devices" / "runtime.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-device/v1
            id: {device_id}
            name: "Runtime speaker"
            vendor: "Test"
            class: A
            transport: inbound
            protocol: {{kind: ws, version: "1"}}
            capabilities: [playback]
            {runtime}
            params: []
            """
        ),
        encoding="utf-8",
    )


def test_manifest_runtime_fills_listen_port_and_token_env(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_runtime_plugin",
        """
        def build(dev, cfg):
            return {"port": dev.listen_port, "token_env": dev.token_env}
        """,
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"dot2={module}:build")
    data_dir = tmp_path / "data"
    _write_runtime_manifest(data_dir, device_id="dot2")
    cfg = _cfg("dot2")
    cfg.system.data_dir = str(data_dir)

    report = loader.load_adapter_plugins()
    built = registry.build_adapters(cfg)

    assert report.warnings == [], report.warnings
    assert built.adapters["d1"] == {"port": 8870, "token_env": "DOT2_TOKEN"}


def test_explicit_config_values_win_over_manifest_runtime(tmp_path, monkeypatch) -> None:
    module = _write_module(
        tmp_path,
        monkeypatch,
        "rui_test_runtime_explicit",
        """
        def build(dev, cfg):
            return {"port": dev.listen_port, "token_env": dev.token_env}
        """,
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"dot2={module}:build")
    data_dir = tmp_path / "data"
    _write_runtime_manifest(data_dir, device_id="dot2")
    cfg = _cfg("dot2")
    cfg.system.data_dir = str(data_dir)
    cfg.devices[0].listen_port = 9999
    cfg.devices[0].token_env = "OPERATOR_TOKEN"

    report = loader.load_adapter_plugins()
    built = registry.build_adapters(cfg)

    assert report.warnings == [], report.warnings

    assert built.adapters["d1"] == {"port": 9999, "token_env": "OPERATOR_TOKEN"}


def test_a_sidecar_plug_fails_with_a_clear_error(tmp_path) -> None:
    data_dir = tmp_path / "data"
    _write_runtime_manifest(data_dir, device_id="dot2-side", mode="sidecar")
    registry.register("dot2-side", lambda dev, cfg: {"unreachable": True})
    cfg = _cfg("dot2-side")
    cfg.system.data_dir = str(data_dir)

    built = registry.build_adapters(cfg)

    assert "dot2-side" not in built.adapters
    assert "sidecar" in built.errors["d1"]
    assert "not implemented" in built.errors["d1"]


def test_co_located_manifest_runtime_defaults_apply(tmp_path, monkeypatch) -> None:
    plug = tmp_path / "plug"
    plug.mkdir()
    module = _write_module(
        plug,
        monkeypatch,
        "rui_test_colocated_runtime",
        """
        def build(dev, cfg):
            return {"port": dev.listen_port, "token_env": dev.token_env}
        """,
        trust=None,
    )
    (plug / "plugin.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-plugin/v0
            id: colocated-plug
            name: "Colocated plug"
            version: 1.0.0
            rui_compat: "{fixture_compat_range()}"
            license: MIT
            maintainer: "Test <test@example.invalid>"
            trust: trusted
            devices:
              - devices/test.yaml
            """
        ),
        encoding="utf-8",
    )
    (plug / "devices").mkdir()
    (plug / "devices" / "test.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-device/v1
            id: {module}
            name: "Colocated speaker"
            vendor: "Test"
            class: A
            transport: inbound
            protocol: {{kind: ws, version: "1"}}
            capabilities: [playback]
            runtime: {{mode: in-process, entrypoint: "fake:build",
                      listen_port: 8899, token_env: COLOCATED_TOKEN}}
            params: []
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", module)

    report = loader.load_adapter_plugins()

    assert report.warnings == [], report.warnings
    built = registry.build_adapters(_cfg(module))
    assert built.adapters["d1"] == {"port": 8899, "token_env": "COLOCATED_TOKEN"}
