"""0.8.13: the console's 试一试 must leave the room's conversation exactly as it was.

Two side effects had to go, both on `_run_debug_say`:

- it ran the turn through `runner.run()`, which set `_had_speech`, so 15 s after a
  console click the speaker said 「没事的话，我先下啦。」 in a room with nobody in it;
- it called `runner.reset_history()` for its own fresh-utterance answer and never put
  the context back, so pressing 试一试 in the middle of a conversation silently threw
  that conversation away.

The runner-level rules are pinned in `test_session_timeout.py`; this file pins the app
side: the turn runs with `counts_as_speech=False`, and `history` / `_had_speech` come
back - on the success path and on the error path.
"""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient
from test_app_fleet import FakeFleet, _app
from test_session_timeout import FakeAsr, FakeClock, FakeLlm, FakeTts, patch_chains

from rui.session.idle import IdleWatch
from rui.trace import TurnTrace


async def _discard(pcm: bytes) -> None:
    return None


def _console_app(tmp_path, monkeypatch, tts: FakeTts):
    """A real app whose runner answers from the fakes (built before the runner exists)."""
    FakeFleet.instances.clear()
    # The chains must be faked before `create_app` builds the runner.
    patch_chains(monkeypatch, asr=FakeAsr("现在几点"), llms=[FakeLlm(["一点了。"])],
                 ttss=[tts])
    return _app(tmp_path, monkeypatch)


def _fake_clock(runner, clock: FakeClock) -> None:
    """The app wires the real 15 s watch; the fake clock makes the boundary exact."""
    runner.idle = IdleWatch(15.0, clock=clock)


def test_a_console_try_does_not_earn_the_room_a_goodbye(
        tmp_path, monkeypatch, caplog) -> None:
    """An empty session: the try answers, the room stays unheard, the close is silent."""
    clock = FakeClock(3600.0)
    tts = FakeTts()
    app = _console_app(tmp_path, monkeypatch, tts)
    with TestClient(app) as client:
        runner = app.state.runner
        _fake_clock(runner, clock)
        body = client.post("/api/v1/debug/say",
                           json={"text": "现在几点", "device": "living"}).json()

        assert body["reply"] == "一点了。", "the try itself must still answer"
        assert body["error"] == ""
        assert tts.texts == ["一点了。"]
        assert runner._had_speech is False, (
            "the console's 试一试 was counted as 客厅 speaking")
        assert runner.idle.active, "a try is still activity for the idle clock"

        tts.texts.clear()
        clock.advance(15.0)
        with caplog.at_level(logging.INFO, logger="rui.session.turn"):
            said = client.portal.call(
                runner.check_idle, _discard, TurnTrace(device_id="living"))

        assert said == ""
        assert tts.texts == [], "the speaker said goodbye to an empty room"
        assert not runner.idle.active
        assert "silent close" in caplog.text


def test_a_console_try_gives_the_live_conversation_back(tmp_path, monkeypatch) -> None:
    """A real turn, then a console try: history and the speech flag come back whole."""
    clock = FakeClock(1800.0)
    tts = FakeTts()
    app = _console_app(tmp_path, monkeypatch, tts)
    with TestClient(app) as client:
        runner = app.state.runner
        _fake_clock(runner, clock)
        # The room speaks (the voice path uses `run()` with its defaults).
        client.portal.call(runner.run, b"\x00\x00" * 800, _discard,
                           TurnTrace(device_id="living"))
        before = [dict(row) for row in runner.history]
        assert runner._had_speech is True
        assert len(before) == 2, before   # the question and the answer
        history_object = id(runner.history)
        tts.texts.clear()

        body = client.post("/api/v1/debug/say",
                           json={"text": "今天几号", "device": "living"}).json()

        assert body["reply"] == "一点了。", "the try must answer on its own"
        assert tts.texts == ["一点了。"], tts.texts
        assert runner._had_speech is True, (
            "a console try erased the fact that the room spoke")
        assert runner.history == before, "the console try threw the conversation away"
        assert id(runner.history) == history_object, (
            "history must be restored in place - channel turns hold the list")

        # And the real conversation still ends the way it should.
        clock.advance(15.0)
        assert client.portal.call(
            runner.check_idle, _discard, TurnTrace(device_id="living")
        ) == "没事的话，我先下啦。"


def test_the_try_reaches_the_runner_as_not_speech(tmp_path, monkeypatch) -> None:
    """The kwarg is the fix, so a spy on `runner.run` has to see it.

    Pinning it through the session's state cannot work: the `finally` restore puts
    `_had_speech` back whether the turn counted as speech or not, so it erases the
    evidence. Deleting `counts_as_speech=False` from `app.py` left this file 3 passed
    (reviewer, 0.8.13). The call's own arguments are the only place the rule is visible.
    """
    clock = FakeClock(1200.0)
    tts = FakeTts()
    app = _console_app(tmp_path, monkeypatch, tts)
    with TestClient(app) as client:
        runner = app.state.runner
        _fake_clock(runner, clock)
        calls: list[dict] = []
        real_run = runner.run

        async def spy(*args: object, **kwargs: object):
            calls.append(dict(kwargs))
            return await real_run(*args, **kwargs)

        monkeypatch.setattr(runner, "run", spy)
        body = client.post("/api/v1/debug/say",
                           json={"text": "现在几点", "device": "living"}).json()

        assert body["reply"] == "一点了。", "the spy must not change what the try answers"
        assert len(calls) == 1, calls
        assert calls[0].get("counts_as_speech") is False, (
            "the console try reached runner.run without counts_as_speech=False, so it "
            f"still counts as 客厅 speaking: {calls[0]}"
        )
        # The reachable half of the same rule, for the record: the room stays unheard.
        assert runner._had_speech is False


def test_a_failed_console_try_also_restores_the_conversation(
        tmp_path, monkeypatch) -> None:
    """The restore is a `finally`: a try that blows up must not end the session."""
    clock = FakeClock(9000.0)
    tts = FakeTts()
    app = _console_app(tmp_path, monkeypatch, tts)
    with TestClient(app, raise_server_exceptions=False) as client:
        runner = app.state.runner
        _fake_clock(runner, clock)
        client.portal.call(runner.run, b"\x00\x00" * 800, _discard,
                           TurnTrace(device_id="living"))
        before = [dict(row) for row in runner.history]
        assert runner._had_speech is True
        tts.texts.clear()

        async def boom(*_args: object, **_kwargs: object) -> None:
            # The try cleared the context and then failed: exactly the case that used
            # to leave the room with no history and no goodbye.
            assert runner.history == [], "the try should clear the context first"
            assert runner._had_speech is True, "the try must not set the flag either"
            raise RuntimeError("the model connection dropped")

        monkeypatch.setattr(runner, "run", boom)
        response = client.post("/api/v1/debug/say",
                               json={"text": "现在几点", "device": "living"})

        assert response.status_code == 500, response.text
        assert runner.history == before, "a failed try threw the conversation away"
        assert runner._had_speech is True, "a failed try erased the room's speech"
        assert tts.texts == []
        clock.advance(15.0)
        assert client.portal.call(
            runner.check_idle, _discard, TurnTrace(device_id="living")
        ) == "没事的话，我先下啦。"
