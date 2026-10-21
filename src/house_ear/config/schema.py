"""Config is the single source of truth; JSON Schema is generated from these models.

Version history:
  1 - one flat `audio` block with a single sample rate and channel count.
      Wrong for every real speaker we measured: an LX06 captures 48 kHz / 8 ch / 32 bit
      and plays 48 kHz / 2 ch, while the voice pipeline runs at 16 kHz.
  2 - rates and channel counts are explicit per direction (`capture`, `voice`,
      `playback`); providers are a catalog plus an ordered fallback chain per stage.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

CONFIG_VERSION = 2

AdapterName = Literal["xiaoai", "mock"]
ExposeMode = Literal["domain", "whitelist", "all"]
STAGES = ("asr", "tts", "llm")


class ConfigError(ValueError):
    """Raised when a config file cannot be parsed or validated."""


class SystemConfig(BaseModel):
    data_dir: str = "/var/lib/house-ear"
    log_level: Literal["debug", "info", "warning", "error"] = "info"


class WebConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    auth_token: str = ""  # empty = trusted LAN, no auth


class DeviceConfig(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    adapter: AdapterName = "xiaoai"
    host: str
    room: str = ""
    enabled: bool = True
    wake_words: list[str] = Field(default_factory=lambda: ["你好小瑞"])
    volume: int = Field(default=60, ge=0, le=100)
    # Inbound transports: the LX06 client dials us (ws://<server>:<port>/?token=...).
    # None means "adapter default". The token value lives in the environment only.
    listen_port: int | None = Field(default=None, ge=1, le=65535)
    token_env: str = "HOUSE_EAR_DEVICE_TOKEN"

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
    mic_shift: int = Field(default=8, ge=0, le=31)
    ref_shift: int = Field(default=13, ge=0, le=31)

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
    kws_threshold: float = Field(default=0.20, ge=0.0, le=1.0)
    vad_min_speech_ms: int = Field(default=200, ge=50, le=2000)
    vad_end_silence_ms: int = Field(default=600, ge=100, le=3000)
    # Audio kept before the *detected* onset. Silero reports speech a little late, so a
    # small value clips the first syllable: "今天天气怎么样" arrives as "天天气怎么样".
    vad_preroll_ms: int = Field(default=200, ge=0, le=1000)
    # The wake phrase's own tail, dropped after the KWS fires.
    wake_skip_ms: int = Field(default=300, ge=0, le=1000)
    barge_in: bool = True
    # Wake word. Off unless a model directory is configured: the model is a download,
    # not something a pip install can guess.
    kws_enabled: bool = False
    kws_model_dir: str = ""
    kws_keywords_file: str = ""
    kws_chunk: int = Field(default=8, ge=4, le=16)
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
    """Music and speech both leave at the device's native rate: 48 kHz, 2 ch on LX06."""

    rate: int = Field(default=48000, ge=8000, le=96000)
    channels: int = Field(default=2, ge=1, le=8)
    queue_ms: int = Field(default=200, ge=20, le=1000)
    fade_out_ms: int = Field(default=120, ge=0, le=500)
    # Acceptance target: fully silent within this many ms of the user starting to speak.
    target_silence_ms: int = Field(default=300, ge=50, le=1500)


class AecConfig(BaseModel):
    enabled: bool = True
    # Taps at `voice.rate`: 4096 taps = 256 ms of echo tail at 16 kHz.
    filter_len: int = Field(default=4096, ge=256, le=16384)


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
    chime_ms: int = Field(default=160, ge=60, le=1000)
    chime_trim: float = Field(default=1.0, ge=0.0, le=1.0)
    max_ms: int = Field(default=600, ge=100, le=3000)
    # The box hears its own spoken cue. Ignore that tail before a turn may start.
    echo_ignore_ms: int = Field(default=1200, ge=0, le=3000)


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
    # Vendors that need more than a bearer key (讯飞 wants app_id + key + secret).
    app_id_env: str = ""
    api_secret_env: str = ""
    # Defaults come from xiaoai-voice-entry's working setup: answers are spoken, so the
    # budget is a ceiling rather than a target.
    max_tokens: int = Field(default=220, ge=16, le=8192)
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    # DeepSeek's reasoner spends 2-3x the latency and shares max_tokens with the answer;
    # for a voice reply the reasoning buys nothing (measured on the live prompt).
    thinking: str = ""  # "" = vendor default, "disabled" = ask for no chain of thought


def _default_catalog() -> dict[str, ProviderConfig]:
    return {
        "asr": ProviderConfig(api_key_env="HOUSE_EAR_ASR_KEY"),
        "tts": ProviderConfig(api_key_env="HOUSE_EAR_TTS_KEY"),
        "llm": ProviderConfig(api_key_env="HOUSE_EAR_LLM_KEY"),
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
    enabled: bool = True


class HaConfig(BaseModel):
    base_url: str = ""
    token_env: str = "HOUSE_EAR_HA_TOKEN"


class ToolsConfig(BaseModel):
    expose: ExposeMode = "domain"
    whitelist: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    ha: HaConfig = Field(default_factory=HaConfig)


class Config(BaseModel):
    version: int = CONFIG_VERSION
    system: SystemConfig = Field(default_factory=SystemConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    devices: list[DeviceConfig] = Field(default_factory=list)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    ack: AckConfig = Field(default_factory=AckConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

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
