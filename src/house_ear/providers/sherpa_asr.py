"""Local ASR with sherpa-onnx SenseVoice, with the cloud entry behind it in the chain.

Measured on the dev Mac: 51-54 ms warm for a 1.75 s clip (rtf 0.03); the same clip went to
the cloud endpoint and came back in 0.38-2.5 s with a 20 s timeout in the tail. Speech
recognition is the one stage where a local model wins outright, so it is first in the chain
and the cloud entry stays as the fallback for a box where the model is not downloaded.

The deployment keeps the model out of the image: `house-ear fetch-models` writes it to a
data volume, and a missing model is a fallback to the cloud rather than a failed startup.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..config.schema import ProviderConfig
from .base import Transcript

log = logging.getLogger(__name__)


class ModelMissing(RuntimeError):
    pass


def model_paths(model_dir: str | Path) -> tuple[Path, Path]:
    """(onnx, tokens) for a SenseVoice model directory, preferring the int8 weights."""
    base = Path(model_dir)
    onnx = base / "model.int8.onnx"
    if not onnx.exists():
        onnx = base / "model.onnx"
    return onnx, base / "tokens.txt"


class SherpaAsr:
    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg
        self._recognizer = None

    def _load(self):
        if self._recognizer is not None:
            return self._recognizer
        onnx, tokens = model_paths(self.cfg.model)
        if not onnx.exists() or not tokens.exists():
            raise ModelMissing(f"sensevoice model not found in {self.cfg.model!r}")
        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - depends on the image
            raise ModelMissing("sherpa-onnx is not installed in this image") from exc

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(onnx),
            tokens=str(tokens),
            num_threads=int(self.cfg.threads),
            use_itn=True,
            language="zh",
            debug=False,
        )
        log.info("local asr ready: %s (threads=%d)", onnx.name, self.cfg.threads)
        return self._recognizer

    def _decode(self, pcm: bytes, sample_rate: int) -> str:
        import numpy as np

        recognizer = self._load()
        samples = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        recognizer.decode_stream(stream)
        return (stream.result.text or "").strip()

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> Transcript:
        # decoding holds the GIL inside C++; keep it off the event loop
        text = await asyncio.to_thread(self._decode, pcm, sample_rate)
        return Transcript(text=text, language="zh")

    async def warm(self) -> None:
        """Load the model before the first user speaks.

        Model load measured 0.5 s (Mac) to 1.3-1.8 s (N100). Paying that on the first
        utterance of a session looks exactly like slow ASR, so the control plane warms it
        at startup.
        """
        try:
            await asyncio.to_thread(self._load)
        except Exception as exc:  # noqa: BLE001 - missing model is a fallback, not a crash
            log.warning("local asr not warmed: %s", exc)

    async def aclose(self) -> None:
        self._recognizer = None
