"""P6 backend: PhraseBook slot contract, override layer, and scenario contracts.

Two layers (docs/phrasebook-editor.md §8):
* the slot/override/file contract, tested directly;
* YAML "scenario" contracts driven by pytest with scripted providers - no real
  LLM, no speaker, no HA. They assert tool / status / phrase key / spoken text.
"""
from __future__ import annotations

import asyncio
import string
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from rui.config.schema import Config
from rui.music.history import PlayHistory
from rui.music.model import Track
from rui.music.service import MusicService
from rui.music.tools import build_music_tools
from rui.providers.base import ChatChunk, Transcript
from rui.session import briefing, phrasebook, phrases
from rui.session.turn import DirectSpeech, TurnRunner
from rui.tools.contract import Permission, ToolSpec
from rui.trace import TurnTrace

_yaml = YAML(typ="safe")


@pytest.fixture(autouse=True)
def _clean_book():
    """The book is a module singleton; every test starts and ends on the defaults."""
    phrases.reset()
    assert phrases.current_overrides() == {}
    yield
    phrases.reset()


# ---------------------------------------------------------------------------
# slot contract
# ---------------------------------------------------------------------------
def test_every_default_key_has_a_derived_slot_set() -> None:
    assert len(phrases.DEFAULTS) == 58  # +7 spoken-reminder lines (C1, §f.2)
    assert set(phrases.DEFAULT_SLOTS) == set(phrases.DEFAULTS)
    for key, template in phrases.DEFAULTS.items():
        slots, errors = phrases._checked_slots(template)
        assert not errors, (key, errors)
        assert slots == phrases.DEFAULT_SLOTS[key], key
        # every declared slot parses back out of the default (no dead registry entry)
        parsed = {field for _l, field, _s, _c in string.Formatter().parse(template)
                  if field is not None}
        assert parsed == set(phrases.DEFAULT_SLOTS[key]), key
    assert phrases.ADVANCED_KEYS == frozenset(
        key for key in phrases.DEFAULTS if key.startswith("ha."))


def test_validate_accepts_rewording_and_reordering() -> None:
    assert phrases.validate_override(
        "music.started", "放《{title}》-{artist}，{count} 首，走起。") == []
    assert phrases.validate_override(
        "device.done", "{device}，{action}好了。") == []
    # escaped literal braces are text, not slots
    assert phrases.validate_override("music.stopped", "好，不放了{{真的}}。") == []
    # HA prompt fragments may be multi-line
    assert phrases.validate_override(
        "ha.entity_list", "设备：\n{list}\n{note}") == []


def test_validate_rejects_the_dangerous_edits() -> None:
    # the rule this whole feature exists for: deleting a slot must not pass
    problems = phrases.validate_override("device.done", "好，开了。")
    assert any("缺少占位符" in item for item in problems)
    assert any("{device}" in item for item in problems)
    # extra / renamed slots are rejected too
    assert any("未知占位符" in item for item in
               phrases.validate_override("device.done", "好，{who}{action}。"))
    assert phrases.validate_override("no.such.key", "x") == ["未知话术键：no.such.key"]
    assert phrases.validate_override("music.stopped", "   ") == ["话术不能为空"]
    assert phrases.validate_override("music.stopped", "好，{")  # bad braces
    assert phrases.validate_override("music.started",
                                     "好，{count:03d}{title}{artist}")  # format spec
    assert phrases.validate_override("music.started",
                                     "好，{title.x}{title}{artist}{count}")  # nested field
    assert phrases.validate_override("music.stopped", "好，不放了\n") == ["朗读话术不能包含换行"]
    assert phrases.validate_override("music.stopped", "好\x00") == ["话术不能包含控制字符"]
    long = "好" * (phrases.SPEECH_MAX_CHARS + 1)
    assert any("太长" in item for item in phrases.validate_override("music.stopped", long))
    # ha prompts keep a larger budget
    huge = "x" * (phrases.PROMPT_MAX_CHARS + 1)
    assert any("太长" in item for item in phrases.validate_override("ha.entity_list", huge))


def test_apply_overrides_and_reset() -> None:
    applied = phrases.apply_overrides({"music.stopped": "好，停。"})
    assert applied == {"music.stopped": "好，停。"}
    assert phrases.book.render("music.stopped") == "好，停。"
    assert phrases.current_overrides() == {"music.stopped": "好，停。"}

    # one bad key rejects the whole batch and leaves the live table untouched
    with pytest.raises(phrases.PhraseOverrideError) as info:
        phrases.apply_overrides({"music.stopped": "停。", "device.done": "好。"})
    assert "device.done" in info.value.errors
    assert phrases.book.render("music.stopped") == "好，停。"

    assert phrases.reset("music.stopped") == {}
    assert phrases.book.render("music.stopped") == phrases.DEFAULTS["music.stopped"]

    phrases.apply_overrides({"music.stopped": "A", "music.paused": "B"})
    assert phrases.reset() == {}
    assert phrases.current_overrides() == {}
    assert phrases.book.render("music.paused") == phrases.DEFAULTS["music.paused"]


def test_render_records_missing_slots_instead_of_silently_eating_them(caplog) -> None:
    out = phrases.book.render("music.started", title="晴天")
    # the fallback strips the whole template (historical behaviour); what matters
    # here is that it is no longer silent: the missing slots are recorded.
    assert "{" not in out
    errors = phrases.book.last_errors
    assert errors and errors[-1]["key"] == "music.started"
    assert set(errors[-1]["missing"]) == {"artist", "count"}
    # the same problem is not appended twice
    before = len(errors)
    phrases.book.render("music.started", title="晴天")
    assert len(phrases.book.last_errors) == before
    # unknown keys are recorded too, and still render as the key (never raise)
    assert phrases.book.render("no.such.key") == "no.such.key"
    assert phrases.book.last_errors[-1]["key"] == "no.such.key"


def test_nested_taste_rendering_follows_overrides() -> None:
    phrases.apply_overrides({"music.taste_tail": "上次听的是《{title}》。"})
    item = phrases.book.render("music.taste_item", artist="周杰伦", plays=3)
    tail = phrases.book.render("music.taste_tail", title="晴天")
    top = phrases.book.render("music.taste_top", names=item, tail=tail)
    assert top == "你听得最多的是：周杰伦 3 次。上次听的是《晴天》。"


# ---------------------------------------------------------------------------
# file-backed override layer
# ---------------------------------------------------------------------------
def test_save_and_load_round_trip_with_0600_and_backup(tmp_path: Path) -> None:
    result = phrasebook.save(tmp_path, {"music.stopped": "好，停。"})
    assert result.ok and result.overrides == {"music.stopped": "好，停。"}
    target = tmp_path / phrasebook.FILE_NAME
    assert target.is_file()
    assert (target.stat().st_mode & 0o777) == 0o600
    raw = _yaml.load(target.read_text(encoding="utf-8"))
    assert raw["version"] == phrasebook.VERSION and "updated_at" in raw
    assert list(raw["overrides"].items()) == [("music.stopped", "好，停。")]
    assert not list(tmp_path.glob("*.tmp"))

    # a second save makes exactly one backup (BACKUPS=1), and load applies the file
    phrasebook.save(tmp_path, {"music.stopped": "好，停。", "music.paused": "先停。"})
    backups = sorted(tmp_path.glob(phrasebook.FILE_NAME + ".bak-*"))
    assert len(backups) == phrasebook.BACKUPS
    phrases.reset()
    loaded = phrasebook.load(tmp_path)
    assert loaded.ok and phrases.book.render("music.paused") == "先停。"


def test_invalid_save_does_not_touch_the_file(tmp_path: Path) -> None:
    phrasebook.save(tmp_path, {"music.stopped": "好，停。"})
    target = tmp_path / phrasebook.FILE_NAME
    before = target.read_bytes()
    with pytest.raises(phrases.PhraseOverrideError) as info:
        phrasebook.save(tmp_path, {"music.stopped": "停。", "device.done": "好。"})
    assert "device.done" in info.value.errors
    assert target.read_bytes() == before
    assert phrases.book.render("music.stopped") == "好，停。"   # live table unchanged


def test_a_broken_file_keeps_the_defaults_and_reports_it(tmp_path: Path) -> None:
    target = tmp_path / phrasebook.FILE_NAME
    target.write_text("overrides: [not: a map", encoding="utf-8")
    result = phrasebook.load(tmp_path)
    assert result.exists and result.error
    assert phrases.current_overrides() == {}
    assert phrases.book.render("music.stopped") == phrases.DEFAULTS["music.stopped"]
    doc = phrasebook.document(tmp_path)
    assert doc["file_error"] and doc["file_exists"] is True


def test_one_invalid_override_on_disk_keeps_all_defaults(tmp_path: Path) -> None:
    target = tmp_path / phrasebook.FILE_NAME
    target.write_text(_yaml_text({
        "version": 1,
        "overrides": {"music.stopped": "好，停。", "device.done": "好，开了。"},
    }), encoding="utf-8")
    result = phrasebook.load(tmp_path)
    assert not result.ok and "device.done" in result.errors
    # all-or-nothing: the valid sibling is not applied either
    assert phrases.current_overrides() == {}
    assert result.overrides == {"music.stopped": "好，停。"}


def test_an_unknown_key_is_kept_as_unused_not_an_error(tmp_path: Path) -> None:
    target = tmp_path / phrasebook.FILE_NAME
    target.write_text(_yaml_text({
        "version": 1,
        "overrides": {"music.stopped": "好，停。", "music.old_key": "旧话术"},
    }), encoding="utf-8")
    result = phrasebook.load(tmp_path)
    assert result.ok
    assert result.unused == ["music.old_key"]
    assert phrases.book.render("music.stopped") == "好，停。"


def test_clear_resets_one_or_all(tmp_path: Path) -> None:
    phrasebook.save(tmp_path, {"music.stopped": "A", "music.paused": "B"})
    phrasebook.clear(tmp_path, keys=["music.stopped"])
    assert phrases.current_overrides() == {"music.paused": "B"}
    phrasebook.clear(tmp_path)
    assert phrases.current_overrides() == {}
    assert (tmp_path / phrasebook.FILE_NAME).is_file()
    assert _yaml.load((tmp_path / phrasebook.FILE_NAME).read_text())["overrides"] == {}


def test_load_overrides_entry_point(tmp_path: Path) -> None:
    """`phrases.load_overrides(path)` is the startup call; never raises."""
    path = tmp_path / phrasebook.FILE_NAME
    assert phrases.load_overrides(path) == (False, [])          # fresh install
    phrasebook.save(tmp_path, {"music.stopped": "好，停。"})
    phrases.reset()
    assert phrases.load_overrides(path) == (True, [])           # valid file
    assert phrases.book.render("music.stopped") == "好，停。"
    # a file whose override lost its slot is rejected as a whole, defaults stay
    path.write_text(_yaml_text({"version": 1, "overrides": {"device.done": "好。"}}),
                    encoding="utf-8")
    applied, errors = phrases.load_overrides(path)
    assert applied is False and errors
    assert phrases.book.render("device.done", device="灯", action="打开") ==         phrases.DEFAULTS["device.done"].format(device="灯", action="打开")


def test_document_is_the_console_payload(tmp_path: Path) -> None:
    phrasebook.save(tmp_path, {"music.stopped": "好，停。"})
    doc = phrasebook.document(tmp_path)
    assert doc["effect"] == "next_phrase"
    assert doc["file_exists"] is True
    assert doc["overrides"] == {"music.stopped": "好，停。"}
    assert len(doc["keys"]) == 58
    by_key = {item["key"]: item for item in doc["keys"]}
    started = by_key["music.started"]
    assert started["slots"] == ["artist", "count", "title"]
    assert started["overridden"] is False
    assert started["example"] == "好，放《晴天》-周杰伦，共 20 首。"
    assert started["group"] == "music"
    assert by_key["music.stopped"]["overridden"] is True
    assert by_key["ha.multi_entity_rule"]["advanced"] is True
    assert by_key["device.done"]["advanced"] is False
    assert doc["last_errors"] == []


# ---------------------------------------------------------------------------
# scenario contracts (YAML in, assertions out; no LLM, no speaker)
# ---------------------------------------------------------------------------
SCENARIOS = _yaml.load("""
- name: stop-while-a-song-plays
  kind: tools
  steps:
    - call: music_play
      args: {query: "放一首晴天"}
      expect: {status: [ok, ok_one], tool: music_play, music_state: playing}
    - call: music_stop
      args: {}
      expect: {status: ok, phrase: music.stopped, speech: "好，不放了。", music_state: idle}
- name: briefing-third-item
  kind: turn
  turns:
    - say: "念一下今天的新闻"
      expect: {tools: [news], llm_rounds: 1, speaks_briefing: true}
    - say: "第三条再说说"
      expect: {history_item: 3, llm_rounds: 1}
""")


class _FakeSource:
    name = "fake"

    async def search(self, term: str, limit: int) -> list[Track]:
        return [Track(id="subsonic:1", title="晴天", artist="周杰伦", duration=269),
                Track(id="subsonic:2", title="晴天 live", artist="周杰伦", duration=260)]

    async def random(self, limit: int) -> list[Track]:
        return []

    async def favorites(self) -> list[Track]:
        return []

    async def star(self, track_id: str, on: bool) -> bool:
        return True

    async def resolve(self, track: Track) -> Path:
        return Path(f"/tmp/{track.id}.pcm")


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def __call__(self, msg: object) -> None:
        self.sent.append(msg)


def _tools(tmp_path: Path):
    cfg = Config()
    cfg.music.enabled = True
    cfg.system.data_dir = str(tmp_path)
    service = MusicService(_FakeSource(), PlayHistory(tmp_path / "h.json", keep=10),
                           _Recorder(), cfg.music)
    pairs = build_music_tools(cfg, service)
    handlers = {spec.llm_name: handler for spec, handler in pairs}
    specs = {spec.llm_name: spec for spec, _ in pairs}
    return handlers, specs, service


def _run_tool_scenario(scenario: dict, handlers, specs, service) -> None:
    async def run() -> None:
        for step in scenario["steps"]:
            handler = handlers[step["call"]]
            result = await handler(step["args"])
            expect = step["expect"]
            status = expect["status"]
            allowed = status if isinstance(status, list) else [status]
            assert result.status in allowed, (step, result)
            spec = specs[step["call"]]
            if "phrase" in expect:
                key = spec.interaction.exec[result.status]  # type: ignore[union-attr]
                assert key == expect["phrase"]
                assert phrases.book.render(key, **result.payload) == expect["speech"]
            if "music_state" in expect:
                assert service.state == expect["music_state"]

    asyncio.run(run())


class _FakeAsr:
    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        return Transcript(text="")


class _ScriptedLlm:
    def __init__(self, rounds: list[list[object]]) -> None:
        self.rounds = rounds
        self.seen: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict]):
        self.seen.append([dict(message) for message in messages])
        for chunk in self.rounds.pop(0):
            yield chunk


class _FakeTts:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str, voice: str = ""):
        self.texts.append(text)
        yield b"\x01\x00" * 320


def _direct_hook(name: str, transcript: str, output: object) -> DirectSpeech | None:
    if not isinstance(output, briefing.Briefing):
        return None
    return DirectSpeech(text=output.speech_text, notice=output.model_notice(),
                        history=output.history_messages(transcript))


def _run_turn_scenario(scenario: dict, monkeypatch, tmp_path: Path) -> None:
    import rui.session.turn as turn

    brief = briefing.from_news(["今天下雨", "明天转晴", "第三条是这条"], date="2026-10-23")
    assert brief is not None
    spec = ToolSpec(name="local.news", description="news", permission=Permission.READ)

    async def handler(_args: dict) -> briefing.Briefing:
        return brief

    call = ChatChunk(delta="", finish_reason="tool_calls",
                     tool_calls=[{"id": "t1", "name": "news", "arguments": "{}"}])
    reply = ChatChunk(delta="第三条说的是今天会下雨。")
    llm = _ScriptedLlm([[call], [reply]])
    tts = _FakeTts()
    monkeypatch.setattr(turn, "asr_chain", lambda providers: [("a", _FakeAsr())])
    monkeypatch.setattr(turn, "llm_chain", lambda providers: [("l", llm)])
    monkeypatch.setattr(turn, "tts_chain", lambda providers, rate: [("t", tts)])

    runner = TurnRunner(Config().providers, tools=[(spec, handler)],
                        direct_hook=_direct_hook)

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    turns = scenario["turns"]
    first, second = turns[0], turns[1]
    result = asyncio.run(runner.run(b"", speak, TurnTrace(device_id="scenario"),
                                    transcript=first["say"]))
    expect = first["expect"]
    assert result.tools == expect["tools"]
    assert len(llm.seen) == expect["llm_rounds"]
    assert tts.texts and "".join(tts.texts) == brief.speech_text
    assert runner.history[-1]["content"] == brief.speech_text

    # the follow-up turn sees the verbatim script in history
    result2 = asyncio.run(runner.run(b"", speak, TurnTrace(device_id="scenario"),
                                     transcript=second["say"]))
    expect2 = second["expect"]
    assert len(llm.seen) == expect["llm_rounds"] + expect2["llm_rounds"]
    prompt = llm.seen[-1]
    assert any(message.get("content") == brief.speech_text for message in prompt)
    assert brief.item_index(second["say"]) == expect2["history_item"]
    assistant_texts = [message["content"] for message in runner.history
                       if message["role"] == "assistant"]
    assert brief.speech_text in assistant_texts
    assert any(brief.item_at(expect2["history_item"]) in text
               for text in assistant_texts)
    assert result2.reply


def test_scenario_contracts(monkeypatch, tmp_path: Path) -> None:
    scenarios = {item["name"]: item for item in SCENARIOS}
    assert set(scenarios) == {"stop-while-a-song-plays", "briefing-third-item"}

    handlers, specs, service = _tools(tmp_path)
    _run_tool_scenario(scenarios["stop-while-a-song-plays"], handlers, specs, service)
    _run_turn_scenario(scenarios["briefing-third-item"], monkeypatch, tmp_path)


def _yaml_text(payload: dict) -> str:
    import io

    buf = io.StringIO()
    _yaml.dump(payload, buf)
    return buf.getvalue()
