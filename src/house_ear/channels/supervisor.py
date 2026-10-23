"""Supervise the channel gateway child process from the control plane.

The gateway stays a separate process (a WeChat/Telegram outage must not take the voice
plane with it), but the console gets Start/Stop/Restart controls. The supervisor stores
no credentials: the token comes from the normal secrets store and is passed through the
child's environment.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from .. import secrets

log = logging.getLogger(__name__)


class ChannelSupervisor:
    def __init__(self, cfg: Any, data_dir: Path) -> None:
        self.cfg = cfg
        self.data_dir = Path(data_dir)
        self.channels_path = self.data_dir / "channels.json"
        self.log_path = self.data_dir / "channels-gateway.log"
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self.started_at = 0.0
        self.last_error = ""
        self._want_running = False
        self._watch_task: asyncio.Task[None] | None = None

    def _config(self) -> dict[str, Any]:
        try:
            data = json.loads(self.channels_path.read_text())
        except Exception:  # noqa: BLE001 - missing config means nothing to start
            return {}
        return data if isinstance(data, dict) else {}

    def enabled(self) -> bool:
        data = self._config()
        return any(bool((data.get(name) or {}).get("enabled")) for name in ("telegram", "weixin"))

    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running(),
            "pid": self._proc.pid if self.running() and self._proc is not None else None,
            "started_at": self.started_at or None,
            "uptime_s": round(time.time() - self.started_at, 1) if self.running() and self.started_at else 0,
            "last_error": self.last_error,
            "log_path": str(self.log_path),
            "managed": True,
            "enabled": self.enabled(),
        }

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self.running():
                return self.status()
            if not self.enabled():
                self.last_error = "no channel is enabled"
                return self.status()
            token = secrets.get("TELEGRAM_BOT_TOKEN") or os.environ.get("HOUSE_EAR_TELEGRAM_TOKEN", "")
            env = dict(os.environ)
            env["HOUSE_EAR_TELEGRAM_TOKEN"] = token
            env["HOUSE_EAR_CONSOLE_TOKEN"] = str(getattr(getattr(self.cfg, "web", None), "auth_token", "") or "")
            env["HOUSE_EAR_CONTROL_URL"] = f"http://127.0.0.1:{int(getattr(getattr(self.cfg, 'web', None), 'port', 8080) or 8080)}"
            env["PYTHONUNBUFFERED"] = "1"
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            logf = self.log_path.open("ab")
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "house_ear.channels",
                    "--control-url", env["HOUSE_EAR_CONTROL_URL"],
                    "--control-token", env["HOUSE_EAR_CONSOLE_TOKEN"],
                    "--channels-config", str(self.channels_path),
                    "--data-dir", str(self.data_dir / "channels"),
                    stdout=logf, stderr=logf, env=env,
                )
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)[:300]
                log.warning("channel supervisor: start failed: %s", exc)
                return self.status()
            finally:
                logf.close()
            self.started_at = time.time()
            self.last_error = ""
            self._want_running = True
            log.info("channel supervisor: started pid=%s", self._proc.pid)
            return self.status()

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            proc, self._proc = self._proc, None
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 10)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
                log.info("channel supervisor: stopped")
            self.started_at = 0.0
            self._want_running = False
            return self.status()

    async def restart(self) -> dict[str, Any]:
        await self.stop()
        return await self.start()

    async def watch(self) -> None:
        """Keep a started gateway alive; a manual Stop is respected."""
        while True:
            await asyncio.sleep(5.0)
            if not self._want_running or self.running() or not self.enabled():
                continue
            log.warning("channel supervisor: gateway exited; restarting")
            await self.start()
