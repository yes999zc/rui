#!/usr/bin/env python3
"""Container health probe: the HTTP API is up, and every configured speaker has a plane.

Why this is a script and not an inline `urllib.urlopen("/health")`:

* the port lives in config, not in the image, so a deployment that changes
  `web.port` must not be reported unhealthy forever (the old probe hard-coded 8080);
* `audio_plane` is the part that actually goes silent. The control plane can answer
  `/health` for hours while the audio plane is dead - the console still loads, the
  speaker still dials in, and nothing is listening to it. When at least one device is
  configured, that is an unhealthy container.

Exit 0 = healthy, 1 = unhealthy (reason on stderr, which lands in `docker inspect`).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path("/config/config.yaml")
DEFAULT_PORT = 8080
ATTEMPTS = 3
TIMEOUT_S = 5.0


def _config() -> tuple[int, int]:
    """(http port, configured device count); schema defaults when the file is absent."""
    import os

    path = Path(os.environ.get("HOUSE_EAR_CONFIG", str(CONFIG)))
    try:
        from house_ear.config import load_config

        cfg = load_config(path)
        return cfg.web.port, len(cfg.devices)
    except Exception:  # noqa: BLE001 - a probe must never be the thing that crashes
        return DEFAULT_PORT, 0


def _fail(reason: str) -> int:
    print(f"unhealthy: {reason}", file=sys.stderr)
    return 1


def main() -> int:
    port, devices = _config()
    url = f"http://127.0.0.1:{port}/health"
    last = ""
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
                body = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = f"{url}: {type(exc).__name__}: {exc}"
            time.sleep(1.0)
            continue
        if body.get("status") != "ok":
            return _fail(f"{url} status={body.get('status')!r}")
        # No device configured means no plane is expected (the first-boot wizard adds one).
        if devices and not body.get("audio_plane"):
            return _fail(
                f"audio plane not running ({devices} device(s) configured); "
                "`docker compose restart house-ear` brings it back"
            )
        print(
            f"ok: uptime={body.get('uptime_s')}s devices={body.get('devices')} "
            f"audio_plane={body.get('audio_plane')}"
        )
        return 0
    return _fail(last or "no /health response")


if __name__ == "__main__":
    sys.exit(main())
