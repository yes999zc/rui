# Audio pipeline

The part that decides whether the product feels good. Everything else is plumbing.

## The frame

16 kHz, 20 ms frames (320 samples). Eight interleaved 32-bit channels:
`ch0..ch5` are microphones, `ch6` and `ch7` are the same playback-reference
loopback.

## The one number that matters

Mic full scale is `2**23`, reference full scale is `2**28` — a factor of 32, five
binary bits. A client that converts every channel to 16-bit with one `>>8` clips:

- 52% of reference samples at volume 110
- 78% at volume 170

A clipped reference cannot cancel echo. Measurements on the same algorithm and
the same music: **5.5 dB before, 23.0 dB after** at volume 110; **4.4 dB → 25.2
dB** at volume 170. Per-channel scaling (`mic >>8`, `ref >>13`) is the whole fix.

If you change one thing in this repo, keep this one.

## Echo canceller

MDF, 4096 taps, 16 kHz, one filter per mic channel. The reference arrives 2.4 ms
before the microphones hear it, so alignment is a fixed delay, not a search.

RTF budget: the canceller must use less than half a core at six channels, or a
4-core NAS cannot host more than one speaker. `house-ear doctor` measures this
with the real tap count and reports device capacity.

## Barge-in

1. Playback runs through a queue of ~200 ms.
2. KWS stays armed during playback; AEC keeps the mic clean.
3. On wake: fade out over 120 ms, flush the queue, transition to `listening`.
4. Target: fully silent within 300 ms of the user starting to speak.

Without a reference channel this cannot work. Say so in the UI.

## Known traps

- **Bit-depth** above. Silent failure mode: sounds fine, fails only with music.
- **Clock drift.** Device clock and server clock diverge; the jitter buffer must
  adapt or the queue grows until latency is unusable.
- **Sample rate on the playback path.** If the device only accepts 16 kHz, music
  through the voice path sounds like a phone call. Measure before designing.
