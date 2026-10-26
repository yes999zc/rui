"""Adding a speaker must be one module plus one registry line.

These tests are the architecture's acceptance test in miniature: a name that no adapter
implements has to fail readably (not at config validation), and a builder registered from
outside must work without touching the registry file.
"""
from __future__ import annotations

import asyncio

import pytest

from rui.config import Config
from rui.config.schema import DeviceConfig
from rui.device import registry
from rui.device.contract import NullServices


def cfg_with(adapter: str, **device) -> Config:
    cfg = Config()
    cfg.devices = [DeviceConfig(id="d1", host="127.0.0.1", adapter=adapter, **device)]
    return cfg


def test_the_shipped_adapters_are_registered() -> None:
    assert {"mock", "lx06"} <= set(registry.available())


def test_an_unknown_adapter_is_a_config_that_loads_and_a_device_that_does_not() -> None:
    """The config layer must not know vendor names: validation passes, building fails."""
    cfg = cfg_with("some-future-speaker")
    built = registry.build_adapters(cfg)
    assert built.adapters == {}
    assert "unknown adapter" in built.errors["d1"]
    assert "some-future-speaker" in built.errors["d1"]


def test_a_new_adapter_can_be_added_from_outside_the_registry_file() -> None:
    # A different name pointing at an existing builder is enough to prove the table is
    # data: a real device ships its own module and registers it the same way.
    registry.register("fake-new-speaker", "rui.device.mock:build")
    try:
        built = registry.build_adapters(cfg_with("fake-new-speaker"))
        assert "d1" in built.adapters
        assert built.errors == {}
    finally:
        registry._BUILDERS.pop("fake-new-speaker", None)


def test_a_device_without_services_says_so_instead_of_guessing() -> None:
    async def run() -> None:
        services = NullServices()
        # Reading answers "unknown" rather than raising: the console shows a dash.
        assert await services.read_volume() is None
        # Writing has no honest fallback, so it refuses.
        with pytest.raises(NotImplementedError):
            await services.set_volume(100)
        with pytest.raises(NotImplementedError):
            await services.shell("ls")

    asyncio.run(run())
