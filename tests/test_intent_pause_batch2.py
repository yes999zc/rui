"""Audit A batch 2+3: narrower pause words, and "pause" with no music keeps listening.

C5: the default pause words are only 「暂停」「停一下」. The old aliases of the pause
action (先停这儿 / 停会儿 / 停住 / 停在这) are removed and are NOT moved into the
end-word list (that list is unchanged at 21 entries).

C3: 「暂停」/「停一下」 with the player idle answers 「现在没在放音乐」 and keeps the
session open. Both words are also end words, so without the intercept they used to
fall into `_stop_command`'s bare_stop and end the conversation.

The tests drive the real `_stop_command` (the intercept lives there, before the
stop-word branch) and the pure `rules` layer.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import rui.web.api.app as app_module
from rui.config import Config
from rui.session import rules
from rui.trace import TurnTrace

END_WORDS = tuple(Config().session.end_words)
PAUSE_WORDS = ("暂停", "停一下")


class _Music:
    def __init__(self, state: str) -> None:
        self.state = state
        self.paused = 0
        self.stops = 0

    async def pause(self) -> bool:
        self.paused += 1
        self.state = "paused"
        return True

    async def resume(self) -> bool:
        self.state = "playing"
        return True

    async def next(self) -> bool:
        return True

    async def prev(self) -> bool:
        return True

    async def stop(self) -> None:
        self.stops += 1
        self.state = "idle"


class _Handle:
    def __init__(self) -> None:
        self.sent: list[object] = []
        self.release_turn_calls = 0
        self.reply_finished_calls = 0
        self.metrics = SimpleNamespace(output_owner="")

    async def send(self, message: object) -> None:
        self.sent.append(message)

    def release_turn(self) -> None:
        self.release_turn_calls += 1

    async def reply_finished(self, **kwargs: object) -> None:  # noqa: ARG002
        self.reply_finished_calls += 1


class _Runner:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.dismissed = 0
        self.idle = SimpleNamespace(touch=lambda: None)

    async def say(self, line: str, speak, trace) -> int:  # noqa: ANN001, ARG002
        self.said.append(line)
        return 0

    async def dismiss(self, speak, trace) -> int:  # noqa: ANN001, ARG002
        self.dismissed += 1
        return 0


class _Traces:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, trace: object) -> None:
        self.items.append(trace)


def _env(monkeypatch, music_state: str):
    monkeypatch.setattr(app_module, "_light_after_audio", lambda *a, **k: None)
    service = _Music(music_state)
    app = SimpleNamespace(state=SimpleNamespace(
        cfg=Config(), music=service, last_turn=None,
        output_owner="", turn_device="living",
    ))
    return app, service, _Runner(), _Handle(), _Traces()


def _stop(monkeypatch, phrase: str, music_state: str):
    app, service, runner, handle, traces = _env(monkeypatch, music_state)
    handled = asyncio.run(app_module._stop_command(
        app, phrase, runner, handle, TurnTrace(device_id="living"), traces))
    return handled, app, service, runner, handle


# ------------------------------------------------------------------ C5: word list
def test_only_two_pause_words_remain() -> None:
    assert rules.MUSIC_TRANSPORT["pause"] == PAUSE_WORDS
    for removed in ("先停这儿", "停会儿", "停住", "停在这"):
        assert removed not in rules.MUSIC_TRANSPORT["pause"]
        assert rules.music_transport(removed) == ""
        assert removed not in END_WORDS, "the removed pause words must not become end words"


def test_the_end_word_list_did_not_grow() -> None:
    # The set is closed on purpose: every extra word is a chance to dismiss a turn by
    # accident. It grew by exactly two on 2026-10-30 (「下线」「下线吧」, 9哥 said goodnight
    # and the session kept running), so the count moves with a named reason only.
    assert len(END_WORDS) == 23
    assert "暂停" in END_WORDS and "停一下" in END_WORDS
    assert "下线" in END_WORDS and "下线吧" in END_WORDS


# ------------------------------------------------------ C3: pause with no music
@pytest.mark.parametrize("phrase", PAUSE_WORDS + ("停一下下",))
def test_pause_without_music_answers_and_keeps_the_session(monkeypatch, caplog, phrase) -> None:
    caplog.set_level(logging.INFO, logger="rui.web.api.app")
    handled, app, service, runner, handle = _stop(monkeypatch, phrase, "idle")

    assert handled is True
    assert runner.said == ["现在没在放音乐。"], "the no-music pause must answer once"
    assert runner.dismissed == 0, "pause without music must not close the session"
    assert app.state.music.stops == 0
    assert app.state.last_turn["session_action"] == "pause_no_music"
    assert handle.reply_finished_calls == 1, "the session stays open (follow-up allowed)"
    assert "pause without music" in caplog.text


def test_removed_pause_words_do_not_reach_the_stop_branch(monkeypatch) -> None:
    for phrase in ("先停这儿", "停会儿", "停住", "停在这"):
        handled, _app, _service, runner, _handle = _stop(monkeypatch, phrase, "playing")
        assert handled is False, f"{phrase!r} must fall through to the model"
        assert runner.dismissed == 0


# ------------------------------------------------- C5: a real pause still pauses
@pytest.mark.parametrize("music_state", ["playing", "paused"])
def test_pause_word_with_music_reaches_the_transport(monkeypatch, music_state) -> None:
    app, service, runner, handle, traces = _env(monkeypatch, music_state)
    line = asyncio.run(app_module._music_transport(
        app, "暂停", runner, handle, TurnTrace(device_id="living"), traces))

    assert line == "", "a successful pause is silent (the music itself is the answer)"
    assert service.paused == 1, "the player pause must be called"
    assert service.state == "paused"
    assert runner.dismissed == 0, "a real pause keeps the session open"
    assert runner.said == []
    assert app.state.last_turn["tools"] == ["music_pause"]
