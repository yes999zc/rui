# Architecture

Six layers. Each one talks to the next through an interface, never through globals.

```
L6 console + REST/WS        web/ (Vue 3 + TS)
L5 orchestration            providers/ (asr, tts, llm)
L4 capabilities             tools/ (local + MCP + Home Assistant)
L3 session                  session/ (state machine, wake arbitration)
L2 audio plane              audio/ (jitter, AEC, KWS, VAD, playback)
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
│   ├─ REST: /health /api/v1/*
│   └─ static console
└─ audio plane (separate process, per device worker)
    ├─ device transport  (long-lived socket)
    ├─ jitter buffer
    ├─ AEC -> KWS -> VAD
    └─ playback queue
```

M0 ships the control plane. M1 adds the audio-plane process and the first adapter.

## Data flow of one turn

1. Device streams 8 interleaved channels, 32-bit.
2. Adapter scales per channel (mics `>>8`, reference `>>13`) and emits frames.
3. AEC subtracts the reference from each mic channel.
4. KWS fires → L3 acquires the turn (first frame wins across devices).
5. VAD closes the utterance → ASR → LLM (+ tools) → TTS.
6. Playback pushes PCM with a fade-in; a new wake starts a fade-out in ≤300 ms.

## Non-goals

Cloud service, firmware flashing, non-SSH devices, general-purpose assistant platform.
