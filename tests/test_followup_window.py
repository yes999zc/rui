"""Follow-up gate configuration: preserve the LX06 default, expose the device-AEC knob.

The gate opens one second after the reply drains and replays what the microphone heard
in between. That 1 s echo tail was measured on the LX06; a plug that does its own AEC
(no reference channel, RUI AEC off) keeps residual echo for longer, and the replayed
tail becomes a turn. The two new audio.voice fields let that plug tune the window
without inventing an adapter-side mute, while the defaults keep the LX06 path byte for
byte identical.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from rui.audio.process import AudioPlane
from rui.config import Config, load_config, save_config
from rui.config.schema import VoiceConfig


def _plane(**voice: object) -> AudioPlane:
    cfg = Config()
    for key, value in voice.items():
        setattr(cfg.audio.voice, key, value)
    return AudioPlane(cfg, "living", Path("/tmp/followup-window.sock"))


def test_defaults_preserve_the_lx06_followup_behaviour() -> None:
    cfg = Config()
    assert cfg.audio.voice.followup_delay_ms == 1000
    assert cfg.audio.voice.followup_replay is True
    assert VoiceConfig().followup_delay_ms == 1000


def test_followup_delay_is_bounded() -> None:
    assert VoiceConfig(followup_delay_ms=0).followup_delay_ms == 0
    assert VoiceConfig(followup_delay_ms=10000).followup_delay_ms == 10000
    with pytest.raises(ValidationError):
        VoiceConfig(followup_delay_ms=10001)
    with pytest.raises(ValidationError):
        VoiceConfig(followup_delay_ms=-1)


def test_replay_off_drops_the_gated_tail_without_feeding_the_vad() -> None:
    plane = _plane(followup_replay=False)
    plane._followup_capture = True
    plane._followup_tail.append((0.0, bytes(320)))
    plane._followup_samples = 320

    asyncio.run(plane._replay_followup_tail())

    assert plane._followup_samples == 0
    assert plane._followup_capture is False
    assert not plane._followup_tail


def test_the_capture_buffer_is_bounded_by_the_configured_window() -> None:
    plane = _plane(followup_delay_ms=2500)
    plane.gate = SimpleNamespace(armed=False)  # type: ignore[assignment]
    now = time.monotonic()
    plane._followup_capture = True
    plane._followup_start = now
    plane._followup_end = now + 2.5
    chunk = bytes(640)  # 20 ms of silence at 16 kHz (320 samples)
    for _ in range(175):  # 3.5 s offered
        plane._capture_followup_tail(chunk, 0.0)

    # The old hard-coded 2 s cap would have kept 32000 samples; the configured
    # 2.5 s window (plus its 0.25 s late-task margin) keeps more, and still bounds it.
    assert 32000 < plane._followup_samples <= 44000


def test_the_window_fields_survive_save_and_reload(tmp_path) -> None:
    cfg = Config()
    cfg.audio.voice.followup_delay_ms = 2500
    cfg.audio.voice.followup_replay = False
    path = tmp_path / "config.yaml"
    save_config(path, cfg)

    assert "followup_delay_ms: 2500" in path.read_text(encoding="utf-8")
    loaded = load_config(path)
    assert loaded.audio.voice.followup_delay_ms == 2500
    assert loaded.audio.voice.followup_replay is False


def test_an_old_config_without_the_fields_keeps_the_lx06_defaults(tmp_path) -> None:
    # Configs written before 0.4.1 have no followup keys; loading must not fail and
    # must keep the measured LX06 behaviour (1 s tail, replay on).
    path = tmp_path / "config.yaml"
    save_config(path, Config())
    text = path.read_text(encoding="utf-8")
    text = "".join(
        line for line in text.splitlines(keepends=True)
        if "followup_" not in line
    )
    path.write_text(text, encoding="utf-8")

    loaded = load_config(path)
    assert loaded.audio.voice.followup_delay_ms == 1000
    assert loaded.audio.voice.followup_replay is True
