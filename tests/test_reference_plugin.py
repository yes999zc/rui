"""P3.1: the LX06 reference plug proves the Socket & Plug v1 package contract.

The package under ``plugins/rui-plugin-xiaoai/`` is the reference that a third
party can copy: a v0 manifest with the built-in LX06 capabilities plus a thin
adapter factory that delegates to ``rui.device.xiaoai``. These tests pin that
the loader accepts it, that it does not drift from the in-code builtin, and that
the contract kit can smoke it without a speaker or a real host.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from rui.__main__ import main
from rui.device.contract import Capability, DeviceAdapter, Transport
from rui.device.xiaoai import XiaoaiAdapter
from rui.plugins import (
    DeviceClass,
    PlugCatalog,
    RuntimeMode,
    TrustLevel,
    WakeSource,
    builtin_plugin,
    load_plugin,
    parse_range,
)

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "rui-plugin-xiaoai"
ADAPTER = "rui_plugin_xiaoai.adapter:build"


def test_reference_plugin_loads_as_a_v0_rui_kws_package() -> None:
    plugin = load_plugin(PLUGIN)

    assert plugin.id == "xiaoai-lx06"
    assert plugin.trust is TrustLevel.BUILTIN
    assert plugin.source_dir == PLUGIN.resolve()
    assert len(plugin.devices) == 1

    device = plugin.devices[0]
    assert device.id == "xiaoai"
    assert device.schema == "rui-device/v0"
    assert device.device_class is DeviceClass.A
    assert device.transport is Transport.INBOUND
    assert device.protocol.kind == "ws"
    # LX06 stays on RUI KWS: v0 must not smuggle in the v1 wake block or caps.
    assert device.wake is None
    assert device.wake_source is WakeSource.RUI
    assert Capability.DEVICE_WAKE not in device.capabilities
    assert Capability.BARGE_VIA_DEVICE not in device.capabilities
    assert device.runtime is not None
    assert device.runtime.mode is RuntimeMode.IN_PROCESS
    assert device.runtime.entrypoint == ADAPTER
    assert device.runtime.listen_port == 4412
    assert [param.key for param in device.params] == ["host", "room", "wake_words"]
    assert device.device_side is not None
    assert device.device_side.install_steps


def test_reference_manifest_matches_the_builtin_capabilities() -> None:
    builtin = builtin_plugin("xiaoai-lx06")
    assert builtin is not None, "the in-code fallback must remain the default"
    builtin_device = builtin.devices[0]
    plugin = load_plugin(PLUGIN)
    reference = plugin.devices[0]

    assert reference.schema == builtin_device.schema
    assert set(reference.capabilities) == set(builtin_device.capabilities)
    assert reference.transport is builtin_device.transport
    assert reference.device_class is builtin_device.device_class
    assert reference.protocol.kind == builtin_device.protocol.kind
    assert [param.key for param in reference.params] == [
        param.key for param in builtin_device.params
    ]
    # v0 version matrix (v1 §7): one range for the whole 0.2/0.3 line. The in-code
    # builtin declares the same range and validates it against rui.__version__.
    assert plugin.rui_compat == ">=0.2.0 <0.6.0"
    assert builtin.rui_compat == ">=0.2.0 <0.6.0"
    compat = parse_range(plugin.rui_compat)
    assert compat.matches("0.2.2")
    assert compat.matches("0.3.2")
    assert compat.matches("0.4.0")
    assert not compat.matches("0.6.0")
    assert compat.matches("0.5.0")


def test_builtin_manifest_stays_the_catalog_default(tmp_path: Path) -> None:
    """Installing the reference package must not replace the fresh-install default."""
    shutil.copytree(
        PLUGIN,
        tmp_path / "plugs" / "rui-plugin-xiaoai",
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    catalog = PlugCatalog(tmp_path)
    entry = catalog.get("xiaoai-lx06")
    assert entry is not None
    assert entry.source == "builtin"
    resolved = catalog.resolve(adapter="xiaoai")
    assert resolved is not None
    assert resolved.source == "builtin"
    assert resolved.device.id == "xiaoai"


def test_reference_adapter_builder_returns_the_core_adapter(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(PLUGIN))
    module = importlib.import_module("rui_plugin_xiaoai.adapter")

    # A DeviceConfig-shaped spec is enough: the wrapper builds the runtime config
    # from LX06 defaults when no real Config is supplied.
    adapter = module.build(
        SimpleNamespace(
            id="xiaoai",
            adapter="xiaoai",
            host="127.0.0.1",
            room="lab",
            params={"host": "127.0.0.1"},
        )
    )

    assert isinstance(adapter, XiaoaiAdapter)
    assert DeviceAdapter in type(adapter).__mro__
    # The kit-only hint is an annotation, not LX06 behaviour.
    assert adapter.kit_live_device_required is True

    info = asyncio.run(adapter.probe())
    assert info.id == "xiaoai"
    assert info.adapter == "xiaoai"
    assert info.transport is Transport.INBOUND
    assert info.channels == 8
    assert info.sample_rate == 48000
    assert info.mic_channels == 6
    assert info.ref_channels == 2
    assert {
        Capability.MIC_ARRAY,
        Capability.REFERENCE,
        Capability.PLAYBACK,
        Capability.LED,
    } <= info.capabilities


def test_reference_adapter_exposes_the_volume_contract(monkeypatch) -> None:
    from rui.device.contract import (
        DEVICE_VOLUME_MAX,
        device_volume_to_percent,
        percent_to_device_volume,
    )

    monkeypatch.syspath_prepend(str(PLUGIN))
    module = importlib.import_module("rui_plugin_xiaoai.adapter")
    adapter = module.build(SimpleNamespace(id="xiaoai", adapter="xiaoai",
                                           host="127.0.0.1", params={}))

    # Both boundaries exist: the adapter surface is percent, the low-level services
    # object is the 0-255 mixer the 0.3 plane actually calls.
    assert callable(adapter.set_volume)
    assert callable(adapter.read_volume)
    assert callable(adapter.services.set_volume)
    assert callable(adapter.services.read_volume)
    assert percent_to_device_volume(0) == 0
    assert percent_to_device_volume(100) == DEVICE_VOLUME_MAX
    assert device_volume_to_percent(DEVICE_VOLUME_MAX) == 100
    for bad in (101, -1, True):
        try:
            percent_to_device_volume(bad)
        except ValueError:
            continue
        raise AssertionError(f"percent_to_device_volume({bad!r}) must be rejected")


def test_reference_adapter_maps_percent_volume_to_the_mixer(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(PLUGIN))
    module = importlib.import_module("rui_plugin_xiaoai.adapter")
    adapter = module.build(SimpleNamespace(id="xiaoai", adapter="xiaoai",
                                           host="127.0.0.1", params={}))
    seen: list[int] = []

    class FakeServices:
        async def set_volume(self, volume: int) -> None:
            seen.append(volume)

        async def read_volume(self) -> int | None:
            return seen[-1] if seen else None

    adapter.services = FakeServices()
    asyncio.run(adapter.set_volume(100))
    asyncio.run(adapter.set_volume(50))
    asyncio.run(adapter.set_volume(0))
    assert seen == [255, 128, 0]
    assert asyncio.run(adapter.read_volume()) == 0


def test_reference_plugin_passes_the_contract_kit(capsys) -> None:
    code = main(["plug-test", str(PLUGIN), "--adapter", ADAPTER, "--json"])
    output = capsys.readouterr().out
    report = json.loads(output)

    assert code == 0, output
    assert report["summary"]["fail"] == 0
    checks = {check["id"]: check for check in report["checks"]}

    for check_id in (
        "manifest.load",
        "manifest.capabilities",
        "manifest.wake",
        "manifest.runtime",
        "adapter.import",
        "adapter.construct",
        "adapter.methods",
        "device.ready",
        "device.probe_layout",
        "poll.drain_signals",
        "poll.drain_wakes",
        "lifecycle.close",
        "lifecycle.after_close",
    ):
        assert checks[check_id]["status"] == "PASS", checks[check_id]

    # Documented host-free skips: inbound LX06 needs its client, and v0 means RUI KWS.
    for check_id in (
        "device.open",
        "capture.frames",
        "capture.cadence",
        "playback.sink",
        "wake.inject",
    ):
        assert checks[check_id]["status"] == "SKIP", checks[check_id]
    assert "open-xiaoai client" in checks["device.open"]["detail"]
