# Architecture

> **Status note (2026-10):** this document was written during the v1 build-up. The layer
> diagram, process model and IPC contract below are still authoritative; some of the
> "not built / to build" notes in the diagram and the debt table in §6 are historical.
> Music, Home Assistant over MCP, generic MCP, playback arbitration and the console have
> since landed. For the current shipped state see the README and
> [`docs/remaining-work.md`](remaining-work.md).

**One rule decides every question in this document: adding a speaker must touch one new
module and one registry line — nothing else.** Where the code does not meet that yet, it is
listed in §6 as a debt with a name, not left as an intention.

## 0. Invariants

```
1. Only the audio plane touches the microphone or the speaker. The control plane never
   writes audio to a device.
2. Only the coordinator decides who is allowed to make sound. The plane executes a claim
   and refuses a second writer; it does not weigh priorities itself.
3. A tool never touches a device. To speak it asks the coordinator for playback; to use
   hardware it goes through DeviceServices, and a device that cannot do it says so.
4. The device dials in. The control plane does not connect out — except through the shell
   channel the device's own client exposes (today: reading mysoftvol, nothing else).
5. A layer knows the layer below only through an interface. No layer above L6 knows a
   vendor name, an ALSA numid, or a model string.
```

## 1. Layers

```
┌────────────────────────────────────────────────────────────────────────────┐
│ L7 users + hardware          LX06 · the next speaker · any box that can SSH  │
└───────────────────────────────┬────────────────────────────────────────────┘
                     the device dials ws://<us>:<port>
┌───────────────────────────────▼────────────────────────────────────────────┐
│ L6 device = the socket      device/{contract,registry}.py                   │
│                             plugs/<plug>/ (image: /opt/rui-plugins/<plug>/)   │
│   adapter   speaks the wire protocol, converts frames, declares a profile    │
│   contract  DeviceInfo · Capability · DeviceServices (optional per device)   │
│   registry  name -> manifest runtime.entrypoint; an unknown name becomes     │
│             AdapterSet.errors, not a crash, so the console can explain it   │
│   ⚠ no vendor name above plugins/ and their manifests                       │
└───────────────────────────────┬────────────────────────────────────────────┘
          frames (N channels + reference) ↑        ↓ PCM · device commands
┌───────────────────────────────▼────────────────────────────────────────────┐
│ L5 audio plane (own process)  audio/{process,decimate,frontend,aec,kws,vad,  │
│                                      ack,chime,protocol}.py                  │
│   decimate → 90 Hz high-pass → array mix → AEC → KWS (+probe) → VAD          │
│   playback: one owner at a time, paced with a 200/350 ms buffer              │
│   ⚠ the only holder of the microphone and the speaker                       │
│   ⚠ every DSP stage is gated on a Capability, never on an assumption         │
└───────────────────────────────┬────────────────────────────────────────────┘
     up:   wake · utterance · metrics · vendor_signal · shell_result
     down: speak(claim) · play_file(claim) · release · stop · shell · query
┌───────────────────────────────▼────────────────────────────────────────────┐
│ L4 coordinator = the layer that decides     web/api/app.py                  │
│   ① turn boundaries        session/turn.py                                   │
│   ② session state          session/state.py  (SessionStateMachine)           │
│   ③ playback arbitration   Output (claim/release)  (P0–P3 owner)             │
│   ④ rules and policy       session/{rules,idle,filler,vendor,text,prompts}   │
└──────────────┬────────────────────────────────────────────┬─────────────────┘
     ToolSpec  │                                            │ config/log/trace
┌──────────────▼───────────────┐             ┌──────────────▼─────────────────┐
│ L3 application  tools/       │             │ L2 capabilities  providers/     │
│  info (weather/news/search)  │             │  chain: per-stage fallback       │
│  reminders (tools+scheduler) │             │  openai_compat · xfyun_tts      │
│  music (asks for a playback  │             │  sherpa_asr · siliconflow        │
│         for a playback claim)│             │                                  │
│  HA via MCP                  │             │                                  │
└──────────────────────────────┘             └──────────────────────────────────┘
┌────────────────────────────────────────────────────────────────────────────┐
│ L1 infrastructure   config/{schema,store} · secrets · log_setup · trace ·    │
│                     doctor · Dockerfile · deploy/ · scripts/                 │
└────────────────────────────────────────────────────────────────────────────┘
```

The console (`web/`) is a view over L4, not a layer of its own: it may call only the REST
API, and every write goes through the same validation the CLI uses.

## 2. Process model

```
rui serve                      rui audio (child)
├─ FastAPI: REST + console           ├─ adapter: the inbound ws (mic + speaker)
├─ supervisor: spawns/restarts       ├─ DSP: decimate → HP → mix → AEC → KWS → VAD
├─ coordinator: turn + session       ├─ playback: speak / play_file / cue (one owner)
│               + Output (claims)    └─ events up: wake, utterance, metrics, shell
└─ timers: idle 1 s, reminders 1 s
```

The split exists for one reason: the echo canceller is a tight per-frame loop, and a slow
HTTP request must not be able to starve it. It is also the seam where a native
implementation could replace the Python one without anything above noticing.

The seam is `audio/protocol.py`: a header (`type`, `length`) followed by either a Pydantic
control message or a PCM block carrying its own sequence number and timestamp. One rule, no
delimiter scanning; a Rust or C++ plane only has to reproduce that file and the models.

Layout is per frame, not per config: `AudioFrame` says its own channels, rate and
`reference_channels`, so an LX06 (48 kHz / 8 ch / 6 mic + 2 ref) and a future ESP32
(16 kHz / 1 ch / no reference) share the same plane code. `audio.capture` is what a device
is asked for, not what the plane bakes in.

## 3. Playback arbitration

Four things want the speaker at the same time: the wake cue, a reply, a reminder that came
due, and a song. Without one owner they interleave their PCM into a single device player —
measured 2026-10-22: a reply spoken over 嘻唰唰 came out stuttering.

```
P0 cue        wake acknowledgement, ~320 ms; preempts everything, releases on its own
P1 dialogue   one answer; preempts P2/P3; cancelled by a barge
P2 reminder   a due reminder; waits for P1, preempts P3
P3 music      a file/song; paused (never killed) by anything above, resumed after
```

Forty lines, not two hundred: an owner string and a priority number in the plane, a
claim/release wrapper in the coordinator. No lease, no TTL, no queue — the only queue the
project actually needs is "a reminder waits for the turn to end", and that is one `await`.

Rules that come from measured failures:

- Preempting P3 pauses and remembers the position, rewound by 0.55 s (`PLAY_LAG_SECONDS`,
  the audio already in the device). Resuming at the last *sent* sample skips that much.
- Preempting P1 cancels and flushes, and tells the control plane to abandon the answer it
  is still synthesising — otherwise the flushed queue refills and talks over the new turn.
- `StopAudio` is an explicit stop: release and do not resume.
- The arbiter's state is a metric (`owner`, `lead_ms`, `paused_sources`), not a log line.

## 4. Adding a speaker — the goal, as a checklist

| # | What the new device supplies | Where | Touches core? |
|---|---|---|---|
| 1 | `adapter.py`: dial-in protocol, frame conversion, `DeviceInfo` + `Capability` | `device/<name>/` | no |
| 2 | `DeviceServices`: shell/volume/led if it has them; omit what it does not | `device/<name>/` | no |
| 3 | one registry entry: `"name": "module:Class"` | `device/registry.py` | one line |
| 4 | `devices[].audio` overrides if its layout differs from the defaults | config file | no |

Acceptance test: a second fake adapter (4 microphones, no reference channel, no playback)
must run from its own module plus one registry line, with **zero changes under `src/`
anywhere else**. That test is the architecture; if it goes red, a layer still knows a vendor.

## 5. Data flow of one turn

1. Device streams N interleaved channels, 32-bit; the client applies the per-channel shift.
2. Adapter converts to floats and emits `AudioFrame`s with their own layout.
3. The plane decimates to the voice rate, high-passes, mixes the array.
4. AEC subtracts the reference (skipped when the device has no reference channel).
5. KWS fires → the coordinator opens a session; the pre-wake ring is replayed into the VAD.
6. VAD closes the utterance → ASR → LLM (+ tools) → streamed sentences → TTS.
7. Playback claims P1 and pushes PCM with the pacer; a barge releases it and pauses music.

## 6. Debts that break the checklist today

| Debt | Where | Why it breaks the goal |
|---|---|---|
| ~~`AdapterName = Literal["xiaoai","mock"]`~~ (fixed in 0.3: adapter is a free string) | `config/schema.py` |—|
| ~~`registry._build()` if/elif~~ (fixed in 0.6 M2: image plugs register from their manifest's `runtime.entrypoint`) | `device/registry.py` |—|
| `audio.capture/voice/playback` are global | `config/schema.py` | two devices with different layouts cannot coexist; a 4-mic speaker changes the global shape |
| DSP assumes 8 ch + 2 reference + playback | `audio/process.py` | a device without a reference channel cannot use the AEC path safely |
| `amixer -c 0 cget numid=177` in the API | `web/api/app.py` | a vendor detail above the device layer |
| `SessionStateMachine` is never constructed | `session/state.py` | the real state is three scattered places; the diagram would lie |
| no playback owner | `audio/process.py` | the same interleaving bug returns with music, reminders and cues |

## 7. Non-goals

- **Not a general voice-assistant framework.** One household, one speaker at a time for
  playback. Multi-room is a different product.
- **Not cloud-first.** Recognition runs locally; the network is a fallback, not the path.
- **No wake-word training.** One model, one keyword file; a different word is a different
  keyword line, not a new pipeline.
- **No streaming ASR re-architecture for v1.** The VAD cuts the utterance and it is decoded
  in one shot; 51–90 ms measured is good enough to answer.
