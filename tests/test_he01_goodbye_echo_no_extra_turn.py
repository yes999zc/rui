"""HE-01: replaying our own goodbye as microphone input must not open a turn.

Inventory ID: HE-01 (`docs/audit/unverified-inventory.md` §8.2; original D5 in
`docs/plan-to-0.6.md:21`). The failure this guards against is the assistant hearing
its own farewell and answering it (「这吗」 -> 「在的，你说吧。」).

What this pins: the real `UtteranceGate` is armed and given the goodbye reply as its
echo reference; the same PCM is fed back as microphone input at device lags 0/150/
400 ms. The gate must excise its own audio: no capture segment and zero turns, i.e.
0 extra turns from the goodbye.

What it cannot pin (real device): the actual speaker-to-microphone echo level and the
device AEC residual. The inventory's second half ("10 real goodbyes on the Dot 2")
still needs the device.

HE-01 ② (the idle-watchdog `reply_finished` bookkeeping at
`src/rui/web/api/app.py:3327-3330`) is deliberately NOT covered here. That path calls
the bare `_session_client(app).reply_finished`, not `_PlaneLink.reply_finished`; the
`_PlaneLink` version only repeats `tests/test_dismiss_turn_release.py:178-189` and
never touches the idle loop, while a bare-client + coordinator sequence would just
re-state the four lines of the idle branch instead of exercising them (fake coverage).
Per the task-9 decision this half stays unverified rather than being faked here.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest
import test_cue_echo_excision as cuefx

from rui.audio.vad import GateConfig, UtteranceGate

RATE = cuefx.RATE
# Deliberately the ack-cue (540 ms) parameters, not the goodbye ones: production uses
# hold 2400 / skip 1450 for a 1200 ms reply (review verified the same conclusion with
# that wiring). The constants are not meant to be read as production values.
HOLD_MS = cuefx.HOLD_MS
SKIP_MS = cuefx.SKIP_MS
GOODBYE_MS = 1200  # a spoken farewell, longer than the 540 ms ack cue


@pytest.fixture()
def make_gate(tmp_path, monkeypatch):
    """The same fake-Silero gate as `test_cue_echo_excision.make_gate`."""
    module = types.ModuleType("sherpa_onnx")
    module.VadModelConfig = cuefx._FakeSileroConfig
    module.VoiceActivityDetector = cuefx._FakeSileroVad
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    model = tmp_path / "silero_vad.onnx"
    model.write_bytes(b"fake")

    def build() -> UtteranceGate:
        gate = UtteranceGate(GateConfig(), model)
        gate.arm(skip_after_wake_ms=0)  # the KWS phrase tail is not this test's subject
        return gate

    return build


@pytest.mark.parametrize("lag_ms", [0, 150, 400])
def test_goodbye_pcm_replayed_as_mic_opens_no_turn(make_gate, lag_ms) -> None:
    """The farewell's own PCM is handed back as mic input at 0/150/400 ms.

    A device-AEC speaker hears its own goodbye; the gate knows the exact PCM (it
    played it) and must remove it instead of letting it become a new request. This is
    the headless half of D5/HE-01; the real-room echo level is not simulated.
    """
    gate = make_gate()
    goodbye = cuefx._raw(GOODBYE_MS / 1000)
    gate.set_echo_reference(np.frombuffer(goodbye, dtype="<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)

    mic = cuefx._silence(lag_ms / 1000) + goodbye + cuefx._silence(6.0)
    captured = cuefx._feed(gate, mic)
    if captured is None:
        captured = cuefx._close(gate)

    assert captured is None, "the goodbye was captured as an utterance"
    assert gate.turns == 0, "the goodbye opened its own turn (extra turn)"


def test_a_person_right_after_the_goodbye_is_captured_once(make_gate) -> None:
    """Positive control: excising the echo must not swallow the person's next words.

    The goodbye is the echo reference and also the first thing in the microphone;
    one second of speech follows 1.25 s later. A gate that over-excises (or drops the
    whole held window) loses that turn; the goodbye-only cases above cover the
    opposite direction (the echo must not become a turn by itself).
    """
    gate = make_gate()
    goodbye = cuefx._raw(GOODBYE_MS / 1000)
    reply = cuefx._user(1.0)
    gate.set_echo_reference(np.frombuffer(goodbye, dtype="<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)

    mic = cuefx._mix(5.0, (0.0, goodbye), (1.25, reply))
    captured = cuefx._feed(gate, mic)
    if captured is None:
        captured = cuefx._close(gate)

    assert captured is not None, "the person's reply was lost with the goodbye echo"
    assert gate.turns == 1, f"expected exactly one person turn, got {gate.turns}"
    segment = cuefx._as_float(captured[0])
    assert cuefx._best_corr(segment, cuefx._as_float(goodbye)) < 0.4, (
        "the goodbye reached ASR instead of being excised")
    assert cuefx._retained_energy(segment, cuefx._as_float(reply)) >= 0.8, (
        "the person's speech was cut by the echo excision")
