"""0.8.16 ④: per-device KWS threshold/boost.

The global keywords file cannot serve a far-field room (0.07) and a noisy bathroom (0.15)
at once. A device that overrides either value gets its own keywords file under
`data_dir/kws/<device>.txt`; a device with no override keeps the global file untouched.
Production keywords.txt and threshold values stay out of this change.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rui.audio.kws import DEFAULT_KWS_BOOST, DEFAULT_KWS_THRESHOLD
from rui.audio.process import AudioPlane
from rui.config.schema import (
    Config,
    DeviceAudioOverride,
    DeviceConfig,
    DeviceVoiceOverride,
    effective_audio,
)


def _device(voice: DeviceVoiceOverride | None = None) -> DeviceConfig:
    return DeviceConfig(
        id="living",
        adapter="mock",
        host="192.0.2.10",
        room="客厅",
        wake_words=["你好小瑞"],
        audio=DeviceAudioOverride(voice=voice) if voice is not None else None,
    )


def _plane(tmp_path: Path, *, voice: DeviceVoiceOverride | None) -> AudioPlane:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.audio.voice.kws_enabled = True
    cfg.audio.voice.kws_model_dir = str(tmp_path / "model")
    cfg.audio.voice.kws_keywords_file = str(tmp_path / "global.txt")
    cfg.devices = [_device(voice)]
    return AudioPlane(cfg, "living", tmp_path / "plane.sock")


class _FakeWakeWord:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.hits = 0


def _capture_wakeword(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def build(**kwargs) -> _FakeWakeWord:
        seen.append(kwargs)
        return _FakeWakeWord(**kwargs)

    monkeypatch.setattr("rui.audio.process.WakeWord", build)
    return seen


def test_override_fields_parse_and_do_not_leak_into_the_effective_voice() -> None:
    cfg = Config()
    cfg.devices = [
        _device(DeviceVoiceOverride(kws_threshold=0.07, kws_boost=3.0))
    ]
    device = cfg.devices[0]
    effective = effective_audio(cfg.audio, device.audio)
    # The plane reads the override from the device block; VoiceConfig must not grow
    # fields that look resolved but are never used by the KWS path.
    assert not hasattr(effective.voice, "kws_threshold")
    assert effective.voice.rate == cfg.audio.voice.rate


@pytest.mark.parametrize(
    "values",
    [
        {"kws_threshold": 0.001},
        {"kws_threshold": 1.5},
        {"kws_boost": 0.1},
        {"kws_boost": 20.0},
    ],
)
def test_out_of_range_overrides_are_rejected(values: dict) -> None:
    with pytest.raises(ValueError):
        DeviceVoiceOverride(**values)


def test_no_override_keeps_the_global_keywords_file(tmp_path: Path, monkeypatch) -> None:
    seen = _capture_wakeword(monkeypatch)
    plane = _plane(tmp_path, voice=None)
    plane._build_kws()
    assert seen and seen[0]["keywords_file"] == str(tmp_path / "global.txt")
    assert not (tmp_path / "data" / "kws").exists(), "no device file for an untouched device"


def test_threshold_override_writes_this_devices_file(tmp_path: Path, monkeypatch, caplog) -> None:
    seen = _capture_wakeword(monkeypatch)
    plane = _plane(tmp_path, voice=DeviceVoiceOverride(kws_threshold=0.07))
    with caplog.at_level("INFO", logger="rui.audio.process"):
        plane._build_kws()
    target = tmp_path / "data" / "kws" / "living.txt"
    assert seen and str(seen[0]["keywords_file"]) == str(target)
    text = target.read_text(encoding="utf-8")
    assert "#0.07" in text
    assert f":{DEFAULT_KWS_BOOST:.2f}" in text, "the unset half keeps the production default"
    assert "@你好小瑞" in text
    assert not target.with_name(target.name + ".tmp").exists(), "atomic write"
    assert any(
        "kws: keywords device=living threshold=0.07 boost=2.00" in r.getMessage()
        for r in caplog.records
    )


def test_boost_only_override_uses_the_default_threshold(tmp_path: Path, monkeypatch) -> None:
    seen = _capture_wakeword(monkeypatch)
    plane = _plane(tmp_path, voice=DeviceVoiceOverride(kws_boost=5.0))
    plane._build_kws()
    text = (tmp_path / "data" / "kws" / "living.txt").read_text(encoding="utf-8")
    assert f"#{DEFAULT_KWS_THRESHOLD:.2f}" in text
    assert ":5.00" in text
    assert seen and str(seen[0]["keywords_file"]).endswith("living.txt")


def test_the_devices_own_wake_words_are_used(tmp_path: Path, monkeypatch) -> None:
    _capture_wakeword(monkeypatch)
    plane = _plane(
        tmp_path,
        voice=DeviceVoiceOverride(kws_threshold=0.15, kws_boost=2.0),
    )
    plane.device_cfg.wake_words = ["小瑞小瑞"]
    plane._build_kws()
    text = (tmp_path / "data" / "kws" / "living.txt").read_text(encoding="utf-8")
    assert "@小瑞小瑞" in text
    assert "你好小瑞" not in text


def test_a_write_failure_falls_back_to_the_global_file(tmp_path: Path, monkeypatch) -> None:
    seen = _capture_wakeword(monkeypatch)
    plane = _plane(tmp_path, voice=DeviceVoiceOverride(kws_threshold=0.07))

    def explode(*_args, **_kwargs) -> int:
        raise OSError("read-only")

    monkeypatch.setattr("rui.audio.process.write_keywords_file", explode)
    plane._build_kws()
    assert seen and seen[0]["keywords_file"] == str(tmp_path / "global.txt")
