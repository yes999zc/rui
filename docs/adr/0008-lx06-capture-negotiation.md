# 0008 — The LX06 manifest's 48 kHz capture rate stays a negotiated value

- Status: accepted (0.8.1, 2026-10-28) — documentation record; no code or manifest change
- Context: task-26 (`3d40266`), ADR 0007 (`docs/adr/0007-audio-facts-ownership.md`),
  production incident recorded in `docs/HANDOFF.md` (0.8.0 P0)
- Scope: `lx06` only. The `dot2` / RS03QR manifest is unaffected (it declares
  `capture.rate: 16000`, which is also the production request)

## Problem

ADR 0007 makes the model manifest authoritative for hardware facts, with one
documented exception: `capture.rate` is *negotiated*, because the rate is the
operator's wire cost decision, not only a property of the box. The `lx06` manifest
then looks contradictory:

| Layer | `capture.rate` | `channels` | `mic/ref` | `bits` |
|---|---|---|---|---|
| `lx06.yaml` `audio_defaults` (native hardware) | **48000** | 8 | 6 / 2 | 32 |
| Production config (`config.example.yaml` / `template.yaml`; the deployed config asks the same) | **16000** | 8 | 6 / 2 | 32 (client converts to 16 bit on the wire) |
| Effective on the living LX06 (`devices[]` empty) | **16000** | 8 | 6 / 2 | 32 |

The living speaker works at 16 kHz. Read literally, "the manifest wins for hardware
facts" would make it 48 kHz — the exact class of change that broke the Dot 2 in 0.8.0.
The question this ADR settles is whether `lx06.yaml` should be changed to `rate: 16000`
to remove the apparent disagreement.

## Decision

**Do not change `lx06.yaml`. `capture.rate` remains the negotiated exception, and the
production 16 kHz request stays explicit.** Nothing about the living room changes.

Reasons, in order:

1. **16 kHz is deliberate, not an accident.** The LX06 client JSON-encodes raw PCM as
   an int array: 48 kHz × 8 ch × 32 bit is roughly **6 MB/s** over wifi versus roughly
   **1 MB/s** at 16 kHz (`config.example.yaml`'s `audio.capture.rate` comment). AEC,
   KWS and VAD all run at 16 kHz, so the extra bandwidth buys nothing on the live path.
2. **The manifest is describing the hardware, and the hardware really is 48 kHz.**
   `lx06.yaml` documents the S32_LE / 8 ch / 6 mic + 2 ref / 48 kHz ALSA layout the
   client captures before it resamples. Writing 16000 into the manifest would replace
   a hardware fact with a deployment preference and make the file a second copy of the
   production template — the coupling ADR 0007 exists to remove.
3. **Changing the manifest would change every LX06 deployment, not just ours.** For
   layout fields the manifest wins over an explicit global value. A manifest edit is a
   release-level behaviour change for every LX06 user, so it needs 9哥's approval and
   an independent probe — a documentation task may not smuggle it in.
4. **The disagreement is already visible, not silent.** Resolution reports a
   `capture.rate` conflict of kind `negotiated`, `winner: "global-config"`,
   `effective: 16000` through `effective_audio()` (`src/rui/config/schema.py`) and the
   console's `/api/v2/devices/<id>/audio` `conflicts[]`. The plane log line
   `audio facts (startup): device=... plug=... model=... source=...` proves which
   manifest resolved. Nothing is guessed.

Behaviour of the merge for this device, for the record:

```
core default 16000  <  global 16000 (explicit)  <  lx06.yaml 48000 (negotiated)  <  devices[].audio
effective capture.rate = 16000   (winner: global-config, kind: negotiated)
effective channels/mic/ref/bits = 8 / 6 / 2 / 32   (winner: device-manifest)
```

## What an independent device probe would settle

Only a probe on a real LX06 can decide whether `lx06.yaml` should ever state 16000.
It must answer, with the device on the production firmware and the production client:

1. **What the client actually puts on the wire.** Request 16000 and 48000 in turn and
   measure the frame byte length and arrival cadence at the WS boundary. If the client
   honours 16000 (expected, because production works), the manifest's 48000 is a true
   native fact and the exception is correct. If the client ignores the request and
   always sends 48 kHz, the production 16 kHz is a fiction and the config is wrong.
2. **Whether 48 kHz is usable at all.** Frame cadence within the plug contract's
   100 ms budget, no dropped frames, audible result unchanged, and the 12/16-bit
   shifts still correct for S32_LE input.
3. **Real cost at 48 kHz.** Wifi throughput and CPU on the speaker (the ~6 MB/s figure
   is arithmetic, not a measurement), plus any client-side buffering change.
4. **Echo tail at 48 kHz.** Whether `ack_echo_ignore_ms: 1200` still describes the
   measured tail once the wire rate changes.
5. **Layout confirmation.** 8 interleaved channels with ch0–ch5 mics and ch6/ch7
   reference at 48 kHz — the manifest's claim, checked against `probe()` output and a
   captured frame.

Decision rule: if (1) shows the client honours 16000, keep everything as is and close
this ADR permanently. If it shows the client always sends 48 kHz, the fix is a
production config change (and a separate decision about the manifest), not a docs
edit; it needs 9哥's approval, a release, and a re-run of the four-device acceptance.

## Cold-start observability (0.8.1 conclusion)

The 0.8.0 P0 also exposed a *timing* bug: the Dot 2's effective read used the
reference speaker's 1200 ms cue window and 16 kHz playback because the manifest was
resolved only after a restart (`plug_resolved` false on the first read). task-26
closed it in three places, and the tree now proves the order rather than assuming it:

1. **Gated manifests are registered before the plane is built.**
   `build_plane()` calls `load_adapter_plugins()` *before* constructing `AudioPlane`
   (`src/rui/audio/process.py`), and the loader's `_register_manifest()`
   (`src/rui/device/loader.py`) calls `register_adapter_plugin()`
   (`src/rui/plugins/resolve.py`). `PlugCatalog` then gives that `adapter` entry
   priority over an older `builtin`/`user` manifest of the same id, so the hardware
   facts resolve on the first read, not after a restart.
2. **The first probe re-resolves before Ready.** `AudioPlane.handle()` runs
   `safe_probe()`, then `_refresh_audio_defaults(reason="probe")`, then the wire
   self-check — all before the room is reported up.
3. **A late ack read retries a bounded number of times.** When a manifest is expected
   but not yet resolved, the `ack_echo_ignore_ms` property calls
   `_refresh_audio_defaults(reason="ack-read")` at most **3** times
   (`_audio_refresh_attempts < 3`); a late resolution covers a `data_dir` populated
   while the plane is already running. The cue window is the field that exposed the
   original bug (1200 vs the Dot 2's 2500), which is why it has its own retry.

Evidence available in the log (one line per plane start/refresh, never per turn):

- resolved: `audio facts (startup): device=<id> plug=<plug> model=<model> source=<source>` (INFO)
- unresolved: `audio facts (startup): device=<id> has no usable model manifest (<reason>); hardware facts fall back to the global/core values - playback/ack/capture may be wrong` (WARNING)
- resolved late: `audio facts (probe|ack-read): device=<id> resolved plug=... model=... (late manifest); playback=<n> Hz ack=<n> ms` plus `; restart the plane if the capture layout changed` when the capture layout moved (WARNING)

### Residual

- **A capture layout that resolves late still needs a plane restart.** The warning says
  so explicitly. Playback, ack and voice can move under a live plane; the capture
  stream is already open with a fixed frame length, so a late `channels`/`bits` change
  is not applied mid-stream. This is deliberate — silently reinterpreting an open
  stream would be worse than asking for a restart.
- **Follow-up (not done in this docs task):** the plane log prints *which* manifest
  resolved but not the negotiated pair itself (effective 16000 / native 48000). That
  pair is currently only in the console's `conflicts[]` (`kind: "negotiated"`,
  `winner: "global-config"`). One INFO line at startup would make the ADR's central
  claim greppable in a log. It is a code change and is intentionally left as a
  follow-up rather than smuggled into a documentation commit.

## Consequences

- No code, manifest, test or config change; production keeps 0.7.9 and the living room
  keeps 16 kHz / 8 ch.
- A future reader who sees `48000` in `lx06.yaml` and `16000` in the production config finds
  this ADR instead of "fixing" the manifest.
- If the probe above is ever run and contradicts point (1), this ADR is
  superseded by that evidence — it is a decision record, not a permanent claim.
