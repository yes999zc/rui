"""Container health probe: the HTTP API is up and the enabled speakers can be heard.

Installed as the ``rui-healthcheck`` console script; ``deploy/rui-healthcheck.py`` is
the thin launcher copied into the image at ``/usr/local/bin/rui-healthcheck``. The rules
live here so the probe is importable and unit-testable.

* the port lives in config, not in the image, so a deployment that changes
  `web.port` must not be reported unhealthy forever (the old probe hard-coded 8080);
* `audio_plane` alone is not enough. The control plane can answer `/health` for hours
  while the audio plane is dead - or while the plane is up but no speaker ever dialled
  in. Both look like "green" to a process check and like a deaf room to the people in it.
* a *disabled* device is configured on purpose and must not require a plane (audit #13:
  the old probe counted every configured device, so disabling the only speaker made the
  container permanently unhealthy, and the message told the operator to restart - which
  cannot help).

Fail-closed rule: if the config cannot be read, this probe reports unhealthy with the
reason. Falling back to "port 8080, zero devices" turned a broken mount into a green
container.

Exit 0 = healthy, 1 = unhealthy (reason on stderr, which lands in `docker inspect`).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import load_config
from .env import get as env_get

CONFIG = Path("/config/config.yaml")
ATTEMPTS = 3
TIMEOUT_S = 5.0


def _config() -> tuple[int | None, int, str]:
    """(http port, enabled device count, error). A read failure is fatal, not a default."""
    path = Path(env_get("RUI_CONFIG", str(CONFIG)))
    try:
        cfg = load_config(path)
    except Exception as exc:  # noqa: BLE001 - a probe must never be the thing that crashes
        return None, 0, f"cannot read config {path}: {type(exc).__name__}: {exc}"
    # Only enabled devices are supposed to open a plane; a disabled one is a deliberate
    # "not in service", not a fault.
    enabled = sum(1 for device in cfg.devices if device.enabled)
    return int(cfg.web.port), enabled, ""


def _rooms(body: dict) -> list[dict]:
    rows = body.get("audio_devices") or []
    return [row for row in rows if isinstance(row, dict)]


def _room_detail(body: dict, *, connected_ok: bool = False) -> str:
    """Name the rooms whose plane is not running / whose speaker is not connected.

    A healthcheck that only says "restart" is useless for the downstream case this was
    written for (dot2 #12 C3/C4): an incompatible adapter makes the plane fail the same
    way on every restart. The /health audio_devices rows carry the room and the child's
    own error, so the probe can say which one and why.
    """
    parts: list[str] = []
    for row in _rooms(body):
        if connected_ok:
            bad = not row.get("speaker_connected")
        else:
            bad = not row.get("running") or not row.get("speaker_connected")
        if not bad:
            continue
        name = str(row.get("id") or "?")
        room = str(row.get("room") or "")
        label = f"{name} ({room})" if room else name
        state = "no speaker" if row.get("running") else "no plane"
        error = str(row.get("error") or "")
        parts.append(f"{label}: {state}" + (f" - {error}" if error else ""))
    return "; ".join(parts)


def evaluate(body: dict, configured_enabled: int = 0) -> tuple[bool, str]:
    """The verdict for one `/health` body. Pure so the rules are testable offline.

    `/health` carries `enabled_devices` and `speaker_connected` itself, so the probe and
    the console read the same source; `configured_enabled` is only a fallback for a
    server built before those fields existed.
    """
    if body.get("status") != "ok":
        return False, f"status={body.get('status')!r}"
    enabled = int(body.get("enabled_devices", configured_enabled) or 0)
    device_errors = int(body.get("device_errors") or 0)
    if enabled and not body.get("audio_plane"):
        if device_errors:
            return False, (
                f"{device_errors} configured device(s) cannot be driven "
                f"(device_errors={device_errors}); an adapter is missing or the manifest "
                "is incompatible with this build - restarting will not fix it. Check "
                "/api/v1/devices and the boot log"
            )
        multi = body.get("multi_device") or {}
        if multi.get("error"):
            return False, (
                f"audio plane not running ({enabled} enabled device(s)); the fleet failed "
                f"to start: {multi['error']} - fix the config/device first, a restart "
                "alone repeats the same error"
            )
        detail = _room_detail(body)
        return False, (
            f"audio plane not running ({enabled} enabled device(s))"
            + (f": {detail}" if detail else "")
            + "; docker compose restart rui brings it back"
        )
    if enabled and not body.get("speaker_connected"):
        detail = _room_detail(body, connected_ok=True)
        return False, (
            f"no speaker connected ({enabled} enabled device(s))"
            + (f": {detail}" if detail else "")
            + "; check the speaker is powered and its client points at this server"
        )
    return True, ""


def _fail(reason: str) -> int:
    print(f"unhealthy: {reason}", file=sys.stderr)
    return 1


def main() -> int:
    port, configured, config_error = _config()
    if config_error:
        return _fail(config_error)
    if port is None:  # defensive: _config returns a port whenever error is empty
        return _fail("no HTTP port in config")
    url = f"http://127.0.0.1:{port}/health"
    last = ""
    for _attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
                body = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = f"{url}: {type(exc).__name__}: {exc}"
            time.sleep(1.0)
            continue
        ok, reason = evaluate(body, configured)
        if not ok:
            return _fail(reason)
        rows = _rooms(body)
        planes = ""
        if rows:
            up = sum(1 for row in rows if row.get("running"))
            live = sum(1 for row in rows if row.get("speaker_connected"))
            planes = f" planes={up}/{len(rows)} connected={live}"
        print(
            f"ok: uptime={body.get('uptime_s')}s devices={body.get('devices')} "
            f"enabled={body.get('enabled_devices', configured)} "
            f"audio_plane={body.get('audio_plane')} "
            f"speaker_connected={body.get('speaker_connected')}{planes}"
        )
        return 0
    return _fail(last or "no /health response")


if __name__ == "__main__":
    sys.exit(main())
