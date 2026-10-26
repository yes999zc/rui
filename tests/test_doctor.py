from __future__ import annotations

import contextlib
import json
import types
from pathlib import Path

from rui.config import Config
from rui.config.schema import (
    AecConfig,
    AudioConfig,
    DeviceAudioOverride,
    DeviceConfig,
    DevicePlaybackOverride,
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


def test_capture_layout_reports_the_neutral_core_default_without_a_device() -> None:
    # 0.6 M1: no device -> the core default is neutral (16000/1ch/0ref), not the LX06.
    check = next(c for c in run_doctor(Config()).checks if c.name == "capture_layout")
    assert "core default" in check.value
    assert "16000 Hz" in check.value and "1 ch" in check.value
    assert "mic 1 + ref 0" in check.value


def test_capture_layout_reports_the_merged_manifest_layout() -> None:
    """The row describes what the plane will run: the device manifest's own layout."""
    cfg = Config(devices=[DeviceConfig(id="lx06", adapter="xiaoai", host="192.0.2.10",
                                       room="客厅")])
    check = next(c for c in run_doctor(cfg).checks if c.name == "capture_layout")
    assert "lx06:" in check.value
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


# --------------------------------------- 0.5.3 observability rows
#
# The three rows asked for by the first multi-device install: wire rate vs the rate that
# actually plays, the channel gateway's last error (a Telegram 409 from a second poller),
# and console-token.txt vs web.auth_token. None of them may print a token value.

def _device_config(tmp_path, *, rate: int | None = None, room: str = "卧室") -> Config:
    cfg = Config(devices=[DeviceConfig(id="bedroom", adapter="dot2", host="192.0.2.10",
                                       room=room)])
    cfg.system.data_dir = str(tmp_path)
    if rate is not None:
        cfg.devices[0].audio = DeviceAudioOverride(
            playback=DevicePlaybackOverride(rate=rate))
    return cfg


def _write_manifest_only_dot2_plug(tmp_path: Path) -> None:
    """A manifest-only plug whose audio_defaults declare the Dot 2 wire (M4 shape)."""
    from ruamel.yaml import YAML

    root = tmp_path / "plugs" / "rui-plugin-dot2"
    (root / "devices").mkdir(parents=True)
    yaml = YAML()
    yaml.default_flow_style = False
    plugin = {
        "schema": "rui-plugin/v0",
        "id": "dot2",
        "name": "Echo Dot 2",
        "version": "1.2.0",
        "rui_compat": ">=0.5.0 <0.8.0",
        "license": "MIT",
        "maintainer": "rui maintainers",
        "homepage": "https://example.invalid/dot2",
        "trust": "third-party",
        "devices": ["devices/dot2.yaml"],
    }
    device = {
        "schema": "rui-device/v1",
        "id": "dot2",
        "name": "Echo Dot 2",
        "vendor": "Amazon",
        "class": "A",
        "transport": "inbound",
        "protocol": {"kind": "ws", "version": "1"},
        "capabilities": ["playback"],
        "audio_defaults": {"playback": {"rate": 48000, "channels": 1}},
        "params": [],
        "compatibility": {"rui_min": "0.5.0", "tested_with": ["0.5.3"]},
    }
    with (root / "plugin.yaml").open("w", encoding="utf-8") as handle:
        yaml.dump(plugin, handle)
    with (root / "devices" / "dot2.yaml").open("w", encoding="utf-8") as handle:
        yaml.dump(device, handle)


def test_audio_rates_uses_the_merged_manifest_playback_rate(tmp_path, monkeypatch) -> None:
    """0.6 M1: the row compares the wire rate with the *merged* playback rate.

    Before M1 it read the global block and a Dot 2 manifest could only fix the
    mismatch through a devices[].audio override. The plug's audio_defaults is now the
    bottom layer, so the shipped manifest is enough - and a config that states its own
    playback rate still overrides it (that path is pinned in test_config_v3.py).
    """
    cfg = _device_config(tmp_path)          # global playback.rate is 16000
    monkeypatch.setattr(probes, "_declared_wire_rate",
                        lambda cfg, dev: (48000, "fake.SPEAKER_RATE"))
    # No plug yet: the neutral/global playback rate is what the plane would use.
    check = probes._audio_rates_check(cfg)
    assert check.status == "warn"
    assert "wire 48000 Hz != playback.rate 16000 Hz" in check.value

    _write_manifest_only_dot2_plug(tmp_path)
    check = probes._audio_rates_check(cfg)
    assert check.status == "ok"
    assert "wire 48000 Hz = playback.rate 48000 Hz" in check.value


def test_audio_rates_reports_a_wire_rate_mismatch_with_both_numbers(tmp_path,
                                                                   monkeypatch) -> None:
    cfg = _device_config(tmp_path)          # global playback.rate is 16000
    monkeypatch.setattr(probes, "_declared_wire_rate",
                        lambda cfg, dev: (48000, "fake.SPEAKER_RATE"))
    check = probes._audio_rates_check(cfg)
    assert check.name == "audio_rates"
    assert check.status == "warn"
    assert "48000" in check.value and "16000" in check.value
    assert "devices[0].audio.playback.rate" in check.hint
    # The per-device override is the fix, and it keeps both numbers in the evidence.
    cfg = _device_config(tmp_path, rate=48000)
    check = probes._audio_rates_check(cfg)
    assert check.status == "ok"
    assert "48000 Hz" in check.value
    assert "not" not in check.value


def test_audio_rates_is_not_applicable_without_a_plug_declaration(tmp_path,
                                                                  monkeypatch) -> None:
    monkeypatch.setattr(probes, "_declared_wire_rate", lambda cfg, dev: (None, ""))
    check = probes._audio_rates_check(_device_config(tmp_path))
    assert check.status == "ok"
    assert "not_applicable" in check.value
    assert "playback.rate 16000 Hz" in check.value


def test_audio_rates_reads_the_rate_from_the_adapter_module_constant(monkeypatch) -> None:
    module = types.SimpleNamespace(SPEAKER_RATE=48000)
    monkeypatch.setattr(probes.importlib, "import_module", lambda name: module)
    rate, source = probes._wire_rate_from_entrypoint("rui_dot2_bridge.adapter:build")
    assert rate == 48000
    assert source == "rui_dot2_bridge.adapter.SPEAKER_RATE"
    # A boolean/out-of-range value is not a rate.
    assert probes._wire_rate_from_module(types.SimpleNamespace(SPEAKER_RATE=True)) == (None, "")
    assert probes._wire_rate_from_module(types.SimpleNamespace(SPEAKER_RATE=123)) == (None, "")

    def broken(name: str):
        raise ImportError("plug not installed")

    monkeypatch.setattr(probes.importlib, "import_module", broken)
    assert probes._wire_rate_from_entrypoint("missing.module:build") == (None, "")


def test_the_doctor_report_carries_the_new_rows_and_degrades_on_a_mismatch(
        tmp_path, monkeypatch) -> None:
    cfg = _device_config(tmp_path)
    monkeypatch.setattr(probes, "_declared_wire_rate",
                        lambda cfg, dev: (48000, "fake.SPEAKER_RATE"))
    monkeypatch.setattr(probes.socket, "create_connection",
                        lambda *args, **kwargs: contextlib.nullcontext())
    monkeypatch.delenv("RUI_CONSOLE_TOKEN_FILE", raising=False)
    report = run_doctor(cfg)
    names = {check.name for check in report.checks}
    assert {"audio_rates", "channels", "console_token"} <= names
    checks = {check.name: check for check in report.checks}
    # CI has no optional extras (sherpa/kws), so the *verdict* is "unsupported" there;
    # pin the rows this test is about, not the verdict (same lesson as c0d7048).
    assert checks["audio_rates"].status == "warn"
    assert "48000" in checks["audio_rates"].value
    assert report.verdict != "ok"


def _write_channels(tmp_path, **channels) -> Path:
    path = tmp_path / "channels.json"
    path.write_text(json.dumps(channels), encoding="utf-8")
    return path


def _gateway(**overrides) -> dict:
    status = {"running": True, "pid": 42, "last_error": ""}
    status.update(overrides)
    return status


def test_channels_lists_enabled_channels_when_the_gateway_is_healthy(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    _write_channels(tmp_path, telegram={"enabled": True})
    check = probes._channels_check(cfg, _gateway())
    assert check.name == "channels"
    assert check.status == "ok"
    assert "telegram enabled" in check.value
    assert "pid 42" in check.value


def test_channels_surfaces_the_gateway_last_error_as_degraded(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    _write_channels(tmp_path, telegram={"enabled": True})
    check = probes._channels_check(
        cfg, _gateway(last_error="Conflict: terminated by other getUpdates request"))
    assert check.status == "warn"
    assert "Conflict" in check.value
    assert "409" in check.hint


def test_channels_reads_the_409_from_the_gateway_log(tmp_path) -> None:
    """The child's poll loop writes the 409 to the log; the supervisor sees only "running"."""
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    _write_channels(tmp_path, telegram={"enabled": True})
    (tmp_path / "channels-gateway.log").write_text(
        "2026-10-25 12:00:00 WARNING rui.channels.telegram: telegram poll: "
        "RuntimeError: Conflict: terminated by other getUpdates request\n",
        encoding="utf-8")
    check = probes._channels_check(cfg, _gateway())
    assert check.status == "warn"
    assert "Conflict" in check.value


def test_channels_never_prints_a_telegram_bot_token(tmp_path) -> None:
    token = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ0123456789"
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    _write_channels(tmp_path, telegram={"enabled": True})
    (tmp_path / "channels-gateway.log").write_text(
        "2026-10-25 12:00:00 WARNING rui.channels.telegram: telegram poll: "
        f"https://api.telegram.org/bot{token}/getUpdates 409 Conflict\n",
        encoding="utf-8")
    check = probes._channels_check(cfg, _gateway())
    assert check.status == "warn"
    assert token not in check.value
    assert token not in check.hint
    assert "<telegram-token>" in check.value


def test_channels_without_an_enabled_channel_is_not_applicable(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    check = probes._channels_check(cfg, _gateway())
    assert check.status == "ok"
    assert "not_applicable" in check.value


def test_console_token_match_is_reported_without_the_value(tmp_path, monkeypatch) -> None:
    cfg = Config(web=WebConfig(auth_token="console-token-do-not-print"))
    cfg.system.data_dir = str(tmp_path)
    saved = tmp_path / "console-token.txt"
    saved.write_text("console-token-do-not-print\n", encoding="utf-8")
    monkeypatch.setenv("RUI_CONSOLE_TOKEN_FILE", str(saved))
    check = probes._console_token_check(cfg)
    assert check.name == "console_token"
    assert check.status == "ok"
    assert "matches web.auth_token" in check.value
    for text in (check.value, check.hint):
        assert "console-token-do-not-print" not in text


def test_console_token_mismatch_is_degraded_and_names_the_fix(tmp_path, monkeypatch) -> None:
    cfg = Config(web=WebConfig(auth_token="console-token-do-not-print"))
    cfg.system.data_dir = str(tmp_path)
    saved = tmp_path / "console-token.txt"
    saved.write_text("stale-console-token\n", encoding="utf-8")
    monkeypatch.setenv("RUI_CONSOLE_TOKEN_FILE", str(saved))
    check = probes._console_token_check(cfg)
    assert check.status == "warn"
    assert "does NOT match" in check.value
    assert "rui token > console-token.txt" in check.hint
    for text in (check.value, check.hint):
        assert "console-token-do-not-print" not in text
        assert "stale-console-token" not in text


def test_console_token_missing_is_not_applicable(tmp_path, monkeypatch) -> None:
    cfg = Config(web=WebConfig(auth_token="console-token-do-not-print"))
    cfg.system.data_dir = str(tmp_path)
    monkeypatch.delenv("RUI_CONSOLE_TOKEN_FILE", raising=False)
    monkeypatch.delenv("RUI_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    check = probes._console_token_check(cfg)
    assert check.status == "ok"
    assert "not_applicable" in check.value
