# The LX06 wire protocol

What our adapter depends on, and what it deliberately ignores. Everything below was
read off the client that actually runs on the speaker (open-xiaoai fork) and the
server it was written for — not inferred from the vendor docs.

## Shape

```
speaker client  ──ws://<server>:<listen_port>/?token=***──▶  house-ear
                ◀── {"Request": {...start_recording...}} ────
                ──▶ {"tag":"record","bytes":[...]} (int16, 20 ms) ──
                ◀── {"tag":"play","bytes":[...]}   (int16, mono) ───
                ◀── {"Request": {"command":"stop_play"}} ───────
```

| frame | direction | type | payload |
|---|---|---|---|
| `Request` | server → client | TEXT | `{"id","command","payload"}` |
| `Response` | client → server | TEXT | `{"id","code","message"}` |
| `Event` | client → server | TEXT | `{"id","event","data"}` (key presses) |
| stream | either | BINARY | `{"id","tag","bytes","data"}` |

`bytes` is a JSON array of integers in the stock client; the fork also accepts base64
on the `play` path. Our decoder takes both, so an unmodified client still works.

## Who applies the bit-depth shift

**The speaker does.** `start_recording` carries `mic_shift` and `ref_shift`; the client
converts S32 capture to int16 per channel and only then sends it. We never see 32-bit
samples on the wire, so these two numbers are *request parameters*, not server-side
math. They live in `audio.capture.mic_shift` / `ref_shift`.

Two pairs are in use, and they are the same trick with different headroom:

| pair | what it means |
|---|---|
| `8 / 13` | mics: 2²³ → int16 full scale. reference: 2²⁸ → 2¹⁵, i.e. the same level. The minimum that stops the reference clipping. |
| `12 / 16` | four bits of headroom on top (mics leave at −24 dBFS), reference ends up 6 dB hotter than the mics. What the old production deployment actually ran, with 24–30 dB ERLE. |

house-ear defaults to `8 / 13` because it is the level-matched, no-clipping baseline and
its arithmetic is easy to reason about; S3 measures both and picks on hardware. Changing
them is a config edit, not a code change.

## Transport cost (why the capture rate is a decision)

The client serialises raw bytes as a JSON array of integers — roughly 4 bytes per sample
on the wire. At 48 kHz × 8 ch × 20 ms that is 30,720 bytes of PCM behind a ~120 kB JSON
message, fifty times a second: about 6 MB/s. At 16 kHz × 8 ch it is 1 MB/s.

The speaker's ALSA device runs at 48 kHz, but `sample_rate` in `start_recording` asks the
plug layer for whatever we want, and 16 kHz × 8 ch is what the old deployment ran in
production. AEC, KWS and VAD all run at 16 kHz anyway, so `config.example.yaml` asks for
16 kHz and `capture.rate: 48000` stays available for analysis runs.

## Lifecycle and failure modes

- The adapter is **inbound**: `open()` starts a websocket server on
  `devices[].listen_port` (default 4400) and waits. Nothing to connect to means the
  audio plane simply yields no frames.
- The device's client holds one connection. A second connection replaces the first
  (code 1012) instead of being silently ignored.
- A wrong or missing `?token=` is refused with code 1008, and an empty
  `HOUSE_EAR_DEVICE_TOKEN` makes `open()` fail closed rather than accept everyone.
- A disconnect never kills the plane: `frames()` keeps waiting for the client to return,
  which it does on its own every few seconds after a wifi blip.
- Malformed stream frames are counted (`bad_frames`), not raised.

## Not implemented yet

Key events (`Event`), LED ring, volume and shell access (`run_shell`) arrive with the
device-shell work in S5. The adapter advertises only `mic_array`, `reference` and
`playback` today, and the console shows exactly that.
