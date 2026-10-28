"""Audit A batch 1 (C1 + C2): end word over music stops the song AND closes the session.

C1: the old `_stop_command` answered an end word over music with `media_stop`
(「好，不放了。」 + `reply_finished`) and kept listening. 9 哥 measured the result
(2026-10-27): 「停止」 after an answer was taken as a music pause, and the session
stayed open. The rule is now: music playing **or paused** + end word -> stop the
queue and dismiss the session together, no middle state.

C2: `rules.stop_intent` no longer accepts an end word appearing anywhere with up to
two extra characters (「停止你好」 was a stop). It accepts the whole request = an end
word, an explicit `ALIASES` entry (「停止播放」「别放了」「别放了吧」), or exactly one
closed-set trailing particle (吧/了/呀/啊/嘛). Question particles 吗/呢/么 are excluded.

The tests drive the real `_stop_command` with a fake music service / runner / plane,
and the pure `rules` layer for the matching contract.
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
STOP_PHRASES = ("停止", "停止播放", "别放了", "别放了吧", "拜拜")


class _Music:
    def __init__(self, state: str) -> None:
        self.state = state
        self.stops = 0

    async def stop(self) -> None:
        self.stops += 1
        self.state = "idle"


class _Handle:
    """A plane handle that fails loudly if a closed session opens a follow-up window."""

    def __init__(self) -> None:
        self.sent: list[object] = []
        self.release_turn_calls = 0
        self.metrics = SimpleNamespace(output_owner="")

    def release_turn(self) -> None:
        # `_release_turn` is how a closed session frees the house's one-turn latch;
        # without this counter, dropping that call would leave every later wake
        # rejected `turn_open` (the recorded 2026-10-26 failure) and no test would red.
        self.release_turn_calls += 1

    async def send(self, message: object) -> None:
        self.sent.append(message)

    async def reply_finished(self, **kwargs: object) -> None:  # noqa: ARG002
        raise AssertionError("a stop that closes the session must not call reply_finished")


class _Runner:
    def __init__(self) -> None:
        self.dismissed = 0
        self.said = 0
        self.idle = SimpleNamespace(touch=lambda: None)

    async def dismiss(self, speak, trace) -> int:  # noqa: ANN001, ARG002
        self.dismissed += 1
        return 0

    async def say(self, line: str, speak, trace) -> int:  # noqa: ANN001, ARG002
        self.said += 1
        return 0


class _Traces:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, trace: object) -> None:
        self.items.append(trace)


def _stop(monkeypatch, phrase: str, music_state: str):
    monkeypatch.setattr(app_module, "_light_after_audio", lambda *a, **k: None)
    app = SimpleNamespace(state=SimpleNamespace(
        cfg=Config(), music=_Music(music_state), last_turn=None,
        turn_device="living", output_owner="",
    ))
    runner = _Runner()
    handle = _Handle()
    traces = _Traces()
    handled = asyncio.run(app_module._stop_command(
        app, phrase, runner, handle, TurnTrace(device_id="living"), traces))
    return handled, app, runner, handle


# ------------------------------------------------------------------ C1: branch
@pytest.mark.parametrize("phrase", STOP_PHRASES)
@pytest.mark.parametrize("music_state", ["playing", "paused"])
def test_end_word_over_music_stops_and_closes(monkeypatch, caplog, phrase, music_state) -> None:
    caplog.set_level(logging.INFO, logger="rui.web.api.app")
    handled, app, runner, _handle = _stop(monkeypatch, phrase, music_state)

    assert handled is True, f"{phrase!r} was not handled as a stop"
    assert app.state.music.stops == 1, "the song was not stopped"
    assert runner.dismissed == 1, "the session was not closed"
    assert _handle.release_turn_calls == 1, "the one-turn latch was not released"
    assert runner.said == 0, "the old 「music stopped, keep listening」 line was spoken"
    assert app.state.last_turn["session_action"] == "stop_and_dismiss"
    assert "media stopped, session closed" in caplog.text
    assert "media stopped, session open" not in caplog.text


@pytest.mark.parametrize("phrase", STOP_PHRASES)
def test_end_word_without_music_only_closes(monkeypatch, phrase) -> None:
    handled, app, runner, _handle = _stop(monkeypatch, phrase, "idle")

    assert handled is True
    assert app.state.music.stops == 0, "nothing was playing, yet the service was stopped"
    assert runner.dismissed == 1, "the session was not closed"
    assert _handle.release_turn_calls == 1, "the one-turn latch was not released"
    assert app.state.last_turn["session_action"] == "bare_stop"


# d.6 (2026-10-28) intentionally changed 「停止你好」 to a stop (fixed wake tail peeled
# once); the positive case lives in tests/test_session.py, so it is no longer listed here.
@pytest.mark.parametrize("phrase", ["停止了吗", "你好停止", "停下来了吗"])
def test_substrings_and_questions_are_not_stops(monkeypatch, phrase) -> None:
    handled, app, runner, _handle = _stop(monkeypatch, phrase, "playing")

    assert handled is False, f"{phrase!r} must fall through to the model/澄清"
    assert app.state.music.stops == 0
    assert runner.dismissed == 0


# ------------------------------------------------------- C2: pure matching contract
@pytest.mark.parametrize("phrase", [
    "停止", "停止播放", "别放了", "别放了吧", "拜拜", "停止吧", "别说了呀", "退下吧",
])
def test_stop_intent_accepts_end_words_aliases_and_one_particle(phrase) -> None:
    assert rules.stop_intent(phrase, END_WORDS) is True


@pytest.mark.parametrize("phrase", [
    # d.6 (2026-10-28): 「停止你好」 moved to the accepted side (fixed wake tail, one
    # strip only); the positive case lives in tests/test_session.py.
    "停止了吗", "停止呢", "停止么", "你好停止", "停下来了吗", "停止啊啊",
])
def test_stop_intent_rejects_substrings_questions_and_double_tails(phrase) -> None:
    assert rules.stop_intent(phrase, END_WORDS) is False


def test_the_explicit_aliases_are_not_end_words() -> None:
    """C2 adds ALIASES entries, not end words (9 哥: the end-word list stays as is)."""
    for alias in ("停止播放", "别放了", "别放了吧"):
        assert alias not in END_WORDS
        assert rules.ALIASES[alias] in END_WORDS


def test_stop_scope_still_uses_the_media_value_for_music() -> None:
    """C1 changes what the app DOES with `MEDIA_STOP`, not the pure scope decision."""
    assert rules.stop_scope("停止", True, END_WORDS) == rules.MEDIA_STOP
    assert rules.stop_scope("停止", False, END_WORDS) == rules.BARE_STOP
    assert rules.stop_scope("退下吧", True, END_WORDS) == rules.BARE_STOP


# --------------------------------------------------- LED convergence (task-17)
class _LedHandle:
    """A plane handle that records LED events like the real socket does."""

    device_id = "living"

    def __init__(self) -> None:
        self.sent: list[object] = []
        self.led: list[str] = []
        self.release_turn_calls = 0
        self.metrics = SimpleNamespace(output_owner="")

    def release_turn(self) -> None:
        self.release_turn_calls += 1

    async def send(self, message: object) -> None:
        self.sent.append(message)

    async def set_led(self, effect: str, repaint: bool = False) -> None:
        self.led.append(effect)

    async def reply_finished(self, **kwargs: object) -> None:  # noqa: ARG002
        raise AssertionError("a stop that closes the session must not call reply_finished")


def test_a_stop_ends_the_led_sequence_off_and_drops_the_previous_turns_paint() -> None:
    """04:29: stop -> `session closed` -> 13 s later `led: listening` + repaints.

    The green came from the *previous* turn's `_light_after_audio`: its delay is the
    length of the answer it painted after, so it can land long after a later stop has
    already turned the ring off. A stop must be terminal for that room: the pending
    paint is cancelled, the off is the last event, and nothing is left that could
    repaint the ring green in the next 30 s.
    """
    app = SimpleNamespace(state=SimpleNamespace(
        cfg=Config(), music=_Music("idle"), last_turn=None,
        turn_device="living", output_owner="",
    ))
    runner = _Runner()
    handle = _LedHandle()
    traces = _Traces()

    async def scenario() -> None:
        # A previous 10 s answer scheduled its post-playback listening paint.
        app_module._light_after_audio(app, 10 * 16000 * 2, 16000, "listening", live=handle)
        assert handle.led == [], "the fixture expected the stale paint to still be pending"

        handled = await app_module._stop_command(
            app, "退下吧", runner, handle, TurnTrace(device_id="living"), traces)
        assert handled is True
        # The goodbye is 0 bytes in this fake runner, so the terminal off lands at 0.6 s.
        await asyncio.sleep(0.8)

    asyncio.run(scenario())

    assert handle.led[-1] == "off", f"the ring did not converge to off: {handle.led}"
    assert "listening" not in handle.led, (
        "a paint from the previous turn landed after the session closed")
    # No live timer remains, so nothing can repaint the ring inside 30 s.
    assert not app.state.led_after_tasks.get("living")
    assert not any(not task.done() for task in app.state.led_tasks)
