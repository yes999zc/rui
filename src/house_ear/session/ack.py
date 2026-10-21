"""Pre-render the spoken wake acknowledgement into the data volume.

Synthesising on the wake would arrive about a second late, which is exactly the latency
the cue exists to hide. So the control plane renders it once - at startup, or whenever the
console changes the text - and the audio plane plays the file with no network in the path.

The cap is enforced at render time: an `ack_text` that takes longer than `ack.max_ms` is
refused, the file is removed, and the plane falls back to the tone. "TTS ack ≤ 600 ms" is
therefore a property of the build instead of a sentence in a document.
"""
from __future__ import annotations

import logging

from ..audio.ack import ack_wav_path
from ..audio.wav import write_wav_mono
from ..config.schema import Config
from ..providers.chain import tts_chain

log = logging.getLogger(__name__)


async def synthesize(cfg: Config, text: str) -> tuple[bytes, int, str]:
    """(pcm, rate, provider) from the first TTS provider that produces audio."""
    rate = cfg.audio.voice.rate
    attempts: list[str] = []
    for name, provider in tts_chain(cfg.providers, rate):
        chunks: list[bytes] = []
        try:
            async for chunk in provider.synthesize(text, ""):
                if chunk:
                    chunks.append(chunk)
        except Exception as exc:  # noqa: BLE001 - the chain exists for exactly this
            attempts.append(f"{name}: {type(exc).__name__}: {exc}")
            log.warning("ack tts: %s failed: %s", name, exc)
            continue
        if chunks:
            return b"".join(chunks), rate, name
        attempts.append(f"{name}: no audio")
    raise RuntimeError("; ".join(attempts) or "no tts provider configured")


async def ensure_ack_wav(cfg: Config) -> str:
    """Render (or remove) the spoken cue. Returns what happened, for the console."""
    path = ack_wav_path(cfg)
    if cfg.ack.mode != "tts":
        return f"mode is {cfg.ack.mode}: the plane uses a tone, no file needed"
    try:
        pcm, rate, provider = await synthesize(cfg, cfg.ack.text)
    except RuntimeError as exc:
        path.unlink(missing_ok=True)
        log.warning("ack: %s", exc)
        return f"tts failed, plane will use a tone: {exc}"
    duration_ms = len(pcm) / 2.0 * 1000.0 / rate
    if duration_ms > cfg.ack.max_ms:
        # Refusing is the point of the cap: a long cue mutes the microphone and eats the
        # request it was supposed to introduce.
        path.unlink(missing_ok=True)
        return (
            f"{cfg.ack.text!r} renders to {duration_ms:.0f} ms, over the {cfg.ack.max_ms} ms"
            " cap: plane will use a tone"
        )
    write_wav_mono(path, pcm, rate)
    return f"ack.wav written from {provider}: {duration_ms:.0f} ms at {rate} Hz -> {path}"
