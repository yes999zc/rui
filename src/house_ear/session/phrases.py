"""The PhraseBook: every sentence the system layer speaks, in one place.

The V-line's single rule (docs/interaction.md): an application-layer tool never
carries its own wording. It returns a structured `ToolResult` and declares an
`InteractionSpec`; this table is the only place the words live. The old build
proved why: four tool families grew four speech dialects (`@@` markers for
music, error strings for HA, notices for search, its own set for reminders), and
the unification attempt only ever reached the prompt text layer
(REQUIRED_HINTS), never the return values.

Render slots use `{title}`-style braces; a wrong slot renders empty rather than
raising - a wording bug must not eat a turn - but it is **logged and remembered**
in `PhraseBook.last_errors` now, because a silently dropped `{device}` is worse
than a visible one (C-UI P6, docs/phrasebook-editor.md).

The console writes an override layer through `session/phrasebook.py`; this module
stays pure so the templates and their rules can be tested without a filesystem.
"""
from __future__ import annotations

import logging
import string
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger(__name__)

# The built-in table. Keys are namespaced `domain.what`; the status templates an
# InteractionSpec points at must exist here (a typo fails loud and early in tests).
DEFAULTS: dict[str, str] = {
    # --- music (V1's migration set) -----------------------------------------
    "music.started": "好，放《{title}》-{artist}，共 {count} 首。",
    "music.started_one": "好，放《{title}》-{artist}。",
    "music.not_found": "曲库里没找到「{want}」。",
    "music.empty": "曲库里现在没有能放的。",
    "music.unreachable": "曲库这会儿连不上，过一会儿再让我放。",
    "music.unresolved": "歌都在，但一首也没取出来，过会儿再试一次吧。",
    "music.paused": "好，先停这儿。",
    "music.resumed": "接着放。",
    "music.not_playing": "现在没有在放的歌。",
    "music.not_paused": "现在没有暂停着的歌。",
    "music.next": "好，《{title}》。",
    "music.last_track": "已经是最后一首了。",
    "music.prev": "好，回到《{title}》。",
    "music.first_track": "已经是第一首了。",
    "music.stopped": "好，不放了。",
    "music.starred": "已收藏《{title}》。",
    "music.unstarred": "取消收藏《{title}》。",
    "music.star_failed": "收藏没成功，服务器没回应。",
    "music.no_current": "现在没有在播的歌，想收藏哪首就先放它。",
    "music.favorites_empty": "还没有收藏的歌，播放时说「收藏这首歌」。",
    "music.favorites_read_failed": "收藏这会儿读不出来。",
    "music.favorites_count": "收藏了 {count} 首，比如：{names}。",
    "music.taste_empty": "还没怎么放过歌，先来一首？",
    "music.taste_top": "你听得最多的是：{names}。{tail}",
    "music.taste_item": "{artist} {plays} 次",
    "music.taste_tail": "最近一次放的是《{title}》。",
    # --- turn-level degrade (the system layer's own fallbacks) ---------------
    "turn.failed": "我没弄成，你再说一遍？",
    "turn.stuck": "我这卡了一下，你再说一遍。",
    # --- volume (the speaker's own mixer) -----------------------------------
    "volume.set": "音量调到 {pct}%了。",
    "volume.read": "现在音量 {pct}%。",
    "volume.failed": "音量没调成，音箱没回应。",
    "volume.unknown": "没听清要调到多少，现在是 {pct}%。",
    # --- memory and conversation context ------------------------------------
    "memory.saved": "好，我记住了：{text}。",
    "memory.empty_text": "要记什么？说清楚一点我再记。",
    "memory.forgotten": "好，忘掉「{text}」了。",
    "memory.not_found": "没找到要忘掉的那条。",
    "memory.recall_empty": "我这儿还没有记住的事。",
    "memory.recall": "我记住的有：{items}。",
    "context.off": "好，之后每句话我都当新的一句，不带上下文。",
    "context.on": "好，之后会接着上下文聊。",
    # --- devices over HA (V2's first application) ----------------------------
    "device.done": "好，{device}{action}。",
    "device.unreachable": "{device}这会儿连不上，没能执行，别当我成功了。",
    "device.excluded": "{device}这类操作我不做。",
    "device.missing_target": "没听清要操作哪个设备。",
    "device.ask_verb": "要开还是关？我只听到设备名，没听清你说开还是关。",
    "device.not_done": "这个我没能执行：刚才没有真正调用设备，不敢说做成了。",
    "ha.entity_list": "家里的设备（名字=实体ID，状态要现查，别用过期状态）：\n{list}\n{note}",
    "ha.list_stale": "（这份清单是上次缓存的，控制可能失败。）",
    "ha.multi_entity_rule": (
        "（控制规则：用户说的名字对应多个 entity_id 时——比如「儿童房的灯」既是主灯也是筒灯——"
        "全部传入 call_service，不要只挑一个，也不要反问用户要哪个。"
        "开关灯用 domain=switch/light，service=turn_on 或 turn_off；"
        "没有真的调用工具并拿到成功结果，就绝不能说已经做了。）"),
}

# P6 rules (docs/phrasebook-editor.md §6). Speech keys are short by ear, prompt
# fragments may be longer and contain newlines.
SPEECH_MAX_CHARS = 300
PROMPT_MAX_CHARS = 2000
MAX_LAST_ERRORS = 20
# A newline in a spoken template ends up in one TTS call; only the HA prompts are
# multi-line by design.
_MULTILINE_PREFIXES = ("ha.",)
ADVANCED_KEYS = frozenset(key for key in DEFAULTS if key.startswith(_MULTILINE_PREFIXES))

_MISSING = object()


# ---------------------------------------------------------------------------
# slots: derived from the defaults, never hand-maintained
# ---------------------------------------------------------------------------
def _checked_slots(template: str) -> tuple[frozenset[str], list[str]]:
    """Parse a template into placeholder names, plus the reasons it is invalid.

    Only plain `{name}` fields are accepted: pydantic/str format specs and nested
    fields would make the console's slot chips a lie, and `render()` would not
    survive them consistently.
    """
    errors: list[str] = []
    slots: set[str] = set()
    try:
        parts = list(string.Formatter().parse(template))
    except ValueError as exc:
        return frozenset(), [f"模板括号不合法：{exc}"]
    for _literal, field, spec, conversion in parts:
        if field is None:
            continue
        if not field:
            errors.append("占位符不能为空 {}")
            continue
        if any(char in field for char in ".[]"):
            errors.append(f"占位符 {{{field}}} 只能是简单字段名")
            continue
        if spec or conversion:
            errors.append(f"占位符 {{{field}}} 不支持格式或转换说明")
            continue
        slots.add(field)
    return frozenset(slots), errors


DEFAULT_SLOTS: dict[str, frozenset[str]] = {
    key: _checked_slots(template)[0] for key, template in DEFAULTS.items()
}


def _sample_slots(slots: frozenset[str]) -> dict[str, object]:
    """Deterministic sample payload for the console's preview column."""
    values: dict[str, object] = {
        "title": "晴天", "artist": "周杰伦", "count": 20, "want": "晴天",
        "pct": 50, "text": "周二有电话会", "items": "客厅灯、儿童房灯",
        "names": "周杰伦、陈奕迅", "tail": "最近一次放的是《晴天》。", "plays": 3,
        "device": "客厅灯", "action": "打开", "list": "客厅灯=light.ka",
        "note": "",
    }
    return {slot: values.get(slot, "…") for slot in slots}


# ---------------------------------------------------------------------------
# validation / application
# ---------------------------------------------------------------------------
class PhraseOverrideError(ValueError):
    """One or more invalid overrides; `.errors` is `{key: [reason, ...]}` for a 422."""

    def __init__(self, errors: dict[str, list[str]]) -> None:
        self.errors = errors
        summary = "; ".join(f"{key}: {'、'.join(items)}"
                            for key, items in sorted(errors.items()))
        super().__init__(f"话术覆盖被拒绝：{summary}")


def validate_override(key: str, template: str) -> list[str]:
    """Return human-readable problems with one override; [] means it is usable.

    The rule the console hard-enforces: the `{slot}` set must equal the default's
    set. Reordering and rewording are fine; deleting `{device}` is not - otherwise
    a user gets 「好，开。」 and nobody notices (docs/phrasebook-editor.md §6).
    """
    if not isinstance(key, str) or key not in DEFAULTS:
        return [f"未知话术键：{key}"]
    if not isinstance(template, str):
        return ["话术必须是字符串"]
    if not template.strip():
        return ["话术不能为空"]
    limit = PROMPT_MAX_CHARS if key in ADVANCED_KEYS else SPEECH_MAX_CHARS
    if len(template) > limit:
        return [f"话术太长（{len(template)} > {limit} 字）"]
    if any(ord(char) < 32 and char != "\n" for char in template):
        return ["话术不能包含控制字符"]
    if "\n" in template and key not in ADVANCED_KEYS:
        return ["朗读话术不能包含换行"]
    slots, parse_errors = _checked_slots(template)
    if parse_errors:
        return parse_errors
    required = DEFAULT_SLOTS[key]
    missing = sorted(required - slots)
    extra = sorted(slots - required)
    problems: list[str] = []
    if missing:
        problems.append("缺少占位符：" + "、".join("{" + name + "}" for name in missing))
    if extra:
        problems.append("多了未知占位符：" + "、".join("{" + name + "}" for name in extra))
    return problems


def validate_overrides(overrides: Mapping[str, object]) -> dict[str, list[str]]:
    """Validate a whole override mapping; returns only the rejected keys."""
    if not isinstance(overrides, Mapping):
        return {"": ["overrides 必须是一个对象"]}
    errors: dict[str, list[str]] = {}
    for key, value in overrides.items():
        problems = validate_override(str(key), value)  # type: ignore[arg-type]
        if problems:
            errors[str(key)] = problems
    return errors


def apply_overrides(overrides: Mapping[str, object] | None) -> dict[str, str]:
    """Replace the live override layer. Raises `PhraseOverrideError` when invalid.

    All-or-nothing: a single bad key must not leave a half-applied table behind.
    """
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, Mapping):
        raise PhraseOverrideError({"": ["overrides 必须是一个对象"]})
    clean: dict[str, str] = {}
    errors: dict[str, list[str]] = {}
    for key, value in overrides.items():
        problems = validate_override(str(key), value)  # type: ignore[arg-type]
        if problems:
            errors[str(key)] = problems
        else:
            clean[str(key)] = str(value)
    if errors:
        raise PhraseOverrideError(errors)
    _overrides.clear()
    _overrides.update(clean)
    _rebuild()
    return dict(clean)


def load_overrides(path: str | Path) -> tuple[bool, list[str]]:
    """Load an override file and apply it. Never raises: a bad file keeps defaults.

    Returns `(applied, errors)`. `applied=False` for a missing file (normal fresh
    install, no errors) or an invalid one (defaults stay active; errors explain
    why). `applied=True` only when the file existed and every override passed.
    """
    from .phrasebook import read  # local import: phrasebook imports this module

    try:
        result = read(path)
    except Exception as exc:  # noqa: BLE001 - startup must not depend on this file
        reset()
        return False, [f"{type(exc).__name__}: {exc}"]
    if result.error or result.errors:
        reset()
        messages: list[str] = []
        if result.error:
            messages.append(result.error)
        for key, problems in sorted(result.errors.items()):
            messages.extend(f"{key}: {problem}" for problem in problems)
        log.warning("phrasebook: overrides not applied (%d problem(s))", len(messages))
        return False, messages
    if not result.exists:
        reset()
        return False, []
    apply_overrides(result.overrides)
    return True, []


def reset(key: str | None = None) -> dict[str, str]:
    """Drop one override (or all). Returns the overrides that remain live."""
    if key is None:
        _overrides.clear()
    else:
        _overrides.pop(key, None)
    _rebuild()
    return dict(_overrides)


def current_overrides() -> dict[str, str]:
    return dict(_overrides)


def snapshot() -> dict[str, object]:
    """Everything the console needs to render the editor (no file metadata).

    `text` is the live template (default or override), `example` is that template
    rendered with deterministic samples, `slots` is the hard contract, `advanced`
    marks the HA prompt fragments.
    """
    keys: list[dict[str, object]] = []
    for key, default in DEFAULTS.items():
        slots = DEFAULT_SLOTS[key]
        template = book.template(key) or default
        keys.append({
            "key": key,
            "default": default,
            "text": template,
            "example": book.render(key, **_sample_slots(slots)),
            "slots": sorted(slots),
            "overridden": key in _overrides,
            "group": key.split(".", 1)[0],
            "advanced": key in ADVANCED_KEYS,
        })
    return {
        "effect": "next_phrase",
        "overrides": dict(_overrides),
        "keys": keys,
        "last_errors": [dict(item) for item in book.last_errors],
    }


# ---------------------------------------------------------------------------
# the book
# ---------------------------------------------------------------------------
class PhraseBook:
    """Immutable defaults plus an optional override layer (the console writes one)."""

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._table = {**DEFAULTS, **(overrides or {})}
        # Recent render problems, for the console (`last_errors`). A list of
        # {"key","missing","template"}; bounded and deduplicated by key+missing.
        self.last_errors: list[dict[str, object]] = []

    def render(self, key: str, **slots: object) -> str:
        text = self._table.get(key, _MISSING)
        if text is _MISSING:
            self._record_error(key, [], "")
            log.warning("phrase: unknown key %s", key)
            return key   # an unknown key is a wiring bug; say it, never crash a turn
        filled = {name: "" if value is None else value for name, value in slots.items()}
        try:
            return str(text).format(**filled)
        except (KeyError, IndexError, ValueError) as exc:
            missing = sorted(DEFAULT_SLOTS.get(key, frozenset()) - set(slots))
            self._record_error(key, missing, str(text))
            # A slot mismatch degrades to the bare template - wording bugs must not
            # eat a turn - but it is no longer silent: last_errors/show it in the console.
            log.warning("phrase: %s missing slots %s (%s)", key, missing or "?", exc)
            return _strip_slots(str(text))

    def has(self, key: str) -> bool:
        return key in self._table

    def keys(self) -> list[str]:
        return sorted(self._table)

    def template(self, key: str) -> str:
        """The live template for `key` (default or override), "" when unknown."""
        value = self._table.get(key, "")
        return str(value)

    def _record_error(self, key: str, missing: list[str], template: str) -> None:
        signature = (key, tuple(missing), template)
        for item in self.last_errors:
            if (item["key"], tuple(item["missing"]), item["template"]) == signature:
                return
        self.last_errors.append({"key": key, "missing": missing, "template": template})
        del self.last_errors[:-MAX_LAST_ERRORS]


def _strip_slots(text: str) -> str:
    """Drop `{...}` placeholders a payload did not fill."""
    out, depth = [], 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out).replace("  ", " ").strip()


# module-level live state: the runner and the deterministic paths share one book
_overrides: dict[str, str] = {}
book = PhraseBook()


def _rebuild() -> None:
    global book
    book = PhraseBook(_overrides)
