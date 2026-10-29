"""Config is the single source of truth; JSON Schema is generated from these models.

Version history:
  1 - one flat `audio` block with a single sample rate and channel count.
      Wrong for every real speaker we measured: one captures 48 kHz / 8 ch / 32 bit
      and plays 48 kHz / 2 ch, while the voice pipeline runs at 16 kHz.
  2 - rates and channel counts are explicit per direction (`capture`, `voice`,
      `playback`); providers are a catalog plus an ordered fallback chain per stage.
  3 - optional `devices[].audio` overrides and the `multi_device` block. Nothing is
      mandatory: a v2 file loads with the same behaviour as before, and an on-disk
      file is only upgraded to `version: 3` when a v3-only field is actually used,
      so a rollback to a 0.4.x image keeps working without editing the file.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CONFIG_VERSION = 3

log = logging.getLogger(__name__)

ExposeMode = Literal["domain", "whitelist", "all"]
STAGES = ("asr", "tts", "llm")


class ConfigError(ValueError):
    """Raised when a config file cannot be parsed or validated."""


class SystemConfig(BaseModel):
    data_dir: str = "/var/lib/rui"
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    # The zone the injected clock line ("现在是...") is rendered in. Empty means the
    # process's local zone, which used to be the only behaviour. That breaks silently in
    # a container: Docker inherits no TZ by default, so a NAS answers "现在几点" eight
    # hours off while the reminders (fixed to Asia/Shanghai) disagree. A config field is
    # the one fix that does not need a new image or a mounted /etc/localtime.
    timezone: str = ""

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            return v
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"unknown IANA timezone {v!r}; use one like Asia/Shanghai"
            ) from exc
        return v


class OutboundSecurityConfig(BaseModel):
    """Outbound SSRF policy (docs/security.md).

    Defaults are the self-hosted case: LAN/private services are reachable, loopback is
    not, and cloud metadata/link-local is blocked regardless. `extra_allowed_*` is an
    explicit escape hatch for a private hostname or range; `blocked_*` always wins.
    """

    enabled: bool = True
    allow_private: bool = True
    allow_loopback: bool = False
    extra_allowed_hosts: list[str] = Field(default_factory=list)
    extra_allowed_cidrs: list[str] = Field(default_factory=list)
    blocked_hosts: list[str] = Field(default_factory=list)
    blocked_cidrs: list[str] = Field(default_factory=list)

    @field_validator("extra_allowed_hosts", "blocked_hosts")
    @classmethod
    def _bare_hosts(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for raw in values:
            host = str(raw or "").strip().lower().rstrip(".")
            if not host:
                raise ValueError("host allow/block entries must not be empty")
            if any(char in host for char in "/:@[]"):
                raise ValueError(f"{host!r} is not a bare hostname; use a CIDR for IPs")
            if host not in out:
                out.append(host)
        return out

    @field_validator("extra_allowed_cidrs", "blocked_cidrs")
    @classmethod
    def _known_cidrs(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for raw in values:
            try:
                network = ipaddress.ip_network(str(raw or "").strip(), strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid CIDR {raw!r}") from exc
            text = str(network)
            if text not in out:
                out.append(text)
        return out


class SecurityConfig(BaseModel):
    outbound: OutboundSecurityConfig = Field(default_factory=OutboundSecurityConfig)


class WebConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    # Empty token used to mean "trusted LAN, no auth". Security audit 2026-10-23:
    # that made every acting endpoint one LAN packet away. An empty token on a
    # non-loopback bind now refuses to start unless `allow_anonymous` is set
    # explicitly (development machines only).
    auth_token: str = ""
    allow_anonymous: bool = False
    # Which console `/` opens (M10). Kept for config compatibility only: 0.8.0 removed
    # the old panel shell, so the value is ignored, `/` always answers a 302 to `/v2/`,
    # and there is no rollback switch. Additive and defaulted: a config written before
    # this field existed loads as "v2", and CONFIG_VERSION stays 3 so an older image can
    # still read a file that names `console: v2`.
    console: Literal["legacy", "v2"] = "v2"


LED_STATES = (
    "off", "listening", "thinking", "speaking", "wake", "error",
    "music", "music_paused", "muted",
)

# Peak colours, straight from the two-day deployment. Brightness is applied when the
# frame is built, never stored pre-multiplied.
LED_COLORS = {
    "off": "#000000",
    "listening": "#004800",
    "thinking": "#000060",
    "speaking": "#003048",
    "wake": "#484848",
    "error": "#480000",
    "music": "#481860",
    "music_paused": "#602800",
    "muted": "#3C0000",
}


class LedConfig(BaseModel):
    """The 18-LED ring. Per device: a second speaker may have no ring at all."""

    enabled: bool = False
    gain: float = Field(default=1.9, ge=0.1, le=5.0)
    night_factor: float = Field(default=0.6, ge=0.05, le=1.0)
    # `start <= hour < end`; start > end crosses midnight. start == end is rejected so
    # "never dim" has one spelling: night_factor 1.0.
    night_start: int = Field(default=0, ge=0, le=23)
    night_end: int = Field(default=7, ge=0, le=23)
    fade_ms: int = Field(default=180, ge=0, le=600)
    colors: dict[str, str] = Field(default_factory=lambda: dict(LED_COLORS))

    @model_validator(mode="after")
    def _sane(self) -> LedConfig:
        if self.night_start == self.night_end:
            raise ValueError("night_start and night_end are equal; set night_factor 1.0 instead")
        unknown = set(self.colors) - set(LED_STATES)
        if unknown:
            raise ValueError(f"unknown LED states: {sorted(unknown)}; use {list(LED_STATES)}")
        for name, value in self.colors.items():
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                raise ValueError(f"led.colors.{name} must be #RRGGBB, got {value!r}")
        return self

    @field_validator("colors", mode="before")
    @classmethod
    def _yaml_off_is_a_boolean(cls, value: object) -> object:
        # PyYAML reads a bare `off:` key as False and `on:` as True. A hand-written
        # config must not fail with "keys must be strings".
        if isinstance(value, dict):
            return {("off" if k is False else "on" if k is True else k): v
                    for k, v in value.items()}
        return value


AecWho = Literal["device", "controller", "both", "none"]


class DeviceAecOverride(BaseModel):
    """AEC ownership for one device (config v3, docs/multi-device-design.md K5)."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    who: AecWho | None = None


class DeviceVoiceOverride(BaseModel):
    """The follow-up window, per device: a device-AEC plug keeps echo for seconds."""

    model_config = ConfigDict(extra="forbid")
    followup_delay_ms: int | None = Field(default=None, ge=0, le=10000)
    followup_replay: bool | None = None
    # Phase 2: after this device's output owner stops speaking, drop its microphone
    # for this long (no VAD/ASR) while playback-time barge stays available. 0 = the
    # old behaviour; a device-AEC plug (ref_channels=0) wants ~2500 because its
    # residual echo outlives the follow-up window (dot2 #7, docs/multi-device-design.md
    # §12.6). Effective only once the plane runs with this device's effective audio.
    post_playback_mute_ms: int | None = Field(default=None, ge=0, le=10000)
    # How long to hold the gate after this device's own spoken cue, replacing the global
    # `ack.echo_ignore_ms`. Under `voice` because it describes this device's microphone
    # (a device-AEC plug keeps its cue's residual echo far longer than the 1200 ms the
    # reference speaker needs), but the value it overrides is the conversational `ack` setting.
    # None = inherit. Resolved by `effective_ack_echo_ignore_ms()`, not by
    # `effective_audio()` (the `ack` block is deliberately outside `audio`).
    ack_echo_ignore_ms: int | None = Field(default=None, ge=0, le=5000)


class DeviceCaptureOverride(BaseModel):
    """Hardware capture layout; every field falls back to `audio.capture`."""

    model_config = ConfigDict(extra="forbid")
    rate: int | None = Field(default=None, ge=8000, le=96000)
    channels: int | None = Field(default=None, ge=1, le=16)
    mic_channels: int | None = Field(default=None, ge=1, le=16)
    ref_channels: int | None = Field(default=None, ge=0, le=16)
    bits: Literal[16, 32] | None = None
    mic_shift: int | None = Field(default=None, ge=0, le=31)
    ref_shift: int | None = Field(default=None, ge=0, le=31)


class DevicePlaybackOverride(BaseModel):
    """Playback transport quirks (a batching adapter needs a prebuffer)."""

    model_config = ConfigDict(extra="forbid")
    rate: int | None = Field(default=None, ge=8000, le=96000)
    channels: int | None = Field(default=None, ge=1, le=8)
    prebuffer_ms: int | None = Field(default=None, ge=0, le=1000)


class DeviceAudioOverride(BaseModel):
    """Per-device overrides of the global `audio` block (config v3).

    The global block is the default; a device names only what its hardware changes.
    `extra="forbid"` is deliberate: `devices[0].audio.eq` looks plausible and would
    otherwise be silently ignored, which is the "fake knob" failure this repo keeps
    paying for. EQ/limiter/trim stay in plug `params`, not in the core schema (K5).
    """

    model_config = ConfigDict(extra="forbid")
    aec: DeviceAecOverride | None = None
    voice: DeviceVoiceOverride | None = None
    capture: DeviceCaptureOverride | None = None
    playback: DevicePlaybackOverride | None = None


class DeviceConfig(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    # Required since 0.8.0: the core has no device default and never guesses a
    # model. A name, not an enum - a plug manifest declares it as its device id and
    # the registry registers it, so config validation never rejects an adapter a
    # future build adds. Removed spellings are rejected in config.store.migrate
    # before this model sees them.
    adapter: str = Field(min_length=1, max_length=64)
    host: str
    room: str = ""
    enabled: bool = True
    wake_words: list[str] = Field(default_factory=lambda: ["你好小瑞"])
    volume: int = Field(default=60, ge=0, le=100)
    # Inbound transports: the device's client dials us (ws://<server>:<port>/?token=...).
    # None means "adapter default". The token value lives in the environment only.
    listen_port: int | None = Field(default=None, ge=1, le=65535)
    token_env: str = "RUI_DEVICE_TOKEN"
    # Development only. Some device boot scripts hand the client a bare ws://host:port
    # with no query string, so a tokenless listener is the only way to test against a
    # speaker whose boot script cannot be edited. Off by default and it says so in the
    # log: anyone on the LAN can open the microphone while it is on.
    allow_no_token: bool = False
    led: LedConfig = Field(default_factory=LedConfig)
    # Plug-declared, non-secret values (see docs/install-wizard-design.md §7.1).
    # The reserved key ``plug_id`` identifies which manifest owns the device when two
    # models share one adapter. Old configs without this key load as ``{}``.
    params: dict[str, Any] = Field(default_factory=dict)

    # v3: overrides of the global `audio` block; absent means "inherit everything".
    audio: DeviceAudioOverride | None = None

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("device id must be alphanumeric (dash/underscore allowed)")
        return v


class CaptureConfig(BaseModel):
    """What arrives from the device; neutral by default (0.6, docs/roadmap-0.6.md M1).

    The core has no device in it: the default layout is the neutral one a generic
    int16 mono microphone delivers (16 kHz, 1 channel, no reference, no shift). A
    speaker that differs declares its own layout in its plug manifest's
    "audio_defaults" block, which is authoritative for the model's hardware
    (core default < global config < manifest < devices[].audio; `rate` is the one
    negotiated field, see `AudioDefaults`). A multi-mic speaker, for instance,
    declares S32_LE / 8 interleaved channels / 48 kHz (ch0..ch5 mic array, ch6/ch7
    playback reference; mic full scale 2**23, reference 2**28).
    """

    rate: int = Field(default=16000, ge=8000, le=96000)
    channels: int = Field(default=1, ge=1, le=16)
    mic_channels: int = Field(default=1, ge=1, le=16)
    ref_channels: int = Field(default=0, ge=0, le=16)
    bits: Literal[16, 32] = 16
    # A 16-bit neutral wire needs no scaling; a 32-bit device (a measured array) declares the
    # headroom it measured. Level-matching clips loud speech, so a fresh install that
    # never edits this file must not start there.
    mic_shift: int = Field(default=0, ge=0, le=31)
    ref_shift: int = Field(default=0, ge=0, le=31)

    @model_validator(mode="after")
    def _layout_adds_up(self) -> CaptureConfig:
        if self.mic_channels + self.ref_channels != self.channels:
            raise ValueError(
                f"mic_channels({self.mic_channels}) + ref_channels({self.ref_channels}) "
                f"must equal channels({self.channels})"
            )
        return self


class VoiceConfig(BaseModel):
    """The AEC/KWS/VAD rate. Downsample from capture; never run DSP at 48 kHz."""

    rate: int = Field(default=16000, ge=8000, le=48000)
    frame_ms: int = Field(default=20, ge=10, le=40)
    vad_min_speech_ms: int = Field(default=200, ge=50, le=2000)
    # The old deployment closes the first request after a wake at 1.0 s, and only
    # later turns at 0.5 s. Every turn here is wake-initiated (the gate disarms
    # after one segment), so the first-segment value is the only one that applies;
    # 600 ms cut a person who pauses mid-sentence (old incident 15:53:54).
    vad_end_silence_ms: int = Field(default=1000, ge=100, le=3000)
    # Audio kept before the *detected* onset. Silero reports speech a little late, so a
    # small value clips the first syllable: measured 20 rounds, "今天天气怎么样" arrived as
    # "天天气怎么样" in 20/20 at 200 ms, and 0/20 at 350 ms and 500 ms.
    vad_preroll_ms: int = Field(default=350, ge=0, le=1000)
    # The wake phrase's own tail, dropped after the KWS fires.
    wake_skip_ms: int = Field(default=300, ge=0, le=1000)
    barge_in: bool = True
    # Wake word. Off unless a model directory is configured: the model is a download,
    # not something a pip install can guess.
    kws_enabled: bool = False
    kws_model_dir: str = ""
    kws_keywords_file: str = ""
    # A second spotter on the same stream with a lower threshold, which only logs. The old
    # deployment needed it to tell "the threshold is too high" from "the audio is too
    # quiet" - the two failures look identical from the outside, and guessing between them
    # costs a device session each time.
    kws_probe_file: str = ""
    kws_chunk: int = Field(default=8, ge=4, le=16)
    # 2 threads on the N100 kept three ONNX workers at 100%; one is enough for KWS
    # and leaves the other cores to AEC/VAD/control.
    kws_num_threads: int = Field(default=1, ge=1, le=4)
    # Standby KWS gate (task-6/9, docs/standby-cpu-0.8.7.md). In true standby - no wake,
    # no turn, no playback - feed the streaming spotter only while the microphone is
    # above the silence line, max(standby_kws_db, floor_db + 6 dB); a steady room then
    # costs no model inferences. `floor_db` is tracked from idle frames only (fast down,
    # slow EMA up), so a turn's or playback's own audio cannot raise it. The gate reopens
    # on the first frame at/above the line and replays the `wake_preroll_ms` ring into
    # the spotter, so the wake word's onset is not lost. `standby_kws_db` is the
    # absolute lower bound: it only matters in very quiet rooms; `standby_kws_hold_ms`
    # is the main knob. 0 dBFS is full scale; -100 dBFS is digital silence here.
    standby_kws_gate: bool = True
    standby_kws_db: float = Field(default=-60.0, ge=-90.0, le=-20.0)
    # Continuous quiet needed before the spotter is suspended; this is the main tuning
    # knob. One second does not add wake latency (the gate reopens on the first speech
    # frame); it only stops a pause between words from suspending it. 0 suspends on the
    # first quiet frame (more saving, more churn).
    standby_kws_hold_ms: int = Field(default=1000, ge=0, le=10000)
    # Turn segmentation. Empty path disables it (wake fires, nothing is transcribed).
    vad_model_path: str = ""
    # Audio kept from *before* the KWS hit and replayed into the gate afterwards. The
    # streaming KWS reports a hit ~0.5 s late, so without this the first words of a
    # request spoken straight after the wake word never reach the transcriber.
    wake_preroll_ms: int = Field(default=500, ge=0, le=1500)
    # Follow-up gate: how long after the pacer drains before the microphone reopens
    # for the next sentence. 1000 ms is the reference speaker's measured echo tail; a device-AEC
    # plug (no reference, RUI AEC off) keeps residual echo on the mic for seconds.
    followup_delay_ms: int = Field(default=1000, ge=0, le=10000)
    # Replay the audio caught between playback end and the gate opening into the
    # transcriber. Turn off for a device-AEC plug whose residual outlasts the delay.
    followup_replay: bool = True
    # How long the follow-up gate may stay open *after* it opens. The gate was armed for
    # `session.timeout_s` (15 s), so a farewell at 04:15 still accepted speech at
    # 04:15:43 with no wake word - "session closed" had become an open listening window.
    # The plane now arms for min(session.timeout_s * 1000, this); 0 = no follow-up
    # window at all (every turn needs the wake word). Eight seconds keeps a natural
    # second sentence without turning a closed session back into a listener.
    followup_max_wait_ms: int = Field(default=8000, ge=0, le=60000)
    # Phase 2 (docs/multi-device-design.md §12.6): after our own output drains, drop
    # the microphone for this long (no VAD/ASR) so a device-AEC plug's residual echo
    # cannot become a turn. 0 keeps the 0.4.x behaviour; a per-device override wins.
    post_playback_mute_ms: int = Field(default=0, ge=0, le=10000)

    @property
    def frame_samples(self) -> int:
        return self.rate * self.frame_ms // 1000


class PlaybackConfig(BaseModel):
    """Speech and music leave at the rate the pipeline runs at: 16 kHz, mono.

    The capture/ASR/TTS side is 16 kHz, the reference client plays mono frames (the
    adapter rejects anything else), and asking for 48 kHz/2 ch here only produced
    a config that looked right and raised on the first reply. The device's own
    ALSA layer resamples; the wire format is what this number describes.
    """

    rate: int = Field(default=16000, ge=8000, le=96000)
    channels: int = Field(default=1, ge=1, le=8)
    queue_ms: int = Field(default=200, ge=20, le=1000)
    fade_out_ms: int = Field(default=120, ge=0, le=500)
    # Blocks (100 ms each) the plane may run ahead of the device's own "consumed"
    # reports. Measured on a real speaker: the report fires when a block enters aplay's stdin
    # pipe, not when ALSA plays it, so a small window keeps that pipe full and every
    # write waits ~100 ms for a drain (228 blocked writes / 30 s at 2, 130 at 2 with
    # the clock too, 0 at 8). The window is a leak bound; the clock paces. 0 leaves
    # only the clock.
    credit_window: int = Field(default=8, ge=0, le=50)
    # P2.9: how much reply audio the plane waits for before it hands the first block
    # to the device. Zero (default) keeps the reference path exactly as it was: its control
    # plane and TTS deliver a sentence in a burst, so waiting only adds
    # first_response latency. A transport that delivers the reply in batches ~400 ms
    # apart (issue #1, Echo Dot 2 adapter) underruns a 250-320 ms prebuffer; set this
    # to ~450 there. The wait is bounded by prebuffer_ms + 250 ms so a short reply
    # still starts. See docs/playback-path.md §6.
    prebuffer_ms: int = Field(default=0, ge=0, le=1000)
    # Acceptance target: fully silent within this many ms of the user starting to speak.
    target_silence_ms: int = Field(default=300, ge=50, le=1500)


class AecConfig(BaseModel):
    enabled: bool = True
    # Taps at `voice.rate`: 4096 taps = 256 ms of echo tail at 16 kHz.
    filter_len: int = Field(default=4096, ge=256, le=16384)
    # "mix" = one canceller on the array mix (the old deployment's topology, 25.2 dB at
    # mysoftvol 170); "per_channel" = six cancellers, better diagnostics, worse ERLE and
    # 6x the CPU. Measured on this speaker 2026-10-22.
    cancel: Literal["mix", "per_channel"] = "mix"
    # v3: who runs the canceller. "controller" is the reference topology we measured
    # (RUI cancels on the array mix); "device" means the plug already cancelled and
    # RUI must not run a second one. "none" is ASR-only (no barge), "both" is an
    # experiment. `effective_audio()` below resolves a per-device override.
    who: AecWho = "controller"


class AckConfig(BaseModel):
    """What the speaker does the instant the wake word fires.

    Kept out of `audio` because it is a conversational decision, not a DSP one: the same
    plane serves a cue, a spoken word or silence depending on what the room prefers.

    `max_ms` is the whole point of having a `tts` mode at all. A spoken acknowledgement
    mutes the reference speaker's microphone for about 1.2 s plus its own length, so the cap is enforced
    when the cue is rendered (anything longer is refused and the cue falls back to a
    tone), not documented and hoped for.
    """

    mode: Literal["none", "earcon", "tts"] = "earcon"
    text: str = "我在。"
    # A barge wake confirms itself with one short earcon instead of the configured
    # cue: the person is already mid-sentence, so a spoken 「我在。」 or the 320 ms tone
    # would cover their first word (measured 2026-10-26 12:07: cue -> seg 「在呢。」 ->
    # reply -> barge again). False restores the pre-T5 silent barge (no feedback at
    # all); `mode: none` and a muted device keep the barge silent too (existing ack
    # semantics). The short tone still uses `chime`/`chime_trim`; `mode: tts` is not
    # spoken here. Old configs without the key keep the new (audible) behaviour.
    barge_ack: bool = True
    chime: str = "soft2"
    # 0 = the preset's natural length (soft2 500 ms, woodblock 120, windchime 700).
    # 320 ms, not 160: the client fills a 300 ms buffer before it starts playing, so a
    # shorter cue sat silent until the reply arrived and then played together with it
    # (heard on the real speaker, 2026-10-22). The old build shipped 500 ms.
    chime_ms: int = Field(default=320, ge=0, le=1200)
    # The floor is 0.5, as in the old console: 0 would be a silent cue that still reports
    # "earcon", which reads as a dead speaker rather than a typo.
    chime_trim: float = Field(default=1.0, ge=0.5, le=1.0)
    max_ms: int = Field(default=600, ge=100, le=3000)
    # The box hears its own spoken cue. Ignore that tail before a turn may start.
    echo_ignore_ms: int = Field(default=1200, ge=0, le=3000)

    @field_validator("chime")
    @classmethod
    def _known_chime(cls, v: str) -> str:
        # A name from the old console that this build does not have must fail loudly here,
        # not fall back to soft2 at runtime while the config still says windchime.
        from ..audio.chime import PRESETS

        if v not in PRESETS:
            raise ValueError(f"unknown chime preset {v!r}; available: {', '.join(PRESETS)}")
        return v


class AudioConfig(BaseModel):
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    playback: PlaybackConfig = Field(default_factory=PlaybackConfig)
    aec: AecConfig = Field(default_factory=AecConfig)


class EffectiveAudio(BaseModel):
    """The global `audio` block with one device's overrides resolved."""

    capture: CaptureConfig
    voice: VoiceConfig
    playback: PlaybackConfig
    aec: AecConfig


class CaptureDefaults(BaseModel):
    """A plug manifest's declared capture layout (rui-device/v1 `audio_defaults`).

    These are hardware facts, so they win over the global `audio.capture` block; a
    disagreeing explicit global value is reported as a conflict. The one exception is
    `rate`, which the client may negotiate down (see `AudioDefaults`). Every field is
    optional: a plug states only what its hardware changes, and the
    neutral core layout fills the rest. The ranges match `CaptureConfig` so a merged
    block can never be out of range. A plug that states one of
    channels/mic_channels/ref_channels must state the layout consistently, otherwise
    the merged block would fail its own `mic + ref == channels` rule later.
    """

    model_config = ConfigDict(extra="forbid")

    rate: int | None = Field(default=None, ge=8000, le=96000)
    channels: int | None = Field(default=None, ge=1, le=16)
    mic_channels: int | None = Field(default=None, ge=1, le=16)
    ref_channels: int | None = Field(default=None, ge=0, le=16)
    bits: Literal[16, 32] | None = None
    mic_shift: int | None = Field(default=None, ge=0, le=31)
    ref_shift: int | None = Field(default=None, ge=0, le=31)

    @model_validator(mode="after")
    def _layout_adds_up(self) -> CaptureDefaults:
        if (
            self.channels is None
            and self.mic_channels is None
            and self.ref_channels is None
        ):
            return self
        neutral = CaptureConfig()
        channels = neutral.channels if self.channels is None else self.channels
        mic = neutral.mic_channels if self.mic_channels is None else self.mic_channels
        ref = neutral.ref_channels if self.ref_channels is None else self.ref_channels
        if mic + ref != channels:
            raise ValueError(
                f"audio_defaults.capture: mic_channels({mic}) + ref_channels({ref}) "
                f"must equal channels({channels}); declare the whole layout "
                "(channels, mic_channels, ref_channels) for a non-neutral device"
            )
        return self


class PlaybackDefaults(BaseModel):
    """A plug manifest's declared playback wire format (rui-device/v1)."""

    model_config = ConfigDict(extra="forbid")

    rate: int | None = Field(default=None, ge=8000, le=96000)
    channels: int | None = Field(default=None, ge=1, le=8)


class VoiceDefaults(BaseModel):
    """A plug manifest's declared voice/timing facts (rui-device/v1).

    Both values describe what this model's microphone/echo path can tolerate, so the
    manifest wins over an explicit global value exactly like the capture layout and
    playback wire (0.8.1: a Dot 2 wants a 300 ms follow-up and 500 ms of post-playback
    mute; the reference speaker's 2500 ms settings are wrong for it). Only the two
    fields that differ per model live here: `followup_replay` stays a global choice
    and `ack_echo_ignore_ms` keeps its own top-level field for compatibility.
    """

    model_config = ConfigDict(extra="forbid")

    followup_delay_ms: int | None = Field(default=None, ge=0, le=10000)
    post_playback_mute_ms: int | None = Field(default=None, ge=0, le=10000)


class AudioDefaults(BaseModel):
    """The optional `audio_defaults` block of a rui-device/v1 manifest.

    `audio_defaults` states the *model's* hardware facts, not a preference: a
    6-mic/2-ref array or a 48 kHz speaker wire is a property of the box, and a global
    config written for the reference speaker must not silently replace it (0.8.0 P0:
    the production template's 16 kHz / 8 ch block was applied to a mono Dot 2, so
    every playback block was rejected). The merge order is therefore:

        core default < global config < manifest hardware facts < devices[].audio

    with two documented exceptions, both reported as
    :class:`AudioConflict` warnings instead of being silent:

    * ``capture.rate`` is *negotiated*: the model states its native rate, but the
      client may resample to the operator's requested wire (reference speaker: 48 kHz native,
      production asks for 16 kHz to save wifi). An explicit global/device rate wins.
    * ``ack_echo_ignore_ms`` is an acoustic fact of the model and wins over an
      explicit global window; a device override still wins over both.

    The `voice` block is the same kind of acoustic fact: a device-AEC model keeps
    residual echo for seconds (or wants almost none), so its follow-up delay and
    post-playback mute are declared here and win over a global template with a
    reported conflict.

    A plug that declares nothing (every pre-0.6 plug) leaves the neutral core
    defaults in place, so the field is unknown-safe and loading an old plug never
    errors.
    """

    model_config = ConfigDict(extra="forbid")

    capture: CaptureDefaults | None = None
    playback: PlaybackDefaults | None = None
    voice: VoiceDefaults | None = None
    ack_echo_ignore_ms: int | None = Field(default=None, ge=0, le=5000)


@dataclass(frozen=True)
class AudioConflict:
    """One field where an explicit config value disagreed with the model manifest.

    The console and the plane both show it; nothing about it is silent. ``winner`` is
    the source whose value became effective (``device-manifest`` or ``global-config``;
    a device override never conflicts because it is a deliberate per-room choice).
    """

    path: str
    global_value: Any
    manifest_value: Any
    effective: Any
    winner: str
    kind: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "global": self.global_value,
            "manifest": self.manifest_value,
            "effective": self.effective,
            "winner": self.winner,
            "kind": self.kind,
            "message": self.message,
        }


#: Conflicts already logged, so a per-request read does not spam the service log.
_LOGGED_CONFLICTS: set[tuple[str, str, str, str]] = set()
#: Manifest misses already logged, keyed by (device/plug, reason).
_LOGGED_MANIFEST_MISSES: set[tuple[str, str, str]] = set()


def _conflict(
    *,
    path: str,
    global_value: Any,
    manifest_value: Any,
    effective: Any,
    winner: str,
    kind: str,
    note: str,
) -> AudioConflict:
    label = path if path.startswith("ack.") else f"audio.{path}"
    message = (
        f"{label}: global {global_value} vs model {manifest_value}; "
        f"using {effective} ({note})"
    )
    conflict = AudioConflict(path, global_value, manifest_value, effective, winner,
                             kind, message)
    key = (path, str(global_value), str(manifest_value), winner)
    if key not in _LOGGED_CONFLICTS:
        _LOGGED_CONFLICTS.add(key)
        log.warning("%s", message)
    return conflict


def _explicit(block: BaseModel, key: str, core: BaseModel) -> bool:
    """Whether the config state for `key` is a real choice, not a core default.

    A field counts as configured when the operator/config file set it
    (`model_fields_set`) or when its value differs from the core default `core` (a
    value mutated after load, which older write paths do). The distinction still
    matters: a manifest fills fields the config never chose, and a conflict with an
    explicit value is reported rather than applied silently.
    """
    return key in block.model_fields_set or getattr(block, key) != getattr(core, key)


#: Capture fields that form one bundle with the channel dimensions: a model that
#: declares its layout also owns the fields it omits (they take the neutral core
#: value, never the global template written for another speaker).
CAPTURE_LAYOUT_FIELDS = (
    "channels", "mic_channels", "ref_channels", "bits", "mic_shift", "ref_shift",
)
#: Only the dimension fields imply a complete layout; declaring `bits` alone says
#: nothing about how many channels arrive, so the global block still supplies them.
CAPTURE_LAYOUT_DIMENSIONS = ("channels", "mic_channels", "ref_channels")


def capture_layout_neutral_fills(
    defaults: AudioDefaults | None, audio: AudioConfig
) -> dict[str, Any]:
    """Capture paths a declared manifest layout replaces with the neutral core value.

    Keys are provenance paths (``capture.mic_shift``); values are the neutral values
    the merge installs. A field is listed when the manifest declares a layout
    dimension (``channels`` / ``mic_channels`` / ``ref_channels``) but omits that
    field, and the global block disagrees with the neutral value - exactly the fields
    :func:`effective_audio` neutralizes and reports as a ``device-manifest`` conflict.
    A global value that already equals the neutral one is not listed: nothing was
    replaced, so ``global-config`` stays truthful for it.

    The provenance endpoint uses this so its ``sources`` cannot claim the global
    block won a field the merge took from the model's declared layout.
    """
    if defaults is None or defaults.capture is None:
        return {}
    declared = defaults.capture.model_dump(exclude_none=True)
    if not set(declared) & set(CAPTURE_LAYOUT_DIMENSIONS):
        return {}
    neutral = CaptureConfig()
    fills: dict[str, Any] = {}
    for key in CAPTURE_LAYOUT_FIELDS:
        if key in declared:
            continue
        if getattr(audio.capture, key) == getattr(neutral, key):
            continue
        fills[f"capture.{key}"] = getattr(neutral, key)
    return fills


def effective_audio(
    audio: AudioConfig,
    override: DeviceAudioOverride | None,
    defaults: AudioDefaults | None = None,
    *,
    conflicts: list[AudioConflict] | None = None,
) -> EffectiveAudio:
    """Merge the audio layers for one device: manifest hardware facts win.

    Order: core default < global config < manifest ``audio_defaults`` <
    ``devices[].audio``. The manifest is authoritative for the model's hardware
    (capture layout, playback wire format); an explicit global value that disagrees is
    kept in ``conflicts`` and the effective value is the model's, except for
    ``capture.rate`` (negotiated: the explicit wire wins and the client resamples).

    `defaults` is the device plug's `audio_defaults` (None for an old plug or an
    unresolved manifest: the global/core value then applies unchanged, which is the
    documented unknown-safe shim - callers that can see a manifest log the miss).
    The merged dict is validated again on purpose: an override that changes
    `capture.channels` without the matching `mic_channels`/`ref_channels` must fail
    here with a schema error, not later in the audio plane as a frame-length crash.
    """
    resolved: dict[str, Any] = {}
    blocks = {
        "capture": audio.capture,
        "voice": audio.voice,
        "playback": audio.playback,
        "aec": audio.aec,
    }

    def overridden(name: str, key: str) -> bool:
        """A deliberate device value makes the global-vs-model conflict moot."""
        part = getattr(override, name, None) if override is not None else None
        return part is not None and getattr(part, key, None) is not None

    for name, block in blocks.items():
        merged = block.model_dump()
        core = type(block)()
        default_block = getattr(defaults, name, None) if defaults is not None else None
        if default_block is not None:
            declared = default_block.model_dump(exclude_none=True)
            # A capture layout is a bundle: the model's channels/bits/shifts only make
            # sense together. A global template's 32-bit/12-bit-shift array-speaker numbers must
            # not leak into a 16-bit mono Dot 2 just because its manifest omitted the
            # shift fields (which it does - neutral 0 is the only correct value there).
            if name == "capture" and set(declared) & set(CAPTURE_LAYOUT_DIMENSIONS):
                for key in CAPTURE_LAYOUT_FIELDS:
                    if key in declared:
                        continue
                    core_value = getattr(core, key)
                    current = getattr(block, key)
                    if current == core_value:
                        continue
                    merged[key] = core_value
                    if overridden(name, key):
                        continue
                    conflict = _conflict(
                        path=f"{name}.{key}", global_value=current,
                        manifest_value=core_value, effective=core_value,
                        winner="device-manifest", kind="hardware",
                        note="manifest declares the layout; neutral core value",
                    )
                    if conflicts is not None:
                        conflicts.append(conflict)
            for key, value in declared.items():
                if not _explicit(block, key, core):
                    merged[key] = value
                    continue
                current = getattr(block, key)
                if current == value:
                    continue
                path = f"{name}.{key}"
                if overridden(name, key):
                    merged[key] = value
                    continue
                if name == "capture" and key == "rate":
                    # The model states its native rate; an explicit wire is the
                    # operator's negotiated request (the reference client is asked for 16 kHz).
                    conflict = _conflict(
                        path=path, global_value=current, manifest_value=value,
                        effective=current, winner="global-config",
                        kind="negotiated",
                        note="explicit wire wins; the client resamples",
                    )
                else:
                    # Hardware fact: channels/bit depth/shifts, or the playback wire
                    # format a plug's adapter rejects anything else for (Dot 2 48 kHz).
                    merged[key] = value
                    conflict = _conflict(
                        path=path, global_value=current, manifest_value=value,
                        effective=value, winner="device-manifest",
                        kind="hardware", note="model hardware",
                    )
                if conflicts is not None:
                    conflicts.append(conflict)
        part = getattr(override, name) if override is not None else None
        if part is not None:
            values = part.model_dump(exclude_none=True)
            # `voice.ack_echo_ignore_ms` replaces a conversational `ack` setting, not an
            # audio one. VoiceConfig would ignore an unknown key, so drop it explicitly
            # instead of letting a resolved-looking value vanish; `ack` is resolved by
            # `effective_ack_echo_ignore_ms()`.
            values.pop("ack_echo_ignore_ms", None)
            merged.update(values)
        resolved[name] = type(block).model_validate(merged)
    return EffectiveAudio(**resolved)


def effective_ack_echo_ignore_ms(
    ack: AckConfig,
    device: DeviceConfig | None,
    defaults: AudioDefaults | None = None,
    *,
    conflicts: list[AudioConflict] | None = None,
) -> int:
    """The spoken-cue echo window for one device, three layers resolved.

    Layer order: the plug's `audio_defaults.ack_echo_ignore_ms` (the model's measured
    acoustic tail) < the global `ack.echo_ignore_ms` **only when the model declares
    none** < `devices[].audio.voice.ack_echo_ignore_ms`. An explicit global window
    that disagrees with a declared model tail is kept in ``conflicts`` and does not
    win: the Dot 2's 2500 ms tail was lost to the reference speaker's 1200 ms exactly
    that way (0.8.0 P0 cold-start symptom: `ack=1200` on a fresh Dot 2).

    The override lives under `voice` because it describes this device's microphone,
    but the value it replaces is the conversational `ack` setting, which is
    deliberately outside the `audio` block. Keeping the two resolved in one place is
    what stops the next caller from reading the global value and re-introducing the
    self-hearing bug.
    """
    base = ack.echo_ignore_ms
    core = type(ack)()
    if defaults is not None and defaults.ack_echo_ignore_ms is not None:
        value = defaults.ack_echo_ignore_ms
        if not _explicit(ack, "echo_ignore_ms", core):
            base = value
        elif ack.echo_ignore_ms != value:
            base = value
            override_value = None
            if (device is not None and device.audio is not None
                    and device.audio.voice is not None):
                override_value = device.audio.voice.ack_echo_ignore_ms
            if override_value is None:
                conflict = _conflict(
                    path="ack.echo_ignore_ms", global_value=ack.echo_ignore_ms,
                    manifest_value=value, effective=value, winner="device-manifest",
                    kind="acoustic", note="model measured tail",
                )
                if conflicts is not None:
                    conflicts.append(conflict)
    override = None
    if device is not None and device.audio is not None and device.audio.voice is not None:
        override = device.audio.voice.ack_echo_ignore_ms
    return base if override is None else override


@dataclass(frozen=True)
class AudioDefaultsInfo:
    """Why a device does or does not have manifest hardware facts.

    ``error`` is empty for a resolved plug and for a device that never named one;
    it is set when the device names a ``plug_id`` (or the adapter matches a plug) but
    no manifest could be read. The plane logs that once and the console shows it, so
    a cold-start catalog miss is visible instead of looking like "the model declares
    nothing".
    """

    defaults: AudioDefaults | None = None
    plug_id: str = ""
    model_id: str = ""
    source: str = ""
    expected: bool = False
    error: str = ""

    @property
    def resolved(self) -> bool:
        return self.defaults is not None


def _catalog_load_error(data_dir: str | None) -> str:
    """One bounded manifest-load failure for the console; "" when the catalog is clean.

    Only called on the unresolved path, so the extra catalog read never touches the
    hot path. Imported lazily for the same cycle reason as the resolver below.
    """
    try:
        from ..plugins.resolve import PlugCatalog

        errors = PlugCatalog(data_dir).errors
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never raise
        return f"{type(exc).__name__}: {exc}"
    return errors[0] if errors else ""


def device_audio_defaults_info(
    device: DeviceConfig | None, data_dir: str | None
) -> AudioDefaultsInfo:
    """Resolve the device's plug manifest and report what was found (or why not).

    Imported lazily: `rui.plugins.models` imports this module, so a module-level
    import here would close a cycle, and config validation must not require the plug
    catalog unless a device actually asks for its defaults. An unresolved or broken
    plug is not an error at this layer - the neutral core defaults are the documented
    shim for a plug that declares nothing - but it is *visible*.
    """
    if device is None:
        return AudioDefaultsInfo()
    params = device.params if isinstance(device.params, dict) else {}
    plug_id = str(params.get("plug_id") or "").strip()
    try:
        from ..plugins.resolve import resolve_device_plug

        resolved = resolve_device_plug(device, data_dir)
    except Exception as exc:  # noqa: BLE001 - a broken plug must not unload the config
        return AudioDefaultsInfo(
            plug_id=plug_id, expected=bool(plug_id),
            error=f"plug catalog failed: {type(exc).__name__}: {exc}",
        )
    if resolved is None:
        error = (
            f"plug {plug_id!r} is not installed or its manifest did not load"
            if plug_id
            else f"no installed plug declares adapter {device.adapter!r}"
        )
        # Task-25: "did not load" is not enough to act on. The catalog keeps one
        # bounded line per broken manifest (missing file, YAML/pydantic error, bad
        # rui_compat); append the first so the WARN and the device page name the file
        # and field instead of only the plug id.
        detail = _catalog_load_error(data_dir)
        if detail:
            error = f"{error}: {detail}"
        key = (str(getattr(device, "id", "") or ""), plug_id,
               str(getattr(device, "adapter", "") or ""))
        if key not in _LOGGED_MANIFEST_MISSES:
            _LOGGED_MANIFEST_MISSES.add(key)
            log.warning(
                "audio facts: device %s (%s) has no usable model manifest: %s; "
                "hardware facts fall back to the global/core values",
                getattr(device, "id", "?"), getattr(device, "adapter", "?"), error,
            )
        return AudioDefaultsInfo(plug_id=plug_id, expected=bool(plug_id), error=error)
    defaults = resolved.device.audio_defaults
    return AudioDefaultsInfo(
        defaults=defaults,
        plug_id=resolved.plug_id,
        model_id=str(resolved.device.id),
        source=resolved.source,
        # A matched plug that declares nothing is the documented shim for a pre-0.6
        # plug: there is nothing to wait for, so no retry and no warning.
        expected=False,
    )


def device_audio_defaults(
    device: DeviceConfig | None, data_dir: str | None
) -> AudioDefaults | None:
    """The `audio_defaults` of the plug manifest that owns `device`, or None."""
    return device_audio_defaults_info(device, data_dir).defaults


def effective_device_audio(
    cfg: Config,
    device: DeviceConfig | None,
    *,
    conflicts: list[AudioConflict] | None = None,
) -> EffectiveAudio:
    """`effective_audio()` for a configured device, resolving its manifest defaults."""
    defaults = device_audio_defaults(device, cfg.system.data_dir)
    return effective_audio(
        cfg.audio, device.audio if device is not None else None, defaults,
        conflicts=conflicts,
    )


def effective_device_ack_echo_ignore_ms(
    cfg: Config,
    device: DeviceConfig | None,
    *,
    conflicts: list[AudioConflict] | None = None,
) -> int:
    """`effective_ack_echo_ignore_ms()` with the device manifest's declared tail."""
    defaults = device_audio_defaults(device, cfg.system.data_dir)
    return effective_ack_echo_ignore_ms(
        cfg.ack, device, defaults, conflicts=conflicts
    )


def device_audio_conflicts(cfg: Config, device: DeviceConfig | None) -> list[AudioConflict]:
    """Every manifest-vs-explicit disagreement for one device, merged and ack."""
    conflicts: list[AudioConflict] = []
    effective_device_audio(cfg, device, conflicts=conflicts)
    effective_device_ack_echo_ignore_ms(cfg, device, conflicts=conflicts)
    return conflicts


class ProviderConfig(BaseModel):
    kind: str = "openai_compat"
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    timeout_s: float = Field(default=15.0, gt=0, le=120)
    # One retry beats one long wait: a 20 s timeout is worse than two 6 s attempts.
    retries: int = Field(default=0, ge=0, le=3)
    threads: int = Field(default=2, ge=1, le=16)  # local models only
    voice: str = ""  # TTS voice name; ignored by ASR/LLM
    # TTS prosody, on the Volcano scale: speech_rate -50..100 means 0.5x..2x speed
    # (0 = the vendor default). xfyun maps it onto its 0..100 speed. Emotion names are
    # vendor/voice specific; empty = leave the vendor default.
    speech_rate: int = Field(default=0, ge=-50, le=100)
    emotion: str = ""
    emotion_scale: int = Field(default=4, ge=1, le=5)
    # Vendors that need more than a bearer key (讯飞 wants app_id + key + secret).
    app_id_env: str = ""
    api_secret_env: str = ""
    # The old deployment's ANSWER_MAX_TOKENS is 3000, and it is a ceiling, not a target:
    # short replies stay short because the prompt says so. 220 was left here from an
    # earlier experiment and silently cut every answer in the 900-character band
    # (`assistant.reply_max_chars_long`), half-way through a sentence that TTS then spoke.
    max_tokens: int = Field(default=3000, ge=16, le=8192)
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    # DeepSeek's reasoner spends 2-3x the latency and shares max_tokens with the answer;
    # for a voice reply the reasoning buys nothing (measured on the live prompt).
    thinking: str = ""  # "" = vendor default, "disabled" = ask for no chain of thought


def _default_catalog() -> dict[str, ProviderConfig]:
    return {
        "asr": ProviderConfig(api_key_env="RUI_ASR_KEY"),
        "tts": ProviderConfig(api_key_env="RUI_TTS_KEY"),
        "llm": ProviderConfig(api_key_env="RUI_LLM_KEY"),
    }


class StageConfig(BaseModel):
    """Ordered provider names: first is primary, the rest are fallbacks in order."""

    chain: list[str] = Field(min_length=1)

    @field_validator("chain")
    @classmethod
    def _sane_chain(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("chain contains duplicate provider names")
        for name in v:
            if not name.replace("-", "").replace("_", "").isalnum():
                raise ValueError(f"provider name must be alphanumeric: {name}")
        return v


class ProvidersConfig(BaseModel):
    """A catalog of named providers plus one ordered chain per stage.

    Separating the name from the settings is what makes per-stage fallback expressible:
    a stage is [primary, fallback, ...] and every name resolves to exactly one block.
    """

    catalog: dict[str, ProviderConfig] = Field(default_factory=_default_catalog)
    asr: StageConfig = Field(default_factory=lambda: StageConfig(chain=["asr"]))
    tts: StageConfig = Field(default_factory=lambda: StageConfig(chain=["tts"]))
    llm: StageConfig = Field(default_factory=lambda: StageConfig(chain=["llm"]))

    @model_validator(mode="after")
    def _chains_resolve(self) -> ProvidersConfig:
        for stage in STAGES:
            for name in getattr(self, stage).chain:
                if name not in self.catalog:
                    raise ValueError(f"providers.{stage}.chain references unknown provider: {name}")
        return self

    def resolve(self, stage: str) -> list[tuple[str, ProviderConfig]]:
        """[(name, config)] in fallback order for one stage."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage: {stage}")
        return [(n, self.catalog[n]) for n in getattr(self, stage).chain]


_MCP_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_MCP_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# An HA entity id is a slug. `allowed_scenes` accepts nothing else: a friendly name
# (or a typo with a space/capital) would silently never match at call time.
_SCENE_ENTITY_ID = re.compile(r"^scene\.[a-z0-9_]+$")


class McpServerConfig(BaseModel):
    """One generic MCP server.

    `headers` carries non-secret request metadata (a vendor version header, an
    `Accept` override). A credential belongs in `token_env`, which names a value in
    the write-only secrets store; the value is resolved when the session opens and is
    never part of config.yaml or an API response. Keeping the two slots separate is
    deliberate: a token in a header would be readable from `GET /api/v1/config`, and a
    second auth mechanism would make "which one wins?" ambiguous.
    """

    name: str = Field(min_length=1, max_length=32)
    transport: Literal["stdio", "http"] = "http"
    url: str = ""
    command: list[str] = Field(default_factory=list)
    # Explicit environment for a stdio server (the manager never passes the service
    # environment). Values end up in config.yaml, so keep secrets out unless the file
    # itself is 0600 and private.
    env: dict[str, str] = Field(default_factory=dict)
    # HTTP-only request headers. Validated here because the value is written straight
    # into an httpx client: a CR/LF is header injection, and an invalid name would only
    # fail later as an opaque transport error.
    headers: dict[str, str] = Field(default_factory=dict)
    # Name of a write-only secret holding a bearer token. Empty = no token.
    token_env: str = ""
    enabled: bool = True

    @field_validator("headers")
    @classmethod
    def _headers_are_safe(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        seen: set[str] = set()
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            if not name or not _MCP_HEADER_NAME_RE.fullmatch(name):
                raise ValueError(f"invalid HTTP header name: {raw_name!r}")
            if name.lower() in seen:
                raise ValueError(f"duplicate HTTP header (case-insensitive): {name}")
            seen.add(name.lower())
            text = str(raw_value)
            if any(char in text for char in ("\r", "\n", "\0")):
                raise ValueError(f"HTTP header {name!r} contains a control character")
            cleaned[name] = text
        return cleaned

    @field_validator("token_env")
    @classmethod
    def _token_env_is_a_secret_name(cls, value: str) -> str:
        name = str(value or "").strip()
        if name and not _MCP_ENV_NAME_RE.fullmatch(name):
            raise ValueError(
                "token_env must look like a secret name: UPPER_CASE_WITH_UNDERSCORES"
            )
        return name

    @model_validator(mode="after")
    def _auth_slots_make_sense(self) -> McpServerConfig:
        if self.transport == "stdio":
            if self.headers or self.token_env:
                raise ValueError(
                    "headers/token_env apply to http MCP servers; a stdio child gets "
                    "its credentials through `env`"
                )
            return self
        if self.token_env and any(
                str(name).lower() == "authorization" for name in self.headers):
            raise ValueError(
                "set either an explicit Authorization header or token_env, not both"
            )
        return self


class HaConfig(BaseModel):
    base_url: str = ""
    token_env: str = "RUI_HA_TOKEN"
    # --- what of the entity list reaches the prompt (0.8.11) ---------------------
    # The list used to be "everything HA has", injected whole every turn: 75 entities /
    # 4121 chars on the production install, 82 % of the prompt. These four knobs make it
    # a decision instead of an accident.
    context: bool = True
    # Domains the prompt lists. script stays absent by default: EXCLUDED_DOMAINS in
    # tools/ha.py refuses calls to it (arbitrary sequences), so listing it would only
    # teach the model to try what always fails. Whitelisted scenes are injected by
    # their own segment (`allowed_scenes`) and need no entry here.
    context_domains: list[str] = Field(
        default_factory=lambda: ["light", "switch", "cover"])
    # Entities injected per turn. The fetch budget is deliberately wider (3x, 200..1000):
    # entries dropped by `context_exclude` must not eat the cap.
    entity_cap: int = Field(default=120, ge=1, le=1000)
    # Regexes (re.search, case-insensitive) against a friendly name OR an entity_id;
    # one match hides that entity. Compiled here so a typo is a config error instead of
    # a turn that dies halfway through the prompt.
    context_exclude: list[str] = Field(default_factory=list)
    # --- sanctioned composite actions (0.8.11) ------------------------------------
    # Scene ids the model may activate. Empty (the default) keeps the 0.8.10 behaviour:
    # every `scene.*` call is refused. A listed id is matched **exactly**; the friendly
    # name is only shown in the prompt, never used to decide whether a call is allowed,
    # so a renamed scene cannot smuggle a different entity past the guard. This is a
    # whitelist, not a widening: lock/alarm/valve/script/automation/shell_command/
    # python_script/hassio stay refused whatever is written here.
    allowed_scenes: list[str] = Field(default_factory=list)
    # Per-call budget for one `call_service` wire call (the multi-entity path sends
    # serial batches of at most 8). The old build's timeout was a fixed 8 s inside
    # MCPClient.call; a slow-but-working HA needs the operator to be able to say 20.
    call_timeout_s: float = Field(default=10.0, ge=2.0, le=60.0)

    @field_validator("allowed_scenes")
    @classmethod
    def _allowed_scenes_valid(cls, values: list[str]) -> list[str]:
        """Entity ids only, deduped: a typo must fail at load, not at "关全屋灯"."""
        out: list[str] = []
        for raw in values:
            text = str(raw or "").strip()
            if not text:
                continue        # an empty YAML list item is not an entity id
            if not _SCENE_ENTITY_ID.fullmatch(text):
                raise ValueError(
                    f"allowed_scenes entry {raw!r} must be a scene entity id such as "
                    "'scene.example_scene' (lowercase letters, digits and underscores "
                    "after the 'scene.' prefix)"
                )
            if text not in out:
                out.append(text)
        return out

    @field_validator("context_exclude")
    @classmethod
    def _exclude_patterns_compile(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for raw in values:
            text = str(raw or "")
            if not text.strip():
                continue        # an empty YAML list item is not a pattern
            try:
                re.compile(text)
            except re.error as exc:
                raise ValueError(
                    f"context_exclude entry {raw!r} is not a valid regular expression: {exc}"
                ) from exc
            out.append(text)
        return out


class WeatherConfig(BaseModel):
    """open-meteo needs no key. The city is deliberately empty by default: this repo is
    public, and shipping one person's home town as the default is both wrong and a leak."""

    city: str = ""
    latitude: float | None = None
    longitude: float | None = None
    days_max: int = Field(default=3, ge=1, le=7)
    timeout_s: float = Field(default=8.0, ge=2.0, le=30.0)


class NewsConfig(BaseModel):
    enabled: bool = True
    # Public static mirrors of a daily digest; no key, no personal feed.
    source: str = "60s"
    timeout_s: float = Field(default=8.0, ge=2.0, le=30.0)


class SearchConfig(BaseModel):
    """Web search as a swappable vendor, not a hard-wired one.

    `kind` picks the client and `base_url` picks the endpoint, so a deployment can point
    this at any OpenAI-ish search API. The key is never in this file: it comes from the
    environment or from `secrets.yaml`, which the console can write and cannot read back.
    """

    enabled: bool = True
    kind: Literal["deepseek", "none"] = "deepseek"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    count: int = Field(default=6, ge=1, le=10)
    # A search that is still running when the turn budget is gone is worse than no answer.
    timeout_s: float = Field(default=12.0, ge=2.0, le=60.0)


class VisionConfig(BaseModel):
    """The camera path behind `local.look` (0.8.11 C block).

    Two sources, both opt-in by configuration:

    - `usb_snapshot_url`: the operator-run snapshot service that grabs one frame from
      an attached USB camera (`scripts/usb_snapshot_server.py`, `GET /snapshot.jpg`).
      Empty (the default) means this install has no USB camera - the tool says so
      instead of failing silently. The public repository must not carry a home LAN
      address as a default.
    - `cameras`: spoken alias -> Home Assistant camera entity id, e.g.
      `{"门口": "camera.example_door"}`. Empty by default, so no alias resolves
      and the tool answers "can't see it" until the deployment writes its own map.
      The frame comes from `GET <tools.ha.base_url>/api/camera_proxy/<entity_id>`
      with the configured HA token: HA's **last** frame, not a live one.

    The picture is never written to disk. It is memory-only, capped and sent as a
    base64 data URI to the configured LLM endpoint, which is what `docs/local-look.md`
    states to the operator.
    """

    usb_snapshot_url: str = ""
    cameras: dict[str, str] = Field(default_factory=dict)
    # The budget for ONE look as a whole: the frame fetch and the vision description
    # share this deadline. The runner's tool timeout is 20 s, so two independent 15 s
    # steps would be cut from the outside - and the failure would surface as the model
    # saying "the tool timed out" instead of the tool's own sentence.
    timeout_s: float = Field(default=15.0, ge=1.0, le=30.0)
    # Process-wide cooldown between looks (0 = no limit). A camera looks at a private
    # space and every look costs a cloud vision call, so one per 10 s is the default.
    cooldown_s: float = Field(default=10.0, ge=0.0, le=120.0)


class ToolsConfig(BaseModel):
    expose: ExposeMode = "domain"
    whitelist: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    # Security gate for MCP stdio: a stdio server is a local child process, i.e.
    # arbitrary command execution. Off by default so a config (or a pasted config from
    # the internet) cannot start one by accident; the console switch says so explicitly.
    mcp_allow_stdio: bool = False
    ha: HaConfig = Field(default_factory=HaConfig)
    # `local.look` (0.8.11): the USB snapshot service and the alias -> HA camera map.
    vision: VisionConfig = Field(default_factory=VisionConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    # The joke tool always exists; this URL only decides whether it asks a self-hosted
    # service first. Empty = never call out, still serve the 20 bundled samples - so an
    # empty default is a working install, not a disabled tool.
    joke_service_url: str = ""
    # A voice turn dies at ~20 s; three seconds is enough for a LAN service and short
    # enough that the fallback joke lands while the user is still listening.
    joke_timeout_s: float = Field(default=3.0, ge=0.5, le=10.0)


class MusicConfig(BaseModel):
    """The music layer. One enabled source at a time; the interface is the point.

    The source id is either a preset (`music/sources/presets.py`): Navidrome,
    Airsonic, Gonic, Ampache and a custom Subsonic-compatible URL all share one
    adapter; or an external id registered through the operator's
    `RUI_MUSIC_SOURCES` environment variable (`music/sources/external.py`), which
    must implement the same `MusicSource` interface. The preset decides the display
    name, defaults and which credential slots exist; the values themselves never
    enter this file. An external source declares none of that - it reads its own
    credentials from `secrets_path`.

    `url`/`user`/`password`/`token` below are **legacy read-only migration fields**.
    Older configs kept the credentials inline; on startup `migrate_legacy_music_credentials`
    moves them into the 0600 credentials JSON and clears them. They are `exclude=True`,
    so every dump (including `save_config`) leaves them out and a save erases them.
    """

    # Off by default: without the NAS reachable the tools would only ever answer
    # 「曲库里没找到」, and a dead tool list is noise the model has to talk around.
    enabled: bool = False
    # A built-in preset id, or an id registered through RUI_MUSIC_SOURCES. Unknown
    # ids are rejected by every write path; a hand-edited YAML still loads so the
    # panel can explain why music is off.
    source: str = "navidrome"
    # Credential keys are the preset's slots inside this json; values never enter the
    # config or the log. Empty = none provisioned: a production default must not
    # point at one person's home directory.
    secrets_path: str = ""
    # Legacy inline credentials (migration only; excluded from every dump).
    url: str = Field(default="", exclude=True)
    user: str = Field(default="", exclude=True)
    password: str = Field(default="", exclude=True)
    token: str = Field(default="", exclude=True)
    # Resolved PCM (16 kHz mono s16) is cached here. The old build transcoded and
    # deleted, so replaying one song meant downloading its flac again.
    cache_dir: str = "/tmp/rui-music"
    cache_tracks: int = Field(default=8, ge=1, le=64)
    # Radio mode (old build `_top_up`, test_radio.py): pull the next batch while the
    # queue still has this many unplayed, and only ever at a track boundary.
    queue_min_tracks: int = Field(default=2, ge=1, le=10)
    queue_batch: int = Field(default=20, ge=1, le=50)
    # How many track ids the history remembers for radio dedup. The old build slid a
    # 40-60 window in memory and lost it on restart; ours persists.
    history_max: int = Field(default=60, ge=10, le=500)
    # How long a song stays parked after an answer before it resumes by itself. The old
    # build used 6 s, which 9哥 heard as a long gap between "pause" and the song coming
    # back. 2.5 s still leaves a beat for 「换一首」 without the wake word.
    resume_hold_ms: int = Field(default=2500, ge=0, le=10000)

    def legacy_credentials(self) -> dict[str, str]:
        """The inline values an older config may still carry (never re-saved)."""
        return {key: str(getattr(self, key) or "").strip()
                for key in ("url", "user", "password", "token")
                if str(getattr(self, key) or "").strip()}

    @model_validator(mode="after")
    def _music_needs_credentials(self) -> MusicConfig:
        legacy = self.legacy_credentials()
        has_legacy = bool(legacy.get("url") and legacy.get("user")
                          and (legacy.get("password") or legacy.get("token")))
        if self.enabled and not self.secrets_path and not has_legacy:
            raise ValueError(
                "music.enabled needs music.secrets_path: a json with the source's "
                "credential fields (values never go in the config file)"
            )
        return self


class AssistantConfig(BaseModel):
    """Who the assistant is and how long it may talk.

    The old deployment kept these in `assistant.*`; the numbers are its production values,
    because they were tuned against real answers, not guessed (`HANDOFF:522-536`: raising
    the caps without changing the wording changed nothing, and changing the wording was
    what made answers useful).
    """

    name: str = Field(default="小瑞", min_length=1, max_length=12)
    # Appended to the built-in prompt, exactly as the old build appended `persona`. Note
    # what is *not* here: the old deployment's local override said 「说话简短干脆，先给结论，
    # 不铺垫、不客套」, which it then appended after a prompt that says 「信息量优先，把话说
    # 完整」 - the two pull in opposite directions and the later one wins. Identity only.
    # {name}/{version} are filled by `session.prompts.build`; {version} is this build's
    # __version__, never a config field (a writable version could lie about the identity).
    persona: str = (
        "你是家里的小助理，叫{name}，跑在 rui {version} 上（家里自建的语音服务，"
        "不是 Home Assistant）。不知道就说不知道，别装懂。"
    )
    # A full replacement, for the console. Empty means "use the built-in prompt".
    system_prompt: str = ""
    # A turn that has not produced audio in this long says something small first: waiting
    # 9 s in silence reads as "it did not hear me". Old build: filler_after_seconds 1.8.
    # This is the cap for a slow *model* with no tool; the tool case uses the field below.
    filler_after_s: float = Field(default=1.8, ge=0.0, le=10.0)
    # How long a *tool* may run before the filler speaks, counted from the moment the
    # model asks for it. Keyed to the question it fired 30 ms before a 1.03 s news tool
    # finished - covering nothing, and pushing the answer back by its own length.
    filler_after_tool_s: float = Field(default=0.6, ge=0.0, le=10.0)
    filler_text: str = "我查一下。"
    reply_max_chars: int = Field(default=120, ge=10, le=300)
    reply_max_chars_search: int = Field(default=300, ge=40, le=600)
    reply_max_chars_long: int = Field(default=900, ge=100, le=2000)


class SessionConfig(BaseModel):
    """Conversation-level behaviour, as opposed to one cue or one turn."""

    # How long we stay quiet after the speaker's own assistant answered. The old build
    # used 12 s: long enough to cover one vendor dialog, short enough that a later
    # "你好小瑞" is not fighting a stale window.
    vendor_quiet_s: float = Field(default=12.0, ge=0.0, le=60.0)
    # No activity for this long ends the session with a goodbye (`session/idle.py`).
    # Old build: `conversation.timeout_seconds = 15`. Activity is a request or a finished
    # turn, not playback; this build has no music player yet, so only the first two are
    # observable. 0 = never time out, which is also what `farewell: "off"` reads like -
    # two ways to say the same thing, because a console user may reach for either.
    timeout_s: float = Field(default=15.0, ge=0.0, le=300.0)
    # off = silent exit, short = one line, full = the line that invites a follow-up.
    # Old build: `conversation.farewell` (default short) with the same two texts.
    farewell: Literal["off", "short", "full"] = "short"
    farewell_text: str = "没事的话，我先下啦。"
    farewell_text_full: str = "我先下啦，有事喊我。"
    # "Dismiss" words: the *whole* short request must be (or end in a two-character
    # tail of) one of these to end the session - see `rules.stop_intent`. 关掉/关闭/关机
    # are deliberately absent: they are the verbs of home-automation commands, and
    # 「关掉客厅灯」/「关闭窗帘」 were being dismissed before HA ran (audit 2026-10-23,
    # same accident the old build had). The old build kept 11 (`conversation.end_words`);
    # ours adds the stop-playing family because v1 has no music, so "停止" can only mean
    # "stop listening".
    end_words: list[str] = Field(default_factory=lambda: [
        "停止", "停下来", "停下", "停一下", "停掉", "别说了", "别放",
        "暂停", "别唱", "不听了", "退下吧", "退下", "不聊了", "够了",
        "退出", "结束", "再见", "拜拜", "睡了", "睡吧", "晚安",
    ])
    # Whether a turn may see the earlier turns of this conversation. Off = every
    # turn stands alone (history cleared after each one). Editable from the
    # console's 设置 panel and by voice (「关闭上下文」/「打开上下文」).
    keep_context: bool = True
    # Rounds (a user+assistant pair each) handed to the model. The old build read this
    # from `conversation.history_rounds` (6); rui had it hard-coded to 4, which is
    # why a repeated question collapsed into its own previous answer.
    history_rounds: int = Field(default=6, ge=0, le=12)

    @model_validator(mode="after")
    def _the_goodbye_has_words(self) -> SessionConfig:
        """A goodbye that is configured must not be blank.

        Falling back to a built-in line when the field is empty is how a config stops
        matching what it says - the same reason `AckConfig` rejects an unknown chime name.
        Here the failure would be a silent exit while `farewell: "short"` claims otherwise.
        """
        if self.farewell == "short" and not self.farewell_text.strip():
            raise ValueError("session.farewell_text is empty but farewell is 'short'")
        if self.farewell == "full" and not self.farewell_text_full.strip():
            raise ValueError("session.farewell_text_full is empty but farewell is 'full'")
        return self


class ArbitrationConfig(BaseModel):
    """WakeArbiter knobs (frozen in docs/multi-device-design.md §9.1 K3/§11)."""

    model_config = ConfigDict(extra="forbid")
    # First version only implements "score" (room_priority and last_active are the
    # tie-break steps inside it, not separate modes).
    mode: Literal["score", "room_priority", "last_active"] = "score"
    # Group order between wake sources. Scores from a device OWW model and from RUI
    # KWS are not comparable, so they are compared inside a group only (K3).
    source_priority: list[Literal["device_wake", "kws"]] = Field(
        default_factory=lambda: ["device_wake", "kws"]
    )
    # One utterance heard by several devices is one candidate set, not several wakes.
    candidate_merge_window_ms: int = Field(default=400, ge=100, le=2000)
    # Same wake word repeated inside this window is a duplicate, not a new turn.
    duplicate_window_ms: int = Field(default=2000, ge=50, le=10000)
    room_priority: list[str] = Field(default_factory=list)
    prefer_playing_room: bool = True
    last_active_window_s: int = Field(default=120, ge=1, le=3600)
    on_no_speech: Literal["end", "retry_next"] = "end"

    @field_validator("source_priority")
    @classmethod
    def _known_sources(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("arbitration.source_priority must not be empty")
        if len(set(value)) != len(value):
            raise ValueError("arbitration.source_priority must not contain duplicates")
        return value


class MultiDeviceConfig(BaseModel):
    """The `multi_device` block (config v3). Disabled keeps 0.4.x behaviour."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    max_planes: int = Field(default=5, ge=1, le=8)  # D4: 4 -> 5 (2026-10-28)
    # Fallback output for reminders/notifications that have no source room (K1/K4).
    default_output: str | None = None
    arbitration: ArbitrationConfig = Field(default_factory=ArbitrationConfig)


class Config(BaseModel):
    version: int = CONFIG_VERSION
    system: SystemConfig = Field(default_factory=SystemConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    devices: list[DeviceConfig] = Field(default_factory=list)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    ack: AckConfig = Field(default_factory=AckConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    music: MusicConfig = Field(default_factory=MusicConfig)
    multi_device: MultiDeviceConfig = Field(default_factory=MultiDeviceConfig)

    @field_validator("version")
    @classmethod
    def _known_version(cls, v: int) -> int:
        if v > CONFIG_VERSION:
            raise ValueError(f"config version {v} is newer than this build ({CONFIG_VERSION})")
        return v

    @model_validator(mode="after")
    def _unique_ids(self) -> Config:
        seen: set[str] = set()
        for d in self.devices:
            if d.id in seen:
                raise ValueError(f"duplicate device id: {d.id}")
            seen.add(d.id)
        names: set[str] = set()
        for s in self.tools.mcp_servers:
            if s.name in names:
                raise ValueError(f"duplicate mcp server name: {s.name}")
            names.add(s.name)
        return self

    @model_validator(mode="after")
    def _multi_device_references(self) -> Config:
        """Validate the v3 block against the device list, and merge every override.

        A profile that references a device that does not exist is a typo, not a
        fallback: it would silently route reminders to "the most active room" while
        the config claims a fixed speaker (§9.2 D7).
        """
        md = self.multi_device
        by_id = {d.id: d for d in self.devices}
        if md.default_output is not None:
            default = by_id.get(md.default_output)
            if default is None:
                raise ValueError(
                    f"multi_device.default_output references unknown device {md.default_output!r}"
                )
            if not default.enabled:
                raise ValueError(
                    f"multi_device.default_output must be an enabled device, "
                    f"{default.id!r} is disabled"
                )
        priorities = md.arbitration.room_priority
        if len(set(priorities)) != len(priorities):
            raise ValueError("multi_device.arbitration.room_priority must not contain duplicates")
        unknown = [ref for ref in priorities if ref not in by_id]
        if unknown:
            raise ValueError(
                "multi_device.arbitration.room_priority references unknown device(s): "
                + ", ".join(unknown)
            )
        if md.enabled:
            enabled = [d for d in self.devices if d.enabled]
            if not enabled:
                raise ValueError("multi_device.enabled=true requires at least one enabled device")
            missing_room = [d.id for d in enabled if not d.room.strip()]
            if missing_room:
                raise ValueError(
                    "multi_device.enabled=true requires devices[].room for every enabled "
                    "device (room drives same-room echo suppression); missing: "
                    + ", ".join(missing_room)
                )
        for dev in self.devices:
            if dev.audio is None:
                continue
            try:
                # Resolve the device's manifest defaults too: an override that only
                # states the changed fields must be checked against the layout the plug
                # actually declares (a multi-mic plug inherits 6 mic + 2 ref), not against the
                # neutral core defaults.
                effective_device_audio(self, dev)
            except ValidationError as exc:
                raise ValueError(
                    f"devices[{dev.id}].audio does not merge into a valid audio block: {exc}"
                ) from exc
        return self


IMPLICIT_DEFAULT_DEVICE_ID = "living"
IMPLICIT_DEFAULT_ROOM = "客厅"


def implicit_default_output_id(cfg: Config) -> str:
    """The device an unset ``multi_device.default_output`` means (D-013).

    D-013 pins the living room as the house's shared speaker: a reminder with no
    source room must not follow whoever happened to wake last. An enabled device with
    id ``living`` wins, then the first enabled device whose room is 客厅. An empty
    string means the house has no living-room speaker, so the caller must fall back
    to the most recently active room (and say so in a warning).
    """
    enabled = [device for device in cfg.devices if device.enabled]
    for device in enabled:
        if device.id == IMPLICIT_DEFAULT_DEVICE_ID:
            return device.id
    for device in enabled:
        if device.room.strip() == IMPLICIT_DEFAULT_ROOM:
            return device.id
    return ""


def config_warnings(cfg: Config) -> list[str]:
    """Legal configurations that still misbehave; surfaced in the log, not as errors.

    Pydantic protects the schema, not the physics: two AEC cancellers in series, or
    more enabled devices than `max_planes`, parse fine and then disappoint. These are
    the messages a person needs before blaming the speaker.
    """
    warnings: list[str] = []
    md = cfg.multi_device
    enabled = [d for d in cfg.devices if d.enabled]
    for dev in cfg.devices:
        if dev.audio is None or dev.audio.aec is None or dev.audio.aec.who != "device":
            continue
        try:
            eff = effective_device_audio(cfg, dev)
        except ValidationError:
            continue  # already rejected by Config._multi_device_references
        if eff.aec.enabled:
            warnings.append(
                f"device {dev.id!r}: aec.who=device but RUI AEC is still enabled; "
                "disable RUI AEC for this device or the two cancellers cascade"
            )
    if md.enabled and len(enabled) > md.max_planes:
        warnings.append(
            f"multi_device.max_planes={md.max_planes} but {len(enabled)} devices are "
            "enabled; the extra planes will not start (there is no queueing)"
        )
    if md.enabled and md.default_output is None:
        implicit = implicit_default_output_id(cfg)
        if implicit:
            warnings.append(
                "multi_device.default_output is empty; reminders with no source room go "
                f"to {implicit!r} (D-013: id living / room 客厅) while it runs, then "
                "fall back to the most recently active room"
            )
        else:
            warnings.append(
                "multi_device.default_output is empty and no enabled device is id "
                "'living' or room 客厅; reminders with no source room will fall back to "
                "the most recently active room"
            )
    return warnings
