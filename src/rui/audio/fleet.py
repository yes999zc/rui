"""Multi-plane fleet: one supervised audio-plane child per inbound device.

Phase 2 of ``docs/multi-device-design.md`` (§6.1, §12). The control plane used to spawn
exactly one plane (``AudioPlaneSupervisor`` in ``client.py``); a home with several
speakers needs one child per enabled inbound device, each with its own AF_UNIX socket,
its own crash watchdog and its own health row. This module owns only that mapping: it
reuses ``AudioPlaneSupervisor`` unchanged as the per-device unit, so the single-device
path and every message on the wire stay byte-identical.

What the fleet does *not* do, by design:

- it never picks a port. Each child is spawned with ``--device <id>`` against the same
  config file, so the effective listen port, token env and audio overrides are resolved
  by that device's manifest/config inside the plane process;
- it never interprets audio, wakes or turns. Arbitration and output ownership live in
  the control plane, above this seam;
- it never rewrites config. Restarting after a device edit is a caller decision.

Failure policy (chosen and tested, not implied):

- too many planes: ``start`` raises ``AudioPlaneError`` before spawning anything and
  names the devices it refused, so nothing is silently truncated;
- one device fails to launch: the other devices still start, the failure is kept in
  ``start_errors`` and appears in ``status()`` for that device, and its supervisor's
  watchdog is armed so it retries with the normal backoff (a misconfigured speaker must
  not take the rest of the house down);
- every requested device fails: ``start`` raises ``AudioPlaneError`` after cleaning up,
  because a fleet with no working plane is a startup failure, not a degraded fleet.

Crash isolation is inherited from ``AudioPlaneSupervisor``: the fleet arms one watchdog
per device and never stops a sibling when one child dies. ``test_audio_fleet.py`` pins
this against real mock-adapter children (SIGKILL one, the other keeps running).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
from collections.abc import Sequence
from pathlib import Path

from ..config import Config
from .client import AudioPlaneError, AudioPlaneSupervisor, PlaneHandle
from .process import default_socket_path, resolve_socket_path

log = logging.getLogger(__name__)

# Socket files are named after the device id: ``audio-<id>.sock`` next to the
# single-device default socket. Short by construction, then handed to
# ``resolve_socket_path`` which keeps it inside the platform's AF_UNIX limit.
SOCKET_PREFIX = "audio-"

_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _socket_stem(device_id: str) -> str:
    """Turn a device id into a file-name stem that is safe and still recognisable.

    Config validation already restricts ids to alphanumerics plus dash/underscore, but
    this module is also callable with ids from tests or a future config path. An id that
    had to be rewritten gets a short digest suffix so two such ids cannot collide on one
    socket file - a silent collision would hand one device another device's plane.
    """
    safe = _UNSAFE_ID_CHARS.sub("_", device_id)[:32]
    if not safe:
        safe = "device"
    if safe != device_id:
        digest = hashlib.sha1(device_id.encode()).hexdigest()[:8]
        safe = f"{safe}-{digest}"
    return safe


def fleet_socket_path(device_id: str, socket_dir: Path | None = None) -> Path:
    """The (short-resolved) socket path for one device's plane.

    ``socket_dir`` overrides where sockets live; by default they sit next to
    ``default_socket_path()`` (respecting ``RUI_AUDIO_SOCK`` and the home-relative
    default). The single-device ``audio.sock`` name is never used here, so a fleet and
    a legacy supervisor can never steal each other's socket.
    """
    directory = default_socket_path().parent if socket_dir is None else Path(socket_dir)
    return resolve_socket_path(directory / f"{SOCKET_PREFIX}{_socket_stem(device_id)}.sock")


class PlaneFleet:
    """Owns every audio-plane child process: one per enabled inbound device."""

    def __init__(
        self,
        cfg: Config,
        config_path: Path | None = None,
        *,
        socket_dir: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.config_path = config_path
        self.socket_dir = socket_dir
        self._supervisors: dict[str, AudioPlaneSupervisor] = {}
        self._device_ids: list[str] = []
        self._start_errors: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle
    async def start(self, device_ids: Sequence[str], timeout_s: float = 15.0) -> PlaneFleet:
        """Spawn one child per device id, each with its own socket and watchdog.

        Ports are not chosen here: every child receives ``--device <id>`` and resolves
        its own listen port from the config/manifest.

        Raises ``AudioPlaneError`` when the request exceeds ``multi_device.max_planes``
        (listing the refused devices), when ids are duplicated, when a config file on
        disk is needed but absent, or when *no* device could be launched. A partial
        failure returns the fleet with the healthy devices running and the failures in
        ``start_errors``/``status()``; see the module docstring for why.
        """
        async with self._lock:
            if self._supervisors:
                raise AudioPlaneError(
                    "the audio plane fleet is already started; use restart()/stop()"
                )
            ids = list(device_ids)
            duplicates = sorted({device_id for device_id in ids if ids.count(device_id) > 1})
            if duplicates:
                raise AudioPlaneError(
                    f"duplicate device id(s) requested: {', '.join(duplicates)}"
                )
            limit = self.cfg.multi_device.max_planes
            if len(ids) > limit:
                extra = ids[limit:]
                raise AudioPlaneError(
                    f"multi_device.max_planes={limit} but {len(ids)} audio planes were "
                    f"requested; refusing to start the extra device(s): {', '.join(extra)}"
                )
            if ids and self.config_path is None:
                raise AudioPlaneError("the audio plane fleet needs a config file on disk")

            failed: list[str] = []
            for device_id in ids:
                if not await self._start_one(device_id, timeout_s):
                    failed.append(device_id)
            if ids and len(failed) == len(ids):
                details = "; ".join(f"{d}: {self._start_errors[d]}" for d in failed)
                await self._stop_all_locked()
                raise AudioPlaneError(f"no audio plane started ({details})")
            return self

    async def restart(self, device_id: str) -> None:
        """Rebuild exactly one plane, leaving every sibling untouched.

        The supervisor picks up the fleet's current ``cfg`` first, so a config edit plus
        ``restart(device_id)`` applies only to that room. The device is not removed from
        the fleet on failure: the error is recorded, the supervisor is re-armed so its
        watchdog retries, and ``AudioPlaneError`` tells the console the manual restart
        did not come back.
        """
        async with self._lock:
            supervisor = self._require(device_id)
            self._start_errors.pop(device_id, None)
            try:
                supervisor.cfg = self.cfg
                await supervisor.stop()
                await supervisor.start()
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller below
                error = f"{type(exc).__name__}: {exc}"
                supervisor.error = error
                self._start_errors[device_id] = error
                raise AudioPlaneError(
                    f"audio plane {device_id!r} did not restart: {exc}"
                ) from exc
            finally:
                supervisor.watch()
            self._start_errors.pop(device_id, None)

    def update_config(self, cfg: Config) -> None:
        """Adopt a reloaded config without disturbing any running plane.

        Callers use this after a config save, then ``restart()`` the devices whose
        settings changed (or all of them for a global audio change). A caller that
        mutates the existing config object in place does not need this.
        """
        self.cfg = cfg
        for supervisor in self._supervisors.values():
            supervisor.cfg = cfg

    async def stop_device(self, device_id: str) -> None:
        """Stop one plane and remove it (and its socket) from the fleet."""
        async with self._lock:
            await self._stop_one_locked(device_id)

    async def stop(self) -> None:
        """Stop every plane and remove every socket this fleet is responsible for."""
        async with self._lock:
            await self._stop_all_locked()

    async def _start_one(self, device_id: str, timeout_s: float) -> bool:
        """Create and launch one supervisor. Returns False when the launch failed."""
        descriptor = fleet_socket_path(device_id, self.socket_dir)
        supervisor = AudioPlaneSupervisor(
            self.cfg,
            config_path=self.config_path,
            socket_path=descriptor,
            device_id=device_id,
        )
        self._supervisors[device_id] = supervisor
        if device_id not in self._device_ids:
            self._device_ids.append(device_id)
        try:
            await supervisor.start(timeout_s)
        except Exception as exc:  # noqa: BLE001 - one device must not stop the rest
            error = f"{type(exc).__name__}: {exc}"
            # start() records its own error on most paths; keep the fleet's copy for
            # status()/start_errors and arm the watchdog so this room retries later.
            supervisor.error = supervisor.error or error
            self._start_errors[device_id] = error
            log.warning(
                "audio plane %r did not start: %s (watchdog armed to retry)", device_id, exc
            )
            supervisor.watch()
            return False
        self._start_errors.pop(device_id, None)
        # Armed even when the first launch failed (above), mirroring the single-device
        # app: the watchdog is what turns "this plane crashed" into a few seconds of
        # silence in one room instead of a manual service restart.
        supervisor.watch()
        pid = supervisor.proc.pid if supervisor.proc is not None else "?"
        log.info(
            "audio plane up: device=%r pid=%s socket=%s", device_id, pid, descriptor
        )
        return True

    async def _stop_one_locked(self, device_id: str) -> None:
        supervisor = self._require(device_id)
        self._supervisors.pop(device_id)
        self._device_ids.remove(device_id)
        self._start_errors.pop(device_id, None)
        try:
            await supervisor.stop()
        finally:
            _remove_socket(supervisor)

    async def _stop_all_locked(self) -> None:
        supervisors = list(self._supervisors.values())
        results = await asyncio.gather(
            *(supervisor.stop() for supervisor in supervisors), return_exceptions=True
        )
        for supervisor, result in zip(supervisors, results, strict=True):
            if isinstance(result, BaseException):
                # Keep stopping the rest: one stuck child must not strand the others.
                log.warning(
                    "audio plane stop failed for %r: %s",
                    supervisor.device_id,
                    result,
                )
            _remove_socket(supervisor)
        self._supervisors.clear()
        self._device_ids.clear()
        self._start_errors.clear()

    # ------------------------------------------------------------------ accessors
    def handle(self, device_id: str) -> PlaneHandle:
        """The stable per-device handle (callbacks survive restarts)."""
        return self._require(device_id).handle

    def supervisor(self, device_id: str) -> AudioPlaneSupervisor:
        """The per-device supervisor, for restart/status-level callers."""
        return self._require(device_id)

    def device_ids(self) -> list[str]:
        """Started device ids, in the order ``start()`` received them."""
        return list(self._device_ids)

    def running(self, device_id: str) -> bool:
        """True when that plane is connected and alive. Unknown ids are not running."""
        supervisor = self._supervisors.get(device_id)
        return bool(supervisor is not None and supervisor.running)

    def running_count(self) -> int:
        return sum(1 for supervisor in self._supervisors.values() if supervisor.running)

    @property
    def start_errors(self) -> dict[str, str]:
        """Devices whose launch failed and was not recovered by a later restart."""
        return dict(self._start_errors)

    def status(self) -> dict:
        """``{device_id: supervisor.status()}`` for /health and the console."""
        return {
            device_id: supervisor.status()
            for device_id, supervisor in self._supervisors.items()
        }

    def _require(self, device_id: str) -> AudioPlaneSupervisor:
        try:
            return self._supervisors[device_id]
        except KeyError:
            raise KeyError(f"unknown audio plane device: {device_id!r}") from None


def _remove_socket(supervisor: AudioPlaneSupervisor) -> None:
    """Unlink the plane's socket after stop, including after a SIGKILLed child.

    The plane unlinks its own socket on a clean shutdown; a killed child cannot, and a
    stale path is exactly how a later restart connects to nothing.
    """
    path = getattr(supervisor, "socket_path", None)
    if path is None:
        return
    with contextlib.suppress(OSError):
        Path(path).unlink(missing_ok=True)
