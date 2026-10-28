# 0007 — Hardware audio facts belong to the model manifest

- Status: accepted (0.8.0 P0, 2026-10-28)
- Context: task-26; production incident documented in `docs/HANDOFF.md` (0.8.0 P0)
- Supersedes the layer order documented for 0.6 in `docs/roadmap-0.6.md` and
  `docs/design/console-ia-v2.md` §5.1 (the manifest was the bottom layer there)

## Problem

The global `audio` block ships as a template measured on the reference LX06
(16 kHz capture / 8 channels / 32 bit / shifts 12/16, 16 kHz mono playback,
`ack.echo_ignore_ms: 1200`). Because the merge applied a manifest field only when the
global field was *not explicit*, every value the template states silently replaced
the model's own declaration. Consequences measured in production:

- a newly added Dot 2 (`devices[].audio` empty) asked the bridge for **16 kHz
  playback**; the bridge requires 48 kHz and raised on every block, so the room lit
  its LED and stayed silent;
- the same device used the reference speaker's **1200 ms** cue window instead of its
  declared **2500 ms** tail (first effective read `ack=1200`, `plug_resolved` false
  until a restart);
- a fresh Dot 2 would also have inherited the LX06's 8 interleaved channels, 32-bit
  samples and 12/16-bit shifts, none of which its firmware produces.

The manifest parse was fine. The ownership rule was wrong.

## Decision

`audio_defaults` states the model's **hardware facts**, so it wins over an explicit
global value. `devices[].audio` still wins over everything (an operator's deliberate
per-room declaration, e.g. the permanent dot2-b override).

Merge order per field:

```
core default  <  global config  <  model manifest  <  devices[].audio
```

Two documented exceptions (both reported, never silent):

1. **`capture.rate` is negotiated.** The manifest states the model's native rate; a
   client may resample to the wire the operator asks for to save WiFi bandwidth. The
   living LX06 declares 48 kHz natively and production asks the open-xiaoai client
   for 16 kHz. An explicit global/device `capture.rate` wins and a
   `kind: "negotiated"` conflict says so.
2. **A declared capture layout is a bundle.** If a manifest declares any of the
   layout *dimensions* `channels / mic_channels / ref_channels`, the layout fields
   it omits (`bits`, `mic_shift`, `ref_shift`, or a missing dimension) take the
   **neutral core** value, not the global value. `bits`/`mic_shift`/`ref_shift`
   alone do not trigger this: a bit depth without channel dimensions says nothing
   about how many channels arrive, so the global block still supplies them. Without
   this, the Dot 2 manifest (`channels: 1`, `bits: 16`, shifts omitted) would
   inherit the LX06's 12/16 shifts - values that only make sense for a 32-bit
   capture stream. Those neutral-filled fields are the model's fact: the provenance
   API attributes them to `device-manifest` (with the neutral value in
   `layers[path].manifest`), never to `global-config`, and `conflicts` names the
   same winner.

`ack_echo_ignore_ms` is an acoustic fact of the model and is resolved like a hardware
field: manifest over an explicit global window, device override over both.

## Why not the alternatives

- **Make `lx06.yaml` declare 16 kHz** (`rui-xiaoai-bridge`, option (a)): the 48 kHz
  number is the speaker's native ALSA rate and the client genuinely resamples; the
  16 kHz request is a deployment choice (bandwidth), not a hardware fact. Editing the
  downstream manifest would also freeze one operator's bandwidth decision into the
  model, and requires a cross-repository change.
- **Global explicit always wins** (the 0.6 behaviour): the production incident above.
- **Refuse the whole config on conflict**: too blunt for the living room, whose only
  disagreement is the negotiated capture rate. Conflicts are visible (WARN in the
  service log, `conflicts` in `GET /api/v2/devices/{id}/audio`, `audio_conflict` in
  device warnings) and the effective value is deterministic.

## Visibility contract

Every disagreement produces an `AudioConflict`:

```json
{"path": "playback.rate", "global": 16000, "manifest": 48000,
 "effective": 48000, "winner": "device-manifest", "kind": "hardware",
 "message": "audio.playback.rate: global 16000 vs model 48000; using 48000 (model hardware)"}
```

- The plane logs each unique conflict once at the `rui.config.schema` logger and logs
  the manifest resolution (`plugin/model/source`) or the reason it is missing.
- `GET /api/v2/devices/{id}/audio` adds `conflicts` and `manifest_error` (additive;
  see C-002 §7.15).
- A device whose `params.plug_id` is set but whose manifest cannot be read is not
  silently neutral: the plane warns, the API reports `plug_resolved: false` plus the
  reason, and the fields the config never stated report `unresolved`.

## Cold start (0.8.0 P0b)

The plane resolves the manifest in `AudioPlane.__init__`, which runs after
`build_plane()` has loaded the downstream adapters. Co-located manifests (the
documented `RUI_ADAPTER_PLUGINS` layout: `plugin.yaml` next to the adapter module) are
now registered into the process catalog by the adapter loader, so the **first**
effective read sees the same manifest the adapter was gated against. Previously the
catalog only scanned the image and `<data_dir>/plugs`, so a co-located manifest backed
a working adapter while the audio resolver reported `plug_unresolved` - ack 1200 /
playback 16 kHz until a restart.

If `<data_dir>` is populated only after the plane started, the plane re-resolves after
the adapter probe and again on the first cue-window read, so the first wake uses the
model's values. A changed *capture* layout still needs a plane restart, because the
adapter was built (and asked the client) with the old layout; the log says so.

## Tests

`tests/test_audio_facts_ownership.py` pins the matrix: new Dot 2 with empty audio
(48000/16000/1 ch/ack 2500), living LX06 unchanged (16 kHz/8 ch, ack 1200), a device
override unchanged, mixed three-device fleet, conflict visibility, unknown plug
rejection through v2 create, and both cold-start paths (co-located manifest before the
first read; data_dir appearing after start).
