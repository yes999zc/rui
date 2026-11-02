"""`audio.voice.speaker_gate`: off by default, validated when on.

The default matters most: a config that never mentions the gate must reproduce 0.8.15
exactly, so `enabled=false` is asserted from a v2 file, a bare `Config()` and a save/
load round trip.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rui.config import Config, load_config, save_config
from rui.config.schema import ConfigError, SpeakerGateConfig

BASE = """\
version: 2
devices:
  - id: living
    adapter: mock
    host: 192.0.2.10
    room: 客厅
"""

GATE = """\
version: 2
audio:
  voice:
    speaker_gate:
      enabled: true
      model_path: /data/models/speaker/campplus.onnx
      threshold: 0.6
      min_speech_ms: 800
      mode: wake_owner
      accept_anchor_or_library: false
      skip_on_barge: false
devices:
  - id: living
    adapter: mock
    host: 192.0.2.10
    room: 客厅
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_off_and_match_the_card() -> None:
    gate = Config().audio.voice.speaker_gate
    assert isinstance(gate, SpeakerGateConfig)
    assert gate.enabled is False
    assert gate.model_path == ""
    assert gate.threshold == 0.55
    assert gate.min_speech_ms == 600
    assert gate.mode == "any_enrolled"
    assert gate.accept_anchor_or_library is True
    assert gate.skip_on_barge is True


def test_a_config_without_the_block_keeps_the_gate_off(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, BASE))
    assert cfg.audio.voice.speaker_gate.enabled is False
    # The rollback key: no v3-only field was used, so the file stays a v2 file.
    assert cfg.version == 2


def test_the_block_parses_every_field(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, GATE))
    gate = cfg.audio.voice.speaker_gate
    assert gate.enabled is True
    assert gate.model_path == "/data/models/speaker/campplus.onnx"
    assert gate.threshold == 0.6
    assert gate.min_speech_ms == 800
    assert gate.mode == "wake_owner"
    assert gate.accept_anchor_or_library is False
    assert gate.skip_on_barge is False


def test_saved_config_roundtrips_the_gate(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE)
    cfg = load_config(path)
    cfg.audio.voice.speaker_gate = SpeakerGateConfig(
        enabled=True, model_path="/models/campplus.onnx", threshold=0.62
    )
    save_config(path, cfg)
    reloaded = load_config(path)
    assert reloaded.audio.voice.speaker_gate.enabled is True
    assert reloaded.audio.voice.speaker_gate.model_path == "/models/campplus.onnx"
    assert reloaded.audio.voice.speaker_gate.threshold == 0.62


@pytest.mark.parametrize("value", [-0.1, 1.5])
def test_threshold_out_of_range_is_rejected(tmp_path: Path, value: float) -> None:
    bad = GATE.replace("threshold: 0.6", f"threshold: {value}")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


@pytest.mark.parametrize("value", [100, 5000])
def test_min_speech_ms_out_of_range_is_rejected(tmp_path: Path, value: int) -> None:
    bad = GATE.replace("min_speech_ms: 800", f"min_speech_ms: {value}")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_unknown_mode_is_rejected(tmp_path: Path) -> None:
    bad = GATE.replace("mode: wake_owner", "mode: face_id")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    bad = GATE.replace("skip_on_barge: false", "skip_on_barge: false\n      face_gate: true")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))
