"""P3.1: the Socket & Plug contract test kit.

These tests pin the external contract (manifest + fake adapter), not an audio
plane: the fake device produces 20 ms silence and an in-memory playback sink, so
nothing here needs ALSA, a network speaker or real audio hardware.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from rui.__main__ import main
from rui.plugins import (
    DEVICE_SCHEMA,
    DEVICE_SCHEMA_V1,
    PLUGIN_SCHEMA,
)
from rui.plugins import contract as kit
from rui.plugins.errors import PluginError

_YAML = YAML()
_YAML.default_flow_style = False

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "plugin-example"
EXAMPLE_ADAPTER = EXAMPLE / "rui_plugin_example.py"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        _YAML.dump(data, handle)


def _plugin_doc(**overrides) -> dict:
    doc = {
        "schema": PLUGIN_SCHEMA,
        "id": "contract-test-plugin",
        "name": "Contract Test Plug",
        "version": "1.0.0",
        "rui_compat": ">=0.2.0 <0.8.0",
        "license": "MIT",
        "maintainer": "Test <test@example.invalid>",
        "trust": "trusted",
        "devices": ["devices/example.yaml"],
    }
    doc.update(overrides)
    return doc


def _device_doc(**overrides) -> dict:
    doc = {
        "schema": DEVICE_SCHEMA_V1,
        "id": "contract-test-device",
        "model": "CT-01",
        "name": "Contract Test Speaker",
        "vendor": "Contract Test",
        "class": "A",
        "transport": "inbound",
        "protocol": {"kind": "ws", "version": "1"},
        "capabilities": ["playback", "volume", "led", "device_wake", "barge_via_device"],
        "wake": {"source": "device", "barge_via_device": True},
        "device_side": {
            "artifact": "client",
            "name": "test client",
            "source": "https://example.invalid/client",
            "version_min": "v1.0",
            "install_steps": ["Install the client."],
            "rollback": "Remove the client.",
        },
        "runtime": {"mode": "in-process", "entrypoint": "fake_adapter:build",
                    "listen_port": 4412},
        "params": [
            {"key": "host", "label": "Address", "type": "string", "required": True},
            {"key": "wake_words", "label": "Wake words", "type": "list",
             "default": ["你好小瑞"], "max": 3},
        ],
        "compatibility": {"rui_min": "0.2.0", "tested_with": ["0.2.2"]},
        "acceptance": {"verified": False, "last_verified": "", "evidence": "kit"},
    }
    doc.update(overrides)
    return doc


def _plugin(tmp_path: Path, *, plugin: dict | None = None, device: dict | None = None) -> Path:
    root = tmp_path / "plug"
    root.mkdir()
    _write(root / "plugin.yaml", plugin if plugin is not None else _plugin_doc())
    _write(root / "devices" / "example.yaml", device if device is not None else _device_doc())
    return root


def _run(capsys, *argv: str) -> tuple[int, str]:
    code = main(["plug-test", *argv])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def _run_json(capsys, *argv: str) -> tuple[int, dict]:
    code, text = _run(capsys, *argv, "--json")
    return code, json.loads(text)


def _write_broken_adapter(tmp_path: Path, body: str) -> Path:
    """A wrapper that subclasses the example fake and breaks one method."""
    path = tmp_path / "broken_adapter.py"
    path.write_text(
        "import importlib.util\n"
        f"_spec = importlib.util.spec_from_file_location('_example_fake', {str(EXAMPLE_ADAPTER)!r})\n"
        "_mod = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_mod)\n"
        "class Broken(_mod.FakeSpeakerAdapter):\n"
        + "".join(f"    {line}\n" for line in body.splitlines())
        + "\ndef build():\n"
        "    return Broken()\n",
        encoding="utf-8",
    )
    return path


def _load_example_adapter():
    spec = importlib.util.spec_from_file_location("_contract_example", EXAMPLE_ADAPTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Happy path + fixture
# --------------------------------------------------------------------------- #


def test_example_plugin_passes_manifest_checks(capsys) -> None:
    code, text = _run(capsys, str(EXAMPLE))
    assert code == 0
    assert "manifest.wake" in text
    assert "-> PASS" in text
    assert "FAIL" not in text


def test_example_plugin_passes_the_full_adapter_contract(capsys) -> None:
    code, report = _run_json(
        capsys, str(EXAMPLE), "--adapter", f"{EXAMPLE_ADAPTER}:build"
    )
    assert code == 0
    assert report["summary"]["verdict"] == "pass"
    assert report["summary"]["fail"] == 0
    checks = {item["id"]: item for item in report["checks"]}
    for required in (
        "adapter.methods",
        "device.ready",
        "capture.frames",
        "capture.cadence",
        "playback.sink",
        "poll.drain_signals",
        "poll.drain_wakes",
        "wake.idle_trigger",
        "wake.drain_once",
        "lifecycle.close",
        "lifecycle.after_close",
    ):
        assert required in checks, required
        assert checks[required]["status"] != "FAIL", checks[required]
    assert checks["wake.idle_trigger"]["status"] == "PASS"
    assert checks["wake.barge_policy"]["status"] == "PASS"


def test_fake_adapter_injects_a_device_wake_once() -> None:
    module = _load_example_adapter()
    adapter = module.build()

    async def run() -> None:
        await adapter.open()
        assert "device_wake" in {str(item) for item in (await adapter.probe()).capabilities}
        adapter.inject_wake(ts_ms=1_000.0, score=0.9, threshold=0.5, barge=False,
                            level=-24.0)
        first = adapter.drain_wakes()
        assert len(first) == 1
        event = first[0]
        assert event.ts_ms == 1_000.0
        assert event.score == 0.9
        assert event.threshold == 0.5
        assert event.barge is False
        assert event.source == "device"
        assert adapter.drain_wakes() == []
        await adapter.close()
        assert adapter.drain_wakes() == []

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Manifest failures
# --------------------------------------------------------------------------- #


def test_missing_plugin_manifest_exits_1(capsys, tmp_path: Path) -> None:
    code, text = _run(capsys, str(tmp_path / "does-not-exist"))
    assert code == 1
    assert "manifest.load" in text
    assert "FAIL" in text


def test_invalid_plugin_field_names_file_and_field(capsys, tmp_path: Path) -> None:
    plugin = _plugin_doc()
    plugin.pop("name")
    root = _plugin(tmp_path, plugin=plugin)

    code, text = _run(capsys, str(root))
    assert code == 1
    assert "plugin.yaml" in text
    assert "name" in text


def test_unknown_capability_exits_1(capsys, tmp_path: Path) -> None:
    root = _plugin(tmp_path, device=_device_doc(capabilities=["playback", "telepathy"]))

    code, text = _run(capsys, str(root))
    assert code == 1
    assert "telepathy" in text
    assert "capabilities" in text


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        # device_wake capability requires wake.source: device.
        (
            _device_doc(capabilities=["playback", "device_wake"], wake=None),
            "wake.source",
        ),
        # wake.source: device requires the device_wake capability.
        (
            _device_doc(capabilities=["playback"], wake={"source": "device"}),
            "device_wake",
        ),
        # barge_via_device: true requires the matching capability.
        (
            _device_doc(
                capabilities=["playback", "device_wake"],
                wake={"source": "device", "barge_via_device": True},
            ),
            "barge_via_device",
        ),
        # rui source forbids the v1 capabilities.
        (
            _device_doc(
                capabilities=["playback", "device_wake"],
                wake={"source": "rui"},
            ),
            "wake.source",
        ),
        # Unknown wake keys are not silently ignored.
        (
            _device_doc(
                capabilities=["playback", "device_wake"],
                wake={"source": "device", "token": "hunter2"},
            ),
            "token",
        ),
        # A v0 manifest cannot smuggle in the v1 wake block.
        (
            _device_doc(schema=DEVICE_SCHEMA, wake={"source": "rui"}),
            "v0",
        ),
    ],
)
def test_wake_rule_violations_exit_1(
    capsys, tmp_path: Path, device: dict, expected: str
) -> None:
    root = _plugin(tmp_path, device=device)

    code, text = _run(capsys, str(root))
    assert code == 1
    assert "manifest.load" in text
    assert expected.lower() in text.lower()


def test_third_party_in_process_is_rejected(capsys, tmp_path: Path) -> None:
    root = _plugin(
        tmp_path,
        plugin=_plugin_doc(trust="third-party"),
        device=_device_doc(runtime={"mode": "in-process", "entrypoint": "fake:build",
                                    "listen_port": 4412}),
    )

    code, text = _run(capsys, str(root))
    assert code == 1
    assert "third-party" in text
    assert "in-process" in text


def test_third_party_sidecar_manifest_passes(capsys, tmp_path: Path) -> None:
    device = _device_doc(
        capabilities=["playback"],
        wake=None,
        runtime={"mode": "sidecar", "image": "ghcr.io/example/plug:1.0.0",
                 "listen_port": 4412},
    )
    root = _plugin(tmp_path, plugin=_plugin_doc(trust="third-party"), device=device)

    code, report = _run_json(capsys, str(root))
    assert code == 0
    assert report["plugin"]["trust"] == "third-party"
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["manifest.runtime"]["status"] == "PASS"


# --------------------------------------------------------------------------- #
# Adapter failures
# --------------------------------------------------------------------------- #


def test_adapter_missing_drain_wakes_fails(capsys, tmp_path: Path) -> None:
    root = _plugin(tmp_path)
    adapter = _write_broken_adapter(tmp_path, "drain_wakes = None")

    code, report = _run_json(capsys, str(root), "--adapter", f"{adapter}:build")
    assert code == 1
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["adapter.methods"]["status"] == "FAIL"
    assert "drain_wakes" in checks["adapter.methods"]["detail"]
    assert report["summary"]["fail"] >= 1


def test_adapter_missing_play_fails(capsys, tmp_path: Path) -> None:
    root = _plugin(tmp_path)
    adapter = _write_broken_adapter(tmp_path, "play = None")

    code, text = _run(capsys, str(root), "--adapter", f"{adapter}:build")
    assert code == 1
    assert "play" in text
    assert "adapter.methods" in text


def test_adapter_import_failure_is_reported(capsys, tmp_path: Path) -> None:
    root = _plugin(tmp_path)

    code, report = _run_json(
        capsys, str(root), "--adapter", "module_that_does_not_exist_xyz:build"
    )
    assert code == 1
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["adapter.import"]["status"] == "FAIL"
    assert "module_that_does_not_exist_xyz" in checks["adapter.import"]["detail"]


def test_adapter_dotted_module_path_is_supported(capsys, tmp_path: Path, monkeypatch) -> None:
    root = _plugin(tmp_path)
    module = tmp_path / "contract_dotted_adapter.py"
    module.write_text(
        "import importlib.util\n"
        f"_spec = importlib.util.spec_from_file_location('_example', {str(EXAMPLE_ADAPTER)!r})\n"
        "_mod = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_mod)\n"
        "def build():\n"
        "    return _mod.FakeSpeakerAdapter()\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    code, report = _run_json(
        capsys, str(root), "--adapter", "contract_dotted_adapter:build"
    )
    assert code == 0
    assert report["summary"]["verdict"] == "pass"


def test_adapter_dotted_module_from_the_plugin_dir(capsys, tmp_path: Path) -> None:
    """The common third-party layout: adapter.py sits next to plugin.yaml."""
    root = _plugin(tmp_path)
    (root / "contract_root_adapter.py").write_text(
        "import importlib.util\n"
        f"_spec = importlib.util.spec_from_file_location('_example', {str(EXAMPLE_ADAPTER)!r})\n"
        "_mod = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_mod)\n"
        "def build():\n"
        "    return _mod.FakeSpeakerAdapter()\n",
        encoding="utf-8",
    )

    code, report = _run_json(
        capsys, str(root), "--adapter", "contract_root_adapter:build"
    )
    assert code == 0
    assert report["summary"]["verdict"] == "pass"


# --------------------------------------------------------------------------- #
# JSON shape and exit codes
# --------------------------------------------------------------------------- #


def test_json_report_shape(capsys) -> None:
    code, text = _run(
        capsys,
        str(EXAMPLE),
        "--adapter",
        f"{EXAMPLE_ADAPTER}:build",
        "--json",
    )
    assert code == 0
    assert text.lstrip().startswith("{")
    report = json.loads(text)
    assert report["schema"] == "contract-report/v1"
    assert report["kit"] == "rui-plug-test"
    assert report["rui"]
    assert report["plugin"]["id"] == "example-plugin"
    assert report["plugin"]["loader"] in {"core", "kit-v1-compat"}
    assert isinstance(report["plugin"]["devices"], list)
    assert report["adapter"].endswith(":build")
    assert report["summary"]["verdict"] == "pass"
    counts = report["summary"]
    assert counts["fail"] == 0
    assert counts["pass"] + counts["warn"] + counts["skip"] + counts["fail"] == len(
        report["checks"]
    )
    for check in report["checks"]:
        assert {"id", "status", "detail"} <= set(check)
        assert check["status"] in {"PASS", "FAIL", "WARN", "SKIP"}


def test_invalid_manifest_json_still_has_the_report_shape(capsys, tmp_path: Path) -> None:
    root = _plugin(tmp_path, device=_device_doc(capabilities=["playback", "nope"]))

    code, report = _run_json(capsys, str(root))
    assert code == 1
    assert report["summary"]["verdict"] == "fail"
    assert report["summary"]["fail"] == 1
    assert report["checks"][0]["id"] == "manifest.load"


def test_manifest_only_run_has_no_adapter(capsys) -> None:
    code, report = _run_json(capsys, str(EXAMPLE))
    assert code == 0
    assert report["adapter"] is None
    assert report["summary"]["verdict"] == "pass"


# --------------------------------------------------------------------------- #
# Compatibility layer (kit still works on a pre-v1 core)
# --------------------------------------------------------------------------- #


def test_v1_compat_layer_handles_a_pre_v1_core(monkeypatch, tmp_path: Path) -> None:
    root = _plugin(tmp_path)

    def old_loader(_path):
        raise PluginError(
            f"{root / 'devices' / 'example.yaml'}: schema: "
            "Input should be 'rui-device/v0' (got 'rui-device/v1')"
        )

    monkeypatch.setattr(kit, "load_plugin", old_loader)
    monkeypatch.setattr(kit, "_core_supports_v1", lambda: False)

    report = kit.run_contract(root)
    assert report.exit_code == 0
    assert report.loader == "kit-v1-compat"
    assert report.verdict == "pass"


def test_v1_compat_layer_still_rejects_bad_wake(monkeypatch, tmp_path: Path) -> None:
    device = _device_doc(capabilities=["playback", "device_wake"], wake=None)
    root = _plugin(tmp_path, device=device)

    def old_loader(_path):
        raise PluginError(
            f"{root / 'devices' / 'example.yaml'}: schema: "
            "Input should be 'rui-device/v0' (got 'rui-device/v1')"
        )

    monkeypatch.setattr(kit, "load_plugin", old_loader)
    monkeypatch.setattr(kit, "_core_supports_v1", lambda: False)

    report = kit.run_contract(root)
    assert report.exit_code == 1
    checks = {item.id: item for item in report.checks}
    assert checks["manifest.wake"].status == "FAIL"
    assert "device_wake" in checks["manifest.wake"].detail


def test_example_adapter_does_not_require_real_audio() -> None:
    """Guard the fixture's premise: it is pure asyncio, no ALSA/sounddevice imports."""
    module = _load_example_adapter()
    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("sounddevice", "pyaudio", "alsa", "socket"):
        assert forbidden not in source


def test_adapter_fixture_is_under_examples() -> None:
    assert EXAMPLE_ADAPTER.is_file()
    assert (EXAMPLE / "plugin.yaml").is_file()
    assert (EXAMPLE / "devices" / "example.yaml").is_file()
    assert "examples" in str(EXAMPLE_ADAPTER)


def test_sys_path_is_not_polluted_by_path_import(capsys, tmp_path: Path) -> None:
    before = list(sys.path)
    root = _plugin(tmp_path)
    adapter = _write_broken_adapter(tmp_path, "play = None")
    _run(capsys, str(root), "--adapter", f"{adapter}:build")
    assert sys.path == before


def test_example_adapter_implements_the_volume_contract() -> None:
    module = _load_example_adapter()
    adapter = module.build()

    asyncio.run(adapter.set_volume(100))
    assert adapter.volume == 255
    assert asyncio.run(adapter.read_volume()) == 255
    asyncio.run(adapter.set_volume(0))
    assert adapter.volume == 0
    # services is the low-level 0-255 surface the 0.3 plane calls.
    asyncio.run(adapter.services.set_volume(128))
    assert adapter.volume == 128
    with pytest.raises(ValueError):
        asyncio.run(adapter.services.set_volume(256))
    with pytest.raises(ValueError):
        asyncio.run(adapter.set_volume(101))
