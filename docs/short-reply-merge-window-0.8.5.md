# short-reply merge window — 17–20 char comma replies (0.8.5, task-3 / E)

Status: implemented in the working tree (2026-10-28); not committed, tagged or deployed.
Not validated on a real speaker — see "Not covered".

## Background

0.8.3/0.8.4 (task-32, `SOFT_MIN_CHARS` 10 -> 16) fixed the measured 13-character reply
「一分钟后提醒你喝水，小志。」: the comma at char 10 used to split it into
`tts_call#1 chars=10` plus a second 624 ms request, and the vendor's head/tail silence
made the comma audible. The residual doubt was one length band higher: a reply of
17–20 characters whose comma sits at the 16-character boundary would still be cut into
"16 chars + 1–4 char tail", which is the same defect.

Measured before the fix (fake TTS, one `synthesize()` call per vendor request; the
`_run_reply` helper in `tests/test_turn.py` streams the whole reply as one delta):

| total chars | sample | comma at | TTS calls before | TTS calls after |
| --- | --- | --- | --- | --- |
| 17 | 今天沈阳多云转晴最高气温二十度，好 | char 16 | 2 | **1** |
| 18 | 今天沈阳多云转晴最高气温二十度，好。 | char 16 | 2 | **1** |
| 19 | 今天沈阳多云转晴最高气温二十度，有雨。 | char 16 | 2 | **1** |
| 20 | 今天沈阳多云转晴最高气温二十度，下雨了。 | char 16 | 2 | **1** |
| 21 (control) | 今天沈阳多云转晴最高气温二十度，明天有雨。 | char 16 | 2 | 2 (by design) |

So the doubt was real: the 16-char rule alone was not enough.

## Rule

A comma is a split point only when **both** hold:

1. the clause is at least `SOFT_MIN_CHARS` (16) characters, and
2. **more than** `SOFT_MERGE_MAX_CHARS` (20) characters of the current sentence are
   already buffered.

Everything else is unchanged:

- A sentence end (`。！？；;`) is still released immediately, whatever the buffer length.
- A 13-char single sentence is still one request (`SOFT_MIN_CHARS` untouched).
- A 19-char two-sentence reply still reaches TTS as two requests (sentence-end rule).
- The ≤10-char hold (`SHORT_REPLY_CHARS`, task-30) is untouched.
- `speech_pieces` (verbatim briefings) deliberately stays `clauses=False`.

Both boundaries are pinned in tests: exactly 20 buffered chars are held whole, 21 split
at the comma; a 40-char reply still streams its first 16-char clause early.

## Why N = 20 (not 24)

- The measured defect band is exactly 17–20 chars (a 16-char clause plus a ≤4-char
  tail). 20 is the smallest window that covers the whole reported band, so it removes
  the audible seam without deferring first audio more than necessary.
- N = 24 would cover a further 21–24 chars, but there is no evidence those replies
  seam in practice, while it would push the first-clause release 8 buffered characters
  past the old threshold on real long answers (vs 5 at N = 20). First audio on real
  long answers is the thing the comma rule exists to protect; widening the window
  beyond the reported band trades a measured fix for an unmeasured one.
- 21–25 char single-sentence comma replies still split. That residual is the same one
  task-32 recorded; its stated next step is a sentence-boundary heuristic, not a
  larger magic number.

Lead review suggested 20; the measurements above agree.

## First-audio cost

- A comma sentence longer than 20 chars now releases its first clause when the 21st
  character is buffered, i.e. at most **5 characters later** than the old 16-char
  threshold. Against the measured ~343 ms first-audio lead (task-32), that is roughly
  0.1–0.2 s at typical model streaming rates, and it applies only to the "long answer
  with an early comma" shape.
- Answers whose first sentence ends with a sentence end are unaffected: that path is
  still immediate and is the dominant first-audio case. Replies of ≤20 chars, which is
  every reply in the reported defect band, now gain a whole vendor request of latency
  back (and lose the seam).
- The 45-char hard cap (`MAX_SENTENCE_CHARS`) is unchanged.

## Files and evidence

Changed:

- `src/rui/session/turn.py`: `SOFT_MERGE_MAX_CHARS = 20`, the clause condition
  `len(buffer) - start > SOFT_MERGE_MAX_CHARS`, comments and `split_sentences`
  docstring.
- `tests/test_turn.py`: boundary assertions (20 held whole, 21 splits, `clauses=False`
  still holds a 21-char clause); parametrized 17/18/19/20 one-synth cases, each run in
  both shapes — whole reply in one delta and the live stream (head at the comma, then
  the tail); a 40-char reply still streaming its first clause early; the old 16-char
  clause case updated to the new 20-char window.
- `tests/test_speech_text.py`: the clause-gap case is now explicit that its 21-char
  reply is above the merge window (still 2 requests, no added sentence gap).

Verification (real output):

```
$ uv run pytest -q -o addopts="" tests/test_speech_text.py tests/test_turn.py
46 passed, 1 skipped in 0.76s

$ uv run ruff check src/rui/session/turn.py tests/test_speech_text.py tests/test_turn.py
All checks passed!
```

Red -> green mutation (window reverted to the old 16, then restored):

```
$ sed -i '' 's/^SOFT_MERGE_MAX_CHARS = 20$/SOFT_MERGE_MAX_CHARS = 16/' src/rui/session/turn.py
$ uv run pytest -q -o addopts="" tests/test_turn.py \
    -k "seventeen_to_twenty or long_comma or sentences_split"
5 failed, 1 passed, 34 deselected in 0.26s   # the 4 length cases + the boundary unit test
# restored to 20 -> 46 passed, 1 skipped
```

The TTS-call count is the number of `_speak_sentence` -> `provider.synthesize()` calls
(`FakeTts.texts`), not a guess from the split pieces. The same four lengths were also
replayed as a **one-character-per-delta** stream (the shape closest to a real model
stream): 17/18/19/20 -> 1 call each, 40-char control -> 2 calls with the first clause
released once 21 characters were buffered.

Note: the 17-char fixture has no closing full stop. A reply whose comma is the 16th
character and still ends with `。` cannot be 17 chars (16-char clause + 1 char tail);
the fixture is the real "16-char clause plus one late character" stream shape.

## Not covered

- **No real-speaker listening test.** The seam is inferred from the call count plus the
  vendor's documented head/tail silence, not from an ear on the LX06. A listening pass
  is still the final arbiter; if a 17–20 char reply still seams on device, the next
  suspect is the vendor's own pause at the comma, not this rule.
- 21–25 char single-sentence comma replies still split (by design, first audio wins).
- No change to `SENTENCE_GAP_MS` / `gap_after`: a comma split still adds no 350 ms gap.
- Only the streaming answer path; channel/console turns and verbatim briefings are out
  of scope.
