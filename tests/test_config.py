from __future__ import annotations

import pytest
from pydantic import ValidationError

from house_ear.config import Config, DeviceConfig, load_config, save_config
from house_ear.config.schema import AudioConfig


def test_defaults_are_sane() -> None:
    cfg = Config()
    assert cfg.audio.aec.mic_shift == 8
    assert cfg.audio.aec.ref_shift == 13
    assert cfg.tools.expose == "domain"


def test_frame_ms_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AudioConfig(frame_ms=100)


def test_duplicate_device_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Config(devices=[
            DeviceConfig(id="living", host="10.0.0.2"),
            DeviceConfig(id="living", host="10.0.0.3"),
        ])


def test_save_is_atomic_and_keeps_backup(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    save_config(path, Config())
    first = path.read_text(encoding="utf-8")
    save_config(path, Config(web={"port": 9090}))
    assert load_config(path).web.port == 9090
    backups = list(tmp_path.glob("config.yaml.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == first


def test_round_trip_preserves_devices(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    save_config(path, Config(devices=[DeviceConfig(id="living", host="<LAN-IP>", room="客厅")]))
    cfg = load_config(path)
    assert cfg.devices[0].room == "客厅"
