"""The device-adapter conformance kit: ``rui-adapter-check``.

A third-party bridge is one class that implements :class:`rui.device.contract.DeviceAdapter`.
Before deploying it, its author needs one answer: *will the core actually be able to drive
this thing?* This kit is that answer. Point it at a plug directory (``plugin.yaml`` is read
with the real :func:`rui.plugins.loader.load_plugin`, so the manifest rules are the shipping
ones) or at a bare ``module:attr``, and it exercises the adapter against a fake device and a
recording sink - no speaker, no audio plane, no network.

It is a sibling of ``rui-plug-test`` (:mod:`rui.plugins.contract`), not a replacement: that
kit validates a whole plug from the outside and has an adapter smoke test of its own; this
one is the adapter author's inner loop - one line per contract member, and the reference
implementation (:class:`rui.device.mock.MockAdapter`) is itself run through the *same* checks
first, so a PASS here can never come from a vacuous check.

The protocol types come from :mod:`rui.device.contract` and the reference device from
:mod:`rui.device.mock`; nothing about the contract is copied into this module. The generic
primitives (import, build, call-with-timeout, frame validation) are reused from
:mod:`rui.plugins.contract` instead of being reimplemented.

Exit code is ``0`` when every check is PASS or WARN and ``1`` when any check is FAIL. A WARN
is advisory ("the kit cannot observe this from here") and never fails the run.

    rui-adapter-check --adapter ../rui-plugin-template
    rui-adapter-check --adapter my_plug.adapter:build
    rui-adapter-check --adapter ./adapter.py:build --no-self-check
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rui.device.contract import (
    AudioFrame,
    Capability,
    DeviceInfo,
    Transport,
    effective_capabilities,
    probe_only_capabilities,
    wake_validation_error,
)
from rui.device.mock import MockAdapter, MockServices
from rui.device.mock import build as build_mock
from rui.plugins.contract import (  # the plug kit's generic primitives, reused as-is
    FAIL,
    PASS,
    WARN,
    Check,
    _adapter_methods,
    _brief,
    _build_adapter,
    _call,
    _caps_of,
    _import_target,
    _split_adapter,
    _validate_frame,
)
from rui.plugins.loader import load_plugin

__all__ = ["KIT_NAME", "Report", "main", "run_checks"]

KIT_NAME = "rui-adapter-check"

#: Same bounds as ``rui-plug-test``: a bridge that needs longer than this against a fake
#: device is not going to keep up with the audio plane either.
PROBE_TIMEOUT_S = 5.0
OPEN_TIMEOUT_S = 10.0
FRAME_TIMEOUT_S = 5.0
#: Frames consumed before ``aclose()``; three is enough to prove the iterator yields.
FRAME_COUNT = 3
#: Device mixer values are 0-255 at the adapter boundary (Socket & Plug v1 §4.1).
DEVICE_VOLUME_MIN = 0
DEVICE_VOLUME_MAX = 255
@dataclass
class Report:
    """The PASS/FAIL/WARN lines of one run, plus the summary."""

    target: str
    checks: list[Check] = field(default_factory=list)

    def add(self, check_id: str, status: str, detail: str = "") -> Check:
        check = Check(id=check_id, status=status, detail=detail)
        self.checks.append(check)
        return check

    def has_failed(self) -> bool:
        return any(check.status == FAIL for check in self.checks)

    @property
    def exit_code(self) -> int:
        return 1 if self.has_failed() else 0

    def counts(self) -> dict[str, int]:
        counts = {PASS.lower(): 0, FAIL.lower(): 0, WARN.lower(): 0}
        for check in self.checks:
            key = check.status.lower()
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_text(self) -> str:
        lines = [f"{KIT_NAME}: {self.target}"]
        for check in self.checks:
            line = f"{check.status:4} {check.id}"
            if check.detail:
                line += f": {check.detail}"
            lines.append(line)
        counts = self.counts()
        verdict = FAIL if self.has_failed() else PASS
        lines.append("")
        lines.append(
            f"summary: pass={counts.get('pass', 0)} fail={counts.get('fail', 0)} "
            f"warn={counts.get('warn', 0)} -> {verdict}"
        )
        return "\n".join(lines)


@dataclass
class Target:
    """What ``--adapter`` resolved to: an entry point plus the manifest facts behind it."""

    #: Directory put on ``sys.path`` when the entry point's module is not installed.
    root: Path
    #: ``module:attr`` handed to the importer.
    spec: str
    #: Device id from the manifest (``module:attr`` mode has a synthetic one).
    device_id: str = ""
    #: ``None`` in ``module:attr`` mode: no manifest was read, so no cross-check is possible.
    manifest_caps: frozenset[Capability] | None = None
    manifest_transport: Transport | None = None


# --------------------------------------------------------------------------- #
# Target resolution (plugin directory / plugin.yaml / module:attr)
# --------------------------------------------------------------------------- #


def _looks_like_path(raw: str) -> bool:
    return raw.endswith("plugin.yaml") or "/" in raw or "\\" in raw or raw.startswith(".")


def _path_part(raw: str) -> str:
    """``/path/to/adapter.py:build`` -> ``/path/to/adapter.py``; a module stays intact."""
    head, separator, _attribute = raw.rpartition(":")
    if separator and ("/" in head or "\\" in head or head.endswith(".py")):
        return head
    return raw


def _resolve_target(raw: str, device_id: str, report: Report) -> Target | None:
    """Resolve ``--adapter`` into a :class:`Target`, reporting manifest checks on the way.

    A directory (or a ``plugin.yaml`` path) is read with the real loader; a ``module:attr``
    (including the ``./adapter.py:build`` file form) stays manifest-free. Returns ``None``
    when nothing can be checked.
    """
    path = Path(_path_part(raw)).expanduser()
    if not path.exists():
        if not _looks_like_path(_path_part(raw)):
            return Target(root=Path.cwd(), spec=raw)
        report.add("manifest.load", FAIL, f"no such plug directory, plugin.yaml or file: {raw}")
        return None
    if path.is_file():
        if path.name != "plugin.yaml":
            # An adapter file: module:attr mode with the file's directory as the import root.
            return Target(root=path.parent, spec=raw)
        path = path.parent
    return _manifest_target(path, device_id, report)


def _manifest_target(root: Path, device_id: str, report: Report) -> Target | None:
    try:
        plugin = load_plugin(root)
    except Exception as exc:  # noqa: BLE001 - a bad manifest is the thing being reported
        report.add("manifest.load", FAIL, f"{type(exc).__name__}: {exc}")
        return None
    report.add(
        "manifest.load",
        PASS,
        f"loaded {plugin.id} {plugin.version} (trust={plugin.trust}) with the real loader",
    )

    devices = list(plugin.devices)
    ids = [str(device.id) for device in devices]
    if not devices:
        report.add("manifest.devices", FAIL, "the plug declares no devices")
        return None
    if len(set(ids)) != len(ids):
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        report.add("manifest.devices", FAIL, "duplicate device id(s): " + ", ".join(duplicates))
        return None
    report.add("manifest.devices", PASS, f"{len(devices)} device(s): " + ", ".join(ids))

    wanted = device_id.strip()
    chosen = None
    if wanted:
        chosen = next((device for device in devices if str(device.id) == wanted), None)
        if chosen is None:
            report.add(
                "manifest.device",
                FAIL,
                f"--device-id {wanted!r} is not one of: " + ", ".join(ids),
            )
            return None
    else:
        runnable = [device for device in devices if _entrypoint_of(device)]
        if len(runnable) > 1:
            report.add(
                "manifest.device",
                WARN,
                "several devices declare a runtime.entrypoint; checking the first "
                f"({runnable[0].id}); pass --device-id to choose another",
            )
        chosen = runnable[0] if runnable else devices[0]

    runtime = getattr(chosen, "runtime", None)
    mode = str(getattr(runtime, "mode", "") or "") if runtime is not None else ""
    if mode == "sidecar":
        report.add(
            "manifest.runtime_mode",
            FAIL,
            "runtime.mode 'sidecar' is not implemented in 0.8.x; a bridge must run "
            "in-process, which needs trust: builtin|trusted in the plug manifest",
        )
        return None
    entrypoint = _entrypoint_of(chosen)
    if runtime is None or not entrypoint:
        report.add(
            "manifest.runtime",
            FAIL,
            f"device {chosen.id!r} declares no runtime block with an entrypoint; there is "
            "no adapter for the core to load (add runtime: {mode: in-process, entrypoint: "
            '"module:build", listen_port: ...})',
        )
        return None
    report.add("manifest.runtime_mode", PASS, f"mode={mode or 'in-process'}")

    report.add("manifest.entrypoint", PASS, entrypoint)

    # `load_plugin` already validated the name (RuntimeSpec token_env); report which one
    # this device would use and whether the manifest chose it or the default applies.
    explicit = "token_env" in getattr(runtime, "model_fields_set", set())
    token_env = str(getattr(runtime, "token_env", "") or "")
    report.add(
        "manifest.token_env",
        PASS,
        f"{token_env} ({'declared' if explicit else 'default'})",
    )

    caps = frozenset(getattr(chosen, "capabilities", ()) or ())
    report.add(
        "manifest.capabilities",
        PASS,
        f"{len(caps)} declared: " + ", ".join(sorted(str(cap) for cap in caps)),
    )

    transport = getattr(chosen, "transport", None)
    return Target(
        root=root,
        spec=entrypoint,
        device_id=str(chosen.id),
        manifest_caps=caps,
        manifest_transport=transport if isinstance(transport, Transport) else None,
    )


def _entrypoint_of(device: Any) -> str:
    runtime = getattr(device, "runtime", None)
    return str(getattr(runtime, "entrypoint", "") or "") if runtime is not None else ""


# --------------------------------------------------------------------------- #
# Reference self-check
# --------------------------------------------------------------------------- #


def _self_check(report: Report) -> None:
    """Run the same surface checks against ``rui.device.mock``'s reference adapter.

    This is what makes the kit trustworthy: the reference implementation is the second
    implementation of the contract (``rui.device.mock``), and it must pass every check the
    kit applies to a third-party adapter. It also exercises the wake-injection path, which
    a real bridge only reports when its device wakes.
    """
    device = SimpleNamespace(id="mock", host="127.0.0.1", room="adapter-check")
    config = SimpleNamespace(data_dir=".", config_path=None, devices=[], plugs_dir=".")
    try:
        adapter = build_mock(device, config)
    except Exception as exc:  # noqa: BLE001 - the reference must always build
        report.add("kit.self_check", FAIL, f"cannot build the reference MockAdapter: {_brief(exc)}")
        return
    if not isinstance(adapter, MockAdapter):
        report.add("kit.self_check", FAIL, "rui.device.mock.build() did not return a MockAdapter")
        return
    # The reference device must carry the reference services object: that is what the
    # ``read_volume`` check reads through the Protocol's default implementation, so the
    # kit is not passing that line by accident of an empty adapter.
    if not isinstance(getattr(adapter, "services", None), MockServices):
        report.add(
            "kit.self_check",
            FAIL,
            "the reference MockAdapter did not attach MockServices; the read_volume path "
            "would not be exercised",
        )
        return
    reference = Target(root=Path.cwd(), spec="rui.device.mock:build", device_id="mock")
    checks: list[Check] = []
    try:
        asyncio.run(_check_object(adapter, reference, checks))
    except Exception as exc:  # noqa: BLE001 - a kit error is a FAIL, never a traceback
        report.add("kit.self_check", FAIL, f"{type(exc).__name__}: {exc}")
        return
    failures = [check for check in checks if check.status == FAIL]
    if failures:
        report.add(
            "kit.self_check",
            FAIL,
            "the reference MockAdapter fails the kit's own checks: "
            + "; ".join(f"{check.id}: {check.detail}" for check in failures),
        )
        return
    report.add(
        "kit.self_check",
        PASS,
        f"reference MockAdapter ({len(checks)} checks) passes",
    )


# --------------------------------------------------------------------------- #
# Adapter checks
# --------------------------------------------------------------------------- #


async def _check_adapter(target: Target, report: Report) -> None:
    try:
        module, attribute = _split_adapter(target.spec)
        entry = _import_target(module, attribute, target.root)
    except Exception as exc:  # noqa: BLE001 - adapter code/data is what we are reporting
        report.add("adapter.import", FAIL, _brief(exc))
        return
    if not callable(entry):
        report.add("adapter.import", FAIL, f"{target.spec} is not callable")
        return
    report.add("adapter.import", PASS, f"{target.spec} imported")

    try:
        adapter = _build_adapter(entry, _fake_device(target), target.root)
        if inspect.isawaitable(adapter):
            adapter = await adapter
    except Exception as exc:  # noqa: BLE001 - the builder is the developer's code
        report.add("adapter.construct", FAIL, _brief(exc))
        return
    if adapter is None:
        report.add("adapter.construct", FAIL, "the builder returned None")
        return
    report.add("adapter.construct", PASS, f"built {type(adapter).__name__}")

    await _check_object(adapter, target, report.checks)


def _fake_device(target: Target) -> SimpleNamespace:
    """The per-device object an adapter's ``build(device, config)`` receives."""
    device_id = target.device_id or "bridge-device"
    return SimpleNamespace(
        id=device_id,
        adapter=device_id,
        model="BRIDGE-1",
        host="127.0.0.1",
        room="adapter-check",
        params={"host": "127.0.0.1"},
        device_id=device_id,
    )


async def _check_object(adapter: Any, target: Target, checks: list[Check]) -> None:
    """Every surface check against one adapter instance; appends to ``checks``.

    Used twice: once for the reference mock (``kit.self_check``) and once for the adapter
    under test. Appending to a plain list keeps the self-check from printing its lines.
    """
    scratch = Report(target=target.spec, checks=checks)

    missing, wrong = _adapter_methods(adapter)
    if missing or wrong:
        details = []
        if missing:
            details.append("missing callable method(s): " + ", ".join(missing))
        if wrong:
            details.append("must be synchronous (not async): " + ", ".join(wrong))
        scratch.add("adapter.methods", FAIL, "; ".join(details))
    else:
        scratch.add(
            "adapter.methods",
            PASS,
            "DeviceAdapter surface: probe/open/close/frames/play/stop_playback/set_volume/"
            "set_led/read_volume/drain_signals/drain_wakes",
        )

    info: DeviceInfo | None = None
    try:
        info = await _call(adapter.probe, timeout=PROBE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - probe is the developer's code
        scratch.add("device.probe", FAIL, f"probe() raised {_brief(exc)}")
    else:
        _check_device_info(info, target, scratch)

    opened = False
    try:
        await _call(adapter.open, timeout=OPEN_TIMEOUT_S)
        opened = True
        await _call(adapter.open, timeout=OPEN_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        scratch.add("device.open", FAIL, f"open() raised {_brief(exc)}")
    else:
        scratch.add("device.open", PASS, "open() completed twice (idempotent)")

    if opened:
        await _check_frames(adapter, info, scratch)
        await _check_playback(adapter, info, scratch)
    else:
        scratch.add("capture.frames", FAIL, "skipped: open() failed")
        scratch.add("playback.play", FAIL, "skipped: open() failed")

    await _check_read_volume(adapter, scratch)
    _check_drain_signals(adapter, scratch)
    _check_drain_wakes(adapter, target, info, scratch)
    await _check_close(adapter, scratch)


def _check_device_info(info: Any, target: Target, report: Report) -> None:
    if not isinstance(info, DeviceInfo):
        report.add(
            "device.probe",
            FAIL,
            "probe() must return rui.device.contract.DeviceInfo "
            f"(got {type(info).__name__}); import the dataclass instead of returning a dict",
        )
        return
    problems: list[str] = []
    if not isinstance(info.id, str) or not info.id:
        problems.append(f"id={info.id!r} must be a non-empty string")
    for name in ("channels", "sample_rate", "mic_channels", "ref_channels"):
        value = getattr(info, name)
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(f"{name}={value!r} must be an int")
    if isinstance(info.channels, int) and not isinstance(info.channels, bool) and info.channels <= 0:
        problems.append(f"channels={info.channels} must be > 0")
    if (
        isinstance(info.sample_rate, int)
        and not isinstance(info.sample_rate, bool)
        and info.sample_rate <= 0
    ):
        problems.append(f"sample_rate={info.sample_rate} must be > 0")
    if isinstance(info.mic_channels, int) and isinstance(info.ref_channels, int):
        if info.mic_channels <= 0:
            problems.append(f"mic_channels={info.mic_channels} must be > 0")
        if info.ref_channels < 0:
            problems.append(f"ref_channels={info.ref_channels} must be >= 0")
        if info.mic_channels + info.ref_channels != info.channels:
            problems.append(
                f"mic_channels({info.mic_channels}) + ref_channels({info.ref_channels}) "
                f"!= channels({info.channels})"
            )
    if not isinstance(info.transport, Transport):
        problems.append(
            f"transport={info.transport!r} must be rui.device.contract.Transport "
            "(a str is not enough)"
        )
    capabilities = getattr(info, "capabilities", None)
    probe_caps = _caps_of(info)
    if probe_caps is None:
        problems.append("capabilities must be an iterable of Capability")
    else:
        unknown = sorted(probe_caps - {str(cap) for cap in Capability})
        if unknown:
            problems.append("unknown capability value(s): " + ", ".join(unknown))
    if problems:
        report.add("device.probe", FAIL, "; ".join(problems))
        return
    report.add(
        "device.probe",
        PASS,
        f"id={info.id} transport={info.transport} {info.channels}ch "
        f"({info.mic_channels} mic + {info.ref_channels} ref) @ {info.sample_rate} Hz",
    )

    if target.manifest_caps is None:
        report.add(
            "device.capabilities",
            PASS,
            f"probe() declares {len(capabilities or ())}: "
            + ", ".join(sorted(probe_caps)),
        )
        return
    _check_capability_consistency(info, target, probe_caps, report)


def _check_capability_consistency(
    info: DeviceInfo, target: Target, probe_caps: set[str], report: Report
) -> None:
    manifest_caps = target.manifest_caps or frozenset()
    effective = effective_capabilities(manifest_caps, [Capability(cap) for cap in probe_caps])
    ignored = probe_only_capabilities(manifest_caps, [Capability(cap) for cap in probe_caps])
    notes: list[str] = []
    declared = {str(cap) for cap in manifest_caps}
    missing = sorted(declared - probe_caps)
    if missing:
        notes.append(
            "manifest declares "
            + ", ".join(missing)
            + " but probe() does not report it: the effective set is manifest ∩ probe "
            f"({len(effective)} effective)"
        )
    if ignored:
        notes.append(
            "probe() reports undeclared "
            + ", ".join(sorted(str(cap) for cap in ignored))
            + " (ignored by the core; a probe never escalates a capability)"
        )
    if (
        target.manifest_transport is not None
        and info.transport is not target.manifest_transport
    ):
        notes.append(
            f"manifest declares transport={target.manifest_transport} but probe() reports "
            f"{info.transport}"
        )
    if notes:
        report.add("device.capabilities", WARN, "; ".join(notes))
        return
    report.add(
        "device.capabilities",
        PASS,
        f"manifest ∩ probe = {len(effective)} effective: "
        + ", ".join(sorted(str(cap) for cap in effective)),
    )


async def _check_frames(adapter: Any, info: DeviceInfo | None, report: Report) -> None:
    try:
        stream = adapter.frames()
    except Exception as exc:  # noqa: BLE001
        report.add("capture.frames", FAIL, f"frames() raised {_brief(exc)}")
        return
    if not hasattr(stream, "__anext__"):
        report.add(
            "capture.frames",
            FAIL,
            f"frames() must return an async iterator (got {type(stream).__name__})",
        )
        return
    frames: list[AudioFrame] = []
    errors: list[str] = []
    try:
        for _index in range(FRAME_COUNT):
            try:
                frame = await asyncio.wait_for(stream.__anext__(), timeout=FRAME_TIMEOUT_S)
            except StopAsyncIteration:
                errors.append(f"frames() ended after {len(frames)} frame(s)")
                break
            except TimeoutError:
                errors.append(f"frames() produced no frame within {FRAME_TIMEOUT_S:g}s")
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"frames() raised {_brief(exc)}")
                break
            if not isinstance(frame, AudioFrame):
                errors.append(
                    f"frames() yielded {type(frame).__name__}, not "
                    "rui.device.contract.AudioFrame"
                )
                break
            _validate_frame(frame, info, errors)
            frames.append(frame)
    finally:
        aclose = getattr(stream, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception:  # noqa: BLE001 - best effort
                pass
    if errors:
        report.add("capture.frames", FAIL, "; ".join(errors))
        return
    detail = f"{len(frames)} AudioFrame(s)"
    durations = [
        float(frame.duration_ms) for frame in frames if math.isfinite(frame.duration_ms)
    ]
    if durations:
        detail += f", ~{sum(durations) / len(durations):.1f} ms each"
    report.add("capture.frames", PASS, detail)


async def _check_playback(adapter: Any, info: DeviceInfo | None, report: Report) -> None:
    rate = info.sample_rate if info is not None else 16000
    channels = 2 if info is not None and info.channels >= 2 else 1
    pcm = b"\x00" * (int(rate) // 50 * channels * 2)  # 20 ms of silence
    before = getattr(adapter, "played", None)
    before_count = len(before) if isinstance(before, list) else None
    try:
        await _call(adapter.play, pcm, sample_rate=rate, channels=channels)
        await _call(adapter.stop_playback)
        await _call(adapter.stop_playback)
    except Exception as exc:  # noqa: BLE001 - playback is the developer's code
        report.add("playback.play", FAIL, f"play()/stop_playback() raised {_brief(exc)}")
        return
    report.add(
        "playback.play",
        PASS,
        f"accepted {len(pcm)} bytes at {rate} Hz/{channels}ch; stop_playback twice",
    )
    after = getattr(adapter, "played", None)
    if isinstance(before, list) and isinstance(after, list):
        if len(after) <= before_count:
            report.add(
                "playback.sink",
                FAIL,
                "play() returned but the recording sink received no PCM "
                f"(played {before_count} -> {len(after)})",
            )
            return
        if after[-1] != pcm:
            report.add("playback.sink", FAIL, "the recording sink received different PCM bytes")
            return
        report.add("playback.sink", PASS, f"recording sink received {len(pcm)} bytes")
        return
    report.add(
        "playback.sink",
        WARN,
        "the adapter keeps no `played` sink, so delivery of these bytes cannot be observed "
        "without hardware (play() itself completed)",
    )


async def _check_read_volume(adapter: Any, report: Report) -> None:
    """The mixer value is checked here rather than in the required-method set.

    ``read_volume`` is a Protocol member with a default implementation and the core reads
    the mixer through ``services.read_volume()``, so a duck-typed bridge that never reports
    a level stays valid (WARN). A present implementation is held to the 0-255 contract.
    """
    reader = getattr(adapter, "read_volume", None)
    if not callable(reader):
        report.add(
            "device.read_volume",
            WARN,
            "read_volume() is not implemented; the core reads the mixer through "
            "services.read_volume() and a bridge that never reports a level is valid - "
            "subclass rui.device.contract.DeviceAdapter (or add the method) to report one",
        )
        return
    try:
        value = await _call(reader)
        again = await _call(reader)
    except NotImplementedError:
        report.add(
            "device.read_volume",
            FAIL,
            "read_volume() raised NotImplementedError; return None when the device cannot "
            "report a level",
        )
        return
    except Exception as exc:  # noqa: BLE001
        report.add("device.read_volume", FAIL, f"read_volume() raised {_brief(exc)}")
        return
    for candidate in (value, again):
        if candidate is None:
            continue
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            report.add(
                "device.read_volume",
                FAIL,
                f"read_volume() must return int|None (got {type(candidate).__name__})",
            )
            return
        if not DEVICE_VOLUME_MIN <= candidate <= DEVICE_VOLUME_MAX:
            report.add(
                "device.read_volume",
                FAIL,
                f"read_volume()={candidate} is outside 0-255 (the adapter's mixer unit)",
            )
            return
    if value is None:
        report.add("device.read_volume", PASS, "None (the device cannot report a level)")
    else:
        report.add("device.read_volume", PASS, f"{value} (0-255 mixer value)")


def _check_drain_signals(adapter: Any, report: Report) -> None:
    drain = getattr(adapter, "drain_signals", None)
    if not callable(drain):
        report.add("device.drain_signals", FAIL, "drain_signals() is missing")
        return
    try:
        events = drain()
        again = drain()
    except Exception as exc:  # noqa: BLE001
        report.add("device.drain_signals", FAIL, f"drain_signals() raised {_brief(exc)}")
        return
    for events_once in (events, again):
        if not isinstance(events_once, list):
            report.add(
                "device.drain_signals",
                FAIL,
                "drain_signals() must return a list (got "
                f"{type(events_once).__name__}); the plane iterates it",
            )
            return
    report.add(
        "device.drain_signals",
        PASS,
        f"returned a list twice ({len(events)} then {len(again)} event(s))",
    )


def _check_drain_wakes(
    adapter: Any, target: Target, info: DeviceInfo | None, report: Report
) -> None:
    drain = getattr(adapter, "drain_wakes", None)
    if not callable(drain):
        report.add("device.drain_wakes", FAIL, "drain_wakes() is missing")
        return
    try:
        wakes = drain()
    except Exception as exc:  # noqa: BLE001
        report.add("device.drain_wakes", FAIL, f"drain_wakes() raised {_brief(exc)}")
        return
    if not isinstance(wakes, list):
        report.add(
            "device.drain_wakes",
            FAIL,
            "drain_wakes() must return a list (got "
            f"{type(wakes).__name__}); the plane iterates it",
        )
        return
    inject = getattr(adapter, "inject_wake", None)
    source = "already queued"
    if callable(inject):
        try:
            inject()
            source = "injected"
        except Exception as exc:  # noqa: BLE001 - injection is a test hook, not the contract
            report.add(
                "device.drain_wakes",
                WARN,
                f"inject_wake() raised {_brief(exc)}; only already-queued events were "
                "validated",
            )
        else:
            try:
                wakes = list(wakes) + list(drain())
            except Exception as exc:  # noqa: BLE001
                report.add("device.drain_wakes", FAIL, f"drain_wakes() raised {_brief(exc)}")
                return
    declared = {str(cap) for cap in (target.manifest_caps or ())}
    probe_caps = _caps_of(info) or set()
    expects_wakes = "device_wake" in declared or "device_wake" in probe_caps
    problems: list[str] = []
    for index, wake in enumerate(wakes):
        reason = wake_validation_error(wake)
        if reason:
            problems.append(f"[{index}] {reason}")
    if problems:
        report.add("device.drain_wakes", FAIL, "; ".join(problems))
        return
    if not wakes:
        if expects_wakes and not callable(inject):
            report.add(
                "device.drain_wakes",
                WARN,
                "device_wake is declared but no event was available to validate; add an "
                "inject_wake() test hook (as rui.device.mock.MockAdapter does) so the "
                "payload can be checked without hardware",
            )
            return
        if not expects_wakes:
            report.add(
                "device.drain_wakes",
                PASS,
                "returned an empty list (device_wake is not declared; RUI KWS stays in use)",
            )
            return
        report.add("device.drain_wakes", PASS, "inject_wake() produced no event to validate")
        return
    report.add(
        "device.drain_wakes",
        PASS,
        f"{len(wakes)} event(s) from {source} validated by "
        "rui.device.contract.wake_validation_error()",
    )


async def _check_close(adapter: Any, report: Report) -> None:
    try:
        await _call(adapter.close)
        await _call(adapter.close)
    except Exception as exc:  # noqa: BLE001
        report.add("lifecycle.close", FAIL, f"close() raised {_brief(exc)}")
        return
    report.add("lifecycle.close", PASS, "close() completed twice (repeatable)")
    drain = getattr(adapter, "drain_wakes", None)
    if not callable(drain):
        return
    try:
        after = drain()
    except Exception as exc:  # noqa: BLE001
        report.add("lifecycle.after_close", FAIL, f"drain_wakes() after close() raised {_brief(exc)}")
        return
    if after:
        report.add(
            "lifecycle.after_close",
            FAIL,
            f"drain_wakes() still returns {len(after)} event(s) after close(); the contract "
            "says no event survives close()",
        )
        return
    report.add("lifecycle.after_close", PASS, "no wake survives close()")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def run_checks(adapter: str, *, device_id: str = "", self_check: bool = True) -> Report:
    """Run the full kit against ``adapter`` and return the report (no printing)."""
    report = Report(target=adapter)
    if self_check:
        _self_check(report)
    started = time.perf_counter()
    target = _resolve_target(adapter, device_id, report)
    if target is not None:
        try:
            asyncio.run(_check_adapter(target, report))
        except RuntimeError as exc:
            # e.g. called from inside a running loop; the CLI never does this.
            report.add("kit.error", FAIL, f"cannot run the async checks: {_brief(exc)}")
        except Exception as exc:  # noqa: BLE001 - the kit always answers with a code
            report.add("kit.error", FAIL, _brief(exc))
    report.add("kit.elapsed", PASS, f"{time.perf_counter() - started:.2f} s")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=KIT_NAME,
        description=(
            "Check that a third-party DeviceAdapter can be driven by the RUI core: manifest "
            "rules (when given a plug directory) and every contract member, against the "
            "reference mock device and a recording sink. No hardware is touched."
        ),
    )
    parser.add_argument(
        "--adapter",
        required=True,
        metavar="PATH|MODULE:ATTR",
        help=(
            "plug directory (or its plugin.yaml) whose device runtime.entrypoint is checked, "
            "or a bare adapter entry point such as my_plug.adapter:build or ./adapter.py:build"
        ),
    )
    parser.add_argument(
        "--device-id",
        default="",
        help="plug directory mode: which device's runtime.entrypoint to check (default: first)",
    )
    parser.add_argument(
        "--no-self-check",
        action="store_true",
        help="skip running the reference MockAdapter through the same checks first",
    )
    args = parser.parse_args(argv)
    try:
        report = run_checks(
            args.adapter, device_id=args.device_id, self_check=not args.no_self_check
        )
    except KeyboardInterrupt:
        return 1
    except Exception as exc:  # noqa: BLE001 - the kit must always answer with a code
        print(f"{KIT_NAME}: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(report.to_text())
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
