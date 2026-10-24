"""Pydantic models for the Socket & Plug v0 manifests.

Two files, two shapes, both defined by ``docs/socket-plug.md``:

* ``plugin.yaml`` -> :class:`PluginManifest` (plugin metadata + device file list).
* ``devices/<model>.yaml`` -> :class:`DeviceManifest` (one speaker model).

:class:`Plugin` is the loader's return value: the plugin metadata plus the
already-validated device manifests and the resolved ``runtime.mode``. It is not
a YAML shape and never appears on disk.

Nothing here imports, resolves or executes ``entrypoint`` / ``image``. Those
fields are validated as strings and left for the runtime that will eventually
own them; a manifest is data, not code.
"""
from __future__ import annotations

import re
import warnings
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..device.contract import Capability, Transport
from .semver import VersionError, parse_range, parse_version

__all__ = [
    "PLUGIN_SCHEMA",
    "DEVICE_SCHEMA",
    "Acceptance",
    "Compatibility",
    "DeviceArtifact",
    "DeviceClass",
    "DeviceManifest",
    "DeviceSide",
    "ParamSpec",
    "ParamType",
    "Plugin",
    "PluginManifest",
    "ProtocolSpec",
    "RuntimeMode",
    "RuntimeSpec",
    "TrustLevel",
]

PLUGIN_SCHEMA = "rui-plugin/v0"
DEVICE_SCHEMA = "rui-device/v0"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_PARAM_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TOKEN_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# module.path:attribute, never imported by this package.
_ENTRYPOINT_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)


class TrustLevel(StrEnum):
    BUILTIN = "builtin"
    TRUSTED = "trusted"
    THIRD_PARTY = "third-party"


class RuntimeMode(StrEnum):
    IN_PROCESS = "in-process"
    SIDECAR = "sidecar"


class DeviceClass(StrEnum):
    A = "A"
    B = "B"


class DeviceArtifact(StrEnum):
    FIRMWARE = "firmware"
    CLIENT = "client"
    APP = "app"
    NONE = "none"


class ParamType(StrEnum):
    STRING = "string"
    INT = "int"
    BOOL = "bool"
    LIST = "list"
    SECRET = "secret"
    CHOICE = "choice"


class ProtocolSpec(BaseModel):
    """Protocol family plus adapter-specific keys.

    ``kind`` is a free string because the concrete fields are interpreted by the
    adapter, not by core. Extra keys are deliberately allowed and carried through
    so a future protocol does not require a core release.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    kind: str = Field(min_length=1)
    version: str = ""


class DeviceSide(BaseModel):
    """What the user must do on the speaker before RUI can talk to it."""

    model_config = ConfigDict(extra="forbid")

    artifact: DeviceArtifact
    name: str = ""
    source: str = ""
    version_min: str = ""
    install_steps: list[str] = Field(default_factory=list)
    rollback: str = ""


class RuntimeSpec(BaseModel):
    """How the optional adapter runs.

    ``mode`` is intentionally optional: the loader resolves it from the plugin's
    trust level (third-party -> sidecar, builtin/trusted -> in-process). A plugin
    that declares no runtime at all is manifest-only and runs no code.
    """

    model_config = ConfigDict(extra="forbid")

    mode: RuntimeMode | None = None
    entrypoint: str = ""
    image: str = ""
    listen_port: int = Field(default=4412, ge=1, le=65535)
    token_env: str = "RUI_DEVICE_TOKEN"

    @field_validator("entrypoint")
    @classmethod
    def _entrypoint_shape(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        if not _ENTRYPOINT_RE.fullmatch(value):
            raise ValueError(
                "runtime.entrypoint must look like 'package.module:function' "
                "(not a filesystem path; it is validated as text and never imported here)"
            )
        return value

    @field_validator("image")
    @classmethod
    def _image_shape(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        if any(char.isspace() for char in value):
            raise ValueError("runtime.image must not contain whitespace")
        if ".." in value or value.startswith(("/", ".")) or "\\" in value:
            raise ValueError(
                "runtime.image must be a container image reference, not a path"
            )
        if not value[0].isalnum():
            raise ValueError("runtime.image must start with a registry/repository name")
        return value

    @field_validator("token_env")
    @classmethod
    def _env_name(cls, value: str) -> str:
        if not _TOKEN_ENV_RE.fullmatch(value):
            raise ValueError(
                "runtime.token_env must be an environment variable name "
                "(letters, digits, underscore; not starting with a digit)"
            )
        return value

    @model_validator(mode="after")
    def _mode_needs_its_field(self) -> RuntimeSpec:
        if self.mode is RuntimeMode.IN_PROCESS and not self.entrypoint:
            raise ValueError("runtime.entrypoint is required when runtime.mode is 'in-process'")
        if self.mode is RuntimeMode.SIDECAR and not self.image:
            raise ValueError("runtime.image is required when runtime.mode is 'sidecar'")
        return self


class ParamSpec(BaseModel):
    """One wizard form field.

    ``secret`` is write-only by contract: a manifest may declare the field but
    must not ship a default, so no secret can arrive from a downloaded plugin.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str = Field(min_length=1)
    type: ParamType
    required: bool = False
    default: str | int | bool | list[str] | None = None
    placeholder: str = ""
    help: str = ""
    max: int | None = Field(default=None, ge=0)
    options: list[str] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _key_shape(cls, value: str) -> str:
        if not _PARAM_KEY_RE.fullmatch(value):
            raise ValueError(
                "param key must be lowercase letters/digits/underscore and start with a letter"
            )
        return value

    @model_validator(mode="after")
    def _default_matches_type(self) -> ParamSpec:
        if self.type is ParamType.CHOICE and not self.options:
            raise ValueError(f"param {self.key!r}: type 'choice' needs a non-empty options list")
        if self.default is None:
            return self
        kind = self.type
        if kind is ParamType.SECRET:
            raise ValueError(
                f"param {self.key!r}: a secret must not declare a default; "
                "the user or RUI writes it"
            )
        if kind in (ParamType.STRING, ParamType.CHOICE):
            matches = isinstance(self.default, str)
        elif kind is ParamType.INT:
            matches = isinstance(self.default, int) and not isinstance(self.default, bool)
        elif kind is ParamType.BOOL:
            matches = isinstance(self.default, bool)
        elif kind is ParamType.LIST:
            matches = isinstance(self.default, list)
        else:  # pragma: no cover - every ParamType is handled above
            matches = False
        if not matches:
            raise ValueError(
                f"param {self.key!r}: default {self.default!r} does not match type {kind.value!r}"
            )
        if kind is ParamType.CHOICE and self.default not in self.options:
            raise ValueError(
                f"param {self.key!r}: default {self.default!r} is not one of {self.options!r}"
            )
        return self


class Compatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rui_min: str = ""
    tested_with: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("rui_min")
    @classmethod
    def _min_semver(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        try:
            parse_version(value, allow_partial=True)
        except VersionError as exc:
            raise ValueError(str(exc)) from None
        return value

    @field_validator("tested_with")
    @classmethod
    def _tested_versions(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                parse_version(value, allow_partial=True)
            except VersionError as exc:
                raise ValueError(str(exc)) from None
        return values


class Acceptance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified: bool = False
    last_verified: str = ""
    evidence: str = ""

    @field_validator("last_verified")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("acceptance.last_verified must be YYYY-MM-DD") from None
        return value


# Pydantic warns that a field named `schema` shadows BaseModel.schema(). The key
# is fixed by the v0 format, so keep the field and silence just this notice for
# the class definitions below rather than for the process.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore", message='Field name "schema"', category=UserWarning
    )

    class DeviceManifest(BaseModel):
        """One speaker model (``devices/<model>.yaml``)."""

        model_config = ConfigDict(extra="forbid", populate_by_name=True)

        schema: Literal["rui-device/v0"]
        id: str
        name: str = Field(min_length=1)
        vendor: str = Field(min_length=1)
        # `class` is a Python keyword; store it as device_class with a YAML alias.
        device_class: DeviceClass = Field(alias="class")
        transport: Transport
        protocol: ProtocolSpec
        capabilities: list[Capability]
        device_side: DeviceSide | None = None
        runtime: RuntimeSpec | None = None
        params: list[ParamSpec]
        compatibility: Compatibility | None = None
        acceptance: Acceptance | None = None

        @field_validator("id")
        @classmethod
        def _model_id(cls, value: str) -> str:
            if not _MODEL_ID_RE.fullmatch(value):
                raise ValueError(
                    "device id must be lowercase letters/digits/underscore/dash "
                    "and start with a letter or digit"
                )
            return value

        @field_validator("capabilities")
        @classmethod
        def _unique_capabilities(cls, values: list[Capability]) -> list[Capability]:
            if len(set(values)) != len(values):
                raise ValueError("capabilities contains duplicate entries")
            return values

    class _PluginBase(BaseModel):
        """Fields shared by the on-disk plugin.yaml and the loaded Plugin."""

        model_config = ConfigDict(extra="forbid")

        schema: Literal["rui-plugin/v0"]
        id: str
        name: str = Field(min_length=1)
        version: str
        rui_compat: str
        license: str = Field(min_length=1)
        maintainer: str = Field(min_length=1)
        homepage: str = ""
        trust: TrustLevel = TrustLevel.THIRD_PARTY

        @field_validator("id")
        @classmethod
        def _plugin_id(cls, value: str) -> str:
            if not _ID_RE.fullmatch(value):
                raise ValueError(
                    "plugin id must match [a-z0-9-] and start with a letter or digit"
                )
            return value

        @field_validator("version")
        @classmethod
        def _plugin_version(cls, value: str) -> str:
            try:
                parse_version(value)
            except VersionError as exc:
                raise ValueError(str(exc)) from None
            return value

        @field_validator("rui_compat")
        @classmethod
        def _compat_range(cls, value: str) -> str:
            try:
                parse_range(value)
            except VersionError as exc:
                raise ValueError(f"rui_compat is invalid: {exc}") from None
            return value

        @field_validator("homepage")
        @classmethod
        def _homepage_url(cls, value: str) -> str:
            value = value.strip()
            if value and not value.startswith(("http://", "https://")):
                raise ValueError("homepage must be an http(s) URL")
            return value

    class PluginManifest(_PluginBase):
        """The on-disk ``plugin.yaml``: metadata plus relative device file paths."""

        devices: list[str] = Field(min_length=1)

        @field_validator("devices")
        @classmethod
        def _device_entries(cls, values: list[str]) -> list[str]:
            cleaned: list[str] = []
            for value in values:
                entry = value.strip()
                if not entry:
                    raise ValueError("devices entries must be non-empty relative paths")
                cleaned.append(entry)
            if len(set(cleaned)) != len(cleaned):
                raise ValueError("devices contains duplicate entries")
            return cleaned

    class Plugin(_PluginBase):
        """A validated plugin directory returned by :func:`load_plugin`."""

        devices: list[DeviceManifest] = Field(min_length=1)
        source_dir: Path

        @field_validator("devices")
        @classmethod
        def _unique_device_ids(cls, values: list[DeviceManifest]) -> list[DeviceManifest]:
            seen: set[str] = set()
            for device in values:
                if device.id in seen:
                    raise ValueError(f"duplicate device id: {device.id}")
                seen.add(device.id)
            return values
