"""`local.tell_joke` must answer with nothing configured, and nothing on the wire.

Every test here runs against `httpx.MockTransport` or calls the handler directly: the
joke service is optional infrastructure, not something the suite may depend on. The
four degradation paths are pinned because the tool's real contract is "never raise" -
a tool call that explodes takes the whole spoken turn with it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import time
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from rui.config import Config
from rui.config.catalog import document
from rui.tools import build_local_tools, joke
from rui.tools.contract import Permission

LOG = "rui.tools.joke"


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch):
    """No test may read the operator's real JOKE_SERVICE_TOKEN (or any secret)."""
    monkeypatch.setattr(joke.secrets, "get", lambda name: "")


@pytest.fixture(autouse=True)
def _fresh_sample_cache():
    joke._fallback_items.cache_clear()
    yield
    joke._fallback_items.cache_clear()


@pytest.fixture()
def cfg(tmp_path):
    conf = Config()
    conf.system.data_dir = str(tmp_path / "data")
    # The mock transport never leaves the process; the SSRF guard is exercised in
    # tests/test_netguard.py, and a made-up hostname has no DNS answer to check.
    conf.security.outbound.enabled = False
    conf.tools.joke_service_url = "http://joke.test:8099"
    return conf


def run(cfg: Config, args: dict, transport=None) -> str:
    return asyncio.run(joke.tell_joke(cfg, args, transport=transport))


def sample_texts() -> set[str]:
    """Every line the fallback may hand back, in the form the tool speaks it."""
    out: set[str] = set()
    for item in joke._fallback_items():
        text = joke._item_text(item)
        out.add(text)
        if item["kind"] == "riddle":
            out.add(f"谜语：{text}")
    return out


def warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOG and r.levelno >= logging.WARNING]


def mock(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ------------------------------------------------------------------ configuration


def test_the_service_is_off_by_default_and_the_timeout_is_bounded() -> None:
    conf = Config()
    assert conf.tools.joke_service_url == ""
    assert conf.tools.joke_timeout_s == 3.0
    assert Config(tools={"joke_timeout_s": 0.5}).tools.joke_timeout_s == 0.5
    assert Config(tools={"joke_timeout_s": 10}).tools.joke_timeout_s == 10.0
    for bad in (0.4, 10.5):
        with pytest.raises(ValidationError):
            Config(tools={"joke_timeout_s": bad})


def test_schema_example_and_catalog_agree() -> None:
    conf = Config()
    body = document(conf)
    fields = {field["path"]: field for field in body["fields"]}
    url, timeout = fields["tools.joke_service_url"], fields["tools.joke_timeout_s"]
    for field in (url, timeout):
        assert field["group"] == "tools.joke"
        assert field["tier"] == 4
        assert field["effect"] == "restart"
    assert (timeout["min"], timeout["max"]) == (0.5, 10)
    assert body["values"]["tools.joke_service_url"] == ""
    assert body["values"]["tools.joke_timeout_s"] == 3.0
    groups = {group["id"]: group for group in body["groups"]}
    assert groups["tools.joke"]["effect"] == "restart"
    # The example config documents the same two keys under tools.
    example = Path(__file__).resolve().parents[1] / "config.example.yaml"
    text = example.read_text(encoding="utf-8")
    assert "joke_service_url:" in text and "joke_timeout_s:" in text
    assert 'joke_service_url: ""' in text


# ------------------------------------------------------------------- registration


def test_the_tool_is_registered_as_a_read_only_local_tool() -> None:
    pairs = build_local_tools(Config())
    spec = next(spec for spec, _ in pairs if spec.name == "local.tell_joke")
    assert spec.permission is Permission.READ
    assert spec.source == "local"
    assert spec.llm_name == "tell_joke"
    # The two-stage riddle and the "tell me another" loop are part of the prompt.
    assert "谜语" in spec.description
    assert "action=answer" in spec.description
    assert "换一个" in spec.description
    props = spec.input_schema["properties"]
    assert props["action"]["enum"] == ["new", "answer"]
    assert props["kind"]["enum"] == ["any", "joke", "riddle"]
    assert "topic" in props


def test_the_tool_does_not_depend_on_the_music_layer() -> None:
    conf = Config()
    assert conf.music.enabled is False
    assert any(spec.name == "local.tell_joke" for spec, _ in build_local_tools(conf))


# ----------------------------------------------------------------- bundled samples


def test_the_bundled_file_holds_exactly_twenty_jokes_and_riddles() -> None:
    payload = json.loads(joke._fallback_source().read_text(encoding="utf-8"))
    assert isinstance(payload, list) and len(payload) == 20
    items = joke._fallback_items()
    assert len(items) == 20
    assert {item["kind"] for item in items} == {"joke", "riddle"}
    riddles = [item for item in items if item["kind"] == "riddle"]
    assert len(riddles) >= 5
    assert all(item["setup"] and item["answer"] for item in riddles)
    assert all(len(joke._item_text(item)) <= joke.MAX_CHARS for item in items)


def test_a_missing_sample_file_falls_back_to_the_emergency_set(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    assert joke._fallback_source().is_file()
    assert joke._fallback_items()  # warm the cache, then point it at nothing
    joke._fallback_items.cache_clear()
    missing = tmp_path / "no-such-samples.json"
    original = joke._fallback_source
    joke._fallback_source = lambda: missing
    try:
        items = joke._fallback_items()
    finally:
        joke._fallback_source = original
    assert items and len(items) < 20
    assert len(warnings(caplog)) == 1
    assert "unreadable" in warnings(caplog)[0].getMessage()


def test_a_corrupt_sample_file_also_falls_back(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    broken = tmp_path / "broken.json"
    broken.write_text("{not a list", encoding="utf-8")
    original = joke._fallback_source
    joke._fallback_source = lambda: broken
    try:
        items = joke._fallback_items()
    finally:
        joke._fallback_source = original
    assert items == joke._EMERGENCY_ITEMS
    assert len(warnings(caplog)) == 1


# ------------------------------------------------------------------- the happy path


def test_a_configured_service_supplies_the_joke(cfg, monkeypatch, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"kind": "joke",
                                         "text": "灯泡为什么不用上班？因为它已经亮了。"})

    monkeypatch.setattr(joke.secrets, "get",
                        lambda name: "s3cret-token" if name == joke.TOKEN_SECRET else "")
    text = run(cfg, {"kind": "joke", "topic": "程序员"}, mock(handler))
    assert text == "灯泡为什么不用上班？因为它已经亮了。"

    request = seen[0]
    assert request.method == "GET"
    assert request.url.host == "joke.test" and request.url.port == 8099
    assert request.url.path == "/v1/joke"
    state = json.loads((Path(cfg.system.data_dir) / joke.STATE_FILE).read_text(encoding="utf-8"))
    assert dict(request.url.params) == {"kind": "joke", "topic": "程序员",
                                        "pid": state["pid"]}
    assert request.headers["X-Internal-Token"] == "s3cret-token"
    assert not warnings(caplog)
    assert "s3cret-token" not in caplog.text


def test_without_the_secret_no_token_header_is_sent(cfg) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "没有令牌也能讲。"})

    assert run(cfg, {}, mock(handler)) == "没有令牌也能讲。"
    assert "x-internal-token" not in seen[0].headers


def test_a_remote_riddle_is_still_asked_in_two_stages(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    replies = iter([
        {"setup": "什么门永远关不上？", "answer": "球门。"},
        {"setup": "什么东西有头有尾却没有身体？", "answer": "硬币。"},
    ])
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        payload = next(replies)
        return httpx.Response(200, json={"kind": "riddle", **payload})

    first = run(cfg, {"kind": "riddle"}, mock(handler))
    assert first == "谜语：什么门永远关不上？"
    assert "球门" not in first

    answer = run(cfg, {"action": "answer"}, mock(handler))
    assert answer == "答案：球门。"

    again = run(cfg, {"action": "answer"}, mock(handler))
    assert "球门" not in again and "揭晓过" in again

    second = run(cfg, {"kind": "riddle"}, mock(handler))
    assert second == "谜语：什么东西有头有尾却没有身体？"
    assert second != first
    # Only the two `new` calls reach the service; answering is local state.
    assert len(calls) == 2
    assert not warnings(caplog)


def test_new_does_not_repeat_the_previous_line(cfg) -> None:
    cfg.tools.joke_service_url = ""
    texts = {item["setup"] for item in joke._fallback_items() if item["kind"] == "riddle"}
    first = run(cfg, {"kind": "riddle"})
    second = run(cfg, {"kind": "riddle"})
    assert first.startswith("谜语：") and second.startswith("谜语：")
    assert first != second
    assert first.removeprefix("谜语：") in texts
    assert second.removeprefix("谜语：") in texts


def test_answering_without_a_riddle_asks_for_one(cfg) -> None:
    text = run(cfg, {"action": "answer"})
    assert "还没出过谜语" in text


def test_a_long_remote_joke_is_clipped_for_one_breath(cfg) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "很长的笑话。" * 500})

    text = run(cfg, {"kind": "joke"}, mock(handler))
    assert text and len(text) <= joke.MAX_CHARS


# ---------------------------------------------------------------- degradation paths


def test_an_empty_url_uses_a_sample_and_warns_once(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    cfg.tools.joke_service_url = ""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no request may be sent when no URL is configured")

    text = run(cfg, {"kind": "joke"}, mock(handler))
    assert text in sample_texts()
    assert len(warnings(caplog)) == 1
    assert "empty" in warnings(caplog)[0].getMessage()


def test_a_timeout_uses_a_sample_and_warns_once(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    text = run(cfg, {"kind": "joke"}, mock(handler))
    assert text in sample_texts()
    assert len(warnings(caplog)) == 1
    assert "timed out" in warnings(caplog)[0].getMessage()


def test_http_500_uses_a_sample_and_warns_once(cfg, monkeypatch, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    monkeypatch.setattr(joke.secrets, "get", lambda name: "s3cret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    text = run(cfg, {"kind": "joke"}, mock(handler))
    assert text in sample_texts()
    assert len(warnings(caplog)) == 1
    assert "500" in warnings(caplog)[0].getMessage()
    assert "s3cret-token" not in caplog.text


def test_a_network_error_uses_a_sample_and_warns_once(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    text = run(cfg, {"kind": "joke"}, mock(handler))
    assert text in sample_texts()
    assert len(warnings(caplog)) == 1
    assert "ConnectError" in warnings(caplog)[0].getMessage()


def test_invalid_json_uses_a_sample_and_warns_once(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    text = run(cfg, {"kind": "joke"}, mock(handler))
    assert text in sample_texts()
    assert len(warnings(caplog)) == 1


def test_an_empty_payload_uses_a_sample_and_warns_once(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"kind": "joke"})

    text = run(cfg, {"kind": "joke"}, mock(handler))
    assert text in sample_texts()
    assert len(warnings(caplog)) == 1


# -------------------------------------------------------------------- the state file


def test_the_pid_is_stable_and_the_state_file_is_private(cfg, tmp_path) -> None:
    pids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pids.append(request.url.params["pid"])
        return httpx.Response(200, json={"text": "一个笑话。"})

    first = run(cfg, {}, mock(handler))
    second = run(cfg, {}, mock(handler))
    assert first == second == "一个笑话。"
    assert len(set(pids)) == 1 and len(pids[0]) == 32
    state_path = Path(cfg.system.data_dir) / joke.STATE_FILE
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert json.loads(state_path.read_text(encoding="utf-8"))["pid"] == pids[0]


def test_a_corrupt_state_file_is_rebuilt_with_a_warning(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    cfg.tools.joke_service_url = ""
    state_path = Path(cfg.system.data_dir) / joke.STATE_FILE
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{broken", encoding="utf-8")

    text = run(cfg, {"kind": "joke"})
    assert text in sample_texts()
    messages = [record.getMessage() for record in warnings(caplog)]
    assert any("unreadable" in message for message in messages)
    rebuilt = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(rebuilt["pid"]) == 32


def test_a_failed_state_write_only_warns(cfg, monkeypatch, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    cfg.tools.joke_service_url = ""

    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(joke.os, "replace", boom)
    text = run(cfg, {"kind": "joke"})
    assert text in sample_texts()
    assert any("cannot write" in record.getMessage() for record in warnings(caplog))


# ------------------------------------------------------- optional audio (task-4)
# The service may render the joke itself. Then the runner plays that file instead of
# synthesising the text; the chain is audio -> text -> bundled sample, and every
# failure of the audio step is one warning plus the text.

def test_a_service_side_audio_path_is_ignored(cfg, tmp_path, caplog) -> None:
    """The contract is a URL rui fetches; a path on the service's disk means nothing here."""
    caplog.set_level(logging.WARNING, logger=LOG)
    existing = tmp_path / "on_the_service.wav"
    existing.write_bytes(_wav_bytes())
    payloads = (
        {"text": "文本笑话。", "audio_path": str(existing)},
        {"text": "文本笑话。", "audio_path": "https://example.com/a.wav"},
        # The old crash (`expanduser` on an unknown user) is unreachable now.
        {"text": "文本笑话。", "audio_path": "~nosuchuser_xyz/a.wav"},
        {"audio_path": str(existing)},          # no text and no URL: bundled sample
    )
    for payload in payloads[:3]:
        out = run(cfg, {}, mock(lambda request, body=payload: httpx.Response(200, json=body)))
        assert out == "文本笑话。"
    assert not warnings(caplog)
    # Nothing usable at all: the bundled samples, with the one honest warning.
    caplog.clear()
    out = run(cfg, {}, mock(lambda request, body=payloads[3]: httpx.Response(200, json=body)))
    assert out in sample_texts()
    assert len(warnings(caplog)) == 1
    assert "no speakable text" in warnings(caplog)[0].getMessage()


def test_the_bundled_samples_never_carry_audio() -> None:
    assert all("audio_path" not in item for item in joke._fallback_items())


class _AudioAsr:
    async def transcribe(self, pcm: bytes, sample_rate: int):
        from rui.providers.base import Transcript

        return Transcript(text="讲个笑话")


class _AudioLlm:
    def __init__(self, rounds: list[list[object]]) -> None:
        self.rounds = rounds
        self.seen: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict]):
        self.seen.append([dict(m) for m in messages])
        for chunk in self.rounds.pop(0):
            yield chunk


class _AudioTts:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str, voice: str = ""):
        self.texts.append(text)
        yield b"\x01\x00" * 320


def _patch_audio(monkeypatch, llm: _AudioLlm, tts: _AudioTts) -> None:
    import rui.session.turn as turn

    monkeypatch.setattr(turn, "asr_chain", lambda providers: [("a", _AudioAsr())])
    monkeypatch.setattr(turn, "llm_chain", lambda providers: [("l0", llm)])
    monkeypatch.setattr(turn, "tts_chain", lambda providers, rate: [("t0", tts)])


def _tool_call() -> object:
    from rui.providers.base import ChatChunk

    return ChatChunk(delta="", finish_reason="tool_calls",
                     tool_calls=[{"id": "t1", "name": "tell_joke", "arguments": "{}"}])


def _run_turn(monkeypatch, cfg, handler, *, play_file=None, rounds=None):
    import asyncio

    from rui.config.schema import Config
    from rui.session.turn import TurnRunner
    from rui.trace import TurnTrace
    from rui.web.api.app import _direct_speech

    llm = _AudioLlm(rounds or [[_tool_call()], []])
    tts = _AudioTts()
    _patch_audio(monkeypatch, llm, tts)
    kwargs = {}
    if play_file is not None:
        kwargs["play_file"] = play_file
    runner = TurnRunner(Config().providers, tools=[(joke.JOKE_SPEC, handler)],
                        direct_hook=_direct_speech, **kwargs)

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    result = asyncio.run(runner.run(b"", speak, TurnTrace(device_id="living"),
                                    transcript="讲个笑话"))
    return runner, result, llm, tts


def _remote_joke(cfg, payload: dict):
    """A tool handler that goes through the real joke client with a mock transport."""
    async def handler(_args: dict):
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        return await joke.tell_joke(cfg, _args, transport=mock(respond))

    return handler


def test_a_dead_audio_plane_falls_back_to_tts(monkeypatch, cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="rui.session.turn")
    transport = _audio_transport({"text": "音频笑话。", "audio_url": "http://joke.test/a.wav"})

    async def handler(_args: dict):
        return await joke.tell_joke(cfg, _args, transport=transport)

    async def play_file(path: str) -> None:
        raise RuntimeError("no audio plane connected")

    _runner, result, _llm, tts = _run_turn(monkeypatch, cfg, handler, play_file=play_file)
    assert tts.texts == ["音频笑话。"]
    assert result.reply == "音频笑话。"
    turn_warnings = [r for r in caplog.records
                     if r.name == "rui.session.turn" and r.levelno >= logging.WARNING]
    assert len(turn_warnings) == 1
    assert "cannot play" in turn_warnings[0].getMessage()


def test_a_missing_audio_file_at_turn_time_falls_back_to_tts(monkeypatch, cfg, tmp_path,
                                                             caplog) -> None:
    """The tool already checked, but the file can vanish between check and playback."""
    caplog.set_level(logging.WARNING, logger="rui.session.turn")
    gone = tmp_path / "gone.wav"
    # The handler hands the runner a path that no longer exists (no tool-level check).
    async def handler(_args: dict):
        from rui.tools.contract import ToolResult

        return ToolResult(status="ok", payload={"say": "音频笑话。", "audio_path": str(gone)})

    async def play_file(path: str) -> None:  # pragma: no cover - must not be reached
        raise AssertionError("a missing file must not be sent to the plane")

    _runner, result, _llm, tts = _run_turn(monkeypatch, cfg, handler, play_file=play_file)
    assert tts.texts == ["音频笑话。"]
    assert result.reply == "音频笑话。"
    turn_warnings = [r for r in caplog.records
                     if r.name == "rui.session.turn" and r.levelno >= logging.WARNING]
    assert len(turn_warnings) == 1


def test_without_a_play_channel_the_audio_joke_is_synthesised(monkeypatch, cfg,
                                                              caplog) -> None:
    caplog.set_level(logging.WARNING, logger="rui.session.turn")
    transport = _audio_transport({"text": "音频笑话。", "audio_url": "http://joke.test/a.wav"})

    async def handler(_args: dict):
        return await joke.tell_joke(cfg, _args, transport=transport)

    _runner, result, _llm, tts = _run_turn(monkeypatch, cfg, handler)
    assert tts.texts == ["音频笑话。"]
    assert result.reply == "音频笑话。"
    turn_warnings = [r for r in caplog.records
                     if r.name == "rui.session.turn" and r.levelno >= logging.WARNING]
    assert len(turn_warnings) == 1
    assert "no play_file" in turn_warnings[0].getMessage()


def test_a_text_only_joke_takes_the_ordinary_two_round_path(monkeypatch, cfg) -> None:
    handler = _remote_joke(cfg, {"text": "文本笑话。"})
    from rui.providers.base import ChatChunk

    played: list[str] = []

    async def play_file(path: str) -> None:
        played.append(path)

    runner, result, llm, tts = _run_turn(
        monkeypatch, cfg, handler, play_file=play_file,
        rounds=[[_tool_call()], [ChatChunk(delta="（模型复述）文本笑话。")]],
    )
    assert played == []
    assert len(llm.seen) == 2
    assert tts.texts  # the model's second-round reply is spoken
    assert "文本笑话" in result.reply
    assert runner.history[-1]["content"] == result.reply




def test_the_direct_hook_only_takes_audio_from_a_joke_result() -> None:
    from rui.tools.contract import ToolResult
    from rui.web.api.app import _direct_speech

    result = ToolResult(status="ok", payload={"say": "音频笑话。", "audio_path": "/tmp/a.wav"})
    speech = _direct_speech("tell_joke", "讲个笑话", result)
    assert speech is not None
    assert (speech.text, speech.audio_path) == ("音频笑话。", "/tmp/a.wav")
    assert speech.history == [
        {"role": "user", "content": "讲个笑话"},
        {"role": "assistant", "content": "音频笑话。"},
    ]
    # No audio: the plain text path, nothing to play.
    assert _direct_speech("tell_joke", "讲个笑话",
                          ToolResult(status="ok", payload={"say": "文本笑话。"})) is None
    # Audio from any other tool is not this channel.
    assert _direct_speech("weather", "天气", result) is None
    assert _direct_speech("tell_joke", "讲个笑话", "文本笑话。") is None


# ------------------------------------------------- audio_url download (task-4)
# The mainline contract: the service names a clip by URL and rui fetches it into
# `<data_dir>/tmp`. The plane only reads WAV, so everything else is a text fallback.

def _wav_bytes(rate: int = 16000, frames: int = 320) -> bytes:
    """A real mono s16 WAV: the plane's reader must accept it, not just the magic."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x01\x00" * frames)
    return buffer.getvalue()


def _broken_fmt_wav() -> bytes:
    """RIFF/WAVE magic but zero channels: passes a magic check, cannot be read."""
    return (b"RIFF" + (36).to_bytes(4, "little") + b"WAVE"
            + b"fmt " + (16).to_bytes(4, "little")
            + (0).to_bytes(2, "little")       # channels = 0
            + (1).to_bytes(2, "little")       # PCM
            + (16000).to_bytes(4, "little")
            + (32000).to_bytes(4, "little")
            + (2).to_bytes(2, "little")
            + (16).to_bytes(2, "little")
            + b"data" + (0).to_bytes(4, "little"))


WAV_BYTES = _wav_bytes()


def _audio_transport(payload: dict, *, wav: bytes = WAV_BYTES, audio_status: int = 200,
                     content_type: str = "audio/wav") -> httpx.MockTransport:
    """JSON at /v1/joke, the clip at any other path - one transport for both calls."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/joke":
            return httpx.Response(200, json=payload)
        if audio_status != 200:
            return httpx.Response(audio_status, text="nope")
        return httpx.Response(200, content=wav, headers={"content-type": content_type})

    return mock(handler)


def test_an_audio_url_is_downloaded_into_data_dir_tmp(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    payload = {"text": "音频笑话。", "audio_url": "http://joke.test/audio/1.wav"}
    out = run(cfg, {}, _audio_transport(payload))
    from rui.tools.contract import ToolResult

    assert isinstance(out, ToolResult)
    path = Path(out.payload["audio_path"])
    assert path.parent == Path(cfg.system.data_dir) / joke.AUDIO_DIRNAME
    assert path.name.startswith(joke.AUDIO_PREFIX) and path.suffix == ".wav"
    assert path.is_file() and path.read_bytes()[:4] == b"RIFF"
    # The plane's own decoder must accept it (the turn skips TTS based on this).
    from rui.audio.wav import read_wav_mono

    samples, rate = read_wav_mono(path)
    assert rate == 16000 and samples.size > 0
    assert out.payload["say"] == "音频笑话。"
    assert not warnings(caplog)


def test_a_failed_audio_download_falls_back_to_the_text(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    payload = {"text": "文本笑话。", "audio_url": "http://joke.test/a.wav"}
    out = run(cfg, {}, _audio_transport(payload, audio_status=500))
    assert out == "文本笑话。"
    assert len(warnings(caplog)) == 1
    assert "HTTP 500" in warnings(caplog)[0].getMessage()
    # The URL never reaches the log: a signed clip link is a credential.
    assert "joke.test" not in caplog.text


def test_an_audio_download_timeout_falls_back_to_the_text(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    payload = {"text": "文本笑话。", "audio_url": "http://joke.test/a.wav"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/joke":
            return httpx.Response(200, json=payload)
        raise httpx.ReadTimeout("slow clip", request=request)

    out = run(cfg, {}, mock(handler))
    assert out == "文本笑话。"
    assert len(warnings(caplog)) == 1
    assert "timeout" in warnings(caplog)[0].getMessage()


def test_an_oversized_clip_is_rejected(cfg, monkeypatch, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    monkeypatch.setattr(joke, "AUDIO_MAX_BYTES", 32)
    payload = {"text": "文本笑话。", "audio_url": "http://joke.test/a.wav"}
    out = run(cfg, {}, _audio_transport(
        payload, wav=b"RIFF" + b"\x00" * 8 + b"WAVE" + b"\x00" * 64))
    assert out == "文本笑话。"
    assert len(warnings(caplog)) == 1
    assert "too large" in warnings(caplog)[0].getMessage()


def test_a_non_wav_clip_is_rejected(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    payload = {"text": "文本笑话。", "audio_url": "http://joke.test/a.mp3"}
    out = run(cfg, {}, _audio_transport(payload, content_type="audio/mpeg"))
    assert out == "文本笑话。"
    assert len(warnings(caplog)) == 1
    assert "not a wav" in warnings(caplog)[0].getMessage()


def test_a_non_http_audio_url_is_never_requested(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    calls: list[str] = []
    payload = {"text": "文本笑话。", "audio_url": "ftp://joke.test/a.wav"}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=payload)

    out = run(cfg, {}, mock(handler))
    assert out == "文本笑话。"
    assert len(calls) == 1, "only the JSON call may happen"
    assert len(warnings(caplog)) == 1
    assert "http(s)" in warnings(caplog)[0].getMessage()


def test_audio_only_with_a_failed_download_uses_a_bundled_sample(cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    payload = {"audio_url": "http://joke.test/a.wav"}
    out = run(cfg, {}, _audio_transport(payload, audio_status=500))
    assert out in sample_texts()
    assert len(warnings(caplog)) == 1


def test_old_downloaded_clips_are_cleaned_up(cfg) -> None:
    from rui.tools.contract import ToolResult

    directory = Path(cfg.system.data_dir) / joke.AUDIO_DIRNAME
    directory.mkdir(parents=True)
    stale = directory / f"{joke.AUDIO_PREFIX}stale.wav"
    stale.write_bytes(b"old")
    long_ago = time.time() - 2 * joke.AUDIO_CACHE_TTL_S
    os.utime(stale, (long_ago, long_ago))

    payload = {"text": "音频笑话。", "audio_url": "http://joke.test/a.wav"}
    out = run(cfg, {}, _audio_transport(payload))
    assert isinstance(out, ToolResult)
    assert not stale.exists()
    assert Path(out.payload["audio_path"]).is_file()


def test_a_turn_plays_a_downloaded_audio_url(monkeypatch, cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    payload = {"text": "音频笑话。", "audio_url": "http://joke.test/a.wav"}
    transport = _audio_transport(payload)

    async def handler(_args: dict):
        return await joke.tell_joke(cfg, _args, transport=transport)

    played: list[str] = []

    async def play_file(path: str) -> None:
        played.append(path)

    runner, result, llm, tts = _run_turn(monkeypatch, cfg, handler, play_file=play_file)
    assert len(played) == 1
    assert Path(played[0]).parent == Path(cfg.system.data_dir) / joke.AUDIO_DIRNAME
    assert tts.texts == [], "a downloaded clip must not also be synthesised"
    assert len(llm.seen) == 1, "the text is the answer; no second model round"
    assert result.reply == "音频笑话。"
    assert runner.history == [
        {"role": "user", "content": "讲个笑话"},
        {"role": "assistant", "content": "音频笑话。"},
    ]
    assert not warnings(caplog)


def test_a_wav_with_a_broken_fmt_chunk_falls_back_to_the_text(cfg, caplog) -> None:
    """Magic-only validation is not enough: the player would fail after TTS was skipped."""
    caplog.set_level(logging.WARNING, logger=LOG)
    payload = {"text": "文本笑话。", "audio_url": "http://joke.test/a.wav"}
    out = run(cfg, {}, _audio_transport(payload, wav=_broken_fmt_wav()))
    assert out == "文本笑话。"
    assert len(warnings(caplog)) == 1


def test_audio_only_download_gets_a_spoken_fallback(cfg) -> None:
    from rui.tools.contract import ToolResult

    out = run(cfg, {}, _audio_transport({"audio_url": "http://joke.test/a.wav"}))
    assert isinstance(out, ToolResult)
    assert out.payload["say"] == joke.AUDIO_ONLY_SAY
    assert Path(out.payload["audio_path"]).is_file()


def test_audio_only_with_a_dead_plane_speaks_the_fallback(monkeypatch, cfg, caplog) -> None:
    """Audio-only + playback failure used to be a silent turn; the say line is now read."""
    caplog.set_level(logging.WARNING, logger="rui.session.turn")
    transport = _audio_transport({"audio_url": "http://joke.test/a.wav"})

    async def handler(_args: dict):
        return await joke.tell_joke(cfg, _args, transport=transport)

    async def play_file(path: str) -> None:
        raise RuntimeError("no audio plane connected")

    _runner, result, _llm, tts = _run_turn(monkeypatch, cfg, handler, play_file=play_file)
    assert tts.texts == [joke.AUDIO_ONLY_SAY]
    assert result.reply == joke.AUDIO_ONLY_SAY


def test_an_audio_only_clip_records_the_fallback_line(monkeypatch, cfg, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOG)
    transport = _audio_transport({"audio_url": "http://joke.test/a.wav"})

    async def handler(_args: dict):
        return await joke.tell_joke(cfg, _args, transport=transport)

    played: list[str] = []

    async def play_file(path: str) -> None:
        played.append(path)

    runner, result, llm, tts = _run_turn(monkeypatch, cfg, handler, play_file=play_file)
    assert len(played) == 1
    assert tts.texts == []
    assert len(llm.seen) == 1
    assert result.reply == joke.AUDIO_ONLY_SAY
    assert runner.history[-1] == {"role": "assistant", "content": joke.AUDIO_ONLY_SAY}
    assert not warnings(caplog)


def test_the_audio_download_obeys_the_ssrf_policy(cfg, caplog) -> None:
    """A loopback clip URL is blocked before the transport, with exactly one warning."""
    caplog.set_level(logging.WARNING, logger=LOG)
    cfg.security.outbound.enabled = True
    cfg.security.outbound.allow_loopback = False
    # An IP literal in the LAN range needs no DNS and is allowed by default.
    cfg.tools.joke_service_url = "http://192.168.5.10:8099"
    seen: list[str] = []
    payload = {"text": "文本笑话。", "audio_url": "http://127.0.0.1:9/a.wav"}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/v1/joke":
            return httpx.Response(200, json=payload)
        return httpx.Response(200, content=WAV_BYTES,
                              headers={"content-type": "audio/wav"})

    out = run(cfg, {}, mock(handler))
    assert out == "文本笑话。"
    assert len(seen) == 1, "the blocked clip URL must never reach the transport"
    assert len(warnings(caplog)) == 1
    assert "OutboundBlocked" in warnings(caplog)[0].getMessage()
