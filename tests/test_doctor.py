from __future__ import annotations

from house_ear.config import Config
from house_ear.doctor import run_doctor
from house_ear.doctor.probes import measure_aec_rtf


def test_rtf_is_positive_and_tracks_channel_count() -> None:
    cfg = Config().audio.aec
    one = measure_aec_rtf(cfg.model_copy(update={"channels": 1}), seconds=1.0)
    six = measure_aec_rtf(cfg.model_copy(update={"channels": 6}), seconds=1.0)
    assert one > 0
    assert six > one * 4


def test_report_shape() -> None:
    report = run_doctor(Config())
    assert report.verdict in {"ok", "degraded", "unsupported"}
    assert report.device_capacity >= 0
    assert any(c.name == "aec_rtf" for c in report.checks)
    assert "verdict" in report.to_dict()
