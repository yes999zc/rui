"""Config is the single source of truth; JSON Schema is generated from these models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

AdapterName = Literal["xiaoai", "mock"]
ExposeMode = Literal["domain", "whitelist", "all"]


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

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("device id must be alphanumeric (dash/underscore allowed)")
        return v


class AecConfig(BaseModel):
    enabled: bool = True
    filter_len: int = Field(default=4096, ge=256, le=16384)
    channels: int = Field(default=6, ge=1, le=8)
    # Per-channel bit-depth scaling: mics are 2**23 full scale, playback reference
    # is 2**28. Scaling both by >>8 clips 52-78% of the reference and kills the echo
    # canceller. These two numbers are the whole trick.
    mic_shift: int = Field(default=8, ge=0, le=31)
    ref_shift: int = Field(default=13, ge=0, le=31)


class AudioConfig(BaseModel):
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    frame_ms: int = Field(default=20, ge=10, le=40)
    aec: AecConfig = Field(default_factory=AecConfig)
    kws_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    vad_min_speech_ms: int = Field(default=200, ge=50, le=2000)
    vad_end_silence_ms: int = Field(default=600, ge=100, le=3000)
    barge_in: bool = True


class ProviderConfig(BaseModel):
    kind: str = "openai_compat"
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    timeout_s: float = Field(default=15.0, gt=0, le=120)
    fallback: str = ""  # name of another provider block, handled in M2


class ProvidersConfig(BaseModel):
    asr: ProviderConfig = Field(default_factory=lambda: ProviderConfig(api_key_env="HOUSE_EAR_ASR_KEY"))
    tts: ProviderConfig = Field(default_factory=lambda: ProviderConfig(api_key_env="HOUSE_EAR_TTS_KEY"))
    llm: ProviderConfig = Field(default_factory=lambda: ProviderConfig(api_key_env="HOUSE_EAR_LLM_KEY"))


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
    version: int = 1
    system: SystemConfig = Field(default_factory=SystemConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    devices: list[DeviceConfig] = Field(default_factory=list)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

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
