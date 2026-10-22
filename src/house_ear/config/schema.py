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
    token_env: str = "HOUSE_EAR_DEVICE_TOKEN"
    # Development only. The LX06's boot script hands the client a bare
    # ws://host:port with no query string, so a tokenless listener is the only way to
    # test against a speaker whose boot script cannot be edited. Off by default and it
    # says so in the log: anyone on the LAN can open the microphone while it is on.
    allow_no_token: bool = False

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
    ha: HaConfig = Field(default_factory=HaConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)


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
    filler_after_s: float = Field(default=1.8, ge=0.0, le=10.0)
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
    # Rounds (a user+assistant pair each) handed to the model. The old build read this
    # from `conversation.history_rounds` (6); house-ear had it hard-coded to 4, which is
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
    devices: list[DeviceConfig] = Field(default_factory=list)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    ack: AckConfig = Field(default_factory=AckConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
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
