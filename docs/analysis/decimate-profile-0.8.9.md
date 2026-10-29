# Decimator profile — 0.8.9 (48 kHz capture path)

Offline CPU profile of the capture path for the 48 kHz wire, before and after the
polyphase rewrite. Task A1–A4. Reproduce with:

```bash
uv run python scripts/bench_decimate.py                  # 10 s of audio, best of 5
uv run python scripts/bench_decimate.py --seconds 30     # gate scales to 0.6 s
```

Every number below is `time.process_time()` (pure CPU, no I/O wait) on this host, printed
by that script.

## Why this was on the table

0.8.8 on the production NAS: 8-channel LX06 with `capture.rate=16000` had the device
resample 48k→16k itself and lost ~15 dB in the speech band (ASR idle). Switching the wire
to 48 kHz fixed ASR, but the living plane went from 27.9 % to ~214 % CPU (single core).
The decimator was the only stage that newly did work at 48 kHz: with a 16 kHz wire
`factor == 1` and `process` returned immediately, so it never ran.

## Setup

| | |
|---|---|
| host | Apple M2 Ultra, macOS (dev box — *not* the NAS) |
| python / numpy | 3.12.14 / 2.5.4 |
| signal | 20 ms frames, 960 samples × 8 ch, 48 kHz, float32 in [-1, 1] (what `_frame_to_voice` hands the filter) |
| run | 500 frames = 10 s of audio, `factor = 3`, fresh `Decimator` per run |
| metric | `time.process_time()` (pure CPU), 5 repeats → median / fastest |
| oracle | `Decimator._reference_decimate` = the verbatim 0.8.8 `process` body |

Run-to-run spread on this host is ~±4 % on the 8-column rows and up to ~7 % on the
7-column row: across our runs and verifier-a's re-runs on the same host the three head-line
rows landed at old 0.0934–0.0986 s, new 8 ch 0.0255–0.0268 s and new 7 ch
0.0333–0.0359 s. The block below is one full run; judge a fresh run against that band,
not against a single number.

## Result

```
bench: 500 frames x 960 samples x 8 ch @ 48000 Hz (10.00 s of audio), factor=3

implementation                         median s     best s  median %core  best %core
old full-rate (8 ch)                     0.0965     0.0935          1.0%        0.9%
new polyphase (8 ch)                     0.0263     0.0260          0.3%        0.3%
new polyphase (7 ch = production)        0.0352     0.0350          0.4%        0.4%

speedup (median, 8 ch): 3.66x | 7 ch vs old 8 ch: 2.74x
gate: new median 0.0263 s (8 ch) / 0.0352 s (7 ch) <= 0.2000 s (0.20 s per 10 s of audio, 10.0 s run) -> PASS
```

"7 ch = production": `_capture_frames` now sizes the filter for the columns
`_frame_to_voice` actually reads (6 mics + the single AEC reference) instead of all 8 wire
columns. `%core` is CPU seconds / audio seconds: the whole decimator is ~1 % of one core
on this host before, ~0.3 % after. **The 0.2 s gate is met by a factor of 7.6 (8 ch) /
5.7 (7 ch); the gate scales with `--seconds` (30 s → 0.6 s).**

### Why it got faster

* old: for each frame, `sliding_window_view(ext, 48)` over **all** 960 input samples,
  then `tensordot` → 960 windows × 48 taps × 8 ch = **368,640 MAC/frame**, and it
  materialises a `(960, 8, 48)` float32 product — **1.47 MB written + read per frame,
  ~737 MB per 10 s**.
* new: taps split by 3, one `matmul` per phase over the samples that become output:
  320 outputs × 48 taps × 8 ch = **122,880 MAC/frame**, no full-rate product. The phase
  windows are strided *views*; nothing of size `(n, ch, taps)` is ever allocated.

That is a 3× MAC reduction and no 1.47 MB/frame intermediate; measured 3.7×, the extra
comes from the memory traffic the old path paid on top of the multiplies.

## Per-stage cost of the core-side capture path

The second table the script prints: 10 s of 8-channel frames pushed through the same
stages `_frame_to_voice` runs, first with a 48 kHz wire (factor 3) and then a 16 kHz wire
(factor 1). `best` of 5, pure CPU.

```
--- core-side capture path per stage, 10.00 s of audio, best of 5 (pure CPU) ---
stage                                           48k wire (f3)  16k wire (f1)
bytes -> int16 (frombuffer)                            0.0002         0.0002
int16 -> float32, 7 of 8 columns                       0.0029         0.0013
Decimator.process                                      0.0345         0.0001
clip + tobytes                                         0.0015         0.0016
voice-rate ring copies (16 kHz mono)                   0.0003         0.0003
(reference) int16 -> float32, 8 columns                0.0019         0.0009
AEC (speex, 6 mic)                                     0.4072         0.4001
sum (excludes the reference row)                       0.4466         0.4035
```

What the table says:

* Everything after `_frame_to_voice` — `clip + tobytes`, the preroll deque
  (`_keep_preroll`, `process.py:1455`), the follow-up tail (`:1457`) and the capture→control
  PCM FIFO `self.ring` (pushed at `:1462`; drained by `_send_audio` at `:2676` and written
  to the control socket as `VOICE_PCM` at `:2681`; its capacity comes from
  `audio.playback.queue_ms`, but its consumer is the control plane, not playback) —
  consumes the **16 kHz mono** voice PCM (640 B per 20 ms frame), so its cost does not
  depend on the capture rate: 0.0015–0.0016 s and 0.0003 s in both columns.
* The frame rate is 50 fps at both wire rates; only the bytes per frame change
  (15,360 B vs 5,120 B, i.e. 768 vs 256 kB/s at 8 ch). That 3× lives in the plug/wire, not
  in these stages.
* The AEC row is the dominant measured cost (~4 % of one core here) and is essentially
  identical at both rates — it runs at the 16 kHz voice rate either way, so it is not a
  48 kHz cost.
* The window between the two wire rates is the first three rows: `decode` (zero-copy
  `np.frombuffer`), the 7-column float32 conversion, and `Decimator.process`.

**Conversion narrowing is not a win in the conversion itself.** 8 contiguous columns cost
0.0019 s per 10 s, the 7-column slice 0.0029 s (16 kHz: 0.0009 vs 0.0013) — the strided
column gather costs more than the column saved. It is kept because it bounds the filter
history to what the voice path reads and because the task requires the slice; the
conversion is noise either way (0.03 % of one core).

### 7 channels is slower than 8 in the decimator (0.0352 vs 0.0263)

`np.matmul` on `(320, 7, 16) @ (16,)` does not map to a full vector register (8 float32
lanes), so the 7-column phase product is slower per element than the 8-column one; that
costs ~34 % and eats the 12.5 % fewer columns. In absolute terms it is ~0.009 s per 10 s
of audio (~0.09 % of one core). The narrowing stays because it is the production shape and it
bounds the history; the alternative (benchmark 8 columns while production decimates 7)
would misreport the path.

## cProfile — old (full-rate), 5 passes, 2,500 frames

```
         165511 function calls in 0.515 seconds

   Ordered by: internal time
   List reduced from 66 to 10 due to restriction <10>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
     2500    0.452    0.000    0.462    0.000 numpy/_core/numeric.py:997(tensordot)
     2500    0.016    0.000    0.512    0.000 src/rui/audio/decimate.py:142(_reference_decimate)
     2500    0.009    0.000    0.030    0.000 numpy/lib/_stride_tricks_impl.py:177(sliding_window_view)
     2500    0.007    0.000    0.009    0.000 numpy/lib/_stride_tricks_impl.py:38(as_strided)
    10000    0.003    0.000    0.005    0.000 {built-in method math.prod}
     2500    0.002    0.000    0.004    0.000 numpy/_core/numeric.py:1427(normalize_axis_tuple)
    30030    0.002    0.000    0.002    0.000 {built-in method builtins.len}
     7500    0.002    0.000    0.002    0.000 {method 'reshape' of 'numpy.ndarray' objects}
     7510    0.002    0.000    0.002    0.000 {built-in method numpy.asarray}
     2510    0.002    0.000    0.003    0.000 numpy/_core/fromnumeric.py:86(_wrapreduction_any_all)
```

The hotspot is the single `tensordot` (0.452 s of 0.515 s, 88 %): the full-rate
convolution itself. Everything else is bookkeeping around it.

## cProfile — new (polyphase), 5 passes, 2,500 frames

```
         235506 function calls in 0.176 seconds

   Ordered by: internal time
   List reduced from 57 to 10 due to restriction <10>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
     2500    0.077    0.000    0.161    0.000 src/rui/audio/decimate.py:115(_polyphase)
     7500    0.025    0.000    0.082    0.000 numpy/lib/_stride_tricks_impl.py:177(sliding_window_view)
     7500    0.018    0.000    0.026    0.000 numpy/lib/_stride_tricks_impl.py:38(as_strided)
     2500    0.011    0.000    0.174    0.000 src/rui/audio/decimate.py:76(process)
     7500    0.007    0.000    0.010    0.000 numpy/_core/numeric.py:1427(normalize_axis_tuple)
     7510    0.005    0.000    0.009    0.000 numpy/_core/fromnumeric.py:86(_wrapreduction_any_all)
     7510    0.004    0.000    0.004    0.000 {built-in method numpy.asarray}
    22505    0.004    0.000    0.004    0.000 {built-in method numpy.array}
     7515    0.003    0.000    0.003    0.000 {method 'reduce' of 'numpy.ufunc' objects}
     7510    0.003    0.000    0.012    0.000 numpy/_core/fromnumeric.py:2474(any)
```

The remaining cost is spread over the 3 phase calls per frame: `_polyphase` (Python loop +
matmuls) 0.077 s, `sliding_window_view`/`as_strided` 0.043 s (3 calls per frame, one per
phase). cProfile inflates both (per-call Python overhead): the unprofiled run is 0.0263 s
against 0.0965 s. There is no single dominant line left; further gains would be a
per-frame view cache or a C-level FIR, neither of which the 0.2 s gate needs.

## Remaining attribution for the production 214 %

**The offline measurements do not explain it, and this document does not claim they do.**
What was measured, and what it rules out:

1. The core-side capture path is the only place inside `src/` whose work depends on the
   wire rate. The 48k-vs-16k delta is carried by the decimator plus the 7-column
   conversion: **~0.036 s of CPU per 10 s of audio** (0.0345 + 0.0029 vs 0.0001 + 0.0013
   in the table). The AEC runs on the voice-rate blocks and cancels between the two
   columns; its own run-to-run variance is ±0.012 s per 10 s, which is why the summed
   delta measured 0.032–0.043 s across runs. After the change that is ~0.36 % of one core
   on this host (before it, decimator + conversion were ~0.099 s, ~1.0 %). Even at a
   ~15–20× slower NAS core that is ~5–7 % of one core after the fix, ~15–20 % before it —
   **it cannot produce a jump of +186 percentage points.**
2. The measured stage that dominates the capture path is the AEC (~0.40 s per 10 s,
   ~4 % of one core here), and it is identical at 48 kHz and 16 kHz within its own
   variance (±0.012 s per 10 s across runs) — it runs on the voice-rate blocks, not the
   wire. The preroll/follow-up/control-ring copies also run on the 16 kHz mono voice PCM
   and are rate-invariant (0.0003 s in both columns); the frame rate stays 50 fps, and
   only the wire payload grows 3× (768 vs 256 kB/s at 8 ch).
3. Therefore the location of the remaining ~186 points is **undetermined**. It is outside
   the code this bench exercises, and this task did not measure it. Candidate owners, all
   unverified here:
   * the plug/adapter and wire decode (binary → PCM) that feeds `frame.pcm` — a separate
     bridge repo, not this workspace;
   * the websocket send/receive threads and the copies of the 3× larger payload;
   * the ASR/VAD worker threads;
   * device-side capture load (the device no longer resamples 48k→16k, we do the 8-column
     float conversion and filter instead);
   * anything else the production host was doing during the measurement.
4. **The task premise — that the decimator's full-rate convolution was the root cause of
   the ~2-core capture cost — is not confirmed offline.** Confirming or refuting it needs a
   profile of the running plane on the production NAS (e.g. `py-spy dump/top` while capture
   is up): that requires 9哥's authorization, is outside this task's scope, and was not
   done. Do not read this document as evidence that the 214 % should drop by 2 cores.

What *is* established: the decimator's own CPU on the 48 kHz path fell from 0.0965 s to
0.0263–0.0352 s per 10 s of 8-channel audio (3.7× / 2.7×), and the whole core-side capture
path (excluding the wire/plug/ASR threads this bench cannot see) now costs 0.447 s per
10 s at 48 kHz versus 0.404 s at 16 kHz, with the difference accounted for by the
decimator and the float conversion.

## 16 kHz wire (`factor == 1`)

Unchanged behaviour, now cheaper still: `Decimator.__init__` no longer designs a filter or
allocates history when `factor == 1` (`fir is None`, `_hist is None`), and `process` returns
`block.astype(np.float32, copy=False)` — byte-identical to 0.8.8 and provably free of any
windowed convolution (`tests/test_decimate.py` pins both: `lowpass` is monkeypatched to
raise, `sliding_window_view` is counted and must stay at zero). The stage table shows the
16 kHz wire still pays the AEC and the clip/tobytes/ring costs — the same work it always
did — and essentially nothing for decimation (0.0001 s / 10 s).
