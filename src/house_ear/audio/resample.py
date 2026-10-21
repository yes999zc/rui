"""Sample-rate conversion for the debug/playback path.

Linear interpolation, on purpose: this is the path that plays a local file at the
speaker so AEC and wake-word tests have something to hear, not a music engine. It is
exact at unity, cheap, and has no hidden latency; S5's music path can bring a proper
polyphase resampler if the artefacts ever matter.
"""
from __future__ import annotations

import numpy as np


def resample(x: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample a mono float32 signal. Returns float32 at `dst_rate`."""
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")
    if src_rate == dst_rate or x.size == 0:
        return x.astype(np.float32, copy=False)
    duration = x.size / float(src_rate)
    out_len = int(round(duration * dst_rate))
    if out_len <= 0:
        return np.zeros(0, dtype=np.float32)
    src_t = np.arange(x.size, dtype=np.float64) / src_rate
    dst_t = np.arange(out_len, dtype=np.float64) / dst_rate
    return np.interp(dst_t, src_t, x.astype(np.float64)).astype(np.float32)
