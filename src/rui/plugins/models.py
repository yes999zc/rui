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
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..device.contract import Capability, Transport
from .semver import VersionError, parse_range, parse_version

__all__ = [
    "PLUGIN_SCHEMA",
    "DEVICE_SCHEMA",
    "DEVICE_SCHEMA_V1",
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
    "WakeSource",
    "WakeSpec",
]

PLUGIN_SCHEMA = "rui-plugin/v0"
DEVICE_SCHEMA = "rui-device/v0"
# Socket & Plug v1 (docs/socket-plug-v1.md §3). 0.3 accepts both: v0 stays exactly as it
# was, v1 adds `model`, the optional `wake` block and two capabilities.
DEVICE_SCHEMA_V1 = "rui-device/v1"

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


class WakeSource(StrEnum):
    """Who detects the wake word for this device model (v1 §3.3)."""

    DEVICE = "device"   # the device runs our wake word and reports DeviceWake
    RUI = "rui"         # RUI's own KWS (the LX06 path, unchanged)


class WakeSpec(BaseModel):
    """The optional ``wake`` block on a ``rui-device/v1`` manifest.

    `source` says who fires; `barge_via_device` says whether this plug *enables* the
    device's advisory barge hint. Capability = "the device can", this block = "this plug
    turns it on"; both must be true before RUI will act on one. RUI still owns the final
    turn/barge decision, so nothing here grants a bypass.
    """

    model_config = ConfigDict(extra="forbid")

    source: WakeSource = WakeSource.RUI
    barge_via_device: bool = False



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
    """What the user must do on the speaker before RUI can talk to it.

    Rules are normative in v1 §3.2/§3.3: ``source`` is an http(s) URL with no
    credentials, ``install_steps`` is bounded (≤20 steps, each ≤500 chars) and a
    class-B device must declare a rollback path. Content is displayed only, never
    executed, so these checks are about safe presentation and honest documentation.
    """

    model_config = ConfigDict(extra="forbid")

    artifact: DeviceArtifact
    name: str = ""
    source: str = ""
    version_min: str = ""
    install_steps: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=20
    )
    rollback: str = ""

    @field_validator("source")
    @classmethod
    def _source_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                "device_side.source must be an http(s) URL or empty (only the link is "
                "displayed, never executed)"
            )
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "device_side.source must not contain a username or password "
                "(manifest has no secret values)"
            )
        if any(char.isspace() for char in value):
            raise ValueError("device_side.source must not contain whitespace")
        return value

    @field_validator("install_steps")
    @classmethod
    def _steps_are_presentable(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        for index, value in enumerate(cleaned):
            if not value:
                raise ValueError(f"device_side.install_steps[{index}] must not be empty")
        return cleaned


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
        if (
            kind is ParamType.LIST
            and self.max is not None
            and isinstance(self.default, list)
            and len(self.default) > self.max
        ):
            raise ValueError(
                f"param {self.key!r}: default has {len(self.default)} items but max is "
                f"{self.max}"
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

    @model_validator(mode="after")
    def _verified_needs_a_date(self) -> Acceptance:
        if self.verified and not self.last_verified:
            raise ValueError(
                "acceptance.verified: true requires acceptance.last_verified "
                "(YYYY-MM-DD); a claim without a date is not evidence"
            )
        return self


# Pydantic warns that a field named `schema` shadows BaseModel.schema(). The key
# is fixed by the v0 format, so keep the field and silence just this notice for
# the class definitions below rather than for the process.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore", message='Field name "schema"', category=UserWarning
    )

    class DeviceManifest(BaseModel):
        """One speaker model (``devices/<model>.yaml``).

        Accepts ``rui-device/v0`` (unchanged) and ``rui-device/v1``. v0 manifests must
        not declare the v1-only ``wake`` block or the two new capabilities: a silent
        upgrade would change device behaviour, so it is an error (§7).
        """

        model_config = ConfigDict(extra="forbid", populate_by_name=True)

        schema: Literal["rui-device/v0", "rui-device/v1"]
        id: str
        # v1: hardware model string (e.g. "RS03QR"). Display only; not identity.
        model: str = ""
        name: str = Field(min_length=1)
        vendor: str = Field(min_length=1)
        # `class` is a Python keyword; store it as device_class with a YAML alias.
        device_class: DeviceClass = Field(alias="class")
        transport: Transport
        protocol: ProtocolSpec
        capabilities: list[Capability]
        # v1 only: who detects our wake word and whether the device barge hint is
        # enabled. Omitting it is the v0 default (`source: rui`, barge off).
        wake: WakeSpec | None = None
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

        @model_validator(mode="after")
        def _wake_contract(self) -> DeviceManifest:
            """Enforce the v1 dependency table (docs/socket-plug-v1.md §2)."""
            if (
                self.device_class is DeviceClass.B
                and self.device_side is not None
                and not self.device_side.rollback.strip()
            ):
                raise ValueError(
                    "device_side.rollback: a class B device must declare the recovery "
                    "path (the guide may not leave a high-privilege install unrollbackable)"
                )
            caps = set(self.capabilities)
            wake = self.wake
            if self.schema == DEVICE_SCHEMA:
                if self.model:
                    raise ValueError(
                        "model: the model field is rui-device/v1 only; "
                        "a v0 manifest must not declare it"
                    )
                if wake is not None:
                    raise ValueError(
                        "wake: the wake block is rui-device/v1 only; a v0 manifest cannot "
                        "declare wake, even {source: rui}. Publish rui-device/v1, or omit "
                        "wake and use RUI KWS"
                    )
                for cap in (Capability.DEVICE_WAKE, Capability.BARGE_VIA_DEVICE):
                    if cap in caps:
                        raise ValueError(
                            f"capabilities: {cap.value!r} is rui-device/v1 only; a v0 "
                            "manifest cannot declare it (publish rui-device/v1)"
                        )
                return self

            if Capability.NATIVE_KWS in caps:
                raise ValueError(
                    "capabilities: 'native_kws' is deprecated in rui-device/v1; "
                    "omit wake for RUI KWS, or use wake.source: device"
                )

            source = wake.source if wake is not None else WakeSource.RUI
            barge = bool(wake is not None and wake.barge_via_device)
            if source is WakeSource.DEVICE:
                if Capability.DEVICE_WAKE not in caps:
                    raise ValueError(
                        "wake.source: 'device' requires capability 'device_wake'"
                    )
                if barge and Capability.BARGE_VIA_DEVICE not in caps:
                    raise ValueError(
                        "wake.barge_via_device: true requires capability "
                        "'barge_via_device' (and 'playback')"
                    )
                if barge and Capability.PLAYBACK not in caps:
                    raise ValueError(
                        "wake.barge_via_device: true requires capability 'playback'"
                    )
            else:
                if Capability.DEVICE_WAKE in caps:
                    raise ValueError(
                        "capabilities: 'device_wake' requires wake.source: device"
                    )
                if Capability.BARGE_VIA_DEVICE in caps:
                    raise ValueError(
                        "capabilities: 'barge_via_device' requires wake.source: device"
                    )
                if barge:
                    raise ValueError(
                        "wake.barge_via_device: true requires wake.source: device"
                    )

            if Capability.BARGE_VIA_DEVICE in caps and (
                Capability.DEVICE_WAKE not in caps or Capability.PLAYBACK not in caps
            ):
                raise ValueError(
                    "capabilities: 'barge_via_device' requires 'device_wake' "
                    "and 'playback'"
                )
            return self

        @property
        def wake_source(self) -> WakeSource:
            """Effective wake source: an omitted block means RUI KWS."""
            return self.wake.source if self.wake is not None else WakeSource.RUI

        @property
        def barge_via_device_enabled(self) -> bool:
            """Whether this plug enables the device's advisory barge hint."""
            return bool(self.wake is not None and self.wake.barge_via_device)

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
