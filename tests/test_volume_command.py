"""The volume reply must describe the level the mixer ends up at, not its cache.

Measured on a Dot 2 (2026-10-27, `/api/v1/debug/say`): 「音量调到30」 answered
「100%」, then 40 → 「30%」, then 70 → 「40%」. The write landed in the config and on
the device; the answer used the *pre-set* value.

Why: the plane already reads the mixer back after a `device_volume set`
(`audio/process.py`), but the dot2 bridge (1.3.1) answers `read_volume()` from
`reported_level` — the device's last report, one report behind. So the control
plane's "set then read" result is the old level. LX06 updates its cache at once,
which is why the same entry point was correct there.

The fix keeps the read-back, asks again briefly when the value did not move, and
never falls back to the pre-set number (the target stands in as the expected value
when the bridge's cache stays stale; an unreadable mixer gets a neutral sentence).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rui.config import Config, DeviceConfig, save_config
from rui.session import phrases
from rui.web.api import app as app_module
from rui.web.api.app import create_app


class FakeMixer:
    """dot2 1.3.1 semantics: the write lands, the immediate read still shows the old level.

    `mode` selects what the *later* reads show:
      - `converge`: the device reports the new level a moment later (the fix's happy path)
      - `stale`:    the cached report never moves (the fix answers the expected target)
      - `unreadable`: the mixer cannot be read at all (the fix answers a neutral sentence)
    """

    def __init__(self, volume: int = 255, mode: str = "converge") -> None:
        self.current = volume
        self.previous = volume
        self.mode = mode
        self.ready = None
        self.sets: list[int] = []
        self.reads = 0

    async def device_volume(self) -> int | None:
        self.reads += 1
        if self.reads == 1:            # the pre-set read the tool always does
            return self.current
        if self.mode == "unreadable":
            return None
        if self.mode == "stale":
            return self.previous
        return self.current

    async def set_device_volume(self, value: int) -> int | None:
        self.sets.append(value)
        previous, self.current = self.current, value
        self.previous = previous
        return previous                # the stale cache: report the pre-set value


async def _noop_start(self) -> None:   # noqa: ANN001 - stands in for the child process
    return None


def _app(tmp_path: Path, monkeypatch, mixer: FakeMixer) -> FastAPI:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.music.cache_dir = str(tmp_path / "cache")
    cfg.devices = [DeviceConfig(id="dot2", adapter="mock", host="192.0.2.10", room="客厅")]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    app = create_app(cfg, config_path=path, start_audio=True)
    # No child process in a unit test: the lifespan still registers the tools, which is
    # all we need to drive the real `volume_tool` closure.
    monkeypatch.setattr(app_module.AudioPlaneSupervisor, "start", _noop_start)
    monkeypatch.setattr(app_module.AudioPlaneSupervisor, "watch", lambda self: None)
    monkeypatch.setattr(app_module, "_handle_for", lambda app_, device_id="": mixer)
    return app


def _set_level(app: FastAPI, level: str):
    with TestClient(app):
        assert app.state.volume_tool is not None
        return asyncio.run(app.state.volume_tool({"level": level}))


def test_the_reply_uses_the_level_read_back_after_the_set(tmp_path, monkeypatch) -> None:
    """「音量调到30」 must answer 30, not the 100 that was there before (the dot2 bug)."""
    mixer = FakeMixer(volume=255, mode="converge")
    result = _set_level(_app(tmp_path, monkeypatch, mixer), "30")

    assert result.status == "ok"
    assert result.payload["pct"] == 30            # old code: 100 (the stale report)
    assert result.payload.get("expected") is not True
    assert mixer.sets == [76]                     # round(30 * 255 / 100)
    assert mixer.reads >= 2                       # a real read-back happened


def test_a_sequence_answers_each_new_level(tmp_path, monkeypatch) -> None:
    """30 → 40 → 70 answers 30/40/70 (the production reproduction, one app)."""
    mixer = FakeMixer(volume=255, mode="converge")
    app = _app(tmp_path, monkeypatch, mixer)
    with TestClient(app):
        seen = [asyncio.run(app.state.volume_tool({"level": level})).payload["pct"]
                for level in ("30", "40", "70")]
    assert seen == [30, 40, 70]
    assert mixer.sets == [76, 102, 178]


def test_a_stale_cache_answers_the_expected_target_never_the_old_value(
        tmp_path, monkeypatch) -> None:
    """When the bridge cache never moves, the target stands in (expected), not 100."""
    mixer = FakeMixer(volume=255, mode="stale")
    result = _set_level(_app(tmp_path, monkeypatch, mixer), "30")

    assert result.status == "ok_unconfirmed"
    assert result.payload["pct"] == 30            # the target, never the old 100
    # The read-back budget is 6 polls x 80 ms: one pre-set read + six polls. Pinned
    # here so a future "just one more retry" cannot stretch the reply latency silently.
    assert mixer.reads == 1 + 6, mixer.reads
    assert phrases.book.render("volume.set_unconfirmed", **result.payload) == "已设置音量 30%。"


def test_an_unreadable_mixer_answers_a_neutral_sentence(tmp_path, monkeypatch) -> None:
    """No read-back at all: say "set", never a number (and never the old value)."""
    mixer = FakeMixer(volume=255, mode="unreadable")
    result = _set_level(_app(tmp_path, monkeypatch, mixer), "30")

    assert result.status == "ok_unconfirmed"
    assert result.payload["pct"] == 30            # target, marked unconfirmed
