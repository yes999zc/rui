from __future__ import annotations

import pytest

from rui.audio.protocol import (
    HEADER,
    MAX_JSON,
    FrameType,
    Metrics,
    Ready,
    decode_pcm,
    encode_json,
    encode_pcm,
    parse_control,
    parse_header,
)


def test_json_control_roundtrip() -> None:
    raw = encode_json(Ready(pid=1, device_id="living", capture_rate=48000, capture_channels=8,
                            mic_channels=6, reference_channels=2, voice_rate=16000))
    kind, length = parse_header(raw[:HEADER.size])
    assert kind is FrameType.CONTROL
    assert length == len(raw) - HEADER.size
    msg = parse_control(raw[HEADER.size:])
    assert isinstance(msg, Ready)
    assert msg.capture_rate == 48000


def test_pcm_frame_carries_its_own_seq_and_timestamp() -> None:
    raw = encode_pcm(FrameType.VOICE_PCM, seq=7, ts_ms=1234.5, pcm=b"\x01\x02" * 320)
    kind, length = parse_header(raw[:HEADER.size])
    assert kind is FrameType.VOICE_PCM
    seq, ts_ms, pcm = decode_pcm(raw[HEADER.size:HEADER.size + length])
    assert (seq, ts_ms) == (7, 1234.5)
    assert len(pcm) == 640


def test_unknown_frame_type_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_header(HEADER.pack(9, 0))


def test_oversized_control_frame_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_header(HEADER.pack(int(FrameType.CONTROL), MAX_JSON + 1))


def test_metrics_are_wire_safe() -> None:
    msg = Metrics(uptime_s=5.0, frames_in=250, frames_out=250, frames_dropped=0, no_consumer=0,
                  jitter_p50_ms=20.0, jitter_p99_ms=20.4, cpu_s=0.2, audio_s=5.0, rtf=0.04)
    parsed = parse_control(msg.model_dump_json().encode())
    assert isinstance(parsed, Metrics)
    assert parsed.frames_in == 250


def test_reply_finished_carries_the_music_hold() -> None:
    """The plane reads hold_music_s off the parsed message; a missing field there
    crashed the audio plane on the first volume confirmation (measured 09:13)."""
    import json

    from rui.audio.protocol import CONTROL

    msg = CONTROL.validate_json(json.dumps({"type": "reply_finished", "hold_music_s": 0}))
    assert msg.hold_music_s == 0
    plain = CONTROL.validate_json(json.dumps({"type": "reply_finished"}))
    assert plain.hold_music_s is None
