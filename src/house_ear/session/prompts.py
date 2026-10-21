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

from ..config.schema import AssistantConfig

DEFAULT_SYSTEM_PROMPT = (
    "你是家里的语音助手，叫{name}。"
    "说话自然、口语化。信息量优先：需要解释、对比、讲经过、或一轮里问了多件事时，"
    "把话说完整、说清楚，不要为了短而漏掉关键信息；日常寒暄一句，不超过{max_chars}个字。"
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
# earlier: this build has no tool layer at all, so anything live is unverifiable by
# construction.
NO_TOOL_NOTE = (
    "（注意：这一轮你没有任何查证工具，天气、新闻、股价、比分、几点、今天周几这类实时信息"
    "你都查不到。查不到就直接说查不到，建议用户自己看一眼；不要给具体数字、日期或者预报，"
    "也不要假装刚刚查过。"
    "你还没有接上家里的设备、音乐和提醒：灯、空调、窗帘、放歌、定时这些你现在都做不到，"
    "被问「你能做什么」时如实说现在只能陪着聊天、答问题，设备还没接上，"
    "不要列出你做不到的能力。）"
)


def build(assistant: AssistantConfig | None = None, *, tools_ok: bool = False) -> str:
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
    if not tools_ok:
        text += "\n" + NO_TOOL_NOTE
    return text
