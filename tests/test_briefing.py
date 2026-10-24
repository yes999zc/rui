"""The daily briefing passthrough: script shape, history, 「第 N 条」 refs.

D1c ④'s pure half. The old build's measured contract (docs/d1c-notes.md §7):
the script is spoken **as written**, the model only gets a "ready" notice, and the
verbatim script goes into history so 「第三条再说说」 still works next turn.
"""
from __future__ import annotations

import datetime as dt

from rui.session import briefing
from rui.session.briefing import BRIEFING_STATUS, Briefing, from_news


def test_the_script_keeps_the_old_build_shape() -> None:
    """Old tools/news.py:126-136: dated head, 第一/第二 body, optional 微语."""
    brief = from_news(["今天下雨", "明天转晴"], date=dt.date(2026, 10, 23),
                      tip="周末愉快")
    assert brief is not None
    assert brief.count == 2
    assert brief.speech_text == (
        "今天是10月23日，星期五。给你念几条新闻。"
        "第一，今天下雨。第二，明天转晴。最后一句微语：周末愉快")


def test_from_news_normalises_the_shapes_the_sources_return() -> None:
    # Strings and {"title": ...} dicts both occur (ours vs the old _titles()).
    items = ["  今天   下雨 ", {"title": "明天转晴"}, {"title": ""}, "已经有句号。"]
    brief = from_news(items, date="2026-10-22", limit=3)
    assert brief is not None
    assert brief.items == ("今天 下雨。", "明天转晴。", "已经有句号。")
    # the whole digest dict is accepted too, tip included
    digest = {"news": [{"title": "A"}, {"title": "B"}], "tip": "晚安"}
    whole = from_news(digest, date="2026-10-22")
    assert whole is not None
    assert whole.tip == "晚安" and whole.count == 2
    # limit is clamped to 1..10 by the caller contract
    many = [f"item {i}" for i in range(20)]
    assert from_news(many, limit=99).count == briefing.MAX_ITEMS  # type: ignore[union-attr]
    assert from_news(many, limit=0).count == 1                    # type: ignore[union-attr]


def test_an_empty_feed_is_none_not_an_empty_header() -> None:
    assert from_news([]) is None
    assert from_news({"news": [], "tip": "x"}) is None
    assert from_news([{"title": ""}, "   "]) is None


def test_the_payload_carries_no_script_so_the_model_cannot_re_read_it() -> None:
    """Old assistant_v3.py:906-913: the model saw a notice, never the text."""
    brief = from_news(["今天下雨", "明天转晴"], date=dt.date(2026, 10, 23))
    assert brief is not None
    payload = brief.to_payload()
    assert payload == {"status": BRIEFING_STATUS, "date": "2026-10-23",
                       "count": 2, "source": "60s"}
    assert brief.speech_text not in str(payload)
    notice = brief.model_notice()
    assert "2 条" in notice and "直接念给用户听" in notice
    assert brief.speech_text not in notice


def test_history_keeps_the_verbatim_script_for_third_item_questions() -> None:
    """Old assistant_v3.py:1017-1024: follow-ups read the real script from history."""
    brief = from_news(["今天下雨", "明天转晴", "第三条是这条"], date="2026-10-23")
    assert brief is not None
    messages = brief.history_messages("念一下今天的新闻")
    assert messages == [
        {"role": "user", "content": "念一下今天的新闻"},
        {"role": "assistant", "content": brief.speech_text},
    ]
    assert "第三，第三条是这条。" in messages[1]["content"]
    assert brief.item_index("第三条再说说") == 3
    assert brief.item_at(3) == "第三条是这条。"


def test_history_is_capped_at_the_old_800_characters() -> None:
    items = [f"第{i}条新闻的内容" * 20 for i in range(1, 11)]
    brief = from_news(items)
    assert brief is not None and len(brief.speech_text) > briefing.HISTORY_LIMIT
    content = brief.history_messages("念新闻")[1]["content"]
    assert len(content) == briefing.HISTORY_LIMIT
    assert content == brief.speech_text[:briefing.HISTORY_LIMIT]


def test_item_index_understands_spoken_and_digit_ordinals() -> None:
    brief = from_news([f"新闻{i}" for i in range(1, 11)], limit=10)
    assert brief is not None
    assert brief.item_index("第三条再说说") == 3
    assert brief.item_index("第 三 条 再 说 说") == 3
    assert brief.item_index("第3条是什么") == 3
    assert brief.item_index("第十条") == 10
    assert brief.item_index("第八条") == 8
    assert brief.item_index("第十一条") is None      # out of range
    assert brief.item_index("三条") is None          # no 第...条 marker
    assert brief.item_index("今天天气怎么样") is None
    assert brief.item_at(0) == "" and brief.item_at(11) == ""


def test_date_defaults_to_today_and_can_be_overridden() -> None:
    today = dt.date.today()
    brief = from_news(["新闻"], tip="")
    assert brief is not None
    assert brief.date == today.isoformat()
    assert brief.speech_text.startswith(
        f"今天是{today.month}月{today.day}日，{briefing.WEEKDAYS[today.weekday()]}。")
    assert from_news(["新闻"], date="not-a-date").date == today.isoformat()  # type: ignore[union-attr]
    assert from_news(["新闻"], source="aihot").source == "aihot"             # type: ignore[union-attr]
    assert isinstance(brief, Briefing)


# ---------------------------------------------------------------------------
# D1c ④ wiring: the opt-in direct-speech hook in TurnRunner + the news tool
# ---------------------------------------------------------------------------
class _DirectAsr:
    def __init__(self, text: str = "念一下今天的新闻") -> None:
        self.text = text

    async def transcribe(self, pcm: bytes, sample_rate: int):
        from rui.providers.base import Transcript

        return Transcript(text=self.text)


class _DirectLlm:
    def __init__(self, rounds: list[list[object]]) -> None:
        self.rounds = rounds
        self.seen: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict]):
        self.seen.append([dict(m) for m in messages])
        for chunk in self.rounds.pop(0):
            yield chunk


class _DirectTts:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str, voice: str = ""):
        self.texts.append(text)
        yield b"\x01\x00" * 320


def _patch_direct(monkeypatch, llm: _DirectLlm, tts: _DirectTts) -> None:
    import rui.session.turn as turn

    monkeypatch.setattr(turn, "asr_chain", lambda providers: [("a", _DirectAsr())])
    monkeypatch.setattr(turn, "llm_chain", lambda providers: [("l0", llm)])
    monkeypatch.setattr(turn, "tts_chain", lambda providers, rate: [("t0", tts)])


def _news_spec():
    from rui.tools.contract import Permission, ToolSpec

    return ToolSpec(name="local.news", description="新闻", permission=Permission.READ)


def _tool_call(name: str = "news") -> object:
    from rui.providers.base import ChatChunk

    return ChatChunk(delta="", finish_reason="tool_calls",
                     tool_calls=[{"id": "t1", "name": name, "arguments": "{}"}])


def test_a_direct_hook_reads_the_briefing_without_a_second_model_round(monkeypatch) -> None:
    """Old @@BRIEF behaviour: the script is the answer, the model is not asked again."""
    import asyncio

    from rui.config.schema import Config
    from rui.providers.base import ChatChunk
    from rui.session.turn import TurnRunner, speech_pieces
    from rui.trace import TurnTrace
    from rui.web.api.app import _direct_speech

    brief = from_news(["今天下雨", "明天转晴"], date="2026-10-23")
    assert brief is not None

    async def handler(_args: dict) -> Briefing:
        return brief

    llm = _DirectLlm([[_tool_call()], [ChatChunk(delta="不应该出现。")]])
    tts = _DirectTts()
    _patch_direct(monkeypatch, llm, tts)
    runner = TurnRunner(Config().providers, tools=[(_news_spec(), handler)],
                        direct_hook=_direct_speech)

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    result = asyncio.run(runner.run(b"", speak, TurnTrace(device_id="living"),
                                    transcript="念一下今天的新闻"))

    assert len(llm.seen) == 1, "the briefing must not trigger a second model round"
    assert result.tools == ["news"]
    assert result.reply == brief.speech_text
    assert tts.texts == speech_pieces(brief.speech_text)
    assert result.audio_bytes > 0
    # history carries user + the verbatim script (「第三条再说说」 next turn)
    assert runner.history == [
        {"role": "user", "content": "念一下今天的新闻"},
        {"role": "assistant", "content": brief.speech_text},
    ]


def test_the_model_only_sees_the_notice_never_the_script(monkeypatch) -> None:
    import asyncio

    from rui.config.schema import Config
    from rui.session.turn import TurnRunner
    from rui.web.api.app import _direct_speech

    brief = from_news(["今天下雨"], date="2026-10-23")
    assert brief is not None

    async def handler(_args: dict) -> Briefing:
        return brief

    _patch_direct(monkeypatch, _DirectLlm([[_tool_call()]]), _DirectTts())
    runner = TurnRunner(Config().providers, tools=[(_news_spec(), handler)],
                        direct_hook=_direct_speech)
    out = asyncio.run(runner._call_tool({"name": "news", "arguments": "{}"}))
    assert out == brief.model_notice()
    assert brief.speech_text not in out
    # and the dataclass fallback (no hook) is the notice, never a repr with the text
    assert str(brief) == brief.model_notice()
    assert brief.speech_text not in str(brief)


def test_without_the_hook_the_tool_round_behaves_as_before(monkeypatch) -> None:
    """The hook is opt-in: no hook -> the historical two-round tool path."""
    import asyncio

    from rui.config.schema import Config
    from rui.providers.base import ChatChunk
    from rui.session.turn import TurnRunner
    from rui.trace import TurnTrace

    async def handler(_args: dict) -> str:
        return "新闻：今天下雨。第二，明天转晴。"

    llm = _DirectLlm([[_tool_call()], [ChatChunk(delta="好，新闻念完了。")]])
    tts = _DirectTts()
    _patch_direct(monkeypatch, llm, tts)
    runner = TurnRunner(Config().providers, tools=[(_news_spec(), handler)])

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    result = asyncio.run(runner.run(b"", speak, TurnTrace(device_id="living"),
                                    transcript="念一下新闻"))
    assert len(llm.seen) == 2
    assert result.reply == "好，新闻念完了。"
    # the second round received the handler's text, as it always has
    assert llm.seen[1][-1]["content"] == "新闻：今天下雨。第二，明天转晴。"


def test_a_non_briefing_result_is_not_direct(monkeypatch) -> None:
    import asyncio

    from rui.config.schema import Config
    from rui.session.turn import TurnRunner
    from rui.tools.contract import ToolResult
    from rui.web.api.app import _direct_speech

    async def handler(_args: dict) -> ToolResult:
        return ToolResult(status="ok").slot(title="晴天")

    spec = _news_spec()
    runner = TurnRunner(Config().providers, tools=[(spec, handler)],
                        direct_hook=_direct_speech)
    out = asyncio.run(runner._call_tool({"name": "news", "arguments": "{}"}))
    # rendered by the phrase layer as before, not intercepted by the direct hook
    assert '"status": "ok"' in out and "晴天" in out
    assert runner._pending_direct is None


def test_the_news_tool_builds_a_briefing_from_the_digest(monkeypatch) -> None:
    import asyncio

    from rui.config.schema import Config
    from rui.tools import info

    async def fake_get(url: str, timeout: float, params: dict | None = None):
        return {"news": ["今天下雨", "明天转晴"], "tip": "晚安"}

    monkeypatch.setattr(info, "_get_json", fake_get)
    got = asyncio.run(info.news(Config(), {"count": 2}))
    assert isinstance(got, briefing.Briefing)
    assert got.date == dt.date.today().isoformat()
    assert got.items == ("今天下雨。", "明天转晴。")
    assert got.tip == "晚安"


def test_debug_say_reads_a_briefing_end_to_end(monkeypatch, tmp_path) -> None:
    """The console's 试一试 path: same runner, same hook, no second model round."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import rui.web.api.app as app_mod
    from rui.config.schema import Config
    from rui.tools import info

    brief = from_news(["今天下雨", "明天转晴"], date="2026-10-23")
    assert brief is not None

    async def fake_news(_cfg, _args):
        return brief

    monkeypatch.setattr(info, "news", fake_news)
    llm = _DirectLlm([[_tool_call()]])
    tts = _DirectTts()
    _patch_direct(monkeypatch, llm, tts)

    async def no_filler(_cfg, _text):
        return b""

    monkeypatch.setattr(app_mod, "render_filler_pcm", no_filler)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    app = app_mod.create_app(cfg)

    class _Client:
        def __init__(self) -> None:
            self.sent: list[object] = []
            self.spoken = 0
            self.metrics = SimpleNamespace(output_owner="")
            self.ready = SimpleNamespace(device_id="debug", voice_rate=16000)

        async def send(self, msg: object) -> None:
            self.sent.append(msg)

        async def speak(self, pcm: bytes, **kwargs: object) -> None:
            self.spoken += len(pcm)

        async def reply_finished(self, **kwargs: object) -> None:
            return None

        async def set_led(self, *args: object, **kwargs: object) -> None:
            return None

        async def stop(self) -> None:
            return None

    class _Supervisor:
        def __init__(self) -> None:
            self.client = _Client()

        async def stop(self) -> None:
            return None

    with TestClient(app) as http:
        app.state.audio = _Supervisor()
        body = http.post("/api/v1/debug/say",
                         json={"text": "念一下今天的新闻"}).json()

    assert body["reply"] == brief.speech_text
    assert body["tools"] == ["news"]
    assert len(llm.seen) == 1, "the briefing must not be re-answered by the model"
    assert tts.texts
