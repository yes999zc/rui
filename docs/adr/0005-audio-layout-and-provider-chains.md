# ADR 0005 — rates and channels are explicit; providers are a catalog plus a chain

Status: accepted (2026-10-21), supersedes the flat `audio` block of config v1

## Context

Config v1 had `audio.sample_rate: 16000`, `audio.frame_ms` and `audio.aec.channels: 6`.
Then the hardware was measured: an LX06 captures `S32_LE`, 8 channels at 48 kHz with
two reference channels, and plays `S16_LE`, 2 channels at 48 kHz. A single `sample_rate`
cannot describe that, and "6 channels" was AEC's filter count wearing the name of the
capture layout.

At the same time `providers.asr.fallback` was a string documented as "name of another
provider block", but no named provider blocks existed — the field could not be filled
in correctly by anyone.

## Decision

1. `audio` splits into `capture` (rate, channels, mic/ref split, bits, both shifts),
   `voice` (rate, frame, KWS/VAD thresholds) and `playback` (rate, channels, queue,
   fade, silence target). Every number in it is a measured fact or a target, not a
   round number someone liked.
2. `providers` becomes `catalog: {name: settings}` plus one ordered `chain` per stage.
   Index 0 is primary; the rest are fallbacks in order. Validation rejects a chain
   naming a provider that is not in the catalog, so a typo fails at load, not mid-turn.
3. `AudioFrame` carries `reference_channels` instead of an `is_reference` flag, because
   the reference travels inside the same interleaved block as the microphones.
4. `play()` takes `sample_rate` and `channels`.
5. Config `version: 2`. A v1 file is migrated in memory on load and never rewritten
   implicitly; the on-disk file is the rollback.

## Consequences

- Anything that reads `audio.sample_rate` must move to `audio.voice.rate` (DSP) or
  `audio.capture.rate` (wire). The migration makes old files load, not silently pass.
- A mis-typed fallback name is a startup error with the offending name in the message.
- The console shows three paths with three rates, which is what the device actually does.
