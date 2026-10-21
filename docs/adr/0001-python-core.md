# ADR 0001 — Python core, process-isolated audio plane

Status: accepted (2026-10-21)

## Context

Candidates: Python with a separate audio process, Go control plane with a Python
audio sidecar, Rust throughout.

The workload is not compute-bound. One turn is roughly 1200 ms, of which network
calls (ASR 300–600 ms, LLM 300–800 ms, TTS 200–600 ms) dominate. Local DSP is a
few milliseconds per 20 ms frame, and the heavy code is already C (speexdsp,
sherpa-onnx).

## Decision

Python 3.12 + FastAPI + Pydantic for the control plane; the audio plane runs as a
separate process behind a narrow interface.

## Consequences

- One toolchain, lowest contribution barrier, existing DSP bindings all usable.
- GIL is not a risk because the audio plane is a separate process, not a thread.
- If profiling ever shows the audio plane is the bottleneck, replace that one
  process (Rust/C++) without touching adapters, tools or the console.
- Docker image is ~300 MB, acceptable for a NAS service.
