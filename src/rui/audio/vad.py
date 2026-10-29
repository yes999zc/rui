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

# The cue is handed to the device at `hold()`, but the device's own player opens
# later: the old build measured aplay pipe + one ALSA buffer as PLAY_LAG_SECONDS =
# 0.55 s, and a device that also waits for its buffer threshold starts even later.
# The gate therefore searches the whole hold window for the cue (not just the nominal
# skip window), but only up to this start. A later start is far more likely to be the
# person's reply than the cue: `test_a_device_that_mutes_during_the_cue_is_unaffected`
# feeds the reply at the skip boundary (950 ms), so the bound stays below it.
# The 2026-10-28 P0 cue (Dot 2, 740-850 ms) is beyond this bound; it is recovered by
# the narrow re-search around the measured-latency prior instead (see `_cue_echo`).
CUE_MATCH_START_MAX_MS = 800.0
# If the cue is clipped/overlapped/late it cannot be located (the envelope is no longer
# the cue's rhythm, or its onset is past the search bound). The person is talking over
# our cue, so the only safe choice is to drop the cue's worst-case window and keep what
# follows. 1000 ms covers the measured worst case (2026-10-26: the Dot2 makes a 540 ms
# cue audible ~740-850 ms after the handover) *and* the 950 ms boundary: the whole cue
# is removed even when it never reaches the skip window, while a reply starting after
# the cue still survives. A reply inside this window is lost with it (fallback=true).
CUE_OVERLAP_GUARD_MS = 1000.0
# A matched cue ends on its last frame; room reverb and the device's buffer keep it
# ringing a little longer. Small enough that a reply started right after the cue
# keeps its first syllable.
CUE_REVERB_GUARD_MS = 40.0
# Full-scale samples: the person talking over the cue clipped the microphone (measured
# 2026-10-26 07:01:44, peak=1.0000). Together with a failed cue match this is the
# evidence that the cue window is contaminated and must be excised conservatively.
CUE_CLIP_LEVEL = 0.99
# The search now covers the whole hold window, so a marginal envelope match is far more
# likely to be the person's reply than the cue: require a clear match before treating a
# window as cue. `GateConfig.echo_match_min` stays the floor for a stricter caller.
CUE_MATCH_MIN = 0.75
# Learned device cue latency: milliseconds from the handover (hold start) to the cue's
# first audible block. The Dot2's prior is the measured 740 ms; a per-process EMA
# tracks a unit that is slower or faster. The first-pass match is bounded at 800 ms so
# the person's reply is never matched; a late cue that the bound misses is re-searched
# only in a narrow window around the learned latency, with a stricter match and a
# silent run before its onset. A device that muted its microphone has no cue there, so
# nothing matches and its reply is kept.
CUE_LAG_INITIAL_MS = 740.0
CUE_LAG_ALPHA = 0.3
CUE_LAG_MIN_MS = 300.0
CUE_LAG_MAX_MS = 1400.0
CUE_LAG_WINDOW_MS = 200.0
CUE_LAG_REMATCH_MIN = 0.90
CUE_ONSET_SILENCE_MS = 100.0
# A cue echo is followed by the device going quiet until the person speaks; a reply
# that the narrow match mistook for the cue keeps sounding past the cue's length.
CUE_POST_SILENCE_MS = 100.0
# A cue starts with sound. The first 100 ms of a match must carry at least this share
# of the reference's loudest block, or the "match" is the silence before the person's
# reply lining up with the cue's tail (a false positive that would cut the reply).
CUE_ONSET_MS = 100.0
CUE_ONSET_RATIO = 0.1
# T5: sample-level clipping in one utterance. At or above this magnitude a sample is
# treated as saturated (0.97 leaves headroom for dither/DC offset); a segment with a
# larger clipped share gets `clipped=NN%` appended to its [seg] line. Diagnostic only:
# the transcript pipeline is not changed.
CLIP_SAMPLE = 0.97
CLIP_LOG_RATIO = 0.05


class VadUnavailable(RuntimeError):
    """sherpa-onnx or silero_vad.onnx is missing: no turn segmentation."""


def _sherpa_unavailable(exc: BaseException) -> str:
    """Missing wheel vs present-but-unloadable C library, with the loader's own text."""
    text = " ".join(str(exc).split())
    if not text:
        text = type(exc).__name__
    elif len(text) > 320:
        text = text[:319] + "…"
    missing = (getattr(exc, "name", None) or "").split(".")[0]
    if isinstance(exc, ModuleNotFoundError) and missing in (
        "", "sherpa_onnx", "sherpa_onnx_core"
    ):
        return "sherpa-onnx is not installed (uv sync --extra kws)"
    return f"sherpa-onnx is installed but failed to load: {text}"


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
    # Cue-echo alignment (see `UtteranceGate.set_echo_reference`): envelope block size,
    # and how similar an observed block sequence must be to the cue before the gate
    # believes the device was still playing it when the skip window ended. Below the
    # minimum the gate keeps its pre-existing behaviour, so a device that mutes its
    # microphone during the cue is unaffected.
    echo_block_ms: int = 20
    echo_match_min: float = 0.6


class UtteranceGate:
    """Armed by a wake, closed by silence."""

    def __init__(self, cfg: GateConfig, model_path: str | Path) -> None:
        try:
            import sherpa_onnx
        except (ImportError, OSError) as exc:  # pragma: no cover - install extra
            raise VadUnavailable(_sherpa_unavailable(exc)) from exc
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
        # Learned across arms: the device's cue latency and whether a match has ever
        # confirmed it. Until then the measured prior is used for the narrow re-search
        # (the first wake of a fresh process must find a late cue too); only confirmed
        # first-pass matches teach the EMA.
        self._cue_lag_ms = CUE_LAG_INITIAL_MS
        self._cue_lag_seen = False
        self._last_match_offset_ms: float | None = None
        # Counts silent wakes across arms: disarm() resets the per-arm state, and the
        # plane compares this counter before/after each block to send WakeIdle.
        self.idle_events = 0

    def _reset_state(self) -> None:
        self._skipping = 0
        self._hold_ms = 0.0
        # The cue itself (and its echo) is not the person: those first milliseconds are
        # skipped instead of replayed into the VAD, or ASR transcribes our own 「在呢」
        # as a request (measured 2026-10-23 15:49/15:53/15:55).
        self._hold_skip_ms = 0.0
        # The loudness envelope of the skipped (cue) samples, matched against the cue's
        # own envelope when the hold lifts; see `_cue_echo`.
        self._hold_skip_samples: list[np.ndarray] = []
        self._echo_env = None
        # Cue duration and the nominal skip/hold windows: the fallback below needs them
        # after the skip counter has been spent.
        self._cue_ms = 0.0
        self._hold_skip_total_ms = 0.0
        self._hold_total_ms = 0.0
        # True once any part of the cue/echo window was discarded. Diagnostic only: a
        # wake that then hears nothing reports the silent timeout either way, because
        # the cue is our own voice, not an utterance (see the timeout branch in feed).
        self._cue_excised = False
        self._hold_buf: deque[np.ndarray] = deque()
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
    def speaking(self) -> bool:
        """True while a request/follow-up is being spoken into the gate.

        `armed` alone is the whole listening window (up to 15 s of waiting for the next
        sentence); a silence timeout must not count that as activity, or the goodbye
        arrives 15 s too late. Only actual speech extends the idle clock.
        """
        return self._started

    @property
    def speech_ms(self) -> float:
        """Speech gathered for the open turn: what a second wake is about to throw away."""
        return self._speech_ms

    # ------------------------------------------------------------------ state
    def arm(self, *, start_after_ms: float = 0.0, followup: bool = False,
            max_wait_ms: int | None = None,
            skip_after_wake_ms: int | None = None) -> None:
        """A wake word just fired: ignore the phrase tail, then listen.

        `start_after_ms` holds the gate shut for the acknowledgement: a spoken cue mutes
        the device microphone, so anything the VAD sees meanwhile is the box's own voice.
        The hold is spent in audio time, not wall time, so the same number means the same
        thing at any feed speed.

        `skip_after_wake_ms` overrides the configured tail skip for this arm. RUI's KWS
        reports the hit after the phrase, so it skips 300 ms; a device wake reports
        `DeviceWake.ts_ms` and the phrase tail is not in our stream, so the plane passes
        0 (Socket & Plug v1 §5).
        """
        self._vad.reset()
        self._reset_state()
        self.armed = True
        self._hold_ms = max(0.0, start_after_ms)
        # A follow-up has no wake phrase to skip and closes faster: the person already
        # knows the assistant is listening, so a natural pause must not hold the turn.
        if skip_after_wake_ms is None:
            skip_ms = 0 if followup else int(self.cfg.skip_after_wake_ms)
        else:
            skip_ms = max(0, int(skip_after_wake_ms))
        self._skipping = int(self.rate * skip_ms / 1000)
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

    def hold(self, ms: float, skip_ms: float = 0.0) -> None:
        """The speaker is still sounding: listen, but do not open a turn yet.

        Separate from `arm()` because the pre-wake replay belongs before the hold: those
        blocks are the person's own words, captured while the microphone was still open.

        `skip_ms` is the cue's own playing time: audio from that window is the box's voice,
        not the person, and replaying it let ASR transcribe our own 「在呢」 as a request
        (measured 2026-10-23 15:49/15:53/15:55, three sessions in a row). Everything after
        the cue is buffered instead of dropped: keeping only a 600 ms tail lost the first
        ~1.1 s of a request that started right after the cue (13:47 「放一首告白气球」 arrived
        as 「告白气球」). The buffer is bounded by the echo window (~1.2 s).

        `skip_ms` is the *nominal* cue length. A device's playback latency can move the
        cue past that boundary; `set_echo_reference` lets the gate measure how much of the
        cue was still unplayed and drop only that much from the buffer.
        """
        self._hold_ms = max(0.0, ms)
        self._hold_skip_ms = max(0.0, min(skip_ms, ms))
        self._hold_total_ms = self._hold_ms
        self._hold_skip_total_ms = self._hold_skip_ms
        self._hold_skip_samples.clear()

    def set_echo_reference(self, samples: np.ndarray, rate: int) -> None:
        """Remember the cue that is about to play, so its echo is not taken for speech.

        The skip window covers the cue at its nominal length, but a device cannot start
        playing the instant the bytes arrive: when its playback buffer is deeper, the
        cue is still sounding after the skip ends, and the buffered echo window replayed
        that tail into ASR as a user turn (2026-10-26: cue 「在呢」 -> turn 「在呢。」 ->
        reply 「怎么了？」). Matching loudness envelopes rather than waveforms survives the
        device AEC's attenuation and phase shift, and needs no per-device latency
        constant: the cue's own syllable rhythm says where the cue sits.
        """
        envelope = self._envelope(np.asarray(samples, dtype=np.float32).reshape(-1), rate)
        # Relative levels only: the caller may hand the cue as float PCM [-1, 1] or as
        # raw int16 (tests, a plug's WAV), and the microphone's echo is attenuated.
        peak = float(np.max(envelope)) if envelope.size else 0.0
        self._echo_env = envelope / peak if peak > 0.0 else None
        self._cue_ms = envelope.size * self.cfg.echo_block_ms

    def _envelope(self, samples: np.ndarray, rate: int) -> np.ndarray:
        """RMS per `echo_block_ms` block: the rhythm of a sound, not its waveform."""
        block = max(1, int(rate * self.cfg.echo_block_ms / 1000))
        usable = samples.size - samples.size % block
        if usable <= 0:
            return np.zeros(0, dtype=np.float32)
        frames = samples[:usable].reshape(-1, block)
        return np.sqrt(np.mean(np.square(frames), axis=1)).astype(np.float32)

    def _cue_echo(
        self, buffered: list[np.ndarray]
    ) -> tuple[float, float, float, bool, bool]:
        """Locate the cue in the skipped + buffered audio of this hold.

        Returns ``(cue_start_ms, cue_end_ms, match_score, matched, contaminated)`` where
        the times are relative to the hold start and ``contaminated`` means the cue may
        be present but could not be located (clipped capture, or cue energy in the skip
        window). The whole held window is searched, not
        just the nominal skip window: a slow device can put the entire cue inside the
        echo buffer, where the old skip-only search saw nothing and replayed all of it
        (2026-10-26 07:01: cue 540 ms -> turn 「在呢。」 -> 「我在，你说。」).

        A clipped or overlapped window cannot be matched (the envelope is no longer the
        cue's rhythm) and reports ``matched=False, contaminated=True``; the caller then
        applies the conservative overlap window instead of replaying unknown audio.
        """
        self._last_match_offset_ms = None
        if self._echo_env is None:
            return 0.0, 0.0, 0.0, False, False
        blocks = list(self._hold_skip_samples) + list(buffered)
        if not blocks:
            return 0.0, 0.0, 0.0, False, False
        raw = np.concatenate(blocks)
        observed = self._envelope(raw, self.rate)
        reference = self._echo_env
        if observed.size == 0 or reference.size == 0:
            return 0.0, 0.0, 0.0, False, False
        # Same relative scale as the stored reference: the echo is attenuated by the
        # device AEC and the capture gain is arbitrary.
        observed_peak = float(np.max(observed))
        if observed_peak > 0.0:
            observed = observed / observed_peak
        clipped = float(np.max(np.abs(raw))) >= CUE_CLIP_LEVEL
        skip_env = self._envelope(
            np.concatenate(self._hold_skip_samples), self.rate
        ) if self._hold_skip_samples else np.zeros(0, dtype=np.float32)
        skip_peak = float(np.max(skip_env)) if skip_env.size else 0.0
        heard_any = bool(
            skip_peak > 0.0
            and skip_peak >= CUE_ONSET_RATIO * float(np.max(observed))
        )
        best_score, best_offset = self._match_cue(
            observed, reference, 0.0, CUE_MATCH_START_MAX_MS)
        if best_score < max(self.cfg.echo_match_min, CUE_MATCH_MIN):
            # The bounded first pass missed. A late cue lives just past that bound: look
            # only in a narrow window around the measured latency, with a stricter match
            # and a silent run before its onset. A muted device has no cue there, so
            # nothing matches and the person's reply is kept. This runs on the first wake
            # too, using the measured prior (740 ms): before 2026-10-28 it waited for a
            # confirmed match to learn a lag, so the very first cue of a fresh process -
            # the Dot 2 starts it 740-850 ms late - had no way to be found and was
            # replayed into ASR (11:01:30 「在呢？」). The strict match plus the
            # quiet-after check is what keeps a person's reply out of it; a false hit
            # cannot teach the EMA because this path does not call `_learn_lag`.
            low = max(0.0, self._cue_lag_ms - CUE_LAG_WINDOW_MS)
            high = self._cue_lag_ms + CUE_LAG_WINDOW_MS
            narrow, narrow_offset = self._match_cue(
                observed, reference, low, high, silent_before=True)
            if (narrow >= CUE_LAG_REMATCH_MIN
                    and self._quiet_after(observed, reference, narrow_offset)):
                # A narrow hit must be a cue, not the person's reply: the cue echo
                # is followed by the device going quiet, while a reply keeps
                # sounding past the cue's length. It does NOT teach the latency -
                # a mistaken narrow hit would reinforce its own error.
                self._last_match_offset_ms = narrow_offset * self.cfg.echo_block_ms
                return 0.0, 0.0, narrow, False, True
            return 0.0, 0.0, best_score, False, clipped or heard_any
        cue_start_ms = best_offset * self.cfg.echo_block_ms
        self._last_match_offset_ms = cue_start_ms
        cue_end_ms = cue_start_ms + reference.size * self.cfg.echo_block_ms
        self._learn_lag(cue_start_ms)
        return cue_start_ms, cue_end_ms, best_score, True, clipped

    def _quiet_after(
        self, observed: np.ndarray, reference: np.ndarray, offset: int,
    ) -> bool:
        """True when the audio right after the matched cue goes quiet."""
        quiet_blocks = max(1, int(CUE_POST_SILENCE_MS / self.cfg.echo_block_ms))
        # One block of tolerance: a 10 ms alignment error would otherwise overlap the
        # real cue tail and make every late cue look like it keeps sounding.
        start = offset + reference.size + 1
        tail = observed[start : start + quiet_blocks]
        if tail.size < quiet_blocks:
            return False
        onset_floor = CUE_ONSET_RATIO * float(np.max(reference))
        return float(np.max(tail)) <= onset_floor

    def _learn_lag(self, lag_ms: float) -> None:
        """Bounded EMA of the observed cue onset delay (prior: the Dot2's 740 ms)."""
        self._cue_lag_ms = min(
            CUE_LAG_MAX_MS,
            max(CUE_LAG_MIN_MS,
                (1.0 - CUE_LAG_ALPHA) * self._cue_lag_ms + CUE_LAG_ALPHA * lag_ms),
        )
        self._cue_lag_seen = True

    def _match_cue(
        self, observed: np.ndarray, reference: np.ndarray,
        low_ms: float, high_ms: float, *, silent_before: bool = False,
    ) -> tuple[float, int]:
        """Best envelope match whose start lies in [low_ms, high_ms]."""
        onset_blocks = max(1, int(CUE_ONSET_MS / self.cfg.echo_block_ms))
        onset_floor = CUE_ONSET_RATIO * float(np.max(reference))
        quiet_blocks = max(1, int(CUE_ONSET_SILENCE_MS / self.cfg.echo_block_ms))
        best_score = 0.0
        best_offset = 0
        for offset in range(observed.size):
            start_ms = offset * self.cfg.echo_block_ms
            if start_ms < low_ms:
                continue
            if start_ms > high_ms:
                break
            overlap = min(reference.size, observed.size - offset)
            if overlap < 3:  # under 60 ms cannot distinguish a match from noise
                break
            window = observed[offset : offset + overlap]
            if float(np.max(window[:onset_blocks])) < onset_floor:
                # The window starts in silence: not a cue onset, just the cue's rhythm
                # correlating with whatever follows the silence.
                continue
            if silent_before:
                pre = observed[max(0, offset - quiet_blocks) : offset]
                if pre.size and float(np.max(pre)) > onset_floor:
                    continue
            head = reference[:overlap]
            denom = float(np.linalg.norm(window) * np.linalg.norm(head))
            if denom <= 0.0:
                continue
            score = float(np.dot(window, head) / denom)
            if score > best_score:
                best_score, best_offset = score, offset
        return best_score, best_offset

    # ------------------------------------------------------------------ input
    def feed(self, pcm: bytes, ts_ms: float = 0.0) -> tuple[bytes, float] | None:
        """int16 mono block in; the finished utterance (int16) out when it closes."""
        if not self.armed or not pcm:
            return None
        block = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if self._hold_ms > 0:
            block_ms = block.size * 1000.0 / self.rate
            if self._hold_skip_ms > 0:
                drop_ms = min(self._hold_skip_ms, block_ms)
                drop = min(block.size, int(round(drop_ms * self.rate / 1000.0)))
                if drop:
                    self._hold_skip_samples.append(block[:drop].copy())
                self._hold_skip_ms = max(0.0, self._hold_skip_ms - drop * 1000.0 / self.rate)
                block = block[drop:]
                block_ms = block.size * 1000.0 / self.rate
                if block.size == 0:
                    self._hold_ms = max(0.0, self._hold_ms - drop_ms)
                    return None
            self._hold_buf.append(block)
            self._hold_ms = max(0.0, self._hold_ms - block_ms)
            if self._hold_ms > 0:
                return None
            # Hold lifted: hand every buffered block through the normal path, oldest first,
            # and stop as soon as one of them closes a turn. A cue tail that was still
            # playing is dropped first, so its echo cannot open a turn by itself; a reply
            # that starts after the cue is untouched and buffered as before.
            pending = list(self._hold_buf)
            self._hold_buf.clear()
            cue_start, cue_end, match, matched, contaminated = self._cue_echo(pending)
            if matched:
                # The cue's end is measured; remove it (plus reverb) and keep the
                # person's reply from that point on, including its first syllable.
                drop_ms = max(
                    0.0, cue_end + CUE_REVERB_GUARD_MS - self._hold_skip_total_ms
                )
                fallback = False
            elif contaminated:
                # Cue and person overlap and the microphone clipped, or cue energy was
                # seen in the skip window but not locatable (2026-10-26
                # 07:01:44: peak=1.0000 -> turn 「在呢。」). The cue cannot be located
                # in that audio, so give the deterministic answer: drop its worst-case
                # window and keep everything after it. The cue never becomes a turn;
                # words spoken underneath it are lost with it (the alternative is the
                # assistant answering its own voice).
                drop_ms = max(
                    0.0,
                    self._cue_ms + CUE_OVERLAP_GUARD_MS - self._hold_skip_total_ms,
                )
                fallback = True
            else:
                # No cue heard at all (a device that mutes its microphone while it
                # cues): nothing is removed, so the reply keeps its first word.
                drop_ms = 0.0
                fallback = False
            buffered_ms = sum(b.size for b in pending) * 1000.0 / self.rate
            drop_ms = min(drop_ms, buffered_ms)
            if drop_ms > 0.0:
                self._cue_excised = True
            if self._echo_env is not None:
                # One line per cue so the next sampling run can check every wake:
                # when the cue was heard, what was dropped, and what reached the VAD.
                dropped_end = min(
                    self._hold_skip_total_ms + drop_ms, self._hold_total_ms)
                log.info(
                    "cue_gate: cue=[%.0f,%.0f]ms dropped=[0,%.0f]ms kept=[%.0f,%.0f]ms "
                    "match=%.2f fallback=%s lag_hint=%s match_offset=%s",
                    cue_start if matched else 0.0,
                    cue_end if matched else self._cue_ms,
                    dropped_end,
                    dropped_end,
                    self._hold_total_ms,
                    match,
                    "true" if fallback else "false",
                    f"{self._cue_lag_ms:.0f}" if self._cue_lag_seen else "unknown",
                    "none" if self._last_match_offset_ms is None
                    else f"{self._last_match_offset_ms:.0f}",
                )
            out = None
            dropped_ms = 0.0
            for buffered in pending:
                if dropped_ms < drop_ms:
                    block_ms = buffered.size * 1000.0 / self.rate
                    take_ms = min(block_ms, drop_ms - dropped_ms)
                    take = min(buffered.size, int(round(take_ms * self.rate / 1000.0)))
                    buffered = buffered[take:]
                    dropped_ms += take * 1000.0 / self.rate
                    if buffered.size == 0:
                        continue
                out = self._consume(buffered, ts_ms)
                if out is not None:
                    return out
            return out
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
                    log.info("wake with no speech after %.0f ms; disarming (cue=%s)",
                             self._waited_ms,
                             "excised" if self._cue_excised else "none")
                    self.discarded += 1
                    # A cue that was excised is our own voice, not an utterance: the
                    # wake still heard nothing, so it must report the silent timeout
                    # exactly like a truly silent wake. Swallowing this event left the
                    # control plane's one-turn latch open until the 20 s watchdog
                    # (production, 2026-10-29: three silent wakes, 20 s deaf each).
                    self.idle_events += 1
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
        # T5: a close-talk clip is invisible in rms/peak (a saturated capture can look
        # quiet to an energy average). Annotate the segment only when the clipped share
        # is material; the transcript path itself is untouched.
        clipped = (
            float(np.count_nonzero(np.abs(audio) >= CLIP_SAMPLE)) / audio.size
            if audio.size else 0.0
        )
        clipped_note = (
            f" clipped={clipped * 100.0:.0f}%" if clipped > CLIP_LOG_RATIO else ""
        )
        log.info("[seg] %.2f s rms=%.4f peak=%.4f speech=%.2f s%s",
                 audio.size / self.rate, rms, peak, speech_ms / 1000.0, clipped_note)
        self.disarm()
        if speech_ms < self.cfg.min_speech_ms:
            log.info("utterance discarded: %.0f ms of speech", speech_ms)
            self.discarded += 1
            return None
        self.turns += 1
        return np.clip(audio * 32768.0, -32768, 32767).astype("<i2").tobytes(), ts_ms
