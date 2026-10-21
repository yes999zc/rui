"""The wake acknowledgement: choose the audio and say which mode really ran.

Two sources, one contract. `earcon` is synthesised here (no I/O, ~1 ms). `tts` is a wav
the control plane pre-rendered into the data volume, because synthesising on the wake
would arrive a second late and be the very latency the cue exists to hide.

The cap is enforced here, at load time: a spoken cue longer than `ack.max_ms` is refused
and the tone is used instead. That is what makes "TTS ack ≤ 600 ms" a property of the
build rather than a note in a document.
"""
from __future__ import annotations

import logging
import wave
from pathlib import Path

import numpy as np

from ..config.schema import Config
from . import chime
from .resample import resample
from .wav import read_wav_mono

log = logging.getLogger(__name__)

ACK_FILE = "ack.wav"


def ack_wav_path(cfg: Config) -> Path:
    """Where the control plane leaves the pre-rendered spoken cue."""
    return Path(cfg.system.data_dir) / ACK_FILE


def tone(cfg: Config) -> np.ndarray:
    """The fallback cue, always renderable: no file, no network, no failure mode."""
    rate = cfg.audio.playback.rate
    try:
        return chime.render(
            cfg.ack.chime,
            rate=rate,
            duration_ms=cfg.ack.chime_ms,
            trim=cfg.ack.chime_trim,
        )
    except KeyError:
        log.warning("ack: unknown chime preset %r, using soft2", cfg.ack.chime)
        return chime.render("soft2", rate=rate, duration_ms=cfg.ack.chime_ms,
                            trim=cfg.ack.chime_trim)


def render(cfg: Config) -> tuple[np.ndarray, str, str]:
    """(audio at the playback rate, the mode actually used, why it was downgraded).

    Never raises: a missing or oversized file is a downgrade to the tone with a reason
    attached, so the console can show "tts → earcon: ack.wav is 940 ms" instead of
    silently sounding different from what the config says.
    """
    if cfg.ack.mode == "none":
        return np.zeros(0, dtype=np.float32), "none", ""
    note = ""
    if cfg.ack.mode == "tts":
        path = ack_wav_path(cfg)
        try:
            audio, rate = read_wav_mono(path)
        except (OSError, wave.Error) as exc:
            note = f"{ACK_FILE} unreadable ({type(exc).__name__}): using a tone"
        else:
            duration_ms = audio.size * 1000.0 / rate
            if duration_ms > cfg.ack.max_ms:
                note = (
                    f"{ACK_FILE} is {duration_ms:.0f} ms, over the {cfg.ack.max_ms} ms cap:"
                    " using a tone"
                )
            elif audio.size == 0:
                note = f"{ACK_FILE} is empty: using a tone"
            else:
                target = cfg.audio.playback.rate
                return resample(audio, rate, target), "tts", ""
        log.info("ack: %s", note)
    return tone(cfg), "earcon", note
