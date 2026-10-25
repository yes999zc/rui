from __future__ import annotations

import types

from rui.config import Config
from rui.config.schema import (
    AecConfig,
    AudioConfig,
    ProviderConfig,
    ProvidersConfig,
    StageConfig,
    VoiceConfig,
    WebConfig,
)
from rui.doctor import probes, run_doctor
from rui.doctor.probes import measure_aec_rtf


def _fake_sherpa() -> types.SimpleNamespace:
    module = types.SimpleNamespace(__version__="1.13.8")
    module.VoiceActivityDetector = type("VoiceActivityDetector", (), {})
    module.KeywordSpotter = type("KeywordSpotter", (), {})
    return module


def _replace_import(monkeypatch, *, module=None, exc: BaseException | None = None):
    def fake_import(name: str, package: str | None = None):
        assert name == "sherpa_onnx"
        if exc is not None:
            raise exc
        return module

    monkeypatch.setattr(probes.importlib, "import_module", fake_import)


def test_sherpa_runtime_import_missing_is_fail(monkeypatch) -> None:
    _replace_import(
        monkeypatch,
        exc=ModuleNotFoundError("No module named 'sherpa_onnx'", name="sherpa_onnx"),
    )
    check = probes._sherpa_runtime_check(Config())
    assert check.name == "wake_runtime"
    assert check.status == "fail"
    assert "not installed" in check.value
    assert "uv sync --extra kws" in check.hint


def test_sherpa_runtime_loader_error_keeps_the_real_text(monkeypatch) -> None:
    _replace_import(
        monkeypatch,
        exc=OSError(
            "libonnxruntime.so: cannot open shared object file: No such file or directory"
        ),
    )
    check = probes._sherpa_runtime_check(Config())
    assert check.status == "fail"
    assert "failed to load" in check.value
    assert "libonnxruntime.so" in check.value


def test_sherpa_runtime_import_error_reports_abi_mismatch(monkeypatch) -> None:
    _replace_import(
        monkeypatch,
        exc=ImportError(
            "libonnxruntime.so.1.28.2: version `VERS_1.28.2' not found "
            "(required by _sherpa_onnx.so)"
        ),
    )
    check = probes._sherpa_runtime_check(Config())
    assert check.status == "fail"
    assert "failed to load" in check.value
    assert "VERS_1.28.2" in check.value


def test_sherpa_runtime_ok_includes_the_version(monkeypatch) -> None:
    _replace_import(monkeypatch, module=_fake_sherpa())
    check = probes._sherpa_runtime_check(Config())
    assert check.name == "wake_runtime"
    assert check.status == "ok"
    assert "sherpa-onnx 1.13.8" in check.value


def test_sherpa_runtime_is_part_of_the_report(monkeypatch) -> None:
    _replace_import(monkeypatch, module=_fake_sherpa())
    report = run_doctor(Config())
    check = next(c for c in report.checks if c.name == "wake_runtime")
    assert check.status == "ok"


def test_sherpa_runtime_model_load_failure_is_fail(monkeypatch, tmp_path) -> None:
    class _VadConfig:
        def __init__(self) -> None:
            self.silero_vad = types.SimpleNamespace(model="")
            self.sample_rate = 0

    class _BrokenVad:
        def __init__(self, cfg, buffer_size_in_seconds: int = 30) -> None:
            raise OSError("invalid model: missing silero metadata")

    module = _fake_sherpa()
    module.VadModelConfig = _VadConfig
    module.VoiceActivityDetector = _BrokenVad
    model = tmp_path / "silero_vad.onnx"
    model.write_bytes(b"not a real model")
    _replace_import(monkeypatch, module=module)

    cfg = Config(audio=AudioConfig(voice=VoiceConfig(vad_model_path=str(model))))
    check = probes._sherpa_runtime_check(cfg)
    assert check.status == "fail"
    assert "failed to load" in check.value
    assert "missing silero metadata" in check.value


def test_configured_but_missing_vad_model_is_not_a_pass() -> None:
    cfg = Config(audio=AudioConfig(voice=VoiceConfig(vad_model_path="/nonexistent/silero_vad.onnx")))
    report = run_doctor(cfg)
    check = next(c for c in report.checks if c.name == "wake_path")
    assert check.status == "warn"
    assert "not found" in check.value


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


# ------------------------------------- model readiness (dot2 #14)

def _models_config(tmp_path, *, create: bool) -> Config:
    asr = tmp_path / "sensevoice"
    vad = tmp_path / "silero_vad.onnx"
    kws = tmp_path / "kws"
    if create:
        asr.mkdir(parents=True)
        (asr / "model.int8.onnx").write_bytes(b"model")
        (asr / "tokens.txt").write_text("tokens", encoding="utf-8")
        vad.write_bytes(b"vad")
        kws.mkdir()
        (kws / "tokens.txt").write_text("tokens", encoding="utf-8")
    providers = ProvidersConfig(
        catalog={
            "local_asr": ProviderConfig(kind="sherpa", model=str(asr)),
            "tts": ProviderConfig(),
            "llm": ProviderConfig(),
        },
        asr=StageConfig(chain=["local_asr"]),
        tts=StageConfig(chain=["tts"]),
        llm=StageConfig(chain=["llm"]),
    )
    return Config(
        providers=providers,
        audio=AudioConfig(voice=VoiceConfig(
            vad_model_path=str(vad),
            kws_enabled=True,
            kws_model_dir=str(kws),
        )),
    )


def test_missing_models_are_a_warn_not_a_silent_cloud_fallback(tmp_path) -> None:
    """No devices means /health is green; this row is where the missing models show."""
    check = probes._models_check(_models_config(tmp_path, create=False))
    assert check.name == "models"
    assert check.status == "warn"
    assert "missing" in check.value
    for part in ("ASR", "VAD", "KWS"):
        assert part in check.value
    assert "fetch-models" in check.hint


def test_present_models_are_reported_ready(tmp_path) -> None:
    check = probes._models_check(_models_config(tmp_path, create=True))
    assert check.status == "ok"
    assert "ASR" in check.value and "VAD" in check.value and "KWS" in check.value
    assert "present" in check.value


def test_a_cloud_only_config_does_not_warn_about_local_models() -> None:
    check = probes._models_check(Config())
    assert check.status == "ok"
    assert "no local models configured" in check.value


def test_the_doctor_report_carries_the_models_row(tmp_path, monkeypatch) -> None:
    # The CI image has no `kws` extra; stub the sherpa import so this test is about the
    # models row and the verdict rule, not about which optional extras are installed.
    _replace_import(monkeypatch, module=_fake_sherpa())
    report = run_doctor(_models_config(tmp_path, create=False))
    check = next(c for c in report.checks if c.name == "models")
    assert check.status == "warn"
    # A warn row degrades the verdict; it must never turn a runnable box "unsupported".
    assert report.verdict == "degraded"
