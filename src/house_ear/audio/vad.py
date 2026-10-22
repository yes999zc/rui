"""Turn segmentation: from a wake word to one utterance worth transcribing.

The gate does what the VAD alone cannot: after "你好小瑞" the person keeps talking
immediately, and the wake phrase itself must not reach the transcriber. So it skips a
short tail, waits for the first speech, keeps a small preroll so the onset is not clipped,
and closes the turn after enough silence - with a hard cap so a stuck VAD cannot grow an
endless buffer.

Silero decides speech/no-speech (same model the old deployment used). This class owns the
state machine around it: `is_speech_detected()` per block, our own buffer and timers.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class VadUnavailable(RuntimeError):
    """sherpa-onnx or silero_vad.onnx is missing: no turn segmentation."""


@dataclass
class GateConfig:
    rate: int = 16000
    skip_after_wake_ms: int = 300     # the tail of the wake phrase itself
    preroll_ms: int = 200             # kept before the first detected speech
    min_speech_ms: int = 200          # anything shorter is a cough, not a request
    end_silence_ms: int = 1000        # old build's first-segment value
    # After the first request, the old build listens for follow-ups with a shorter
    # end point (0.5 s) and no wake word. Same here: the plane re-arms the gate
    # when a reply finishes.
    end_silence_followup_ms: int = 500
    max_utterance_ms: int = 45000     # old build: 45 s, one 43.28 s segment measured
    max_wait_ms: int = 5000           # wake with nothing said: give up
    threshold: float = 0.5
    # How much of the acknowledgement window is kept for the moment it lifts: enough for
    # the start of a request spoken over the cue, not the cue itself.
    hold_keep_ms: int = 600


class UtteranceGate:
    """Armed by a wake, closed by silence."""

    def __init__(self, cfg: GateConfig, model_path: str | Path) -> None:
        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - install extra
            raise VadUnavailable("sherpa-onnx is not installed (uv sync --extra kws)") from exc
        model = Path(model_path)
        if not model.exists():
            raise VadUnavailable(f"VAD model not found: {model}")

        self.cfg = cfg
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model)
        config.silero_vad.threshold = cfg.threshold
        # Silero only answers is_speech_detected() here; the gate owns the end point. Its
        # own silence window must be the shorter of the two, or a 500 ms follow-up can
        # never see the silence it counts.
        config.silero_vad.min_silence_duration = (
            min(cfg.end_silence_ms, cfg.end_silence_followup_ms) / 1000.0
        )
        config.silero_vad.min_speech_duration = cfg.min_speech_ms / 1000.0
        config.sample_rate = cfg.rate
        self._vad = sherpa_onnx.VoiceActivityDetector(
            config, buffer_size_in_seconds=max(30, cfg.max_utterance_ms // 1000 + 5)
        )
        self.rate = cfg.rate
        self.armed = False
        self.turns = 0
        self.discarded = 0
        # A second wake while a turn was still open: its buffer is thrown away by arm(),
        # so it is counted here instead of vanishing without a trace.
        self.interrupted = 0
        self._reset_state()

    def _reset_state(self) -> None:
        self._skipping = 0
        self._hold_ms = 0.0
        self._hold_buf: deque[np.ndarray] = deque()
        self._hold_samples = 0
        self._preroll: deque[np.ndarray] = deque()
        self._preroll_samples = 0
        self._buf: list[np.ndarray] = []
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._waited_ms = 0.0
        self._started = False
        self._end_silence_ms = self.cfg.end_silence_ms
        self._max_wait_ms = self.cfg.max_wait_ms

    @property
    def speech_ms(self) -> float:
        """Speech gathered for the open turn: what a second wake is about to throw away."""
        return self._speech_ms

    # ------------------------------------------------------------------ state
    def arm(self, *, start_after_ms: float = 0.0, followup: bool = False,
            max_wait_ms: int | None = None) -> None:
        """A wake word just fired: ignore the phrase tail, then listen.

        `start_after_ms` holds the gate shut for the acknowledgement: a spoken cue mutes
        the device microphone, so anything the VAD sees meanwhile is the box's own voice.
        The hold is spent in audio time, not wall time, so the same number means the same
        thing at any feed speed.
        """
        self._vad.reset()
        self._reset_state()
        self.armed = True
        self._hold_ms = max(0.0, start_after_ms)
        # A follow-up has no wake phrase to skip and closes faster: the person already
        # knows the assistant is listening, so a natural pause must not hold the turn.
        self._skipping = 0 if followup else int(self.rate * self.cfg.skip_after_wake_ms / 1000)
        self._end_silence_ms = (
            self.cfg.end_silence_followup_ms if followup else self.cfg.end_silence_ms
        )
        # A follow-up window lasts as long as the session, not the 5 s "nobody spoke
        # after the wake" limit: 9哥 said 退下吧 six seconds in and the gate had shut.
        self._max_wait_ms = max_wait_ms if max_wait_ms is not None else self.cfg.max_wait_ms

    def disarm(self) -> None:
        self.armed = False
        self._vad.reset()
        self._reset_state()

    def hold(self, ms: float) -> None:
        """The speaker is still sounding: listen, but do not open a turn yet.

        Separate from `arm()` because the pre-wake replay belongs before the hold: those
        blocks are the person's own words, captured while the microphone was still open.

        Blocks that arrive *during* the hold are buffered, not dropped. A deaf window is
        how the start of the request gets eaten: the old build hit exactly that and fixed
        it with an echo-tail buffer, so the cue's echo cannot open a turn while the words
        spoken over it still reach the transcriber once the hold lifts.
        """
        self._hold_ms = max(0.0, ms)

    # ------------------------------------------------------------------ input
    def feed(self, pcm: bytes, ts_ms: float = 0.0) -> tuple[bytes, float] | None:
        """int16 mono block in; the finished utterance (int16) out when it closes."""
        if not self.armed or not pcm:
            return None
        block = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if self._hold_ms > 0:
            self._hold_buf.append(block)
            self._hold_samples += block.size
            # Only the last `hold_keep_ms` of the hold is kept. The cue's own echo sits at
            # the *start* of the hold and a person answers at its end, so keeping the newest
            # audio recovers the request without feeding our own tone to the VAD.
            limit = int(self.rate * self.cfg.hold_keep_ms / 1000)
            while self._hold_samples > limit and len(self._hold_buf) > 1:
                self._hold_samples -= self._hold_buf.popleft().size
            self._hold_ms -= block.size * 1000.0 / self.rate
            if self._hold_ms > 0:
                return None
            # Hold lifted: hand every buffered block through the normal path, oldest first,
            # and stop as soon as one of them closes a turn.
            pending = list(self._hold_buf)
            self._hold_buf.clear()
            self._hold_samples = 0
            out = None
            for buffered in pending:
                out = self._consume(buffered, ts_ms)
                if out is not None:
                    return out
            return None
        return self._consume(block, ts_ms)

    def _consume(self, samples: np.ndarray, ts_ms: float) -> tuple[bytes, float] | None:
        """One block through the VAD, the wake-tail skip and the segment state machine."""
        # The VAD always sees the whole stream, including the wake phrase tail: resetting
        # it at the wake and starting mid-word is how the first syllable gets missed.
        self._vad.accept_waveform(samples)
        speaking = bool(self._vad.is_speech_detected())
        if self._skipping:
            skip = min(self._skipping, samples.size)
            samples = samples[skip:]
            self._skipping -= skip
            if samples.size == 0:
                return None

        block_ms = samples.size * 1000.0 / self.rate

        if not self._started:
            self._preroll.append(samples)
            self._preroll_samples += samples.size
            limit = int(self.rate * self.cfg.preroll_ms / 1000)
            while self._preroll_samples > limit and len(self._preroll) > 1:
                self._preroll_samples -= self._preroll.popleft().size
            if not speaking:
                self._waited_ms += block_ms
                if self._waited_ms >= self._max_wait_ms:
                    log.info("wake with no speech after %.0f ms; disarming", self._waited_ms)
                    self.discarded += 1
                    self.disarm()
                return None
            # First speech: include the preroll so the onset is not chopped.
            self._started = True
            self._buf = list(self._preroll)
            self._preroll.clear()
            self._preroll_samples = 0
            self._speech_ms = sum(b.size for b in self._buf) * 1000.0 / self.rate
            self._silence_ms = 0.0
            return None

        self._buf.append(samples)
        if speaking:
            self._silence_ms = 0.0
            self._speech_ms += block_ms
        else:
            self._silence_ms += block_ms

        if self._silence_ms < self._end_silence_ms and self._speech_ms < self.cfg.max_utterance_ms:
            return None

        audio = np.concatenate(self._buf) if self._buf else np.zeros(0, dtype=np.float32)
        # Trailing silence adds nothing for the transcriber; keep 100 ms of it.
        trim = int(self.rate * max(0.0, self._silence_ms - 100) / 1000)
        if trim and trim < audio.size:
            audio = audio[: audio.size - trim]
        speech_ms = self._speech_ms
        # The old build logged every segment's level, and that line is how its 0.25/0.8
        # ASR normalisation was tuned. Without it, an end-point or ASR A/B has to be
        # judged by ear or by keeping wav files. rms/peak are on the [-1, 1] scale the
        # ASR sees, not dBFS.
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        log.info("[seg] %.2f s rms=%.4f peak=%.4f speech=%.2f s",
                 audio.size / self.rate, rms, peak, speech_ms / 1000.0)
        self.disarm()
        if speech_ms < self.cfg.min_speech_ms:
            log.info("utterance discarded: %.0f ms of speech", speech_ms)
            self.discarded += 1
            return None
        self.turns += 1
        return np.clip(audio * 32768.0, -32768, 32767).astype("<i2").tobytes(), ts_ms
