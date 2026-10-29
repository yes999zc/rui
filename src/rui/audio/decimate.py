"""Streaming decimation from an adapter's capture rate to the voice rate.

A reference array speaker hands over 48 kHz; AEC, KWS and VAD run at 16 kHz. Naive `[::3]` aliases
everything between 8 and 24 kHz back into the speech band, which is exactly where a
speaker's own music lives - so the filter is not optional, it is the difference between
"KWS works with music playing" and "KWS only works in silence".

Stateful on purpose: frames arrive 20 ms at a time and the filter cannot restart per
frame, or every frame boundary becomes a click and a lost sample.

The filter runs as a polyphase decimator (0.8.9): the taps are split by `factor` and only
the samples that actually become an output sample are multiplied - a 48-tap / factor-3
frame costs 48 taps * (n / 3) outputs instead of 48 taps * n sliding windows. The
pre-polyphase full-rate convolution is kept as `Decimator._reference_decimate` because it
is the oracle the equivalence tests compare against; production code must not call it.
"""
from __future__ import annotations

import numpy as np


def lowpass(factor: int, taps: int = 48, passband: float = 0.9) -> np.ndarray:
    """Windowed-sinc low-pass for decimation by `factor`, normalised to unity DC gain.

    `passband` is measured against the *output* Nyquist: 0.9 keeps 0..7.2 kHz when
    going 48 kHz -> 16 kHz, which covers the whole speech band with room to spare.
    """
    if factor <= 1:
        return np.ones(1, dtype=np.float32)
    m = taps - 1
    n = np.arange(taps, dtype=np.float64) - m / 2.0
    fc = passband / (2.0 * factor)          # cycles per input sample
    h = 2.0 * fc * np.sinc(2.0 * fc * n) * np.kaiser(taps, 8.0)
    total = h.sum()
    if total == 0:
        raise ValueError("degenerate low-pass design")
    return (h / total).astype(np.float32)


class Decimator:
    """One filter per channel, with history, phase and a running output index."""

    def __init__(self, factor: int, channels: int, taps: int = 48, passband: float = 0.9) -> None:
        if factor < 1:
            raise ValueError("factor must be >= 1")
        if channels < 1:
            raise ValueError("channels must be >= 1")
        self.factor = factor
        self.channels = channels
        # factor == 1 is the 16 kHz wire: the voice rate *is* the capture rate, so there
        # is nothing to filter and nothing to remember. Short-circuit on purpose - a
        # filter here would be 48 taps of pointless work on every frame (and, on the old
        # build, was skipped only by `process` returning early).
        self.taps = taps if factor > 1 else 1
        self.fir: np.ndarray | None = None
        self._phases: tuple[np.ndarray, ...] = ()
        self._hist: np.ndarray | None = None
        self._base = 0          # absolute input index of _hist[0]
        self._next_out = 0      # absolute input index of the next sample to emit
        if factor > 1:
            self.fir = lowpass(factor, taps, passband)
            # fir[j0::factor] holds every tap that multiplies the same phase of the
            # decimated input; output m only ever touches one such phase at a time.
            self._phases = tuple(self.fir[r::factor] for r in range(factor))
            self._hist = np.zeros((self.taps - 1, channels), dtype=np.float32)
        # State for the pre-polyphase oracle in `_reference_decimate`, allocated on first
        # use so production never pays for it.
        self._ref_hist: np.ndarray | None = None
        self._ref_base = 0
        self._ref_next_out = 0

    @property
    def outputs_emitted(self) -> int:
        return self._next_out // self.factor

    def process(self, block: np.ndarray) -> np.ndarray:
        """(n, channels) int16 or float -> (m, channels) float32, m = n // factor on average."""
        if block.ndim != 2 or block.shape[1] != self.channels:
            raise ValueError(f"expected (n, {self.channels}) block, got {block.shape}")
        if self.factor == 1:
            return block.astype(np.float32, copy=False)

        assert self._hist is not None
        x = block.astype(np.float32, copy=False)
        ext = np.concatenate([self._hist, x], axis=0) if self._hist.size else x

        if len(ext) < self.taps:
            self._hist = ext
            return np.zeros((0, self.channels), dtype=np.float32)

        # `ext` is the input seen so far, `_base` its absolute offset. A full window
        # ending at absolute index `start_abs + i` lives at ext[i:i+taps], which is why
        # the old flow emitted conv[i::factor] for i = _next_out - start_abs.
        start_abs = self._base + (self.taps - 1)
        offset = self._next_out - start_abs
        if offset < 0:
            # First block: emit the first sample whose window is complete *and* land on
            # the same phase grid the rest of the stream uses (_next_out % factor == 0).
            jump = ((-offset + self.factor - 1) // self.factor) * self.factor
            offset += jump
            self._next_out += jump

        out = np.zeros((0, self.channels), dtype=np.float32)
        conv_len = len(ext) - self.taps + 1
        if 0 <= offset < conv_len:
            count = (conv_len - 1 - offset) // self.factor + 1
            out = self._polyphase(ext, offset, count)
            self._next_out += count * self.factor

        keep = self.taps - 1
        self._hist = ext[-keep:].copy() if keep else np.zeros((0, self.channels), dtype=np.float32)
        self._base += len(ext) - keep
        return out

    def _polyphase(self, ext: np.ndarray, offset: int, count: int) -> np.ndarray:
        """Emit `count` outputs at ext indices `offset + m * factor`, phase by phase.

        Output m is `sum_j fir[j] * ext[offset + m*factor + j]`. Group the taps by
        `(offset + j) % factor`: every tap in one group reads one residue class of the
        input, i.e. one phase `ext[r::factor]`, and all of those taps are a strided slice
        of `fir`. Multiplying a phase-sized slice and adding is the whole decimation -
        no window over the full-rate signal, no (n, channels, taps) intermediate.
        """
        assert self._phases
        out = np.zeros((count, self.channels), dtype=np.float32)
        for r in range(self.factor):
            j0 = (r - offset) % self.factor          # first tap index of this phase
            coeffs = self._phases[j0]
            taps_here = len(coeffs)
            if taps_here == 0:                        # factor > taps: empty phase
                continue
            # ext[r + t*factor] * phase coefficient (t - t0 - m) contributes to output m.
            t0 = (offset + j0 - r) // self.factor
            phase = ext[r::self.factor]               # strided view, no copy
            windows = np.lib.stride_tricks.sliding_window_view(phase, taps_here, axis=0)
            # (count, channels, taps_here) @ (taps_here,) -> (count, channels): one
            # matrix-vector product per output sample, no full-rate window. matmul beats
            # tensordot here by 2x because it needs no axis transpose/copy.
            out += np.matmul(windows[t0:t0 + count], coeffs)
        return out

    def _reference_decimate(self, block: np.ndarray) -> np.ndarray:
        """Pre-polyphase full-rate convolution - the equivalence oracle, tests only.

        This is the 0.8.8 `process` body, verbatim, against its own history/base/next_out
        so a test can drive `Decimator.process` and this oracle from the same instance
        and compare sample for sample. Do not call it from production: it slides a
        `taps`-wide window over every input sample and materialises an
        (n, channels, taps) product.
        """
        if block.ndim != 2 or block.shape[1] != self.channels:
            raise ValueError(f"expected (n, {self.channels}) block, got {block.shape}")
        if self.factor == 1:
            return block.astype(np.float32, copy=False)
        assert self.fir is not None
        if self._ref_hist is None:
            self._ref_hist = np.zeros((self.taps - 1, self.channels), dtype=np.float32)
            self._ref_base = 0
            self._ref_next_out = 0

        x = block.astype(np.float32, copy=False)
        ext = np.concatenate([self._ref_hist, x], axis=0) if self._ref_hist.size else x

        if len(ext) < self.taps:
            self._ref_hist = ext
            return np.zeros((0, self.channels), dtype=np.float32)

        windows = np.lib.stride_tricks.sliding_window_view(ext, self.taps, axis=0)
        conv = np.tensordot(windows, self.fir, axes=([2], [0]))
        start_abs = self._ref_base + (self.taps - 1)

        offset = self._ref_next_out - start_abs
        if offset < 0:
            jump = ((-offset + self.factor - 1) // self.factor) * self.factor
            offset += jump
            self._ref_next_out += jump

        out = np.zeros((0, self.channels), dtype=np.float32)
        if 0 <= offset < len(conv):
            out = conv[offset:: self.factor].astype(np.float32, copy=False)
            self._ref_next_out += len(out) * self.factor

        keep = self.taps - 1
        self._ref_hist = (
            ext[-keep:].copy() if keep else np.zeros((0, self.channels), dtype=np.float32)
        )
        self._ref_base += len(ext) - keep
        return out
