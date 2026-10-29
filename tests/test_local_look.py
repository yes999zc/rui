"""`local.look` — the camera tool, entirely headless.

Every test here runs against `httpx.MockTransport` and a scripted LLM: the suite must
never open a socket to DeepSeek, to Home Assistant or to the Mac's camera (the
deployment card does the real-device check, silently). What is pinned:

- both camera paths and the wording they speak (`现在` vs `最近一次`);
- every failure mode speaks one sentence and the tool never raises;
- the frame is memory-only: no file under `data_dir`, no base64 in the log;
- the cooldown is process-wide and injectable (`clock=`);
- the wire shape of the vision request: an `image_url` data URI through the existing
  `chat_payload` (no new provider stack).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from rui.config import Config
from rui.config.schema import ProviderConfig
from rui.providers.base import ChatChunk
from rui.providers.openai_compat import chat_payload
from rui.session import phrases
from rui.tools import look
from rui.tools.contract import Permission, ToolResult

# A JPEG-shaped blob: the tool never decodes the frame, so the container's magic is
# enough to be able to prove later that exactly these bytes travelled.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x11" * 32 + b"\xff\xd9"
LOG = "rui.tools.look"
DESCRIPTION = "桌上有一只白杯子，旁边没有人。"


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _no_secrets(monkeypatch: pytest.MonkeyPatch):
    """No test may read the operator's real HA token."""
    monkeypatch.setattr(look.secrets, "get", lambda name: "")
    phrases.reset()
    yield
    phrases.reset()


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    conf = Config()
    conf.system.data_dir = str(tmp_path / "data")
    # The mock transport never leaves the process; the SSRF guard has its own tests.
    conf.security.outbound.enabled = False
    conf.tools.vision.usb_snapshot_url = "http://cam.test:8766/snapshot.jpg"
    return conf


class FakeLlm:
    """A scripted provider: records the messages, answers one line (or raises)."""

    def __init__(self, text: str = DESCRIPTION, error: Exception | None = None,
                 delay_s: float = 0.0) -> None:
        self.text = text
        self.error = error
        self.delay_s = delay_s
        self.seen: list[list[dict]] = []
        self.closed = False

    async def chat(self, messages: list[dict], tools: list[dict]):
        self.seen.append(messages)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        yield ChatChunk(delta=self.text)
        yield ChatChunk(delta="", done=True)

    async def aclose(self) -> None:
        self.closed = True


def use_llm(monkeypatch: pytest.MonkeyPatch, llm: FakeLlm) -> FakeLlm:
    monkeypatch.setattr(look, "llm_chain", lambda providers: [("deepseek", llm)])
    return llm


def transport(body: bytes = JPEG, *, status: int = 200,
              content_type: str = "image/jpeg",
              error: Exception | None = None,
              seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if error is not None:
            raise error
        return httpx.Response(status, content=body,
                              headers={"content-type": content_type})

    return httpx.MockTransport(handler)


def make_handler(cfg: Config, *, transport_: httpx.MockTransport | None = None,
                 clock=None):
    """One tool pair, one cooldown state: the same handler is called twice below."""
    (spec, handler), = look.build_tools(cfg, clock=clock, transport=transport_)
    assert spec.name == "local.look"

    def call(args: dict) -> ToolResult:
        return asyncio.run(handler(args))

    return call


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def say(key: str, **slots: object) -> str:
    return phrases.DEFAULTS[key].format(**slots)


# --------------------------------------------------------------------------- success
def test_usb_success_speaks_a_live_description(cfg, monkeypatch) -> None:
    llm = use_llm(monkeypatch, FakeLlm())
    seen: list[httpx.Request] = []
    call = make_handler(cfg, transport_=transport(seen=seen))

    result = call({"camera": "书房", "question": "桌上有什么"})

    assert result.status == "ok"
    assert result.payload["say"] == say("vision.label_live", text=DESCRIPTION)
    assert result.payload["say"].startswith("现在")
    assert [str(r.url) for r in seen] == [cfg.tools.vision.usb_snapshot_url]
    assert seen[0].method == "GET"
    # The question reached the model, and the frame travelled as a data URI.
    content = llm.seen[0][0]["content"]
    assert "桌上有什么" in content[0]["text"]
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64," \
        + base64.b64encode(JPEG).decode("ascii")


def test_ha_alias_goes_through_camera_proxy_and_says_latest(cfg, monkeypatch) -> None:
    cfg.tools.ha.base_url = "http://ha.test:8123"
    cfg.tools.vision.cameras = {"门口": "camera.door"}
    monkeypatch.setattr(look.secrets, "get", lambda name: "tok-123")
    use_llm(monkeypatch, FakeLlm())
    seen: list[httpx.Request] = []
    call = make_handler(cfg, transport_=transport(seen=seen))

    result = call({"camera": "门口"})

    assert result.payload["say"] == say("vision.label_latest", text=DESCRIPTION)
    assert result.payload["say"].startswith("最近一次")
    assert [str(r.url) for r in seen] == ["http://ha.test:8123/api/camera_proxy/camera.door"]
    assert seen[0].headers["authorization"] == "Bearer tok-123"


def test_a_literal_entity_id_is_accepted_without_an_alias(cfg, monkeypatch) -> None:
    cfg.tools.ha.base_url = "http://ha.test:8123"
    use_llm(monkeypatch, FakeLlm())
    seen: list[httpx.Request] = []
    call = make_handler(cfg, transport_=transport(seen=seen))

    result = call({"camera": "camera.study"})

    assert result.payload["say"] == say("vision.label_latest", text=DESCRIPTION)
    assert [str(r.url) for r in seen] == ["http://ha.test:8123/api/camera_proxy/camera.study"]


def test_the_description_is_clipped_to_one_short_line(cfg, monkeypatch) -> None:
    use_llm(monkeypatch, FakeLlm(text="画面里" * 40))
    call = make_handler(cfg, transport_=transport())

    text = call({}).payload["say"]

    label = say("vision.label_live", text="")
    body = text.split("：", 1)[1]
    assert len(body) <= look.MAX_DESCRIPTION_CHARS
    assert body.endswith("…")
    assert text.startswith(label.split("：", 1)[0])


# --------------------------------------------------------------------------- failures
@pytest.mark.parametrize(("kwargs", "label"), [
    ({"status": 500}, "non-200"),
    ({"status": 503, "content_type": "text/plain"}, "camera service down"),
    ({"content_type": "text/html"}, "HTML instead of an image"),
    ({"content_type": "application/json"}, "JSON instead of an image"),
    ({"body": b""}, "empty body"),
    ({"error": httpx.ReadTimeout("slow")}, "fetch timeout"),
    ({"error": httpx.ConnectError("refused")}, "connection refused"),
])
def test_every_fetch_failure_says_it_cannot_see(cfg, monkeypatch, kwargs, label) -> None:
    use_llm(monkeypatch, FakeLlm())
    call = make_handler(cfg, transport_=transport(**kwargs))

    result = call({})

    assert result.status == "unreachable", label
    assert result.payload["say"] == say("vision.no_frame"), label


def test_a_frame_above_the_cap_is_rejected_after_being_read(cfg, monkeypatch) -> None:
    """R4: the cap is a post-read check, not a streaming guard - the name says so."""
    monkeypatch.setattr(look, "MAX_IMAGE_BYTES", 8)
    use_llm(monkeypatch, FakeLlm())
    call = make_handler(cfg, transport_=transport(body=JPEG))

    result = call({})

    assert result.payload["say"] == say("vision.no_frame")


def test_an_unconfigured_usb_camera_says_so(cfg, monkeypatch) -> None:
    cfg.tools.vision.usb_snapshot_url = ""
    use_llm(monkeypatch, FakeLlm())

    def never(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no camera is configured; nothing may be fetched")

    call = make_handler(cfg, transport_=httpx.MockTransport(never))

    result = call({})

    assert result.status == "empty"
    assert result.payload["say"] == say("vision.no_usb")


def test_a_vision_failure_keeps_the_frame_out_of_the_excuse(cfg, monkeypatch) -> None:
    use_llm(monkeypatch, FakeLlm(error=RuntimeError("vendor 400")))
    call = make_handler(cfg, transport_=transport())

    result = call({})

    assert result.status == "unreachable"
    assert result.payload["say"] == say("vision.describe_failed")


def test_an_empty_description_is_a_description_failure(cfg, monkeypatch) -> None:
    llm = use_llm(monkeypatch, FakeLlm(text="   "))
    call = make_handler(cfg, transport_=transport())

    assert call({}).payload["say"] == say("vision.describe_failed")
    assert llm.closed is True


def test_the_vision_round_is_bounded_by_vision_timeout(cfg, monkeypatch) -> None:
    cfg.tools.vision.timeout_s = 1.0
    use_llm(monkeypatch, FakeLlm(delay_s=5.0))
    call = make_handler(cfg, transport_=transport())

    result = call({})

    assert result.payload["say"] == say("vision.describe_failed")


def test_the_fetch_and_the_description_share_one_budget(cfg, monkeypatch) -> None:
    """Review R1: one look = one deadline, not 15 s + 15 s (the turn cap is 20 s).

    A camera that spends 14 of the 15 s leaves the description one second, and that
    (not a fresh 15) is what the vision round receives.
    """
    cfg.tools.vision.timeout_s = 15.0
    clock = Clock()
    recorded: list[float] = []

    async def fake_describe(config, jpeg, question, *, timeout):  # noqa: ARG001
        recorded.append(timeout)
        return "画面里有一张桌子。"

    monkeypatch.setattr(look, "_describe", fake_describe)

    def slow_camera(_request: httpx.Request) -> httpx.Response:
        clock.now += 14.0
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    call = make_handler(cfg, transport_=httpx.MockTransport(slow_camera), clock=clock)
    result = call({})

    assert result.payload["say"].startswith("现在")
    assert recorded == [pytest.approx(1.0)]


def test_the_whole_fallback_chain_shares_one_budget(cfg, monkeypatch) -> None:
    """A second provider must not get a fresh timeout (review R1, second pass).

    Two providers that each hang for 5 s under a 1 s budget: with a per-provider
    timeout the look takes 2 s (and the default 15 s x 2 would exceed the turn's 20 s
    tool timeout); with one deadline the second provider is never started.
    """
    cfg.tools.vision.timeout_s = 1.0
    first, second = FakeLlm(delay_s=5.0), FakeLlm(delay_s=5.0)
    monkeypatch.setattr(look, "llm_chain",
                        lambda providers: [("p1", first), ("p2", second)])
    call = make_handler(cfg, transport_=transport())

    started = time.monotonic()
    result = call({})
    elapsed = time.monotonic() - started

    assert result.payload["say"] == say("vision.describe_failed")
    assert elapsed < 1.5, f"the budget was not shared: {elapsed:.2f}s"
    assert first.seen, "the primary provider is tried first"
    assert second.seen == [], "the fallback must not get a fresh timeout"
    assert first.closed and second.closed


def test_a_fetch_that_uses_the_whole_budget_skips_the_vision_round(cfg, monkeypatch) -> None:
    """The frame arrived but the budget did not: say it could not be described.

    Spending a fresh timeout here is exactly what the runner's 20 s tool timeout would
    punish - the user would get the model's "the tool timed out" instead of our line.
    """
    cfg.tools.vision.timeout_s = 15.0
    clock = Clock()
    described: list[float] = []

    async def fake_describe(config, jpeg, question, *, timeout):  # noqa: ARG001
        described.append(timeout)
        return "不该走到这里。"

    monkeypatch.setattr(look, "_describe", fake_describe)

    def exhausted_camera(_request: httpx.Request) -> httpx.Response:
        clock.now += 15.0
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    call = make_handler(cfg, transport_=httpx.MockTransport(exhausted_camera), clock=clock)
    result = call({})

    assert described == [], "no vision call may start after the budget is gone"
    assert result.payload["say"] == say("vision.describe_failed")


def test_no_ready_provider_is_a_description_failure(cfg, monkeypatch) -> None:
    monkeypatch.setattr(look, "llm_chain", lambda providers: [])
    call = make_handler(cfg, transport_=transport())

    assert call({}).payload["say"] == say("vision.describe_failed")


# --------------------------------------------------------------------------- cameras
@pytest.mark.parametrize("camera", ["", "书房", "这里", "本机", "usb", "书房摄像头"])
def test_the_usb_spellings_all_mean_the_mac_camera(cfg, monkeypatch, camera) -> None:
    use_llm(monkeypatch, FakeLlm())
    seen: list[httpx.Request] = []
    call = make_handler(cfg, transport_=transport(seen=seen))

    assert call({"camera": camera}).payload["say"].startswith("现在")
    assert len(seen) == 1


@pytest.mark.parametrize("camera", ["客厅", "camera.Door", "门口", "https://evil.test/x"])
def test_an_unknown_camera_is_not_fetched(cfg, monkeypatch, camera) -> None:
    cfg.tools.vision.cameras = {"书房": "camera.study"}
    use_llm(monkeypatch, FakeLlm())
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    call = make_handler(cfg, transport_=httpx.MockTransport(recording))

    result = call({"camera": camera})

    # "empty" is the refusal; "unreachable" would mean a request was actually made.
    assert result.status == "empty"
    assert seen == [], f"an unknown camera must not be fetched: {[str(r.url) for r in seen]}"
    assert result.payload["say"] == say("vision.no_frame")


def test_an_alias_that_is_not_a_camera_entity_is_not_fetched(cfg, monkeypatch) -> None:
    """The alias VALUE is validated, not just the shape of the request.

    `base_url` is set on purpose (review C3): with it empty this test passed even with
    the entity-id check removed, because "no HA configured" already answers no_frame.
    The transport records instead of raising: an AssertionError raised inside the
    handler would be swallowed by `_fetch`'s own `except Exception` and only ever show
    up as "unreachable" - a mutation that DID send the request stayed green that way.
    """
    cfg.tools.ha.base_url = "http://ha.test:8123"
    cfg.tools.vision.cameras = {"门口": "sensor.not_a_camera"}
    use_llm(monkeypatch, FakeLlm())
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    call = make_handler(cfg, transport_=httpx.MockTransport(recording))

    result = call({"camera": "门口"})

    assert result.status == "empty"
    assert seen == [], f"a non-camera entity must not be fetched: {[str(r.url) for r in seen]}"
    assert result.payload["say"] == say("vision.no_frame")


def test_a_valid_alias_does_fetch_the_ha_camera(cfg, monkeypatch) -> None:
    """The control for the test above: the same setup with a real camera id fetches."""
    cfg.tools.ha.base_url = "http://ha.test:8123"
    cfg.tools.vision.cameras = {"门口": "camera.door"}
    use_llm(monkeypatch, FakeLlm())
    seen: list[httpx.Request] = []
    call = make_handler(cfg, transport_=transport(seen=seen))

    result = call({"camera": "门口"})

    assert result.payload["say"].startswith("最近一次")
    assert [str(request.url) for request in seen] == \
        ["http://ha.test:8123/api/camera_proxy/camera.door"]


def test_an_alias_without_an_ha_base_url_cannot_see(cfg, monkeypatch) -> None:
    cfg.tools.vision.cameras = {"门口": "camera.door"}
    cfg.tools.ha.base_url = ""
    use_llm(monkeypatch, FakeLlm())
    call = make_handler(cfg, transport_=transport())

    assert call({"camera": "门口"}).payload["say"] == say("vision.no_frame")


# --------------------------------------------------------------------------- cooldown
def test_the_second_look_inside_the_cooldown_is_refused(cfg, monkeypatch) -> None:
    cfg.tools.vision.cooldown_s = 10.0
    use_llm(monkeypatch, FakeLlm())
    clock = Clock()
    seen: list[httpx.Request] = []
    call = make_handler(cfg, transport_=transport(seen=seen), clock=clock)

    assert call({}).payload["say"].startswith("现在")
    clock.now += 1.0
    refused = call({})
    assert refused.status == "denied"
    assert refused.payload["say"] == say("vision.cooldown")
    assert len(seen) == 1, "the refused look must not touch the camera"

    clock.now += 11.0
    assert call({}).payload["say"].startswith("现在")
    assert len(seen) == 2


def test_a_zero_cooldown_allows_back_to_back_looks(cfg, monkeypatch) -> None:
    cfg.tools.vision.cooldown_s = 0.0
    use_llm(monkeypatch, FakeLlm())
    call = make_handler(cfg, transport_=transport(), clock=Clock())

    assert call({}).payload["say"].startswith("现在")
    assert call({}).payload["say"].startswith("现在")


def test_a_failed_look_also_consumes_the_cooldown(cfg, monkeypatch) -> None:
    """A broken camera must not be hammered once per turn."""
    cfg.tools.vision.cooldown_s = 10.0
    use_llm(monkeypatch, FakeLlm())
    clock = Clock()
    call = make_handler(cfg, transport_=transport(status=503), clock=clock)

    assert call({}).payload["say"] == say("vision.no_frame")
    clock.now += 1.0
    assert call({}).payload["say"] == say("vision.cooldown")


# --------------------------------------------------------------------------- privacy
def test_nothing_is_written_and_the_log_carries_no_picture(cfg, monkeypatch, caplog) -> None:
    use_llm(monkeypatch, FakeLlm())
    data_dir = Path(cfg.system.data_dir)
    before = sorted(str(p) for p in data_dir.rglob("*")) if data_dir.exists() else []
    caplog.set_level(logging.DEBUG)

    result = make_handler(cfg, transport_=transport())({"question": "有人吗"})

    after = sorted(str(p) for p in data_dir.rglob("*")) if data_dir.exists() else []
    assert before == after, "the frame must not touch the data directory"
    assert result.payload["say"].startswith("现在")

    records = [r for r in caplog.records if r.name == LOG]
    assert records, "the look logs its device, bytes and elapsed time"
    blob = base64.b64encode(JPEG).decode("ascii")
    for record in records:
        message = record.getMessage()
        assert "data:image" not in message
        assert blob not in message
        assert blob[:16] not in message
        assert "有人吗" not in message, "the question is not a log field"
    assert any("bytes=" in r.getMessage() and "elapsed_ms=" in r.getMessage()
               and "device=usb" in r.getMessage() for r in records)


# --------------------------------------------------------------------------- wire shape
def test_the_messages_carry_an_openai_image_part() -> None:
    plain = look._messages(JPEG, "")
    assert [part["type"] for part in plain[0]["content"]] == ["text", "image_url"]
    assert plain[0]["content"][1]["image_url"]["url"] == \
        "data:image/jpeg;base64," + base64.b64encode(JPEG).decode("ascii")
    assert "不要编" in plain[0]["content"][0]["text"]

    asked = look._messages(JPEG, "  门口  有人吗 ")
    assert "门口 有人吗" in asked[0]["content"][0]["text"]


def test_chat_payload_passes_the_image_part_through_untouched() -> None:
    """The existing provider stack is the wire layer: no second HTTP client exists."""
    messages = look._messages(JPEG, "有人吗")
    payload = chat_payload(
        ProviderConfig(kind="openai_compat", base_url="http://llm.test", model="m",
                       api_key_env="DEEPSEEK_API_KEY"),
        messages, [])
    assert payload["messages"] == messages
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,")


# --------------------------------------------------------------------------- contract
def test_the_spec_is_a_read_only_tool_named_look_for_the_model() -> None:
    assert look.LOOK_SPEC.name == "local.look"
    assert look.LOOK_SPEC.llm_name == "look"
    assert look.LOOK_SPEC.permission is Permission.READ
    schema = look.LOOK_SPEC.input_schema
    assert set(schema["properties"]) == {"question", "camera"}
    assert look.LOOK_SPEC.as_llm_tool()["name"] == "look"


def test_every_vision_phrase_is_in_the_book() -> None:
    for key in ("vision.label_live", "vision.label_latest", "vision.no_frame",
                "vision.no_usb", "vision.describe_failed", "vision.cooldown"):
        assert key in phrases.DEFAULTS, key
        assert phrases.book.render(key, text="画面")
    live = phrases.book.render("vision.label_live", text="画面")
    latest = phrases.book.render("vision.label_latest", text="画面")
    assert live != latest
    assert "现在" in live and "最近一次" in latest


def test_the_tool_never_raises_on_junk_arguments(cfg, monkeypatch) -> None:
    cfg.tools.vision.cooldown_s = 0.0
    use_llm(monkeypatch, FakeLlm())
    call = make_handler(cfg, transport_=transport())
    assert call({"camera": None, "question": None}).payload["say"].startswith("现在")
    # A non-string camera is simply an unknown camera: one sentence, no exception.
    junk = call({"camera": 42, "question": ["x"]})
    assert junk.payload["say"] == say("vision.no_frame")


# --------------------------------------------------------------------------- wiring
def test_local_look_reaches_the_runners_tool_table_and_debug_endpoint(tmp_path) -> None:
    """Acceptance 9+10: the app's real assembly, and both names resolve on debug/tool.

    The handler is replaced after startup so the console path is exercised without a
    camera, an LLM or a socket.
    """
    from rui.web.api.app import create_app

    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    app = create_app(cfg)
    with TestClient(app) as client:
        specs = {spec.name: spec for spec, _handler in app.state.runner.tools}
        assert "local.look" in specs, sorted(specs)
        assert specs["local.look"].llm_name == "look"
        assert specs["local.look"].permission is Permission.READ
        assert "look" in app.state.runner._handlers

        calls: list[dict] = []

        async def stub(args: dict) -> ToolResult:
            calls.append(dict(args))
            return ToolResult.say("stub 画面")

        app.state.runner._handlers["look"] = stub

        body = client.post("/api/v1/debug/tool",
                           json={"name": "local.look",
                                 "args": {"camera": "书房"}}).json()
        assert body["name"] == "local.look"
        assert body["rendered"] == "stub 画面"
        assert calls == [{"camera": "书房"}]

        # The model's name keeps working too.
        assert client.post("/api/v1/debug/tool", json={"name": "look"}).json()[
            "rendered"] == "stub 画面"

        missing = client.post("/api/v1/debug/tool", json={"name": "local.nope"})
        assert missing.status_code == 404
