# house-ear

Self-hosted voice endpoint for SSH-enabled smart speakers.

`house-ear` turns a speaker you already own into a private voice assistant:
wake word, speech, reasoning, and playback all run on your own box, and tools
(Home Assistant, MCP servers, local commands) are exposed through one registry.

> Status: **M1 in progress (step S0 done)** — control plane, contracts and the
> latency ruler are in place; the audio plane and the LX06 adapter land in S1/S2.
> v1 supports exactly one speaker model: Xiaomi 小爱音箱 Pro (LX06).

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

- [docs/architecture.md](docs/architecture.md) — layers and boundaries
- [docs/adapter-contract.md](docs/adapter-contract.md) — how a new speaker is supported
- [docs/audio-pipeline.md](docs/audio-pipeline.md) — three rates, AEC, bit-depth scaling, barge-in
- [docs/deploy.md](docs/deploy.md) — NAS deployment
- [docs/adr/](docs/adr/) — the decisions and what they cost

## License

MIT
