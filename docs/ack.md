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

_Filled in after the parameter scan; the raw jsonl and the summariser command above are
the record. Numbers from the first sweep, which had a second bug mixed in, are in the
commit message of the follow-up fix rather than quoted here as if they were final._
