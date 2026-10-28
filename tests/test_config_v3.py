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
    AckConfig,
    AecConfig,
    AudioDefaults,
    CaptureConfig,
    ConfigError,
    DeviceAudioOverride,
    DeviceVoiceOverride,
    MultiDeviceConfig,
    config_warnings,
    device_audio_defaults,
    effective_ack_echo_ignore_ms,
    effective_audio,
    effective_device_ack_echo_ignore_ms,
    effective_device_audio,
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
    # §9.2 D4: raised 4 -> 5 on 2026-10-28 for lx06 + four dot2; the ge/le window
    # (1..8) is unchanged, see test_max_planes_window_stays_one_to_eight.
    assert md.max_planes == 5
    assert md.default_output is None
    arb = md.arbitration
    # §9.1 K3: group by source, never compare a device score with a KWS score.
    assert arb.source_priority == ["device_wake", "kws"]
    # §11 Q3: an utterance merge window and a same-word duplicate window are separate.
    assert arb.candidate_merge_window_ms == 400
    assert arb.duplicate_window_ms == 2000
    assert arb.on_no_speech == "end"  # §9.2 D1


def test_max_planes_window_stays_one_to_eight() -> None:
    # D4 only moved the default (4 -> 5); the accepted window did not change.
    assert MultiDeviceConfig(max_planes=1).max_planes == 1
    assert MultiDeviceConfig(max_planes=8).max_planes == 8
    for bad in (0, 9):
        with pytest.raises(ValueError):
            MultiDeviceConfig(max_planes=bad)


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


def _fleet_config(count: int) -> Config:
    """`count` enabled inbound devices under the default multi_device block."""
    return Config(
        multi_device={"enabled": True},
        devices=[
            {
                "id": f"dev{i}",
                "adapter": "mock",
                "host": f"192.0.2.{10 + i}",
                "room": f"房间{i}",
            }
            for i in range(count)
        ],
    )


def test_five_planes_fit_within_the_default_and_the_sixth_is_refused() -> None:
    # D4 (2026-10-28): lx06 + four dot2 = 5 planes, exactly the default budget.
    assert not any("max_planes" in m for m in config_warnings(_fleet_config(5)))
    # The sixth is refused and named - never silently truncated, never queued.
    messages = config_warnings(_fleet_config(6))
    assert any(
        "multi_device.max_planes=5 but 6 devices are enabled" in m for m in messages
    )


def test_warning_names_the_living_room_when_default_output_is_unset() -> None:
    """D-013: the warning must describe the implicit living-room default, not recency."""
    with_living = Config(
        multi_device={"enabled": True},
        devices=[
            {"id": "living", "adapter": "mock", "host": "192.0.2.10", "room": "客厅"},
            {"id": "study", "adapter": "mock", "host": "192.0.2.11", "room": "书房"},
        ],
    )
    messages = config_warnings(with_living)
    assert any("living" in message and "客厅" in message for message in messages)
    assert not any("no enabled device" in message for message in messages)

    no_living = Config(
        multi_device={"enabled": True},
        devices=[
            {"id": "study", "adapter": "mock", "host": "192.0.2.11", "room": "书房"},
        ],
    )
    messages = config_warnings(no_living)
    assert any(
        "no enabled device" in message and "most recently active room" in message
        for message in messages
    )


def test_clean_v2_config_has_no_warnings(tmp_path: Path) -> None:
    assert config_warnings(load_config(_write(tmp_path, V2_CONFIG))) == []


# --------------------------------------- 0.6 M1: the three-layer audio merge
#
# docs/roadmap-0.6.md M1: device manifest default < global config < devices[].audio.
# The core defaults became neutral, so an LX06 config now gets its hardware layout
# from the built-in manifest. These tests pin the new layering AND the promised
# non-regression: an LX06-shaped config resolves field-for-field as it did before M1.

LX06_CONFIG = """\
version: 3
devices:
  - id: lx06
    adapter: lx06
    host: 192.0.2.10
    room: 客厅
"""

# The production/template shape: the config states the LX06 numbers itself. The 16 kHz
# capture rate is a deliberate bandwidth choice (the client JSON-encodes raw bytes),
# so it must keep beating the manifest's 48 kHz hardware rate.
LX06_TEMPLATE_CONFIG = """\
version: 3
audio:
  capture:
    rate: 16000
    channels: 8
    mic_channels: 6
    ref_channels: 2
    bits: 32
    mic_shift: 12
    ref_shift: 16
  playback:
    rate: 16000
    channels: 1
    queue_ms: 200
    fade_out_ms: 120
    credit_window: 8
    target_silence_ms: 300
  aec:
    enabled: true
    filter_len: 4096
    cancel: mix
devices:
  - id: lx06
    adapter: lx06
    host: 192.0.2.10
    room: 客厅
"""

# Captured from the pre-M1 code path (schema defaults were the LX06 layout): the golden
# values an existing LX06 config must keep resolving after M1. Written out field by
# field on purpose - a typo in a default must fail this test, not be compared against
# itself.
_VOICE_GOLDEN = {
    "barge_in": True,
    "followup_delay_ms": 1000,
    "followup_max_wait_ms": 8000,
    "followup_replay": True,
    "frame_ms": 20,
    "kws_chunk": 8,
    "kws_enabled": False,
    "kws_keywords_file": "",
    "kws_model_dir": "",
    "kws_num_threads": 1,
    "kws_probe_file": "",
    "post_playback_mute_ms": 0,
    "rate": 16000,
    "vad_end_silence_ms": 1000,
    "vad_min_speech_ms": 200,
    "vad_model_path": "",
    "vad_preroll_ms": 350,
    "wake_preroll_ms": 500,
    "wake_skip_ms": 300,
}
_AEC_GOLDEN = {"cancel": "mix", "enabled": True, "filter_len": 4096, "who": "controller"}
_PLAYBACK_GOLDEN = {
    "channels": 1,
    "credit_window": 8,
    "fade_out_ms": 120,
    "prebuffer_ms": 0,
    "queue_ms": 200,
    "rate": 16000,
    "target_silence_ms": 300,
}
# The old core CaptureConfig() values, now declared by the LX06 manifest.
_CAPTURE_GOLDEN = {
    "bits": 32,
    "channels": 8,
    "mic_channels": 6,
    "mic_shift": 12,
    "rate": 48000,
    "ref_channels": 2,
    "ref_shift": 16,
}
_PRE_M1_SCHEMA_DEFAULT = {
    "aec": _AEC_GOLDEN,
    "capture": _CAPTURE_GOLDEN,
    "playback": _PLAYBACK_GOLDEN,
    "voice": _VOICE_GOLDEN,
}
_PRE_M1_TEMPLATE = {
    "aec": _AEC_GOLDEN,
    "capture": {**_CAPTURE_GOLDEN, "rate": 16000},
    "playback": _PLAYBACK_GOLDEN,
    "voice": _VOICE_GOLDEN,
}


def test_lx06_manifest_declares_the_old_core_defaults() -> None:
    from rui.plugins import builtin_plugin

    plugin = builtin_plugin("xiaoai-lx06")
    assert plugin is not None
    defaults = plugin.devices[0].audio_defaults
    assert defaults is not None and defaults.capture is not None
    assert defaults.capture.model_dump(exclude_none=True) == _CAPTURE_GOLDEN
    assert defaults.ack_echo_ignore_ms == 1200
    assert defaults.playback is None, "playback stays the neutral pipeline choice"


def test_lx06_shaped_config_without_an_audio_block_resolves_as_before_m1(
        tmp_path: Path) -> None:
    """Golden: the old default-shaped config resolves every field as it did before M1."""
    cfg = load_config(_write(tmp_path, LX06_CONFIG))
    device = cfg.devices[0]
    defaults = device_audio_defaults(device, cfg.system.data_dir)
    assert defaults is not None, "the built-in LX06 manifest declares audio_defaults"
    eff = effective_audio(cfg.audio, device.audio, defaults)
    assert eff.model_dump() == _PRE_M1_SCHEMA_DEFAULT
    assert effective_ack_echo_ignore_ms(cfg.ack, device, defaults) == 1200
    # The wrapper (what the plane/doctor/xiaoai builder call) resolves the same layers.
    assert effective_device_audio(cfg, device).model_dump() == _PRE_M1_SCHEMA_DEFAULT


def test_lx06_template_shaped_config_resolves_as_before_m1(tmp_path: Path) -> None:
    """Golden: an existing production config (explicit LX06 numbers) does not drift."""
    cfg = load_config(_write(tmp_path, LX06_TEMPLATE_CONFIG))
    device = cfg.devices[0]
    eff = effective_device_audio(cfg, device)
    assert eff.model_dump() == _PRE_M1_TEMPLATE
    # Written values win over the manifest, field by field.
    assert eff.capture.rate == 16000
    assert eff.capture.channels == 8
    assert effective_ack_echo_ignore_ms(
        cfg.ack, device, device_audio_defaults(device, cfg.system.data_dir)) == 1200


def test_an_explicit_global_capture_field_beats_the_manifest_default(tmp_path: Path) -> None:
    config = LX06_CONFIG.replace(
        "version: 3\n",
        "version: 3\naudio:\n  capture:\n    rate: 24000\n",
    )
    cfg = load_config(_write(tmp_path, config))
    eff = effective_device_audio(cfg, cfg.devices[0])
    assert eff.capture.rate == 24000      # the config states it
    assert eff.capture.channels == 8      # untouched fields still come from the plug
    assert eff.capture.mic_channels == 6
    assert eff.capture.ref_channels == 2


def test_a_device_override_beats_both_lower_layers(tmp_path: Path) -> None:
    config = LX06_CONFIG + (
        "    audio:\n"
        "      capture: {rate: 16000, channels: 2, mic_channels: 1, ref_channels: 1}\n"
        "      playback: {rate: 48000}\n"
    )
    cfg = load_config(_write(tmp_path, config))
    eff = effective_device_audio(cfg, cfg.devices[0])
    assert (eff.capture.rate, eff.capture.channels) == (16000, 2)
    assert (eff.capture.mic_channels, eff.capture.ref_channels) == (1, 1)
    assert eff.playback.rate == 48000


def test_a_plug_without_audio_defaults_keeps_the_neutral_core(tmp_path: Path) -> None:
    # V2_CONFIG's device uses the mock adapter, i.e. no plug manifest: the documented
    # shim for a pre-0.6 plug is the neutral core layout, not an error.
    cfg = load_config(_write(tmp_path, V2_CONFIG))
    device = cfg.devices[0]
    assert device_audio_defaults(device, cfg.system.data_dir) is None
    eff = effective_device_audio(cfg, device)
    assert eff.capture == CaptureConfig()
    assert (eff.capture.rate, eff.capture.channels, eff.capture.ref_channels) == (
        16000, 1, 0)


def test_the_ack_window_resolves_its_three_layers(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, V2_CONFIG))
    device = cfg.devices[0]
    defaults = AudioDefaults(ack_echo_ignore_ms=2500)
    # The config states no ack.echo_ignore_ms, so the manifest's measured tail beats
    # the neutral core 1200.
    assert effective_ack_echo_ignore_ms(cfg.ack, device, defaults) == 2500
    # A config that states the global value wins over the manifest ...
    cfg.ack = AckConfig(echo_ignore_ms=900)
    assert effective_ack_echo_ignore_ms(cfg.ack, device, defaults) == 900
    # ... and a device override wins over both (the 0.5.3 self-hearing knob).
    device.audio = DeviceAudioOverride(
        voice=DeviceVoiceOverride(ack_echo_ignore_ms=3000))
    assert effective_ack_echo_ignore_ms(cfg.ack, device, defaults) == 3000


def test_saving_does_not_materialize_the_neutral_audio_defaults(tmp_path: Path) -> None:
    """A console save must not freeze the core defaults over the plug's layout.

    The console write path revalidates a full model dump before saving, so
    `model_fields_set` is empty information there; `save_config` keeps the previous
    file's "not configured" state instead.
    """
    path = _write(tmp_path, LX06_CONFIG)
    cfg = load_config(path)
    assert effective_device_audio(cfg, cfg.devices[0]).capture.rate == 48000

    save_config(path, Config.model_validate(cfg.model_dump(mode="json")))

    from ruamel.yaml import YAML

    document = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    assert "audio" not in document, "the neutral core defaults must not be persisted"
    # The global cue window is the middle layer of the same merge: its core default
    # must not be frozen either, or a plug's measured tail could never apply.
    assert "echo_ignore_ms" not in document["ack"]
    reloaded = load_config(path)
    assert effective_device_audio(reloaded, reloaded.devices[0]).capture.rate == 48000
    assert effective_device_ack_echo_ignore_ms(reloaded, reloaded.devices[0]) == 1200


def test_saving_keeps_a_stated_ack_window(tmp_path: Path) -> None:
    path = _write(tmp_path, LX06_CONFIG)
    cfg = load_config(path)
    cfg.ack = AckConfig(echo_ignore_ms=900)   # the operator changed the global window
    save_config(path, Config.model_validate(cfg.model_dump(mode="json")))

    from ruamel.yaml import YAML

    document = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    assert document["ack"]["echo_ignore_ms"] == 900
    reloaded = load_config(path)
    assert effective_device_ack_echo_ignore_ms(reloaded, reloaded.devices[0]) == 900


def test_an_explicit_cue_window_at_the_default_is_preserved(tmp_path: Path) -> None:
    # A file that already states the window keeps it, even at the core default: it is
    # an explicit conversational choice (the production template writes 1200).
    path = _write(tmp_path, LX06_TEMPLATE_CONFIG.replace(
        "devices:\n", "ack:\n  echo_ignore_ms: 1200\ndevices:\n"))
    cfg = load_config(path)
    save_config(path, Config.model_validate(cfg.model_dump(mode="json")))

    from ruamel.yaml import YAML

    document = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    assert document["ack"]["echo_ignore_ms"] == 1200


def test_saving_keeps_an_explicit_capture_layout(tmp_path: Path) -> None:
    path = _write(tmp_path, LX06_TEMPLATE_CONFIG)
    cfg = load_config(path)
    save_config(path, Config.model_validate(cfg.model_dump(mode="json")))

    text = path.read_text(encoding="utf-8")
    assert "rate: 16000" in text
    reloaded = load_config(path)
    eff = effective_device_audio(reloaded, reloaded.devices[0])
    assert eff.capture.rate == 16000
    assert eff.capture.channels == 8
