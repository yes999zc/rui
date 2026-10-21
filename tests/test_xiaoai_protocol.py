from __future__ import annotations

import base64
import json

import pytest

from house_ear.device.xiaoai import protocol


def test_request_is_a_text_control_frame() -> None:
    body = json.loads(protocol.request("start_recording", {"channels": 8}))
    assert body["Request"]["command"] == "start_recording"
    assert body["Request"]["payload"] == {"channels": 8}
    assert body["Request"]["id"]


def test_stream_roundtrip_as_byte_array() -> None:
    tag, raw = protocol.decode_stream(protocol.stream("record", b"\x01\x02\x03"))
    assert tag == "record"
    assert raw == b"\x01\x02\x03"


def test_stream_roundtrip_as_base64() -> None:
    frame = protocol.stream("play", b"\x00\x01" * 8, base64_bytes=True)
    assert isinstance(json.loads(frame)["bytes"], str)
    assert protocol.decode_stream(frame) == ("play", b"\x00\x01" * 8)


def test_decode_accepts_what_a_stock_client_sends() -> None:
    pcm = b"\x10\x20" * 4
    frame = json.dumps({"id": "x", "tag": "record", "bytes": list(pcm), "data": None}).encode()
    assert protocol.decode_stream(frame) == ("record", pcm)


@pytest.mark.parametrize(
    "frame",
    [
        b"not json",
        b"[]",
        b'{"id":"x","bytes":[1,2]}',
        b'{"id":"x","tag":"record","bytes":"!!!notbase64"}',
        b'{"id":"x","tag":"record","bytes":{}}',
        json.dumps({"id": "x", "tag": "record", "bytes": [256]}).encode(),
    ],
)
def test_decode_rejects_malformed_frames(frame: bytes) -> None:
    with pytest.raises(protocol.StreamError):
        protocol.decode_stream(frame)


def test_start_recording_payload_carries_layout_and_shifts() -> None:
    payload = protocol.start_recording_payload(
        rate=48000, channels=8, frame_ms=20, mic_shift=12, ref_shift=16
    )
    assert payload["sample_rate"] == 48000
    assert payload["channels"] == 8
    assert payload["bits_per_sample"] == 16
    assert payload["period_size"] == 960          # 20 ms at 48 kHz
    assert payload["buffer_size"] == 1920         # two periods per websocket frame
    assert payload["mic_shift"] == 12 and payload["ref_shift"] == 16


def test_reply_parsing() -> None:
    ok = protocol.parse_reply('{"Response": {"id": "1", "code": 0}}')
    assert ok is not None and ok["Response"]["code"] == 0
    event = protocol.parse_reply('{"Event": {"event": "key_event"}}')
    assert event is not None and event["Event"]["event"] == "key_event"
    assert protocol.parse_reply("nonsense") is None
    assert protocol.parse_reply("[]") is None


def test_base64_helper_matches_stdlib() -> None:
    assert protocol.stream("play", b"abc", base64_bytes=True)
    assert base64.b64encode(b"abc") == b"YWJj"
