# Wake acknowledgement (S5a)

What the speaker does between "你好小瑞" and the answer. Three modes, one contract:
`ack.mode: none | earcon | tts`.

## Why a tone is the default

On an LX06 the microphone is muted for about 1.2 s while the speaker plays. A spoken
acknowledgement therefore eats the beginning of the request it is supposed to introduce.
The old deployment learned this in production and settled on a 160 ms tone; house-ear
copies that decision, and `ack.max_ms` (default 600) is what keeps `mode: tts` from
reintroducing the problem.

The modes, and what they cost:

| mode | what plays | microphone | measured cost |
|---|---|---|---|
| `none` | nothing | open | the user hears nothing for ~3.5 s (turn p50) |
| `earcon` | 160 ms two-note cue, synthesised in the plane | open | none: the cue is on the AEC reference, and the gate is held only for the cue itself |
| `tts` | `ack.text`, pre-rendered at startup | muted ~1.2 s + cue length | the first words spoken during that window are gone |

## The three things that have to happen in order

```
KWS hit ──► pick the cue ──► gate.arm() ──► replay wake_preroll_ms ──► gate.hold(cue) ──► play cue
                                                │                        │
                                     the person's own words      live audio only
```

1. **The cue is chosen first**, because its length decides how long the gate stays shut.
2. **The pre-wake ring is replayed before the hold.** Those blocks were captured while the
   microphone was still open: they are the person, not the speaker. Replaying after the
   hold (or re-arming the gate for the replay) throws them away, which is the same as not
   replaying at all.
3. **The cue is fire-and-forget.** Awaiting it blocks the frame consumer for the cue's
   length; on the old build that cost ~0.7 s of microphone audio per wake.

`gate.hold()` is spent in *audio* time (samples), not wall time, so a harness that feeds
frames faster than real time measures the same number.

## Where the first word actually goes

The streaming KWS does not report a hit at the end of the wake phrase. Measured with the
real model, real VAD and real ASR (p50 over 20 rounds): **1.09–1.22 s from the start of
the wake clip**, i.e. 60–190 ms after the phrase ends. With `wake_preroll_ms: 0` and the
request spoken in the same breath, the transcriber receives `天气怎么样？` / `气怎么样？`
instead of `今天天气怎么样？` — 0 of 20 rounds intact. With `wake_preroll_ms: 500`, the
replay puts the missing audio back in front of the gate.

Two further losses are *not* the acknowledgement's fault, and both were found while
measuring it:

- **The gate's own onset buffer** (`vad_preroll_ms`, default 200) is smaller than Silero's
  detection lag, so the first syllable is sometimes clipped even when the audio arrives:
  `今天天气怎么样` → `天天气怎么样`. Measured separately; see the table below.
- **Digital silence at fixture edges** made the first sweep look better than reality: with
  500 ms of silence between wake word and request, the 300 ms skip eats silence. Real
  speech does not pause there. `measure_turn_pipeline.py --tight` trims the edges so the
  wake word and the request are one breath, which is the case the bug is about.

## How it is measured

```bash
# one configuration: 20 rounds, real KWS/VAD/ASR, fake speaker, no room needed
uv run python scripts/measure_turn_pipeline.py \
    --config ~/.hermes/cache/scratch/house-ear-s4/real-device.yaml \
    --command ~/.hermes/cache/scratch/house-ear-s4/commands/01-weather.wav \
    --rounds 20 --gap-ms 0 --speed 1.0 --tight --asr-only \
    --ack-mode earcon --preroll-ms 500 --gate-preroll-ms 350 \
    --out ~/.hermes/cache/scratch/house-ear-s5a/earcon-gap0.jsonl

# then aggregate a whole sweep directory
uv run python scripts/summarize_ack.py ~/.hermes/cache/scratch/house-ear-s5a
```

`--asr-only` stops the turn after transcription: first-word loss is an ASR question and
running the LLM and TTS for every round only makes the sweep slow. `--speed 1.0` matters:
at 3× the cue plays in real time while frames arrive faster, and the ordering stops
matching the room.

## Results

Eight configurations, 20 rounds each, all through the real KWS, turn gate and ASR with the
wake word and the request spoken **in one breath** (`--tight --gap-ms 0` unless stated).
"Intact" means the transcript is exactly `今天天气怎么样？`.

| config | preroll | gap | ack | intact | what was heard |
|---|---|---|---|---|---|
| base | 0 ms | 0 | none | **0/20** | `天气怎么样？` x12, `天天气怎么样？` x8 |
| none | 500 ms | 0 | none | **20/20** | complete |
| earcon | 500 ms | 0 | tone | **20/20** | complete |
| earcon | 500 ms | 150 ms | tone | **20/20** | complete |
| earcon | 500 ms | 300 ms | tone | **20/20** | complete |
| earcon | 500 ms | 500 ms | tone | **20/20** | complete |
| tts | 500 ms | 0 | spoken | **0/20** | nothing at all: 0 utterances |

Three conclusions, in the order they cost the most:

1. **The pre-wake replay is not optional.** Without it the first syllable is gone in every
   round, and it is gone from the *audio*, not from the model's imagination: 20/20 rounds
   lost 今天 or 今天+气.
2. **The 160 ms tone costs nothing.** Gap 0/150/300/500 all came back complete: the cue and
   its hold do not touch the request.
3. **A spoken cue loses the whole request** when the person answers immediately - not part
   of it, all of it: 0 utterances in 20 rounds. The LX06 mutes its microphone for ~1.2 s
   while the speaker plays, so the 550 ms cue plus that window swallows a 1.15 s request
   whole. This is why `mode: earcon` is the default and why the cap exists at all; the old
   project recorded the same thing (`HANDOFF.md:1214`: "唤醒侧 preroll 只回放 0.5 秒，补不全").

The KWS lag is a constant, not a distribution: **p50 1.09-1.62 s measured from the start of
the wake clip** (the spread is the gap, not the model). Two independent causes then clip the
first syllable, and both were fixed:

- the replay length (`wake_preroll_ms`, 500 ms) - fixes the audio that never reached the gate;
- the gate's own onset buffer (`vad_preroll_ms`, 200 → 350 ms) - fixes the syllable that the
  VAD reported too late to include. Measured separately: 200 ms lost 今天 in 5/5 rounds,
  350 ms and 500 ms lost it in 0/5.

## What the measurement itself got wrong first

Worth recording, because both mistakes made the numbers look better than reality:

- the fixtures carry 200-400 ms of digital silence at each edge, so with a 500 ms gap the
  300 ms post-wake skip ate silence instead of speech. `--tight` trims the edges.
- the harness stopped reading the device socket as soon as a turn landed; the plane then
  blocked in `writer.drain()` and the run died ~10 rounds in with `keepalive ping timeout`,
  which reads like a crash. A real client always drains (`_consume` in the harness).
