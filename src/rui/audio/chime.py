"""Wake acknowledgement tones, synthesised from parameters - no sample files.

The cue exists to confirm the wake without costing a millisecond of the request. A spoken
acknowledgement keeps the LX06 microphone muted for ~1.2 s; a tone costs only its own
length, so the default here is 160 ms. (The old deployment shipped 500 ms; `chime_ms` is a
knob and `chime_ms: 0` renders a preset at its natural length.)

What is inherited exactly: partial weights, decay shapes, attack, note pairs, and the level
normalisation. What is not inherited blindly:

- note lengths are scaled to `duration_ms` and `tau` scales with them, so a shortened
  preset keeps its shape instead of turning into a chirp;
- every preset keeps its natural length (soft2 500 ms, marimba 420, glass 600, piano 520,
  woodblock 120, windchime 700) for `duration_ms: 0`;
- level: every preset is normalised to `PEAK = 0.05`, the value that finally landed below
  the knee of the device playback path - 0.55 and 0.27 measured identical at the
  microphones, so a hotter render buys nothing and only risks the "quiet at 1, clipping at
  2" failure. `trim` can only go down (MAX_TRIM).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Digital peak every preset is normalised to. 0.05 was inherited from the old build, where
# it sat under a playback path with its own gain; here the cue goes out beside full-scale
# TTS audio, and 9哥 heard it as "too quiet" (2026-10-22). 0.12 is just under the 0.14
# knee the old build measured, so it is ~7.6 dB louder without touching the saturation
# the speaker already showed above it.
PEAK = 0.12
MAX_TRIM = 1.0       # never above PEAK: the playback path saturates past ~0.14
DEFAULT_MS = 320     # the shipped length: see AckConfig.chime_ms for why


@dataclass(frozen=True)
class Preset:
    """One cue: struck notes, the partial weights they share, and how they ring."""

    notes: tuple[tuple[float, float, float], ...]  # (freq_hz, ms, amp)
    partials: tuple[tuple[float, float], ...] = field(default=((1.0, 1.0), (2.0, 0.06)))
    tau: float = 0.18        # exponential decay, seconds at the natural length
    attack: float = 0.03     # seconds; a hard onset clicks on a small speaker
    detune_hz: float = 0.0   # two sines a few Hz apart beat slowly
    noise: float = 0.0       # transient burst level, for struck wood
    seed: int = 0
    overlap: bool = False    # notes ring together (a chime) instead of playing in sequence

    @property
    def natural_ms(self) -> float:
        if self.overlap:
            return max(ms for _, ms, _ in self.notes)
        return sum(ms for _, ms, _ in self.notes)


PRESETS: dict[str, Preset] = {
    # 660 -> 880 Hz: the old deployment's default before its console existed.
    "soft2": Preset(notes=((660.0, 220.0, 1.0), (880.0, 280.0, 0.92))),
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
    # The shortest cue: a low body plus a 6 ms noise transient.
    "woodblock": Preset(
        notes=((520.0, 120.0, 1.0),),
        partials=((1.0, 1.0), (2.2, 0.25)),
        tau=0.05,
        attack=0.010,
        noise=0.18,
        seed=7,
    ),
    # Inharmonic partials 1 : 2.76 : 5.40, the classic bell signature; the notes ring
    # together instead of following each other.
    "windchime": Preset(
        notes=((1046.5, 420.0, 0.8), (1318.5, 320.0, 0.5)),
        partials=((1.0, 1.0), (2.76, 0.55), (5.40, 0.30)),
        tau=0.38,
        attack=0.015,
        seed=3,
        overlap=True,
    ),
    # A single short blip, for rooms where two notes feel like an alarm.
    "blip": Preset(notes=((880.0, 120.0, 1.0),), tau=0.10),
}


def _note(preset: Preset, freq: float, ms: float, amp: float, rate: int, tau: float) -> np.ndarray:
    """One struck note: partials x exponential decay, attack, 25 ms fade at the end."""
    n = max(1, int(rate * ms / 1000.0))
    t = np.arange(n) / rate
    env = np.minimum(1.0, t / max(preset.attack, 1e-4))
    env *= np.exp(-np.maximum(t - preset.attack, 0.0) / max(tau, 1e-4))
    env *= np.minimum(1.0, np.maximum(0.0, (n / rate - t)) / 0.025)
    sig = np.zeros(n)
    for ratio, weight in preset.partials:
        f = freq * ratio
        if preset.detune_hz:
            sig += weight * 0.5 * np.sin(2 * math.pi * (f - preset.detune_hz) * t)
            sig += weight * 0.5 * np.sin(2 * math.pi * (f + preset.detune_hz) * t + 0.4)
        else:
            sig += weight * np.sin(2 * math.pi * f * t)
    if preset.noise:
        rng = np.random.default_rng(preset.seed)
        burst = rng.standard_normal(n) * np.exp(-np.maximum(t - preset.attack, 0.0) / 0.006)
        sig += preset.noise * burst
    return amp * sig * env


def render(
    name: str = "soft2",
    *,
    rate: int = 48000,
    duration_ms: int = DEFAULT_MS,
    trim: float = 1.0,
) -> np.ndarray:
    """One cue as mono float32 in [-1, 1] at `rate`; `duration_ms=0` keeps the natural one.

    Note lengths and decay constants scale together, so shortening a preset keeps its
    character. An unknown name raises KeyError: a silent fallback would make a typo in the
    config look like a broken speaker.
    """
    preset = PRESETS[name]
    factor = 1.0 if not duration_ms else max(1.0, duration_ms) / preset.natural_ms
    notes = [
        _note(preset, freq, ms * factor, amp, rate, preset.tau * factor)
        for freq, ms, amp in preset.notes
    ]
    if preset.overlap:
        audio = np.zeros(max(n.size for n in notes), dtype=np.float32)
        for i, note in enumerate(notes):
            audio[: note.size] += note * (0.9 if i else 1.0)
    else:
        audio = np.concatenate(notes) if notes else np.zeros(1, dtype=np.float32)
    peak = float(np.max(np.abs(audio)))
    if peak > 0:
        audio = audio * (PEAK / peak)
    return (audio * min(max(trim, 0.0), MAX_TRIM)).astype(np.float32)
