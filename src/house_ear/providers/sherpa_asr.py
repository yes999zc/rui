"""Local ASR with sherpa-onnx SenseVoice.

Cloud ASR has a floor of a few hundred milliseconds and a tail that is far worse: the same
1.2 s clip measured 0.46 s, 2.1 s and then a 20 s timeout inside one afternoon. A wake-up
assistant cannot absorb that, so speech recognition defaults to a local model and the cloud
entry stays in the chain as a fallback for boxes where the local model is not installed.

The model directory is `cfg.model`; `tokens.txt` is read from next to the onnx file.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..config.schema import ProviderConfig
from .base import Transcript

log = logging.getLogger(__name__)


class SherpaUnavailable(RuntimeError):
    pass


class SherpaAsr:
    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg
        self._recognizer = None

    def _load(self):
        if self._recognizer is not None:
            return self._recognizer
        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - depends on the image
            raise SherpaUnavailable("sherpa-onnx is not installed") from exc

        model_dir = Path(self.cfg.model)
        onnx = model_dir / "model.int8.onnx"
        if not onnx.exists():
            onnx = model_dir / "model.onnx"
        tokens = model_dir / "tokens.txt"
        if not onnx.exists() or not tokens.exists():
            raise SherpaUnavailable(f"sensevoice model not found under {model_dir}")

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(onnx),
            tokens=str(tokens),
            num_threads=2,
            use_itn=True,
            language="zh",
            debug=False,
        )
        log.info("local asr ready: %s", onnx.name)
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
        # decoding holds the GIL in C++; keep it off the event loop
        text = await asyncio.to_thread(self._decode, pcm, sample_rate)
        return Transcript(text=text, language="zh")

    async def aclose(self) -> None:
        self._recognizer = None
