# house-ear

Self-hosted voice endpoint for SSH-enabled smart speakers.

`house-ear` turns a speaker you already own into a private voice assistant:
wake word, speech, reasoning, and playback all run on your own box, and tools
(Home Assistant, MCP servers, local commands) are exposed through one registry.

> Status: **M0 skeleton**. The device adapter, audio pipeline and console land in M1/M2.

## What works today

- `house-ear doctor` — hardware/network probe with an audio-DSP load estimate
- `house-ear serve` — control-plane API (health, config, devices, tools, doctor)
- Config loading with schema validation, atomic writes and backups
- Device adapter contract + mock adapter (so the abstraction is tested without hardware)
- Wake arbitration (first frame wins), session state machine, tool registry with namespacing

## Quick start

```bash
uv sync
uv run house-ear doctor
uv run house-ear serve
```

Then open `http://127.0.0.1:8080/`.

## Design

- [docs/architecture.md](docs/architecture.md) — layers and boundaries
- [docs/adapter-contract.md](docs/adapter-contract.md) — how a new speaker is supported
- [docs/audio-pipeline.md](docs/audio-pipeline.md) — AEC, reference signal, bit-depth scaling
- [docs/deploy.md](docs/deploy.md) — NAS deployment

## License

MIT
