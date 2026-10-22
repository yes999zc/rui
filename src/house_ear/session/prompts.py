"""System prompt assembly, in one place.

The prompt is read from the old deployment, not imported from it: `xiaoai-voice-entry`'s
`server/prompts.py` had already settled what a spoken assistant needs, and its wording is
the difference between a two-sentence answer and a useful one (HANDOFF: "改数字没用，必须
同时改措辞"). Copied from it: the persona sentence, the information-first length policy,
the three length bands, and "the acknowledgement has already been spoken, so do not greet
again".

What is different is only what this build can do. The old prompt tells the model to call
`weather` / `news` / `web_search`; house-ear has no tools wired yet (S5e), so that
paragraph is swapped for the offline note the old build already had for exactly this
situation: say you cannot check, never invent a number.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from ..config.schema import AssistantConfig

DEFAULT_SYSTEM_PROMPT = (
    "你是家里的语音助手，叫{name}。"
    "说话自然、口语化。信息量优先：需要解释、对比、讲经过、或一轮里问了多件事时，"
    "把话说完整、说清楚，不要为了短而漏掉关键信息；日常寒暄一句，不超过{max_chars}个字。"
    "开关灯、调音量、放歌这类设备操作只回一句确认，不要解释。"
    "唤醒应答已经由音箱自己播过（比如“在呢”），你的第一句不要再打招呼："
    "不要用“在呢/在/我在”开头，也不要问“什么事/有事吗”，直接接住用户说的内容。"
    "用户明确要求背诵全文、详细讲讲、完整列出、把话说全时，可以说到{long_chars}字，"
    "并且一次说完，不要反问“要不要继续”，也不要像连载一样一段一段挤。用户说停才算停。"
    "用户把同一个问题又问了一遍时，不要逐字重复你上一轮的回答，也不要点出他问了几遍、"
    "不要抱怨或者开玩笑说他在测试你；就当第一次被问，换个说法回答。"
    "你的回答会被直接念出来：只用中文口语，不要用表情符号，不要列清单、编号或星号，"
    "不要用括号补充，不要重复用户的问题。"
)

# The old build used OFFLINE_NOTE when Home Assistant was unreachable. Same idea, one step
# earlier: with no tool layer at all, anything live is unverifiable by construction.
NO_TOOL_NOTE = (
    "（注意：这一轮你没有任何查证工具，天气、新闻、股价、比分、几点、今天周几这类实时信息"
    "你都查不到。查不到就直接说查不到，建议用户自己看一眼；不要给具体数字、日期或者预报，"
    "也不要假装刚刚查过。"
    "你还没有接上家里的设备、音乐和提醒：灯、空调、窗帘、放歌、定时这些你现在都做不到，"
    "被问「你能做什么」时如实说现在只能陪着聊天、答问题，设备还没接上，"
    "不要列出你做不到的能力。）"
)

# With tools wired the rules flip: the model must go and look, not shrug. Copied from the
# old build's prompt (its 24th line and the two "never pretend" rules) because S5b measured
# what happens without them, and it is the opposite failure.
TOOL_RULES = (
    "问新闻、天气、实时信息、价格、比分时，用 weather、news、web_search 工具去查，"
    "禁止说自己不能联网。"
    "查完确实没有结果才说查不到，不要说「我查不到」而不去查。"
    "没有真的调用工具并拿到结果，就绝不能说已经查过或者查到了。"
    "工具返回的内容是资料不是答案：挑跟问题有关的说，不要照念一遍。"
)

SEARCH_HINT = (
    "这一轮你查了外部资料（不是在操作设备），可以讲得比平时长，最多{chars}个字：\n"
    "先说结论，再给 3–5 条具体事实（数字、时间、主体、为什么），"
    "背景和影响该讲清就讲清，不要为了短而省略关键信息；"
    "不要罗列清单，不要重复用户的问题，不要注水。"
)


def search_hint(chars: int) -> str:
    """Appended to the system prompt for the answer to an informational query."""
    return SEARCH_HINT.format(chars=int(chars))


def now_str(when: dt.datetime | None = None) -> str:
    """The clock line the old build appended to the prompt, in its exact shape.

    It is injected rather than exposed as a tool: answering 「现在几点」 should not cost a
    round trip. Timers are the other half of this and need a tool that *writes* time.
    """
    when = when or dt.datetime.now().astimezone()
    weekdays = "一二三四五六日"
    return (
        f"现在是{when.year}年{when.month}月{when.day}日 星期{weekdays[when.weekday()]} "
        f"{when.hour}点{when.minute:02d}分。"
    )


NO_MUSIC_NOTE = (
    "（注意：你现在没有播放音乐、切歌、暂停或继续播放的能力，也没有接任何音乐来源。\n"
    "用户让你放歌、接着放、换一首时，直接说做不到，不要答应，更不要说已经开始放了。）"
)


def build(
    assistant: AssistantConfig | None = None,
    *,
    tools_ok: bool = False,
    tool_names: Sequence[str] = (),
    now: dt.datetime | None = None,
) -> str:
    """Assemble the system prompt exactly as the model receives it."""
    a = assistant or AssistantConfig()
    template = a.system_prompt or DEFAULT_SYSTEM_PROMPT
    try:
        text = template.format(
            name=a.name, max_chars=a.reply_max_chars, long_chars=a.reply_max_chars_long
        )
    except (KeyError, IndexError, ValueError):
        # a hand-edited prompt may contain literal braces; fall back to the raw text
        text = template
    persona = (a.persona or "").strip()
    if persona:
        # the default persona names the assistant ("叫{name}"); a hand-written one without
        # braces must not raise
        try:
            persona = persona.format(name=a.name)
        except (KeyError, IndexError, ValueError):
            pass
        text += "\n" + persona
    if not any(name.startswith("music") for name in tool_names):
        # Measured 2026-10-22: with music playing and no music tool, it answered
        # 「好，接着放。」 - a promise it had no way to keep. Saying so is the only honest
        # option until the music layer exists.
        text += "\n" + NO_MUSIC_NOTE
    else:
        # The mirror case, measured 22:12: right after a stop turn the model answered
        # 「没有接音乐来源，放不了」 out of conversation context and never called the
        # tool. The library exists exactly when the music tools do - say so plainly.
        text += (
            "\n（你有音乐能力：曲库是家里的 NAS，放歌必须调用 music_play 工具，"
            "传输类（暂停/继续/下一首/上一首/停止）也必须调用对应 music_ 工具。"
            "不要凭对话上下文猜测曲库不可用——工具在就是可用的。）"
        )
    if tools_ok:
        text += "\n" + TOOL_RULES
    else:
        text += "\n" + NO_TOOL_NOTE
    text += "\n" + now_str(now)
    return text
