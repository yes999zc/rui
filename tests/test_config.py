from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from rui.config import Config, DeviceConfig, load_config, save_config
from rui.config.schema import (
    CaptureConfig,
    ProviderConfig,
    ProvidersConfig,
    StageConfig,
    VoiceConfig,
)

REPO = Path(__file__).resolve().parents[1]


def test_defaults_are_sane() -> None:
    cfg = Config()
    assert cfg.version == 3
    # 0.6 M1: the core defaults are neutral. The LX06's 48000/8/6/2/S32 with >>12/>>16
    # is declared by its own manifest (src/rui/plugins/builtin.py), not by the core.
    assert (cfg.audio.capture.rate, cfg.audio.capture.channels) == (16000, 1)
    assert (cfg.audio.capture.mic_channels, cfg.audio.capture.ref_channels) == (1, 0)
    assert (cfg.audio.capture.bits, cfg.audio.capture.mic_shift,
            cfg.audio.capture.ref_shift) == (16, 0, 0)
    assert cfg.audio.voice.rate == 16000
    assert cfg.audio.playback.rate == 16000
    # The client plays mono frames; the adapter rejects stereo, so a default of 2
    # channels would raise on the first reply of a fresh install.
    assert cfg.audio.playback.channels == 1
    # The neutral core ack is the tone, not a spoken word.
    assert cfg.ack.mode == "earcon"
    assert cfg.tools.expose == "domain"


def test_frame_ms_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VoiceConfig(frame_ms=100)


def test_capture_layout_must_add_up() -> None:
    with pytest.raises(ValidationError):
        CaptureConfig(channels=8, mic_channels=6, ref_channels=3)


def test_duplicate_device_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Config(devices=[
            DeviceConfig(id="living", host="10.0.0.2"),
            DeviceConfig(id="living", host="10.0.0.3"),
        ])


def test_chain_must_reference_a_catalog_entry() -> None:
    with pytest.raises(ValidationError):
        ProvidersConfig(llm=StageConfig(chain=["ghost"]))


def test_chain_orders_primary_then_fallback() -> None:
    catalog = {
        "deepseek": ProviderConfig(base_url="https://a", model="deepseek-chat"),
        "backup": ProviderConfig(base_url="https://b", model="qwen"),
    }
    cfg = Config(providers=ProvidersConfig(
        catalog=catalog,
        asr=StageConfig(chain=["deepseek"]),
        tts=StageConfig(chain=["backup"]),
        llm=StageConfig(chain=["deepseek", "backup"]),
    ))
    assert [name for name, _ in cfg.providers.resolve("llm")] == ["deepseek", "backup"]
    assert cfg.providers.resolve("tts")[0][0] == "backup"


def test_duplicate_names_in_a_chain_are_rejected() -> None:
    with pytest.raises(ValidationError):
        StageConfig(chain=["a", "a"])


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
    save_config(path, Config(devices=[DeviceConfig(id="living", host="192.0.2.11", room="客厅")]))
    cfg = load_config(path)
    assert cfg.devices[0].room == "客厅"


def test_shipped_example_config_parses() -> None:
    cfg = load_config(REPO / "config.example.yaml")
    assert cfg.version == 2
    assert cfg.devices[0].adapter == "lx06"


V1_CONFIG = """\
version: 1
web:
  host: 0.0.0.0
  port: 8080
devices:
  - id: living
    adapter: mock
    host: 127.0.0.1
audio:
  sample_rate: 16000
  frame_ms: 20
  aec:
    enabled: true
    filter_len: 4096
    channels: 6
    mic_shift: 8
    ref_shift: 13
  kws_threshold: 0.6
  vad_end_silence_ms: 500
providers:
  asr:
    kind: openai_compat
    base_url: http://asr
    model: sensevoice
    api_key_env: RUI_ASR_KEY
  tts:
    base_url: http://tts
    model: cosyvoice
  llm:
    base_url: http://llm
    model: deepseek-chat
"""


def test_v1_config_is_migrated_in_memory(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(V1_CONFIG, encoding="utf-8")

    cfg = load_config(path)

    assert cfg.version == 2
    # one rate for everything becomes three explicit ones
    assert cfg.audio.capture.rate == 48000
    assert cfg.audio.capture.mic_channels == 6
    assert cfg.audio.capture.ref_channels == 2
    assert cfg.audio.voice.rate == 16000
    assert cfg.audio.voice.vad_end_silence_ms == 500
    # shifts move from `aec` to `capture`, exactly as the v1 file wrote them
    assert cfg.audio.capture.ref_shift == 13
    # providers become catalog entries behind a one-name chain
    assert cfg.providers.llm.chain == ["llm"]
    assert cfg.providers.catalog["asr"].model == "sensevoice"
    # migration is read-only: the file on disk is untouched
    assert path.read_text(encoding="utf-8") == V1_CONFIG


def test_led_defaults_match_the_old_build() -> None:
    from rui.config.schema import LedConfig

    led = LedConfig()
    assert led.enabled is False
    assert (led.gain, led.night_factor) == (1.9, 0.6)
    assert (led.night_start, led.night_end) == (0, 7)
    assert led.fade_ms == 180
    assert led.colors["listening"] == "#004800"
    assert len(led.colors) == 9


def test_led_night_window_must_not_be_empty() -> None:
    from pydantic import ValidationError

    from rui.config.schema import LedConfig

    with pytest.raises(ValidationError):
        LedConfig(night_start=7, night_end=7)


def test_led_yaml_off_key_is_accepted() -> None:
    """PyYAML reads a bare `off:` as False; a hand-written config must still load."""
    from rui.config.schema import LedConfig

    led = LedConfig.model_validate({"colors": {False: "#000000", "thinking": "#000060"}})
    assert led.colors["off"] == "#000000"


def test_exit_words_cover_the_old_build_set() -> None:
    """A session must be dismissable by voice, with the old build's 11 words plus ours."""
    words = set(Config().session.end_words)
    assert {"停止", "别说了", "不听了", "退出", "结束", "再见", "拜拜", "睡了", "睡吧", "晚安",
            "退下吧"} <= words


def test_music_resume_hold_is_short_and_configurable() -> None:
    """6 s of silence after an answer was heard as a long gap (9哥, 16:10)."""
    from rui.config import Config

    assert Config().music.resume_hold_ms == 2500


def test_the_catalog_exposes_the_music_resume_delay() -> None:
    from rui.config import Config
    from rui.config.catalog import document

    fields = {field["path"]: field for field in document(Config())["fields"]}
    field = fields["music.resume_hold_ms"]
    assert (field["min"], field["max"]) == (0, 10000)
    assert field["effect"] == "restart"
