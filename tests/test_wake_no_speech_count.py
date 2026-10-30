"""A wake that heard nothing is a measurement, not only a log line (0.8.13).

Production logs carried 20 `wake with no speech` lines in one afternoon of sampling
(2026-10-30) with no number anywhere to divide by: the console showed `gate_discarded`
(which also counts too-short utterances) and nothing else, so "how often does an empty
wake happen, and is it getting worse?" could only be answered by grepping text.

The gate now keeps two counters and prints both in its existing line:

* `silent_wakes` - since the plane process started;
* `silent_wakes_session` - since a control plane connected (`reset_session()`).

Both ship inside `Metrics`, which is the mechanism the plane already uses for every
other number (`gate_discarded`, `utterances`, ...). Nothing else changes: the wake still
disarms silently, and the timeout still reaches the control plane as `WakeIdle`.

The gate is driven through the same fake-Silero (energy VAD) harness as
`test_cue_echo_excision.py`, so this file needs no model.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
import pytest

from rui.audio import process as process_module
from rui.audio.probe import ProbeResult
from rui.audio.process import AudioPlane
from rui.audio.protocol import Metrics, Shutdown, encode_json
from rui.audio.vad import GateConfig, UtteranceGate
from rui.config import Config
from rui.device.contract import DeviceInfo

RATE = 16000
BLOCK = 640  # 20 ms of s16 at 16 kHz, the frame size the plane feeds


class _FakeSileroConfig:
    def __init__(self) -> None:
        self.silero_vad = types.SimpleNamespace()
        self.sample_rate = RATE


class _FakeSileroVad:
    """Per-block energy VAD: speech whenever the block is not near-silence."""

    def __init__(self, config, buffer_size_in_seconds: int = 30) -> None:  # noqa: ARG002
        self.config = config
        self._speech = False

    def reset(self) -> None:
        self._speech = False

    def accept_waveform(self, samples: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        self._speech = rms > 0.01

    def is_speech_detected(self) -> bool:
        return self._speech


@pytest.fixture()
def fake_silero(tmp_path, monkeypatch):
    """The model file + module stub the gate needs - no real VAD, no inference."""
    module = types.ModuleType("sherpa_onnx")
    module.VadModelConfig = _FakeSileroConfig
    module.VoiceActivityDetector = _FakeSileroVad
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    model = tmp_path / "silero_vad.onnx"
    model.write_bytes(b"fake")
    return model


@pytest.fixture()
def make_gate(fake_silero):
    def build() -> UtteranceGate:
        gate = UtteranceGate(GateConfig(), fake_silero)
        gate.arm(skip_after_wake_ms=0)  # the KWS phrase tail is not this test's subject
        return gate

    return build


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * RATE)


def _voice(seconds: float) -> bytes:
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    syllable = (np.sin(2.0 * np.pi * t / 0.5) > -0.2).astype(np.float32)
    sig = (0.32 * np.sin(2.0 * np.pi * 180.0 * t)
           + 0.18 * np.sin(2.0 * np.pi * 320.0 * t)) * syllable
    return (np.clip(sig, -0.99, 0.99) * 32768.0).astype("<i2").tobytes()


def _feed(gate: UtteranceGate, pcm: bytes) -> tuple[bytes, float] | None:
    out = None
    for start in range(0, len(pcm), BLOCK):
        result = gate.feed(pcm[start : start + BLOCK])
        if result is not None:
            out = result
    return out


def _silent_wake(gate: UtteranceGate, seconds: float = 6.0) -> None:
    """One armed wake, then nothing said: the silent-timeout path."""
    gate.arm(skip_after_wake_ms=0)
    assert _feed(gate, _silence(seconds)) is None
    assert gate.armed is False, "the wake must still disarm silently"


def _gate_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "wake with no speech" in r.getMessage()]


# ------------------------------------------------------------------ the counters

def test_a_silent_wake_counts_once(make_gate) -> None:
    gate = make_gate()
    _silent_wake(gate)
    assert gate.silent_wakes == 1
    assert gate.silent_wakes_session == 1
    # The pre-existing behaviour is untouched: still one discard, still one WakeIdle.
    assert gate.discarded == 1
    assert gate.idle_events == 1
    assert gate.turns == 0


def test_a_turn_that_actually_spoke_is_not_a_silent_wake(make_gate) -> None:
    gate = make_gate()
    gate.arm(skip_after_wake_ms=0)
    _feed(gate, _voice(1.0) + _silence(1.5))
    assert gate.turns == 1
    assert gate.silent_wakes == 0, "a real turn was counted as an empty wake"
    assert gate.silent_wakes_session == 0


def test_the_count_climbs_across_wakes(make_gate) -> None:
    gate = make_gate()
    for _ in range(3):
        _silent_wake(gate)
    assert (gate.silent_wakes, gate.silent_wakes_session) == (3, 3)


def test_the_log_line_carries_both_numbers(make_gate, caplog) -> None:
    """One grep of the log answers "how often" without a metrics round trip."""
    gate = make_gate()
    with caplog.at_level(logging.INFO, logger="rui.audio.vad"):
        _silent_wake(gate)
        _silent_wake(gate)
    lines = _gate_lines(caplog)
    assert len(lines) == 2, lines
    assert "silent #1 this session, #1 since plane start" in lines[0], lines[0]
    assert "silent #2 this session, #2 since plane start" in lines[1], lines[1]


def test_a_new_session_restarts_only_the_session_number(make_gate) -> None:
    """A reconnecting console must not erase the process's own history."""
    gate = make_gate()
    _silent_wake(gate)
    _silent_wake(gate)
    gate.reset_session()
    assert (gate.silent_wakes, gate.silent_wakes_session) == (2, 0)
    _silent_wake(gate)
    assert (gate.silent_wakes, gate.silent_wakes_session) == (3, 1)


# ------------------------------------------------- the existing metrics mechanism

class _StubAdapter:
    def health(self) -> dict:
        return {"connected": True}


def _plane(gate: UtteranceGate) -> AudioPlane:
    cfg = Config()
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "dot2", Path("/tmp/no-speech-count.sock"))
    plane.adapter = _StubAdapter()  # type: ignore[assignment]
    plane.gate = gate
    return plane


def test_metrics_ship_both_counts(make_gate) -> None:
    """The plane's metrics message is the output: no new endpoint, no new shape."""
    gate = make_gate()
    plane = _plane(gate)
    assert plane.metrics().gate_silent_wakes == 0

    _silent_wake(gate)
    _silent_wake(gate)
    metrics = plane.metrics()
    assert metrics.gate_silent_wakes == 2
    assert metrics.gate_silent_wakes_session == 2
    # Round trip through the wire shape the control plane validates: the fields are
    # additive with defaults, so an older receiver must not choke on them.
    assert Metrics.model_validate(metrics.model_dump()).gate_silent_wakes == 2


def test_metrics_reported_zero_without_a_gate() -> None:
    """`vad_enabled: false` must read as "no evidence", never as a missing field."""
    cfg = Config()
    plane = AudioPlane(cfg, "dot2", Path("/tmp/no-speech-count.sock"))
    plane.adapter = _StubAdapter()  # type: ignore[assignment]
    plane.gate = None
    metrics = plane.metrics()
    assert metrics.gate_silent_wakes == 0
    assert metrics.gate_silent_wakes_session == 0


# --------------------------------------- the wiring: a real connection, a real handle()

class _CountingGate(UtteranceGate):
    """A real gate that counts `reset_session()` calls on itself.

    The gate a connection will use does not exist when a test starts - `_capture`
    builds it - so spying on the class is the only way to assert *which* gate the
    session reset landed on. That is exactly what was broken: the call in `handle()`
    ran before any gate existed and quietly did nothing.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.session_resets = 0

    def reset_session(self) -> None:
        self.session_resets += 1
        super().reset_session()


class _FramesAdapter:
    """Connection-only adapter: it never yields a frame, the socket ends the session."""

    def __init__(self) -> None:
        self.closed = False

    def health(self) -> dict:
        return {"connected": True}

    async def close(self) -> None:
        self.closed = True

    async def frames(self):
        await asyncio.Event().wait()
        yield  # pragma: no cover - cancelled when the connection ends


async def _probe(_adapter: object) -> ProbeResult:
    return ProbeResult(
        info=DeviceInfo(id="dot2", adapter="dot2", model="dot2", host="192.0.2.10"),
    )


async def _one_session(plane: AudioPlane, sock: Path, adapter: _FramesAdapter,
                       previous: UtteranceGate) -> None:
    """One real control-plane connection through `AudioPlane.handle()`."""
    server = await asyncio.start_unix_server(plane.handle, path=str(sock))
    try:
        reader, writer = await asyncio.open_unix_connection(str(sock))
        try:
            for _ in range(500):
                if plane.gate is not previous:
                    break
                await asyncio.sleep(0.01)
            assert plane.gate is not previous, "the capture task never built a gate"
            # `_build_vad` and the session reset are one synchronous block inside
            # `_capture`, so an observed new gate means the reset has already run.
            writer.write(encode_json(Shutdown()))
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        for _ in range(500):
            if adapter.closed:
                break
            await asyncio.sleep(0.01)
        assert adapter.closed, "handle() did not run its teardown"
    finally:
        server.close()
        await server.wait_closed()
        current = asyncio.current_task()
        rest = [task for task in asyncio.all_tasks() if task is not current]
        for task in rest:
            task.cancel()
        await asyncio.gather(*rest, return_exceptions=True)


def test_a_new_connection_restarts_the_session_count_only(
    make_gate, fake_silero, tmp_path, monkeypatch,
) -> None:
    """The session reset must land on the gate this connection actually uses.

    Driven through `handle()` over a unix socket, not by calling `reset_session()`
    directly: the bug was the call site, and a direct call cannot see it. The plane
    starts with the gate a previous session left behind (3 empty wakes since the plane
    started, 2 of them in that session), so both halves of the contract are visible:
    the session number restarts, the process number does not.
    """
    cfg = Config()
    cfg.audio.aec.enabled = False
    cfg.audio.voice.vad_model_path = str(fake_silero)
    plane = AudioPlane(cfg, "dot2", Path("/tmp/no-speech-count.sock"))
    previous = make_gate()
    previous.silent_wakes = 3
    previous.silent_wakes_session = 2
    plane.gate = previous

    adapter = _FramesAdapter()

    async def open_adapter() -> _FramesAdapter:
        return adapter

    plane._open_adapter = open_adapter  # type: ignore[method-assign]
    monkeypatch.setattr(process_module, "safe_probe", _probe)
    monkeypatch.setattr(process_module, "UtteranceGate", _CountingGate)

    # AF_UNIX caps the whole path at ~104 bytes; pytest's tmp_path is longer.
    with tempfile.TemporaryDirectory(prefix="ruivad-") as td:
        asyncio.run(_one_session(plane, Path(td) / "c.sock", adapter, previous))

    gate = plane.gate
    assert isinstance(gate, _CountingGate)
    assert gate.session_resets == 1, (
        "the new session never reset the gate it is using - the call is dead wiring "
        "again (it must run after `_build_vad`, not in `handle`)")
    assert gate.silent_wakes == 3, "a reconnect erased the since-plane-start count"
    assert gate.silent_wakes_session == 0
    metrics = plane.metrics()
    assert (metrics.gate_silent_wakes, metrics.gate_silent_wakes_session) == (3, 0)
