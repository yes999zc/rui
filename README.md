# house-ear

Self-hosted voice endpoint for SSH-enabled smart speakers.

`house-ear` turns a speaker you already own into a private voice assistant:
wake word, speech, reasoning, and playback all run on your own box, and tools
(Home Assistant, MCP servers, local commands) are exposed through one registry.

> Status: **v1 in progress** — the whole turn (wake → ASR → LLM → speech → playback),
> music (Navidrome), Home Assistant over MCP, reminders, memory, the console and the
> one-command Docker deploy are in. What is left is cold-install acceptance on the NAS
> (see [`docs/ROADMAP.md`](docs/ROADMAP.md)). v1 supports exactly one speaker model:
> Xiaomi 小爱音箱 Pro (LX06).

> **Picking this up mid-flight?** Read [`docs/HANDOFF.md`](docs/HANDOFF.md) first: current
> state, the parameters inherited from the old project, known problems and the next steps.

## What works today

- `house-ear doctor` — hardware probe: measured AEC real-time factor, configured
  capture/playback layouts, and a capacity number that names its assumption
- `house-ear serve` — control-plane API (health, config, devices, tools, doctor,
  traces) plus the bilingual admin console (four-tier config center)
- Config v2 — capture/voice/playback rates and layouts are explicit, atomic writes,
  timestamped backups, secrets only as environment-variable names, v1 files migrate
  on load
- Device adapter contract (outbound and inbound transports) + a mock adapter that
  mirrors the LX06's 48 kHz / 8 ch / 6-mic + 2-reference layout
- Provider chains per stage: one catalog of named providers, ordered fallback per
  stage, validated at load
- Wake arbitration (first frame wins), session state machine, tool registry with
  namespacing and a 50-tool cap
- `house_ear.trace` — per-turn latency marks (`first_response_ms`,
  `silence_after_ms`) so every M1 step is measured, not estimated
- A whole turn: utterance from the audio plane → ASR → streaming LLM → speech per clause →
  PCM pushed to the speaker, with the provider per stage reported by `GET /api/v1/turns`
- Providers: local sherpa-onnx SenseVoice (ASR, first in the chain), DeepSeek (`deepseek-flash`,
  thinking off), 讯飞 websocket TTS (`x4_lingxiaowan_boy`) with SiliconFlow as the fallback for
  both ASR and TTS; every stage is an ordered chain and a missing key is a fallback, not a crash
- Audio plane as a child process behind framed Unix-socket IPC (JSON control +
  mono 16 kHz PCM), stateful 48 kHz -> 16 kHz FIR decimation, bounded ring with a
  drop counter, and `/api/v1/audio` for live numbers
- LX06 adapter — tier 2, inbound websocket: the speaker's own client dials us, we ask
  it for 8 ch with the reference pair and receive int16 at the rate we request;
  documented in [docs/xiaoai-protocol.md](docs/xiaoai-protocol.md)
- `scripts/measure_plane.py` — runs the plane for N seconds and prints frames,
  drops, jitter percentiles, CPU and rtf; every M1 claim comes from it
- `scripts/measure_capture.py` — records raw frames through the adapter (per-channel
  RMS, frame intervals, WAV out); `--fake` synthesizes a speaker for desk checks
- echo cancellation in the plane — speexdsp 4096 taps per mic, best-channel selection,
  never-worse and divergence guards, ERLE reported only while the speaker is playing
- `house-ear debug-play` — plays a local wav at the speaker (CLI only) so AEC and
  wake-word tests have something real to hear
- `scripts/bench_aec.py` — offline ERLE and CPU for the real chain
- `scripts/measure_erle.py` — hardware ERLE: plays a wav while recording the array on the
  same connection, then computes ERLE offline from the capture
- wake word — sherpa-onnx KWS on the echo-cancelled stream, proven parameters (chunk 8,
  boost 2.0, threshold 0.20), `Wake` events over the IPC seam
- `scripts/measure_wake.py` — plays music, listens, prints every wake with its timestamp,
  ERLE and reference state; `--save-voice` keeps the exact stream KWS saw
- Music from your own library — Navidrome (Subsonic) source, queue with natural
  end-of-track advance, pause/resume/next/prev/stop, search and taste tools; token+salt
  auth and a persistent queue/history. Enable with `music.enabled: true`; see
  [docs/deploy.md](docs/deploy.md)
- Home Assistant — MCP client plus a six-tool whitelist (state, call service, areas,
  search, batch read, system status), entity names injected without state, hard refusal
  for lock/alarm/valve, and degradation instead of a crash when HA is down
- Memory and a context switch — curated entries extracted in the background, confirmed
  from the console, injected into the prompt; "关闭上下文/打开上下文" works by voice
- Console — panels for overview, devices, conversations, logs, music, memory and
  settings, with form/table/log blocks declared by the server
- Docker one-command deploy — `setup` writes the production template and wake keywords,
  `fetch-models` brings ASR + VAD + the wake model, and the image ships one `config`/`data`/
  `models` volume set, a health check, log rotation and a documented rollback; a watchdog
  restarts the audio plane if it crashes. See
  [docs/deploy.md](docs/deploy.md) and [docs/runbook.md](docs/runbook.md)

## Measured so far

| what | number | how |
|---|---|---|
| AEC load, 6 channels | rtf 0.004 (0.4% of one core) | `house-ear doctor` |
| audio plane, 5 min, mock 48 kHz/8 ch | 14234 frames in, 0 dropped, rtf 0.0055 | `scripts/measure_plane.py` |
| frame interval | p50 21.2 ms, p99 21.3 ms | same run |
| LX06 adapter, synthetic speaker, 48 kHz/8 ch | 417 frames, 960 samples/ch/frame, ref ch6/7 at −5.4 dBFS, mic0 −17.7 dBFS, 0 clipped | `scripts/measure_capture.py --fake` |
| same, frame interval | p50 24.4 ms, p99 24.7 ms (includes the fake client's JSON encode) | same run |
| LX06 hardware, 16 kHz/8 ch, 60 s | 1494 frames, 640 samples/ch, mic −50 dBFS (peaks −20), ref silent, 0 clipped, interval p50 39.6/p99 84.5 ms | `scripts/measure_capture.py` |
| LX06 hardware, 48 kHz/8 ch, 10 s | 246 frames, 1920 samples/ch, mic −45 dBFS, ref silent, 0 clipped | same |
| AEC, 6 filters, 4096 taps, synthetic 50 ms room | ERLE 30.7 dB converged, rtf 0.066 | `scripts/bench_aec.py` |
| AEC, 6 filters, 4096 taps, synthetic 200 ms room | ERLE 30.5 dB converged, rtf 0.066 | same |
| AEC on the LX06, 30 s of music, ref −25.5 dBFS | ERLE 24.7-25.1 dB (second half), residual −39 dBFS, 0 clipped | `scripts/measure_erle.py` |
| AEC convergence | ~10 s of playback; first windows only 15-17 dB | same |
| wake word on the LX06 with music playing | 18 wakes in 75 s (spoken ~every 4 s), 0 dropped frames | `scripts/measure_wake.py` |
| audio plane rtf with AEC + KWS | 0.0935 at 16 kHz / 8 ch | same run |
| local ASR, SenseVoice int8, 1.75 s clip | Mac 51-54 ms (rtf 0.03); NAS N100 210 ms at 2 threads (rtf 0.12) | `scripts/smoke_turn.py`, bench on the NAS |
| cloud ASR, same clip | 384 / 1204 / 2464 ms, one 20 s timeout | same |
| full turn, SiliconFlow TTS | first response p50 8713 ms, max 23541 ms (20 rounds) | `scripts/measure_turn_pipeline.py` |
| full turn, 讯飞 TTS + deepseek-flash | first response p50 3563 ms, max 4197 ms, 20/20 replies | same |
| turn breakdown (讯飞 run) | ASR 463 ms, LLM first token 1065 ms, TTS first byte 1591 ms (p50) | same |
| config v1 -> v2 migration | loads, file untouched | `tests/test_config.py` |

## Quick start

On the NAS (production), one command — details, config and models in
[docs/deploy.md](docs/deploy.md):

```bash
curl -fsSL https://raw.githubusercontent.com/yes999zc/house-ear/main/deploy/install.sh | sh
```

Then open `http://<nas-ip>:8080/` and run `docker compose exec house-ear house-ear doctor`.

On a development machine:

```bash
uv sync
uv run house-ear doctor
uv run house-ear serve
```

Then open `http://127.0.0.1:8080/`.

Operations (start/stop, logs, changing config, upgrading, rolling back, common failures)
are in [docs/runbook.md](docs/runbook.md).

## M1 acceptance

1. Fresh deploy to first spoken reply in **≤ 30 minutes**.
2. Music at 80% volume: **≥ 19 of 20** wake attempts succeed.
3. Interrupt: fully silent within **300 ms** of the user starting to speak.
4. Audio plane **rtf ≤ 0.5** — total CPU over audio duration, including
   decimation, AEC, KWS and VAD, averaged over a 10-minute run with music playing.

## Design

- [docs/architecture.md](docs/architecture.md) — layers, process model, IPC seam
- [docs/adapter-contract.md](docs/adapter-contract.md) — how a new speaker is supported
- [docs/xiaoai-protocol.md](docs/xiaoai-protocol.md) — the LX06 wire protocol we depend on
- [docs/audio-pipeline.md](docs/audio-pipeline.md) — three rates, AEC, bit-depth scaling, barge-in
- [docs/deploy.md](docs/deploy.md) — NAS deployment, music/HA config, volumes, image tags
- [docs/runbook.md](docs/runbook.md) — operations: start/stop, logs, config, upgrade, rollback, failures
- [docs/music.md](docs/music.md) — the music layer and its sources
- [docs/adr/](docs/adr/) — the decisions and what they cost

## License

MIT
