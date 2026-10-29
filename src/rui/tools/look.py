"""`local.look`: one camera frame, described by the model in a sentence (0.8.11).

Two sources, one reading:

- the Mac's USB camera, through the operator-run snapshot service
  (`scripts/usb_snapshot_server.py`, `GET /snapshot.jpg`, port 8766): a frame
  captured right now;
- a Home Assistant camera, through `GET <tools.ha.base_url>/api/camera_proxy/<id>`
  with the configured HA token: the last frame HA kept, which may be seconds old.

The difference is spoken, not hidden: the reply is labelled `vision.label_live` for
the USB path and `vision.label_latest` for the HA one.

The picture never touches the disk. One frame is fetched into memory (read, then
checked against an 8 MB cap), turned into a `data:image/jpeg;base64,` URI, and handed
to the **existing** LLM provider chain (`providers.chain.llm_chain` ->
`OpenAiCompatLlm.chat`; its `chat_payload` passes `messages` through untouched, so an
OpenAI-style `image_url` content part works unchanged). No new HTTP client, no new
vendor stack, and the conversational LLM path is not modified. Every provider on that
configured chain sees the frame, one after another, until one answers. This is the one
place where a frame leaves the machine, which is what `docs/local-look.md` states to
the operator.

Rules this module keeps (the same ones the joke tool keeps):

- a tool call never raises: every failure is one log line and one reachable sentence
  (`vision.*` in `session/phrases.py`; this file carries no spoken Chinese at all -
  the aliases below are input vocabulary, not wording);
- the words live in the phrase book, not here: the handler returns
  `ToolResult.say(phrases.book.render(...))`, like the HA read tools
  (docs/interaction.md §3);
- one look per `vision.cooldown_s` for the whole process, measured with
  `time.monotonic()` - a wall clock that jumps must not unlock the camera;
- `vision.timeout_s` is the budget for the **whole** look (fetch, description and the
  LLM fallback chain share one deadline), not one limit per step: the turn's tool
  timeout is shorter than two steps would take, and being cut from outside would
  surface as the model saying "the tool timed out" instead of our own sentence;
- the tool's own logs carry the device label, the byte count and the elapsed
  milliseconds, and nothing else: never the bytes, never the base64, never the
  question. (The runner's framework log - `tool look({...}) -> <reply>` in
  `session/turn.py` - does include the arguments and the reply; that is global
  behaviour, not this module's contract.)
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from .. import secrets
from ..config.schema import Config
from ..providers.chain import llm_chain
from ..security.netguard import guarded_async_client, policy_from_config
from ..session import phrases
from .contract import Permission, ToolResult, ToolSpec

log = logging.getLogger(__name__)

# Spoken names for "the Mac's own camera", plus English spellings a model may emit.
# This is input vocabulary (which camera was meant), not wording that is spoken back.
USB_ALIASES = frozenset({
    "", "书房", "书房摄像头", "书房的摄像头", "这里", "这", "本机", "usb", "this", "here",
})
# Only HA camera entities. The value is used to build a URL path, so anything else is
# treated as an unknown camera instead of being interpolated into the request.
_CAMERA_ENTITY_ID = re.compile(r"camera\.[a-z0-9_]+")
# A frame that big is not a snapshot service or a camera proxy; capping it keeps one
# broken endpoint from eating the process memory budget.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
# 1-2 spoken sentences. The prompt asks for 80 characters; this is the hard bound.
MAX_DESCRIPTION_CHARS = 80
# Below this much of the budget left, a vision round is not worth starting: it would
# be cut at the deadline anyway, and every attempt sends the frame to a cloud endpoint.
# So the tool answers "could not describe it" without spending the request. This is a
# gate, not a floor: the two steps never get MORE time than `vision.timeout_s`.
MIN_DESCRIBE_S = 0.5

LOOK_SPEC = ToolSpec(
    name="local.look",
    description=(
        "看一眼摄像头。用户问「看看书房」「门口有人吗」「现在什么样」时用。"
        "camera 留空或说书房/这里 = 书房的 USB 摄像头（实时）；说「门口」这类"
        "配置过的别名 = 那台 Home Assistant 摄像头（最近一帧）。"
        "question 填用户真正想问的画面内容，比如「桌上有没有杯子」；没问具体内容就留空。"
        "画面看不到、或者画面里没有他问的东西，就如实说看不到，不要编。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "想问画面里的什么，可留空",
            },
            "camera": {
                "type": "string",
                "description": "哪台摄像头：留空/书房/这里 = USB；也可用配置里的别名"
                               "或 camera.xxx 形式的实体 ID",
            },
        },
    },
    permission=Permission.READ,
)


@dataclass
class _Cooldown:
    """Process-wide last-look timestamp; one instance per built tool list."""

    last_at: float | None = None


def _say(key: str, *, status: str = "ok", **slots: object) -> ToolResult:
    """The V-line shape: the book renders, the tool only names the key."""
    return ToolResult(status=status, payload={"say": phrases.book.render(key, **slots)})


def _resolve(cfg: Config, camera: str) -> tuple[str, str] | None:
    """`("usb", "")` / `("ha", entity_id)` / None for an unknown camera.

    Order: the USB spellings, then the configured alias map, then a literal
    `camera.xxx` id. The map is checked before the literal so a deployment can rename
    an entity without retraining the household.
    """
    text = str(camera or "").strip()
    if text in USB_ALIASES:
        return ("usb", "")
    mapped = str(cfg.tools.vision.cameras.get(text) or "").strip()
    if mapped:
        return ("ha", mapped) if _CAMERA_ENTITY_ID.fullmatch(mapped) else None
    if _CAMERA_ENTITY_ID.fullmatch(text):
        return ("ha", text)
    return None


def _is_image(response: httpx.Response) -> bool:
    """A camera endpoint that answers HTML/JSON is a failure, not a picture."""
    content_type = response.headers.get("content-type", "")
    return content_type.split(";")[0].strip().lower().startswith("image/")


def _ha_headers(cfg: Config) -> dict[str, str]:
    token = secrets.get(cfg.tools.ha.token_env)
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _fetch(cfg: Config, url: str, headers: dict[str, str], *,
                 timeout: float,
                 transport: httpx.AsyncBaseTransport | None = None) -> bytes | None:
    """One frame as bytes, or None after one log line. Never raises.

    `timeout` is what is LEFT of the look's single budget (see `build_tools`), not the
    configured limit: one look must finish inside `vision.timeout_s` in total, so the
    fetch cannot spend the whole budget and leave the description none.

    The body is read and then checked against `MAX_IMAGE_BYTES`; the cap protects the
    process from a broken endpoint, it is not a streaming guard.

    `transport` exists for tests (`httpx.MockTransport` keeps the suite offline);
    production calls this without the keyword.
    """
    kwargs: dict[str, Any] = {"timeout": timeout}
    if transport is not None:
        kwargs["transport"] = transport
    try:
        async with guarded_async_client(
                policy_from_config(cfg.security), **kwargs) as client:
            response = await client.get(url, headers=headers)
            if response.status_code != 200:
                log.info("look: fetch answered HTTP %s", response.status_code)
                return None
            if not _is_image(response):
                log.info("look: fetch answered %s, not an image",
                         response.headers.get("content-type", "no content-type"))
                return None
            body = response.content
    except Exception as exc:  # noqa: BLE001 - a camera is optional, the turn is not
        # Type only: a transport error's message can carry the URL, which carries the
        # entity id; the log contract is device + bytes + elapsed.
        log.info("look: fetch failed (%s)", type(exc).__name__)
        return None
    if not body:
        log.info("look: fetch returned an empty body")
        return None
    if len(body) > MAX_IMAGE_BYTES:
        log.info("look: frame too large (%d bytes)", len(body))
        return None
    return bytes(body)


def _data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def _messages(jpeg: bytes, question: str) -> list[dict]:
    """The vision request body's `messages`: one text part and one image part.

    Built here (and not in the provider) so a test can assert the exact content the
    vendor receives without any network.
    """
    text = phrases.book.render("vision.prompt")
    asked = " ".join(str(question or "").split())[:120]
    if asked:
        text = phrases.book.render("vision.prompt_ask", prompt=text, question=asked)
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": _data_uri(jpeg)}},
        ],
    }]


def _clip(text: str) -> str:
    """One short spoken line: a paragraph of scene description is not an answer."""
    text = " ".join(str(text or "").split())
    if len(text) <= MAX_DESCRIPTION_CHARS:
        return text
    return text[: MAX_DESCRIPTION_CHARS - 1].rstrip("，,。.、；; ") + "…"


async def _close(provider: object) -> None:
    closer = getattr(provider, "aclose", None)
    if closer is None:
        return
    with contextlib.suppress(Exception):    # a close failure must not fail the answer
        await closer()                      # type: ignore[misc]


async def _describe(cfg: Config, jpeg: bytes, question: str, *, timeout: float) -> str:
    """One streaming chat round on the existing LLM chain; "" means "not described".

    `timeout` is what is LEFT of the look's single budget (never the configured limit
    again): the fetch has already spent its share, and a description that outlives the
    turn is worse than an honest "could not describe it".

    The deadline is computed ONCE for the whole fallback chain (0.8.11 review, second
    pass): handing every provider a fresh `timeout` meant two providers = 30 s, which
    the turn's 20 s tool timeout cuts from the outside - the exact failure this budget
    exists to prevent. A provider only ever gets what is left, and when nothing is left
    the chain stops instead of trying again.

    The first ready provider speaks for the picture; a vendor error or the timeout
    falls through to the next one, exactly like the conversational chain. Nothing is
    retried inside a provider.
    """
    messages = _messages(jpeg, question)
    chain = list(llm_chain(cfg.providers))
    if not chain:
        log.info("look: no ready llm provider, cannot describe")
        return ""
    deadline = time.monotonic() + timeout
    try:
        for name, provider in chain:
            left = deadline - time.monotonic()
            if left <= 0:
                log.info("look: budget exhausted before %s could answer", name)
                break
            started = time.monotonic()
            try:
                async with asyncio.timeout(left):
                    parts = [chunk.delta async for chunk in provider.chat(messages, [])
                             if chunk.delta]
                text = _clip("".join(parts))
                if text:
                    log.info("look: described via %s in %d ms (%d chars)", name,
                             (time.monotonic() - started) * 1000, len(text))
                    return text
                log.info("look: %s returned no text", name)
            except Exception as exc:  # noqa: BLE001 - the next provider is the fallback
                log.info("look: %s could not describe (%s)", name, type(exc).__name__)
    finally:
        # Every provider built for this look is closed, including the ones the deadline
        # never reached: they may already hold an HTTP client.
        for _name, provider in chain:
            await _close(provider)
    return ""


def build_tools(cfg: Config, *, clock: Callable[[], float] | None = None,
                transport: httpx.AsyncBaseTransport | None = None
                ) -> list[tuple[ToolSpec, Any]]:
    """`[(LOOK_SPEC, handler)]` - always registered, like the joke tool.

    With nothing configured the tool still exists and answers `vision.no_usb`: a dead
    tool would make the model improvise an answer about the room instead.

    `clock` (tests) and `transport` (tests) are keywords, not config: production calls
    this as `build_tools(cfg)` and gets the monotonic clock and a real client.
    """
    now = clock if clock is not None else time.monotonic
    state = _Cooldown()

    async def look(args: dict[str, Any]) -> ToolResult:
        camera = str(args.get("camera") or "")
        question = str(args.get("question") or "")
        started = now()
        if state.last_at is not None and started - state.last_at < cfg.tools.vision.cooldown_s:
            log.info("look: cooldown hit (%.1fs since the last look)", started - state.last_at)
            return _say("vision.cooldown", status="denied")
        target = _resolve(cfg, camera)
        if target is None:
            # Unknown alias: the deployment did not configure that camera. Not recorded
            # as a look - "刚看过一次" would be a lie, and the honest answer is the same
            # however often it is asked.
            log.info("look: unknown camera (not an alias, not a camera entity id)")
            return _say("vision.no_frame", status="empty")
        kind, where = target
        if kind == "usb":
            url = cfg.tools.vision.usb_snapshot_url.strip()
            if not url:
                log.info("look: usb snapshot service is not configured")
                return _say("vision.no_usb", status="empty")
            frame_device = "usb"
            headers: dict[str, str] = {}
        else:
            base = cfg.tools.ha.base_url.strip().rstrip("/")
            if not base:
                log.info("look: camera %s configured but tools.ha.base_url is empty", where)
                return _say("vision.no_frame", status="unreachable")
            url = base + "/api/camera_proxy/" + where
            frame_device = where
            headers = _ha_headers(cfg)
        # The attempt counts before the network: a slow or broken camera must not turn
        # into an unlimited retry loop against the snapshot service (and the camera is
        # pointed at a private room, so "one look per window" is the promise).
        state.last_at = started
        # ONE budget for the whole look, not one per step (0.8.11 review R1): the
        # turn's tool timeout is 20 s, so 15 s + 15 s would be cut from the outside and
        # the user would hear the model say "the tool timed out" instead of our line.
        # Nothing below ever gets MORE time than what is left of this deadline.
        deadline = started + cfg.tools.vision.timeout_s
        fetch_budget = deadline - now()
        if fetch_budget <= 0:
            log.info("look: no time left for the frame")
            return _say("vision.no_frame", status="unreachable")
        jpeg = await _fetch(cfg, url, headers, timeout=fetch_budget, transport=transport)
        if jpeg is None:
            log.info("look: no frame from %s after %d ms", frame_device,
                     (now() - started) * 1000)
            return _say("vision.no_frame", status="unreachable")
        log.info("look: device=%s bytes=%d elapsed_ms=%d live=%s", frame_device, len(jpeg),
                 (now() - started) * 1000, kind == "usb")
        remaining = deadline - now()
        if remaining < MIN_DESCRIBE_S:
            # The frame arrived and the budget is effectively gone: say exactly that.
            # Starting a cloud call with what is left would only cut it at the deadline
            # (and send the picture for nothing).
            log.info("look: %d ms left after the frame, not describing",
                     max(0, (now() - started) * 1000))
            return _say("vision.describe_failed", status="unreachable")
        text = await _describe(cfg, jpeg, question, timeout=remaining)
        if not text:
            return _say("vision.describe_failed", status="unreachable")
        key = "vision.label_live" if kind == "usb" else "vision.label_latest"
        return _say(key, text=text)

    return [(LOOK_SPEC, look)]
