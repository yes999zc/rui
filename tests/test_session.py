from __future__ import annotations

from types import SimpleNamespace

import pytest

from rui.session import rules
from rui.session.state import InvalidTransition, SessionState, SessionStateMachine


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
    from rui.session import rules

    assert rules.stop_intent("下课", ["下课"]) is True
    # A default stop word absent from the configured list must not dismiss.
    assert rules.stop_intent("退下吧", ["下课"]) is False


def test_stop_scope_is_media_stop_only_while_a_song_plays() -> None:
    """D1c ③: the same stop word means "stop the song" or "end the conversation"."""
    from rui.session import rules

    # The stated rule: 「停止播放」 with nothing playing = 散场.
    assert rules.stop_scope("停止播放", music_playing=False) == rules.BARE_STOP
    assert rules.stop_scope("停止播放", music_playing=True) == rules.MEDIA_STOP
    # The whole stop family follows the same split.
    for word in ("停止", "停下来", "停下", "别放", "不听了", "停一下"):
        assert rules.stop_scope(word, music_playing=True) == rules.MEDIA_STOP, word
        assert rules.stop_scope(word, music_playing=False) == rules.BARE_STOP, word


def test_stop_scope_keeps_dismissals_ending_the_conversation() -> None:
    """「退下吧」 names the conversation, not the speaker - even mid-song."""
    from rui.session import rules

    for word in ("退下吧", "退下", "不聊了", "对一下吧"):
        assert rules.stop_scope(word, music_playing=True) == rules.BARE_STOP, word
        assert rules.stop_scope(word, music_playing=False) == rules.BARE_STOP, word
    # An ASR slip of a dismissal is still a dismissal (measured 「对一下吧」).
    assert rules.is_dismiss("对一下吧")
    # 关机 is no longer a default (P0: it was one of the HA verbs), but a config that
    # explicitly keeps it still dismisses: removing a default is not hard-coding a ban.
    assert rules.stop_scope("关机", music_playing=False) == ""
    assert rules.stop_scope("关机", music_playing=False, words=["关机"]) == rules.BARE_STOP


def test_stop_intent_is_whole_request_only() -> None:
    """P0: substring matching dismissed the session before HA ran (audit 2026-10-23).

    「关掉客厅灯」「关闭窗帘」 were True with the old default list; explicit aliases keep
    「停止播放」 and one closed-set particle keeps 「别说了吧」, but a longer sentence is not
    a stop (audit A C2 removed the "up to two extra characters anywhere" rule).
    """
    from rui.session import rules

    for command in ("关掉客厅灯", "关闭窗帘", "关空调", "打开客厅灯"):
        assert rules.stop_intent(command) is False, command
    for request in ("停止", "停止播放", "别说了", "别说了吧", "退下吧", "对一下吧"):
        assert rules.stop_intent(request) is True, request
    # A configured word only matches the whole request: exact word, explicit
    # ALIASES entry, or one closed-set particle.
    assert rules.stop_intent("下课", ["下课"]) is True
    assert rules.stop_intent("今天下课", ["下课"]) is False    # whole request only (C2)
    assert rules.stop_intent("今天下课以后去食堂", ["下课"]) is False


def test_stop_intent_strips_one_fixed_wake_tail() -> None:
    """d.6 (2026-10-28): 「停止你好」 is a stop, worked out from a closed tail list.

    ASR appends the wake word while it is still audible at the segment boundary, so the
    measured 「停止你好」 was a dismissal that fell through to the model (N2). The fix
    strips exactly one fixed wake tail from the tail only, then runs the same
    whole-request match - no length or position tolerance comes back.
    """
    from rui.session import rules

    # The five required cases.
    assert rules.stop_intent("停止你好") is True
    assert rules.stop_intent("停止播放你好") is True        # through ALIASES
    assert rules.stop_intent("你好小瑞") is False           # pure wake word
    assert rules.stop_intent("你好停止") is False           # tail only, never the head
    assert rules.stop_intent("停止了吗") is False           # a question stays a question

    # Boundaries: the longest tail wins, a tail never rescues a question, and a second
    # tail is not peeled (one strip only).
    assert rules.stop_intent("停止你好小瑞") is True
    assert rules.stop_intent("停止你好吗") is False
    assert rules.stop_intent("") is False
    assert rules.stop_intent("停止你好你好") is False


def test_stop_scope_returns_empty_for_non_stop_requests() -> None:
    from rui.session import rules

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
        self.followups: list[bool] = []

    async def send(self, msg: object) -> None:
        self.sent.append(msg)

    async def speak(self, pcm: bytes, **kwargs: object) -> None:
        return None

    async def reply_finished(self, **kwargs: object) -> None:
        self.followups.append(bool(kwargs.get("followup", True)))


class _StopTraces:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, trace: object) -> None:
        self.items.append(trace)


def _stop_env(monkeypatch, state: str | None):
    import rui.web.api.app as app_module
    from rui.config import Config

    monkeypatch.setattr(app_module, "_light_after_audio", lambda *a, **k: None)
    service = _StopMusic(state) if state else None
    app = SimpleNamespace(state=SimpleNamespace(
        cfg=Config(), music=service, last_turn=None))
    return app_module, app, service, _StopRunner(), _StopClient(), _StopTraces()


def test_stop_word_with_music_stops_the_song_and_closes_the_session(monkeypatch) -> None:
    """Audit A C1: an end word over music stops the song AND closes the session."""
    import asyncio

    from rui.trace import TurnTrace

    app_module, app, service, runner, client, traces = _stop_env(monkeypatch, "playing")
    handled = asyncio.run(app_module._stop_command(
        app, "停止播放", runner, client, TurnTrace(device_id="living"), traces))

    assert handled is True
    assert service is not None and service.stops == 1
    assert runner.said == [], "the old 「music stopped, keep listening」 line must be gone"
    assert runner.dismissed == 1, "an end word over music must close the session"
    assert app.state.last_turn["session_action"] == "stop_and_dismiss"
    # The close finish still reaches the plane (drain + mute), but it must say
    # followup=False: a closed session opens no wake-free window.
    assert client.followups == [False]


def test_bare_stop_without_music_ends_the_session(monkeypatch) -> None:
    """The measured old rule: 「停止播放」 with nothing playing = 散场."""
    import asyncio

    from rui.trace import TurnTrace

    app_module, app, service, runner, client, traces = _stop_env(monkeypatch, "idle")
    handled = asyncio.run(app_module._stop_command(
        app, "停止播放", runner, client, TurnTrace(device_id="living"), traces))

    assert handled is True
    assert service is not None and service.stops == 0
    assert runner.dismissed == 1
    assert runner.said == []
    assert app.state.last_turn["session_action"] == "bare_stop"
    assert client.followups == [False]


def test_a_dismissal_mid_song_stops_the_music_then_ends_the_session(monkeypatch) -> None:
    """Measured 09:17: the song came back 12 s after 「退下吧」 - it must be stopped."""
    import asyncio

    from rui.trace import TurnTrace

    app_module, app, service, runner, client, traces = _stop_env(monkeypatch, "playing")
    handled = asyncio.run(app_module._stop_command(
        app, "退下吧", runner, client, TurnTrace(device_id="living"), traces))

    assert handled is True
    assert service is not None and service.stops == 1
    assert runner.dismissed == 1
    assert app.state.last_turn["session_action"] == "bare_stop"


def test_bare_stop_silences_a_stale_music_owner_without_the_service(monkeypatch) -> None:
    import asyncio

    from rui.audio.protocol import StopAudio
    from rui.trace import TurnTrace

    app_module, app, _service, runner, client, traces = _stop_env(monkeypatch, None)
    client.metrics.output_owner = "music"
    handled = asyncio.run(app_module._stop_command(
        app, "停止", runner, client, TurnTrace(device_id="living"), traces))

    assert handled is True
    assert any(isinstance(msg, StopAudio) for msg in client.sent)
    assert runner.dismissed == 1


class _StopCoordinator:
    def __init__(self) -> None:
        self.owners: list[object] = []

    def set_owner(self, value: object) -> None:
        self.owners.append(value)


def test_media_stop_in_another_room_does_not_cut_the_song(monkeypatch) -> None:
    """Section 4.4: a stop word in a non-owner room ends that room's session only."""
    import asyncio

    from rui.audio.protocol import StopAudio
    from rui.trace import TurnTrace

    app_module, app, service, runner, client, traces = _stop_env(monkeypatch, "playing")
    coordinator = _StopCoordinator()
    app.state.output_owner = "study"
    app.state.wake_coordinator = coordinator
    handled = asyncio.run(app_module._stop_command(
        app, "停止播放", runner, client, TurnTrace(device_id="living"), traces))

    assert handled is True
    assert service is not None and service.stops == 0, "the song belongs to study"
    assert runner.dismissed == 1, "this room's session still closes"
    assert any(isinstance(msg, StopAudio) for msg in client.sent)
    assert app.state.last_turn["session_action"] == "stop_elsewhere"
    assert app.state.output_owner == "study"
    assert coordinator.owners == [], "another room's stop must not release the owner"


def test_stop_in_the_owner_room_releases_the_output(monkeypatch) -> None:
    """The room that owns the speaker stops it and the house's owner is released."""
    import asyncio

    from rui.trace import TurnTrace

    app_module, app, service, runner, client, traces = _stop_env(monkeypatch, "playing")
    coordinator = _StopCoordinator()
    app.state.output_owner = "living"
    app.state.wake_coordinator = coordinator
    handled = asyncio.run(app_module._stop_command(
        app, "停止播放", runner, client, TurnTrace(device_id="living"), traces))

    assert handled is True
    assert service is not None and service.stops == 1
    assert app.state.output_owner == ""
    assert coordinator.owners[-1] is None


def test_a_non_stop_request_is_left_to_the_turn(monkeypatch) -> None:
    import asyncio

    from rui.trace import TurnTrace

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
    # 16:51: a clarification question was mistaken for a success claim (给你放) and
    # triggered a retry whose forced text leaked into the transcript and the search.
    assert not rules.claims_music_success(
        "周杰伦的。", "想听周杰伦哪首？说个歌名，我就给你放。")
    assert not rules.claims_music_success(
        "放首歌", "你想听什么？说一首，我就给你放。")
