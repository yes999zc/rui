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

import pytest

from rui.config import Config
from rui.config.schema import DeviceConfig
from rui.device import loader, registry


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch):
    """Every test starts from the shipped table and an empty plugin environment."""
    before = dict(registry._BUILDERS)
    monkeypatch.delenv("RUI_ADAPTER_PLUGINS", raising=False)
    monkeypatch.delenv("HOUSE_EAR_ADAPTER_PLUGINS", raising=False)
    yield
    registry._BUILDERS.clear()
    registry._BUILDERS.update(before)


@pytest.fixture(autouse=True)
def _no_installed_entry_points(monkeypatch: pytest.MonkeyPatch):
    """Hermetic by default: an installed distribution must not leak into a test."""
    monkeypatch.setattr(loader.metadata, "entry_points", lambda **_: [])


def _write_module(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    source: str,
) -> str:
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
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

