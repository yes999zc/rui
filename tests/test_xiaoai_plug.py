"""0.6 M2: the LX06 plug ships in plugs/xiaoai/ and the image loads it by manifest.

These tests pin the shape the image relies on: the directory manifest loads through
the same loader as a downstream plug, the catalog finds it without an in-code table,
the pre-0.6 adapter spelling still resolves (zero migration), and the contract kit
still smoke-tests the adapter host-free.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import textwrap
from pathlib import Path
from types import SimpleNamespace

from xiaoai_plug.adapter import XiaoaiAdapter, build

from rui.__main__ import main
from rui.adapter_aliases import ADAPTER_ALIASES, canonical_adapter
from rui.config import load_config
from rui.config.schema import CaptureConfig, VoiceConfig
from rui.device import build_adapters
from rui.device.contract import Capability, DeviceAdapter, Transport
from rui.plugins import (
    DeviceClass,
    PlugCatalog,
    RuntimeMode,
    TrustLevel,
    WakeSource,
    builtin_plugin,
    builtin_plugins,
    load_plugin,
)

REPO = Path(__file__).resolve().parents[1]
PLUG = REPO / "plugs" / "xiaoai"
ADAPTER = "xiaoai_plug.adapter:build"


def test_the_image_plug_is_a_v1_manifest_with_audio_defaults() -> None:
    plugin = load_plugin(PLUG)

    assert plugin.id == "xiaoai-lx06"
    assert plugin.trust is TrustLevel.BUILTIN
    assert plugin.rui_compat == ">=0.3.0 <0.8.0"
    assert plugin.source_dir == PLUG.resolve()
    assert len(plugin.devices) == 1

    device = plugin.devices[0]
    # The canonical adapter name is the manifest device id; the old spelling is an
    # alias in core for one minor (see test_the_old_adapter_name_is_a_one_minor_alias).
    assert device.id == "lx06"
    assert device.schema == "rui-device/v1"
    assert device.device_class is DeviceClass.A
    assert device.transport is Transport.INBOUND
    assert device.protocol.kind == "ws"
    # LX06 stays on RUI KWS: omitting wake means source: rui and no device_wake.
    assert device.wake is None
    assert device.wake_source is WakeSource.RUI
    assert Capability.DEVICE_WAKE not in device.capabilities
    assert Capability.BARGE_VIA_DEVICE not in device.capabilities
    defaults = device.audio_defaults
    assert defaults is not None and defaults.capture is not None
    assert (defaults.capture.rate, defaults.capture.channels) == (48000, 8)
    assert (defaults.capture.mic_channels, defaults.capture.ref_channels) == (6, 2)
    assert (defaults.capture.bits, defaults.capture.mic_shift,
            defaults.capture.ref_shift) == (32, 12, 16)
    assert defaults.ack_echo_ignore_ms == 1200
    assert device.runtime is not None
    assert device.runtime.mode is RuntimeMode.IN_PROCESS
    assert device.runtime.entrypoint == ADAPTER
    assert device.runtime.listen_port == 4412
    assert [param.key for param in device.params] == ["host", "room", "wake_words"]
    assert device.device_side is not None and device.device_side.install_steps


def test_the_catalog_finds_the_image_plug_without_an_in_code_table(tmp_path: Path) -> None:
    """No src/rui/plugins/builtin.py literal: the catalog is the plug directory."""
    assert builtin_plugin("xiaoai-lx06") is not None
    ids = {plugin.id for plugin in builtin_plugins()}
    assert "xiaoai-lx06" in ids

    catalog = PlugCatalog(tmp_path)
    entry = catalog.get("xiaoai-lx06")
    assert entry is not None and entry.source == "builtin"
    resolved = catalog.resolve(adapter="lx06")
    assert resolved is not None
    assert resolved.device.id == "lx06"
    assert resolved.device.audio_defaults is not None


def test_the_old_adapter_name_is_a_one_minor_alias(tmp_path: Path) -> None:
    assert ADAPTER_ALIASES == {"xiaoai": "lx06"}
    assert canonical_adapter("xiaoai") == "lx06"
    assert canonical_adapter("lx06") == "lx06"
    resolved = PlugCatalog(tmp_path).resolve(adapter="xiaoai")
    assert resolved is not None
    assert resolved.device.id == "lx06"


def test_an_old_config_builds_the_same_adapter_without_editing(tmp_path: Path) -> None:
    """Zero migration: a 0.5-shaped config still selects the LX06 adapter."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "version: 2\n"
        "devices:\n"
        "  - id: living\n"
        "    adapter: xiaoai\n"
        "    host: 192.0.2.10\n"
        "    room: 客厅\n"
        "    listen_port: 4412\n"
        "    token_env: RUI_TEST_DEVICE_TOKEN\n",
        encoding="utf-8",
    )
    cfg = load_config(config_path)

    built = build_adapters(cfg)

    assert built.errors == {}
    adapter = built.adapters["living"]
    assert isinstance(adapter, XiaoaiAdapter)
    # The manifest still supplies the hardware layout through the alias.
    info = asyncio.run(adapter.probe())
    assert info.id == "living"
    assert info.adapter == "xiaoai"
    assert (info.channels, info.mic_channels, info.ref_channels) == (8, 6, 2)
    assert (info.sample_rate, info.transport) == (48000, Transport.INBOUND)


def test_the_adapter_builder_keeps_the_kit_hint_and_layout() -> None:
    adapter = build(
        SimpleNamespace(
            id="lx06",
            adapter="lx06",
            host="127.0.0.1",
            room="lab",
            params={"host": "127.0.0.1"},
        )
    )

    assert isinstance(adapter, XiaoaiAdapter)
    assert DeviceAdapter in type(adapter).__mro__
    assert adapter.kit_live_device_required is True
    info = asyncio.run(adapter.probe())
    assert info.id == "lx06"
    assert info.adapter == "lx06"
    assert (info.channels, info.mic_channels, info.ref_channels) == (8, 6, 2)
    assert {
        Capability.MIC_ARRAY,
        Capability.REFERENCE,
        Capability.PLAYBACK,
        Capability.LED,
    } <= info.capabilities


def test_the_adapter_maps_volume_to_the_device_services(monkeypatch) -> None:
    adapter = XiaoaiAdapter(
        SimpleNamespace(
            id="lx06",
            adapter="lx06",
            host="127.0.0.1",
            room="",
            params={},
            token_env="RUI_DEVICE_TOKEN",
            listen_port=None,
            allow_no_token=False,
            led=SimpleNamespace(
                colors={}, gain=1.9, night_factor=0.6, night_start=0,
                night_end=7, fade_ms=180, enabled=False,
            ),
        ),
        CaptureConfig(
            rate=48000, channels=8, mic_channels=6, ref_channels=2,
            bits=32, mic_shift=12, ref_shift=16,
        ),
        VoiceConfig(),
    )
    seen: list[int] = []

    class FakeServices:
        async def set_volume(self, volume: int) -> None:
            seen.append(volume)

        async def read_volume(self) -> int | None:
            return seen[-1] if seen else None

    adapter.services = FakeServices()
    asyncio.run(adapter.set_volume(100))
    asyncio.run(adapter.set_volume(0))
    assert seen == [100, 0]


def test_the_plug_passes_the_contract_kit(capsys) -> None:
    code = main(["plug-test", str(PLUG), "--adapter", ADAPTER, "--json"])
    output = capsys.readouterr().out
    report = json.loads(output)

    assert code == 0, output
    assert report["summary"]["fail"] == 0
    assert report["summary"]["skip"] == 5
    checks = {check["id"]: check for check in report["checks"]}
    for check_id in (
        "manifest.load",
        "manifest.compatibility",
        "manifest.capabilities",
        "manifest.wake",
        "manifest.runtime",
        "adapter.import",
        "adapter.construct",
        "adapter.methods",
        "device.ready",
        "poll.drain_signals",
        "poll.drain_wakes",
        "lifecycle.close",
        "lifecycle.after_close",
    ):
        assert checks[check_id]["status"] == "PASS", checks[check_id]
    for check_id in (
        "device.open",
        "capture.frames",
        "capture.cadence",
        "playback.sink",
        "wake.inject",
    ):
        assert checks[check_id]["status"] == "SKIP", checks[check_id]
    assert "open-xiaoai client" in checks["device.open"]["detail"]


def test_two_image_plugs_are_listed_side_by_side(tmp_path: Path, monkeypatch) -> None:
    """The image root is a container: the LX06 plus a downstream plug both appear.

    A downstream image (e.g. rui-dot2-bridge) copies its package under
    /opt/rui-plugins next to /opt/rui-plugins/xiaoai. The catalog must list both,
    and each keeps its own device id; the alias only applies to the LX06 name.
    """
    image = tmp_path / "image"
    shutil.copytree(PLUG, image / "xiaoai", ignore=shutil.ignore_patterns("__pycache__"))
    second = image / "rui_dot2_bridge"
    (second / "devices").mkdir(parents=True)
    (second / "plugin.yaml").write_text(
        textwrap.dedent(
            """
            schema: rui-plugin/v0
            id: dot2-echomuse
            name: "Echo Dot 2 (EchoMuse) plug"
            version: 1.2.0
            rui_compat: ">=0.3.0 <0.8.0"
            license: MIT
            maintainer: "Test"
            trust: trusted
            devices:
              - devices/rs03qr.yaml
            """
        ),
        encoding="utf-8",
    )
    (second / "devices" / "rs03qr.yaml").write_text(
        textwrap.dedent(
            """
            schema: rui-device/v1
            id: dot2
            name: "Echo Dot 2 (EchoMuse firmware)"
            vendor: "Amazon / EchoMuse"
            class: B
            transport: inbound
            protocol: {kind: wss, version: "1"}
            capabilities: [playback, led, volume, keys, device_wake, barge_via_device]
            wake: {source: device, barge_via_device: true}
            runtime: {mode: in-process, entrypoint: "rui_dot2_bridge.adapter:build"}
            params: []
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RUI_PLUGINS_DIR", str(image))

    ids = {plugin.id for plugin in builtin_plugins()}
    assert ids == {"xiaoai-lx06", "dot2-echomuse"}

    catalog = PlugCatalog(tmp_path)
    assert catalog.get("xiaoai-lx06") is not None
    dot2 = catalog.get("dot2-echomuse")
    assert dot2 is not None and dot2.source == "builtin"
    assert catalog.resolve(adapter="dot2").device.id == "dot2"
    # The one-minor alias is LX06-specific; it must not rename the Dot 2 plug.
    assert catalog.resolve(adapter="xiaoai").device.id == "lx06"

