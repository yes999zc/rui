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
