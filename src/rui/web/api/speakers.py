"""Speaker enrollment: state machine and ``/api/v2/speakers`` routes (Card B).

Why this is its own module
--------------------------
The recording is done by the audio plane (it owns the post-AEC stream), while the
library and the console are control-plane concerns. This module is the control
plane's half: validate the request, ask one device's plane to record, wait for
the finished file, embed it, and store the profile.

The handshake is a **file**, not a socket reply
-----------------------------------------------
``AudioPlaneClient._dispatch`` routes control messages by an explicit
``isinstance`` chain; a message type that is not in it is parsed and dropped.
Sending an ``EnrollResult`` from the plane would therefore be logged as an
unhandled message on every enrollment and never reach this module. So the plane
completes the job on disk:

    <data_dir>/enroll/<enroll_id>.wav.tmp  ->  os.replace -> .wav
    <data_dir>/enroll/<enroll_id>.json     (atomic sidecar)

    {"enroll_id": ..., "device_id": ..., "path": ..., "ms": ..., "speech_ms": ...}

``commit`` treats "wav exists and sidecar parses" as the only completion signal
and verifies ``enroll_id``/``device_id`` against the session it started. A wav
without a sidecar is an unfinished (or crashed) recording, never a usable one.

State
-----
One in-process dict, expiring after ``ENROLL_TTL_S``: an enrollment is a "do it
now" action, so a console restart in the middle of one must start over rather
than adopt a file it cannot attribute.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse

from ...audio.protocol import EnrollStart
from ...audio.wav import read_wav_mono

log = logging.getLogger(__name__)

ENROLL_DIR = "enroll"
SPEAKERS_DIR = "speakers"

#: An enrollment that is never committed is garbage after this long (card: 30 s).
#: Longer than the 10 s maximum recording, so a slow commit is never punished.
ENROLL_TTL_S = 30.0
#: A second enrollment on the same device is a conflict only while the first one is
#: still capturing (plus the poll grace below), not for the whole TTL.
RECORD_IDLE_GRACE_S = 2.0
#: ``commit`` waits this long for a recording that is a moment away from finishing;
#: after that it answers 409 and the console retries (the card's "未录完 409").
COMMIT_WAIT_S = 2.0
POLL_S = 0.05

DEFAULT_SECONDS = 3
MIN_SECONDS = 1
MAX_SECONDS = 10
MAX_NAME_CHARS = 20
DEFAULT_MIN_SPEECH_MS = 600.0
DEFAULT_THRESHOLD = 0.55

_UNSAFE = re.compile(r"[^0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff_.-]+")
_DIGEST_CHARS = 8


@dataclass
class _Enroll:
    """One in-flight enrollment. Everything the commit path needs."""

    enroll_id: str
    name: str
    device_id: str
    seconds: int
    started_at: float
    wav: Path
    sidecar: Path

    def recording_until(self) -> float:
        return self.started_at + self.seconds + RECORD_IDLE_GRACE_S


# ---------------------------------------------------------------- path helpers


def _normalize_name(value: str) -> str:
    """The library's key normalisation (Card A): collapse every whitespace run.

    ``SpeakerLibrary.add/remove`` key rows by ``" ".join(raw.split())``. The API has
    to use the *same* string everywhere: a name typed with a double space and later
    with a single one must be one key, otherwise a re-record appends a second
    profile, DELETE misses it and the sample lookup points at nothing.
    """
    return " ".join(str(value or "").split())


def _safe_component(text: str) -> str:
    """A filename fragment: keeps CJK and ASCII, replaces separators and spaces.

    ``/`` and ``\\`` must never survive (a name is user input), and a name of only
    punctuation still needs a non-empty component.
    """
    cleaned = _UNSAFE.sub("_", str(text or "").strip())
    cleaned = cleaned.strip("._") or "_"
    return cleaned[:64]


def sample_path(data_dir: str | Path, name: str, device_id: str) -> Path:
    """Where one registered sample wav lives (``data_dir/speakers/``).

    Readable prefix + short digest: two names that sanitize to the same fragment
    must not overwrite each other's sample, and the digest is over the exact
    ``(name, device_id)`` key the library uses.
    """
    digest = hashlib.sha1(f"{name}|{device_id}".encode()).hexdigest()[:_DIGEST_CHARS]
    stem = f"{_safe_component(name)}__{_safe_component(device_id)}__{digest}.wav"
    return Path(data_dir).expanduser() / SPEAKERS_DIR / stem


def _enroll_paths(data_dir: str | Path, enroll_id: str) -> tuple[Path, Path]:
    root = Path(data_dir).expanduser() / ENROLL_DIR
    return root / f"{enroll_id}.wav", root / f"{enroll_id}.json"


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - a read-only data dir is reported elsewhere
        log.warning("speaker enroll: could not remove %s: %s", path, exc)


# ---------------------------------------------------------------- value helpers


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two embeddings; 0.0 when either is degenerate.

    Deliberately local: the console only wants "how close was this re-recording
    to the old one", and that must not depend on the library's threshold logic.
    """
    left = [float(value) for value in ([] if a is None else a)]
    right = [float(value) for value in ([] if b is None else b)]
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(x * y for x, y in zip(left, right, strict=False))
    na = math.sqrt(sum(x * x for x in left))
    nb = math.sqrt(sum(y * y for y in right))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return round(dot / (na * nb), 4)


def _iso(ts: float | int | str | None) -> str:
    """Epoch seconds (the library's wire form) -> the console's ISO copy."""
    if isinstance(ts, str) and ts:
        return ts
    try:
        value = float(ts or 0.0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return dt.datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")


def _gate_config(cfg: Any) -> Any:
    """``audio.voice.speaker_gate`` or ``None``.

    Card A owns that sub-model. This module reads it through ``getattr`` so the
    console and the API work on a build where the gate is not merged yet (the
    defaults below are the card's values).
    """
    voice = getattr(getattr(cfg, "audio", None), "voice", None)
    return getattr(voice, "speaker_gate", None)


def _min_speech_ms(cfg: Any) -> float:
    raw = getattr(_gate_config(cfg), "min_speech_ms", DEFAULT_MIN_SPEECH_MS)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_SPEECH_MS


def _threshold(cfg: Any) -> float:
    raw = getattr(_gate_config(cfg), "threshold", DEFAULT_THRESHOLD)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


def _model_path(cfg: Any) -> str:
    return str(getattr(_gate_config(cfg), "model_path", "") or "").strip()


# ---------------------------------------------------------------- default ports


def _default_store(data_dir: str, threshold: float) -> Any:
    from ...audio.speaker_gate import SpeakerLibrary

    library = SpeakerLibrary(data_dir, threshold=threshold)
    library.load()
    return library


_EMBEDDERS: dict[str, Any] = {}


def _default_embedder(model_path: str) -> Any:
    """One ``SpeakerEmbedder`` per model path: loading the ONNX graph is not free."""
    from ...audio.speaker_gate import SpeakerEmbedder

    embedder = _EMBEDDERS.get(model_path)
    if embedder is None:
        embedder = SpeakerEmbedder(model_path)
        _EMBEDDERS[model_path] = embedder
    return embedder


# ---------------------------------------------------------------- request helpers


async def _json_object(request: Request, error_cls: Callable[..., Exception]) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        raise error_cls(400, "bad_request", "request body is required",
                        hint="Send a JSON object with name, device_id and seconds.")
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise error_cls(400, "bad_request", f"request body is not JSON: {exc}",
                        hint="Send a JSON object with name, device_id and seconds.") from exc
    if not isinstance(body, dict):
        raise error_cls(400, "bad_request", "request body must be a JSON object")
    return body


def _bad_field(error_cls: Callable[..., Exception], field: str, reason: str,
               message: str) -> Exception:
    return error_cls(400, "bad_request", message,
                     hint=f"Check the `{field}` field.",
                     details={"field": field, "reason": reason})


def _parse_name(body: dict[str, Any], error_cls: Callable[..., Exception]) -> str:
    raw = body.get("name")
    if not isinstance(raw, str):
        raise _bad_field(error_cls, "name", "required", "name must be a string")
    name = _normalize_name(raw)
    if not name:
        raise _bad_field(error_cls, "name", "required", "name must not be empty")
    if len(name) > MAX_NAME_CHARS:
        raise _bad_field(error_cls, "name", "too_long",
                         f"name must be at most {MAX_NAME_CHARS} characters")
    return name


def _parse_device_id(body: dict[str, Any], error_cls: Callable[..., Exception]) -> str:
    raw = body.get("device_id")
    if not isinstance(raw, str) or not raw.strip():
        raise _bad_field(error_cls, "device_id", "required", "device_id must be a non-empty string")
    return raw.strip()


def _parse_seconds(body: dict[str, Any], error_cls: Callable[..., Exception]) -> int:
    raw = body.get("seconds", DEFAULT_SECONDS)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _bad_field(error_cls, "seconds", "type", "seconds must be an integer")
    if not MIN_SECONDS <= raw <= MAX_SECONDS:
        raise _bad_field(error_cls, "seconds", "range",
                         f"seconds must be between {MIN_SECONDS} and {MAX_SECONDS}")
    return raw


# ---------------------------------------------------------------- route installer


def install_speaker_routes(
    app: FastAPI,
    *,
    auth_dep: Callable[..., Any],
    config_now: Callable[[], Any],
    error_cls: Callable[..., Exception],
    plane_client: Callable[[str], Any],
    store_factory: Callable[[str, float], Any] | None = None,
    embedder_factory: Callable[[str], Any] | None = None,
) -> None:
    """Register the five enrollment endpoints on ``app``.

    Everything is injected: the console app owns the config loader, the plane
    lookup and the C-002 error type; this module must not build a second copy of
    any of them (and must stay testable without a running plane).
    """
    store_of = store_factory or _default_store
    embedder_of = embedder_factory or _default_embedder
    state: dict[str, _Enroll] = {}

    def data_dir() -> Path:
        raw = str(getattr(getattr(config_now(), "system", None), "data_dir", "") or "").strip()
        if not raw:
            raise error_cls(409, "no_data_dir", "system.data_dir is not configured",
                            hint="Set system.data_dir in the config before enrolling speakers.")
        return Path(raw).expanduser()

    def device_or_404(device_id: str) -> Any:
        for device in getattr(config_now(), "devices", []) or []:
            if getattr(device, "id", None) != device_id:
                continue
            if not getattr(device, "enabled", True):
                break
            return device
        raise error_cls(
            404, "not_found", f"no such device: {device_id}",
            hint="Check the device id on the devices page; a disabled device cannot record.",
            details={"resource": "device", "id": device_id},
        )

    def sweep_orphans(stamp: float, root: Path) -> None:
        """Delete recordings on disk that no session in this process can commit.

        A plane that finishes *after* its session was dropped (DELETE of the person,
        a control-plane restart, a crash mid-arm) leaves ``enroll/<id>.wav`` plus
        ``enroll/<id>.json`` behind; committing is impossible and nothing else would
        ever remove them, so the speaker's voice data would stay on disk forever.
        The age guard is what keeps the sweep from unlinking a file the plane is
        still writing: only entries older than the enrollment TTL are garbage.
        """
        folder = root / ENROLL_DIR
        try:
            entries = list(folder.iterdir())
        except OSError:
            return
        for entry in entries:
            # "abc.wav" / "abc.json" / "abc.wav.tmp" / "abc.wav.json.part" -> "abc"
            if entry.name.split(".", 1)[0] in state:
                continue
            try:
                if stamp - entry.stat().st_mtime <= ENROLL_TTL_S:
                    continue
                entry.unlink()
            except OSError:
                continue
            log.info("speaker enroll: removed orphan %s", entry.name)

    def sweep(now: float | None = None) -> None:
        """Drop enrollments nobody committed (card: 30 s) and their files."""
        stamp = time.time() if now is None else now
        for enroll_id, session in list(state.items()):
            if stamp - session.started_at <= ENROLL_TTL_S:
                continue
            state.pop(enroll_id, None)
            _unlink(session.wav)
            _unlink(session.sidecar)
            log.info("speaker enroll expired: %s (device=%s)", enroll_id, session.device_id)
        sweep_orphans(stamp, data_dir())

    def discard(session: _Enroll) -> None:
        state.pop(session.enroll_id, None)
        _unlink(session.wav)
        _unlink(session.sidecar)

    def read_result(session: _Enroll) -> dict[str, Any] | None:
        """The sidecar, if this recording is provably finished.

        Presence of the wav alone is not enough: the plane writes the wav first and
        the sidecar second, so a wav without one is a recording still in flight (or
        a plane that died) - never a profile to store.
        """
        if not session.wav.is_file() or not session.sidecar.is_file():
            return None
        try:
            payload = json.loads(session.sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    async def wait_result(session: _Enroll, timeout_s: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_s
        while True:
            result = read_result(session)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(POLL_S)

    # ------------------------------------------------------------------ POST /enroll
    @app.post("/api/v2/speakers/enroll", status_code=201,
              dependencies=[Depends(auth_dep)])
    async def enroll(request: Request) -> dict[str, Any]:
        """Arm one device's recording. 201 means "recording", not "enrolled"."""
        body = await _json_object(request, error_cls)
        name = _parse_name(body, error_cls)
        device_id = _parse_device_id(body, error_cls)
        seconds = _parse_seconds(body, error_cls)
        device = device_or_404(device_id)
        root = data_dir()

        now = time.time()
        sweep(now)
        for session in state.values():
            if session.device_id == device_id and now < session.recording_until():
                raise error_cls(
                    409, "device_busy",
                    f"{device_id} is already recording an enrollment",
                    hint="Wait for that recording to finish (at most 10 s) and retry.",
                    details={"device_id": device_id, "enroll_id": session.enroll_id},
                    retryable=True,
                )

        client = plane_client(device_id)
        if client is None:
            reason = str(getattr(device, "status_reason", "") or "")
            raise error_cls(
                409, "plane_not_connected",
                f"no audio plane connected for {device_id}",
                hint="Open the device page and make sure the speaker is online, then retry.",
                details={"device_id": device_id, "reason": reason},
                retryable=True,
            )

        enroll_id = uuid.uuid4().hex
        wav, sidecar = _enroll_paths(root, enroll_id)
        wav.parent.mkdir(parents=True, exist_ok=True)
        session = _Enroll(
            enroll_id=enroll_id, name=name, device_id=device_id, seconds=seconds,
            started_at=now, wav=wav, sidecar=sidecar,
        )
        message = EnrollStart(
            enroll_id=enroll_id, device_id=device_id, seconds=seconds, path=str(wav),
        )
        # Track the session before the send: the plane may finish its file the moment
        # it receives the request, and the orphan sweep must never see that file as
        # unowned (it only deletes files older than the TTL, but "old enough" must not
        # depend on how fast the recorder is).
        state[enroll_id] = session
        try:
            await client.send(message)
        except Exception as exc:  # noqa: BLE001 - a dead socket is a 409, not a 500
            discard(session)
            raise error_cls(
                409, "plane_not_connected", f"could not reach {device_id}: {exc}",
                hint="The audio plane may have restarted; retry once it is online.",
                details={"device_id": device_id}, retryable=True,
            ) from exc
        # The person's name never reaches the log: only which device and how long.
        log.info("speaker enroll started: %s device=%s seconds=%d",
                 enroll_id, device_id, seconds)
        return {
            "enroll_id": enroll_id,
            "device_id": device_id,
            "seconds": seconds,
            "started_at": _iso(now),
        }

    # ------------------------------------------------------------------ POST /commit
    @app.post("/api/v2/speakers/enroll/{enroll_id}/commit",
              dependencies=[Depends(auth_dep)])
    async def commit(enroll_id: str) -> dict[str, Any]:
        """Read the finished wav, embed it, and write the profile."""
        now = time.time()
        sweep(now)
        session = state.get(enroll_id)
        if session is None:
            raise error_cls(
                404, "not_found", f"no such enrollment: {enroll_id}",
                hint="Start a new recording; an idle enrollment is dropped after 30 s.",
                details={"resource": "enrollment", "id": enroll_id},
            )

        result = await wait_result(session, COMMIT_WAIT_S)
        if result is None:
            raise error_cls(
                409, "recording_incomplete", "the recording has not finished yet",
                hint="Wait for the requested seconds to elapse, then commit again.",
                details={"enroll_id": enroll_id, "device_id": session.device_id,
                         "seconds": session.seconds},
                retryable=True,
            )

        recorded_id = str(result.get("enroll_id") or "")
        recorded_device = str(result.get("device_id") or "")
        if recorded_id != enroll_id or recorded_device != session.device_id:
            discard(session)
            raise error_cls(
                409, "recording_mismatch",
                "the recording does not belong to this enrollment",
                hint="Start a new recording; the sidecar names another enrollment/device.",
                details={"expected": {"enroll_id": enroll_id,
                                      "device_id": session.device_id},
                         "found": {"enroll_id": recorded_id,
                                   "device_id": recorded_device}},
            )

        try:
            ms = float(result.get("ms") or 0.0)
        except (TypeError, ValueError):
            ms = 0.0
        raw_speech_ms = result.get("speech_ms")
        if raw_speech_ms is None:
            # An older plane's sidecar has no speech counter: the whole recording is
            # the best available upper bound. An explicit 0 is different - the plane
            # heard nothing - and must stay 0 so `speech_too_short` can fire.
            speech_ms = ms
        else:
            try:
                speech_ms = float(raw_speech_ms)
            except (TypeError, ValueError):
                speech_ms = 0.0

        cfg = config_now()
        minimum = _min_speech_ms(cfg)
        if speech_ms < minimum:
            discard(session)
            log.info("speaker enroll rejected: short speech_ms=%.0f min=%.0f device=%s",
                     speech_ms, minimum, session.device_id)
            raise error_cls(
                400, "speech_too_short",
                "the recording did not contain enough speech",
                hint="Ask the person to speak for the whole recording window and retry.",
                details={"speech_ms": round(speech_ms), "min_speech_ms": round(minimum)},
            )

        try:
            samples, rate = read_wav_mono(session.wav)
        except Exception as exc:  # noqa: BLE001 - a truncated wav is the caller's 400
            discard(session)
            raise error_cls(
                400, "bad_audio", f"the recording is not a readable wav: {exc}",
                hint="Record again; the file was truncated or not 16-bit PCM.",
            ) from exc

        model_path = _model_path(cfg)
        if not model_path:
            discard(session)
            raise error_cls(
                503, "speaker_model_unavailable",
                "audio.voice.speaker_gate.model_path is not configured",
                hint="Point it at the CAM++ onnx model, then enroll again.",
                retryable=False,
            )
        try:
            embedder = embedder_of(model_path)
        except Exception as exc:  # noqa: BLE001 - model missing / sherpa unavailable
            discard(session)
            raise error_cls(
                503, "speaker_model_unavailable", f"cannot load the speaker model: {exc}",
                hint="Check the model path and that sherpa-onnx is installed.",
                details={"model_path": model_path},
            ) from exc
        if not getattr(embedder, "available", True):
            discard(session)
            raise error_cls(
                503, "speaker_model_unavailable", "the speaker embedder is not available",
                hint="Check the model path and that sherpa-onnx is installed.",
                details={"model_path": model_path},
            )

        embedding = embedder.embed(samples, rate)
        if embedding is None:
            discard(session)
            raise error_cls(
                400, "no_speech", "no identifiable speech in the recording",
                hint="Record again in a quiet room, speaking for the whole window.",
            )

        threshold = _threshold(cfg)
        try:
            library = store_of(str(data_dir()), threshold)
            profiles = list(library.profiles())
        except Exception as exc:  # noqa: BLE001 - corrupt speakers.json is not a 500
            discard(session)
            raise error_cls(
                503, "speaker_store_unavailable", f"cannot read the speaker library: {exc}",
                hint="Check data_dir permissions and speakers.json.",
            ) from exc

        same_key = [p for p in profiles
                    if getattr(p, "name", None) == session.name
                    and getattr(p, "device_id", None) == session.device_id]
        same_device = [p for p in profiles
                       if getattr(p, "device_id", None) == session.device_id
                       and getattr(p, "name", None) != session.name]
        # `score` answers "is this the same voice as before", which is what the
        # console shows after a re-record: distance to the old embedding of the
        # same person on this device, else the closest other person on it.
        if same_key:
            score: float | None = _cosine(embedding, getattr(same_key[0], "embedding", []))
            reference = "previous"
        elif same_device:
            score = max(_cosine(embedding, getattr(p, "embedding", [])) for p in same_device)
            reference = "others"
        else:
            score = None
            reference = "none"

        try:
            # `SpeakerLibrary.add` writes speakers.json atomically itself (Card A), so
            # a second save here would rewrite the same file twice.
            profile = library.add(session.name, session.device_id, embedding, samples=1)
        except Exception as exc:  # noqa: BLE001 - a read-only data dir must say so
            discard(session)
            raise error_cls(
                503, "speaker_store_unavailable", f"cannot write the speaker library: {exc}",
                hint="Check data_dir permissions.",
            ) from exc

        # Keep the sample for the console's play button, then drop the temp pair.
        kept = sample_path(data_dir(), session.name, session.device_id)
        kept.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(session.wav, kept)
        except OSError as exc:
            # EXDEV (enroll/ and speakers/ on different mounts) is not fatal: copy
            # the sample, then drop the temp file. A missing sample only costs the
            # console's play button, never the enrollment itself.
            log.warning("speaker sample: could not move %s -> %s (%s); copying",
                        session.wav, kept, exc)
            try:
                shutil.copyfile(session.wav, kept)
                _unlink(session.wav)
            except OSError as copy_exc:
                log.warning("speaker sample could not be kept for device=%s: %s",
                            session.device_id, copy_exc)
        # A voice sample is personal data: keep it owner-only, like reminders.json.
        try:
            os.chmod(kept, 0o600)
        except OSError:
            pass
        _unlink(session.sidecar)
        state.pop(enroll_id, None)

        dim = int(getattr(profile, "dim", 0) or 0)
        log.info("speaker enrolled: device=%s dim=%d speech_ms=%.0f reference=%s",
                 session.device_id, dim, speech_ms, reference)
        return {
            "name": session.name,
            "device_id": session.device_id,
            "samples": int(getattr(profile, "samples", 1) or 1),
            "dim": dim,
            "score": score,
            "reference": reference,
            "speech_ms": round(speech_ms),
        }

    # ------------------------------------------------------------------ GET /speakers
    @app.get("/api/v2/speakers", dependencies=[Depends(auth_dep)])
    def list_speakers() -> list[dict[str, Any]]:
        """Everyone enrolled, grouped by name, with one row per device."""
        sweep()
        root = data_dir()
        cfg = config_now()
        threshold = _threshold(cfg)
        try:
            profiles = list(store_of(str(root), threshold).profiles())
        except Exception as exc:  # noqa: BLE001 - the list page must render, not 500
            raise error_cls(
                503, "speaker_store_unavailable", f"cannot read the speaker library: {exc}",
                hint="Check data_dir permissions and speakers.json.",
            ) from exc

        grouped: dict[str, dict[str, Any]] = {}
        for profile in profiles:
            name = str(getattr(profile, "name", "") or "")
            device_id = str(getattr(profile, "device_id", "") or "")
            if not name or not device_id:
                continue
            entry = grouped.setdefault(name, {"name": name, "devices": [], "threshold": threshold})
            entry["devices"].append({
                "device_id": device_id,
                "samples": int(getattr(profile, "samples", 1) or 1),
                "enrolled_at": _iso(getattr(profile, "enrolled_at", 0.0)),
                "sample": sample_path(root, name, device_id).is_file(),
            })
        for entry in grouped.values():
            entry["devices"].sort(key=lambda item: item["device_id"])
        return [grouped[name] for name in sorted(grouped)]

    # ------------------------------------------------------------------ DELETE /speakers/{name}
    @app.delete("/api/v2/speakers/{name}", dependencies=[Depends(auth_dep)])
    def remove_speaker(name: str, device_id: str | None = None) -> dict[str, Any]:
        """Delete one person (all devices) or one (person, device) row."""
        root = data_dir()
        cfg = config_now()
        threshold = _threshold(cfg)
        # Normalise the path parameter too: `/speakers/%E5%BC%A0%20%20%E4%B8%89`
        # is the same person as "张 三" and must delete that person's rows.
        name = _normalize_name(name)
        wanted_device = (device_id or "").strip() or None
        try:
            library = store_of(str(root), threshold)
            before = [p for p in library.profiles()
                      if getattr(p, "name", None) == name
                      and (wanted_device is None
                           or getattr(p, "device_id", None) == wanted_device)]
            # `remove` saves when it removed something (same contract as `add`).
            removed = int(library.remove(name, wanted_device))
        except Exception as exc:  # noqa: BLE001 - a broken file must not look like a success
            raise error_cls(
                503, "speaker_store_unavailable", f"cannot update the speaker library: {exc}",
                hint="Check data_dir permissions and speakers.json.",
            ) from exc

        if removed <= 0:
            raise error_cls(
                404, "not_found", f"no such speaker: {name}",
                hint="The list endpoint returns the enrolled names.",
                details={"resource": "speaker", "id": name,
                         "device_id": wanted_device},
            )

        for profile in before:
            _unlink(sample_path(root, name, str(getattr(profile, "device_id", "")
                                               or (wanted_device or ""))))
        # A recording that is still running for that person is now meaningless.
        for session in list(state.values()):
            if session.name != name:
                continue
            if wanted_device is not None and session.device_id != wanted_device:
                continue
            discard(session)
        log.info("speaker removed: device=%s removed=%d", wanted_device or "*", removed)
        return {"removed": removed}

    # ------------------------------------------------------------------ GET /speakers/{name}/sample.wav
    @app.get("/api/v2/speakers/{name}/sample.wav", dependencies=[Depends(auth_dep)])
    def sample_wav(name: str, device_id: str | None = None) -> FileResponse:
        """The kept sample, for the console's "play sample" button.

        Without ``device_id`` the request is only answerable when the person has a
        single device - otherwise the caller must say which room's voice it wants.
        """
        root = data_dir()
        cfg = config_now()
        name = _normalize_name(name)
        try:
            profiles = [p for p in store_of(str(root), _threshold(cfg)).profiles()
                        if getattr(p, "name", None) == name]
        except Exception as exc:  # noqa: BLE001
            raise error_cls(
                503, "speaker_store_unavailable", f"cannot read the speaker library: {exc}",
                hint="Check data_dir permissions and speakers.json.",
            ) from exc

        wanted = (device_id or "").strip() or None
        if wanted is not None:
            profiles = [p for p in profiles if getattr(p, "device_id", None) == wanted]
        if not profiles:
            raise error_cls(
                404, "not_found", f"no sample for speaker: {name}",
                hint="Re-record this person; samples are kept only after a successful commit.",
                details={"resource": "sample", "id": name, "device_id": wanted},
            )
        if wanted is None and len(profiles) > 1:
            raise error_cls(
                409, "device_required", "this speaker has samples on more than one device",
                hint="Pass ?device_id= to choose the sample.",
                details={"resource": "sample", "id": name,
                         "devices": sorted(str(getattr(p, "device_id", "")) for p in profiles)},
            )

        path = sample_path(root, name, str(getattr(profiles[0], "device_id", "")))
        if not path.is_file():
            raise error_cls(
                404, "not_found", f"no sample file for speaker: {name}",
                hint="Re-record this person; the sample wav is missing on disk.",
                details={"resource": "sample", "id": name},
            )
        return FileResponse(path, media_type="audio/wav",
                            headers={"Cache-Control": "no-store"})
