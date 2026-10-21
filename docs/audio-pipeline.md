# Audio pipeline

The part that decides whether the product feels good. Everything else is plumbing.

## Three rates, three jobs

| path | rate | layout | why |
|---|---|---|---|
| capture | 48000 | 8 ch, 32 bit | what the LX06 actually sends |
| voice | 16000 | 1 ch per filter | AEC / KWS / VAD; 4096 taps = 256 ms of tail |
| playback | 48000 | 2 ch, 16 bit | what the LX06 amplifies |

Measured, not assumed: `cat /proc/asound/card*/pcm*/sub0/hw_params` on the device
`S32_LE` 8 ch 48000 Hz while capturing, `S16_LE` 2 ch 48000 Hz while playing.
Music never squeezes through the 16 kHz path — downsample only the copy that goes
to the voice pipeline.

## The frame

`voice.frame_ms` (20 ms) frames at `voice.rate` (320 samples). Capture arrives
wider and faster: 20 ms at 48 kHz is 960 samples per channel, so the adapter
decimates 3:1 before anything else touches the samples.

Eight interleaved channels: `ch0..ch5` microphones, `ch6` and `ch7` the same
playback-reference loopback. Reference travels **inside** the frame, which is why
`AudioFrame` carries `reference_channels` instead of an `is_reference` flag.

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

speexdsp MDF, 4096 taps (256 ms of tail at 16 kHz), one filter per microphone, fed
128-sample speex frames from arbitrary block sizes. The reference arrives 2.4 ms before
the microphones hear it, so alignment is a fixed delay, not a search.

Same geometry the vendor front end uses (`speex_echo_state_init(0x80, 0x1000)`), which
is why the measured behaviour matches the old deployment's 24-30 dB ERLE instead of
being a filter we would have to tune from scratch.

Offline benchmark (`scripts/bench_aec.py`, synthetic 50 ms and 200 ms rooms, six
microphones, 20 s of music-like reference):

| room tail | ERLE whole run | ERLE converged | CPU rtf (6 filters) |
|---|---|---|---|
| 50 ms | 22.9 dB | 30.7 dB | 0.066 |
| 200 ms | 22.0 dB | 30.5 dB | 0.066 |

The chain picks the microphone that survives best (hysteresis 6 dB) and refuses to
return a residual louder than its input; a diverged filter resets instead of leaking
garbage into KWS. While the reference is silent the canceller is bypassed - there is no
echo to cancel, and adapting a filter to silence only drifts it.

`house-ear doctor` still prices AEC at rtf 0.004 because it measures the filter
arithmetic alone; the 0.066 above is what running the real library costs.

## Barge-in

1. Playback runs through a queue of ~200 ms (`playback.queue_ms`).
2. KWS stays armed during playback; AEC keeps the mic clean.
3. On wake: fade out over `playback.fade_out_ms`, flush the queue **and whatever
   the device has already buffered**, transition to `listening`.
4. Target: fully silent within `playback.target_silence_ms` (300 ms) of the user
   starting to speak.

Step 3 is where the risk lives: flushing our own queue is microseconds, flushing
the speaker's ALSA buffer needs a control message the device client must honour.
Measure it (wake → mic energy floor) before believing the 300 ms number.

Without a reference channel this cannot work. Say so in the UI.

## Latency marks

`house_ear.trace` stamps the boundaries that cost time: `wake`,
`end_of_speech`, `asr_first`, `asr_done`, `llm_first`, `llm_done`, `tts_first`,
`play_start`, `barge_in`, `silence`. It computes `first_response_ms`
(wake → play_start) and `silence_after_ms` (barge_in → silence). A stage without a
number is a stage nobody has measured.

## Known traps

- **Bit-depth** above. Silent failure mode: sounds fine, fails only with music.
- **Clock drift.** Device clock and server clock diverge; the jitter buffer must
  adapt or the queue grows until latency is unusable.
- **Decimation is not free.** 8 ch × 48 kHz × 32 bit ≈ 1.5 MB/s over the socket;
  one bad WiFi hop shows up as a gap in `frames()`, not as an error.
