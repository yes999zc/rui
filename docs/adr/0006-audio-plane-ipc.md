# ADR 0006 — the audio plane is a child process behind framed IPC

Status: accepted (2026-10-21)

## Context

The plan always said "audio plane in its own process, replaceable later". That is a
promise about a seam, and a seam has to be chosen before the code on either side of
it exists. Options considered:

- A. in-process asyncio tasks — simplest, and exactly what we said we would not do:
  control-plane work (HTTP, YAML, JSON schema) then shares a loop with a 20 ms
  deadline.
- B. Unix domain socket, one frame format: `type (1) | length (4) | payload`, JSON
  control messages, raw PCM frames.
- C. loopback WebSocket — debuggable in a browser, adds an HTTP stack and a TCP port
  to a purely local hop.
- D. ZeroMQ/msgpack — best throughput and backpressure for a workload that moves
  32 KB/s.

## Decision

**B.** Details that matter:

1. **Framing is length-prefixed, never newline-delimited.** PCM contains every byte
   including `\n`; a delimiter would make the two message kinds ambiguous.
2. **PCM frames carry `seq` and `ts_ms`.** The plane is the only side that knows when
   a block was captured, and latency marks are the point of M1.
3. **The voice uplink is mono.** Which microphone, or which beamformed sum, stays a
   decision inside the plane, so S3 can add AEC and beamforming without changing the
   contract the control plane sees.
4. **Backpressure is a bounded ring with a drop counter**, not an unbounded queue.
   An unbounded queue converts a slow consumer into permanent latency, which is
   worse than a gap you can measure.
5. **Long socket paths are hashed into `/tmp`.** AF_UNIX caps at 104 bytes on macOS;
   a deep `data_dir` would otherwise fail with a bare "path too long".
6. **The plane is a child process, not a systemd unit.** One deployable container,
   two processes, one supervisor that can restart the plane without dropping the
   console.

## Consequences

- A Rust/C++ plane reimplements `audio/protocol.py` (about 20 lines of framing plus
  the message models) and nothing else in the repo changes.
- Debugging a plane in isolation works: `rui audio --config ... --device ...`
  and any client that speaks the framing.
- The seam costs one hop of copying per frame (~32 KB/s), which is noise next to the
  1.5 MB/s the device already sends.
- Windows would need a different transport (named pipes); the project targets Linux
  NAS boxes and macOS for development, so that is accepted, not solved.
