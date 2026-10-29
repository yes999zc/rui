"""A joke/riddle tool that still works when nothing is configured.

Contract with the optional self-hosted `joke-svc`
-------------------------------------------------
`GET <tools.joke_service_url>/v1/joke?kind=<any|joke|riddle>&topic=<urlencoded>&pid=<id>`
with `X-Internal-Token: <token>` when the secret `JOKE_SERVICE_TOKEN` exists (environment
first, then `<data_dir>/secrets.yaml`; no secret = no header). `pid` is an
installation-level uuid4 hex kept in `<data_dir>/joke_state.json` - the way a stateless
service can vary its answers without tracking a person. The response is JSON, parsed
leniently: `setup|question` plus `answer|punchline` is a riddle, `text|joke|content` is a
joke, and a missing `kind` is inferred from whether an answer came with it.

The service may also return `audio_url`: a rendered clip rui fetches itself. The
degradation order is audio -> text -> bundled sample. The URL is downloaded into
`<data_dir>/tmp/` through the same SSRF-guarded client as the JSON call, with its own
10 s timeout and an 8 MB cap, and the bytes are validated as WAV - the plane's file
player only reads WAV (`audio/process.py:read_wav_mono`), so anything else would play
as silence and be worse than the text. A path on the service's own disk is ignored: the
only accepted audio transport is a URL rui downloads itself, and no audio problem is
ever a reason for the tool to raise.

Two rules this module exists to keep:

- a tool call must never raise. Every failure (no URL, timeout, 5xx, transport error,
  invalid JSON) is exactly one `log.warning` - the token is never in it - followed by one
  of the 20 bundled samples in `rui/data/jokes.fallback.json`. A turn that dies in a tool
  takes the whole spoken answer with it, which is a much worse outcome than a mediocre
  joke.
- a riddle is asked in two stages. `new` returns the question only; `answer` reveals the
  last un-revealed one; asking again says it was already revealed instead of repeating it.
  `new` tries not to hand out the same line twice in a row.

The state file is written 0600 with temp + `os.replace` (same rule as `secrets.py`): a
crash can never leave half a JSON file, and a corrupt one is rebuilt with a warning.
"""
from __future__ import annotations

import contextlib
import functools
import importlib.resources
import io
import json
import logging
import os
import random
import time
import uuid
import wave
from pathlib import Path
from typing import Any

import httpx

from .. import secrets
from ..config.schema import Config
from ..security.netguard import guarded_async_client, policy_from_config
from .contract import Permission, ToolResult, ToolSpec

log = logging.getLogger(__name__)

# The secret that proves the call comes from this install. Optional: a LAN joke
# service may not need one, and a missing value must not disable the tool.
TOKEN_SECRET = "JOKE_SERVICE_TOKEN"
STATE_FILE = "joke_state.json"
FALLBACK_RESOURCE = "data/jokes.fallback.json"
# A spoken turn queues all of its TTS before returning; a joke is a sentence, not a
# monologue (the news fallback was cut for the same reason).
MAX_CHARS = 500
# Downloaded joke audio. A fixed bound rather than a config knob: this is one short
# clip, and a voice turn cannot wait long for it (the caller falls back to the text).
AUDIO_URL_TIMEOUT_S = 10.0
AUDIO_MAX_BYTES = 8 * 1024 * 1024
AUDIO_CACHE_TTL_S = 3600.0
AUDIO_DIRNAME = "tmp"
AUDIO_PREFIX = "joke_audio_"
# An audio-only answer still needs words: this is what TTS says when the clip cannot
# play, and what history records when it played (the clip itself is not transcribable).
AUDIO_ONLY_SAY = "来，听一段。"

JOKE_SPEC = ToolSpec(
    name="local.tell_joke",
    description=(
        "讲个笑话或出个谜语，用户说「讲个笑话」「出个谜语」「来点好玩的」时用。"
        "谜语先只念题面，不要自己猜答案；用户问「答案」「谜底」时，再传 action=answer "
        "拿答案。用户说不好笑、换一个、再来一个，就再调一次（kind 不变）换条新的。"
        "用户说了主题就填 topic，没说就留空。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["new", "answer"],
                "description": "new = 讲新的（默认）；answer = 揭晓上一个谜语的答案",
            },
            "kind": {
                "type": "string",
                "enum": ["any", "joke", "riddle"],
                "description": "要笑话（joke）还是谜语（riddle），默认 any",
            },
            "topic": {"type": "string", "description": "想听哪方面的，例如 程序员、动物；可留空"},
        },
    },
    permission=Permission.READ,
)

# If the bundled file is missing or corrupt there is still an answer: these three are in
# the code itself. They are deliberately plain - their job is to keep the tool speaking,
# and a warning says the real file was not usable.
_EMERGENCY_ITEMS: tuple[dict[str, str], ...] = (
    {"kind": "joke", "text": "我问手机：我和 wifi 谁重要？手机说：没有你，我还能被谁捧着。"},
    {"kind": "joke", "text": "闹钟响了，我说再睡五分钟。一天就这么过去了，我睡得特别满足。"},
    {"kind": "riddle", "setup": "什么东西越洗越脏？", "answer": "水。"},
)


def _clip(text: str) -> str:
    """One spoken line, bounded: a joke that runs a minute is not a joke any more."""
    text = " ".join(str(text).split())
    if len(text) <= MAX_CHARS:
        return text
    return text[: MAX_CHARS - 1].rstrip() + "…"


def _fallback_source() -> Any:
    """The bundled samples as an importlib Traversable (works from a wheel too)."""
    return importlib.resources.files("rui").joinpath(FALLBACK_RESOURCE)


def _clean_item(raw: Any) -> dict[str, str] | None:
    """One sample, or None when it cannot be spoken as-is."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind == "riddle":
        setup = _clip(str(raw.get("setup") or raw.get("question") or ""))
        answer = str(raw.get("answer") or raw.get("punchline") or "").strip()
        if setup and answer:
            return {"kind": "riddle", "setup": setup, "answer": _clip(answer)}
        return None
    text = _clip(str(raw.get("text") or raw.get("joke") or raw.get("content") or ""))
    return {"kind": "joke", "text": text} if text else None


@functools.lru_cache(maxsize=1)
def _fallback_items() -> tuple[dict[str, str], ...]:
    """The 20 bundled samples, loaded once; the emergency trio when that fails."""
    try:
        payload = json.loads(_fallback_source().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:  # noqa: BLE001 - samples are not worth a crash
        log.warning("joke: fallback samples unreadable (%s), using the built-in emergency set",
                    type(exc).__name__)
        return _EMERGENCY_ITEMS
    if not isinstance(payload, list):
        log.warning("joke: fallback samples are not a list, using the emergency set")
        return _EMERGENCY_ITEMS
    items = tuple(item for item in (_clean_item(raw) for raw in payload) if item is not None)
    if not items:
        log.warning("joke: fallback samples hold no usable entry, using the emergency set")
        return _EMERGENCY_ITEMS
    return items


def _item_text(item: dict[str, str]) -> str:
    return item["setup"] if item["kind"] == "riddle" else item["text"]


def _pick(items: tuple[dict[str, str], ...], kind: str, last: str) -> dict[str, str]:
    """A sample of the requested kind, avoiding the previous line when it can."""
    pool = [item for item in items if kind == "any" or item["kind"] == kind] or list(items)
    fresh = [item for item in pool if _item_text(item) != last]
    return random.choice(fresh or pool)


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _audio_cache_dir(cfg: Config) -> Path:
    return Path(cfg.system.data_dir) / AUDIO_DIRNAME


def _clean_audio_cache(directory: Path) -> None:
    """Best effort: downloaded clips must not accumulate for weeks.

    One hour is far longer than a turn; anything older is a previous answer whose file
    the plane has long finished with. Failures here are ignored - a stale file is not
    worth a warning in a spoken turn.
    """
    cutoff = time.time() - AUDIO_CACHE_TTL_S
    try:
        candidates = list(directory.glob(AUDIO_PREFIX + "*"))
    except OSError:
        return
    for old in candidates:
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        except OSError:
            continue


def _validate_wav(data: bytes) -> None:
    """Raise unless this is a WAV the plane's reader can decode.

    The RIFF magic alone is not enough: `read_wav_mono` branches on `sampwidth`, so a
    file with an odd fmt chunk (0 channels, 8-byte samples, no frames) passes the magic
    test and then fails at playback time - after the turn already skipped TTS. That is
    a silent answer, the one outcome this tool exists to prevent.
    """
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a wav")
    with wave.open(io.BytesIO(data)) as handle:
        if (handle.getnchannels() <= 0 or handle.getframerate() <= 0
                or handle.getnframes() <= 0):
            raise ValueError("empty wav")
        if handle.getsampwidth() not in (1, 2, 3, 4):
            raise ValueError("unsupported wav")
        handle.readframes(handle.getnframes())


def _assert_playable(path: Path) -> None:
    """The plane's own reader is the last word on "can this be played"."""
    from ..audio.wav import read_wav_mono

    samples, rate = read_wav_mono(path)
    if rate <= 0 or samples.size == 0:
        raise ValueError("empty wav")


def _download_reason(exc: Exception) -> str:
    """A log-safe category. Never the URL: a signed link is a credential."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, (wave.Error, EOFError)):
        return "not a readable wav"
    if isinstance(exc, ValueError):
        return str(exc) or "invalid audio"
    return type(exc).__name__


async def _download_audio(cfg: Config, url: str, *,
                          transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Fetch the service's rendered clip into `<data_dir>/tmp`, or "" + one warning.

    The URL comes from the authenticated service, but it is still an outbound request:
    it passes the SSRF guard, has its own timeout, is capped in size, and must be WAV -
    the plane's file player only reads WAV, so an mp3 would be silence instead of the
    joke. Every failure falls back to the text, which is why this returns a path or "".
    """
    try:
        parsed = httpx.URL(url)
    except Exception:  # noqa: BLE001 - a malformed URL is a fallback, not a crash
        log.warning("joke: audio_url is not a valid URL; using the text")
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.host:
        log.warning("joke: audio_url is not an http(s) URL; using the text")
        return ""
    directory = _audio_cache_dir(cfg)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("joke: cannot create %s (%s); using the text", directory.name,
                    type(exc).__name__)
        return ""
    _clean_audio_cache(directory)
    # A unique name: a later turn must not overwrite the file the plane is playing.
    target = directory / f"{AUDIO_PREFIX}{uuid.uuid4().hex[:12]}.wav"
    kwargs: dict[str, Any] = {"timeout": AUDIO_URL_TIMEOUT_S}
    if transport is not None:
        kwargs["transport"] = transport
    try:
        async with guarded_async_client(
                policy_from_config(cfg.security), **kwargs) as client:
            async with client.stream("GET", str(parsed)) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0]
                if content_type.strip().lower() not in (
                        "", "audio/wav", "audio/x-wav", "audio/wave",
                        "application/octet-stream"):
                    raise ValueError("not a wav")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > AUDIO_MAX_BYTES:
                        raise ValueError("too large")
        _validate_wav(bytes(chunks))
    except Exception as exc:  # noqa: BLE001 - the text is the fallback path
        log.warning("joke: audio_url download failed (%s); using the text",
                    _download_reason(exc))
        return ""
    try:
        target.write_bytes(bytes(chunks))
        # Written, then read back with the player's own decoder: a file that passes the
        # header checks but cannot be decoded would be a silent turn.
        _assert_playable(target)
    except Exception as exc:  # noqa: BLE001 - a broken clip is a text answer
        log.warning("joke: audio_url clip cannot be played (%s); using the text",
                    _download_reason(exc))
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        return ""
    return str(target)


def _parse_remote(data: Any) -> dict[str, str] | None:
    """The service's JSON in our shape, or None when it says nothing usable.

    `audio_url` is carried through untouched for `_fetch_remote` to download. The text
    is kept either way, so a failed download or playback still has something to say.
    A service-side `audio_path` (a path on the *service's* disk) is ignored on purpose:
    the contract is a URL rui fetches, never a path from another machine.
    """
    if not isinstance(data, dict):
        return None
    audio_url = str(data.get("audio_url") or "").strip()
    setup = _first_str(data, "setup", "question")
    answer = _first_str(data, "answer", "punchline")
    if setup and answer:
        item = {"kind": "riddle", "setup": _clip(setup), "answer": _clip(answer)}
    else:
        text = _first_str(data, "text", "joke", "content") or setup
        if not text and not audio_url:
            return None
        item = {"kind": "joke", "text": _clip(text)}
    if audio_url:
        item["audio_url"] = audio_url
    return item


def _state_path(cfg: Config) -> Path:
    return Path(cfg.system.data_dir) / STATE_FILE


def _fresh_state() -> dict[str, Any]:
    return {"pid": uuid.uuid4().hex}


def _read_state(path: Path) -> dict[str, Any]:
    """The persisted state; a missing file is the first run, a corrupt one is rebuilt."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _fresh_state()
    except (OSError, ValueError) as exc:
        log.warning("joke: %s is unreadable (%s), rebuilding it", path.name, type(exc).__name__)
        return _fresh_state()
    if not isinstance(payload, dict):
        log.warning("joke: %s does not hold an object, rebuilding it", path.name)
        return _fresh_state()
    state = dict(payload)
    pid = state.get("pid")
    if not isinstance(pid, str) or not pid:
        state["pid"] = uuid.uuid4().hex
    riddle = state.get("riddle")
    if isinstance(riddle, dict) and riddle.get("setup"):
        state["riddle"] = {
            "setup": str(riddle["setup"]),
            "answer": str(riddle.get("answer") or ""),
            "revealed": bool(riddle.get("revealed")),
        }
    else:
        state.pop("riddle", None)
    if not isinstance(state.get("last_text"), str):
        state.pop("last_text", None)
    return state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    """Atomic 0600 write; a state file that cannot be written only warns.

    Losing the riddle state costs one repeated joke. Raising here would cost the turn.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.chmod(tmp, 0o600)          # umask-proof, before the first byte
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1                   # fdopen owns it now
                json.dump(state, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            if fd >= 0:
                os.close(fd)
            tmp.unlink(missing_ok=True)
            raise
    except OSError as exc:
        log.warning("joke: cannot write %s: %s", tmp.name, exc)


async def _fetch_remote(cfg: Config, kind: str, topic: str, pid: str, *,
                        transport: httpx.AsyncBaseTransport | None = None
                        ) -> dict[str, str] | None:
    """One GET against joke-svc; None after one warning means "use the samples"."""
    base = cfg.tools.joke_service_url.strip()
    if not base:
        log.warning("joke: tools.joke_service_url is empty, serving a bundled sample")
        return None
    headers: dict[str, str] = {}
    token = secrets.get(TOKEN_SECRET)
    if token:
        headers["X-Internal-Token"] = token
    kwargs: dict[str, Any] = {"timeout": cfg.tools.joke_timeout_s}
    if transport is not None:
        kwargs["transport"] = transport
    try:
        async with guarded_async_client(
                policy_from_config(cfg.security), **kwargs) as client:
            response = await client.get(
                base.rstrip("/") + "/v1/joke",
                params={"kind": kind, "topic": topic, "pid": pid},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        log.warning("joke: joke-svc timed out after %ss, serving a bundled sample",
                    cfg.tools.joke_timeout_s)
        return None
    except httpx.HTTPStatusError as exc:
        log.warning("joke: joke-svc answered HTTP %s, serving a bundled sample",
                    exc.response.status_code)
        return None
    except ValueError as exc:  # response.json() on a non-JSON body
        log.warning("joke: joke-svc returned invalid JSON (%s), serving a bundled sample",
                    type(exc).__name__)
        return None
    except Exception as exc:  # noqa: BLE001 - unreachable service, blocked host, ...
        # Include the type: transport errors here can have an empty message. The value
        # never includes the token (it lives in a header, not in the URL or the repr).
        log.warning("joke: joke-svc request failed (%s: %s), serving a bundled sample",
                    type(exc).__name__, exc)
        return None
    # Parsing is its own failure domain: a payload that makes the parser throw (an odd
    # audio path, an unexpected type) is one warning and the bundled samples, never an
    # exception out of a tool call.
    try:
        item = _parse_remote(data)
    except Exception as exc:  # noqa: BLE001 - the samples are always a valid answer
        log.warning("joke: joke-svc payload could not be parsed (%s), serving a bundled sample",
                    type(exc).__name__)
        return None
    if item is None:
        log.warning("joke: joke-svc returned no speakable text, serving a bundled sample")
        return None
    audio_url = str(item.pop("audio_url", "") or "")
    if audio_url:
        # The audio contract: rui fetches the clip itself. A failed download is the text.
        local = await _download_audio(cfg, audio_url, transport=transport)
        if local:
            item["audio_path"] = local
    if not item.get("audio_path") and not _item_text(item):
        # Audio-only payload whose download failed: nothing left to say, so take a
        # bundled sample instead of returning an empty line (one warning, not two).
        return None
    return item


def _reveal(state: dict[str, Any]) -> str:
    """The answer to the last riddle - once; a second ask is told so, not repeated."""
    riddle = state.get("riddle")
    if not isinstance(riddle, dict) or not riddle.get("setup"):
        return "还没出过谜语：想猜的话，先让我出一个谜语。"
    if riddle.get("revealed"):
        return "上一个谜语已经揭晓过了：想听新的，就再让我出一个。"
    riddle["revealed"] = True
    state["riddle"] = riddle
    answer = str(riddle.get("answer") or "").strip()
    if not answer:
        return "这个谜语没有答案，换一个吧。"
    return _clip(f"答案：{answer}")


def _choice(args: dict[str, Any], key: str, allowed: tuple[str, ...], default: str) -> str:
    value = str(args.get(key) or "").strip().lower()
    return value if value in allowed else default


async def tell_joke(cfg: Config, args: dict[str, Any], *,
                    transport: httpx.AsyncBaseTransport | None = None
                    ) -> str | ToolResult:
    """The handler: always a speakable line, never an exception.

    `transport` exists for tests (`httpx.MockTransport` makes the client leave the
    process zero times); production calls it without the keyword.
    """
    action = _choice(args, "action", ("new", "answer"), "new")
    kind = _choice(args, "kind", ("any", "joke", "riddle"), "any")
    topic = str(args.get("topic") or "").strip()[:60]
    path = _state_path(cfg)
    state = _read_state(path)
    if action == "answer":
        text = _reveal(state)
        _write_state(path, state)
        return text
    item = await _fetch_remote(cfg, kind, topic, str(state["pid"]), transport=transport)
    if item is None:
        item = _pick(_fallback_items(), kind, str(state.get("last_text") or ""))
    spoken = _item_text(item)
    if item["kind"] == "riddle":
        # Only the question goes back to the model: the answer stays in the state file
        # until the user asks for it, or a chatty model would read both at once.
        state["riddle"] = {"setup": spoken, "answer": item["answer"], "revealed": False}
        text = _clip(f"谜语：{spoken}")
    else:
        text = _clip(spoken)
    state["last_text"] = spoken
    _write_state(path, state)
    audio_path = str(item.get("audio_path") or "")
    if audio_path:
        # The runner plays this file instead of TTS (see `DirectSpeech.audio_path`);
        # `say` stays the spoken fallback and what history re-reads next turn. A clip
        # without text gets a neutral line so a failed playback is not silence.
        return ToolResult(status="ok", payload={"say": text or AUDIO_ONLY_SAY,
                                                "audio_path": audio_path})
    return text


def build_tools(cfg: Config) -> list[tuple[ToolSpec, Any]]:
    """Always registered: an empty `joke_service_url` still answers from the samples."""
    return [(JOKE_SPEC, lambda args: tell_joke(cfg, args))]
