# Turn quality (S5b)

What the answer sounds like: one question repeated, an answer that says more than 「不知道」,
and the two ways a hosted model goes wrong when there is nothing behind it — inventing
numbers, and repeating itself until it sounds broken.

Everything here was measured through the real ASR and the real DeepSeek endpoint. TTS is off
in these runs on purpose (`--tts` exists, but the numbers below are about text): the
transcript is identical every round, so the only thing that can move is the prompt, the
history window and the reply shaping.

```bash
uv run python scripts/measure_reply_quality.py \
    --config /tmp/house-ear-scratch/house-ear-s4/real-device.yaml \
    --command /tmp/house-ear-scratch/house-ear-s4/commands/01-weather.wav \
    --rounds 20 --out /tmp/house-ear-scratch/house-ear-s5b/final-01-weather.jsonl
```

The harness keeps **one** `TurnRunner` for all rounds. That is the bug being measured: a
fresh runner per round hides in-context imitation completely.

## What was copied from the old build, and what was not

| old build | house-ear | why |
|---|---|---|
| `prompts.DEFAULT_SYSTEM_PROMPT` (persona, information-first length policy, three bands, "the acknowledgement has already been played, do not greet again") | `session/prompts.py`, verbatim wording | its HANDOFF: "改数字没用，必须同时改措辞"; the bands alone changed nothing |
| `clean_reply` (drop DSML tool-call markup) | `session/text.py` | a model with no `tools=` writes the call as text; the old build read it aloud for 14 s |
| `strip_wake` (drop the wake phrase the pre-roll dragged in) | `session/text.py` | our 500 ms replay is bigger than the old 500 ms one, so the risk is higher, not lower |
| `conversation.history_rounds` = 6 | `session.history_rounds` = 6 | house-ear had 4 hard-coded |
| `assistant.*` length bands (120 / 300 / 900) | `assistant.*` | same numbers, same assistant |
| tool rules ("use weather/news/web_search, never claim you did something you did not") | **not copied** — no tool layer yet (S5e) | telling the model to call tools it does not have is how markup gets spoken |
| the text-level ack-echo dedupe (`_is_ack_echo`) | **not ported yet** | the default cue is a 160 ms tone, and the spoken cue was measured unusable (0/20, `docs/ack.md`); it comes back with `mode: tts` |
| an anti-repeat guard | **not built** | the old build never had one; its answer to repetition was the history window plus wording |

## Numbers

Same four questions, 20 rounds each, one `TurnRunner`, real ASR + real DeepSeek
(`deepseek-flash`, temp 0.4, thinking off). "Distinct" = how many of the 20 replies differ;
"run" = the longest stretch of byte-identical replies in a row.

| question | before: distinct / run | after: distinct / run | numbers invented |
|---|---|---|---|
| 今天天气怎么样 | 11/20 (55%) / 10 | **17/20 (85%) / 1** | 0/20 → 0/20 |
| 明天会下雨吗 | 9/20 (45%) / 12 | **19/20 (95%) / 1** | 0/20 → 0/20 |
| 现在几点了 | 8/20 (40%) / 12 | **18/20 (90%) / 1** | 0/20 → 0/20 |
| 你都能做什么 | 13/20 (65%) / 8 | **17/20 (85%) / 2** | 2/20 → 0/20 |

"Before" is the old 85-character prompt and a 4-round window. Two things it did that the
table does not show:

- **It stopped answering, and complained.** 你都能做什么, rounds 4-12: 「你是不是在试我」、
  「我不答了，你换个问题」、「再问我就装没听见」. The old build recorded the same drift
  (`HANDOFF.md` §6: 「再问我要罢工了哦」).
- **It advertised devices it cannot drive.** 「我能帮你开关灯、调空调、设闹钟和提醒、放音乐」
  with zero tools wired - a claim about capability, which is worse than a wrong number.

Both are gone in the "after" column: no complaint, and the honest answer is
「现在能陪你聊天、答问题，家里的设备还没接上」.

## Why the repetition happened (and why "less history" was the wrong fix)

Three experiments, in the order they were run:

1. **Window 4 → 6, same prompt**: 55% → no reliable change. The window was not the cause
   on its own.
2. **`--history-turns 0`**: 6/20 distinct, 15 identical in a row — *worse*. It also exposed a
   real bug: `self.history[-2 * 0:]` is `self.history[0:]`, i.e. **the whole history**, not
   nothing. So that run was "unbounded history", and unbounded history is what locks the
   model onto its own words. Fixed, `history_rounds: 0` now really means no history, and
   there is a test for it.
3. **The same question, with the earlier copy of it removed from the window**: 85-95%.
   The mechanism is in-context imitation — the model reads its own previous answers to this
   exact question and continues the pattern. Take those out and it answers fresh, and at
   temp 0.4 it is genuinely non-deterministic (measured directly: 10 independent calls to
   the same prompt gave 9 distinct answers).

So `TurnRunner.history_messages()` drops earlier copies of the question being asked now and
keeps everything else. It is one rule with one reason: history is context, not a script to
continue.

## What this does *not* fix

- **It cannot know the time, the weather or the news.** Those are tools (`server/tools/` in
  the old build: `weather` is open-meteo, `news` and `web_search` are HTTP): house-ear has
  none wired until S5e. Until then the honest answer is 「查不到」, and the prompt says so
  explicitly. The old build also appended 「现在是X年X月X日 星期X X点X分」 to the system
  prompt, which is how it answered 现在几点 without a tool; whether house-ear does the same
  or answers time from a tool is a S5e decision.
- **A canonical question still converges.** 你都能做什么 ends up with 2 identical rounds out
  of 20: there are only so many ways to say "I can chat, the devices are not connected".
  That is a limit of the answer, not of the prompt.
- **The length bands are unexercised.** Nothing a tool-less assistant can say needs 900
  characters; the 120 band is what the everyday answers obey now. `reply_max_chars_search`
  is inert until a search tool exists.
- **Turn time grows with the answer.** 你都能做什么 p50 5.7 s to a full stream (weather 3.6 s).
  The first sentence reaches TTS much earlier; that is the number `docs/latency.md` tracks.
