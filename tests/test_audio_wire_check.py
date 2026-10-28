"""task-24 (P0 R3): runtime wire-format self-check and visible adapter errors.

The production failure this pins: the Dot 2 was handed PCM at 16 kHz while the bridge
accepts only 48 kHz. Every block raised, the speak loop logged one INFO line per chunk,
and the console still showed the room as healthy. This suite pins:

* before the plane says Ready it compares the device's reported capture wire (probe)
  and the model manifest's playback wire against the effective config;
* a mismatch refuses the plane with both values, their source and a fix, and the refusal
  reaches the control plane as an Error (never a silent 16 kHz speaker);
* a matching Dot 2 config starts and plays at 48 kHz; the LX06's negotiated 16 kHz
  capture wire is not a false positive;
* ``adapter.play`` errors become a counted, circuit-broken plane failure instead of one
  INFO line per chunk;
* the console device status says the configuration does not match the device, carries
  both values/source/fix, and /health exposes the playback error counter.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
import textwrap
import time
from pathlib import Path

import numpy as np

from rui.audio.probe import (
    WIRE_MISMATCH_PREFIX,
    WireFormat,
    check_wire_format,
    is_wire_mismatch,
)
from rui.audio.process import AudioPlane
from rui.audio.protocol import CONTROL, Error, FrameType, read_frame
from rui.config import Config
from rui.config.schema import (
    DeviceAudioOverride,
    DeviceCaptureOverride,
    DeviceConfig,
    DevicePlaybackOverride,
)
from rui.device.contract import Capability, DeviceInfo, Transport

PLAYBACK = 48000
CAPTURE = 16000


# --------------------------------------------------------------------- fixtures

DOT2_MANIFEST = f"""\
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
  capture: {{rate: {CAPTURE}, channels: 1, mic_channels: 1, ref_channels: 0, bits: 16}}
  playback: {{rate: {PLAYBACK}, channels: 1}}
  ack_echo_ignore_ms: 2500
runtime: {{mode: in-process, entrypoint: "unused:build", listen_port: 8870,
           token_env: DOT2_EM_TOKEN}}
params: []
"""


def install_dot2_plug(root: Path, *, manifest: str = DOT2_MANIFEST) -> None:
    plug = root / "data" / "plugs" / "dot2-echomuse"
    (plug / "devices").mkdir(parents=True)
    (plug / "plugin.yaml").write_text(textwrap.dedent("""
        schema: rui-plugin/v0
        id: dot2-echomuse
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


def dot2_config(
    tmp_path: Path, *, playback: int = CAPTURE, capture: int = CAPTURE,
) -> Config:
    """A Dot 2 whose per-device override states playback (the P0 failure shape)."""
    cfg = Config(devices=[DeviceConfig(
        id="dot2",
        adapter="dot2",
        host="192.0.2.10",
        room="儿童房",
        params={"plug_id": "dot2-echomuse"},
        audio=DeviceAudioOverride(
            playback=DevicePlaybackOverride(rate=playback, channels=1),
            capture=DeviceCaptureOverride(rate=capture, channels=1,
                                          mic_channels=1, ref_channels=0),
        ),
    )])
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    cfg.audio.aec.enabled = False
    return cfg


class Dot2WireAdapter:
    """The bridge's contract surface, reduced to what the plane touches."""

    def __init__(self, *, sample_rate: int = CAPTURE, channels: int = 1,
                 error: BaseException | None = None) -> None:
        self.info = DeviceInfo(
            id="dot2", adapter="dot2", model="Echo Dot 2 (RS03QR)",
            host="192.0.2.10", capabilities=frozenset({Capability.PLAYBACK}),
            transport=Transport.INBOUND, channels=channels,
            sample_rate=sample_rate, bit_depth=16,
            mic_channels=1, ref_channels=0,
        )
        self.play_calls: list[tuple[int, int]] = []
        self.error = error
        self.closed = False

    async def probe(self) -> DeviceInfo:
        return self.info

    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        self.play_calls.append((sample_rate, channels))
        if self.error is not None:
            raise self.error

    def health(self) -> dict:
        return {"connected": True}

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


def make_plane(tmp_path: Path, adapter: Dot2WireAdapter, *, playback: int = CAPTURE,
               capture: int = CAPTURE) -> AudioPlane:
    plane = AudioPlane(dot2_config(tmp_path, playback=playback, capture=capture),
                       "dot2", tmp_path / "plane.sock")
    plane._open_adapter = _returning(adapter)  # type: ignore[method-assign]
    return plane


def _returning(adapter):
    async def open_adapter():
        return adapter
    return open_adapter


def pcm(ms: int = 40, rate: int = 16000) -> bytes:
    return np.zeros(rate * ms // 1000, dtype="<i2").tobytes()


def enqueue(plane: AudioPlane, chunk: bytes) -> None:
    plane._speak_queued_s += len(chunk) / 2 / plane.cfg.audio.voice.rate
    plane._speak_finished = False
    plane._speak_queue.put_nowait((chunk, False))


async def wait_until(predicate, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")


# ------------------------------------------------------------ pure format check

def test_playback_mismatch_names_both_values_source_and_fix() -> None:
    configured = WireFormat(rate=CAPTURE, channels=1, source="effective config")
    expected = WireFormat(
        rate=PLAYBACK, channels=1,
        source="model manifest dot2 (dot2-echomuse)",
    )
    check = check_wire_format(
        playback_configured=configured, playback_expected=expected,
    )

    assert check.ok is False
    message = check.as_error()
    assert "playback.rate" in message
    assert "16000" in message and "48000" in message
    assert "model manifest" in message
    assert "fix:" in message
    assert "devices[].audio.playback.rate" in message


def test_capture_layout_mismatch_is_reported_from_the_probe() -> None:
    check = check_wire_format(
        capture_configured=WireFormat(rate=CAPTURE, channels=1,
                                      mic_channels=1, ref_channels=0,
                                      source="effective config"),
        capture_reported=WireFormat(rate=48000, channels=8, mic_channels=6,
                                    ref_channels=2, source="probe (lx06 LX06)"),
    )
    paths = {problem.path for problem in check.problems}
    assert paths == {"capture.rate", "capture.channels", "capture.mic_channels",
                     "capture.ref_channels"}
    assert "probe" in check.as_error()


def test_a_matching_dot2_wire_passes() -> None:
    check = check_wire_format(
        capture_configured=WireFormat(rate=CAPTURE, channels=1,
                                      mic_channels=1, ref_channels=0),
        capture_reported=WireFormat(rate=CAPTURE, channels=1,
                                    mic_channels=1, ref_channels=0),
        playback_configured=WireFormat(rate=PLAYBACK, channels=1),
        playback_expected=WireFormat(rate=PLAYBACK, channels=1,
                                     source="model manifest dot2"),
    )
    assert check.ok
    assert check.as_error() == ""


def test_the_mismatch_code_is_matched_inside_supervisor_prefixes() -> None:
    detail = "playback.rate: configured 16000, device 48000"
    for message in (
        f"{WIRE_MISMATCH_PREFIX}{detail}",
        f"audio plane refused: {WIRE_MISMATCH_PREFIX}{detail}",
        f"audio plane degraded: {WIRE_MISMATCH_PREFIX}{detail}",
        f"AudioPlaneError: audio plane refused: {WIRE_MISMATCH_PREFIX}{detail}",
    ):
        assert is_wire_mismatch(message), message
    assert not is_wire_mismatch("audio plane reader stopped: IncompleteReadError")
    assert not is_wire_mismatch("")
    assert not is_wire_mismatch(None)


def test_the_lx06_negotiated_16k_capture_wire_is_not_a_mismatch() -> None:
    # The LX06 adapter builds its probe from the effective capture config, so its
    # native 48 kHz manifest entry is not the wire the plane asks for.
    check = check_wire_format(
        capture_configured=WireFormat(rate=16000, channels=8,
                                      mic_channels=6, ref_channels=2),
        capture_reported=WireFormat(rate=16000, channels=8,
                                    mic_channels=6, ref_channels=2),
        playback_configured=WireFormat(rate=16000, channels=1),
        playback_expected=None,  # no playback facts in the LX06 manifest
    )
    assert check.ok


# ------------------------------------------------------------------ plane gates

def test_plane_refuses_a_dot2_configured_for_16k_playback(tmp_path: Path) -> None:
    install_dot2_plug(tmp_path)
    adapter = Dot2WireAdapter()

    async def scenario() -> str:
        plane = make_plane(tmp_path, adapter, playback=CAPTURE)
        with tempfile.TemporaryDirectory(prefix="ruiwire-") as td:
            sock = Path(td) / "p.sock"

            async def handler(reader, writer) -> None:
                with contextlib.suppress(Exception):
                    await plane.handle(reader, writer)

            server = await asyncio.start_unix_server(handler, path=str(sock))
            try:
                reader, writer = await asyncio.open_unix_connection(str(sock))
                try:
                    kind, payload = await read_frame(reader, timeout=3.0)
                    assert kind is FrameType.CONTROL
                    msg = CONTROL.validate_json(payload)
                    assert isinstance(msg, Error), msg
                    return msg.message
                finally:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
            finally:
                server.close()
                await server.wait_closed()

    message = asyncio.run(scenario())
    assert message.startswith("audio_config_mismatch:")
    assert "playback.rate" in message
    assert "16000" in message and "48000" in message
    assert "model manifest" in message
    assert "fix:" in message
    assert adapter.play_calls == [], "the plane played before checking the wire"


def test_a_correctly_configured_dot2_starts_and_plays_at_48k(tmp_path: Path) -> None:
    install_dot2_plug(tmp_path)
    adapter = Dot2WireAdapter()

    async def scenario() -> tuple[bool, list[tuple[int, int]]]:
        plane = make_plane(tmp_path, adapter, playback=PLAYBACK, capture=CAPTURE)
        plane.adapter = adapter  # type: ignore[assignment]
        check = plane._verify_wire_format(adapter.info)
        task = asyncio.create_task(plane._speak_loop())
        try:
            enqueue(plane, pcm())
            await wait_until(lambda: len(adapter.play_calls) >= 1)
            plane._stop.set()
            await wait_until(lambda: task.done())
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return check.ok, list(adapter.play_calls)

    ok, calls = asyncio.run(scenario())
    assert ok is True
    assert calls == [(PLAYBACK, 1)]


# ------------------------------------------------------- adapter play failures

def test_a_play_value_error_stops_the_plane_and_is_counted_once(
    tmp_path: Path, caplog,
) -> None:
    install_dot2_plug(tmp_path)
    adapter = Dot2WireAdapter(error=ValueError(
        "the Dot 2 wire is 48000 Hz mono; set audio.playback.rate to 48000 (got 16000)"
    ))

    async def scenario():
        plane = make_plane(tmp_path, adapter, playback=PLAYBACK)
        plane.adapter = adapter  # type: ignore[assignment]
        with caplog.at_level(logging.INFO, logger="rui.audio.process"):
            task = asyncio.create_task(plane._speak_loop())
            try:
                for _ in range(6):
                    enqueue(plane, pcm())
                await wait_until(lambda: plane.playback_errors >= 1)
                await wait_until(lambda: plane._stop.is_set())
                await asyncio.sleep(0.05)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        return plane

    plane = asyncio.run(scenario())
    metrics = plane.metrics()
    assert metrics.playback_errors == 1, metrics.playback_errors
    assert "48000" in metrics.playback_last_error
    assert plane._stop.is_set(), "a deterministic ValueError must trip the breaker"

    messages = [record.getMessage() for record in caplog.records]
    error_lines = [line for line in messages if "playback error" in line]
    assert len(error_lines) == 1, error_lines
    assert not [line for line in messages if line.startswith("speak:")], (
        "one INFO line per chunk is the silent failure this task removes"
    )


def test_three_transient_play_errors_trip_the_circuit_breaker(tmp_path: Path) -> None:
    install_dot2_plug(tmp_path)
    adapter = Dot2WireAdapter(error=RuntimeError("speaker went away"))

    async def scenario():
        plane = make_plane(tmp_path, adapter, playback=PLAYBACK)
        plane.adapter = adapter  # type: ignore[assignment]
        task = asyncio.create_task(plane._speak_loop())
        try:
            for _ in range(6):
                enqueue(plane, pcm())
            await wait_until(lambda: plane._stop.is_set())
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return plane

    plane = asyncio.run(scenario())
    metrics = plane.metrics()
    assert metrics.playback_errors >= 3
    assert "speaker went away" in metrics.playback_last_error
    assert plane._stop.is_set()


# --------------------------------------------------------------- console / API

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}
ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path, monkeypatch, *, playback: int):
    from fastapi.testclient import TestClient

    from rui.config import save_config
    from rui.device.registry import AdapterSet
    from rui.web.api import app as app_module
    from rui.web.api.app import create_app

    install_dot2_plug(tmp_path)
    cfg = dot2_config(tmp_path, playback=playback)
    cfg.web.auth_token = TOKEN
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    adapter = Dot2WireAdapter()
    monkeypatch.setattr(
        app_module, "build_adapters",
        lambda _cfg: AdapterSet(adapters={"dot2": adapter}),
    )
    return TestClient(create_app(cfg, config_path=path)), adapter


def test_device_status_says_configuration_does_not_match_the_device(
    tmp_path: Path, monkeypatch,
) -> None:
    client, _adapter = _client(tmp_path, monkeypatch, playback=CAPTURE)
    try:
        body = client.get("/api/v2/devices/dot2", headers=AUTH).json()
        row = client.get("/api/v2/devices", headers=AUTH).json()
    finally:
        client.close()

    assert body["status"] == "error"
    assert body["ready"] is False
    assert body["status_reason"]["code"] == "audio_config_mismatch"
    warnings = [w for w in body["warnings"] if w["code"] == "audio_wire_mismatch"]
    assert len(warnings) == 1
    warning = warnings[0]
    assert "16000" in warning["message"] and "48000" in warning["message"]
    assert "model manifest" in warning["message"]
    assert "fix:" in warning["message"]
    assert "devices[].audio.playback.rate" in warning["hint"]
    assert body["audio_wire"]["ok"] is False
    assert body["audio_wire"]["mismatches"][0]["path"] == "playback.rate"
    assert row["items"][0]["status_reason"]["code"] == "audio_config_mismatch"


def test_a_matching_dot2_is_ready_and_has_no_wire_warning(
    tmp_path: Path, monkeypatch,
) -> None:
    client, _adapter = _client(tmp_path, monkeypatch, playback=PLAYBACK)
    try:
        body = client.get("/api/v2/devices/dot2", headers=AUTH).json()
    finally:
        client.close()

    # The plane is not started in this unit app, so the link reason is not `ready`;
    # what matters here is that the correct wire raises no mismatch of its own.
    assert body["status_reason"]["code"] != "audio_config_mismatch"
    assert body["audio_wire"]["ok"] is True
    assert not [w for w in body["warnings"] if w["code"] == "audio_wire_mismatch"]


class _PlaneStatus:
    """The supervisor's `status()` dict, without spawning a real plane."""

    def __init__(self, **status: object) -> None:
        self._status = status

    def status(self) -> dict:
        return dict(self._status)


def test_plane_refusal_is_a_mismatch_when_the_api_cannot_recompute(
    tmp_path: Path, monkeypatch,
) -> None:
    client, adapter = _client(tmp_path, monkeypatch, playback=PLAYBACK)

    async def failing_probe() -> DeviceInfo:
        raise RuntimeError("probe timed out")

    monkeypatch.setattr(adapter, "probe", failing_probe)
    try:
        # The exact supervisor shape: client.py wraps the plane's own refusal.
        client.app.state.audio = _PlaneStatus(
            running=False,
            error="AudioPlaneError: audio plane refused: audio_config_mismatch: "
                  "playback.rate: configured 16000, device 48000",
            refusal="",
        )
        body = client.get("/api/v2/devices/dot2", headers=AUTH).json()

        # A generic reader error whose cause survives only in the supervisor's
        # refusal field (the restart reset the playback counter).
        client.app.state.audio = _PlaneStatus(
            running=False,
            error="audio plane reader stopped: IncompleteReadError",
            refusal="audio plane degraded: audio_config_mismatch: playback rejected "
                    "by the adapter: ValueError: expected 48000",
        )
        after_restart = client.get("/api/v2/devices/dot2", headers=AUTH).json()
    finally:
        client.close()

    assert body["audio_wire"]["ok"] is True, "nothing recomputable: the probe failed"
    assert body["status_reason"]["code"] == "audio_config_mismatch"
    assert after_restart["status_reason"]["code"] == "audio_config_mismatch"
    assert after_restart["plane"]["refusal"].startswith("audio plane degraded:")


def test_the_client_keeps_a_plane_error_and_the_supervisor_remembers_it(
    tmp_path: Path,
) -> None:
    from rui.audio.client import AudioPlaneClient, AudioPlaneSupervisor

    message = ("audio plane degraded: audio_config_mismatch: playback rejected "
               "by the adapter: ValueError: the Dot 2 wire is 48000 Hz mono")
    seen: list[str] = []
    client = AudioPlaneClient(None, None)  # type: ignore[arg-type]
    client.on_error = seen.append
    client._dispatch(
        FrameType.CONTROL, Error(message=message).model_dump_json().encode("utf-8"),
    )
    assert client.last_error == message
    assert seen == [message]

    supervisor = AudioPlaneSupervisor(Config(), config_path=tmp_path / "config.yaml")
    supervisor._client_error(message)
    status = supervisor.status()
    assert status["refusal"] == message
    assert status["running"] is False


def test_health_row_carries_the_playback_error_counter() -> None:
    from rui.web.api import app as app_module

    device = DeviceConfig(id="dot2", adapter="dot2", host="192.0.2.10")
    row = app_module._health_device_row(
        {
            "device_id": "dot2",
            "running": True,
            "metrics": {
                "speaker_connected": True,
                "playback_errors": 2,
                "playback_last_error": "ValueError: the Dot 2 wire is 48000 Hz mono",
            },
        },
        device,
    )
    assert row["playback_errors"] == 2
    assert "48000" in row["playback_error"]


def test_console_translates_the_mismatch_status() -> None:
    text = (ROOT / "web/src/v2/i18n.ts").read_text(encoding="utf-8")
    assert text.count("'v2.reason.audio_config_mismatch':") == 2
    assert "audio_config_mismatch: 'v2.reason.audio_config_mismatch'" in text
    assert "配置与设备不符" in text
