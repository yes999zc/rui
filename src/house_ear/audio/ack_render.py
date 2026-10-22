"""Render the spoken wake acknowledgement once, ahead of time.

The cue exists to hide latency, so it cannot itself cost latency. On the LX06 a wake
answer synthesised on demand took ~1.5 s and arrived after the user had already started
talking - and the old build found the queued audio then hit its echo gate at the wrong
moment and swallowed the first request. Its fix (`assistant_v3.py:1111`, `ack_warmer`) was
to synthesise in the background, keyed on the TTS config plus the text, and play the
cached bytes; this is the same arrangement with the file in `system.data_dir` so the audio
plane (a separate process) can read it.

The `.stamp` sidecar is what makes "control plane rewrites it at any time" safe: the audio
plane already keys its cache on the wav's mtime and size, and this file stops us paying
for a synthesis on every startup.
"""
from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path

import numpy as np

from ..config.schema import Config
from ..providers.chain import tts_chain
from .ack import ack_wav_path
from .wav import trim_silence, write_wav_mono

log = logging.getLogger(__name__)

STAMP_FILE = "ack.stamp"


def _stamp(cfg: Config, voice: str) -> str:
    """Everything that changes the audio. A new voice or a new TTS vendor must re-render."""
    return json.dumps(
        {
            "text": cfg.ack.text,
            "voice": voice,
            "providers": cfg.providers.model_dump(mode="json"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


async def render_ack_wav(cfg: Config) -> Path | None:
    """Leave `ack.wav` in the data dir. Returns None when there is nothing to render.

    Never raises: a broken TTS must not stop the control plane from starting. The audio
    plane falls back to its tone and reports the downgrade, which is visible in the console
    instead of being a silent difference between config and behaviour.
    """
    if cfg.ack.mode != "tts" or not cfg.ack.text.strip():
        return None
    path = ack_wav_path(cfg)
    stamp_path = path.parent / STAMP_FILE
    chain = tts_chain(cfg.providers, cfg.audio.playback.rate)
    if not chain:
        log.warning("ack: mode is tts but no tts provider is configured")
        return None

    errors: list[str] = []
    for name, provider in chain:
        voice = getattr(getattr(provider, "cfg", None), "voice", "") or ""
        stamp = _stamp(cfg, f"{name}|{voice}")
        try:
            if path.exists() and stamp_path.read_text(encoding="utf-8") == stamp:
                return path
        except OSError:
            pass
        try:
            pcm = b"".join([chunk async for chunk in provider.synthesize(cfg.ack.text, voice)])
        except Exception as exc:  # noqa: BLE001 - the next provider may work
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            log.warning("ack: %s failed: %s", name, exc)
            continue
        if not pcm:
            errors.append(f"{name}: empty audio")
            continue
        rate = int(getattr(provider, "sample_rate", cfg.audio.playback.rate))
        trimmed = trim_silence(pcm, rate)
        duration_ms = len(trimmed) / 2 / rate * 1000
        if duration_ms > cfg.ack.max_ms:
            # Refused here as well as at playback: better to say so in the log than to have
            # the plane silently swap in a tone every wake.
            log.warning("ack: %r is %.0f ms, over the %d ms cap - the plane will use a tone",
                        cfg.ack.text, duration_ms, cfg.ack.max_ms)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_wav_mono(path, trimmed, rate)
        stamp_path.write_text(stamp, encoding="utf-8")
        log.info("ack: rendered %r -> %s (%.2f s, %d Hz)", cfg.ack.text, path.name,
                 duration_ms / 1000, rate)
        return path
    log.warning("ack: every tts provider failed: %s", errors)
    return None


async def render_filler_pcm(cfg: Config, text: str | None = None) -> bytes:
    """Synthesise `assistant.filler_text` once, into memory.

    The filler exists to cover a wait, so it cannot pay the vendor's first-byte latency
    (measured 0.5-0.9 s on this box) at the moment it is needed: 1.8 s of silence plus
    0.9 s of synthesis is the 2.5 s gap 9哥 hears. Rendering it at startup turns the
    filler into a memcpy. Kept in memory, not a file, because only the control plane
    speaks it.
    """
    text = (text if text is not None else cfg.assistant.filler_text or "").strip()
    if not text or cfg.assistant.filler_after_s <= 0:
        return b""
    for name, provider in tts_chain(cfg.providers, cfg.audio.voice.rate):
        try:
            buf = bytearray()
            async with contextlib.aclosing(provider.synthesize(text, "")) as stream:
                async for chunk in stream:
                    if chunk:
                        buf.extend(chunk)
        except Exception as exc:  # noqa: BLE001 - a filler is never worth an outage
            log.warning("filler: %s failed: %s", name, exc)
            continue
        if buf:
            pcm = trim_silence(bytes(buf), cfg.audio.voice.rate)
            log.info("filler: rendered %r with %s (%.2f s)", text, name,
                     len(pcm) / 2 / cfg.audio.voice.rate)
            return pcm
    log.warning("filler: no provider produced audio; the gap stays")
    return b""


def pcm_of(path: Path) -> np.ndarray:
    """Read the rendered cue as float samples (tests and the console preview)."""
    from .wav import read_wav_mono

    audio, _rate = read_wav_mono(path)
    return audio
