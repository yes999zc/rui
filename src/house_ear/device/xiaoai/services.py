"""What an LX06 can do besides audio, and the only place its mixer lives.

`mysoftvol` (numid=177) is the sole knob that changes loudness on this box, the vendor
rewrites it whenever it touches its own player, and the previous deployment measured
160 -> 255 -> 160 within minutes. So it is read when asked, never assumed, and the ALSA
detail stays here instead of leaking into the API layer.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MIXER = "numid=177"
READ_VOLUME = (
    f"amixer -c 0 cget {MIXER} | awk -F'[=,]' '/: values={{print $2; exit}}'"
)


class XiaoaiServices:
    def __init__(self, adapter: object) -> None:
        self._adapter = adapter

    async def shell(self, script: str, timeout: float = 3.0) -> str:
        response = await self._adapter.call("run_shell", script, timeout)  # type: ignore[attr-defined]
        return str((response.get("data") or {}).get("stdout") or "").strip()

    async def read_volume(self) -> int | None:
        out = await self.shell(READ_VOLUME)
        try:
            value = int(out.strip().splitlines()[0])
        except (ValueError, IndexError):
            return None
        return value if 0 <= value <= 255 else None

    async def set_volume(self, volume: int) -> None:
        if not 0 < volume <= 255:
            raise ValueError("device volume is 0-255 (0 means 'leave it to the vendor')")
        await self.shell(f"amixer -c 0 cset {MIXER} {int(volume)} >/dev/null; echo ok")
        log.info("device volume set to %d", volume)
