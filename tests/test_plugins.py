"""P2.1: Socket & Plug v0 manifests are validated, resolved and never executed.

Every test builds a throwaway plugin directory, so the suite pins the on-disk
contract (plugin.yaml + devices/*.yaml) rather than the Python models alone.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from rui import __version__
from rui.device.contract import Capability, Transport
from rui.plugins import (
    DEVICE_SCHEMA,
    DEVICE_SCHEMA_V1,
    PLUGIN_SCHEMA,
    DeviceArtifact,
    DeviceClass,
    ParamType,
    PluginError,
    RuntimeMode,
    TrustLevel,
    VersionError,
    WakeSource,
    builtin_plugin,
    load_plugin,
    parse_range,
    parse_version,
)

_YAML = YAML()
_YAML.default_flow_style = False


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        _YAML.dump(data, handle)


def _plugin_doc(*, devices: list[str] | None = None, **overrides) -> dict:
    doc = {
        "schema": PLUGIN_SCHEMA,
        "id": "example-plugin",
        "name": "Example Speaker Plug",
        "version": "1.2.0",
        "rui_compat": ">=0.0.1 <999.0.0",
        "license": "MIT",
        "maintainer": "Example <dev@example.invalid>",
        "homepage": "https://example.invalid/plugin",
        "trust": "builtin",
        "devices": ["devices/a.yaml"],
    }
    if devices is not None:
        doc["devices"] = devices
    doc.update(overrides)
    return doc


def _device_doc(*, device_class: str = "A", capabilities: list[str] | None = None, **overrides) -> dict:
    doc = {
        "schema": DEVICE_SCHEMA,
        "id": "example-a",
        "name": "Example Speaker A",
        "vendor": "Example Inc.",
        "class": device_class,
        "transport": "inbound",
        "protocol": {"kind": "ws", "version": "1"},
        "capabilities": capabilities
        if capabilities is not None
        else ["mic_array", "reference", "playback", "led", "keys", "volume"],
        "device_side": {
            "artifact": "client",
            "name": "example client",
            "source": "https://example.invalid/client",
            "version_min": "v0.1",
            "install_steps": ["install the client", "confirm it reconnects"],
            "rollback": "remove the client",
        },
        "params": [
            {"key": "host", "label": "Speaker address", "type": "string", "required": True},
            {"key": "room", "label": "Room", "type": "string", "default": "客厅"},
            {
                "key": "wake_words",
                "label": "Wake words",
                "type": "list",
                "default": ["你好小瑞"],
                "max": 3,
            },
            {
                "key": "device_token",
                "label": "Device token",
                "type": "secret",
                "required": True,
                "help": "write-only; never rendered back",
            },
        ],
        "compatibility": {"rui_min": "0.1.0", "tested_with": ["0.1.0"], "notes": "manual"},
        "acceptance": {"verified": True, "last_verified": "2026-10-24", "evidence": "README"},
    }
    doc.update(overrides)
    return doc


def _write_plugin(tmp_path: Path, *, plugin: dict | None = None, devices: dict[str, dict] | None = None) -> Path:
    root = tmp_path / "rui-plugin-example"
    root.mkdir(parents=True)
    _write(root / "plugin.yaml", plugin if plugin is not None else _plugin_doc())
    for relative, device in (devices or {}).items():
        _write(root / relative, device)
    return root


def _single(tmp_path: Path, *, plugin: dict | None = None, device: dict | None = None) -> Path:
    return _write_plugin(
        tmp_path,
        plugin=plugin,
        devices={"devices/a.yaml": device if device is not None else _device_doc()},
    )


def test_loads_a_valid_multi_device_plugin(tmp_path: Path) -> None:
    root = _write_plugin(
        tmp_path,
        plugin=_plugin_doc(devices=["devices/a.yaml", "devices/b.yaml"]),
        devices={
            "devices/a.yaml": _device_doc(
                runtime={"entrypoint": "example_plugin:build"},
            ),
            "devices/b.yaml": _device_doc(
                id="example-b",
                name="Example Speaker B",
                device_class="B",
                transport="outbound",
                capabilities=["mic_array", "playback", "volume"],
                runtime={"mode": "sidecar", "image": "ghcr.io/example/plug-b:1.0.0"},
            ),
        },
    )
    plugin = load_plugin(root)

    assert plugin.id == "example-plugin"
    assert plugin.trust is TrustLevel.BUILTIN
    assert plugin.source_dir == root.resolve()
    assert [device.id for device in plugin.devices] == ["example-a", "example-b"]

    first = plugin.devices[0]
    assert first.device_class is DeviceClass.A
    assert first.transport is Transport.INBOUND
    assert Capability.REFERENCE in first.capabilities
    assert DeviceArtifact.CLIENT is first.device_side.artifact
    # builtin + no explicit mode -> in-process, entrypoint kept as text.
    assert first.runtime is not None
    assert first.runtime.mode is RuntimeMode.IN_PROCESS
    assert first.runtime.entrypoint == "example_plugin:build"
    assert first.runtime.listen_port == 4412

    second = plugin.devices[1]
    assert second.device_class is DeviceClass.B
    assert second.transport is Transport.OUTBOUND
    assert second.runtime is not None
    assert second.runtime.mode is RuntimeMode.SIDECAR
    assert second.runtime.image == "ghcr.io/example/plug-b:1.0.0"


@pytest.mark.parametrize("missing", ["schema", "id", "name", "version", "rui_compat", "devices"])
def test_plugin_manifest_missing_fields_names_the_field(tmp_path: Path, missing: str) -> None:
    doc = _plugin_doc()
    doc.pop(missing)
    root = _write_plugin(tmp_path, plugin=doc)

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "plugin.yaml" in message
    assert missing in message
    assert "Field required" in message


def test_device_manifest_missing_fields_names_the_device_file(tmp_path: Path) -> None:
    device = _device_doc()
    device.pop("class")
    root = _single(tmp_path, device=device)

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "devices/a.yaml" in message
    assert "class" in message
    assert "Field required" in message


def test_unknown_capability_is_an_error_with_the_bad_value(tmp_path: Path) -> None:
    root = _single(tmp_path, device=_device_doc(capabilities=["mic_array", "telepathy"]))

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "capabilities" in message
    assert "telepathy" in message


def test_incompatible_rui_compat_is_refused(tmp_path: Path) -> None:
    root = _single(tmp_path, plugin=_plugin_doc(rui_compat=">=9.0 <10.0"))

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "requires RUI >=9.0 <10.0" in message
    assert __version__ in message
    assert "refusing to load" in message


def test_third_party_in_process_is_rejected_not_coerced(tmp_path: Path) -> None:
    root = _single(
        tmp_path,
        plugin=_plugin_doc(trust="third-party"),
        device=_device_doc(runtime={"mode": "in-process", "entrypoint": "evil:run"}),
    )

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "third-party" in message
    assert "in-process" in message
    assert "sidecar" in message


def test_third_party_runtime_defaults_to_sidecar(tmp_path: Path) -> None:
    root = _single(
        tmp_path,
        plugin=_plugin_doc(trust="third-party"),
        device=_device_doc(runtime={"image": "ghcr.io/example/plug:1.0.0"}),
    )

    plugin = load_plugin(root)

    assert plugin.devices[0].runtime is not None
    assert plugin.devices[0].runtime.mode is RuntimeMode.SIDECAR


def test_sidecar_requires_an_image(tmp_path: Path) -> None:
    root = _single(
        tmp_path,
        plugin=_plugin_doc(trust="third-party"),
        device=_device_doc(runtime={"mode": "sidecar"}),
    )

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    assert "runtime.image is required" in str(error.value)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("../escape.yaml", "escapes the plugin directory"),
        ("devices/../../escape.yaml", "escapes the plugin directory"),
        ("/etc/escape.yaml", "must be a relative path"),
    ],
)
def test_device_entry_escaping_the_plugin_dir_is_rejected(
    tmp_path: Path, entry: str, expected: str
) -> None:
    _write(tmp_path / "escape.yaml", _device_doc())
    root = _write_plugin(tmp_path, plugin=_plugin_doc(devices=[entry]))

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "plugin.yaml" in message
    assert "escape.yaml" in message
    assert expected in message


def test_symlink_escaping_the_plugin_dir_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    _write(outside, _device_doc())
    root = _write_plugin(tmp_path, plugin=_plugin_doc(devices=["devices/link.yaml"]))
    (root / "devices").mkdir()
    (root / "devices" / "link.yaml").symlink_to(outside)

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    assert "escapes the plugin directory" in str(error.value)


def test_secret_param_type_is_accepted(tmp_path: Path) -> None:
    device = _device_doc(
        params=[{"key": "device_token", "label": "Device token", "type": "secret", "required": True}]
    )
    plugin = load_plugin(_single(tmp_path, device=device))

    param = plugin.devices[0].params[0]
    assert param.type is ParamType.SECRET
    assert param.required is True
    assert param.default is None


def test_secret_param_may_not_ship_a_default(tmp_path: Path) -> None:
    device = _device_doc(
        params=[
            {
                "key": "device_token",
                "label": "Device token",
                "type": "secret",
                "default": "hunter2",
            }
        ]
    )
    root = _single(tmp_path, device=device)

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    assert "must not declare a default" in str(error.value)


def test_entrypoint_is_validated_but_never_imported(tmp_path: Path) -> None:
    module = "definitely_not_a_real_plugin_module_xyz"
    root = _single(
        tmp_path,
        device=_device_doc(runtime={"mode": "in-process", "entrypoint": f"{module}:build"}),
    )

    plugin = load_plugin(root)

    assert plugin.devices[0].runtime is not None
    assert plugin.devices[0].runtime.entrypoint == f"{module}:build"
    assert module not in sys.modules


@pytest.mark.parametrize(
    "runtime",
    [
        {"mode": "in-process", "entrypoint": "../../evil:build"},
        {"mode": "sidecar", "image": "../evil"},
    ],
)
def test_runtime_code_references_must_not_be_paths(tmp_path: Path, runtime: dict) -> None:
    root = _single(tmp_path, device=_device_doc(runtime=runtime))

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    assert "runtime" in str(error.value)
    assert "path" in str(error.value)


def test_malformed_yaml_is_reported_without_a_traceback(tmp_path: Path) -> None:
    root = tmp_path / "rui-plugin-bad"
    root.mkdir()
    (root / "plugin.yaml").write_text("schema: [unclosed\n", encoding="utf-8")

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "cannot parse" in message
    assert "Traceback" not in message


def test_manifest_only_plug_needs_no_runtime(tmp_path: Path) -> None:
    root = _single(tmp_path, plugin=_plugin_doc(trust="third-party"))

    plugin = load_plugin(root)

    assert plugin.devices[0].runtime is None


@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.1.0", True), ("0.2.9", True), ("0.3.0", False), ("0.0.9", False)],
)
def test_doc_style_range_is_supported(version: str, expected: bool) -> None:
    assert parse_range(">=0.1 <0.3").matches(version) is expected


def test_semver_prerelease_ordering() -> None:
    assert parse_version("1.2.3-alpha.1") < parse_version("1.2.3-alpha.2")
    assert parse_version("1.2.3-alpha.2") < parse_version("1.2.3")
    assert parse_range(">=1.0.0 <2.0.0").matches("1.5.0")


def test_bad_range_reports_the_offending_token() -> None:
    with pytest.raises(VersionError) as error:
        parse_range(">=0.1 || <0.3")

    assert "||" in str(error.value)


# --- Socket & Plug v1: the wake block and its dependency table ----------------------


def _v1_device_doc(**overrides) -> dict:
    doc = _device_doc(**overrides)
    doc["schema"] = DEVICE_SCHEMA_V1
    return doc


def test_a_v1_device_wake_manifest_loads(tmp_path: Path) -> None:
    device = _v1_device_doc(
        id="dot2-model",
        model="RS03QR",
        capabilities=["mic_array", "playback", "device_wake", "barge_via_device"],
        wake={"source": "device", "barge_via_device": True},
    )
    plugin = load_plugin(_single(tmp_path, device=device))

    loaded = plugin.devices[0]
    assert loaded.schema == DEVICE_SCHEMA_V1
    assert loaded.model == "RS03QR"
    assert loaded.wake_source is WakeSource.DEVICE
    assert loaded.barge_via_device_enabled is True
    assert Capability.DEVICE_WAKE in loaded.capabilities


def test_a_v0_manifest_defaults_to_rui_wake(tmp_path: Path) -> None:
    plugin = load_plugin(_single(tmp_path, device=_device_doc()))
    assert plugin.devices[0].wake_source is WakeSource.RUI
    assert plugin.devices[0].barge_via_device_enabled is False


def test_a_v0_manifest_cannot_declare_the_v1_wake_block(tmp_path: Path) -> None:
    device = _device_doc(wake={"source": "rui"})
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))
    assert "rui-device/v1 only" in str(error.value)


def test_device_source_without_the_capability_is_rejected(tmp_path: Path) -> None:
    device = _v1_device_doc(
        capabilities=["mic_array", "playback"],
        wake={"source": "device"},
    )
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))

    message = str(error.value)
    assert "wake.source" in message
    assert "device_wake" in message


def test_barge_hint_requires_device_source(tmp_path: Path) -> None:
    device = _v1_device_doc(wake={"source": "rui", "barge_via_device": True})
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))

    message = str(error.value)
    assert "barge_via_device" in message
    assert "wake.source: device" in message


def test_barge_capability_needs_playback(tmp_path: Path) -> None:
    device = _v1_device_doc(
        capabilities=["device_wake", "barge_via_device"],
        wake={"source": "device", "barge_via_device": True},
    )
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))

    assert "playback" in str(error.value)


def test_device_wake_capability_without_the_wake_block_is_rejected(tmp_path: Path) -> None:
    # A capability that can never fire is a manifest bug, not a quiet no-op (§2).
    device = _v1_device_doc(capabilities=["playback", "device_wake"])
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))

    assert "wake.source: device" in str(error.value)


def test_device_wake_capability_on_rui_source_is_rejected(tmp_path: Path) -> None:
    device = _v1_device_doc(
        capabilities=["playback", "device_wake"],
        wake={"source": "rui"},
    )
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))

    assert "requires wake.source: device" in str(error.value)


def test_native_kws_is_deprecated_in_v1(tmp_path: Path) -> None:
    device = _v1_device_doc(capabilities=["playback", "native_kws"])
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))

    assert "native_kws" in str(error.value)
    assert "deprecated" in str(error.value)


def test_unknown_wake_fields_are_rejected(tmp_path: Path) -> None:
    device = _v1_device_doc(
        capabilities=["playback", "device_wake"],
        wake={"source": "device", "token": "hunter2"},
    )
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))

    message = str(error.value)
    assert "wake" in message
    assert "token" in message


def test_lx06_is_a_v1_plug_that_still_wakes_through_rui_kws() -> None:
    plugin = builtin_plugin("xiaoai-lx06")
    assert plugin is not None
    device = plugin.devices[0]
    # 0.6 M1: the builtin is rui-device/v1 so it can declare audio_defaults. Wake is
    # still RUI KWS because the block is omitted (the v1 default is source: rui), and
    # the manifest must never claim device_wake/barge_via_device.
    assert device.schema == DEVICE_SCHEMA_V1
    assert device.wake is None, "an omitted wake block means source: rui"
    assert device.wake_source is WakeSource.RUI
    assert Capability.DEVICE_WAKE not in device.capabilities
    assert Capability.BARGE_VIA_DEVICE not in device.capabilities
    # The hardware numbers the core used to hard-code now live here.
    defaults = device.audio_defaults
    assert defaults is not None and defaults.capture is not None
    assert (defaults.capture.rate, defaults.capture.channels) == (48000, 8)
    assert (defaults.capture.mic_channels, defaults.capture.ref_channels) == (6, 2)
    assert (defaults.capture.bits, defaults.capture.mic_shift,
            defaults.capture.ref_shift) == (32, 12, 16)
    assert defaults.ack_echo_ignore_ms == 1200


# --- 0.6 M1: the optional audio_defaults block (docs/socket-plug-v1.md §3.2.1) -----


def test_a_v1_manifest_declares_audio_defaults(tmp_path: Path) -> None:
    device = _v1_device_doc(
        capabilities=["playback"],
        audio_defaults={
            "capture": {"rate": 48000, "channels": 8, "mic_channels": 6,
                        "ref_channels": 2, "bits": 32, "mic_shift": 12,
                        "ref_shift": 16},
            "playback": {"rate": 48000, "channels": 1},
            "ack_echo_ignore_ms": 2500,
        },
    )
    loaded = load_plugin(_single(tmp_path, device=device)).devices[0]

    defaults = loaded.audio_defaults
    assert defaults is not None
    assert defaults.capture is not None and defaults.playback is not None
    assert (defaults.capture.mic_channels, defaults.capture.ref_channels) == (6, 2)
    assert defaults.playback.rate == 48000
    assert defaults.ack_echo_ignore_ms == 2500


def test_audio_defaults_is_optional_for_a_v1_manifest(tmp_path: Path) -> None:
    # The compatibility shim: a plug that declares nothing gets the neutral core
    # defaults, exactly like every pre-0.6 plug.
    loaded = load_plugin(_single(tmp_path, device=_v1_device_doc())).devices[0]
    assert loaded.audio_defaults is None


def test_a_v0_manifest_cannot_declare_audio_defaults(tmp_path: Path) -> None:
    # §7: v0 + a new behaviour-changing field is an error, never a silent upgrade.
    device = _device_doc(audio_defaults={"capture": {"rate": 48000}})
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))
    message = str(error.value)
    assert "audio_defaults" in message
    assert "rui-device/v1" in message


def test_unknown_audio_defaults_keys_are_rejected(tmp_path: Path) -> None:
    device = _v1_device_doc(audio_defaults={"capture": {"rate": 48000, "eq": "x"}})
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))
    assert "audio_defaults" in str(error.value)


def test_audio_defaults_capture_layout_must_add_up(tmp_path: Path) -> None:
    # A partial hardware layout that cannot merge with the neutral core defaults is a
    # manifest bug; the error names the field instead of failing later in the plane.
    device = _v1_device_doc(audio_defaults={"capture": {"channels": 8}})
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))
    message = str(error.value)
    assert "audio_defaults.capture" in message
    assert "must equal channels(8)" in message


def test_a_v0_manifest_cannot_declare_the_v1_model_field(tmp_path: Path) -> None:
    # §7: v0 + new fields is an error, never a silent upgrade.
    device = _device_doc(model="RS03QR")
    with pytest.raises(PluginError) as error:
        load_plugin(_single(tmp_path, device=device))
    assert "rui-device/v1 only" in str(error.value)


# --- v1 §3.2/§3.3: DeviceSide, params, acceptance and builtin parity ---------------


def _device_side(**overrides) -> dict:
    doc = _device_doc(device_class=overrides.pop("device_class", "A"))
    side = dict(doc["device_side"])
    side.update(overrides)
    doc["device_side"] = side
    return doc


@pytest.mark.parametrize(
    "source",
    ["file:///etc/passwd", "ftp://example.invalid/client", "https://", "not a url"],
)
def test_device_side_source_must_be_http_or_https(tmp_path: Path, source: str) -> None:
    root = _single(tmp_path, device=_device_side(source=source))

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "device_side.source" in message
    assert "http" in message


def test_device_side_source_must_not_contain_credentials(tmp_path: Path) -> None:
    root = _single(tmp_path, device=_device_side(source="https://user:pass@example.invalid/x"))

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    assert "username or password" in str(error.value)


def test_device_side_install_steps_are_bounded(tmp_path: Path) -> None:
    root = _single(tmp_path, device=_device_side(install_steps=["x"] * 21))

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    assert "install_steps" in str(error.value)


def test_device_side_step_length_is_bounded(tmp_path: Path) -> None:
    root = _single(tmp_path, device=_device_side(install_steps=["x" * 501]))

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "install_steps" in message
    assert "500" in message


def test_a_class_b_device_needs_a_rollback(tmp_path: Path) -> None:
    root = _single(
        tmp_path, device=_device_side(device_class="B", rollback="   ")
    )

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "rollback" in message
    assert "class B" in message


def test_a_class_a_device_may_omit_the_rollback(tmp_path: Path) -> None:
    plugin = load_plugin(_single(tmp_path, device=_device_side(rollback="")))
    assert plugin.devices[0].device_side is not None


def test_list_max_must_cover_the_default(tmp_path: Path) -> None:
    device = _device_doc(
        params=[
            {
                "key": "wake_words",
                "label": "Wake words",
                "type": "list",
                "default": ["a", "b", "c", "d"],
                "max": 3,
            }
        ]
    )
    root = _single(tmp_path, device=device)

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    message = str(error.value)
    assert "wake_words" in message
    assert "max is 3" in message


def test_verified_acceptance_needs_a_date(tmp_path: Path) -> None:
    root = _single(
        tmp_path,
        device=_device_doc(acceptance={"verified": True, "last_verified": ""}),
    )

    with pytest.raises(PluginError) as error:
        load_plugin(root)

    assert "last_verified" in str(error.value)


def test_builtin_uses_the_v1_matrix() -> None:
    builtin = builtin_plugin("xiaoai-lx06")
    assert builtin is not None
    # 0.6 M1: declaring audio_defaults requires rui-device/v1, whose matrix floor is
    # 0.3.0 (docs/socket-plug-v1.md §7). 0.6 M2: the manifest now ships in
    # tests/fixtures/legacy_lx06/ and its ceiling leaves room for the 0.7.x line.
    assert builtin.rui_compat == ">=0.3.0 <0.8.0"
    device = builtin.devices[0]
    assert device.schema == DEVICE_SCHEMA_V1
    assert device.compatibility is not None
    assert device.compatibility.rui_min == "0.3.0"
    plugin_range = parse_range(builtin.rui_compat)
    minimum = parse_version(device.compatibility.rui_min, allow_partial=True)
    assert plugin_range.matches(str(minimum))
    assert plugin_range.matches(__version__)


def test_the_example_v1_plugin_uses_the_v1_version_matrix() -> None:
    repo = Path(__file__).resolve().parents[1]
    example = load_plugin(repo / "examples" / "plugin-example")
    assert example.rui_compat == ">=0.3.0 <0.8.0"
    device = example.devices[0]
    assert device.schema == DEVICE_SCHEMA_V1
    assert device.compatibility is not None
    assert device.compatibility.rui_min == "0.3.0"
    assert parse_range(example.rui_compat).matches(__version__)


def test_a_v0_manifest_cannot_declare_device_wake_or_barge(tmp_path: Path) -> None:
    for capability in ("device_wake", "barge_via_device"):
        device = _device_doc(capabilities=["playback", capability])
        with pytest.raises(PluginError) as error:
            load_plugin(_single(tmp_path / capability, device=device))
        message = str(error.value)
        assert capability in message
        assert "rui-device/v1" in message


def test_v0_load_warns_once_per_path(tmp_path: Path, caplog) -> None:
    root = _single(tmp_path)
    with caplog.at_level(logging.WARNING, logger="rui.plugins.loader"):
        load_plugin(root)
        load_plugin(root)
    messages = [
        record.message for record in caplog.records
        if "uses rui-device/v0" in record.message
    ]
    assert len(messages) == 1
    assert "0.6.0" in messages[0]
