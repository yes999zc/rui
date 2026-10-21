"""Explicit session state machine. Barge-in is a transition, not an if-statement."""
from __future__ import annotations

from enum import StrEnum


class InvalidTransition(RuntimeError):
    pass


class SessionState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"


_ALLOWED: dict[SessionState, set[SessionState]] = {
    SessionState.IDLE: {SessionState.LISTENING},
    SessionState.LISTENING: {SessionState.THINKING, SessionState.IDLE, SessionState.INTERRUPTED},
    SessionState.THINKING: {SessionState.SPEAKING, SessionState.IDLE, SessionState.INTERRUPTED},
    SessionState.SPEAKING: {SessionState.INTERRUPTED, SessionState.IDLE, SessionState.LISTENING},
    SessionState.INTERRUPTED: {SessionState.LISTENING, SessionState.IDLE},
}


class SessionStateMachine:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.state = SessionState.IDLE

    def can(self, target: SessionState) -> bool:
        return target in _ALLOWED[self.state]

    def to(self, target: SessionState) -> SessionState:
        if not self.can(target):
            raise InvalidTransition(f"{self.device_id}: {self.state} -> {target} not allowed")
        self.state = target
        return self.state

    def barge_in(self) -> SessionState:
        """Woken while speaking: stop playback first, then listen."""
        if self.state is not SessionState.SPEAKING:
            raise InvalidTransition(f"{self.device_id}: barge-in only from speaking, not {self.state}")
        self.state = SessionState.INTERRUPTED
        return self.to(SessionState.LISTENING)
