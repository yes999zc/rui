# Short-reply seam on dot2-b (task-30, 2026-10-28)

## Symptom

A 5-character reply 「在，你说。」 was heard with an audible gap at the comma on
`dot2-b`. The turn trace had `playback_gap_count=0`, so the existing underrun
instrumentation reported nothing: the seam is **silence inside the PCM**, not a starved
device queue.

## What the instrumentation says

Added (additive only, no behaviour change in itself):

| Where | Field / line | Meaning |
|---|---|---|
| `session/turn.py` | `tts_call#k chars=N provider=... synth=... audio=... gap=... text=...` | one line per vendor request |
| `TurnTrace.meta` | `tts_call_count`, `tts_call_chars`, `tts_call_audio_ms`, `tts_inter_call_silence_ms` | producer-side ledger for the turn |
| `audio/process.py` `reply:` | `reply: N chunks, lead max X ms, S segments, segments_ms=[...], silences_ms=[...], inner_ms=[...]` | per reply, at drain |
| `Metrics` (wire) | `reply_segment_count`, `reply_segment_audio_ms`, `reply_segment_silences_ms`, `reply_segment_inner_silence_ms` | survives the plane → control wire |
| `TurnTrace.meta` | the same four fields (via `note_playback_metrics`) | readable from a turn trace |

`silences_ms[k]` is the audible seam before segment `k+1`:
`previous trailing silence + producer stall + next leading silence`.
`inner_ms[k]` is the longest silent run **inside** one rendition (the vendor's own comma
pause). A five-character reply that reaches TTS once reports `segments=1` and
`silences=[]`; if it still sounds paused, `inner_ms` is the number to look at.

## Reproduction

`scripts/measure_reply_seam.py` drives the real `TurnRunner` streaming/split/TTS path
with a vendor-shaped fake TTS: every request is **80 ms pad + 60 ms tone + (comma pause:
120 ms) + 60 ms tone + 80 ms pad**. Two requests therefore put at least 160 ms of zero
PCM at the seam, and the comma pause inside one rendition is reported as `inner_ms`.
The `HEAD` row emulates the pre-task-30 release policy (hold until 300 ms, then split);
`task-30` runs the current policy.

```
uv run python scripts/measure_reply_seam.py
```

Result (2026-10-28):

| reply / delivery | mode | TTS calls | first audio | inner pause | seam |
|---|---|---:|---:|---:|---:|
| `在，你说。` (one delta) | HEAD | 1 | 0 ms | 120 ms | — |
| `在，你说。` (one delta) | task-30 | 1 | 0 ms | 120 ms | — |
| `好，稍等。` / `嗯，好的。` | HEAD / task-30 | 1 / 1 | 0 ms | 120 ms | — |
| `在，\n` + 400 ms + `你说。` | HEAD | 2 | 402 ms | 120 + 0 ms | **160 ms** |
| `在，\n` + 400 ms + `你说。` | task-30 | 1 | 402 ms | 120 ms | — |
| `好，\n` + 400 ms + `稍等。` | HEAD | 2 | 402 ms | 120 + 0 ms | **160 ms** |
| `好，\n` + 400 ms + `稍等。` | task-30 | 1 | 402 ms | 120 ms | — |
| `嗯，\n` + 400 ms + `好的。` | HEAD | 2 | 401 ms | 120 + 0 ms | **160 ms** |
| `嗯，\n` + 400 ms + `好的。` | task-30 | 1 | 402 ms | 120 ms | — |

Three of three splitting repros confirm a seam > 60 ms at HEAD; the literal single-line
5-character strings do **not** split at HEAD either. They do have the vendor's own
120 ms comma pause (`inner_ms=120`) inside one rendition - that is normal prosody, not a
second TTS request. The remaining split is the `d.10` residual risk: the short-reply
hold was released by **splitting** when the model paused longer than 300 ms after the
comma/newline. That is the shape the LLM actually emitted in the measured case
(`在，\n你说。`), and it is what the fix targets: the seam drops from **160 ms** to none,
leaving only the rendition's own comma pause.

## Chosen fix

`session/turn.py`, `split_sentences(..., hold_short=True)`:

* the ultra-short window is 10 characters (was 8), covering every 5-character reply
  plus the newline;
* when the hold expires (or the tail/`force` arrives), an ultra-short buffer that would
  split is emitted as **one merged piece**, never as two pieces. An incomplete buffer
  (`在，\n你`) keeps waiting; the merge happens on the delta that completes the reply.
* first-audio timing is unchanged: the release still happens on the same delta as the
  old split (402 ms vs 402 ms in the table); the literal one-sentence reply still goes
  out at 0 ms.

No changes to barge-in, AEC, volume, follow-up, or the playback path. The plane-side
change is instrumentation only: it reads samples for the ledger and does not modify
them.

## Tests

* `tests/test_segment_silence.py` (new, 9 tests): slow newline short reply is one TTS call;
  literal 5-character reply is one call; multi-piece trace exposes count/gaps; plane
  ledger exposes segment count, duration, seam and inner silence; the drain-time `reply:`
  line carries both; wire round-trip; trace copy; older plane without the fields leaves the
  trace unchanged.
* `tests/test_turn.py::test_the_short_reply_hold_is_bounded` updated: the 300 ms release
  now merges instead of splitting.
* Red evidence: the new tests fail on `f8dc9fc` (7 failed, 1 passed — the older-plane
  compatibility test); they pass on the change.
* `uv run pytest -o addopts=""`: see the task report for the final count; `uv run ruff
  check .` is clean.

## Residual risks

* A sentence boundary inside a longer reply still gets the intentional 350 ms
  `SENTENCE_GAP_MS`; only the ultra-short split is merged.
* If a model pauses for seconds in the middle of an ultra-short sentence, the hold waits
  for the completion (bounded by the 30 s turn ceiling) instead of speaking a fragment
  that would later split. A complete utterance at the 300 ms cap is merged immediately.
* `inner_ms` labels the vendor's comma pause but deliberately does not trim it: cutting
  samples inside a rendition could clip speech, and the permitted fix (b) only trims
  head/tail silence.
* The plane keeps the last reply's segment ledger until the next reply starts, so a
  trace read just after `run()` always finds its own reply. A turn that spoke nothing
  can therefore inherit the previous reply's ledger; the turn's `audio_bytes` remains
  the authoritative "did this turn speak" field.
