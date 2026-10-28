# task-32 — tool reminder room + short-sentence merge window

Status: implemented in the working tree (2026-10-28), not committed / tagged / deployed.

Two independent fixes reported from the same 次卧 sentence:

1. 「一分钟后提醒我喝水。我是小志。」 reached the **LLM tool** path (`local.set_reminder`),
   stored `room=None`, and rang on the living default output.
   0.8.3/task-31 only fixed the fixed-sentence path (`_voice_reminder_command`).
2. The 13-character reply 「一分钟后提醒你喝水，小志。」 was split at its comma into
   `tts_call#1 chars=10` plus a second 624 ms request; the vendor head/tail silence
   became an audible seam.

## 1. Tool context carries the speaking device's room

Chain, one value from the plane that heard the sentence to the handler that writes it:

```
 app.py on_utterance (device_id, link)
   └─ _tool_context(cfg, link)                 # ToolContext(device_id, room)
        └─ _reminder_room(cfg, link)           # ONE resolver (shared with voice path)
             └─ _device_room(cfg, _speaking_device_id(link))
   └─ runner.run(..., tool_context=ctx)
        └─ use_tool_context(ctx)               # ContextVar, scoped to this turn
             └─ _call_tool -> set_reminder handler
                  └─ store.add(text, at, room=current_tool_context().room)
```

- `src/rui/tools/reminders.py`
  - `ToolContext(device_id, room)` and a `ContextVar` with
    `current_tool_context()` / `use_tool_context()`.
  - `_set_handler` passes `room=current_tool_context().room or None` to
    `ReminderStore.add`. Outside a turn the context is empty, so a console/channel
    reminder keeps the historical `room=None` (default output at delivery time).
  - A log line records the stored room (`reminders: tool set ... for room ...`).
- `src/rui/session/turn.py`
  - `TurnRunner.run(..., tool_context=None)` wraps the turn in `use_tool_context`.
    The old body moved to `_run_turn` unchanged.
  - ContextVar rather than a runner attribute: one `TurnRunner` serves every room.
    A per-turn ContextVar copy is task-local, so a 客厅 turn cannot observe a 次卧
    tool call (and a channel turn gets an empty context).
- `src/rui/web/api/app.py`
  - `_reminder_room(cfg, client, named=None)` is the only resolver: a named room
    wins, otherwise the speaking device's configured room; unknown/room-less stays
    `""` (stored as `None`). The fixed-sentence path
    (`_voice_reminder_command`) and `_tool_context` both call it.
  - `_tool_context(cfg, client)` builds the `ToolContext` for the audio turn.
  - Both audio `runner.run` calls (first answer and the false-claim retry) pass it.
  - Channel/console turns pass no context: they have no speaking room, so `room`
    stays `None` there by design.

`remember` (the memory tool) has no room field and memory is not room-scoped; the
context is available to it, but this task does not add a room to memory rows.

Delivery needs no change: `_deliver_reminder` already routes `Reminder.room` through
`_reminder_route`, stops only that room's music, never resumes it, and falls back to
`default_output` + 「<room>那台没在线」 when the source room is offline.

## 2. Ultra-short merge window: `SOFT_MIN_CHARS` 10 -> 16

Chosen rule: **do not break at a comma until the clause is at least 16 characters.**

Why this rather than "never split inside one natural sentence":

- While streaming, the sentence boundary is unknown; a rule that waits for one would
  add latency to every long answer and could hold the first clause until the model
  stops. A deterministic character window keeps the existing early-first-audio path
  for clauses of 16+ characters.
- 16 covers the measured 13-character sentence with margin, including a short name
  after the comma. The exact failing shape is
  `一分钟后提醒你喝水，小志。` (comma at char 10, full stop at char 13).
- A sentence end (`。！？；`) is still released immediately, so the extra wait is
  bounded by the characters between the comma and the full stop, not by a timer.
  The measured first-audio lead is 343 ms; this case waits 3 characters longer than
  before, which fits that margin. Long-clause answers (the ones that actually stream
  early) are unchanged.
- A 19-character two-sentence reply still reaches TTS as two requests, because the
  sentence-end rule is untouched.

## Tests and evidence

Targeted files:

```
uv run pytest -q -o addopts="" \
  tests/test_reminders.py tests/test_reminder_voice.py \
  tests/test_reminder_delivery.py tests/test_turn.py tests/test_app_fleet.py
```

New/updated coverage:

- `tests/test_reminders.py`: tool handler stores `room="次卧"` under
  `ToolContext`; without a context the room stays `None`.
- `tests/test_turn.py`: 13-char comma reply = one TTS request (single chunk and the
  reported two-chunk stream); 19-char two-sentence reply = two requests; the
  existing clause-split test now uses a 16-char clause.
- `tests/test_speech_text.py`: the clause-gap test uses the new 16-char boundary.
- `tests/test_reminder_delivery.py`: a reminder created through the tool surface
  stores 次卧; 次卧 playing -> stop + speak + no resume, 客厅 untouched; 客厅
  playing -> 客厅 untouched, 次卧 speaks (no reply/follow-up).
- `tests/test_app_fleet.py`: full app path — wake in 次卧, utterance
  「一分钟后提醒我喝水。我是小志。」, scripted LLM calls `set_reminder`; the stored
  item is `("喝水", "次卧")`.

Red -> green (each fix reverted on purpose, then restored):

| Mutation | Result |
| --- | --- |
| `SOFT_MIN_CHARS = 10` | 2 failed: `tts_call#1 chars=10` + `chars=3` for the 13-char reply; 19-char test still passed |
| `runner.run` called without `tool_context=` | app test failed: stored room `None` |
| handler ignores `current_tool_context()` | unit test failed: stored room `None` |

Green:

```
targeted (the files above + tests/test_speech_text.py): 217 passed, 1 skipped
1937 passed, 6 skipped in 145.52s
uv run ruff check .  ->  All checks passed!
```

Baseline was 1929 passed / 6 skipped; this change adds 8 tests.

## Residual risks

- The 16-char window defers the comma split by at most 6 characters. Only the
  measured 13-char case is covered; a 16+ char single sentence with a comma still
  splits (by design, to protect first-audio latency). If a longer single-sentence
  seam appears, the next step is a sentence-boundary heuristic, not a larger number.
- `tool_context` is wired for audio turns only. Channel text turns deliberately see
  an empty context (no speaking room); a future channel-to-room mapping must build
  its own `ToolContext`.
- `remember` does not store a room; the reported sentence's name ("我是小志") is a
  memory concern, not a reminder-routing concern.
