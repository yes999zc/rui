"""Speaker gate on the wake word: only an enrolled family voice may wake the box.

The 0.8.15 false wakes were all the child's video playing in the living room: the KWS
is a keyword spotter, so any voice that says the phrase wakes it. This module answers
one question at wake time - "does the wake audio match anyone enrolled on this device?"
- and nothing else. It is a gate, not authentication: a matched score admits the wake,
a low score keeps the whole wake silent (no cue, no turn, one log line).

Three pieces, deliberately separate so the wake path stays testable without a model:

* `SpeakerLibrary` - `speakers.json` in the data directory; atomic writes, mtime-based
  hot reload (enrolling a person must not need a restart), and per-device candidates.
* `SpeakerEmbedder` - the sherpa-onnx CAM++ wrapper. It raises `SpeakerUnavailable`
  when the extra/model is missing, and the gate then admits without gating.
* `SpeakerGate` - the decision, cheap enough to run on the frame loop.

Fail-open is the rule everywhere: a missing model, a corrupt library, an embedding
error or an empty library must never make the wake word stop working. Each condition
warns once and admits.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

SPEAKERS_FILE = "speakers.json"
_LIBRARY_VERSION = 1
# How much wake audio is scored at least: the KWS reports a hit ~0.5 s late and a
# person says the phrase in well under a second, so the tail before the hit catches
# the whole phrase. `min_speech_ms` can ask for more (a slow/tall voice), never less.
SPEAKER_WAKE_WINDOW_MS = 1500
# The preroll ring must hold the gate window plus the KWS latency; 2000 ms is the
# floor the card fixes. The replay window into the VAD stays `wake_preroll_ms`.
SPEAKER_PREROLL_HISTORY_MS = 2000


class SpeakerUnavailable(RuntimeError):
    """sherpa-onnx (or its model file) is missing: the gate admits instead of failing."""


def _brief_exc(exc: BaseException, limit: int = 320) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass(frozen=True)
class SpeakerProfile:
    """One enrolled voice. `device_id` empty means "every device"."""

    name: str
    device_id: str
    embedding: list[float]
    dim: int
    enrolled_at: float
    samples: int = 1

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device_id": self.device_id,
            "embedding": list(self.embedding),
            "dim": int(self.dim),
            "enrolled_at": float(self.enrolled_at),
            "samples": int(self.samples),
        }

    @classmethod
    def from_json(cls, raw: object) -> SpeakerProfile | None:
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name") or "").strip()
        vector = raw.get("embedding")
        if not name or not isinstance(vector, list) or not vector:
            return None
        try:
            embedding = [float(v) for v in vector]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(v) for v in embedding):
            return None
        dim = int(raw.get("dim") or len(embedding))
        if dim != len(embedding):
            # A file edited by hand can disagree; the vector is what the maths uses.
            dim = len(embedding)
        try:
            enrolled_at = float(raw.get("enrolled_at") or 0.0)
        except (TypeError, ValueError):
            enrolled_at = 0.0
        try:
            samples = max(1, int(raw.get("samples") or 1))
        except (TypeError, ValueError):
            samples = 1
        return cls(
            name=name,
            device_id=str(raw.get("device_id") or "").strip(),
            embedding=embedding,
            dim=dim,
            enrolled_at=enrolled_at,
            samples=samples,
        )


def _normalised(vector: np.ndarray) -> np.ndarray | None:
    """Unit vector for cosine similarity; None for an all-zero or non-finite input."""
    arr = np.asarray(vector, dtype=np.float32).reshape(-1)
    if arr.size == 0 or not np.all(np.isfinite(arr)):
        return None
    norm = float(np.linalg.norm(arr))
    if norm <= 1e-9:
        return None
    return arr / norm


class SpeakerLibrary:
    """`<data_dir>/speakers.json`: profiles enrolled by Card B's API, read here.

    The file is the shared state (the console writes it, this process reads it), so the
    gate reloads whenever its mtime changes instead of caching a snapshot from startup.
    A missing file is "nobody enrolled yet", not an error; a corrupt one is a warning
    plus an empty library, which the gate treats as fail-open.
    """

    def __init__(self, data_dir: str | Path, *, threshold: float = 0.55) -> None:
        if not str(data_dir).strip():
            # An empty data_dir would scatter speakers.json into the process's cwd.
            raise ValueError("data_dir is empty: the speaker library needs system.data_dir")
        self.dir = Path(data_dir).expanduser()
        self.threshold = float(threshold)
        self._profiles: list[SpeakerProfile] = []
        self._mtime: float | None = None
        self._loaded = False

    @property
    def path(self) -> Path:
        return self.dir / SPEAKERS_FILE

    def _stat_mtime(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def load(self) -> None:
        """Read the file; every failure degrades to an empty library plus one warning."""
        mtime = self._stat_mtime()
        self._loaded = True
        self._mtime = mtime
        if mtime is None:
            self._profiles = []
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("speaker library: cannot read %s: %s", self.path, exc)
            self._profiles = []
            return
        entries = raw.get("speakers") if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            log.warning("speaker library: %s has no speaker list", self.path)
            self._profiles = []
            return
        profiles: list[SpeakerProfile] = []
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            profile = SpeakerProfile.from_json(entry)
            if profile is None:
                continue
            key = (profile.name, profile.device_id)
            if key in seen:
                # Last write wins, matching `add()`'s re-record overwrite.
                profiles = [p for p in profiles if (p.name, p.device_id) != key]
            seen.add(key)
            profiles.append(profile)
        self._profiles = profiles

    def reload_if_changed(self) -> bool:
        """Re-read only when the file changed; the gate calls this before every wake."""
        mtime = self._stat_mtime()
        if self._loaded and mtime == self._mtime:
            return False
        self.load()
        return True

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _LIBRARY_VERSION,
            "speakers": [p.to_json() for p in self._profiles],
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)
        # Store the new mtime: our own write must not look like an external change and
        # make the next wake reload the file we just wrote.
        self._mtime = self._stat_mtime()
        self._loaded = True

    def add(
        self, name: str, device_id: str, embedding: Any, *, samples: int = 1
    ) -> SpeakerProfile:
        """Enrol (or re-enrol) one voice; the same (name, device) is replaced."""
        clean_name = " ".join(str(name or "").split())
        if not clean_name:
            raise ValueError("speaker name is empty")
        vector = _normalised(embedding)
        if vector is None:
            raise ValueError("speaker embedding is empty or all zeros")
        profile = SpeakerProfile(
            name=clean_name,
            device_id=str(device_id or "").strip(),
            embedding=[round(float(v), 6) for v in vector],
            dim=int(vector.size),
            enrolled_at=time.time(),
            samples=max(1, int(samples)),
        )
        self._profiles = [
            p for p in self._profiles
            if (p.name, p.device_id) != (profile.name, profile.device_id)
        ]
        self._profiles.append(profile)
        self.save()
        return profile

    def remove(self, name: str, device_id: str | None = None) -> int:
        clean_name = " ".join(str(name or "").split())
        before = len(self._profiles)
        if device_id is None:
            self._profiles = [p for p in self._profiles if p.name != clean_name]
        else:
            target = str(device_id).strip()
            self._profiles = [
                p for p in self._profiles
                if not (p.name == clean_name and p.device_id == target)
            ]
        removed = before - len(self._profiles)
        if removed:
            self.save()
        return removed

    def profiles(self) -> list[SpeakerProfile]:
        return list(self._profiles)

    def count(self) -> int:
        return len(self._profiles)

    def best_match(
        self, embedding: Any, device_id: str | None = None
    ) -> tuple[float, str | None]:
        """Highest cosine score among candidates; (0.0, None) when none apply."""
        query = _normalised(embedding)
        if query is None:
            return 0.0, None
        device = None if device_id is None else str(device_id).strip()
        best_score = 0.0
        best_name: str | None = None
        for profile in self._profiles:
            if device is not None and profile.device_id not in ("", device):
                continue
            if profile.dim != query.size:
                # A model swap changes the dimension; a stale profile must not produce
                # a bogus score (nor crash the wake path).
                continue
            candidate = _normalised(profile.embedding)
            if candidate is None:
                continue
            score = float(np.dot(query, candidate))
            if best_name is None or score > best_score:
                best_score = score
                best_name = profile.name
        return best_score, best_name


class SpeakerEmbedder:
    """CAM++ speaker embedding via sherpa-onnx, with one lazily created stream per call."""

    def __init__(self, model_path: str | Path, *, num_threads: int = 1) -> None:
        text = str(model_path or "").strip()
        if not text:
            raise SpeakerUnavailable("speaker_gate.model_path is empty")
        path = Path(text).expanduser()
        if not path.exists():
            raise SpeakerUnavailable(f"speaker model not found: {path}")
        try:
            import sherpa_onnx
        except (ImportError, OSError) as exc:  # pragma: no cover - install extra
            raise SpeakerUnavailable(
                f"sherpa-onnx is not available ({_brief_exc(exc)}); "
                "uv sync --extra kws"
            ) from exc
        try:
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(path), num_threads=max(1, int(num_threads))
            )
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        except Exception as exc:  # noqa: BLE001 - a bad model file must not kill wake
            raise SpeakerUnavailable(
                f"cannot load speaker model {path}: {_brief_exc(exc)}"
            ) from exc
        self.model_path = path
        self._dim = int(getattr(self._extractor, "dim", 0) or 0)

    @property
    def available(self) -> bool:
        return self._extractor is not None

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, pcm: bytes | np.ndarray, rate: int) -> np.ndarray | None:
        """One embedding, or None when the audio cannot be embedded (caller admits)."""
        if self._extractor is None or (pcm.size == 0 if isinstance(pcm, np.ndarray) else not pcm):
            return None
        samples = _as_float32(pcm)
        if samples is None or samples.size < max(1, int(rate) // 20):
            return None
        try:
            stream = self._extractor.create_stream()
            stream.accept_waveform(sample_rate=int(rate), waveform=samples)
            stream.input_finished()
            if hasattr(self._extractor, "is_ready") and not self._extractor.is_ready(stream):
                return None
            embedding = self._extractor.compute(stream)
        except Exception as exc:  # noqa: BLE001 - inference must not break the wake path
            log.warning("speaker embed failed: %s", _brief_exc(exc))
            return None
        if embedding is None:
            return None
        arr = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if arr.size == 0 or not np.all(np.isfinite(arr)):
            return None
        self._dim = int(arr.size)
        return arr


def _as_float32(pcm: bytes | np.ndarray) -> np.ndarray | None:
    """int16 bytes / int16 array / float32 array -> float32 in [-1, 1]."""
    if isinstance(pcm, (bytes, bytearray, memoryview)):
        raw = np.frombuffer(bytes(pcm), dtype="<i2")
        return raw.astype(np.float32) / 32768.0
    arr = np.asarray(pcm)
    if arr.size == 0:
        return None
    if arr.dtype.kind == "i":
        return arr.astype(np.float32) / 32768.0
    return arr.astype(np.float32)


@dataclass
class WakeDecision:
    """One gate answer. `ms` is the evaluation cost, not the audio length."""

    accepted: bool
    score: float
    best: str | None
    reason: str
    ms: float = 0.0


@dataclass
class SegmentDecision:
    """One segment-gate answer; `ms` is the evaluation cost."""

    accepted: bool
    score: float
    anchor_score: float
    best: str | None
    reason: str
    window: bool
    chunks: int
    ms: float = 0.0


class SpeakerGate:
    """Decide whether the wake audio belongs to an enrolled voice.

    Reasons: "match" (admitted), "mismatch"/"short" (silently rejected), and the
    fail-open set "disabled"/"barge"/"unavailable"/"empty"/"no_embedding"/
    "no_candidate"/"error", which admit because a broken gate must not break the wake
    word (or deafen a device that simply has no enrolment yet).
    """

    def __init__(
        self,
        cfg: Any,
        library: SpeakerLibrary,
        embedder: SpeakerEmbedder | None,
        *,
        device_id: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.library = library
        self.embedder = embedder
        self.device_id = device_id
        self._unavailable_logged = False
        self._empty_logged = False
        self._embed_failed_logged = False
        self._no_candidate_logged = False
        # Per-device, not per-gate: a house with three unenrolled rooms must log three
        # lines, not one, or the operator cannot tell which room needs enrolment.
        self._no_enrollment_logged: set[str] = set()
        # The wake's own embedding, set at admission and compared against every segment
        # of that turn (the "anchor" layer, 0.8.16 P1). None when the wake was admitted
        # without an embedding (barge skip / fail-open), which the segment gate treats
        # as "no anchor" and fails open on.
        self._anchor: np.ndarray | None = None
        self._anchor_name: str | None = None
        # The embedding of the most recent wake evaluation, for `set_anchor` at admission.
        self.last_wake_embedding: np.ndarray | None = None
        self._segment_unavailable_logged = False
        self._no_anchor_logged = False

    @property
    def available(self) -> bool:
        return self.embedder is not None and bool(getattr(self.embedder, "available", False))

    def _device_has_enrollment(self) -> bool:
        """True when at least one profile is valid for this device (or for all devices)."""
        device = str(self.device_id or "").strip()
        if not device:
            return self.library.count() > 0
        return any(p.device_id in ("", device) for p in self.library.profiles())

    def _match_device(self) -> str | None:
        """The device filter for a library match, with the unenrolled-device fallback."""
        match_device = str(self.device_id or "").strip() or None
        if match_device is not None and not self._device_has_enrollment():
            match_device = None
            if str(self.device_id) not in self._no_enrollment_logged:
                self._no_enrollment_logged.add(str(self.device_id))
                log.info(
                    "speaker gate: no enrollment for device=%s; matching all devices",
                    self.device_id,
                )
        return match_device

    def _fail_open(self, reason: str, detail: str) -> WakeDecision:
        log.warning("speaker gate unavailable: %s; admitting without gating", detail)
        return WakeDecision(accepted=True, score=0.0, best=None, reason=reason)

    def set_anchor(self, embedding: Any, name: str | None = None) -> None:
        """Remember the wake's voice for this turn's segment gate."""
        self._anchor = _normalised(embedding)
        self._anchor_name = name

    def clear_anchor(self) -> None:
        self._anchor = None
        self._anchor_name = None

    def anchor_score(self, embedding: Any) -> float:
        """Cosine similarity to the wake anchor, 0.0 when there is no anchor."""
        if self._anchor is None:
            return 0.0
        vector = _normalised(embedding)
        if vector is None or vector.size != self._anchor.size:
            return 0.0
        return float(np.dot(vector, self._anchor))

    def evaluate_wake(
        self, pcm: bytes | np.ndarray, rate: int, *, barge: bool = False
    ) -> WakeDecision:
        started = time.monotonic()
        decision = self._evaluate(pcm, rate, barge=barge)
        decision.ms = (time.monotonic() - started) * 1000.0
        return decision

    def _evaluate(
        self, pcm: bytes | np.ndarray, rate: int, *, barge: bool
    ) -> WakeDecision:
        if not bool(getattr(self.cfg, "enabled", False)):
            return WakeDecision(True, 0.0, None, "disabled")
        # A barge wake is spoken over music/our own voice; the embedding is polluted by
        # the mix, so gating it would reject the very person interrupting. The switch is
        # config so this decision can be reversed without a code change.
        if barge and bool(getattr(self.cfg, "skip_on_barge", True)):
            return WakeDecision(True, 0.0, None, "barge")
        try:
            self.library.reload_if_changed()
        except Exception as exc:  # noqa: BLE001 - file stat/read must never break wake
            if not self._unavailable_logged:
                self._unavailable_logged = True
                return self._fail_open("error", _brief_exc(exc))
        if not self.available:
            if not self._unavailable_logged:
                self._unavailable_logged = True
                return self._fail_open(
                    "unavailable",
                    f"model unavailable ({self.cfg.model_path or 'no model_path'})",
                )
            return WakeDecision(True, 0.0, None, "unavailable")
        if self.library.count() == 0:
            if not self._empty_logged:
                self._empty_logged = True
                return self._fail_open("empty", "no enrolled speakers in speakers.json")
            return WakeDecision(True, 0.0, None, "empty")

        samples = _as_float32(pcm)
        duration_ms = 0.0 if samples is None else samples.size * 1000.0 / max(1, int(rate))
        min_ms = max(0, int(getattr(self.cfg, "min_speech_ms", 600)))
        if samples is None or duration_ms < min_ms:
            return WakeDecision(False, 0.0, None, "short")

        embedding = self.embedder.embed(pcm, int(rate))
        if embedding is None:
            self.last_wake_embedding = None
            if not self._embed_failed_logged:
                self._embed_failed_logged = True
                return self._fail_open("no_embedding", "the wake audio produced no embedding")
            return WakeDecision(True, 0.0, None, "no_embedding")
        self.last_wake_embedding = embedding

        # A device with no enrolment of its own must not go deaf: three people enrolled
        # in the living room say nothing about the bathroom's sensor. Match every device
        # then, and say so once per device so the missing enrolment is visible.
        score, best = self.library.best_match(embedding, self._match_device())
        if best is None:
            # The library is non-empty but nothing is comparable (e.g. profiles enrolled
            # with a different model's dimension). Rejecting would deafen every device
            # after a model swap; admit once with a warning instead.
            if not self._no_candidate_logged:
                self._no_candidate_logged = True
                return self._fail_open(
                    "no_candidate",
                    "no stored profile matches this embedding "
                    "(dimension changed after a model swap?)",
                )
            return WakeDecision(True, 0.0, None, "no_candidate")
        threshold = float(getattr(self.cfg, "threshold", 0.55))
        if score >= threshold:
            return WakeDecision(True, score, best, "match")
        return WakeDecision(False, score, best, "mismatch")

    # ------------------------------------------------------------- segment gate
    def _segment_fail_open(self, reason: str, detail: str) -> SegmentDecision:
        if not self._segment_unavailable_logged:
            self._segment_unavailable_logged = True
            log.warning(
                "speaker gate unavailable: %s; admitting segment without gating", detail
            )
        return SegmentDecision(True, 0.0, 0.0, None, reason, False, 1)

    def _score_chunk(
        self, embedding: np.ndarray, *, in_wake_window: bool
    ) -> tuple[float, float, str | None, str | None]:
        """Score one chunk under the mode/window rules.

        Returns ``(score, anchor_score, best, fail_open_reason)``; the last element is
        non-None when the rules cannot decide and the chunk must be admitted.
        """
        threshold = float(getattr(self.cfg, "threshold", 0.55))
        anchor = self.anchor_score(embedding)
        lib_score, best = self.library.best_match(embedding, self._match_device())
        if best is None and self.library.count() > 0:
            # Non-empty library with no comparable profile (model swap): do not deafen.
            return 0.0, anchor, None, "no_candidate"
        mode = str(getattr(self.cfg, "mode", "any_enrolled"))
        anchor_or_library = bool(getattr(self.cfg, "accept_anchor_or_library", True))
        if in_wake_window:
            # Layer 4: inside the wake window only the wake's own voice counts. Without
            # an anchor there is nothing to compare, so admit rather than deafen.
            if self._anchor is None:
                return 0.0, anchor, best, "no_anchor"
            score = anchor
        elif mode == "wake_owner":
            if self._anchor is not None:
                score = max(anchor, lib_score) if anchor_or_library else anchor
            elif anchor_or_library:
                score = lib_score
            else:
                return 0.0, anchor, best, "no_anchor"
        else:  # any_enrolled
            score = max(lib_score, anchor) if anchor_or_library else lib_score
        return score, anchor, best, None if score >= threshold else "low"

    def evaluate_segment(
        self,
        pcm: bytes | np.ndarray,
        rate: int,
        *,
        since_wake_ms: float | None = None,
        max_speech_ms: int = 8000,
    ) -> SegmentDecision:
        """Gate one finished VAD segment, before it becomes a turn.

        Layers, in order: library/anchor score by mode; chunking of anything longer
        than `max_speech_ms` (a 9.38 s media sentence must not be judged by one
        embedding); and the post-wake window, which only trusts the wake anchor.
        """
        started = time.monotonic()
        decision = self._evaluate_segment(
            pcm, int(rate), since_wake_ms=since_wake_ms, max_speech_ms=max_speech_ms
        )
        decision.ms = (time.monotonic() - started) * 1000.0
        return decision

    def _evaluate_segment(
        self,
        pcm: bytes | np.ndarray,
        rate: int,
        *,
        since_wake_ms: float | None,
        max_speech_ms: int,
    ) -> SegmentDecision:
        if not bool(getattr(self.cfg, "enabled", False)) or not bool(
            getattr(self.cfg, "segment_gate", True)
        ):
            return SegmentDecision(True, 0.0, 0.0, None, "disabled", False, 1)
        if not self.available:
            return self._segment_fail_open(
                "unavailable",
                f"model unavailable ({self.cfg.model_path or 'no model_path'})",
            )
        try:
            self.library.reload_if_changed()
        except Exception as exc:  # noqa: BLE001 - file problems must not stop turns
            return self._segment_fail_open("error", _brief_exc(exc))
        if self.library.count() == 0 and self._anchor is None:
            return self._segment_fail_open("empty", "no enrolled speakers in speakers.json")

        samples = _as_float32(pcm)
        if samples is None or samples.size == 0:
            return self._segment_fail_open("empty_audio", "the segment carried no samples")

        window_ms = max(0, int(getattr(self.cfg, "wake_window_ms", 2500)))
        in_wake_window = (
            window_ms > 0 and since_wake_ms is not None and since_wake_ms < window_ms
        )
        chunk_samples = max(1, int(rate * max(1000, int(max_speech_ms)) / 1000))
        total_chunks = max(1, (samples.size + chunk_samples - 1) // chunk_samples)
        worst: SegmentDecision | None = None
        for index in range(total_chunks):
            chunk = samples[index * chunk_samples : (index + 1) * chunk_samples]
            embedding = self.embedder.embed(chunk, rate)
            if embedding is None:
                return self._segment_fail_open(
                    "no_embedding", "the segment produced no embedding"
                )
            score, anchor, best, fail_reason = self._score_chunk(
                embedding, in_wake_window=in_wake_window
            )
            if fail_reason == "no_candidate":
                return self._segment_fail_open(
                    "no_candidate",
                    "no stored profile matches this embedding "
                    "(dimension changed after a model swap?)",
                )
            if fail_reason == "no_anchor":
                if not self._no_anchor_logged:
                    self._no_anchor_logged = True
                    log.warning(
                        "speaker gate unavailable: no anchor for this wake; "
                        "admitting segments without the wake window"
                    )
                # The anchor layer cannot judge (barge skip / failed embedding). The
                # library layers already had their say, so admit instead of closing a
                # turn the person actually asked for.
                return SegmentDecision(
                    True, score, anchor, best, "no_anchor", in_wake_window, total_chunks
                )
            if worst is None or score < worst.score:
                worst = SegmentDecision(
                    True, score, anchor, best, "match", in_wake_window, total_chunks
                )
            if fail_reason == "low":
                reason = "wake_window" if in_wake_window else (
                    "chunk" if total_chunks > 1 else "speaker"
                )
                return SegmentDecision(
                    False, score, anchor, best, reason, in_wake_window, total_chunks
                )
        assert worst is not None  # at least one chunk was scored
        worst.accepted = True
        return worst
