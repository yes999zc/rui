# Architecture

Six layers. Each one talks to the next through an interface, never through globals.

```
L6 console + REST/WS        web/ (Vue 3 + TS)
L5 orchestration            providers/ (asr, tts, llm)
L4 capabilities             tools/ (local + MCP + Home Assistant)
L3 session                  session/ (state machine, wake arbitration)
L2 audio plane              audio/ (decimation, AEC, KWS, VAD, playback)
L1 devices                  device/ (adapter contract, one adapter per speaker)
```

## Why the split

- **L1 is the only place that knows a vendor exists.** A new speaker adds one
  adapter; nothing above it changes.
- **L2 runs in its own process.** The echo canceller is a tight per-frame loop;
  keeping it out of the HTTP process means a slow request cannot cause dropped
  audio frames. This is also the seam where a future Rust/C++ replacement drops
  in without touching the rest.
- **L3 is a state machine, not a pile of flags.** Barge-in is a transition from
  `speaking` to `interrupted` to `listening`; illegal transitions raise.
- **L4 has one tool shape.** A local function, an MCP tool and a Home Assistant
  entity all become `ToolSpec` with a namespaced name and a permission level.
- **L5 keeps vendors swappable per stage.** ASR, TTS and LLM can come from three
  different providers; nothing assumes one vendor.

## Process model

```
house-ear serve
├─ control plane (FastAPI, asyncio)
│   ├─ REST: /health /api/v1/*  (config, devices, tools, doctor, traces, audio)
│   ├─ static console
│   └─ supervisor  ──────────────┐  spawns, greets, restarts
└─ audio plane (child process, asyncio)
    ├─ device transport (adapter; inbound socket for an LX06)
    ├─ decimation 48 kHz -> 16 kHz (stateful FIR per channel)
    ├─ bounded ring (200 ms) -> voice-rate PCM out
    └─ S3: AEC -> KWS -> VAD      S5: playback queue
                                   ↑
        unix socket: type|len|payload frames, JSON control + mono PCM
```

The seam is `audio/protocol.py`: a header (`type`, `length`) followed by either a
Pydantic control message or a PCM block carrying its own sequence number and
timestamp. One rule, no delimiter scanning, and a Rust or C++ plane only has to
reproduce that file plus the message models.

Why not in-process threads: the GIL is not the problem (numpy and speexdsp release
it), the *blast radius* is. A stalled HTTP handler or a body-parsing bug in the
control plane must not be able to pause the audio loop; a separate process makes
that a fact instead of a hope. The supervisor also gets to restart the plane without
dropping the console.

Layout is per frame, not per config: `AudioFrame` says its own channels, rate and
`reference_channels`, so an LX06 (48 kHz / 8 ch / 6 mic + 2 ref) and a future ESP32
(16 kHz / 1 ch / no reference) share the same plane code. `audio.capture` in config
is what the doctor expects, not something the plane bakes in.

## Data flow of one turn

1. Device streams 8 interleaved channels, 32-bit.
2. Adapter scales per channel (mics `>>8`, reference `>>13`) and emits frames.
3. The plane decimates the speech channel(s) to 16 kHz.
4. AEC subtracts the reference from each mic channel (S3).
5. KWS fires → L3 acquires the turn (first frame wins across devices).
6. VAD closes the utterance → ASR → LLM (+ tools) → TTS.
7. Playback pushes PCM with a fade-in; a new wake starts a fade-out in ≤300 ms.

## Non-goals

Cloud service, firmware flashing, general-purpose assistant platform.
