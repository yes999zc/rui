"""0.8.0 P0: hardware facts belong to the model manifest (task-26).

The production failure this pins: the global ``audio`` block was written for the
reference LX06 (16 kHz capture, 8 channels, 16 kHz playback, 1200 ms cue window), and
it silently replaced the Dot 2's manifest facts. A newly added Dot 2 then asked the
bridge for 16 kHz playback (the bridge requires 48 kHz and raised on every block) and
used the reference speaker's cue window instead of its own 2500 ms tail.

Resolution matrix under test:

* a model manifest's hardware facts win over an explicit global value, and the
  disagreement is reported (``audio.capture.channels: global 8 vs model 1; using 1``);
* ``capture.rate`` is negotiated: the explicit wire wins and the client resamples
  (the living LX06 must keep asking for 16 kHz);
* ``devices[].audio`` still wins over everything (the permanent dot2-b declaration);
* a missing/unloadable manifest is visible, never a silent core default;
* a co-located manifest is in the catalog before the plane's first effective read
  (the cold-start path that used to need a restart: ack 1200 -> 2500).
"""
from __future__ import annotations

import logging
import sys
import textwrap
from pathlib import Path

import pytest

from rui.config import Config
from rui.config.schema import (
    AckConfig,
    AecConfig,
    AudioConfig,
    CaptureConfig,
    DeviceConfig,
    PlaybackConfig,
    device_audio_conflicts,
    device_audio_defaults_info,
    effective_device_ack_echo_ignore_ms,
    effective_device_audio,
)
from rui.device import loader as adapter_loader
from rui.device import registry
from rui.plugins.resolve import clear_adapter_plugins, resolve_device_plug

LX06_ID = "xiaoai-lx06"

GLOBAL_TEMPLATE = """\
version: 3
system: {data_dir: "%(data_dir)s"}
audio:
  capture:
    rate: 16000
    channels: 8
    mic_channels: 6
    ref_channels: 2
    bits: 32
    mic_shift: 12
    ref_shift: 16
  playback:
    rate: 16000
    channels: 1
ack:
  echo_ignore_ms: 1200
devices:
%(devices)s
"""

DOT2_MANIFEST = """\
schema: rui-device/v1
id: dot2
model: "RS03QR"
name: "Echo Dot 2"
vendor: "Amazon / EchoMuse"
class: B
transport: inbound
protocol: {kind: wss, version: "1"}
capabilities: [playback, led, volume]
audio_defaults:
  capture: {rate: 16000, channels: 1, mic_channels: 1, ref_channels: 0, bits: 16}
  playback: {rate: 48000, channels: 1}
  ack_echo_ignore_ms: 2500
runtime: {mode: in-process, entrypoint: "rui_test_colocated_adapter:build", listen_port: 8870, token_env: DOT2_EM_TOKEN}
params: []
"""

# 0.8.1: the model declares its acoustic timing as well. The global template keeps
# the reference speaker's 2500/2500 on purpose, so the dot2 test proves the manifest
# wins and the lx06 test proves a model that declares nothing is untouched.
DOT2_VOICE_MANIFEST = DOT2_MANIFEST.replace(
    "  playback: {rate: 48000, channels: 1}\n",
    "  playback: {rate: 48000, channels: 1}\n"
    "  voice: {followup_delay_ms: 300, post_playback_mute_ms: 500}\n",
)
VOICE_GLOBAL_TEMPLATE = GLOBAL_TEMPLATE.replace(
    "audio:\n  capture:\n",
    "audio:\n"
    "  voice:\n"
    "    followup_delay_ms: 2500\n"
    "    post_playback_mute_ms: 2500\n"
    "  capture:\n",
)


def _install_plug(root: Path, *, plug_id: str = "dot2-echomuse",
                  manifest: str = DOT2_MANIFEST) -> Path:
    plug = root / "data" / "plugs" / plug_id
    (plug / "devices").mkdir(parents=True)
    (plug / "plugin.yaml").write_text(textwrap.dedent(f"""
        schema: rui-plugin/v0
        id: {plug_id}
        name: "EchoMuse"
        version: 1.3.2
        rui_compat: ">=0.7.0 <0.9.0"
        license: MIT
        maintainer: "Test <test@example.invalid>"
        trust: trusted
        devices:
          - devices/rs03qr.yaml
    """), encoding="utf-8")
    (plug / "devices" / "rs03qr.yaml").write_text(manifest, encoding="utf-8")
    return plug


def _device(device_id: str, *, adapter: str, plug_id: str, room: str = "",
            audio: dict | None = None) -> str:
    lines = [
        f"  - id: {device_id}",
        f"    adapter: {adapter}",
        "    host: 192.0.2.10",
        f"    room: {room or device_id}",
        f"    params: {{plug_id: {plug_id}}}",
    ]
    if audio is not None:
        import json
        lines.append(f"    audio: {json.dumps(audio)}")
    return "\n".join(lines) + "\n"


def _load(tmp_path: Path, devices: list[str], *, template: str = GLOBAL_TEMPLATE) -> Config:
    text = template % {"data_dir": tmp_path / "data", "devices": "".join(devices)}
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    from rui.config import load_config

    return load_config(path)


@pytest.fixture(autouse=True)
def _clean_adapter_catalog():
    """Keep the process-wide adapter/catalog caches out of neighbouring tests."""
    clear_adapter_plugins()
    builders = dict(registry._BUILDERS)
    defaults = dict(registry.RUNTIME_DEFAULTS)
    yield
    clear_adapter_plugins()
    registry._BUILDERS.clear()
    registry._BUILDERS.update(builders)
    registry.RUNTIME_DEFAULTS.clear()
    registry.RUNTIME_DEFAULTS.update(defaults)


# --------------------------------------------------------------- resolution matrix

def test_new_dot2_with_empty_audio_gets_the_models_hardware_facts(tmp_path: Path) -> None:
    _install_plug(tmp_path)
    cfg = _load(tmp_path, [_device("dot2", adapter="dot2", plug_id="dot2-echomuse")])
    device = cfg.devices[0]

    defaults = device_audio_defaults_info(device, cfg.system.data_dir)
    assert defaults.resolved is True
    assert defaults.plug_id == "dot2-echomuse"
    assert defaults.model_id == "dot2"

    eff = effective_device_audio(cfg, device)
    # The manifest, not the reference-speaker template: 48 kHz playback / mono 16 kHz
    # capture. The old order produced 16 kHz playback here and the bridge raised.
    assert (eff.playback.rate, eff.playback.channels) == (48000, 1)
    assert (eff.capture.rate, eff.capture.channels) == (16000, 1)
    assert (eff.capture.mic_channels, eff.capture.ref_channels) == (1, 0)
    assert (eff.capture.bits, eff.capture.mic_shift, eff.capture.ref_shift) == (16, 0, 0)
    assert effective_device_ack_echo_ignore_ms(cfg, device) == 2500

    conflicts = {item.path: item for item in device_audio_conflicts(cfg, device)}
    assert set(conflicts) == {
        "capture.channels", "capture.mic_channels", "capture.ref_channels",
        "capture.bits", "capture.mic_shift", "capture.ref_shift",
        "playback.rate", "ack.echo_ignore_ms",
    }
    assert conflicts["playback.rate"].winner == "device-manifest"
    assert conflicts["playback.rate"].global_value == 16000
    assert conflicts["playback.rate"].manifest_value == 48000
    assert conflicts["playback.rate"].effective == 48000
    assert "global 16000 vs model 48000; using 48000" in conflicts["playback.rate"].message
    assert conflicts["capture.channels"].winner == "device-manifest"
    assert conflicts["ack.echo_ignore_ms"].winner == "device-manifest"


def test_a_new_dot2_with_empty_audio_gets_300ms_followup_and_500ms_mute(
        tmp_path: Path) -> None:
    """0.8.1: the model's acoustic timing wins over the reference-speaker template.

    Production's global block carries the LX06-tuned 2500/2500. A Dot 2 added with no
    `devices[].audio` used to inherit those numbers, which made a dismissed session
    keep a listening window; the manifest now declares 300/500 and the merge must pick
    it, reporting the disagreement instead of hiding it.
    """
    _install_plug(tmp_path, manifest=DOT2_VOICE_MANIFEST)
    cfg = _load(tmp_path, [_device("dot2", adapter="dot2", plug_id="dot2-echomuse")],
                template=VOICE_GLOBAL_TEMPLATE)
    device = cfg.devices[0]

    eff = effective_device_audio(cfg, device)
    assert (eff.voice.followup_delay_ms, eff.voice.post_playback_mute_ms) == (300, 500)
    # The same model facts still win over a *device* value? No: an explicit device
    # override is the deliberate per-room choice and is covered elsewhere. Here the
    # device has none, so the manifest layer is the winner.
    conflicts = {item.path: item for item in device_audio_conflicts(cfg, device)}
    for path, value in (("voice.followup_delay_ms", 300),
                        ("voice.post_playback_mute_ms", 500)):
        assert conflicts[path].winner == "device-manifest"
        assert conflicts[path].kind == "hardware"
        assert conflicts[path].global_value == 2500
        assert conflicts[path].manifest_value == value
        assert conflicts[path].effective == value


def test_lx06_declares_no_voice_defaults_so_the_global_timing_is_unchanged(
        tmp_path: Path) -> None:
    """The other half of 0.8.1: do not touch lx06/living."""
    cfg = _load(tmp_path, [_device("living", adapter="lx06", plug_id=LX06_ID,
                                   room="客厅")],
                template=VOICE_GLOBAL_TEMPLATE)
    device = cfg.devices[0]

    eff = effective_device_audio(cfg, device)
    assert (eff.voice.followup_delay_ms, eff.voice.post_playback_mute_ms) == (2500, 2500)
    assert "voice.followup_delay_ms" not in {
        item.path for item in device_audio_conflicts(cfg, device)}
    assert "voice.post_playback_mute_ms" not in {
        item.path for item in device_audio_conflicts(cfg, device)}


def test_living_lx06_keeps_16k_8ch_and_its_1200ms_ack(tmp_path: Path) -> None:
    cfg = _load(tmp_path, [_device("living", adapter="lx06", plug_id=LX06_ID,
                                   room="客厅")])
    device = cfg.devices[0]
    eff = effective_device_audio(cfg, device)

    # Production shape: the explicit global wire keeps asking the client for 16 kHz;
    # every other field already equals the manifest, so nothing drifts.
    assert (eff.capture.rate, eff.capture.channels) == (16000, 8)
    assert (eff.capture.mic_channels, eff.capture.ref_channels) == (6, 2)
    assert (eff.capture.bits, eff.capture.mic_shift, eff.capture.ref_shift) == (32, 12, 16)
    assert (eff.playback.rate, eff.playback.channels) == (16000, 1)
    assert effective_device_ack_echo_ignore_ms(cfg, device) == 1200

    conflicts = device_audio_conflicts(cfg, device)
    # The LX06 manifest declares 48 kHz native capture; the production wire is a
    # deliberate 16 kHz request (client resamples), reported as negotiated, not silent.
    assert [item.path for item in conflicts] == ["capture.rate"]
    assert conflicts[0].winner == "global-config"
    assert conflicts[0].kind == "negotiated"
    assert "global 16000 vs model 48000; using 16000" in conflicts[0].message


def test_a_dot2_device_override_still_beats_the_manifest(tmp_path: Path) -> None:
    _install_plug(tmp_path)
    cfg = _load(tmp_path, [_device(
        "dot2-b", adapter="dot2", plug_id="dot2-echomuse", room="次卧",
        audio={
            "playback": {"rate": 48000},
            "capture": {"rate": 16000, "channels": 1, "mic_channels": 1,
                        "ref_channels": 0},
            "voice": {"ack_echo_ignore_ms": 3000},
            "aec": {"enabled": False, "who": "device"},
        },
    )])
    device = cfg.devices[0]
    eff = effective_device_audio(cfg, device)
    assert (eff.playback.rate, eff.capture.channels) == (48000, 1)
    assert eff.aec.who == "device" and eff.aec.enabled is False
    assert effective_device_ack_echo_ignore_ms(cfg, device) == 3000
    # Conflicts describe the global layer vs the model, not the device override; the
    # override is reported separately in `overrides`. The effective value is still the
    # device's, which is what the assertions above pin.
    # Fields this device states cannot conflict (the device value is deliberate); the
    # remaining ones are the global layout leaking into the model's neutral values.
    conflicts = {item.path for item in device_audio_conflicts(cfg, device)}
    assert conflicts == {
        "capture.bits", "capture.mic_shift", "capture.ref_shift",
    }


def test_a_three_device_fleet_resolves_each_room_independently(tmp_path: Path) -> None:
    _install_plug(tmp_path)
    cfg = _load(tmp_path, [
        _device("living", adapter="lx06", plug_id=LX06_ID, room="客厅"),
        _device("dot2", adapter="dot2", plug_id="dot2-echomuse", room="儿童房"),
        _device("dot2-b", adapter="dot2", plug_id="dot2-echomuse", room="次卧",
                audio={"capture": {"rate": 16000, "channels": 1,
                                   "mic_channels": 1, "ref_channels": 0},
                       "voice": {"ack_echo_ignore_ms": 3000}}),
    ])
    by_id = {device.id: device for device in cfg.devices}
    living = effective_device_audio(cfg, by_id["living"])
    dot2 = effective_device_audio(cfg, by_id["dot2"])
    dot2_b = effective_device_audio(cfg, by_id["dot2-b"])

    assert (living.capture.rate, living.capture.channels, living.playback.rate) == (
        16000, 8, 16000)
    assert (dot2.capture.rate, dot2.capture.channels, dot2.playback.rate) == (
        16000, 1, 48000)
    assert (dot2_b.capture.channels, dot2_b.playback.rate) == (1, 48000)
    assert effective_device_ack_echo_ignore_ms(cfg, by_id["living"]) == 1200
    assert effective_device_ack_echo_ignore_ms(cfg, by_id["dot2"]) == 2500
    assert effective_device_ack_echo_ignore_ms(cfg, by_id["dot2-b"]) == 3000
    # Resolution of one room cannot leak into another: the LX06 keeps its negotiated
    # wire while both Dot 2 devices take the mono 48 kHz manifest.
    assert [item.path for item in device_audio_conflicts(cfg, by_id["living"])] == [
        "capture.rate"]
    assert len(device_audio_conflicts(cfg, by_id["dot2"])) == 8
    # The per-device override suppresses the conflicts it answers; the playback wire
    # is not overridden here, so that disagreement stays visible.
    assert {item.path for item in device_audio_conflicts(cfg, by_id["dot2-b"])} == {
        "capture.bits", "capture.mic_shift", "capture.ref_shift", "playback.rate",
    }


def test_a_missing_manifest_is_visible_instead_of_a_silent_core_default(
        tmp_path: Path) -> None:
    cfg = _load(tmp_path, [_device("ghost", adapter="dot2", plug_id="ghost-plug")])
    device = cfg.devices[0]
    info = device_audio_defaults_info(device, cfg.system.data_dir)
    assert info.resolved is False
    assert info.expected is True
    assert "ghost-plug" in info.error
    # The effective values still fall back so a read endpoint keeps rendering, but the
    # caller can say why (and the plane logs it once).
    eff = effective_device_audio(cfg, device)
    assert eff.playback.rate == 16000
    assert effective_device_ack_echo_ignore_ms(cfg, device) == 1200


def test_conflicts_are_logged_once_per_process(caplog, tmp_path: Path) -> None:
    from rui.config import schema as schema_module

    schema_module._LOGGED_CONFLICTS.clear()
    _install_plug(tmp_path)
    cfg = _load(tmp_path, [_device("dot2", adapter="dot2", plug_id="dot2-echomuse")])
    device = cfg.devices[0]
    with caplog.at_level("WARNING", logger="rui.config.schema"):
        device_audio_conflicts(cfg, device)
        device_audio_conflicts(cfg, device)
    messages = [record.getMessage() for record in caplog.records]
    playback = [m for m in messages if m.startswith("audio.playback.rate:")]
    assert len(playback) == 1
    assert "global 16000 vs model 48000; using 48000" in playback[0]


# ------------------------------------------------------------------- cold start

DOT2_ADAPTER_MODULE = """\
def build(dev, cfg):
    return {"device": dev.id}
"""


def _co_located_plug(tmp_path: Path, *, plug_id: str = "dot2-echomuse",
                     model: str = "dot2") -> Path:
    """A downstream plug as an operator installs it: adapter + manifest together."""
    # Same directory as the adapter module: the documented downstream layout.
    root = tmp_path
    (root / "plugin.yaml").write_text(textwrap.dedent(f"""
        schema: rui-plugin/v0
        id: {plug_id}
        name: "EchoMuse"
        version: 1.3.2
        rui_compat: ">=0.7.0 <0.9.0"
        license: MIT
        maintainer: "Test <test@example.invalid>"
        trust: trusted
        devices:
          - devices/rs03qr.yaml
    """), encoding="utf-8")
    (root / "devices").mkdir()
    (root / "devices" / "rs03qr.yaml").write_text(DOT2_MANIFEST, encoding="utf-8")
    (tmp_path / "rui_test_colocated_adapter.py").write_text(
        DOT2_ADAPTER_MODULE, encoding="utf-8")
    # A stale module from an earlier tmp_path would hide this manifest.
    sys.modules.pop("rui_test_colocated_adapter", None)
    return root


def _plane_config(tmp_path: Path, *, adapter: str, plug_id: str) -> Config:
    cfg = Config(devices=[DeviceConfig(
        id="speaker", adapter=adapter, host="127.0.0.1",
        params={"plug_id": plug_id})])
    cfg.system.data_dir = str(tmp_path / "data")
    # Production template shape: every value explicit, playback/ack at the reference
    # speaker's values. Without the manifest this is exactly the broken 16 kHz / 1200.
    cfg.audio = AudioConfig(
        capture=CaptureConfig(rate=16000, channels=8, mic_channels=6,
                              ref_channels=2, bits=32, mic_shift=12, ref_shift=16),
        playback=PlaybackConfig(rate=16000, channels=1),
        aec=AecConfig(),
    )
    cfg.ack = AckConfig(echo_ignore_ms=1200)
    return cfg


def test_a_colocated_manifest_is_visible_to_the_catalog(tmp_path: Path,
                                                       monkeypatch) -> None:
    """The adapter loader gates the manifest; the audio resolver must see it too."""
    _co_located_plug(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS",
                       "dot2=rui_test_colocated_adapter:build")

    report = adapter_loader.load_adapter_plugins()
    assert report.warnings == []
    assert "dot2" in report.registered

    device = DeviceConfig(id="speaker", adapter="dot2", host="127.0.0.1",
                          params={"plug_id": "dot2-echomuse"})
    resolved = resolve_device_plug(device, str(tmp_path / "data"))
    assert resolved is not None, "the co-located manifest backs the loaded adapter"
    assert resolved.source == "adapter"
    assert resolved.device.audio_defaults is not None
    assert resolved.device.audio_defaults.playback.rate == 48000


def test_plane_first_effective_read_already_has_the_manifest(tmp_path: Path,
                                                            monkeypatch) -> None:
    """Cold start: build_plane loads the adapter (and its manifest) first.

    Reproduces the production symptom: `ack=1200` on the first read although the Dot 2
    declares 2500. Before the catalog fix this returned 1200/16000; a restart was the
    only cure because the plane cached the unresolved manifest for its lifetime.
    """
    from rui.audio.process import build_plane

    _co_located_plug(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS",
                       "dot2=rui_test_colocated_adapter:build")
    cfg = _plane_config(tmp_path, adapter="dot2", plug_id="dot2-echomuse")

    plane = build_plane(cfg, "speaker", tmp_path / "audio.sock")
    assert plane.audio_defaults_info.resolved is True
    assert plane.audio_defaults_info.source == "adapter"
    assert plane.ack_echo_ignore_ms == 2500
    assert plane.effective.playback.rate == 48000
    # The negotiated capture wire stays the operator's explicit request.
    assert plane.effective.capture.rate == 16000
    assert plane.effective.capture.channels == 1


def test_plane_heals_when_the_manifest_appears_after_start(tmp_path: Path) -> None:
    """A data_dir mount that lands late must not need a restart to fix ack/playback."""
    from rui.audio.process import build_plane

    cfg = _plane_config(tmp_path, adapter="dot2", plug_id="dot2-echomuse")
    plane = build_plane(cfg, "speaker", tmp_path / "audio.sock")
    # Cold start with the catalog not ready: the reference values are in effect ...
    assert plane.audio_defaults_info.resolved is False
    assert plane.audio_defaults_info.expected is True
    assert plane.ack_echo_ignore_ms == 1200
    assert plane.effective.playback.rate == 16000

    _install_plug(tmp_path)
    assert plane._refresh_audio_defaults(reason="probe") is True
    # ... and the first wake after the probe uses the model's hardware facts.
    assert plane.ack_echo_ignore_ms == 2500
    assert plane.effective.playback.rate == 48000
    assert plane.audio_defaults_info.resolved is True


def test_plane_restart_hint_when_the_layout_changes_after_resolution(
        tmp_path: Path, caplog) -> None:
    """A later resolution that moves the capture layout also needs the restart hint.

    A manifest can first declare only playback/ack facts (no capture layout) and later
    gain its capture block. The plane has already opened its stream by then, so the
    old hint (`only when before is None`) stayed silent on exactly the case that needs
    a restart.
    """
    from rui.audio.process import build_plane

    playback_only = DOT2_MANIFEST.replace(
        "  capture: {rate: 16000, channels: 1, mic_channels: 1, ref_channels: 0,"
        " bits: 16}\n", "")
    _install_plug(tmp_path, manifest=playback_only)
    cfg = _plane_config(tmp_path, adapter="dot2", plug_id="dot2-echomuse")
    plane = build_plane(cfg, "speaker", tmp_path / "audio.sock")
    assert plane.audio_defaults is not None
    assert plane.audio_defaults.capture is None

    # The same plug now declares the capture layout (rewrite: _install_plug is
    # create-only).
    (tmp_path / "data" / "plugs" / "dot2-echomuse" / "devices" / "rs03qr.yaml"
     ).write_text(DOT2_MANIFEST, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert plane._refresh_audio_defaults(reason="probe") is True
    assert plane.effective.capture.channels == 1
    assert "restart the plane if the capture layout changed" in caplog.text


def test_first_ack_read_retries_the_catalog_before_using_the_global_window(
        tmp_path: Path) -> None:
    """The user-visible cold-start assertion: first effective ack is 2500, not 1200."""
    from rui.audio.process import build_plane

    cfg = _plane_config(tmp_path, adapter="dot2", plug_id="dot2-echomuse")
    plane = build_plane(cfg, "speaker", tmp_path / "audio.sock")
    _install_plug(tmp_path)          # the manifest shows up after the plane started

    assert plane.audio_defaults is None
    assert plane.ack_echo_ignore_ms == 2500
    assert plane.audio_defaults_info.resolved is True
    assert plane._audio_refresh_attempts == 1


# --------------------------------------------------------------- v2 API (R1)

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}


def _app(tmp_path: Path, raw: dict):
    from fastapi.testclient import TestClient

    from rui.config import Config as AppConfig
    from rui.config import save_config
    from rui.web.api.app import create_app

    raw.setdefault("web", {"auth_token": TOKEN})
    config = AppConfig.model_validate(raw)
    config.system.data_dir = str(tmp_path / "data")
    config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    path = tmp_path / "config.yaml"
    save_config(path, config)
    return TestClient(create_app(config, config_path=path))


def _raw_device(device_id: str, adapter: str, plug_id: str, room: str,
                *, audio: dict | None = None) -> dict:
    payload = {
        "id": device_id,
        "adapter": adapter,
        "host": "192.0.2.10",
        "room": room,
        "params": {"plug_id": plug_id},
    }
    if audio is not None:
        payload["audio"] = audio
    return payload


def _raw_config(devices: list[dict], **extra) -> dict:
    return {
        "audio": {
            "capture": {"rate": 16000, "channels": 8, "mic_channels": 6,
                        "ref_channels": 2, "bits": 32, "mic_shift": 12,
                        "ref_shift": 16},
            "playback": {"rate": 16000, "channels": 1},
        },
        "ack": {"echo_ignore_ms": 1200},
        "devices": devices,
        **extra,
    }


def test_api_dot2_audio_reports_manifest_effective_values_and_conflicts(
        tmp_path: Path) -> None:
    _install_plug(tmp_path)
    client = _app(tmp_path, _raw_config([
        _raw_device("dot2", "dot2", "dot2-echomuse", "儿童房"),
    ]))
    body = client.get("/api/v2/devices/dot2/audio", headers=AUTH).json()
    assert body["plug_resolved"] is True
    assert body["manifest_error"] == ""
    assert body["effective"]["playback"]["rate"] == 48000
    assert body["effective"]["capture"]["channels"] == 1
    assert body["effective"]["capture"]["rate"] == 16000
    assert body["effective"]["ack_echo_ignore_ms"] == 2500
    # A declared layout neutralizes the fields the manifest omits; provenance must
    # attribute them to the manifest, not to the global template they replaced.
    assert (body["effective"]["capture"]["bits"],
            body["effective"]["capture"]["mic_shift"],
            body["effective"]["capture"]["ref_shift"]) == (16, 0, 0)
    assert body["sources"]["playback.rate"] == "device-manifest"
    assert body["sources"]["capture.channels"] == "device-manifest"
    assert body["sources"]["capture.bits"] == "device-manifest"
    assert body["sources"]["capture.mic_shift"] == "device-manifest"
    assert body["sources"]["capture.ref_shift"] == "device-manifest"
    assert body["sources"]["ack.echo_ignore_ms"] == "device-manifest"
    assert body["layers"]["capture.bits"]["manifest"] == 16
    assert body["layers"]["capture.mic_shift"]["manifest"] == 0
    assert body["layers"]["capture.ref_shift"]["manifest"] == 0
    for path in ("capture.bits", "capture.mic_shift", "capture.ref_shift"):
        assert body["layers"][path]["source"] == "device-manifest", path
    conflicts = {item["path"]: item for item in body["conflicts"]}
    assert set(conflicts) == {
        "capture.channels", "capture.mic_channels", "capture.ref_channels",
        "capture.bits", "capture.mic_shift", "capture.ref_shift",
        "playback.rate", "ack.echo_ignore_ms",
    }
    assert conflicts["playback.rate"]["winner"] == "device-manifest"
    assert conflicts["playback.rate"]["effective"] == 48000
    # sources/layers must agree with the conflict winner for the neutral-filled
    # layout fields (review blocker): the manifest won, the global value did not.
    for path in ("capture.bits", "capture.mic_shift", "capture.ref_shift"):
        assert conflicts[path]["winner"] == "device-manifest", path
        assert body["sources"][path] == "device-manifest", path
    assert "global 16000 vs model 48000; using 48000" in         conflicts["playback.rate"]["message"]

    row = client.get("/api/v2/devices", headers=AUTH).json()["items"][0]
    codes = {warning["code"] for warning in row["warnings"]}
    assert "audio_conflict" in codes


def test_api_layout_without_bits_attributes_neutral_values_to_the_manifest(
        tmp_path: Path) -> None:
    """Review BLOCKER: bits/shifts omitted while the layout is declared.

    The manifest declares channels/mic/ref (so the merge fills the neutral 16-bit,
    zero-shift values) but says nothing about bit depth or shifts. Before the fix the
    payload reported those fields with source `global-config` (the global 32-bit/12/16
    template) while the effective value was neutral and the conflict named the
    manifest winner - the console showed "global won".
    """
    _install_plug(tmp_path, manifest=DOT2_MANIFEST.replace(", bits: 16", ""))
    client = _app(tmp_path, _raw_config([
        _raw_device("dot2", "dot2", "dot2-echomuse", "儿童房"),
    ]))
    body = client.get("/api/v2/devices/dot2/audio", headers=AUTH).json()
    capture = body["effective"]["capture"]
    assert (capture["bits"], capture["mic_shift"], capture["ref_shift"]) == (16, 0, 0)
    for key in ("bits", "mic_shift", "ref_shift"):
        path = f"capture.{key}"
        assert body["sources"][path] == "device-manifest", path
        assert body["layers"][path]["source"] == "device-manifest", path
        assert body["layers"][path]["manifest"] == capture[key], path
    conflicts = {item["path"]: item for item in body["conflicts"]}
    assert conflicts["capture.bits"]["winner"] == "device-manifest"
    assert conflicts["capture.bits"]["effective"] == 16


def test_api_lx06_keeps_the_negotiated_16k_wire(tmp_path: Path) -> None:
    client = _app(tmp_path, _raw_config([
        _raw_device("living", "lx06", LX06_ID, "客厅"),
    ]))
    body = client.get("/api/v2/devices/living/audio", headers=AUTH).json()
    assert body["effective"]["capture"]["rate"] == 16000
    assert body["effective"]["capture"]["channels"] == 8
    assert body["effective"]["playback"]["rate"] == 16000
    assert body["effective"]["ack_echo_ignore_ms"] == 1200
    assert body["sources"]["capture.rate"] == "global-config"
    assert len(body["conflicts"]) == 1
    assert body["conflicts"][0]["kind"] == "negotiated"
    assert body["conflicts"][0]["winner"] == "global-config"


def test_api_unresolved_manifest_is_visible_not_a_silent_default(
        tmp_path: Path) -> None:
    client = _app(tmp_path, _raw_config([
        _raw_device("ghost", "dot2", "ghost-plug", "书房"),
    ]))
    body = client.get("/api/v2/devices/ghost/audio", headers=AUTH).json()
    assert body["plug_resolved"] is False
    assert "ghost-plug" in body["manifest_error"]
    assert body["conflicts"] == []
    # Fields the config never stated cannot pretend to know the model: the UI must
    # show `unresolved`, not a fabricated core default. Stated global values keep
    # their (visible) source.
    assert body["sources"]["voice.rate"] == "unresolved"
    assert body["sources"]["capture.rate"] == "global-config"


def test_create_device_from_the_catalog_seeds_manifest_audio_and_fails_closed(
        tmp_path: Path) -> None:
    _install_plug(tmp_path)
    client = _app(tmp_path, _raw_config([
        _raw_device("living", "lx06", LX06_ID, "客厅"),
    ]))

    response = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "study",
        "room": "书房",
        "plugin_id": "dot2-echomuse",
        "model_id": "dot2",
        "host": "192.0.2.20",
    })
    assert response.status_code == 201, response.text
    device = response.json()["device"]
    # The server picked plug_id/model from the catalog; there is no free-form plug id.
    assert device["params"]["plug_id"] == "dot2-echomuse"
    assert device["model_id"] == "dot2"
    # No devices[].audio override was written: the manifest is what the console shows.
    assert device["audio"]["overrides"] == []
    assert device["audio"]["effective"]["playback"]["rate"] == 48000
    assert device["audio"]["effective"]["capture"]["channels"] == 1
    assert device["audio"]["effective"]["ack_echo_ignore_ms"] == 2500
    assert device["audio"]["sources"]["playback.rate"] == "device-manifest"
    assert device["audio"]["conflicts"]

    # A free plug id is not a field at all ...
    free = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "attic", "room": "阁楼", "plugin_id": "dot2-echomuse",
        "model_id": "dot2", "host": "192.0.2.21", "plug_id": "not-a-plug",
    })
    assert free.status_code == 422
    # ... and an unknown catalog id / model fails closed.
    unknown = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "attic", "room": "阁楼", "plugin_id": "not-a-plug",
        "host": "192.0.2.21",
    })
    assert unknown.status_code == 409
    assert unknown.json()["error"]["code"] == "plug_unavailable"
    bad_model = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "attic", "room": "阁楼", "plugin_id": "dot2-echomuse",
        "model_id": "no-such-model", "host": "192.0.2.21",
    })
    assert bad_model.status_code == 409
    # The model of an existing device is immutable (edit cannot silently re-point it).
    patched = client.patch("/api/v2/devices/study", headers=AUTH,
                           json={"model_id": "dot2"})
    assert patched.status_code == 422


# ------------------------------------------------------- console copy (task-26 follow-up)

ROOT = Path(__file__).resolve().parents[1]


def test_console_negotiated_rate_rule_matches_the_api_layers(tmp_path: Path) -> None:
    """The badge helper is path-scoped: only capture.rate may be 'negotiated'.

    ``playback.rate`` / ``playback.channels`` carry the same
    ``global_explicit``/manifest shape, but the manifest wins for them. If the
    deleted ``globalShadowsManifest(layer)`` rule comes back, these layers would
    render “global overrides the manifest” for a value the manifest actually won.
    """
    _install_plug(tmp_path)
    raw = _raw_config([
        _raw_device("dot2", "dot2", "dot2-echomuse", "儿童房"),
        _raw_device("living", "lx06", LX06_ID, "客厅"),
    ])
    raw["audio"]["playback"]["channels"] = 2   # differ from the Dot 2 manifest's 1
    client = _app(tmp_path, raw)

    def layer(device_id: str, path: str) -> dict:
        body = client.get(f"/api/v2/devices/{device_id}/audio", headers=AUTH).json()
        return body["layers"][path]

    def negotiated(path: str, item: dict) -> bool:
        """Mirror of web/src/v2/audio.ts::isNegotiatedCaptureRate."""
        return (
            path == "capture.rate"
            and item["global_explicit"] is True
            and item["manifest"] is not None
            and item["global"] is not None
            and item["global"] != item["manifest"]
        )

    # negotiated: the LX06 asks the client for 16 kHz while the manifest is 48 kHz.
    lx06_rate = layer("living", "capture.rate")
    assert lx06_rate["source"] == "global-config"
    assert lx06_rate["effective"] == 16000
    assert (lx06_rate["global"], lx06_rate["manifest"]) == (16000, 48000)
    assert negotiated("capture.rate", lx06_rate) is True

    # hardware facts: the Dot 2 manifest wins despite explicit global values.
    dot2_rate = layer("dot2", "playback.rate")
    assert dot2_rate["source"] == "device-manifest"
    assert dot2_rate["effective"] == 48000
    assert (dot2_rate["global"], dot2_rate["manifest"]) == (16000, 48000)
    assert negotiated("playback.rate", dot2_rate) is False

    dot2_channels = layer("dot2", "playback.channels")
    assert dot2_channels["source"] == "device-manifest"
    assert dot2_channels["effective"] == 1
    assert (dot2_channels["global"], dot2_channels["manifest"]) == (2, 1)
    assert negotiated("playback.channels", dot2_channels) is False

    # Equal values are not a negotiation either (Dot 2 capture.rate: 16k in both).
    dot2_capture = layer("dot2", "capture.rate")
    assert (dot2_capture["global"], dot2_capture["manifest"]) == (16000, 16000)
    assert negotiated("capture.rate", dot2_capture) is False


def test_console_copy_has_no_global_shadows_manifest_claim() -> None:
    """Source contract: no layer-only helper, negotiated text, manifest-first hint."""
    audio_source = (ROOT / "web/src/v2/audio.ts").read_text(encoding="utf-8")
    assert "globalShadowsManifest" not in audio_source
    assert "NEGOTIATED_AUDIO_PATH = 'capture.rate'" in audio_source
    # The helper must be path-scoped; a layer-only predicate cannot distinguish
    # capture from playback and is the regression this test exists for.
    start = audio_source.index("export function isNegotiatedCaptureRate(")
    end = audio_source.index("\n}", start)
    helper = audio_source[start:end]
    assert "path !== NEGOTIATED_AUDIO_PATH" in helper
    assert "layer.global_explicit !== true" in helper
    assert "layer.manifest === null" in helper
    assert "layer.global === null" in helper
    assert "layer.global !== layer.manifest" in helper

    panel = (ROOT / "web/src/v2/components/device/AudioPanel.vue").read_text(
        encoding="utf-8")
    assert "isNegotiatedCaptureRate(row.path, row.layer)" in panel
    assert "globalShadowsManifest(" not in panel
    assert "t('v2.audio.globalShadow'" in panel

    i18n = (ROOT / "web/src/v2/i18n.ts").read_text(encoding="utf-8")
    assert "全局配置盖过了插头默认值" not in i18n
    assert "Global config overrides the plug manifest default" not in i18n

    shadows = [line for line in i18n.splitlines()
               if "'v2.audio.globalShadow':" in line]
    assert len(shadows) == 2, "zh-Hans and en must both define the badge copy"
    for line in shadows:
        assert "capture.rate" in line
        assert "{global}" in line and "{manifest}" in line

    hints = [line for line in i18n.splitlines()
             if "'v2.houseAudio.clearHint':" in line]
    assert len(hints) == 2, "zh-Hans and en must both define the clear hint"
    zh_hint, en_hint = hints
    assert zh_hint.index("manifest") < zh_hint.index("核心默认")
    assert en_hint.index("plug manifest") < en_hint.index("core default")


def test_console_exposes_both_per_device_followup_timing_fields() -> None:
    """0.8.1 c: both knobs are editable leaves in the device audio editor.

    The server payload is covered by test_api_v2_write; this is the source contract
    for the table that renders it. `null` (inherited) is already handled by the
    panel's restore/inherit path, so each leaf is a bounded number and the effective
    value column shows the manifest's 300/500 as the inherited default.
    """
    audio_source = (ROOT / "web/src/v2/audio.ts").read_text(encoding="utf-8")
    assert "voice.followup_delay_ms" in audio_source
    assert "voice.post_playback_mute_ms" in audio_source
    for field in ("followup_delay_ms", "post_playback_mute_ms"):
        assert f"leaf('voice.{field}', 'voice', 'number'" in audio_source, field
        assert f"labelKey: 'v2.audio.leaf.{field}'" in audio_source, field
        assert "min: 0" in audio_source and "max: 10000" in audio_source
    i18n = (ROOT / "web/src/v2/i18n.ts").read_text(encoding="utf-8")
    expected = {
        "followup_delay_ms": ("续听窗口", "Follow-up window"),
        "post_playback_mute_ms": ("播放后静音窗口", "Post-playback mute"),
    }
    for field, (zh, en) in expected.items():
        lines = [line for line in i18n.splitlines()
                 if f"'v2.audio.leaf.{field}':" in line]
        assert len(lines) == 2, f"zh-Hans and en must both label voice.{field}"
        assert any(zh in line for line in lines), field
        assert any(en in line for line in lines), field
    panel = (ROOT / "web/src/v2/components/device/AudioPanel.vue").read_text(
        encoding="utf-8")
    # Editing goes through the generic row controls; the inherited value is the
    # placeholder, the manifest layer is displayed underneath, and the two new
    # leaves get a zh/en human label beside their technical path.
    assert "row.editable" in panel
    assert "inheritsCurrently(row)" in panel
    assert "row.layer" in panel
    assert "row.spec?.labelKey" in panel
    assert "t(row.spec.labelKey)" in panel
