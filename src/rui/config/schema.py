"""Config is the single source of truth; JSON Schema is generated from these models.

Version history:
  1 - one flat `audio` block with a single sample rate and channel count.
      Wrong for every real speaker we measured: an LX06 captures 48 kHz / 8 ch / 32 bit
      and plays 48 kHz / 2 ch, while the voice pipeline runs at 16 kHz.
  2 - rates and channel counts are explicit per direction (`capture`, `voice`,
      `playback`); providers are a catalog plus an ordered fallback chain per stage.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

CONFIG_VERSION = 2

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


class DeviceConfig(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    # A name, not an enum: the registry decides what exists. Keeping the two shipped
    # names in a Literal meant config validation rejected any adapter a future build
    # registers, so "add a speaker" touched this file - the layer that must not care.
    adapter: str = "xiaoai"
    host: str
    room: str = ""
    enabled: bool = True
    wake_words: list[str] = Field(default_factory=lambda: ["你好小瑞"])
    volume: int = Field(default=60, ge=0, le=100)
    # Inbound transports: the LX06 client dials us (ws://<server>:<port>/?token=...).
    # None means "adapter default". The token value lives in the environment only.
    listen_port: int | None = Field(default=None, ge=1, le=65535)
    token_env: str = "RUI_DEVICE_TOKEN"
    # Development only. The LX06's boot script hands the client a bare
    # ws://host:port with no query string, so a tokenless listener is the only way to
    # test against a speaker whose boot script cannot be edited. Off by default and it
    # says so in the log: anyone on the LAN can open the microphone while it is on.
    allow_no_token: bool = False
    led: LedConfig = Field(default_factory=LedConfig)
    # Plug-declared, non-secret values (see docs/install-wizard-design.md §7.1).
    # The reserved key ``plug_id`` identifies which manifest owns the device when two
    # models share one adapter. Old configs without this key load as ``{}``.
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("device id must be alphanumeric (dash/underscore allowed)")
        return v


class CaptureConfig(BaseModel):
    """What arrives from the device, measured rather than assumed.

    LX06 (1.94.13): S32_LE, 8 interleaved channels, 48000 Hz; ch0..ch5 are the mic
    array, ch6 and ch7 carry the same playback reference. Mic full scale is 2**23,
    reference full scale is 2**28 - hence two different shifts, not one.
    """

    rate: int = Field(default=48000, ge=8000, le=96000)
    channels: int = Field(default=8, ge=1, le=16)
    mic_channels: int = Field(default=6, ge=1, le=16)
    ref_channels: int = Field(default=2, ge=0, le=16)
    bits: Literal[16, 32] = 32
    # Production values from the two-day-old deployment: 4 bits of headroom on the
    # mics and 6 dB on the reference. Level-matching (8/13) clips loud speech - a
    # fresh install that never edits this file must not start there.
    mic_shift: int = Field(default=12, ge=0, le=31)
    ref_shift: int = Field(default=16, ge=0, le=31)

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
    # Turn segmentation. Empty path disables it (wake fires, nothing is transcribed).
    vad_model_path: str = ""
    # Audio kept from *before* the KWS hit and replayed into the gate afterwards. The
    # streaming KWS reports a hit ~0.5 s late, so without this the first words of a
    # request spoken straight after the wake word never reach the transcriber.
    wake_preroll_ms: int = Field(default=500, ge=0, le=1500)

    @property
    def frame_samples(self) -> int:
        return self.rate * self.frame_ms // 1000


class PlaybackConfig(BaseModel):
    """Speech and music leave at the rate the pipeline runs at: 16 kHz, mono.

    The capture/ASR/TTS side is 16 kHz, the LX06 client plays mono frames (the
    adapter rejects anything else), and asking for 48 kHz/2 ch here only produced
    a config that looked right and raised on the first reply. The device's own
    ALSA layer resamples; the wire format is what this number describes.
    """

    rate: int = Field(default=16000, ge=8000, le=96000)
    channels: int = Field(default=1, ge=1, le=8)
    queue_ms: int = Field(default=200, ge=20, le=1000)
    fade_out_ms: int = Field(default=120, ge=0, le=500)
    # Blocks (100 ms each) the plane may run ahead of the device's own "consumed"
    # reports. Measured on LX06: the report fires when a block enters aplay's stdin
    # pipe, not when ALSA plays it, so a small window keeps that pipe full and every
    # write waits ~100 ms for a drain (228 blocked writes / 30 s at 2, 130 at 2 with
    # the clock too, 0 at 8). The window is a leak bound; the clock paces. 0 leaves
    # only the clock.
    credit_window: int = Field(default=8, ge=0, le=50)
    # P2.9: how much reply audio the plane waits for before it hands the first block
    # to the device. Zero (default) keeps the LX06 path exactly as it was: its control
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


class AckConfig(BaseModel):
    """What the speaker does the instant the wake word fires.

    Kept out of `audio` because it is a conversational decision, not a DSP one: the same
    plane serves a cue, a spoken word or silence depending on what the room prefers.

    `max_ms` is the whole point of having a `tts` mode at all. A spoken acknowledgement
    mutes the LX06 microphone for about 1.2 s plus its own length, so the cap is enforced
    when the cue is rendered (anything longer is refused and the cue falls back to a
    tone), not documented and hoped for.
    """

    mode: Literal["none", "earcon", "tts"] = "earcon"
    text: str = "我在。"
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


class McpServerConfig(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    transport: Literal["stdio", "http"] = "http"
    url: str = ""
    command: list[str] = Field(default_factory=list)
    # Explicit environment for a stdio server (the manager never passes the service
    # environment). Values end up in config.yaml, so keep secrets out unless the file
    # itself is 0600 and private.
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class HaConfig(BaseModel):
    base_url: str = ""
    token_env: str = "RUI_HA_TOKEN"


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


class ToolsConfig(BaseModel):
    expose: ExposeMode = "domain"
    whitelist: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    # Security gate for MCP stdio: a stdio server is a local child process, i.e.
    # arbitrary command execution. Off by default so a config (or a pasted config from
    # the internet) cannot start one by accident; the console switch says so explicitly.
    mcp_allow_stdio: bool = False
    ha: HaConfig = Field(default_factory=HaConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)


class MusicConfig(BaseModel):
    """The music layer. One enabled source at a time; the interface is the point.

    The source id is a preset (`music/sources/presets.py`): Navidrome, Airsonic,
    Gonic, Ampache and a custom Subsonic-compatible URL all share one adapter. The
    preset decides the display name, defaults and which credential slots exist; the
    values themselves never enter this file.

    `url`/`user`/`password`/`token` below are **legacy read-only migration fields**.
    Older configs kept the credentials inline; on startup `migrate_legacy_music_credentials`
    moves them into the 0600 credentials JSON and clears them. They are `exclude=True`,
    so every dump (including `save_config`) leaves them out and a save erases them.
    """

    # Off by default: without the NAS reachable the tools would only ever answer
    # 「曲库里没找到」, and a dead tool list is noise the model has to talk around.
    enabled: bool = False
    # A preset id from the registry. Unknown ids are rejected by every write path;
    # a hand-edited YAML still loads so the panel can explain why music is off.
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
    persona: str = "你是家里的小助理，叫{name}。不知道就说不知道，别装懂。"
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
