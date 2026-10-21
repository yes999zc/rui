from __future__ import annotations

from house_ear.config import Config
from house_ear.config.schema import AecConfig, WebConfig
from house_ear.doctor import run_doctor
from house_ear.doctor.probes import measure_aec_rtf


def test_rtf_is_positive_and_tracks_channel_count() -> None:
    aec = AecConfig()
    one = measure_aec_rtf(aec, rate=16000, frame_ms=20, channels=1, seconds=1.0)
    six = measure_aec_rtf(aec, rate=16000, frame_ms=20, channels=6, seconds=1.0)
    assert one > 0
    assert six > one * 4


def test_disabled_aec_costs_nothing() -> None:
    assert measure_aec_rtf(AecConfig(enabled=False), channels=6, seconds=0.2) == 0.0


def test_report_shape() -> None:
    report = run_doctor(Config())
    assert report.verdict in {"ok", "degraded", "unsupported"}
    assert report.device_capacity >= 1
    assert any(c.name == "aec_rtf" for c in report.checks)
    body = report.to_dict()
    assert "verdict" in body
    # the capacity number must never be reported without the assumption behind it
    assert body["capacity_basis"]


def test_capacity_basis_names_the_unmeasured_part() -> None:
    body = run_doctor(Config()).to_dict()
    assert "not measured" in body["capacity_basis"]


def test_unauthenticated_lan_console_is_warned_about() -> None:
    report = run_doctor(Config(web=WebConfig(host="0.0.0.0", auth_token="")))
    assert any(c.name == "web_auth" and c.status == "warn" for c in report.checks)


def test_capture_layout_is_reported() -> None:
    report = run_doctor(Config())
    check = next(c for c in report.checks if c.name == "capture_layout")
    assert "48000 Hz" in check.value
    assert "8 ch" in check.value
    assert "mic 6 + ref 2" in check.value
