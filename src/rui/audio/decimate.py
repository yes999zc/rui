"""Streaming decimation from an adapter's capture rate to the voice rate.

A reference array speaker hands over 48 kHz; AEC, KWS and VAD run at 16 kHz. Naive `[::3]` aliases
everything between 8 and 24 kHz back into the speech band, which is exactly where a
speaker's own music lives - so the filter is not optional, it is the difference between
"KWS works with music playing" and "KWS only works in silence".

Stateful on purpose: frames arrive 20 ms at a time and the filter cannot restart per
frame, or every frame boundary becomes a click and a lost sample.
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
        self.taps = taps if factor > 1 else 1
        self.fir = lowpass(factor, taps, passband)
        self._hist = np.zeros((self.taps - 1, channels), dtype=np.float32)
        self._base = 0          # absolute input index of _hist[0]
        self._next_out = 0      # absolute input index of the next sample to emit

    @property
    def outputs_emitted(self) -> int:
        return self._next_out // self.factor

    def process(self, block: np.ndarray) -> np.ndarray:
        """(n, channels) int16 or float -> (m, channels) float32, m = n // factor on average."""
        if block.ndim != 2 or block.shape[1] != self.channels:
            raise ValueError(f"expected (n, {self.channels}) block, got {block.shape}")
        if self.factor == 1:
            return block.astype(np.float32, copy=False)

        x = block.astype(np.float32, copy=False)
        ext = np.concatenate([self._hist, x], axis=0) if self._hist.size else x

        if len(ext) < self.taps:
            self._hist = ext
            return np.zeros((0, self.channels), dtype=np.float32)

        windows = np.lib.stride_tricks.sliding_window_view(ext, self.taps, axis=0)
        conv = np.tensordot(windows, self.fir, axes=([2], [0]))
        start_abs = self._base + (self.taps - 1)

        offset = self._next_out - start_abs
        if offset < 0:
            jump = ((-offset + self.factor - 1) // self.factor) * self.factor
            offset += jump
            self._next_out += jump

        out = np.zeros((0, self.channels), dtype=np.float32)
        if 0 <= offset < len(conv):
            out = conv[offset:: self.factor].astype(np.float32, copy=False)
            self._next_out += len(out) * self.factor

        keep = self.taps - 1
        self._hist = ext[-keep:].copy() if keep else np.zeros((0, self.channels), dtype=np.float32)
        self._base += len(ext) - keep
        return out
