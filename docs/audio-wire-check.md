# Audio wire-format self-check (task-24 / R3)

The 0.8.0 production failure: a Dot 2 room resolved to **16 kHz playback** while the
bridge accepts **48 kHz mono only**. Every `PLAY_PCM` block raised `ValueError`, the
speak loop wrote one INFO line per chunk, and the console kept showing the room as
healthy. task-26 fixed the field ownership; this task adds the runtime gate that makes
the same mismatch impossible to ship silently again.

## What is compared

`rui.audio.probe.check_wire_format()` is a pure comparison with two sides:

| Direction | Device side | Why |
| --- | --- | --- |
| capture | `probe()` → `DeviceInfo` (`sample_rate`, `channels`, `mic_channels`, `ref_channels`) | Only for **inbound** devices: the device dials us and is told the wire, so a different reported layout means frames will be interpreted at the wrong rate/channel count. |
| playback | model manifest `audio_defaults.playback` (`rate`, `channels`) | `DeviceInfo` has no playback field; the plug's manifest is the hardware fact the adapter enforces (Dot 2 raises for anything but 48 kHz mono). |

An outbound adapter owns its capture path and delivers self-describing frames; the
plane's decimator already uses `probe()`, so a capture mismatch there is advisory and
does not refuse a working room. Playback is checked for every transport.

A side that is absent is left unchecked rather than invented:

* a failed/absent probe leaves capture unchecked in the console;
* an old plug with no `audio_defaults.playback` leaves playback unchecked.

## Refusal (plane)

`AudioPlane.handle()` runs `_verify_wire_format()` after the manifest re-resolve and
**before `Ready`**. A mismatch raises:

```
audio_config_mismatch: playback.rate: configured 16000, device 48000
(source: model manifest dot2 (dot2-echomuse)); fix: set
devices[].audio.playback.rate to 48000 ...
```

The supervisor therefore reports `audio plane refused: audio_config_mismatch: ...` and
the room never appears up. The log has exactly one ERROR line per start.

## Console status and warning

`GET /api/v2/devices/{id}` computes the same comparison independently (so a stopped
plane still explains itself) and:

* sets `status = "error"`, `ready = false`,
  `status_reason.code = "audio_config_mismatch"`;
* the UI translates that code to **配置与设备不符** / "Configuration does not match the
  device";
* adds an `audio_wire_mismatch` warning whose `message` carries both values, the
  source and the `fix:` sentence, and whose `hint` repeats the fixes;
* exposes `audio_wire = {ok, checked, message, mismatches[]}` on the device detail.

When the API cannot recompute the comparison (the probe failed, or no manifest states
the playback wire), the plane's own refusal still decides the status: the supervisor's
`error` / `refusal` text is matched for the shared `WIRE_MISMATCH_PREFIX`
(`probe.is_wire_mismatch`), so the supervisor wrappers `audio plane refused: ...`,
`audio plane degraded: ...` and `AudioPlaneError: ...` all read as
`audio_config_mismatch` instead of a generic `probe_failed`/`audio_plane_stopped`.
The supervisor keeps that refusal text across a restart, where the rebuilt metrics no
longer carry `playback_errors`; `/health` rows expose it as `refusal`.

## Adapter `play()` failures

Every PCM handover goes through `AudioPlane._adapter_play()`:

* `ValueError` (a deterministic rejection such as "the Dot 2 wire is 48000 Hz mono")
  increments `playback_errors`, records `playback_last_error`, and stops the plane
  immediately with the `audio_config_mismatch:` prefix. Continuing would raise once
  per chunk and bury the config error in log noise.
* other exceptions (the speaker went away) are counted and retried; **3 consecutive**
  failures trip the same breaker (`PLAY_ERROR_LIMIT`).
* a successful block resets the streak. The failure is logged once per streak at
  ERROR, repeats at DEBUG; never one INFO line per chunk.

Both counters travel in `Metrics` (`playback_errors`, `playback_last_error`), appear
in `/health` `audio_devices[]`, in `device.plane` on `/api/v2/devices/{id}`, and in the
v2 `PlaneCard` / health table.

## Production fix for a Dot 2

If the console says 配置与设备不符, set the playback wire explicitly for that room (the
model manifest normally already does this):

```yaml
devices:
  - id: dot2-b
    audio:
      playback: {rate: 48000, channels: 1}
```

The permanent `devices[].audio` override for `dot2-b` stays (it is an explicit hardware
declaration, not a temporary patch).

## Tests

`tests/test_audio_wire_check.py` covers:

* both values/source/fix in the pure comparison and in the refusal `Error` frame;
* a correct Dot 2 config starts and plays at 48 kHz;
* the LX06's negotiated 16 kHz capture wire is not a false positive;
* a `ValueError` is counted once, logged once, and stops the plane;
* three transient failures trip the breaker;
* `/api/v2/devices` status is `audio_config_mismatch` + the warning, and a matching
  config has neither;
* a supervisor-wrapped refusal still yields `audio_config_mismatch` when the probe
  failed and nothing can be recomputed, and the retained `refusal` works after a
  restart;
* the client keeps the plane's `Error` text and the supervisor exposes it as
  `refusal`;
* `/health` carries `playback_errors` / `playback_error`.
