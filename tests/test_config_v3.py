"""Config v3: per-device audio overrides and the multi_device block.

The decisions under test are frozen in `docs/multi-device-design.md` §9/§11:

- the global `audio` block stays the default, `devices[].audio` is a partial override;
- a v2 file loads untouched and keeps v2 semantics (no rewrite, rollback-safe);
- a file only becomes `version: 3` on disk once a v3-only field is actually used;
- every v3 profile is validated against the device list (unknown ids are typos).

Phase 2 wires the fleet: `multi_device.enabled=true` starts one plane per enabled
inbound device, so the old "still runs a single active plane" warning is gone.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rui.config import Config, load_config, save_config
from rui.config.schema import (
    CONFIG_VERSION,
    AecConfig,
    CaptureConfig,
    ConfigError,
    DeviceAudioOverride,
    DeviceVoiceOverride,
    MultiDeviceConfig,
    config_warnings,
    effective_ack_echo_ignore_ms,
    effective_audio,
)

V2_CONFIG = """\
version: 2
devices:
  - id: lx06
    adapter: mock
    host: 192.0.2.10
    room: 客厅
"""

V3_CONFIG = """\
version: 3
audio:
  voice: {followup_delay_ms: 1000, followup_replay: true}
  aec: {enabled: true}
devices:
  - id: lx06
    adapter: mock
    host: 192.0.2.10
    room: 客厅
  - id: dot2
    adapter: mock
    host: 192.0.2.20
    room: 书房
    audio:
      aec: {enabled: false, who: device}
      voice: {followup_delay_ms: 2500, followup_replay: false}
      playback: {prebuffer_ms: 450}
multi_device:
  enabled: true
  default_output: lx06
  arbitration:
    source_priority: [device_wake, kws]
    room_priority: [lx06, dot2]
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------ schema shape

def test_current_schema_version_is_three() -> None:
    assert CONFIG_VERSION == 3
    assert Config().version == 3


def test_multi_device_defaults_match_the_frozen_decisions() -> None:
    md = MultiDeviceConfig()
    assert md.enabled is False  # 0.4.x behaviour is the default (§9.2 D9)
    assert md.max_planes == 4  # §9.2 D4
    assert md.default_output is None
    arb = md.arbitration
    # §9.1 K3: group by source, never compare a device score with a KWS score.
    assert arb.source_priority == ["device_wake", "kws"]
    # §11 Q3: an utterance merge window and a same-word duplicate window are separate.
    assert arb.candidate_merge_window_ms == 400
    assert arb.duplicate_window_ms == 2000
    assert arb.on_no_speech == "end"  # §9.2 D1


def test_aec_ownership_defaults_to_the_controller() -> None:
    # The LX06 path (RUI cancels the array mix) must stay the default (§9.1 K5).
    assert AecConfig().who == "controller"


# --------------------------------------------------------------- v2 compatibility

def test_v2_config_loads_unchanged(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, V2_CONFIG))
    assert cfg.version == 2
    assert cfg.multi_device.enabled is False
    # No overrides: the effective block is exactly the global one.
    assert effective_audio(cfg.audio, cfg.devices[0].audio) == effective_audio(cfg.audio, None)


def test_v2_config_is_not_upgraded_on_save_without_v3_fields(tmp_path: Path) -> None:
    path = _write(tmp_path, V2_CONFIG)
    cfg = load_config(path)
    cfg.web.port = 8081
    save_config(path, cfg)
    assert load_config(path).version == 2
    assert path.read_text(encoding="utf-8").startswith("version: 2")


def test_v2_config_becomes_v3_once_a_device_override_is_used(tmp_path: Path) -> None:
    path = _write(tmp_path, V2_CONFIG)
    cfg = load_config(path)
    cfg.devices[0].audio = DeviceAudioOverride(voice=DeviceVoiceOverride(followup_delay_ms=2500))
    save_config(path, cfg)
    cfg2 = load_config(path)
    assert cfg2.version == 3
    eff = effective_audio(cfg2.audio, cfg2.devices[0].audio)
    assert eff.voice.followup_delay_ms == 2500
    assert eff.voice.followup_replay is True  # inherited from the global default


# ------------------------------------------------------------ per-device overrides

def test_v3_overrides_parse_and_merge_over_the_global_block(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, V3_CONFIG))
    assert cfg.version == 3
    dot2 = next(d for d in cfg.devices if d.id == "dot2")
    assert dot2.audio is not None

    eff = effective_audio(cfg.audio, dot2.audio)
    # Overridden fields come from the device ...
    assert eff.aec.enabled is False
    assert eff.aec.who == "device"
    assert eff.voice.followup_delay_ms == 2500
    assert eff.voice.followup_replay is False
    assert eff.playback.prebuffer_ms == 450
    # ... everything else is inherited, not defaulted to a different value.
    assert eff.capture == CaptureConfig()  # LX06 capture layout untouched
    assert eff.playback.rate == 16000
    assert eff.voice.vad_end_silence_ms == 1000


def test_post_playback_mute_parses_per_device_and_inherits(tmp_path: Path) -> None:
    config = V3_CONFIG.replace(
        "      voice: {followup_delay_ms: 2500, followup_replay: false}",
        "      voice: {followup_delay_ms: 2500, followup_replay: false,"
        " post_playback_mute_ms: 2500}",
    )
    cfg = load_config(_write(tmp_path, config))
    dot2 = next(d for d in cfg.devices if d.id == "dot2")
    assert dot2.audio is not None and dot2.audio.voice is not None
    assert dot2.audio.voice.post_playback_mute_ms == 2500

    eff = effective_audio(cfg.audio, dot2.audio)
    assert eff.voice.post_playback_mute_ms == 2500
    # An untouched device inherits the global default (0 = the 0.4.x behaviour).
    lx06 = next(d for d in cfg.devices if d.id == "lx06")
    assert effective_audio(cfg.audio, lx06.audio).voice.post_playback_mute_ms == 0


def test_post_playback_mute_out_of_range_is_rejected(tmp_path: Path) -> None:
    config = V3_CONFIG.replace(
        "      voice: {followup_delay_ms: 2500, followup_replay: false}",
        "      voice: {followup_delay_ms: 2500, followup_replay: false,"
        " post_playback_mute_ms: 60000}",
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, config))


def test_ack_echo_ignore_ms_parses_per_device_and_inherits(tmp_path: Path) -> None:
    """The spoken-cue echo window is the device's, or the global `ack` value.

    It lives under `voice` (it describes the microphone/echo path) but replaces a
    conversational `ack` setting, so `effective_audio()` deliberately does not resolve
    it - a value it silently ignored would be the "fake knob" this schema forbids.
    """
    config = V3_CONFIG.replace(
        "      voice: {followup_delay_ms: 2500, followup_replay: false}",
        "      voice: {followup_delay_ms: 2500, followup_replay: false,"
        " ack_echo_ignore_ms: 2500}",
    )
    cfg = load_config(_write(tmp_path, config))
    dot2 = next(d for d in cfg.devices if d.id == "dot2")
    lx06 = next(d for d in cfg.devices if d.id == "lx06")
    assert dot2.audio is not None and dot2.audio.voice is not None
    assert dot2.audio.voice.ack_echo_ignore_ms == 2500
    # An explicit value wins over the global default ...
    assert effective_ack_echo_ignore_ms(cfg.ack, dot2) == 2500
    # ... every other device still inherits it.
    assert cfg.ack.echo_ignore_ms == 1200
    assert effective_ack_echo_ignore_ms(cfg.ack, lx06) == 1200
    assert effective_ack_echo_ignore_ms(cfg.ack, None) == 1200
    # Changing the global moves the inheriting device and leaves the override alone.
    cfg.ack.echo_ignore_ms = 900
    assert effective_ack_echo_ignore_ms(cfg.ack, lx06) == 900
    assert effective_ack_echo_ignore_ms(cfg.ack, dot2) == 2500
    # The override must not leak into the audio merge or make it invalid.
    assert effective_audio(cfg.audio, dot2.audio).voice.followup_delay_ms == 2500
    assert not hasattr(effective_audio(cfg.audio, dot2.audio).voice, "ack_echo_ignore_ms")


def test_ack_echo_ignore_ms_null_means_inherit(tmp_path: Path) -> None:
    config = V3_CONFIG.replace(
        "      voice: {followup_delay_ms: 2500, followup_replay: false}",
        "      voice: {followup_delay_ms: 2500, followup_replay: false,"
        " ack_echo_ignore_ms: null}",
    )
    cfg = load_config(_write(tmp_path, config))
    dot2 = next(d for d in cfg.devices if d.id == "dot2")
    assert dot2.audio is not None and dot2.audio.voice is not None
    assert dot2.audio.voice.ack_echo_ignore_ms is None
    assert effective_ack_echo_ignore_ms(cfg.ack, dot2) == cfg.ack.echo_ignore_ms


@pytest.mark.parametrize("value", [-1, 5001])
def test_ack_echo_ignore_ms_out_of_range_is_rejected(tmp_path: Path, value: int) -> None:
    config = V3_CONFIG.replace(
        "      voice: {followup_delay_ms: 2500, followup_replay: false}",
        "      voice: {followup_delay_ms: 2500, followup_replay: false,"
        f" ack_echo_ignore_ms: {value}}}",
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, config))


def test_lx06_keeps_the_controller_aec_path(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, V3_CONFIG))
    lx06 = next(d for d in cfg.devices if d.id == "lx06")
    eff = effective_audio(cfg.audio, lx06.audio)
    assert eff.aec.who == "controller"
    assert eff.aec.enabled is True


def test_unknown_override_key_is_rejected(tmp_path: Path) -> None:
    bad = V3_CONFIG.replace(
        "      playback: {prebuffer_ms: 450}",
        "      playback: {prebuffer_ms: 450}\n      eq: {bass: 3}",
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_a_partial_capture_override_must_still_add_up(tmp_path: Path) -> None:
    bad = V3_CONFIG.replace(
        "      playback: {prebuffer_ms: 450}",
        "      playback: {prebuffer_ms: 450}\n      capture: {channels: 2}",
    )
    # channels=2 with the inherited mic_channels=6/ref_channels=2 is not a layout.
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_unknown_default_output_is_rejected(tmp_path: Path) -> None:
    bad = V3_CONFIG.replace("default_output: lx06", "default_output: ghost")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_disabled_default_output_is_rejected(tmp_path: Path) -> None:
    bad = V3_CONFIG.replace(
        "  - id: lx06\n    adapter: mock\n    host: 192.0.2.10",
        "  - id: lx06\n    enabled: false\n    adapter: mock\n    host: 192.0.2.10",
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_unknown_room_priority_is_rejected(tmp_path: Path) -> None:
    bad = V3_CONFIG.replace("room_priority: [lx06, dot2]", "room_priority: [lx06, ghost]")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_duplicate_room_priority_is_rejected(tmp_path: Path) -> None:
    bad = V3_CONFIG.replace("room_priority: [lx06, dot2]", "room_priority: [lx06, lx06]")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_enabled_multi_device_requires_a_room(tmp_path: Path) -> None:
    bad = V3_CONFIG.replace("    room: 书房\n", "")
    with pytest.raises(ConfigError, match="room"):
        load_config(_write(tmp_path, bad))


def test_source_priority_must_be_a_known_unique_group_order() -> None:
    from rui.config.schema import ArbitrationConfig

    with pytest.raises(ValueError):
        ArbitrationConfig(source_priority=[])
    with pytest.raises(ValueError):
        ArbitrationConfig(source_priority=["device_wake", "device_wake"])
    with pytest.raises(ValueError):
        ArbitrationConfig(source_priority=["microphone"])


def test_a_future_version_is_still_rejected() -> None:
    with pytest.raises(ValueError):
        Config(version=CONFIG_VERSION + 1)


# ------------------------------------------------------------------- warnings

def test_warnings_flag_double_cancellation(tmp_path: Path) -> None:
    # who=device without switching RUI's own canceller off: two cancellers in series.
    bad = V3_CONFIG.replace("      aec: {enabled: false, who: device}", "      aec: {who: device}")
    messages = config_warnings(load_config(_write(tmp_path, bad)))
    assert any("aec.who=device" in m for m in messages)
    # The explicit combination is clean and must not warn.
    clean = config_warnings(load_config(_write(tmp_path, V3_CONFIG)))
    assert not any("aec.who=device" in m for m in clean)


def test_warnings_no_longer_claim_a_single_active_plane(tmp_path: Path) -> None:
    # Phase 2 starts every enabled inbound device, so the old warning would be a lie.
    messages = config_warnings(load_config(_write(tmp_path, V3_CONFIG)))
    assert not any("single active audio plane" in m for m in messages)


def test_warnings_flag_too_many_planes(tmp_path: Path) -> None:
    config = V3_CONFIG.replace("enabled: true\n  default_output", "enabled: true\n  max_planes: 1\n  default_output")
    messages = config_warnings(load_config(_write(tmp_path, config)))
    assert any("max_planes=1" in m for m in messages)


def test_clean_v2_config_has_no_warnings(tmp_path: Path) -> None:
    assert config_warnings(load_config(_write(tmp_path, V2_CONFIG))) == []
