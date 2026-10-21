# ADR 0003 — wake arbitration: first frame wins

Status: accepted (2026-10-21)

## Context

With one speaker per room, several can hear the same wake word. Options: loudest
signal, room-qualified wake word, HA area proximity, first frame to arrive.

## Decision

First frame wins, with a 200 ms comparison window. Ties break by device id.

## Consequences

- Deterministic and explainable ("the one that heard you first answered").
- No dependency on HA areas, which many users do not maintain.
- Audio from the winner is buffered during the window, so the answer is not
  delayed by arbitration.
- Room-qualified wake words remain a config option, not the default.
