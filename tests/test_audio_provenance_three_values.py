"""task-25 (P0 R4): the device page shows manifest / global / device-reported side by side.

The production P0 was a mismatch between what the model manifest declared, what the
global template said, and what the device actually sent. Task-26 exposed the first two
layers plus conflicts; task-25 adds the third (the probe's capture wire) and pins the
source contract of ``GET /devices/{id}`` and ``GET /devices/{id}/audio``:

* every path in ``sources`` has a matching ``layers[path]`` carrying
  ``effective/source/core/manifest/global/global_explicit/device/reported/
  reported_source`` (a page can render three values from one endpoint);
* ``reported`` is the probe's value, null for playback (DeviceInfo has no playback
  wire) and for an outbound adapter;
* a device-reported value that disagrees with the effective value appears in
  ``reported_conflicts`` (visible, not silent), while manifest-vs-global stays in
  ``conflicts``.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from _plugin_compat import fixture_compat_range
from fastapi.testclient import TestClient

from rui.config import Config, DeviceConfig, save_config
from rui.config.schema import CaptureConfig
from rui.device.contract import Capability, DeviceInfo, Transport
from rui.device.registry import AdapterSet
from rui.plugins import builtin
from rui.plugins.resolve import clear_adapter_plugins
from rui.web.api import app as app_module
from rui.web.api.app import create_app

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}
CAPTURE = 16000
PLAYBACK = 48000


def _install_plug(tmp_path: Path) -> None:
    plug = tmp_path / "data" / "plugs" / "dot2-echomuse"
    (plug / "devices").mkdir(parents=True)
    (plug / "plugin.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-plugin/v0
            id: dot2-echomuse
            name: "EchoMuse"
            version: 1.3.2
            rui_compat: "{fixture_compat_range()}"
            license: MIT
            maintainer: "rui tests"
            trust: trusted
            devices:
              - devices/rs03qr.yaml
            """
        ),
        encoding="utf-8",
    )
    (plug / "devices" / "rs03qr.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-device/v1
            id: dot2
            model: "RS03QR"
            name: "Echo Dot 2"
            vendor: "Amazon / EchoMuse"
            class: B
            transport: inbound
            protocol: {{kind: wss, version: "1"}}
            capabilities: [playback, led, volume]
            audio_defaults:
              capture: {{rate: {CAPTURE}, channels: 1, mic_channels: 1,
                         ref_channels: 0, bits: 16}}
              playback: {{rate: {PLAYBACK}, channels: 1}}
              ack_echo_ignore_ms: 2500
            runtime: {{mode: in-process, entrypoint: "unused:build", listen_port: 8870,
                       token_env: DOT2_EM_TOKEN}}
            params: []
            """
        ),
        encoding="utf-8",
    )


class ProbeAdapter:
    """The bridge's contract surface, reduced to ``probe()``."""

    def __init__(self) -> None:
        self.info = DeviceInfo(
            id="dot2",
            adapter="dot2",
            model="Echo Dot 2 (RS03QR)",
            host="192.0.2.10",
            capabilities=frozenset({Capability.PLAYBACK}),
            transport=Transport.INBOUND,
            sample_rate=CAPTURE,
            channels=4,
            mic_channels=4,
            ref_channels=0,
        )

    async def probe(self) -> DeviceInfo:
        return self.info


@pytest.fixture()
def three_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clear_adapter_plugins()
    builtin._CACHE.clear()
    monkeypatch.delenv("RUI_ADAPTER_PLUGINS", raising=False)
    _install_plug(tmp_path)
    cfg = Config(
        devices=[DeviceConfig(
            id="dot2",
            adapter="dot2",
            host="192.0.2.10",
            room="儿童房",
            params={"plug_id": "dot2-echomuse"},
        )],
        web={"auth_token": TOKEN},
    )
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    cfg.audio.aec.enabled = False
    # Explicit global wire: the manifest owns the layout, but capture.rate is the
    # documented negotiated exception. This is exactly the production split.
    cfg.audio.capture = CaptureConfig(
        rate=PLAYBACK, channels=2, mic_channels=2, ref_channels=0)
    path = tmp_path / "config.yaml"
    save_config(path, cfg)

    adapter = ProbeAdapter()
    monkeypatch.setattr(
        app_module, "build_adapters",
        lambda _cfg: AdapterSet(adapters={"dot2": adapter}),
    )
    client = TestClient(create_app(cfg, config_path=path))
    try:
        yield client
    finally:
        client.close()
        clear_adapter_plugins()
        builtin._CACHE.clear()


def test_audio_layers_carry_manifest_global_and_device_reported(
    three_values: TestClient,
) -> None:
    body = three_values.get("/api/v2/devices/dot2", headers=AUTH).json()
    audio = body["audio"]

    # (1) The model manifest declares 1 channel; the global template says 2; the
    # device probe reports 4. The page has all three values, and the manifest wins
    # the effective value (0.8.0 hardware-facts rule).
    channels = audio["layers"]["capture.channels"]
    assert channels["manifest"] == 1
    assert channels["global"] == 2
    assert channels["global_explicit"] is True
    assert channels["reported"] == 4
    assert channels["reported_source"].startswith("probe (dot2 ")
    assert channels["source"] == "device-manifest"
    assert audio["sources"]["capture.channels"] == "device-manifest"
    assert audio["effective"]["capture"]["channels"] == 1

    # (2) capture.rate is the negotiated exception: the explicit global 48 kHz is
    # effective, the manifest still shows its native 16 kHz, and the probe reports
    # 16 kHz - all three visible.
    rate = audio["layers"]["capture.rate"]
    assert (rate["manifest"], rate["global"], rate["reported"]) == (CAPTURE, PLAYBACK, CAPTURE)
    assert rate["source"] == "global-config"
    assert audio["effective"]["capture"]["rate"] == PLAYBACK

    # (3) Playback has no probe wire (DeviceInfo describes capture only): reported is
    # null, never invented from the manifest.
    playback = audio["layers"]["playback.rate"]
    assert playback["manifest"] == PLAYBACK
    assert playback["reported"] is None
    assert playback["reported_source"] == ""

    # (4) The whole probe wire is exposed once, with its source.
    assert audio["device_reported"] == {
        "rate": CAPTURE,
        "channels": 4,
        "mic_channels": 4,
        "ref_channels": 0,
        "source": audio["layers"]["capture.channels"]["reported_source"],
    }


def test_conflicts_are_highlightable_and_reported_conflicts_are_separate(
    three_values: TestClient,
) -> None:
    audio = three_values.get("/api/v2/devices/dot2", headers=AUTH).json()["audio"]

    # Manifest-vs-global conflicts stay in `conflicts` (task-26 shape).
    manifest_conflicts = {item["path"]: item for item in audio["conflicts"]}
    assert manifest_conflicts["capture.channels"]["global"] == 2
    assert manifest_conflicts["capture.channels"]["manifest"] == 1
    assert manifest_conflicts["capture.rate"]["kind"] == "negotiated"

    # Device-reported disagreements are a separate, equally visible list.
    reported = {item["path"]: item for item in audio["reported_conflicts"]}
    assert set(reported) == {"capture.rate", "capture.channels", "capture.mic_channels"}
    assert reported["capture.channels"]["effective"] == 1
    assert reported["capture.channels"]["reported"] == 4
    assert "device-reported 4" in reported["capture.channels"]["message"]

    # The audio tab gets the same evidence from the standalone endpoint (no fallback).
    standalone = three_values.get("/api/v2/devices/dot2/audio", headers=AUTH).json()
    assert standalone["device_reported"] == audio["device_reported"]
    assert standalone["reported_conflicts"] == audio["reported_conflicts"]


def test_every_source_row_has_the_full_layer_contract(three_values: TestClient) -> None:
    audio = three_values.get("/api/v2/devices/dot2", headers=AUTH).json()["audio"]
    required = {
        "effective", "source", "core", "manifest", "global", "global_explicit",
        "device", "reported", "reported_source",
    }
    assert audio["sources"], "the endpoint must expose at least one field"
    for path, source in audio["sources"].items():
        layer = audio["layers"][path]
        assert required <= set(layer), path
        assert layer["source"] == source, path
        assert layer["effective"] == (
            audio["effective"]["ack_echo_ignore_ms"]
            if path == "ack.echo_ignore_ms"
            else audio["effective"][path.split(".", 1)[0]][path.split(".", 1)[1]]
        ), path
    # capture.rate's layer shows the global winner even though the manifest states
    # another value; playback.rate's layer shows the manifest winner.
    assert audio["layers"]["capture.rate"]["source"] == "global-config"
    assert audio["layers"]["playback.rate"]["source"] == "device-manifest"


def test_an_outbound_adapter_has_no_device_reported_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_plug(tmp_path)
    cfg = Config(
        devices=[DeviceConfig(
            id="dot2",
            adapter="dot2",
            host="192.0.2.10",
            room="儿童房",
            params={"plug_id": "dot2-echomuse"},
        )],
        web={"auth_token": TOKEN},
    )
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)

    class OutboundProbe(ProbeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.info = DeviceInfo(
                id="dot2", adapter="dot2", model="Echo Dot 2",
                host="192.0.2.10", transport=Transport.OUTBOUND,
            )

    adapter = OutboundProbe()
    monkeypatch.setattr(
        app_module, "build_adapters",
        lambda _cfg: AdapterSet(adapters={"dot2": adapter}),
    )
    client = TestClient(create_app(cfg, config_path=path))
    try:
        audio = client.get("/api/v2/devices/dot2", headers=AUTH).json()["audio"]
    finally:
        client.close()
    assert audio["device_reported"] is None
    assert audio["reported_conflicts"] == []
    assert all(layer["reported"] is None for layer in audio["layers"].values())
