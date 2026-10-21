"""Wake acknowledgement tones, synthesised from parameters - no sample files.

A 160 ms cue is the default because a spoken acknowledgement keeps the microphone
muted for ~1.2 s on an LX06 (measured on the old deployment) and eats the start of a
request that follows the wake word immediately.

Level: every preset is normalised to `PEAK = 0.05`, the level that finally landed below
the knee of the device playback path - on the old deployment 0.55 and 0.27 sounded
identical at the microphones, so a loud digital peak buys nothing and only risks the
"quiet at 1, clipping at 2" failure. `trim` can therefore only go down (MAX_TRIM).

Timbres are approximations on purpose: this is a cue, not music. The note pairs, decay
times and partial weights are the ones the old project shipped in production.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

PEAK = 0.05          # digital peak every preset is normalised to
MAX_TRIM = 1.0       # never above PEAK: the playback path saturates past ~0.14
DEFAULT_MS = 160     # the default acknowledgement length


@dataclass(frozen=True)
class Preset:
    """One cue: struck notes in order, and the partial weights they share."""

    notes: tuple[tuple[float, float, float], ...]  # (freq_hz, ms, amp)
    partials: tuple[tuple[float, float], ...] = field(default=((1.0, 1.0), (2.0, 0.06)))
    tau: float = 0.18        # exponential decay, seconds
    attack: float = 0.03     # seconds; a hard onset clicks on a small speaker
    detune_hz: float = 0.0   # two sines a few Hz apart beat slowly


PRESETS: dict[str, Preset] = {
    # The one the old deployment used before the console existed: 660 -> 880 Hz.
    "soft2": Preset(notes=((660.0, 220.0, 1.0), (880.0, 280.0, 0.92))),
    # One short blip. The least intrusive option when the room is quiet.
    "blip": Preset(notes=((880.0, 120.0, 1.0),), tau=0.10),
    # Wooden bars: the 4th partial carries the character, fast decay.
    "marimba": Preset(
        notes=((523.25, 240.0, 1.0), (783.99, 200.0, 0.85)),
        partials=((1.0, 1.0), (4.0, 0.35), (10.0, 0.10)),
        tau=0.13,
    ),
    # Two sines 1.5 Hz apart beat slowly; long tail, soft attack.
    "glass": Preset(
        notes=((1318.5, 340.0, 0.8), (1567.98, 260.0, 0.7)),
        partials=((1.0, 1.0),),
        tau=0.30,
        attack=0.02,
        detune_hz=1.5,
    ),
    # 1/n^1.6 partials - the closest to a struck string.
    "piano": Preset(
        notes=((1046.5, 300.0, 1.0), (1567.98, 220.0, 0.8)),
        partials=((1.0, 1.0), (2.0, 0.45), (3.0, 0.28), (4.0, 0.18), (5.0, 0.12)),
        tau=0.22,
        attack=0.012,
    ),
}


def _note(preset: Preset, freq: float, ms: float, amp: float, rate: int) -> np.ndarray:
    """One struck note: partials x exponential decay, attack, 25 ms fade at the end."""
    n = max(1, int(rate * ms / 1000.0))
    t = np.arange(n) / rate
    env = np.minimum(1.0, t / max(preset.attack, 1e-4))
    env *= np.exp(-np.maximum(t - preset.attack, 0.0) / max(preset.tau, 1e-4))
    env *= np.minimum(1.0, np.maximum(0.0, (n / rate - t)) / 0.025)
    sig = np.zeros(n)
    for ratio, weight in preset.partials:
        f = freq * ratio
        if preset.detune_hz:
            sig += weight * 0.5 * np.sin(2 * math.pi * (f - preset.detune_hz) * t)
            sig += weight * 0.5 * np.sin(2 * math.pi * (f + preset.detune_hz) * t + 0.4)
        else:
            sig += weight * np.sin(2 * math.pi * f * t)
    return amp * sig * env


def render(
    name: str = "soft2",
    *,
    rate: int = 48000,
    duration_ms: int = DEFAULT_MS,
    trim: float = 1.0,
) -> np.ndarray:
    """Synthesise one cue as mono float32 in [-1, 1] at `rate`, `duration_ms` long.

    The preset's own note lengths are scaled to fit `duration_ms`, so the timbre is
    preserved when the length is changed (the console exposes both). Unknown names raise
    KeyError with the available presets: a silent fallback would make a typo in the config
    look like a broken speaker.
    """
    preset = PRESETS[name]
    total_ms = sum(ms for _, ms, _ in preset.notes)
    factor = max(1.0, duration_ms) / total_ms if total_ms else 1.0
    parts = [
        _note(preset, freq, ms * factor, amp, rate) for freq, ms, amp in preset.notes
    ]
    audio = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
    peak = float(np.max(np.abs(audio)))
    if peak > 0:
        audio = audio * (PEAK / peak)
    return (audio * min(max(trim, 0.0), MAX_TRIM)).astype(np.float32)
