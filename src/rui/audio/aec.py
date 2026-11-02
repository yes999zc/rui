"""Echo cancellation: one speexdsp MDF filter per microphone, then pick a channel.

Why speexdsp and not our own filter: the vendor front end itself calls
`speex_echo_state_init(0x80, 0x1000)` - 128-sample frames, 4096 taps - and the old
deployment measured 24-30 dB ERLE with those parameters on this exact speaker. Matching
the vendor's geometry is the cheapest way to inherit that result; anything else is a
filter we would have to tune from scratch.

Two invariants kept from the old implementation, both learned the hard way:

* **Never make it worse.** Right after playback starts the filter can overshoot; a
  residual louder than the input is worse than no cancellation, so the raw microphone
  wins whenever the frame gets louder.
* **A diverged filter must reset, not leak.** speex writes garbage on divergence;
  feeding non-finite samples onwards poisons KWS silently forever.

The reference channel is the speaker's own loopback (ch6/ch7), so alignment is a fixed
2.4 ms lead rather than a search.
"""
from __future__ import annotations

import ctypes
import time

import numpy as np

from ..env import get as env_get
from .frontend import ChannelMixer

SPEEX_ECHO_SET_SAMPLING_RATE = 24
SPEEX_FRAME = 128  # speex only speaks in 128-sample frames
# Attaching the echo state to the preprocessor is what turns the linear MDF into the
# vendor's chain: its `AECProcess` (libxaudio_engine.so @0x29714) calls
# `speex_echo_cancellation` and then `speex_preprocess_run` on the same block, and the
# second call is where residual echo suppression lives.
#
# The old "linear 18.0-20.0 dB vs +preprocess 21.8-23.7 dB" bench numbers are void: that
# script (01_Code/experiments/erle-bench/erle_bench.py) wrote the echo-state slot as 14
# (= SET_PROB_START), so the echo state was never attached and it measured linear + plain
# spectral denoise, not residual echo suppression. The script needs its own fix (not this
# card). On the in-tree synthetic bench (tests/test_aec_residual.py) the second stage is
# worth ~+5 dB; the only real number is the device's, from `GET /api/v1/audio`
# `metrics.aec_erle_db` (0.8.14 baseline, 20% volume, steady playback: 19.3-23.0 dB).
# Values from speex_preprocess.h: the echo-state slot is 24 (14 is SET_PROB_START).
SPEEX_PREPROCESS_SET_ECHO_STATE = 24
SPEEX_PREPROCESS_GET_ECHO_STATE = 25
SPEEX_PREPROCESS_SET_AGC = 2
SPEEX_PREPROCESS_SET_VAD = 4

_LIB_CANDIDATES = (
    env_get("RUI_SPEEXDSP", ""),
    "/opt/homebrew/lib/libspeexdsp.dylib",
    "/usr/local/lib/libspeexdsp.dylib",
    "libspeexdsp.so.1",
    "libspeexdsp.so",
    "libspeexdsp.dylib",
)


class AecUnavailable(RuntimeError):
    """libspeexdsp is not installed: AEC is off, not silently degraded."""


def load_speexdsp() -> ctypes.CDLL:
    last: Exception | None = None
    for candidate in _LIB_CANDIDATES:
        if not candidate:
            continue
        try:
            lib = ctypes.CDLL(candidate)
            lib.speex_echo_state_init  # noqa: B018 - resolution check
        except (OSError, AttributeError) as exc:
            last = exc
            continue
        # Every prototype, including destroy: leaving restype default (int) on a void
        # function corrupts the stack and segfaults on macOS.
        lib.speex_echo_state_init.restype = ctypes.c_void_p
        lib.speex_echo_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.speex_echo_cancellation.restype = ctypes.c_int
        lib.speex_echo_cancellation.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        ]
        lib.speex_echo_ctl.restype = ctypes.c_int
        lib.speex_echo_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        lib.speex_echo_state_reset.restype = None
        lib.speex_echo_state_reset.argtypes = [ctypes.c_void_p]
        lib.speex_echo_state_destroy.restype = None
        lib.speex_echo_state_destroy.argtypes = [ctypes.c_void_p]
        # Residual echo suppression is optional on purpose: a libspeexdsp build without
        # the preprocessor must still cancel linearly rather than go silent.
        lib.has_preprocess = hasattr(lib, "speex_preprocess_state_init")
        if lib.has_preprocess:
            lib.speex_preprocess_state_init.restype = ctypes.c_void_p
            lib.speex_preprocess_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
            lib.speex_preprocess_run.restype = ctypes.c_int
            lib.speex_preprocess_run.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            lib.speex_preprocess_ctl.restype = ctypes.c_int
            lib.speex_preprocess_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            lib.speex_preprocess_state_destroy.restype = None
            lib.speex_preprocess_state_destroy.argtypes = [ctypes.c_void_p]
        return lib
    raise AecUnavailable(f"libspeexdsp not found ({last})")


def speex_available() -> bool:
    try:
        load_speexdsp()
    except AecUnavailable:
        return False
    return True


class HighPass:
    """Second-order Butterworth high-pass (biquad, numpy only).

    Same 90 Hz corner on the microphone and on the reference: fitting a high-passed
    microphone against an unfiltered reference leaves the canceller modelling a filter
    it cannot see.
    """

    def __init__(self, fc: float = 90.0, rate: int = 16000) -> None:
        w0 = 2.0 * np.pi * fc / rate
        alpha = np.sin(w0) / (2.0 * np.sqrt(0.5))
        cos = np.cos(w0)
        b0 = (1 + cos) / 2
        b1 = -(1 + cos)
        b2 = (1 + cos) / 2
        a0 = 1 + alpha
        self.b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64)
        self.a = np.array([1.0, (-2 * cos) / a0, (1 - alpha) / a0], dtype=np.float64)
        self._x = np.zeros(2)
        self._y = np.zeros(2)

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return x.astype(np.float32, copy=False)
        y = np.empty(x.size, dtype=np.float64)
        x1, x2 = self._x
        y1, y2 = self._y
        b0, b1, b2 = self.b
        _, a1, a2 = self.a
        for i, xi in enumerate(x.astype(np.float64, copy=False)):
            yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, xi
            y2, y1 = y1, yi
            y[i] = yi
        self._x = np.array([x1, x2])
        self._y = np.array([y1, y2])
        return y.astype(np.float32)

    def reset(self) -> None:
        self._x = np.zeros(2)
        self._y = np.zeros(2)


class EchoCanceller:
    """One MDF filter (speexdsp), fed arbitrary block sizes."""

    def __init__(self, taps: int = 4096, rate: int = 16000, residual: bool = True) -> None:
        self.lib = load_speexdsp()
        self.taps = taps
        self.rate = rate
        self.handle = self.lib.speex_echo_state_init(SPEEX_FRAME, taps)
        if not self.handle:
            raise AecUnavailable("speex_echo_state_init failed")
        sr = ctypes.c_int(rate)
        self.lib.speex_echo_ctl(self.handle, SPEEX_ECHO_SET_SAMPLING_RATE, ctypes.byref(sr))
        # Residual echo suppression: the same second stage the vendor runs. Without it the
        # canceller leaves the late/echo-tail energy the linear filter cannot model, which
        # is what the wake path then transcribes as noise. `hold` switches it off for the
        # frames right after a wake: the preprocessor's suppressor is tuned to remove
        # anything that correlates with playback, and the first command word does too.
        self.pp = None
        if residual and getattr(self.lib, "has_preprocess", False):
            self.pp = self.lib.speex_preprocess_state_init(SPEEX_FRAME, rate)
            if self.pp:
                # preprocess.c does `case SET_ECHO_STATE: st->echo_state = ptr`, so the
                # pointer handed to speex_preprocess_ctl *is* the echo state: pass the
                # handle itself. byref() would store the address of a stack temporary and
                # the first `run` would dereference a dangling pointer.
                self.lib.speex_preprocess_ctl(
                    self.pp, SPEEX_PREPROCESS_SET_ECHO_STATE, ctypes.c_void_p(self.handle)
                )
                # Denoise stays at its default (on) on purpose. It is *not* just noise
                # suppression: preprocess.c gates the whole spectral gain on it
                # (`if (!st->denoise_enabled) gain2[i] = Q15_ONE`) and the echo suppressor
                # is applied through that same gain2, so SET_DENOISE=0 silently turns the
                # residual stage into a pass-through. speexdsp has no echo-only mode -
                # setting noise_suppress to 0 measures ~0.7 dB of suppression - so the
                # price of this stage is its default noise suppression: up to -15 dB on
                # noise, alongside up to -40 dB on echo that is clamped to -15 dB
                # (echo_suppress_active) once near-end speech is detected. AGC and VAD are
                # off; they are not part of the cancellation, and AGC would move levels.
                for ctl, value in (
                    (SPEEX_PREPROCESS_SET_AGC, 0),
                    (SPEEX_PREPROCESS_SET_VAD, 0),
                ):
                    flag = ctypes.c_int(value)
                    self.lib.speex_preprocess_ctl(self.pp, ctl, ctypes.byref(flag))
        self.hold = False
        self._mic = np.zeros(0, dtype=np.float32)
        self._ref = np.zeros(0, dtype=np.float32)
        self._out = np.zeros(0, dtype=np.float32)

    def _frame(self, mic16: np.ndarray, ref16: np.ndarray) -> np.ndarray:
        out = np.zeros(SPEEX_FRAME, dtype="<i2")
        self.lib.speex_echo_cancellation(
            self.handle,
            mic16.ctypes.data_as(ctypes.c_void_p),
            ref16.ctypes.data_as(ctypes.c_void_p),
            out.ctypes.data_as(ctypes.c_void_p),
        )
        if self.pp is not None and not self.hold:
            self.lib.speex_preprocess_run(self.pp, out.ctypes.data_as(ctypes.c_void_p))
        return out.astype(np.float32) / 32768.0

    def process(self, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
        mic = np.nan_to_num(np.ascontiguousarray(mic, dtype=np.float32), copy=False)
        ref = np.nan_to_num(np.ascontiguousarray(ref, dtype=np.float32), copy=False)
        if mic.size == 0:
            return mic
        if mic.size != ref.size:
            ref = ref[: mic.size] if ref.size > mic.size else np.pad(ref, (0, mic.size - ref.size))

        self._mic = np.concatenate([self._mic, mic])
        self._ref = np.concatenate([self._ref, ref])
        blocks = min(self._mic.size, self._ref.size) // SPEEX_FRAME
        if blocks:
            take = blocks * SPEEX_FRAME
            outs = []
            for i in range(0, take, SPEEX_FRAME):
                m16 = np.clip(self._mic[i:i + SPEEX_FRAME] * 32768.0, -32768, 32767).astype("<i2")
                r16 = np.clip(self._ref[i:i + SPEEX_FRAME] * 32768.0, -32768, 32767).astype("<i2")
                outs.append(self._frame(m16, r16))
            self._out = np.concatenate([self._out, *outs])
            self._mic = self._mic[take:]
            self._ref = self._ref[take:]

        # Keep the returned block sample-aligned with what the caller sent: at most one
        # 8 ms speex frame of latency, never a shifting timeline for KWS.
        if self._out.size >= mic.size:
            out, self._out = self._out[: mic.size], self._out[mic.size:]
        else:
            out = np.concatenate([self._out, np.zeros(mic.size - self._out.size, dtype=np.float32)])
            self._out = np.zeros(0, dtype=np.float32)

        if not np.isfinite(out).all():
            self.reset()
            return mic
        if _rms(out) > _rms(mic) * 1.05:  # never make it worse
            return mic
        return out

    def reset(self) -> None:
        if self.handle:
            self.lib.speex_echo_state_reset(self.handle)
        self._mic = np.zeros(0, dtype=np.float32)
        self._ref = np.zeros(0, dtype=np.float32)
        self._out = np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        if self.handle:
            self.lib.speex_echo_state_destroy(self.handle)
            self.handle = None
        if self.pp:
            self.lib.speex_preprocess_state_destroy(self.pp)
            self.pp = None


def _rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


class AecChain:
    """Cancel every microphone against the reference, emit the channel that survived.

    Channel choice is what the array is for: after cancellation the microphone that
    still hears the person best is the one worth transcribing. Switching is damped by
    a hysteresis margin so a wake word is not chopped by frame-to-frame energy ties.
    """

    def __init__(
        self,
        channels: int = 6,
        rate: int = 16000,
        taps: int = 4096,
        hp_hz: float = 90.0,
        hysteresis_db: float = 6.0,
        cancel: str = "mix",
        residual: bool = True,
        wake_hold_ms: int = 15000,
    ) -> None:
        if channels < 1:
            raise ValueError("channels must be >= 1")
        self.channels = channels
        self.rate = rate
        self.hysteresis_db = hysteresis_db
        # `mix` reproduces the old deployment's topology, which measured 25.2 dB ERLE at
        # mysoftvol 170 on this speaker: one speexdsp canceller on the *array mix*, where
        # the filter sees the best SNR the six microphones can give. Per-channel filters
        # (six of them, rtf 0.066) measured 20.9 dB at the same volume - each one adapts
        # against a noisier single microphone. Kept as an option because the per-channel
        # residuals are what the diagnostic ERLE per channel needs.
        self.cancel = cancel
        self.residual = residual
        self.wake_hold_ms = wake_hold_ms
        self.cancellers = (
            [EchoCanceller(taps=taps, rate=rate, residual=residual) for _ in range(channels)]
            if cancel == "per_channel" else []
        )
        self.mix_canceller = (
            EchoCanceller(taps=taps, rate=rate, residual=residual) if cancel == "mix" else None
        )
        self._hold_until = 0.0
        self.mic_hp = [HighPass(hp_hz, rate) for _ in range(channels)]
        self.ref_hp = HighPass(hp_hz, rate)
        self.mixer = ChannelMixer(channels)
        self.selected = 0                  # loudest channel, for reporting only
        self._power = np.zeros(channels)   # EMA of post-cancellation power per channel
        self._mic_power = 0.0              # EMA of pre-cancellation power (selected ch)
        self._res_power = 0.0              # EMA of residual power (selected ch)
        self.ref_active = False
        self.blocks = 0

    # reference is "active" above about -80 dBFS; below it ERLE is meaningless
    REF_ACTIVE_RMS = 1e-4

    def hold_for(self, seconds: float | None = None) -> None:
        """Pause residual suppression for the wake window.

        The preprocessor removes whatever correlates with the playback reference, and the
        first words spoken over music correlate too - so it eats precisely the sentence
        the wake just announced. The vendor solves this from the AEC side with
        `xaudio_wakeup_get_wakeup_flag_4_aec`; we drop only the suppressor for the wake
        window, so linear cancellation keeps running and the near-end speech survives.
        """
        if self.wake_hold_ms <= 0 and seconds is None:
            return
        span = self.wake_hold_ms / 1000.0 if seconds is None else max(0.0, seconds)
        self._hold_until = max(self._hold_until, time.monotonic() + span)

    def release(self) -> None:
        """End the wake hold now: the turn it protected is over.

        0.8.16: `hold_for()` is no longer a fixed 1.5 s that outlives a long command; the
        plane holds at the wake and releases when the segment is reported, rejected, or
        the listening window closes. `wake_hold_ms` stays as the cap (a stuck turn must
        not keep the suppressor off forever).
        """
        self._hold_until = 0.0
        self._apply_hold()

    @property
    def holding(self) -> bool:
        """True while residual suppression is bypassed (diagnostics/tests)."""
        return time.monotonic() < self._hold_until

    def _apply_hold(self) -> None:
        held = time.monotonic() < self._hold_until
        if self.mix_canceller is not None:
            self.mix_canceller.hold = held
        for canceller in self.cancellers:
            canceller.hold = held

    def process(self, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
        if mic.ndim != 2 or mic.shape[1] != self.channels:
            raise ValueError(f"expected (n, {self.channels}) microphone block, got {mic.shape}")
        if mic.shape[0] == 0:
            return np.zeros(0, dtype=np.float32)

        self._apply_hold()

        ref_hp = self.ref_hp.process(ref)
        self.ref_active = _rms(ref) > self.REF_ACTIVE_RMS

        # Every channel's high-pass runs every block, played or not. Advancing only the
        # selected one froze five filter states, and the first block after playback
        # started carried their stale history as a click.
        hp = np.empty_like(mic, dtype=np.float32)
        for ch, filt in enumerate(self.mic_hp):
            hp[:, ch] = filt.process(mic[:, ch])

        # Weights come from the *microphone*, not the residual: while the speaker is
        # emitting, the loudest residual is the channel that cancelled worst, so
        # weighting towards it would amplify the echo we just removed. Frozen during
        # playback for the old build's reason - the weights would otherwise chase the
        # echo and change mid-word.
        weights = self.mixer.update(hp, freeze=self.ref_active)
        self.selected = int(np.argmax(weights))

        if not self.ref_active:
            # Nothing playing: no echo to cancel, and adapting a filter to silence
            # drifts it. The microphones still get the array mix - handing through one
            # channel threw away the only gain this speaker has.
            self.blocks += 1
            return self.mixer.mix(hp, weights)

        mic_mix = self.mixer.mix(hp, weights)
        if self.mix_canceller is not None:
            out = self.mix_canceller.process(mic_mix, ref_hp)
        else:
            residuals = np.empty_like(mic, dtype=np.float32)
            for ch, canceller in enumerate(self.cancellers):
                residuals[:, ch] = canceller.process(hp[:, ch], ref_hp)
            out = self.mixer.mix(residuals, weights)
        # Never make it worse, now on the mixed signals: a residual louder than the
        # microphone means the filter overshot or diverged on this block.
        if _rms(out) > 1.05 * max(_rms(mic_mix), 1e-9):
            out = mic_mix

        # ERLE is measured on what actually leaves this stage, which is also how the old
        # deployment measured it (on its mixed stream), so the two numbers compare.
        self._mic_power = 0.9 * self._mic_power + 0.1 * float(
            np.mean(np.square(mic_mix.astype(np.float64)))
        )
        self._res_power = 0.9 * self._res_power + 0.1 * float(
            np.mean(np.square(out.astype(np.float64)))
        )
        self.blocks += 1
        return out

    @property
    def erle_db(self) -> float | None:
        if not self.ref_active or self._res_power <= 0.0 or self._mic_power <= 0.0:
            return None
        return round(10.0 * float(np.log10(self._mic_power / self._res_power)), 1)

    def reset(self) -> None:
        for canceller in self.cancellers:
            canceller.reset()
        if self.mix_canceller is not None:
            self.mix_canceller.reset()
        for hp in self.mic_hp:
            hp.reset()
        self.ref_hp.reset()
        self.mixer.reset()
        self._power = np.zeros(self.channels)
        self._mic_power = self._res_power = 0.0
        self._hold_until = 0.0

    def close(self) -> None:
        for canceller in self.cancellers:
            canceller.close()
        if self.mix_canceller is not None:
            self.mix_canceller.close()

    # ------------------------------------------------------------------ factory
    @classmethod
    def from_config(cls, channels: int, rate: int, taps: int,
                    cancel: str = "mix", residual: bool = True,
                    wake_hold_ms: int = 15000) -> AecChain:
        return cls(channels=channels, rate=rate, taps=taps, cancel=cancel,
                   residual=residual, wake_hold_ms=wake_hold_ms)
