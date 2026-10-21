# house-ear

Self-hosted voice endpoint for SSH-enabled smart speakers.

`house-ear` turns a speaker you already own into a private voice assistant:
wake word, speech, reasoning, and playback all run on your own box, and tools
(Home Assistant, MCP servers, local commands) are exposed through one registry.

> Status: **M1 in progress (S0 + S1 done)** — control plane, contracts, latency
> ruler and the separate audio-plane process are in place; the LX06 adapter lands in
> S2. v1 supports exactly one speaker model: Xiaomi 小爱音箱 Pro (LX06).

## What works today

- `house-ear doctor` — hardware probe: measured AEC real-time factor, configured
  capture/playback layouts, and a capacity number that names its assumption
- `house-ear serve` — control-plane API (health, config, devices, tools, doctor,
  traces) plus the six-page console
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
- Audio plane as a child process behind framed Unix-socket IPC (JSON control +
  mono 16 kHz PCM), stateful 48 kHz -> 16 kHz FIR decimation, bounded ring with a
  drop counter, and `/api/v1/audio` for live numbers
- `scripts/measure_plane.py` — runs the plane for N seconds and prints frames,
  drops, jitter percentiles, CPU and rtf; every M1 claim comes from it

## Measured so far

| what | number | how |
|---|---|---|
| AEC load, 6 channels | rtf 0.004 (0.4% of one core) | `house-ear doctor` |
| audio plane, 5 min, mock 48 kHz/8 ch | 14234 frames in, 0 dropped, rtf 0.0055 | `scripts/measure_plane.py` |
| frame interval | p50 21.2 ms, p99 21.3 ms | same run |
| config v1 -> v2 migration | loads, file untouched | `tests/test_config.py` |

## Quick start

```bash
uv sync
uv run house-ear doctor
uv run house-ear serve
```

Then open `http://127.0.0.1:8080/`.

## M1 acceptance

1. Fresh deploy to first spoken reply in **≤ 30 minutes**.
2. Music at 80% volume: **≥ 19 of 20** wake attempts succeed.
3. Interrupt: fully silent within **300 ms** of the user starting to speak.
4. Audio plane **rtf ≤ 0.5** — total CPU over audio duration, including
   decimation, AEC, KWS and VAD, averaged over a 10-minute run with music playing.

## Design

- [docs/architecture.md](docs/architecture.md) — layers, process model, IPC seam
- [docs/adapter-contract.md](docs/adapter-contract.md) — how a new speaker is supported
- [docs/audio-pipeline.md](docs/audio-pipeline.md) — three rates, AEC, bit-depth scaling, barge-in
- [docs/deploy.md](docs/deploy.md) — NAS deployment
- [docs/adr/](docs/adr/) — the decisions and what they cost

## License

MIT
