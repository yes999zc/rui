"""``rui-adapter-check``: the device-adapter conformance kit (third-party bridge entry).

Two fixture plugs under ``tests/fixtures/adapter-check`` drive these tests:

* ``pass/`` - the smallest plug whose adapter satisfies every contract member;
* ``fail/`` - one adapter without ``play()`` and one whose ``frames()`` yields a dict
  instead of ``AudioFrame``, chosen with ``--device-id``.

The assertions are about the kit's *output contract* (``PASS <check>`` / ``FAIL <check>:
reason``, exit 1 on any FAIL), not about its internals, so a bridge author can script
against exactly what the tests read here.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rui.device.adapter_check import main, run_checks

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "adapter-check"
PASS_PLUG = FIXTURES / "pass"
FAIL_PLUG = FIXTURES / "fail"

#: Every line the PASS fixture must produce. The list is the contract surface, so a new
#: check added without covering the reference shape fails here instead of silently
#: becoming a warning.
PASS_LINES = (
    "PASS kit.self_check:",
    "PASS manifest.load:",
    "PASS manifest.devices:",
    "PASS manifest.runtime_mode:",
    "PASS manifest.entrypoint:",
    "PASS manifest.token_env:",
    "PASS manifest.capabilities:",
    "PASS adapter.import:",
    "PASS adapter.construct:",
    "PASS adapter.methods:",
    "PASS device.probe:",
    "PASS device.capabilities:",
    "PASS device.open:",
    "PASS capture.frames:",
    "PASS playback.play:",
    "PASS playback.sink:",
    "PASS device.read_volume:",
    "PASS device.drain_signals:",
    "PASS device.drain_wakes:",
    "PASS lifecycle.close:",
    "PASS lifecycle.after_close:",
)


def _run(capsys, *args: str) -> tuple[int, str]:
    code = main(list(args))
    return code, capsys.readouterr().out


# ---------------------------------------------------------------- the PASS template
def test_the_pass_fixture_passes_every_contract_check(capsys) -> None:
    code, out = _run(capsys, "--adapter", str(PASS_PLUG))

    assert code == 0, out
    for line in PASS_LINES:
        assert line in out, f"missing {line!r} in:\n{out}"
    assert "summary: pass=" in out and "fail=0" in out and "warn=0" in out
    assert out.rstrip().endswith("-> PASS")


def test_the_self_check_runs_the_reference_mock_adapter(capsys) -> None:
    """A PASS must mean the checks work: the kit runs rui.device.mock first."""
    _code, out = _run(capsys, "--adapter", str(PASS_PLUG))

    assert "reference MockAdapter" in out
    assert "PASS kit.self_check:" in out


def test_the_self_check_can_be_skipped(capsys) -> None:
    code, out = _run(capsys, "--adapter", str(PASS_PLUG), "--no-self-check")

    assert code == 0, out
    assert "kit.self_check" not in out


def test_the_reference_mock_adapter_also_passes_as_a_target(capsys) -> None:
    """`rui.device.mock:build` is the second implementation: the kit accepts it too, and
    its wake-injection hook is what proves the drain_wakes payload check."""
    code, out = _run(capsys, "--adapter", "rui.device.mock:build", "--no-self-check")

    assert code == 0, out
    assert "PASS device.drain_wakes: 1 event(s) from injected" in out
    assert "manifest." not in out


# ------------------------------------------------------------------ the FAIL fixtures
def test_a_missing_play_method_is_named_and_exits_non_zero(capsys) -> None:
    code, out = _run(capsys, "--adapter", str(FAIL_PLUG))

    assert code == 1, out
    assert "FAIL adapter.methods: missing callable method(s): play" in out
    assert "FAIL playback.play:" in out
    assert "-> FAIL" in out


def test_frames_that_are_not_audio_frames_fail_capture(capsys) -> None:
    code, out = _run(capsys, "--adapter", str(FAIL_PLUG), "--device-id", "fail-bad-frames")

    assert code == 1, out
    assert "FAIL capture.frames:" in out
    assert "AudioFrame" in out
    assert "-> FAIL" in out


def test_an_unknown_device_id_fails_with_the_available_ids(capsys) -> None:
    code, out = _run(capsys, "--adapter", str(FAIL_PLUG), "--device-id", "nope")

    assert code == 1, out
    assert "FAIL manifest.device:" in out
    assert "fail-missing-play" in out


def test_a_missing_plug_directory_fails_readably(capsys) -> None:
    code, out = _run(capsys, "--adapter", str(FIXTURES / "does-not-exist"))

    assert code == 1, out
    assert "FAIL manifest.load:" in out
    assert "no such plug directory" in out


def test_a_duck_typed_adapter_without_read_volume_is_a_warning(capsys) -> None:
    """The repo's own CI plug (``tests/fixtures/sample-plug``) never reports a level.

    The core reads the mixer through ``services.read_volume()`` and the Protocol default
    returns None, so a bridge that omits ``read_volume`` must still pass - with a WARN that
    says how to opt in. A hard FAIL here would reject every duck-typed adapter, including
    the fixture ``rui plug-test`` has shipped since 0.7.5.
    """
    code, out = _run(capsys, "--adapter", str(REPO / "tests" / "fixtures" / "sample-plug"))

    assert code == 0, out
    assert "WARN device.read_volume:" in out
    assert "-> PASS" in out


# ------------------------------------------------------------- module:attr entry points
def test_module_attr_mode_checks_the_adapter_without_a_manifest(capsys) -> None:
    code, out = _run(capsys, "--adapter", f"{PASS_PLUG / 'pass_adapter.py'}:build")

    assert code == 0, out
    assert "PASS adapter.import:" in out
    assert "manifest." not in out


def test_run_checks_returns_the_report_and_prints_nothing(capsys) -> None:
    report = run_checks(str(PASS_PLUG), self_check=False)

    assert capsys.readouterr().out == ""
    assert report.exit_code == 0
    assert report.counts()["pass"] >= len(PASS_LINES) - 1  # -1: kit.self_check not run
    ids = {check.id for check in report.checks}
    assert {"adapter.methods", "device.probe", "capture.frames"} <= ids


# --------------------------------------------------------------- the console command
def test_the_wrapper_script_runs_the_same_kit() -> None:
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "rui_adapter_check.py"), "--adapter",
         str(PASS_PLUG)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS adapter.methods:" in result.stdout
    assert "-> PASS" in result.stdout
