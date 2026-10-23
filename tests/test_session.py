from __future__ import annotations

import pytest

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
