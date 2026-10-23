from __future__ import annotations

from types import SimpleNamespace

import pytest

from house_ear.session import rules
from house_ear.session.state import InvalidTransition, SessionState, SessionStateMachine


def test_happy_path() -> None:
    sm = SessionStateMachine("living")
    sm.to(SessionState.LISTENING)
    sm.to(SessionState.THINKING)
    sm.to(SessionState.SPEAKING)
    assert sm.state is SessionState.SPEAKING


def test_barge_in_stops_then_listens() -> None:
    sm = SessionStateMachine("living")
    sm.to(SessionState.LISTENING)
    sm.to(SessionState.THINKING)
    sm.to(SessionState.SPEAKING)
    assert sm.barge_in() is SessionState.LISTENING


def test_cannot_barge_in_when_idle() -> None:
    with pytest.raises(InvalidTransition):
        SessionStateMachine("living").barge_in()


def test_stop_intent_uses_the_configured_words() -> None:
    from house_ear.session import rules

    assert rules.stop_intent("下课", ["下课"]) is True
    # A default stop word absent from the configured list must not dismiss.
    assert rules.stop_intent("退下吧", ["下课"]) is False


def test_stop_scope_is_media_stop_only_while_a_song_plays() -> None:
    """D1c ③: the same stop word means "stop the song" or "end the conversation"."""
    from house_ear.session import rules

    # The stated rule: 「停止播放」 with nothing playing = 散场.
    assert rules.stop_scope("停止播放", music_playing=False) == rules.BARE_STOP
    assert rules.stop_scope("停止播放", music_playing=True) == rules.MEDIA_STOP
    # The whole stop family follows the same split.
    for word in ("停止", "停下来", "停下", "别放", "关闭", "不听了", "停一下"):
        assert rules.stop_scope(word, music_playing=True) == rules.MEDIA_STOP, word
        assert rules.stop_scope(word, music_playing=False) == rules.BARE_STOP, word


def test_stop_scope_keeps_dismissals_ending_the_conversation() -> None:
    """「退下吧」 names the conversation, not the speaker - even mid-song."""
    from house_ear.session import rules

    for word in ("退下吧", "退下", "关机", "不聊了", "对一下吧"):
        assert rules.stop_scope(word, music_playing=True) == rules.BARE_STOP, word
        assert rules.stop_scope(word, music_playing=False) == rules.BARE_STOP, word
    # An ASR slip of a dismissal is still a dismissal (measured 「对一下吧」).
    assert rules.is_dismiss("对一下吧")


def test_stop_scope_returns_empty_for_non_stop_requests() -> None:
    from house_ear.session import rules

    assert rules.stop_scope("现在几点了", music_playing=True) == ""
    assert rules.stop_scope("把灯关掉以后告诉我天气怎么样", music_playing=True) == ""
    assert rules.stop_scope("", music_playing=True) == ""
    # The configured end-word list is the same gate `stop_intent` uses.
    assert rules.stop_scope("下课", music_playing=False, words=["下课"]) == rules.BARE_STOP
    assert rules.stop_scope("退下吧", music_playing=True, words=["下课"]) == ""


# ---------------------------------------------------------------------------
# D1c ③: the app-level stop branch (media_stop vs bare_stop)
# ---------------------------------------------------------------------------
class _StopRunner:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.dismissed = 0
        self.idle = SimpleNamespace(touch=lambda: None)

    async def say(self, text: str, speak: object, trace: object) -> int:
        self.said.append(text)
        return 3200

    async def dismiss(self, speak: object, trace: object) -> int:
        self.dismissed += 1
        return 3200


class _StopMusic:
    def __init__(self, state: str) -> None:
        self.state = state
        self.stops = 0

    async def stop(self) -> None:
        self.stops += 1
        self.state = "idle"


class _StopMetrics:
    output_owner = ""


class _StopClient:
    def __init__(self) -> None:
        self.metrics = _StopMetrics()
        self.sent: list[object] = []
        self.reply_finished_calls = 0

    async def send(self, msg: object) -> None:
        self.sent.append(msg)

    async def speak(self, pcm: bytes, **kwargs: object) -> None:
        return None

    async def reply_finished(self, **kwargs: object) -> None:
        self.reply_finished_calls += 1


class _StopTraces:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, trace: object) -> None:
        self.items.append(trace)


def _stop_env(monkeypatch, state: str | None):
    import house_ear.web.api.app as app_module
    from house_ear.config import Config

    monkeypatch.setattr(app_module, "_light_after_audio", lambda *a, **k: None)
    service = _StopMusic(state) if state else None
    app = SimpleNamespace(state=SimpleNamespace(
        cfg=Config(), music=service, last_turn=None))
    return app_module, app, service, _StopRunner(), _StopClient(), _StopTraces()


def test_media_stop_stops_the_song_and_keeps_the_session(monkeypatch) -> None:
    """D1c ③: with a song playing, 「停止播放」 stops the media, not the talk."""
    import asyncio

    from house_ear.session import phrases
    from house_ear.trace import TurnTrace

    app_module, app, service, runner, client, traces = _stop_env(monkeypatch, "playing")
    handled = asyncio.run(app_module._stop_command(
        app, "停止播放", runner, client, TurnTrace(device_id="living"), traces))

    assert handled is True
    assert service is not None and service.stops == 1
    assert runner.said == [phrases.book.render("music.stopped")]
    assert runner.dismissed == 0, "a media stop must not close the session"
    assert app.state.last_turn["session_action"] == "media_stop"
    assert client.reply_finished_calls == 1


def test_bare_stop_without_music_ends_the_session(monkeypatch) -> None:
    """The measured old rule: 「停止播放」 with nothing playing = 散场."""
    import asyncio

    from house_ear.trace import TurnTrace

    app_module, app, service, runner, client, traces = _stop_env(monkeypatch, "idle")
    handled = asyncio.run(app_module._stop_command(
        app, "停止播放", runner, client, TurnTrace(device_id="living"), traces))

    assert handled is True
    assert service is not None and service.stops == 0
    assert runner.dismissed == 1
    assert runner.said == []
    assert app.state.last_turn["session_action"] == "bare_stop"
    assert client.reply_finished_calls == 0


def test_a_dismissal_mid_song_stops_the_music_then_ends_the_session(monkeypatch) -> None:
    """Measured 09:17: the song came back 12 s after 「退下吧」 - it must be stopped."""
    import asyncio

    from house_ear.trace import TurnTrace

    app_module, app, service, runner, client, traces = _stop_env(monkeypatch, "playing")
    handled = asyncio.run(app_module._stop_command(
        app, "退下吧", runner, client, TurnTrace(device_id="living"), traces))

    assert handled is True
    assert service is not None and service.stops == 1
    assert runner.dismissed == 1
    assert app.state.last_turn["session_action"] == "bare_stop"


def test_bare_stop_silences_a_stale_music_owner_without_the_service(monkeypatch) -> None:
    import asyncio

    from house_ear.audio.protocol import StopAudio
    from house_ear.trace import TurnTrace

    app_module, app, _service, runner, client, traces = _stop_env(monkeypatch, None)
    client.metrics.output_owner = "music"
    handled = asyncio.run(app_module._stop_command(
        app, "停止", runner, client, TurnTrace(device_id="living"), traces))

    assert handled is True
    assert any(isinstance(msg, StopAudio) for msg in client.sent)
    assert runner.dismissed == 1


def test_a_non_stop_request_is_left_to_the_turn(monkeypatch) -> None:
    import asyncio

    from house_ear.trace import TurnTrace

    app_module, app, _service, runner, client, traces = _stop_env(monkeypatch, "playing")
    handled = asyncio.run(app_module._stop_command(
        app, "现在几点了", runner, client, TurnTrace(device_id="living"), traces))
    assert handled is False
    assert runner.said == [] and runner.dismissed == 0


def test_a_music_claim_without_a_tool_call_is_recognised() -> None:
    """13:47:36: the music guard refused the call (the verb was lost by ASR) and the
    model still answered 「好，放《告白气球》。」 - a false claim the user acts on."""
    assert rules.claims_music_success("告白气球。", "好，放《告白气球》。")
    assert rules.claims_music_success("放首歌", "正在放《晴天》-周杰伦。")
    assert not rules.claims_music_success("放首歌", "好。")
    assert not rules.claims_music_success("现在几点？", "现在下午一点。")
