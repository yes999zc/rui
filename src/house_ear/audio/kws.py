"""Wake word: sherpa-onnx keyword spotting on the echo-cancelled mono stream.

Parameters are inherited, not re-tuned: the zipformer KWS model, `chunk 8` (160 ms of
context, chosen for wake latency), the 2.0 boost / `#0.20` threshold keyword file and two
threads are exactly what the old deployment ran in production, where wake at 80 % volume
worked well. Changing them would be a new experiment with a new regression surface.

One behaviour worth knowing: the spotter needs context on both sides of the phrase. A clip
that starts mid-word (or a stream that begins exactly on the first phoneme) can be missed,
which is why the tests pad with silence and production never has to - the microphone is
already running when someone speaks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class KwsUnavailable(RuntimeError):
    """sherpa-onnx or the model files are missing: wake word is off, not silently dead."""


@dataclass(frozen=True)
class WakeHit:
    keyword: str
    ts_ms: float


def model_files(model_dir: Path, chunk: int = 8) -> dict[str, str]:
    tag = f"chunk-{chunk}-left-64"
    files = {
        "tokens": model_dir / "tokens.txt",
        "encoder": model_dir / f"encoder-epoch-13-avg-2-{tag}.int8.onnx",
        "decoder": model_dir / f"decoder-epoch-13-avg-2-{tag}.onnx",
        "joiner": model_dir / f"joiner-epoch-13-avg-2-{tag}.int8.onnx",
    }
    missing = [name for name, path in files.items() if not path.exists()]
    if missing:
        raise KwsUnavailable(f"missing KWS model files in {model_dir}: {', '.join(missing)}")
    return {name: str(path) for name, path in files.items()}


class WakeProbe:
    """A second spotter at a lower threshold that never triggers a turn.

    Same model, same stream, its own decoder state: feeding the *production* stream twice
    would corrupt it, so the probe needs its own. `boost`/threshold live in the keywords
    file (the old build shipped `:3.00 #0.10` for exactly this).
    """

    def __init__(
        self,
        model_dir: Path | str,
        keywords_file: Path | str,
        chunk: int = 8,
        num_threads: int = 1,
        rate: int = 16000,
    ) -> None:
        import sherpa_onnx

        model_dir = Path(model_dir)
        files = model_files(model_dir, chunk)
        self.rate = rate
        self.hits = 0
        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=files["tokens"],
            encoder=files["encoder"],
            decoder=files["decoder"],
            joiner=files["joiner"],
            num_threads=num_threads,
            keywords_file=str(keywords_file),
            provider="cpu",
        )
        self._stream = self._spotter.create_stream()
        self._pending = np.zeros(0, dtype=np.float32)

    def accept(self, pcm: bytes, ts_ms: float = 0.0, rms_db: float | None = None) -> list[str]:
        if not pcm or self._stream is None:
            return []
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, samples])
        out: list[str] = []
        step = self.rate // 50
        while self._pending.size >= step:
            block, self._pending = self._pending[:step], self._pending[step:]
            self._stream.accept_waveform(self.rate, block)
            while self._spotter.is_ready(self._stream):
                self._spotter.decode_stream(self._stream)
                result = self._spotter.get_result(self._stream)
                if result:
                    self.hits += 1
                    out.append(result)
                    log.info(
                        "kws probe (not triggering): %s at ts=%.0f ms, voice %s dBFS",
                        result, ts_ms, "?" if rms_db is None else f"{rms_db:.1f}",
                    )
                    self._spotter.reset_stream(self._stream)
        return out

    def close(self) -> None:
        self._stream = None
        self._spotter = None


class WakeWord:
    """Streaming spotter over 16-bit mono PCM at the voice rate."""

    def __init__(
        self,
        model_dir: Path | str,
        keywords_file: Path | str | None = None,
        chunk: int = 8,
        num_threads: int = 2,
        rate: int = 16000,
    ) -> None:
        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - depends on the install extra
            raise KwsUnavailable(
                "sherpa-onnx is not installed (uv sync --extra kws)"
            ) from exc

        model_dir = Path(model_dir)
        files = model_files(model_dir, chunk)
        keywords = Path(keywords_file) if keywords_file else model_dir / "keywords.txt"
        if not keywords.exists():
            raise KwsUnavailable(f"keywords file not found: {keywords}")

        self.rate = rate
        self.keywords_file = keywords
        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=files["tokens"],
            encoder=files["encoder"],
            decoder=files["decoder"],
            joiner=files["joiner"],
            num_threads=num_threads,
            keywords_file=str(keywords),
            provider="cpu",
        )
        self._stream: object | None = self._spotter.create_stream()
        self._pending = np.zeros(0, dtype=np.float32)
        self.hits = 0
        self.blocks = 0

    def accept(self, pcm: bytes, ts_ms: float = 0.0) -> list[WakeHit]:
        """Feed one block of int16 mono; return the keywords it completed."""
        if not pcm or self._stream is None:
            return []
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, samples])
        out: list[WakeHit] = []
        # KWS is a streaming model: feed in 20 ms steps and drain the decoder after each,
        # otherwise a whole-file feed only produces one chunk of context.
        step = self.rate // 50
        while self._pending.size >= step:
            block, self._pending = self._pending[:step], self._pending[step:]
            self._stream.accept_waveform(self.rate, block)
            while self._spotter.is_ready(self._stream):
                self._spotter.decode_stream(self._stream)
                result = self._spotter.get_result(self._stream)
                if result:
                    self.hits += 1
                    out.append(WakeHit(keyword=result, ts_ms=ts_ms))
                    log.info("wake: %s", result)
                    self._spotter.reset_stream(self._stream)
        self.blocks += 1
        return out

    def reset(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)
        self._spotter.reset_stream(self._stream)

    def close(self) -> None:
        self._stream = None
        self._spotter = None


def total_hits(wake: WakeWord) -> int:
    return wake.hits
