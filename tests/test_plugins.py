"""P2.1: Socket & Plug v0 manifests are validated, resolved and never executed.

Every test builds a throwaway plugin directory, so the suite pins the on-disk
contract (plugin.yaml + devices/*.yaml) rather than the Python models alone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from house_ear import __version__
from house_ear.device.contract import Capability, Transport
from house_ear.plugins import (
    DEVICE_SCHEMA,
    PLUGIN_SCHEMA,
    DeviceArtifact,
    DeviceClass,
    ParamType,
    PluginError,
    RuntimeMode,
    TrustLevel,
    VersionError,
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
    root.mkdir()
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
