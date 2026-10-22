"""Yielding to the speaker's own assistant.

The failure this prevents is audible: 小爱同学 wakes the vendor, our KWS fires on the same
speech, and the room gets two answers to one question. The old project's fix is the rule
here - a vendor wake means we stay quiet for a window, and our own wake word takes over.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from house_ear.config.schema import CaptureConfig, DeviceConfig, VoiceConfig
from house_ear.device.xiaoai import protocol
from house_ear.device.xiaoai.adapter import XiaoaiAdapter
from house_ear.session.vendor import VendorWindow


def instruction(namespace: str, name: str, payload: dict | None = None, as_string: bool = True):
    line = {"header": {"namespace": namespace, "name": name}, "payload": payload or {}}
    data = {"NewLine": json.dumps(line) if as_string else line}
    return {"event": "instruction", "data": data}


def test_a_final_recognition_is_a_vendor_wake() -> None:
    signal = protocol.parse_vendor_signal(
        instruction(
            "SpeechRecognizer",
            "RecognizeResult",
            {"is_final": True, "results": [{"text": "今天天气怎么样"}]},
        )
    )
    assert signal is not None
    assert signal.kind == "wake" and signal.text == "今天天气怎么样"


def test_a_partial_recognition_is_not_a_wake() -> None:
    signal = protocol.parse_vendor_signal(
        instruction("SpeechRecognizer", "RecognizeResult", {"is_final": False, "results": [{"text": "今天"}]})
    )
    assert signal is None


def test_the_stop_capture_event_is_the_dialog_ending_not_a_wake() -> None:
    signal = protocol.parse_vendor_signal(instruction("SpeechRecognizer", "StopCapture"))
    assert signal is not None and signal.kind == "dialog_end"


def test_the_new_line_also_arrives_as_a_nested_string_and_as_a_dict() -> None:
    payload = {"is_final": True, "results": [{"text": "几点了"}]}
    as_string = protocol.parse_vendor_signal(
        instruction("SpeechRecognizer", "RecognizeResult", payload)
    )
    as_dict = protocol.parse_vendor_signal(
        instruction("SpeechRecognizer", "RecognizeResult", payload, as_string=False)
    )
    doubled = protocol.parse_vendor_signal(
        {"event": "instruction", "data": {"NewLine": json.dumps({"NewLine": json.dumps(
            {"header": {"namespace": "SpeechRecognizer", "name": "RecognizeResult"},
             "payload": payload})})}}
    )
    assert as_string and as_dict and doubled
    assert {s.text for s in (as_string, as_dict, doubled)} == {"几点了"}


def test_other_namespaces_are_ignored() -> None:
    assert protocol.parse_vendor_signal(instruction("Speaker", "SetVolume", {"volume": 30})) is None
    assert protocol.parse_vendor_signal({"event": "consumed", "data": {"n": 3}}) is None


def test_the_window_mutes_us_and_our_own_wake_word_clears_it() -> None:
    now = [1000.0]
    window = VendorWindow(quiet_s=12.0, clock=lambda: now[0])

    assert window.muted() is False
    assert window.vendor_spoke("wake", "今天天气怎么样") is True
    assert window.muted() is True
    now[0] += 5.0
    assert window.muted() is True
    assert window.remaining() == pytest.approx(7.0)

    window.ours_spoke()  # "你好小瑞": the user is talking to us
    assert window.muted() is False

    window.vendor_spoke("wake", "x")
    now[0] += 12.0
    assert window.muted() is False
    assert window.vendor_wakes == 2


def test_a_dialog_end_does_not_open_the_window() -> None:
    window = VendorWindow(quiet_s=12.0, clock=lambda: 0.0)
    assert window.vendor_spoke("dialog_end") is False
    assert window.vendor_spoke("playing") is False
    assert window.vendor_wakes == 0
    assert window.muted() is False


def test_yielded_turns_are_counted_so_a_silent_assistant_is_explainable() -> None:
    now = [0.0]
    window = VendorWindow(quiet_s=5.0, clock=lambda: now[0])
    window.vendor_spoke("wake", "现在几点")
    assert window.muted() is True
    assert window.muted() is True
    assert window.yielded_turns == 2


def make_adapter() -> XiaoaiAdapter:
    dev = DeviceConfig(
        id="living", adapter="xiaoai", host="<LAN-IP>",
        token_env="HOUSE_EAR_TEST_DEVICE_TOKEN",
    )
    return XiaoaiAdapter(dev, CaptureConfig(), VoiceConfig())


def test_playing_events_track_the_vendors_player() -> None:
    adapter = make_adapter()
    adapter._on_text(json.dumps({"Event": {"event": "playing", "data": "Playing"}}))
    assert adapter.vendor_playing is True
    adapter._on_text(json.dumps({"Event": {"event": "playing", "data": "Idle"}}))
    assert adapter.vendor_playing is False
    assert adapter.health()["vendor_playing"] is False


def test_pause_vendor_player_uses_the_guarded_ubus_pause() -> None:
    """The old build's script, verbatim: only a running player gets paused."""
    adapter = make_adapter()
    seen: list[str] = []

    async def fake_shell(script: str, timeout: float = 3.0) -> str:
        seen.append(script)
        return "before=1 after=2"

    adapter.shell = fake_shell  # type: ignore[method-assign]
    asyncio.run(adapter.pause_vendor_player("wake takeover"))
    assert adapter.vendor_pauses == 1
    assert "mute_stat" in seen[0], "the pause must check the vendor's state first"
    assert '"action":"pause"' in seen[0], "not the mphelper pause the old build used"


def test_a_dead_rpc_does_not_raise_from_pause_vendor_player() -> None:
    adapter = make_adapter()

    async def dead_shell(script: str, timeout: float = 3.0) -> str:
        raise RuntimeError("speaker gone")

    adapter.shell = dead_shell  # type: ignore[method-assign]
    asyncio.run(adapter.pause_vendor_player("wake takeover"))
    assert adapter.vendor_pauses == 1, "counted as attempted even when the speaker is gone"
