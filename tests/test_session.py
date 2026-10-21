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
