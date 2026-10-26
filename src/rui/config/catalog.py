"""The console's configuration directory (docs/config-catalog-contract.md).

This module is not validation - `schema.py` already validates a `Config`. It is the
*UI directory*: every value a person may change, sorted into the four tiers 9哥 asked
for, each with a hint that says what changing it actually achieves and whether saving
is enough or the service (or just the audio plane) has to restart.

Keeping the directory next to the schema is deliberate: one file describes "what is
editable and why", the other describes "what is legal". A field that exists only in
the schema is a knob the console cannot show; a field that points at a path the schema
does not have is caught by the tests.

Effects:
  save    - written to disk and applied to the live process (next turn / next call).
  restart - written to disk, but the running process keeps the old value until the
            service restarts (`docker compose restart`); audio.* can be applied by
            restarting only the audio plane (`POST /api/v1/system/audio/restart`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import secrets
from ..env import get as env_get
from ..providers.volc_voices import voices_for
from .schema import LED_STATES, Config
from .store import redacted

RESTART_COMMAND = "docker compose restart"

# Top-level sections the dotted-path writer may touch. It is a coarse guard; the real
# rule is per-field (`writable` in the directory below).
WRITABLE_ROOT = [
    "assistant", "session", "audio", "ack", "system", "web", "security",
    "devices", "multi_device", "providers", "tools", "music",
]

# The four tiers, in 9哥's words: ① 底子（不轻易动）② 跟随音箱变化
# ③ 外部资源（LLM/ASR/TTS）④ 工具类.
TIERS = [
    {"id": 1, "title": {"zh": "底子", "en": "Foundation"},
     "hint": {"zh": "一般不动。改错会影响整条语音链路，通常要重启服务才生效。",
              "en": "Rarely touched. A wrong value affects the whole voice path and "
                    "usually needs a service restart."}},
    {"id": 2, "title": {"zh": "音箱相关", "en": "Speaker"},
     "hint": {"zh": "跟随音箱硬件变化：换另一款音箱时这些数字要重新量。改完重启音频面即可。",
              "en": "Follows the speaker hardware. Re-measure when changing speakers; "
                    "a restart of the audio plane applies them."}},
    {"id": 3, "title": {"zh": "外部资源", "en": "Providers"},
     "hint": {"zh": "LLM / ASR / TTS 的地址、模型和密钥变量名。改完要重启服务让整条链重新加载。",
              "en": "LLM / ASR / TTS endpoints, models and key variable names. "
                    "Restart the service so the chains reload."}},
    {"id": 4, "title": {"zh": "工具", "en": "Tools"},
     "hint": {"zh": "天气/新闻/搜索/HA/MCP/音乐音源等工具。多数保存即生效；音源本身改完要重启。",
              "en": "Weather/news/search/HA/MCP/music sources. Most apply on save; "
                    "the music source itself needs a restart."}},
]


def _t(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": en}


# id -> (title, tier, order, effect, hint). Dynamic groups (provider catalog, chains)
# are built in `field_specs` but their metadata lives here because the panel scoping
# (`component_args.groups`) names them.
_GROUPS: dict[str, tuple[dict, int, int, str, dict]] = {
    # --- tier 1: foundation ------------------------------------------------------
    "assistant": (_t("助手", "Assistant"), 1, 10, "save", _t(
        "助手是谁、怎么说话、回答多长。保存后下一轮就生效，不用重启。",
        "Who the assistant is and how long it answers. Saving applies it on the next turn.")),
    "session": (_t("会话", "Session"), 1, 20, "save", _t(
        "什么时候告别、告别说什么、哪些词结束会话、要不要带上下文。保存后下一轮生效。",
        "When the session ends, the goodbye line, dismiss words and context. "
        "Saving applies it on the next turn.")),
    "system": (_t("系统", "System"), 1, 100, "restart", _t(
        "数据目录、日志级别、时区。属于进程底子，改错会影响日志/记忆/密钥的位置，通常要重启。",
        "Data directory, log level and timezone. Process-level basics; a wrong value "
        "moves logs/memory/secrets and usually needs a restart.")),
    "web": (_t("控制台", "Console"), 1, 110, "restart", _t(
        "控制台自己的监听地址与端口。改完用新地址访问，需要重启服务。",
        "Where the console listens. Reach it on the new address after a restart.")),
    "security": (_t("出站安全", "Outbound security"), 1, 120, "restart", _t(
        "允许哪些外部/内网地址被工具访问（SSRF 防护）。云元数据与链路本地地址永远拒绝；"
        "默认允许内网服务（NAS、Home Assistant），默认不允许回环地址。改完需要重启服务。",
        "Which outbound addresses tools may reach (SSRF guard). Cloud metadata and "
        "link-local addresses are always refused; LAN services are allowed by default "
        "and loopback is not. Restart to apply.")),
    # --- tier 2: speaker ---------------------------------------------------------
    "devices": (_t("音箱", "Speakers"), 2, 10, "restart", _t(
        "已登记的音箱。新增/删除、唤醒词、音量目标和监听端口在「设备管理」页修改；"
        "保存后音频面自动重启。这里只读展示。",
        "Registered speakers. Add/remove, wake words, volume target and listen port "
        "live on the Device manager page; saving restarts the audio plane. Read-only here.")),
    "multi_device": (_t("多设备", "Multi-device"), 2, 20, "restart", _t(
        "多房间共存：每台启用音箱一个音频面，控制面统一仲裁唤醒（全屋同时刻一个回合、"
        "一个输出 owner）。关闭 = 0.4.x 单平面行为。arbitration.mode / "
        "prefer_playing_room / on_no_speech 已冻结为 score/true/end，没有对应旋钮。"
        "改完重启服务。",
        "Several rooms at once: one audio plane per enabled speaker and one arbiter in "
        "the control plane (one turn, one output owner for the house). Off = the 0.4.x "
        "single-plane behaviour. arbitration.mode / prefer_playing_room / on_no_speech "
        "are frozen to score/true/end and have no input. Restart the service.")),
    "audio.capture": (_t("采集", "Capture"), 2, 100, "restart", _t(
        "从音箱收到的原始格式。这些数值是按参考机型实测的（48000 Hz / 8 通道 / 32 bit，"
        "麦移位 12、参考移位 16），换音箱必须重新量；改错会变调、削顶或没声音。",
        "The raw format the speaker sends. Measured on the reference model (48000 Hz / 8 ch / 32 bit, "
        "mic shift 12, ref shift 16); re-measure for another speaker. A wrong value "
        "pitch-shifts, clips, or silences the input.")),
    "audio.voice": (_t("唤醒与分段", "Wake & segmentation"), 2, 110, "restart", _t(
        "唤醒词模型、VAD 静音判定、预录与唤醒尾巴。多数按实测调过；改错会吞首字、提前收尾或叫不醒。",
        "Wake model, VAD end-of-speech, preroll and wake tail. Tuned by measurement; a "
        "wrong value eats the first syllable, cuts early, or never wakes.")),
    "audio.playback": (_t("播放", "Playback"), 2, 120, "restart", _t(
        "推给音箱的音频格式和控速。参考机型的客户端只收 16 kHz 单声道；credit_window 是在途上限"
        "（8 = 实测 0 次阻塞）。改错会没声音或卡顿。",
        "Format pushed to the speaker and write pacing. The reference client takes 16 kHz mono "
        "only; credit_window is the in-flight bound (8 = zero blocked writes measured).")),
    "audio.aec": (_t("回声消除", "Echo cancellation"), 2, 130, "restart", _t(
        "回声消除。mix = 一个滤波器消 6 麦加权混音（默认，实测 23.5 dB）；关掉会听见自己、"
        "放歌时唤醒率直线下降。改完重启音频面。",
        "AEC. mix = one canceller on the weighted 6-mic mix (default, 23.5 dB measured); "
        "off means hearing itself and a much worse wake rate over music.")),
    "ack": (_t("唤醒应答", "Wake acknowledgement"), 2, 200, "restart", _t(
        "听到唤醒词后先播什么。tts 会占用麦克风约 1.2 s；earcon 是无损提示音。"
        "改文字/上限保存即生效，改模式/音效要重启音频面。",
        "What plays the instant the wake word fires. tts mutes the mic ~1.2 s; earcon is "
        "lossless. Text/cap apply on save; mode/chime need an audio-plane restart.")),
    # --- tier 3: providers -------------------------------------------------------
    "providers.catalog": (_t("模型提供方", "Providers"), 3, 10, "restart", _t(
        "ASR / TTS / LLM 的地址、模型名和密钥变量名。密钥值不在这里（去「密钥」页），"
        "这里填它所在环境变量的名字。改完重启服务让整条链重新加载。",
        "ASR / TTS / LLM endpoints, models and key variable names. Values are not here "
        "(use the Secrets page); this holds the variable name. Restart to reload.")),
    "providers.asr": (_t("语音识别链", "ASR chain"), 3, 20, "restart", _t(
        "ASR 的尝试顺序：第一个为主用，失败按顺序兜底。名字必须是上面目录里的键；改完需重启。",
        "ASR fallback order: first is primary, later ones are tried in turn. Names must "
        "exist in the catalog above; restart applies it.")),
    "providers.tts": (_t("语音合成链", "TTS chain"), 3, 30, "restart", _t(
        "TTS 的尝试顺序：讯飞主用、硅基兜底。改顺序/换主用后需要重启服务。",
        "TTS fallback order (讯飞 primary, SiliconFlow fallback). Restart after changing it.")),
    "providers.llm": (_t("大模型链", "LLM chain"), 3, 40, "restart", _t(
        "LLM 的尝试顺序。换模型/加兜底后需要重启服务；下一轮开始用新链。",
        "LLM fallback order. Restart after switching or adding a fallback.")),
    # --- tier 4: tools -----------------------------------------------------------
    "tools.weather": (_t("天气", "Weather"), 4, 10, "save", _t(
        "天气工具（open-meteo，不需要密钥）。城市留空时助手会先问你城市，而不是猜一个。"
        "保存后下一轮生效。",
        "Weather (open-meteo, no key). With an empty city the assistant asks instead of "
        "guessing. Applies on the next turn.")),
    "tools.news": (_t("新闻", "News"), 4, 20, "save", _t(
        "每日新闻/日报工具，源是公开静态镜像。关掉后模型不再有新闻能力。保存后下一轮生效。",
        "Daily news/digest tool from public static mirrors. Off = no news ability. "
        "Applies on the next turn.")),
    "tools.search": (_t("联网搜索", "Web search"), 4, 30, "save", _t(
        "联网搜索。关掉 = 不再实时查资料；改地址/密钥变量后下一次调用就用新值，无需重启。",
        "Web search. Off = no live lookups; endpoint/key-variable changes apply on the "
        "next call without a restart.")),
    "tools.ha": (_t("Home Assistant", "Home Assistant"), 4, 40, "save", _t(
        "Home Assistant（经 MCP）。填地址并保存会立刻重连，不用重启；token 值在「密钥」页或"
        "「MCP / HA」管理页填。",
        "Home Assistant over MCP. Saving the URL reconnects it live, no restart; the "
        "token is entered on the Secrets or MCP / HA page.")),
    "tools.mcp": (_t("MCP 服务器", "MCP servers"), 4, 50, "save", _t(
        "通用 MCP 服务器列表（当前只有 HA 真正接线）。在「MCP / HA」页维护，这里只读展示。",
        "Generic MCP server list (only HA is wired end to end today). Maintained on the "
        "MCP / HA page; read-only here.")),
    "tools.exposure": (_t("工具暴露策略", "Tool exposure"), 4, 60, "restart", _t(
        "哪些工具让模型看见：domain = 按域模板（推荐）、whitelist = 只放白名单、all = 全放"
        "（工具太多会挤爆提示词）。改完需重启服务。",
        "Which tools the model sees: domain (recommended), whitelist, or all (too many "
        "tools crowd out the prompt). Restart applies it.")),
    "music": (_t("音乐", "Music"), 4, 100, "restart", _t(
        "音乐源（当前 Navidrome/Subsonic）。改音源时 secrets_path / cache_dir 会自动指向"
        "data_dir 下的标准位置；enabled 保存即生效，其它字段改完要重启服务。",
        "Music source (Navidrome/Subsonic today). Changing the source auto-fills "
        "secrets_path / cache_dir under data_dir; enabled applies on save, the rest "
        "needs a restart.")),
}


def group_specs() -> list[dict]:
    """Group metadata in panel order (tier, then order)."""
    groups = [
        {"id": gid, "title": title, "tier": tier, "order": order,
         "effect": effect, "hint": hint}
        for gid, (title, tier, order, effect, hint) in _GROUPS.items()
    ]
    groups.sort(key=lambda g: (g["tier"], g["order"], g["id"]))
    return groups


def _field(
    path: str,
    group: str,
    kind: str,
    label: dict[str, str],
    hint: dict[str, str],
    *,
    effect: str = "save",
    unit: dict[str, str] | None = None,
    minimum: int | float | None = None,
    maximum: int | float | None = None,
    options: list[Any] | None = None,
    advanced: bool = False,
    writable: bool = True,
    nullable: bool = False,
    virtual: bool = False,
    audition: dict[str, Any] | None = None,
) -> dict:
    """One catalog entry. The contract keys are fixed; `nullable`/`virtual` are additive.

    `virtual` marks a value that is not a `Config` field: it lives in the keywords file
    (wake threshold/boost) or is derived from another file (music credentials present).
    """
    tier = _GROUPS[group][1]
    if effect not in ("save", "restart"):
        raise ValueError(f"bad effect {effect!r} for {path}")
    field = {
        "path": path,
        "group": group,
        "tier": tier,
        "kind": kind,
        "label": label,
        "hint": hint,
        "effect": effect,
        "unit": unit,
        "min": minimum,
        "max": maximum,
        "options": options,
        "advanced": advanced,
        "writable": writable,
        "nullable": nullable,
        "virtual": virtual,
    }
    if audition is not None:
        field["audition"] = audition
    return field


# ---------------------------------------------------------------- path helpers

def get_path(data: Any, path: str) -> Any:
    """Read a dotted path; integer segments index lists (`devices.0.volume`)."""
    node = data
    for part in path.split("."):
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, dict):
            node = node[part]
        else:  # a scalar in the middle: the directory and the schema disagree
            raise KeyError(path)
    return node


def set_path(data: Any, path: str, value: Any) -> None:
    """Write a dotted path in place, creating containers only for missing branches."""
    parts = path.split(".")
    node = data
    for index, part in enumerate(parts[:-1]):
        following = parts[index + 1]
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, dict):
            # A nullable override block is dumped as None (devices[0].audio); writing a
            # field inside it has to create the container instead of failing on None.
            if part not in node or node[part] is None:
                node[part] = [] if following.isdigit() else {}
            node = node[part]
        else:
            raise KeyError(path)
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    elif isinstance(node, dict):
        node[last] = value
    else:
        raise KeyError(path)


# ---------------------------------------------------------------- field tables

def coerce(spec: dict, value: Any) -> Any:
    """Validate one browser-supplied value against its catalog entry.

    Raises `ValueError` with a sentence the console can show under the field. Pydantic
    still validates the whole `Config` afterwards; this layer exists so the 422 names
    the *field* the person typed into, not a location three levels deep.
    """
    kind = spec["kind"]
    path = spec["path"]
    # A nullable field's empty browser value means "inherit / no override", not "".
    if spec.get("nullable") and (value is None or value == ""):
        return None
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
        return value
    if kind == "number":
        if value is None and spec.get("nullable"):
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        if spec["min"] is not None and value < spec["min"]:
            raise ValueError(f"must be at least {spec['min']}")
        if spec["max"] is not None and value > spec["max"]:
            raise ValueError(f"must be at most {spec['max']}")
        return value
    if kind in ("text", "textarea"):
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return value
    if kind == "select":
        options = spec["options"] or []
        # Options may carry localized labels ({"value", "label"}); compare the values.
        allowed = [option["value"] if isinstance(option, dict) else option
                   for option in options]
        if value not in allowed:
            raise ValueError(f"must be one of {allowed}")
        return value
    if kind == "list":
        if not isinstance(value, list):
            raise ValueError("must be a list of strings")
        if not all(isinstance(item, str) for item in value):
            raise ValueError("every item must be a string")
        return value
    raise ValueError(f"{path} has kind {kind!r} and cannot be written")


def _foundation_fields() -> list[dict]:
    return [
        _field("system.data_dir", "system", "text",
               _t("数据目录", "Data directory"),
               _t("日志、记忆、密钥、提醒、音乐缓存都放在这里。Docker 里通常是挂载的卷；"
                  "改错会“丢了”日志和记忆（其实是写到新目录了），改完必须重启。",
                  "Logs, memory, secrets, reminders and the music cache live here. In "
                  "Docker this is usually a mounted volume; a wrong value 'loses' logs "
                  "and memory, and a restart is required."),
               effect="restart", advanced=True),
        _field("system.log_level", "system", "select",
               _t("日志级别", "Log level"),
               _t("决定日志有多啰嗦。debug 会明显增大日志量，只在排障时临时用；"
                  "改完重启服务生效。",
                  "How verbose the log is. debug grows it a lot; use it only while "
                  "debugging. Restart to apply."),
               effect="restart", options=["debug", "info", "warning", "error"]),
        _field("system.timezone", "system", "text",
               _t("时区", "Timezone"),
               _t("修正「现在几点 / 今天周几」用的时区（提示词里注入的当前时间）。"
                  "留空 = 跟随进程本地时区；NAS 的 Docker 默认 UTC 时填 Asia/Shanghai，"
                  "否则助手会差 8 小时。提醒/定时目前固定按 Asia/Shanghai 解析。"
                  "保存后下一轮生效。",
                  "Fixes the clock injected into the prompt (「现在几点」). Empty = the "
                  "process local zone; on a NAS whose Docker runs UTC set Asia/Shanghai or "
                  "the assistant is 8 hours off. Reminders are still parsed in "
                  "Asia/Shanghai today. Applies on the next turn."),
               effect="save"),
        _field("web.host", "web", "text",
               _t("监听地址", "Bind address"),
               _t("控制台监听在哪。0.0.0.0 = 局域网都能访问（默认）；127.0.0.1 = 只有本机。"
                  "改完重启服务。",
                  "Where the console listens. 0.0.0.0 means the LAN (default), 127.0.0.1 "
                  "only this host. Restart to apply."),
               effect="restart"),
        _field("web.allow_anonymous", "web", "bool",
               _t("允许无令牌访问（危险）", "Allow anonymous access (dangerous)"),
               _t("默认 false：绑非本机地址且没有令牌时服务拒绝启动。只有在本机开发、"
                  "确定局域网可信时才打开——打开后同网段任何人都能读转写、改配置、"
                  "调设备。改完重启服务。",
                  "Default false: a non-loopback bind without a token refuses to start. "
                  "Only for development on a trusted LAN - anyone on the network can "
                  "read transcripts, change config and actuate devices. Restart."),
               effect="restart", advanced=True),
        _field("web.port", "web", "number",
               _t("端口", "Port"),
               _t("控制台端口（默认 8080）。和别的服务冲突时改这里，改完重启并用新端口访问。",
                  "Console port (8080 by default). Change it on a port conflict, then "
                  "restart and use the new port."),
               effect="restart", minimum=1, maximum=65535),
    ]


def _security_fields() -> list[dict]:
    return [
        _field("security.outbound.enabled", "security", "bool",
               _t("启用出站防护", "Enable outbound guard"),
               _t("关闭后 HA/MCP/音乐/搜索的 URL 不再做 SSRF 检查。只应在排障时临时关闭；"
                  "关闭后模型或外部数据可诱导服务访问云元数据或内网管理口。改完重启服务。",
                  "Disables the SSRF check for HA, MCP, music and search URLs. Turn it "
                  "off only while debugging: tool output or a stored URL could otherwise "
                  "reach cloud metadata or an admin port. Restart to apply."),
               effect="restart"),
        _field("security.outbound.allow_private", "security", "bool",
               _t("允许内网地址", "Allow private addresses"),
               _t("默认 true：自建服务通常在 10/8、172.16/12、192.168/16 或 IPv6 ULA。"
                  "设为 false 后只允许公网地址；云元数据、链路本地、组播、保留地址不受"
                  "这个开关影响。改完重启服务。",
                  "Default true because self-hosted services usually live on 10/8, "
                  "172.16/12, 192.168/16 or an IPv6 ULA. False permits public addresses "
                  "only. Metadata, link-local, multicast and reserved addresses stay "
                  "blocked regardless. Restart to apply."),
               effect="restart"),
        _field("security.outbound.allow_loopback", "security", "bool",
               _t("允许回环地址", "Allow loopback"),
               _t("默认 false：127.0.0.1/::1 上通常是控制台、模型服务和本机管理口，"
                  "不该由 MCP 工具或外部数据触发访问。只在本机开发且明确需要时打开，"
                  "或改用「额外放行主机」精确放行。改完重启服务。",
                  "Default false: 127.0.0.1/::1 usually holds the console, local model "
                  "servers and admin ports. Enable only for local development, or use "
                  "the extra-allowed host list for one exact name. Restart to apply."),
               effect="restart"),
        _field("security.outbound.extra_allowed_hosts", "security", "list",
               _t("额外放行主机", "Extra allowed hosts"),
               _t("精确主机名白名单（不带端口/协议）。用于 DNS 解析暂不可用、或想放行"
                  "回环/内网中的单个名字；它仍然绕不过云元数据/链路本地等硬性封禁。"
                  "改完重启服务。",
                  "Exact hostnames (no scheme/port). Useful when DNS is flaky or you "
                  "want one loopback/private name allowed. It cannot override the hard "
                  "metadata/link-local blocks. Restart to apply."),
               effect="restart"),
        _field("security.outbound.extra_allowed_cidrs", "security", "list",
               _t("额外放行网段", "Extra allowed CIDRs"),
               _t("按解析后的 IP 匹配的 CIDR 白名单，例如 192.168.5.0/24。放行范围应尽量小；"
                  "它同样绕不过云元数据/链路本地硬性封禁。改完重启服务。",
                  "CIDRs matched against the resolved IP, e.g. 192.168.5.0/24. Keep the "
                  "range as narrow as possible; the hard metadata/link-local blocks still "
                  "win. Restart to apply."),
               effect="restart"),
        _field("security.outbound.blocked_hosts", "security", "list",
               _t("额外封禁主机", "Extra blocked hosts"),
               _t("精确主机名黑名单，优先级高于所有放行条目。用于封掉一个不该被工具访问的"
                  "内网服务名。改完重启服务。",
                  "Exact hostnames that always lose, even against an allow entry. Use it "
                  "to deny one internal service name. Restart to apply."),
               effect="restart"),
        _field("security.outbound.blocked_cidrs", "security", "list",
               _t("额外封禁网段", "Extra blocked CIDRs"),
               _t("按解析后的 IP 匹配的 CIDR 黑名单，优先级最高。用于封掉一段内网管理地址；"
                  "云元数据和链路本地本来就是永封。改完重启服务。",
                  "CIDRs matched against the resolved IP; highest priority. Use it to "
                  "deny an admin range. Metadata and link-local are already always "
                  "blocked. Restart to apply."),
               effect="restart"),
    ]


def _assistant_fields() -> list[dict]:
    return [
        _field("assistant.name", "assistant", "text",
               _t("助手名字", "Assistant name"),
               _t("助手自称的名字，也会替换人设里的 {name}。保存后下一轮生效。",
                  "The name the assistant calls itself; also fills {name} in the persona. "
                  "Applies on the next turn."),
               unit=_t("字", "chars")),
        _field("assistant.persona", "assistant", "textarea",
               _t("人设", "Persona"),
               _t("追加在内置提示词后面的一段身份描述（叫什么叫、什么脾气）。只写身份，"
                  "别写「说话要简短」这类规则，那些在回答上限里。保存后下一轮生效。",
                  "Extra identity text appended to the built-in prompt. Keep it to "
                  "identity; length rules belong to the reply budgets. Next turn.")),
        _field("assistant.system_prompt", "assistant", "textarea",
               _t("整段系统提示词", "Full system prompt"),
               _t("整段替换内置提示词。留空 = 用内置的（推荐）。改错会改变全部回答风格，"
                  "排障时可用来对照。保存后下一轮生效。",
                  "Replaces the whole built-in prompt. Empty = built-in (recommended). A "
                  "wrong value changes every answer; useful for A/B. Next turn."),
               advanced=True),
        _field("assistant.reply_max_chars", "assistant", "number",
               _t("日常回答上限", "Everyday reply budget"),
               _t("日常闲聊/操作确认的字数上限：超过的部分会被服务端按句截断。调小更干脆，"
                  "调大更啰嗦。保存后下一轮生效。",
                  "Cap for everyday answers; the server truncates at a sentence boundary. "
                  "Smaller = terser, larger = longer. Next turn."),
               unit=_t("字", "chars"), minimum=10, maximum=300),
        _field("assistant.reply_max_chars_search", "assistant", "number",
               _t("查资料时上限", "Lookup reply budget"),
               _t("这一轮用过天气/新闻/搜索工具时允许的字数：给结论+事实留空间。"
                  "太大会拖长播报时间，太小会丢掉关键信息。保存后下一轮生效。",
                  "Budget for turns that used a lookup tool: room for a conclusion plus "
                  "facts. Too large slows playback, too small drops facts. Next turn."),
               unit=_t("字", "chars"), minimum=40, maximum=600),
        _field("assistant.reply_max_chars_long", "assistant", "number",
               _t("要求展开时上限", "Long reply budget"),
               _t("用户明确说「背全文 / 详细讲讲」时允许的字数。一次说完、不反问要不要继续。"
                  "保存后下一轮生效。",
                  "Budget when the user explicitly asks for the long version. Said in one "
                  "go, no 'shall I continue'. Next turn."),
               unit=_t("字", "chars"), minimum=100, maximum=2000),
        _field("assistant.filler_after_s", "assistant", "number",
               _t("慢回答垫话延迟", "Filler delay"),
               _t("模型迟迟不出声时，先垫一句「我查一下」的等待秒数。0 = 不垫（更安静，"
                  "但长等待会像没听见）。保存后下一轮生效。",
                  "Seconds of silence before speaking a filler line. 0 = none (quieter, but "
                  "a long wait reads as deaf). Next turn."),
               unit=_t("秒", "s"), minimum=0, maximum=10),
        _field("assistant.filler_after_tool_s", "assistant", "number",
               _t("工具调用垫话延迟", "Tool filler delay"),
               _t("模型开始调用工具后，多久先垫一句。0.6 是按工具实测调的值；"
                  "调大能让「在查了」更早出现，但会叠在慢工具的等待上。保存后下一轮生效。",
                  "Delay before a filler once the model starts a tool call. 0.6 measured; "
                  "larger surfaces 「在查了」 sooner but adds to the wait. Next turn."),
               unit=_t("秒", "s"), minimum=0, maximum=10, advanced=True),
        _field("assistant.filler_text", "assistant", "text",
               _t("垫话内容", "Filler text"),
               _t("垫话实际说什么（默认「我查一下。」）。改成别的话下一轮生效；"
                  "预渲染的那句仍是旧文本，第一次会现合成。",
                  "What the filler says (default 「我查一下。」). Applies next turn; the "
                  "pre-rendered clip is the old line, so the first use synthesises.")),
    ]


def _session_fields() -> list[dict]:
    return [
        _field("session.timeout_s", "session", "number",
               _t("空闲多久告别", "Idle goodbye"),
               _t("统一空闲规则：只要在会话里（唤醒没说话、回答结束、歌放完、停止播放后），"
                  "这么久没有新的输入就告别（「我先下啦」）并结束会话。调小更省事，调大更"
                  "「陪着」。0 = 永不超时。保存后立即按新值计时（进行中的窗口也生效）。",
                  "Unified idle rule: inside a session (a silent wake, a finished answer, "
                  "a finished song, after stop), this many seconds without new input says "
                  "the goodbye and ends it. 0 = never time out. Applies immediately, "
                  "including to an open window."),
               unit=_t("秒", "s"), minimum=0, maximum=300),
        _field("session.farewell", "session", "select",
               _t("告别方式", "Farewell mode"),
               _t("off = 静默退出；short = 说一句；full = 说那句邀请继续的话。"
                  "保存后下一次告别生效。",
                  "off = silent exit; short = one line; full = the line that invites a "
                  "follow-up. Applies at the next goodbye."),
               options=["off", "short", "full"]),
        _field("session.farewell_text", "session", "text",
               _t("短告别语", "Short farewell"),
               _t("short 模式下说的那句。保存后下一次告别生效。",
                  "The line spoken in short mode. Applies at the next goodbye.")),
        _field("session.farewell_text_full", "session", "text",
               _t("长告别语", "Full farewell"),
               _t("full 模式下说的那句（邀请用户继续说）。保存后下一次告别生效。",
                  "The line spoken in full mode. Applies at the next goodbye."),
               advanced=True),
        _field("session.end_words", "session", "list",
               _t("结束词", "Dismiss words"),
               _t("整句（或只多 2 个字）等于这些词才结束会话——不是子串匹配，"
                  "所以「关掉客厅灯」「关闭窗帘」这类 HA 命令不会被误当散场。"
                  "增删都要小心；保存后下一轮生效。",
                  "A request ends the session only when it is exactly one of these "
                  "phrases (or at most two characters longer) - not substring matching, "
                  "so HA commands such as 关掉客厅灯 are never mistaken for a dismissal. "
                  "Next turn."),
               advanced=True),
        _field("session.keep_context", "session", "bool",
               _t("保留多轮上下文", "Keep context"),
               _t("开 = 这一轮能看到前面的对话；关 = 每句都当新的一句。关掉更省 token，"
                  "但「继续/第三个」这类追问会失效。保存后下一轮生效。",
                  "On = this turn sees earlier turns; off = every turn stands alone. Off "
                  "saves tokens but breaks follow-ups. Next turn.")),
        _field("session.history_rounds", "session", "number",
               _t("上下文轮数", "Context rounds"),
               _t("把最近几轮（用户+助手算一轮）交给模型。太多会挤占提示词、让模型翻旧账；"
                  "太少会让重复问题塌成上一轮答案。保存后下一轮生效。",
                  "How many recent rounds the model sees. Too many crowds the prompt; too "
                  "few collapses repeated questions. Next turn."),
               unit=_t("轮", "rounds"), minimum=0, maximum=12),
        _field("session.vendor_quiet_s", "session", "number",
               _t("让位静默窗口", "Vendor quiet window"),
               _t("设备自带助手被唤醒后我们安静多久：避免两个助手同时答。调大更保守，"
                  "但可能吞掉紧接着的「你好小瑞」。保存后下一轮生效。",
                  "How long we stay quiet after the vendor assistant is woken. Larger is "
                  "safer but can swallow a quick wake. Next turn."),
               unit=_t("秒", "s"), minimum=0, maximum=60),
    ]


def _audio_fields() -> list[dict]:
    fields: list[dict] = []
    capture = [
        ("rate", "采集采样率", "Capture rate", "number", 8000, 96000,
         "音箱实际输出的采样率。参考机型为 48000 Hz（生产模板会让客户端降到 16000 Hz 以省带宽）；填错会变调或直接没声音。",
         "The rate the speaker actually sends. The reference model is 48000 Hz (the production "
         "template asks its client for 16000 Hz to save bandwidth); a wrong value pitch-shifts or silences it.", _t("Hz", "Hz")),
        ("channels", "总通道数", "Total channels", "number", 1, 16,
         "采集总通道数 = 麦克风通道 + 参考通道。参考机型为 8 通道。填错会让 DSP 读错帧。",
         "Total capture channels = mic + reference. The reference model has 8 channels; a wrong value misreads "
         "every frame.", None),
        ("mic_channels", "麦克风通道", "Mic channels", "number", 1, 16,
         "前几路是麦克风。参考机型有 6 路；换 4 麦音箱时改这里。",
         "The first N channels are mics. The reference model has 6; change it for a 4-mic speaker.",
         None),
        ("ref_channels", "参考通道", "Reference channels", "number", 0, 16,
         "最后几路是播放参考（供 AEC 使用）。参考机型有 2 路；删掉参考会失去回声消除。",
         "The last N channels are the playback reference for AEC. The reference model has 2; removing "
         "it loses echo cancellation.", None),
        ("bits", "位深", "Bit depth", "select", None, None,
         "设备 ALSA 的位深。参考机型为 32 bit；填错会导致电平异常或削顶。",
         "The device's ALSA bit depth. The reference model is 32 bit; a wrong value breaks levels or clipping.",
         _t("bit", "bit")),
        ("mic_shift", "麦克风移位", "Mic shift", "number", 0, 31,
         "定点右移位数，决定麦克风电平。12 = 留 4 bit 余量（实测值）；调小会削顶，"
         "调大会变轻、唤醒变差。",
         "Right-shift that sets the mic level. 12 leaves 4 bits of headroom (measured); "
         "smaller clips, larger gets quiet and loses wakes.", None),
        ("ref_shift", "参考移位", "Reference shift", "number", 0, 31,
         "参考通道的右移位数。参考满量程是麦克风的 32 倍，参考机型用 16；改错会让 AEC 效果骤降。",
         "Right-shift for the reference. Its full scale is 32x the mic, so the reference model uses 16; "
         "a wrong value cripples AEC.", None),
    ]
    for key, zh, en, kind, lo, hi, hzh, hen, unit in capture:
        options = [16, 32] if key == "bits" else None
        fields.append(_field(f"audio.capture.{key}", "audio.capture", kind, _t(zh, en),
                             _t(hzh, hen), effect="restart",
                             unit=unit, minimum=lo, maximum=hi, options=options))
    voice = [
        ("rate", "处理采样率", "Voice rate", "number", 8000, 48000,
         "AEC/KWS/VAD 运行采样率。16 kHz 是设计值（从采集降采样）；不要在这里跑 48 kHz。",
         "Rate AEC/KWS/VAD run at. 16 kHz is the design point (downsampled from capture); "
         "do not run DSP at 48 kHz.", _t("Hz", "Hz"), False),
        ("frame_ms", "帧长", "Frame length", "number", 10, 40,
         "每帧毫秒数。20 ms 是与模型/VAD 对齐的值；改错门限与分段都会漂。",
         "Milliseconds per frame. 20 ms aligns with the models/VAD; a wrong value drifts "
         "thresholds and segmentation.", _t("ms", "ms"), False),
        ("vad_min_speech_ms", "最短语音", "Min speech", "number", 50, 2000,
         "短于这个时长的声音不算一句话（防咳嗽/关门成轮）。调小更敏感但更容易误触发。",
         "Sounds shorter than this are not a turn (coughs/doors). Smaller is more "
         "sensitive and more false turns.", _t("ms", "ms"), False),
        ("vad_end_silence_ms", "收尾静音", "End silence", "number", 100, 3000,
         "说完后静音多久算一句结束。1000 ms 是按实测改的（600 会切掉句中停顿）；"
         "调小更快但也更容易截断。",
         "Silence that ends an utterance. 1000 ms measured (600 cut mid-sentence pauses); "
         "smaller is faster and cuts people off.", _t("ms", "ms"), False),
        ("vad_preroll_ms", "语音前预录", "VAD preroll", "number", 0, 1000,
         "检测到说话前多留的音频。350 ms 实测：200 会吃掉第一个字；调小可能丢字。",
         "Audio kept before detected onset. 350 ms measured: 200 ate the first syllable; "
         "smaller can clip it.", _t("ms", "ms"), False),
        ("wake_skip_ms", "唤醒尾巴", "Wake tail", "number", 0, 1000,
         "唤醒词自己尾巴的时长，唤醒后丢掉。300 ms 是实测值；调大可能吃掉紧接的正文。",
         "Length of the wake phrase's own tail, dropped after a wake. 300 ms measured; "
         "larger can eat the following words.", _t("ms", "ms"), True),
        ("wake_preroll_ms", "唤醒前预录", "Wake preroll", "number", 0, 1500,
         "唤醒词命中前保留的音频，回灌给识别器，避免「你好小瑞，现在几点」丢开头。"
         "500 ms 是实测值。",
         "Audio kept from before the KWS hit and replayed into the gate; 500 ms measured. "
         "Without it the words after the wake word are lost.", _t("ms", "ms"), True),
        ("followup_delay_ms", "跟进窗口延迟", "Follow-up delay", "number", 0, 10000,
         "回答结束后等多久重新开麦听下一句（毫秒）。1000 ms 是参考机型实测的回声尾；设备"
         "自己做 AEC 的插头（rui 不跑 AEC）残余更久，调到 2500 可减少把自己的回答"
         "听成一句。改完重启音频面。",
         "How long after playback drains before the mic reopens (ms). 1000 ms is the "
         "reference speaker's measured echo tail; a device-AEC plug (RUI AEC off) may need 2500. "
         "Restart the audio plane.", _t("ms", "ms"), True),
        ("followup_replay", "回放跟进尾巴", "Replay follow-up tail", "bool", None, None,
         "是否把「播放结束到重新开麦」之间的麦克风音频回灌给识别器。设备侧 AEC 的"
         "插头会在这里把自己的残响转成一句；关掉 = 不回灌（可能丢紧接回答的开头）。",
         "Replay the mic audio caught between playback end and the gate opening. Off "
         "stops a device-AEC speaker's residual echo becoming a turn, at the cost of "
         "possibly clipping the first syllable.", None, True),
        ("barge_in", "允许打断", "Barge-in", "bool", None, None,
         "说话中再次唤醒是否打断当前回答。关掉 = 不能打断（问答排队），调大音量时更稳。",
         "Whether a second wake interrupts the current answer. Off = no interrupts "
         "(answers queue); steadier at high volume.", None, False),
        ("kws_enabled", "启用唤醒词", "Wake word enabled", "bool", None, None,
         "是否启用我们自己的唤醒词。关掉 = 只能靠厂商唤醒或控制台「试一试」；"
         "无声开发/省 CPU 时用。改完重启音频面。",
         "Whether our own wake word runs. Off = only vendor wake or the console try-it; "
         "used for silent development. Restart the audio plane.", None, False),
        ("kws_model_dir", "唤醒模型目录", "Wake model dir", "text", None, None,
         "KWS 模型所在目录（fetch-models 下载到这里）。换唤醒词模型时改；改完重启音频面。",
         "Directory holding the KWS model (fetch-models writes it). Change it for another "
         "wake model; restart the audio plane.", None, True),
        ("kws_keywords_file", "关键词文件", "Keywords file", "text", None, None,
         "唤醒词与门限的落盘文件（真正生效的门限在这里，不在配置里）。"
         "换唤醒词后由「设备管理」/`rui keywords` 重生成。",
         "The file that actually carries the wake words and threshold. Regenerated by the "
         "Device manager / `rui keywords` after a wake-word change.", None, True),
        ("kws_probe_file", "探针关键词文件", "Probe keywords", "text", None, None,
         "低门限探针文件：只记日志、不触发唤醒，用来区分「门限太高」和「声音太小」。"
         "排障用，平时留空。",
         "Low-threshold probe that only logs, to tell 'threshold too high' from 'audio too "
         "quiet'. For debugging; empty normally.", None, True),
        ("kws_chunk", "唤醒分块", "KWS chunk", "number", 4, 16,
         "唤醒模型每次吃的帧数。8 是与模型对齐的值；改错会改唤醒延迟/漏唤醒。",
         "Frames per KWS step. 8 matches the model; a wrong value changes wake latency or "
         "misses wakes.", None, True),
        ("vad_model_path", "VAD 模型路径", "VAD model path", "text", None, None,
         "Silero VAD 模型文件（fetch-models 下载）。留空 = 不分段：唤醒后不会识别。"
         "改完重启音频面。",
         "Silero VAD model file (fetch-models). Empty = no segmentation: a wake is never "
         "transcribed. Restart the audio plane.", None, True),
    ]
    for key, zh, en, kind, lo, hi, hzh, hen, unit, advanced in voice:
        fields.append(_field(f"audio.voice.{key}", "audio.voice", kind, _t(zh, en),
                             _t(hzh, hen), effect="restart",
                             unit=unit, minimum=lo, maximum=hi, advanced=advanced,
                             options=None))
    playback = [
        ("rate", "播放采样率", "Playback rate", "number", 8000, 96000,
         "推给音箱的采样率。参考机型的客户端只接受 16000 Hz；填 48000 会被 adapter 拒绝、没有声音。",
         "Rate pushed to the speaker. The reference client takes 16000 Hz only; 48000 is rejected "
         "by the adapter (no sound).", _t("Hz", "Hz")),
        ("channels", "播放通道", "Playback channels", "number", 1, 8,
         "推给音箱的通道数。参考机型只接受单声道；填 2 会被拒绝。",
         "Channels pushed to the speaker. The reference model takes mono only; 2 is rejected.", None),
        ("queue_ms", "队列缓冲", "Queue buffer", "number", 20, 1000,
         "服务端播放队列的缓冲毫秒数。调大可吸收网络抖动，但打断时多吐一点尾巴。",
         "Server-side playback queue in ms. Larger absorbs jitter but keeps a little more "
         "queued audio to drain on an interrupt.", _t("ms", "ms")),
        ("fade_out_ms", "淡出时长", "Fade out", "number", 0, 500,
         "停止播放时的淡出毫秒数，防爆音。调大会让「停」听起来更慢。",
         "Fade-out on stop, to avoid a pop. Larger makes 'stop' sound slower.",
         _t("ms", "ms")),
        ("credit_window", "在途上限", "Credit window", "number", 0, 50,
         "允许领先设备回报的块数（100 ms/块）。8 = 实测 0 次阻塞；调到 2 会把管道灌满，"
         "每块都等 100 ms。0 = 只靠墙钟。",
         "Blocks (100 ms each) the plane may run ahead of device reports. 8 = zero blocked "
         "writes measured; 2 fills the pipe and waits ~100 ms per block. 0 = clock only.",
         None),
        ("target_silence_ms", "打断静音目标", "Interrupt target", "number", 50, 1500,
         "验收用的目标：用户开口后多少毫秒内完全静音（默认 300）。只影响验收口径与日志，"
         "不改变播放行为。",
         "Acceptance target: fully silent within this many ms of the user speaking "
         "(300 default). It documents the goal and the log line; it does not change "
         "playback.", _t("ms", "ms"), True),
    ]
    for key, zh, en, kind, lo, hi, hzh, hen, unit, *rest in playback:
        advanced = bool(rest[0]) if rest else False
        fields.append(_field(f"audio.playback.{key}", "audio.playback", kind,
                             _t(zh, en), _t(hzh, hen), effect="restart",
                             unit=unit, minimum=lo, maximum=hi, advanced=advanced))
    fields.extend([
        _field("audio.aec.enabled", "audio.aec", "bool",
               _t("启用回声消除", "AEC enabled"),
               _t("关掉后音箱会听见自己：放歌/播报时唤醒率暴跌，还可能自己答自己。"
                  "只有在没有参考通道时才考虑关。改完重启音频面。",
                  "Off makes the speaker hear itself: the wake rate collapses over playback "
                  "and it may answer itself. Only for a device without a reference "
                  "channel. Restart the audio plane."),
               effect="restart"),
        _field("audio.aec.filter_len", "audio.aec", "number",
               _t("滤波器长度", "Filter length"),
               _t("回声尾长度（taps，按 voice.rate 算）。4096 = 256 ms，是实测够用的值；"
                  "调大更吃 CPU，调小压不住长回声。改完重启音频面。",
                  "Echo tail in taps at voice.rate. 4096 = 256 ms, measured sufficient; "
                  "larger costs CPU, smaller leaves echo. Restart the audio plane."),
               effect="restart", minimum=256, maximum=16384),
        _field("audio.aec.cancel", "audio.aec", "select",
               _t("消法", "Canceller"),
               _t("mix = 一个滤波器消 6 麦加权混音（默认，实测 170 档 23.5 dB）；"
                  "per_channel = 每路一个（诊断用，ERLE 更差、CPU 6 倍）。别为了「更彻底」改回去。",
                  "mix = one canceller on the weighted 6-mic mix (default, 23.5 dB at "
                  "level 170); per_channel = one per mic (diagnostics only, worse ERLE, "
                  "6x CPU). Do not switch back for a 'more thorough' feeling."),
               effect="restart", options=["mix", "per_channel"]),
    ])
    ack = [
        ("mode", "应答方式", "Ack mode", "select", ["none", "earcon", "tts"],
         "none = 不出声；earcon = 提示音（不占麦克风）；tts = 说话（会压麦克风约 1.2 s）。"
         "改完重启音频面。", "none = silent; earcon = a tone (no mic loss); tts = a "
         "spoken word (~1.2 s of muted mic). Restart the audio plane.", "restart", False),
        ("text", "应答文字", "Ack text", "text", None,
         "tts 模式下说的那句（默认「我在。」）。控制在 max_ms 以内，否则会退回提示音。"
         "改了会自动重新合成，保存即生效。",
         "What tts mode says (default 「我在。」). It must fit max_ms or the tone is used "
         "instead. Re-synthesised on save, so no restart.", "save", False),
        ("chime", "提示音", "Chime", "select", ["soft2", "blip", "marimba", "glass", "piano"],
         "earcon 模式用的音效。改完重启音频面。",
         "The tone used in earcon mode. Restart the audio plane.", "restart", False),
        ("chime_ms", "提示音时长", "Chime length", "number", None,
         "提示音毫秒数（0 = 预设自然长度）。320 是为了填满客户端 300 ms 缓冲；太短会被憋到"
         "答案一起出。改完重启音频面。",
         "Cue length in ms (0 = preset length). 320 fills the client's 300 ms buffer; "
         "shorter sits silent until the reply. Restart the audio plane.", "restart", False),
        ("chime_trim", "提示音音量", "Chime trim", "number", None,
         "提示音音量系数（0.5–1.0）。只在听感上太响时调小；改完重启音频面。",
         "Cue level (0.5–1.0). Lower it only when it sounds too loud; restart the audio "
         "plane.", "restart", False),
        ("max_ms", "应答时长上限", "Ack cap", "number", None,
         "tts 应答的时长上限：超过就退回提示音，这是防止「应答吃麦 1.2 s+」的硬闸。"
         "保存后重新合成即生效。",
         "Hard cap for the tts cue: longer than this falls back to the tone (the cue "
         "holds the mic ~1.2 s+). Re-synthesised on save.", "save", False),
        ("echo_ignore_ms", "自听忽略窗口", "Echo ignore", "number", None,
         "tts 应答后忽略自己声音的毫秒数（防止应答尾巴被当成一句话）。改完重启音频面。",
         "How long the box ignores its own spoken cue so the tail cannot become a turn. "
         "Restart the audio plane.", "restart", False),
    ]
    # The real wake gate is the keywords file's own suffix (`... :2.00 #0.15 @word`),
    # not a value in config.yaml. These two are virtual fields: writing them rewrites
    # keywords.txt (preserving the wake words) and never touches the config schema - a
    # knob that does nothing is exactly the failure HANDOFF §6.2 recorded.
    fields.extend([
        _field("audio.voice.keywords.threshold", "audio.voice", "number",
               _t("唤醒门限", "Wake threshold"),
               _t("真正生效的唤醒门限（写在 keywords.txt 的 #0.15 里，不是配置字段）。"
                  "调高更难误唤醒、也更难唤醒；0.15 是实测值（0.20 时 4/5，0.15 时 4/4）。"
                  "保存会重写 keywords.txt，重启音频面生效。",
                  "The gate that actually runs (the #0.15 in keywords.txt, not a config "
                  "field). Higher = fewer false wakes and fewer wakes; 0.15 measured "
                  "(0.20 gave 4/5, 0.15 gave 4/4). Saving rewrites keywords.txt; restart "
                  "the audio plane."),
               effect="restart", minimum=0.01, maximum=1.0, virtual=True),
        _field("audio.voice.keywords.boost", "audio.voice", "number",
               _t("唤醒词权重加成", "Wake boost"),
               _t("关键词行的 :2.00 权重（旧的 3.00 只给探针用）。调高让该唤醒词更容易"
                  "压过背景声，也更容易误触发。保存会重写 keywords.txt，重启音频面生效。",
                  "The :2.00 weight on each keyword line (3.00 was the probe's value in the "
                  "old build). Higher makes the word win over background noise - and "
                  "false-trigger more. Saving rewrites keywords.txt; restart the plane."),
               effect="restart", minimum=0.1, maximum=10.0, advanced=True, virtual=True),
    ])
    for key, zh, en, kind, options, hzh, hen, effect, advanced in ack:
        lo = hi = None
        if key == "chime_ms":
            lo, hi = 0, 1200
        elif key == "chime_trim":
            lo, hi = 0.5, 1.0
        elif key == "max_ms":
            lo, hi = 100, 3000
        elif key == "echo_ignore_ms":
            lo, hi = 0, 3000
        unit = _t("ms", "ms") if key in ("chime_ms", "max_ms", "echo_ignore_ms") else None
        fields.append(_field(f"ack.{key}", "ack", kind, _t(zh, en), _t(hzh, hen),
                             effect=effect, unit=unit, minimum=lo, maximum=hi,
                             options=options, advanced=advanced))
    return fields


def _provider_fields(cfg: Config) -> list[dict]:
    """One field per provider setting, built from the live catalog.

    Provider names are deployment-specific (`local_asr`, `xfyun_tts`, ...), so the
    directory has to follow the config instead of hard-coding today's template.
    """
    fields: list[dict] = []
    stage_of = {}
    for stage in ("asr", "tts", "llm"):
        for name in getattr(cfg.providers, stage).chain:
            stage_of.setdefault(name, stage)
    meta: list[tuple[str, str, str, str, dict | None, int | float | None,
                     int | float | None, bool, dict[str, str] | None]] = [
        ("kind", "text", "类型", "Provider kind", None, None, None, False, None),
        ("base_url", "text", "API 地址", "Base URL", None, None, None, False, None),
        ("model", "text", "模型名", "Model", None, None, None, False, None),
        ("api_key_env", "text", "密钥变量名", "API key variable", None, None, None, True, None),
        ("timeout_s", "number", "超时", "Timeout", None, 0.1, 120, False, _t("秒", "s")),
        ("retries", "number", "重试次数", "Retries", None, 0, 3, True, _t("次", "times")),
        ("threads", "number", "线程数", "Threads", None, 1, 16, True, None),
        ("voice", "text", "音色", "Voice", None, None, None, True, None),
        ("app_id_env", "text", "App ID 变量名", "App ID variable", None, None, None, True, None),
        ("api_secret_env", "text", "API Secret 变量名", "API secret variable",
         None, None, None, True, None),
        ("max_tokens", "number", "回答 token 上限", "Max tokens",
         None, 16, 8192, True, None),
        ("temperature", "number", "随机度", "Temperature",
         None, 0.0, 2.0, True, None),
        ("thinking", "text", "思考模式", "Thinking", None, None, None, True, None),
    ]
    hints = {
        "kind": _t("这一条用哪种客户端：openai_compat 是通用 API，sherpa 是本地模型，"
                   "xfyun 是讯飞，volc_tts 是火山豆包语音合成，volc_asr 是火山豆包流式语音识别。"
                   "改错这一条链就用不了。",
                   "Which client this entry is: openai_compat (generic API), sherpa "
                   "(local model), xfyun, volc_tts (Volcano Doubao TTS), volc_asr "
                   "(Volcano streaming ASR). A wrong value breaks this chain entry."),
        "base_url": _t("API 地址。换自建/中转服务时改这里；改错后这一级会失败，才轮到兜底链。",
                       "API endpoint. Change it for a self-hosted/proxy service; if it is "
                       "wrong this level fails and the fallback is used."),
        "model": _t("模型名。换模型会改变速度/质量/价格；改完重启服务让链重新加载。",
                    "Model name. Switching changes speed/quality/price; restart so the "
                    "chain reloads."),
        "api_key_env": _t("密钥在环境变量里的变量名（不是密钥本身）。密钥值去「密钥」页填；"
                          "这里只决定程序去哪里找它。",
                          "The environment variable name that holds the key (not the key). "
                          "Fill the value on the Secrets page; this says where to look."),
        "timeout_s": _t("单次请求超时（秒）。网络慢可调大，但长超时会拖慢整个回合；"
                        "兜底链按这个时间依次等。",
                        "Per-request timeout (s). Larger survives slow links but delays the "
                        "turn; fallbacks wait in turn."),
        "retries": _t("失败后重试几次。1 次短重试通常比把超时调大更划算；0 = 不重试。",
                      "Retries after a failure. One short retry beats a longer timeout; "
                      "0 = no retry."),
        "threads": _t("本地模型的线程数（仅 sherpa 等本地模型）。N100 上 2 是实测值；"
                      "调大不一定更快。",
                      "Threads for a local model. 2 measured on N100; more is not "
                      "necessarily faster."),
        "voice": _t("TTS 音色名（仅 TTS 提供方）。改这个换说话声音。",
                    "TTS voice name (TTS providers only). Change the speaking voice."),
        "app_id_env": _t("讯飞这类需要 app_id 的厂商：填环境变量名，值去「密钥」页填。",
                          "For vendors that need an app_id (讯飞): the variable name; the "
                          "value goes on the Secrets page."),
        "api_secret_env": _t("需要第三个 secret 的厂商（讯飞）：填环境变量名，值去「密钥」页填。",
                              "For vendors that need a third secret (讯飞): the variable "
                              "name; the value goes on the Secrets page."),
        "max_tokens": _t("单次回答的 token 上限（仅 LLM）。3000 是生产值；调小可能把一句话"
                          "截在半句。",
                          "LLM answer token ceiling. 3000 is the production value; smaller "
                          "can cut a sentence in half."),
        "temperature": _t("LLM 随机度。0.4 是生产值；调高更发散、调低更稳定。",
                          "LLM temperature. 0.4 is the production value; higher is more "
                          "inventive, lower more repeatable."),
        "thinking": _t("思考模式（仅 LLM）。空 = 厂商默认；disabled = 关闭思考"
                        "（实测延迟翻 2–3 倍、准确率无收益）。",
                        "LLM thinking mode. Empty = vendor default; disabled = no chain of "
                        "thought (2-3x latency, no accuracy gain measured)."),
    }
    for name, provider in cfg.providers.catalog.items():
        stage = stage_of.get(name, "")
        for key, kind, zh, en, options, lo, hi, advanced, unit in meta:
            if key == "voice" and stage not in ("tts", ""):
                continue
            if key in ("max_tokens", "temperature", "thinking") and stage != "llm":
                continue
            if key == "threads" and str(getattr(provider, "kind", "")) != "sherpa":
                continue
            if key in ("app_id_env", "api_secret_env") and str(
                    getattr(provider, "kind", "")) != "xfyun":
                continue
            audition = None
            hint = hints[key]
            if key == "voice" and stage == "tts":
                kind = "select"
                options = voices_for(str(getattr(provider, "kind", ""))) or None
                hint = _t(
                    "TTS 说话用的音色（火山 2.0 这类提供方给出官方音色名）。改完保存后"
                    "重启服务生效；可点旁边的「试听」先用当前选择念一句，试听不写配置。",
                    "The speaking voice for this TTS provider (火山 2.0 lists its official "
                    "speakers). Save then restart the service to apply; use 「试听」 to hear "
                    "the current choice first - it does not write config.")
                advanced = False  # the voice picker is a first-class control now
                if options:
                    audition = {"stage": "tts", "provider": name}
            fields.append(_field(
                f"providers.catalog.{name}.{key}", "providers.catalog", kind,
                {"zh": f"{name} · {zh}", "en": f"{name} · {en}"}, hint,
                effect="restart", unit=unit, minimum=lo, maximum=hi, options=options,
                advanced=advanced, audition=audition,
                # Security (audit round 2 P1-7): an env NAME decides which secret is
                # sent where; making it writable let a console token exfiltrate any
                # ambient secret by pointing it at another variable.
                writable=key not in ("api_key_env", "app_id_env", "api_secret_env")))
        if stage == "tts":
            fields.append(_field(
                f"providers.catalog.{name}.speech_rate", "providers.catalog", "number",
                {"zh": f"{name} · 语速", "en": f"{name} · Speech rate"},
                _t("相对正常语速的调节：-50 = 0.5 倍，0 = 正常，100 = 2 倍。"
                   "火山/讯飞都支持；改完保存并重启服务生效，可先「试听」。",
                   "Speech rate relative to normal: -50 = 0.5x, 0 = normal, 100 = 2x. "
                   "Works for 火山 and 讯飞; save + restart, or audition first."),
                effect="restart", unit={"zh": "%", "en": "%"},
                minimum=-50, maximum=100))
        if stage == "tts" and str(getattr(provider, "kind", "")) == "volc_tts":
            fields.append(_field(
                f"providers.catalog.{name}.emotion", "providers.catalog", "select",
                {"zh": f"{name} · 情感", "en": f"{name} · Emotion"},
                _t("给音色加情绪。只有部分音色支持（火山 2.0 的多情感音色），"
                   "不支持的音色会忽略；不同音色支持的情绪范围不同。先「试听」再决定。",
                   "Adds an emotion to the voice. Only some voices support it (火山 2.0 "
                   "multi-emotion speakers); unsupported voices ignore it, and the "
                   "supported set differs per voice. Audition before saving."),
                effect="restart",
                options=[{"value": "", "label": {"zh": "默认（不加情感）", "en": "Default (none)"}},
                         {"value": "happy", "label": {"zh": "开心", "en": "Happy"}},
                         {"value": "sad", "label": {"zh": "难过", "en": "Sad"}},
                         {"value": "angry", "label": {"zh": "生气", "en": "Angry"}},
                         {"value": "surprised", "label": {"zh": "惊讶", "en": "Surprised"}},
                         {"value": "fear", "label": {"zh": "害怕", "en": "Fear"}},
                         {"value": "hate", "label": {"zh": "厌恶", "en": "Hate"}},
                         {"value": "excited", "label": {"zh": "兴奋", "en": "Excited"}},
                         {"value": "coldness", "label": {"zh": "冷漠", "en": "Coldness"}},
                         {"value": "neutral", "label": {"zh": "平静", "en": "Neutral"}}]))
            fields.append(_field(
                f"providers.catalog.{name}.emotion_scale", "providers.catalog", "number",
                {"zh": f"{name} · 情感强度", "en": f"{name} · Emotion strength"},
                _t("设置情感后生效：1–5，越大情绪越明显（默认 4）。先「试听」再决定。",
                   "Applies after an emotion is set: 1–5, larger is more expressive "
                   "(default 4). Audition before saving."),
                effect="restart", minimum=1, maximum=5, advanced=True))
    names = list(cfg.providers.catalog)
    for stage in ("asr", "tts", "llm"):
        fields.append(_field(
            f"providers.{stage}.chain", f"providers.{stage}", "list",
            _t(f"{stage.upper()} 尝试顺序", f"{stage.upper()} chain"),
            _t("按顺序尝试的提供方名字：第一个为主，后面的依次兜底。名字必须是 "
               "providers.catalog 里的键；填错后者整条链校验失败。改完重启服务。",
               "Provider names tried in order: first is primary, the rest are fallbacks. "
               "Names must exist in providers.catalog; restart to apply."),
            effect="restart", options=names))
    return fields


def _tool_fields(cfg: Config) -> list[dict]:
    fields = [
        _field("tools.weather.city", "tools.weather", "text",
               _t("默认城市", "Default city"),
               _t("问天气没提城市时用这里。留空 = 助手先问你城市，而不是猜一个（公开仓库"
                  "不能带个人默认城市）。保存后下一轮生效。",
                  "City used when a weather question names none. Empty = the assistant asks "
                  "instead of guessing (the public repo must not ship a home town). Next "
                  "turn.")),
        _field("tools.weather.latitude", "tools.weather", "number",
               _t("纬度", "Latitude"),
               _t("城市的精确纬度（可选）。和经度一起填能避免同名城市查错；留空用城市名。"
                  "保存后下一轮生效。",
                  "Exact latitude (optional). With longitude it disambiguates same-named "
                  "cities; empty falls back to the city name. Next turn."),
               advanced=True, minimum=-90, maximum=90),
        _field("tools.weather.longitude", "tools.weather", "number",
               _t("经度", "Longitude"),
               _t("城市的精确经度（可选）。与纬度成对使用。保存后下一轮生效。",
                  "Exact longitude (optional), used with latitude. Next turn."),
               advanced=True, minimum=-180, maximum=180),
        _field("tools.weather.days_max", "tools.weather", "number",
               _t("最多预报天数", "Forecast days"),
               _t("天气工具最多能查几天（1–7）。调大模型能回答「周末天气」，但每次请求更大。"
                  "保存后下一轮生效。",
                  "How many days the weather tool may fetch (1-7). Larger answers weekend "
                  "questions but makes each request bigger. Next turn."),
               unit=_t("天", "days"), minimum=1, maximum=7),
        _field("tools.weather.timeout_s", "tools.weather", "number",
               _t("天气超时", "Weather timeout"),
               _t("天气请求超时（秒）。open-meteo 偶发慢，8 秒是实测值；调大能多等一次。"
                  "保存后下一轮生效。",
                  "Weather request timeout. open-meteo is occasionally slow; 8 s measured. "
                  "Larger waits longer. Next turn."),
               unit=_t("秒", "s"), minimum=2, maximum=30, advanced=True),
        _field("tools.news.enabled", "tools.news", "bool",
               _t("启用新闻", "News enabled"),
               _t("关掉后模型没有新闻/日报能力（会如实说查不到）。工具只在启动时按开关"
                  "注册，改完重启服务生效。",
                  "Off removes the news/digest ability (the model says it cannot check). "
                  "Tools are registered at startup, so restart to apply."),
               effect="restart"),
        _field("tools.news.source", "tools.news", "text",
               _t("新闻源", "News source"),
               _t("每日摘要的公开镜像代号（默认 60s）。换源需要对应的镜像地址；普通用户不用改。"
                  "保存后下一轮生效。",
                  "Public digest mirror code (60s by default). Changing it needs a matching "
                  "mirror; most installs never touch it. Next turn."),
               advanced=True),
        _field("tools.news.timeout_s", "tools.news", "number",
               _t("新闻超时", "News timeout"),
               _t("抓每日摘要的超时（秒）。源站偶发不可达时有并行兜底，超时决定等多久。"
                  "保存后下一轮生效。",
                  "Digest fetch timeout. A parallel fallback runs when a mirror is down; "
                  "this bounds the wait. Next turn."),
               unit=_t("秒", "s"), minimum=2, maximum=30, advanced=True),
        _field("tools.search.enabled", "tools.search", "bool",
               _t("启用联网搜索", "Search enabled"),
               _t("关掉后不再实时查资料（价格/比分/新闻类问题会如实说查不到）。工具只在"
                  "启动时按开关注册，改完重启服务生效。",
                  "Off removes live lookups (prices/scores/news answer honestly that they "
                  "cannot check). Tools are registered at startup, so restart to apply."),
               effect="restart"),
        _field("tools.search.kind", "tools.search", "select",
               _t("搜索客户端", "Search client"),
               _t("deepseek = 走 DeepSeek 搜索端点；none = 停用该类客户端。选 none 等价于关闭"
                  "搜索。工具只在启动时注册，改完重启服务生效。",
                  "deepseek = the DeepSeek search endpoint; none = disable this client "
                  "(same as turning search off). Tools are registered at startup, so "
                  "restart to apply."),
               options=["deepseek", "none"], effect="restart"),
        _field("tools.search.base_url", "tools.search", "text",
               _t("搜索地址", "Search URL"),
               _t("搜索 API 地址。换供应商/自建中转时改；保存后下一次调用生效。",
                  "Search API endpoint. Change it for another vendor/proxy; applies on the "
                  "next call.")),
        _field("tools.search.api_key_env", "tools.search", "text",
               _t("搜索密钥变量（只读）", "Search key variable (read-only)"),
               _t("搜索密钥所在的变量名（不是密钥本身）。密钥值去「密钥」页填。"
                  "只读：变量名决定哪个凭据被发往哪里，不在控制台改。",
                  "Variable name holding the search key (not the key). Fill the value on "
                  "the Secrets page. Read-only: the variable name decides which credential "
                  "is sent where, so it is not editable from the console."),
               advanced=True, writable=False),
        _field("tools.search.count", "tools.search", "number",
               _t("结果条数", "Result count"),
               _t("一次搜索取几条结果（1–10）。条数多给模型更多资料，也更占提示词。"
                  "保存后下一次调用生效。",
                  "Results per search (1-10). More gives the model more material and uses "
                  "more prompt. Applies on the next call."),
               unit=_t("条", "results"), minimum=1, maximum=10),
        _field("tools.search.timeout_s", "tools.search", "number",
               _t("搜索超时", "Search timeout"),
               _t("搜索请求超时（秒）。超过就按「查不到」处理，避免拖垮整轮。保存后下一次调用生效。",
                  "Search timeout; past it the turn treats the search as unavailable so "
                  "the answer is not held hostage. Applies on the next call."),
               unit=_t("秒", "s"), minimum=2, maximum=60, advanced=True),
        _field("tools.ha.base_url", "tools.ha", "text",
               _t("家庭助理地址", "Home Assistant address"),
               _t("家庭助理（Home Assistant）的 MCP 地址。填好保存会立刻重连并注册家庭助理工具，"
                  "不用重启；留空 = 不启用家庭助理。",
                  "Home Assistant MCP endpoint. Saving reconnects and registers the Home "
                  "Assistant tools live, no restart; empty = Home Assistant off.")),
        _field("tools.ha.token_env", "tools.ha", "text",
               _t("家庭助理令牌变量（只读）", "Home Assistant token variable (read-only)"),
               _t("家庭助理长期令牌所在的变量名（不是令牌本身）。值去「密钥」页或家庭助理管理页填。"
                  "只读：变量名决定哪个凭据被发往哪里。",
                  "Variable name holding the Home Assistant long-lived token (not the token). "
                  "Fill the value on the Secrets or Home Assistant page. Read-only: the "
                  "variable name decides which credential is sent where."),
               advanced=True, writable=False),
        _field("tools.mcp_servers", "tools.mcp", "list",
               _t("MCP 服务器列表", "MCP servers"),
               _t("通用 MCP 服务器列表（name/transport/url/command/enabled），每台的连接"
                  "状态/工具数/延迟见「MCP / HA」页。在这里只读；编辑走 PUT /api/v1/mcp。"
                  "transport=stdio 的服务器要先打开下面的「允许 stdio」开关。",
                  "Generic MCP servers (name/transport/url/command/enabled); per-server "
                  "connected/tools/latency is on the MCP / HA page. Read-only here; edit via "
                  "PUT /api/v1/mcp. transport=stdio requires the allow-stdio switch below."),
               effect="save", writable=False),
        _field("tools.mcp_allow_stdio", "tools.mcp", "bool",
               _t("允许 stdio MCP", "Allow stdio MCP"),
               _t("⚠ 打开后 rui 会在本机直接执行 stdio 服务器的命令（等于代码执行权限）。"
                  "子进程只拿到最小环境变量（PATH/HOME/LANG + 服务器自己声明的 env），"
                  "看不到 .env 里的模型/HA/设备密钥；但仍只应添加你完全信任的服务器。"
                  "关闭时 PUT /api/v1/mcp 会拒绝 transport=stdio，测试端点也不会启动子进程。"
                  "默认关闭；改完重启服务生效。",
                  "⚠ When on, rui executes the stdio server's command on this host "
                  "(code execution). The child gets a minimal environment (PATH/HOME/LANG "
                  "plus env declared by the server itself) and cannot see the .env "
                  "provider/HA/device keys - but only add servers you fully trust. When "
                  "off, PUT /api/v1/mcp rejects transport=stdio and even the test endpoint "
                  "will not spawn a child. Off by default; restart to apply."),
               effect="restart"),
        _field("tools.expose", "tools.exposure", "select",
               _t("暴露策略", "Exposure mode"),
               _t("domain = 按域模板（推荐）；whitelist = 只放白名单里的工具；all = 全放"
                  "（工具太多会挤爆提示词、变慢）。改完重启服务。",
                  "domain = per-domain templates (recommended); whitelist = only the list; "
                  "all = everything (too many tools crowd the prompt). Restart to apply."),
               effect="restart", options=["domain", "whitelist", "all"]),
        _field("tools.whitelist", "tools.exposure", "list",
               _t("白名单", "Whitelist"),
               _t("expose = whitelist 时真正放给模型的工具名列表。名字写错只是看不到该工具；"
                  "改完重启服务。",
                  "Tool names exposed when expose = whitelist. A typo just hides that tool; "
                  "restart to apply."),
               effect="restart", advanced=True),
    ]
    return fields


def _music_source_registry() -> tuple[list[str], str, str]:
    """The select options and bilingual one-line list, from the preset registry."""
    from ..music.sources.presets import PRESETS

    available = [preset for preset in PRESETS if preset.supported]
    ids = [preset.id for preset in available]
    zh = "、".join(f"{preset.name_zh}（{preset.id}）" for preset in available)
    en = ", ".join(f"{preset.name_en} ({preset.id})" for preset in available)
    return ids, zh, en


def _music_fields() -> list[dict]:
    source_ids, source_list_zh, source_list_en = _music_source_registry()
    return [
        _field("music.enabled", "music", "bool",
               _t("启用音乐", "Music enabled"),
               _t("打开后模型获得点歌/暂停/下一首等 9 个工具，控制台出现音乐面板。"
                  "需要先在「音源与凭据」里选音源、填好凭据并测试连接。"
                  "保存即生效（运行期装配服务与工具）；关掉立即摘掉这些工具。",
                  "On gives the model the nine music tools and the console its music "
                  "panel. Pick a source, fill its credentials and test the connection "
                  "under Source & credentials first. Applies on save; off removes the "
                  "tools at once.")),
        _field("music.has_credentials", "music", "bool",
               _t("凭据已配置", "Credentials present"),
               _t("当前音源所需的凭据是否齐全（只报存在，绝不回显任何值）。"
                  "在「音源与凭据」里保存到 data_dir/lan_media.json（0600），再用"
                  "「测试连接」验证；空值删除。",
                  "Whether the current source's required credentials are complete "
                  "(presence only; no value is ever returned). Saved to "
                  "data_dir/lan_media.json (0600) from Source & credentials and verified "
                  "with Test connection; empty deletes."),
               effect="save", writable=False, virtual=True),
        # Select, not free text: a typo used to leave the panel "enabled" while the
        # runtime silently kept music off. The options come from the preset registry,
        # so a new adapter is one entry there rather than a second list here.
        _field("music.source", "music", "select",
               _t("音乐源", "Music source"),
               _t(f"音源预设（{source_list_zh}）。Subsonic 家族共用同一套协议与凭据槽位；"
                  "自定义 = 任何 Subsonic 兼容的自建地址。保存后运行期重建音乐服务，"
                  "并把 secrets_path 填成 <data_dir>/lan_media.json、"
                  "cache_dir 填成 <data_dir>/music_cache。未知音源保存时 422；"
                  "手改 YAML 的未知值不会让面板假装已启用。",
                  f"Source preset ({source_list_en}). The Subsonic family shares one "
                  "protocol and credential slots; Custom is any self-hosted "
                  "Subsonic-compatible URL. Saving rebuilds the music service at runtime "
                  "and points secrets_path at <data_dir>/lan_media.json and cache_dir at "
                  "<data_dir>/music_cache. An unknown source is a 422 on save; a "
                  "hand-edited unknown value cannot make the panel claim it is enabled."),
               effect="save", options=source_ids),
        _field("music.secrets_path", "music", "text",
               _t("凭据文件", "Credentials file"),
               _t("存当前音源凭据的 json 路径（值不进配置文件、不进日志）。"
                  "换音源时通常自动更新；手动改要保证 NAS 上该文件存在。"
                  "凭据本身用「音源与凭据」端点写入（PUT /api/v1/music/credentials），"
                  "这里只决定文件放哪。保存后运行期重建音乐服务。",
                  "JSON path holding the current source's credentials (never in the "
                  "config or the log). Auto-updated on a source change; a manual path "
                  "must exist on the NAS. Write the credentials via "
                  "PUT /api/v1/music/credentials; this only decides where the file "
                  "lives. Saving rebuilds the music service at runtime."),
               effect="save", advanced=True),
        _field("music.cache_dir", "music", "text",
               _t("缓存目录", "Cache directory"),
               _t("解析后的 PCM 缓存在这里（重放同一首不用再下载转码）。换音源时通常自动更新；"
                  "保存后运行期重建音乐服务，旧缓存留在旧目录。",
                  "Resolved PCM cache (replaying a song avoids a re-download/transcode). "
                  "Auto-updated on a source change; saving rebuilds the music service, "
                  "the old cache stays behind."),
               effect="save"),
        _field("music.cache_tracks", "music", "number",
               _t("缓存首数", "Cached tracks"),
               _t("最多缓存几首歌的 PCM（1–64）。调大省流量但占磁盘；改完重启服务。",
                  "How many tracks' PCM to keep (1-64). Larger saves downloads and uses "
                  "disk; restart to apply."),
               effect="restart", minimum=1, maximum=64, advanced=True),
        _field("music.resume_hold_ms", "music", "number",
               _t("打断后续播延迟", "Resume delay after speech"),
               _t("说话打断音乐后，答完再等多久恢复播放（毫秒）。0 = 答完立刻续播（之后"
                  "要换歌得重新唤醒）；调大 = 留时间直接说「换一首」而不必唤醒，但中间"
                  "安静更久。默认 2500。改完重启音频面/服务生效。",
                  "How long the song stays parked after an answer ends (ms). 0 resumes "
                  "immediately (a follow-up then needs the wake word); larger leaves room "
                  "for 「换一首」 without a wake word but a longer silence. Default 2500. "
                  "Restart the audio plane/service to apply."),
               effect="restart", minimum=0, maximum=10000, unit=_t("ms", "ms")),
        _field("music.queue_min_tracks", "music", "number",
               _t("队列补齐阈值", "Top-up threshold"),
               _t("队列剩几首时开始补下一批（1–10）。调大更早补歌、几乎不断流，但会多占曲库请求。"
                  "改完重启服务。",
                  "Top up when this many tracks are left (1-10). Larger refills earlier and "
                  "avoids gaps but costs more library calls. Restart to apply."),
               effect="restart", unit=_t("首", "tracks"), minimum=1, maximum=10,
               advanced=True),
        _field("music.queue_batch", "music", "number",
               _t("每批首数", "Batch size"),
               _t("每次从曲库拉多少首进队列（1–50）。调大减少请求、但「随便放点」更偏向同一批。"
                  "改完重启服务。",
                  "Tracks fetched per radio batch (1-50). Larger means fewer requests but "
                  "'play something' stays within one batch. Restart to apply."),
               effect="restart", unit=_t("首", "tracks"), minimum=1, maximum=50,
               advanced=True),
        _field("music.history_max", "music", "number",
               _t("历史保留首数", "History size"),
               _t("记住多少首已播过的歌用于去重（10–500）。调大随机播放更不容易重复；"
                  "改完重启服务。",
                  "How many played track ids are remembered for radio de-duplication "
                  "(10-500). Larger makes random play repeat less. Restart to apply."),
               effect="restart", unit=_t("首", "tracks"), minimum=10, maximum=500,
               advanced=True),
    ]


def _device_fields(cfg: Config) -> list[dict]:
    """Per-device fields, so the speaker tier is not a read-only dead corner.

    The device manager page is the sequence-oriented editor (add/remove/probe); the
    catalog exposes the same values one field at a time - including the LED ring, which
    has no other console input. Every entry restarts the audio plane (the adapter reads
    the device list and the ring config at process start), and wake words also regenerate
    the keywords file before the restart.
    """
    fields = [
        _field("devices", "devices", "list",
               _t("已登记音箱", "Registered speakers"),
               _t("当前配置里的音箱概览。新增/删除/探测在「设备管理」页；下面按设备列出可改字段。"
                  "设备类字段都要重启音频面（保存后会提示），唤醒词还会自动重生成关键词文件。",
                  "Overview of the configured speakers. Add/remove/probe on the Device "
                  "manager page; the editable fields are listed per device below. Device "
                  "changes need an audio-plane restart, and wake-word edits regenerate the "
                  "keywords file."),
               effect="restart", writable=False),
    ]
    for index, device in enumerate(cfg.devices):
        prefix = f"devices.{index}"
        name = device.id or f"devices[{index}]"
        fields.extend([
            _field(f"{prefix}.id", "devices", "text",
                   {"zh": f"{name} · 设备 id", "en": f"{name} · device id"},
                   _t("适配器和日志里用的名字（小写字母/数字/连字符）。改名后旧 id 的脚本和收藏链接"
                      "会失效；改完重启音频面。",
                      "The name used by the adapter and the log. Renaming breaks scripts and "
                      "bookmarks that use the old id; restart the audio plane."),
                   effect="restart", advanced=True),
            _field(f"{prefix}.adapter", "devices", "text",
                   {"zh": f"{name} · 驱动类型", "en": f"{name} · adapter"},
                   _t("用哪种驱动：填已安装 plug 的 manifest 里声明的机型 id（设备管理页会列出），"
                      "或 mock。填错设备会不可用；改完重启音频面。",
                      "Which driver runs this speaker: the device id declared by an installed "
                      "plug manifest (the Device manager lists them), or mock. A wrong "
                      "value makes it unavailable; restart the audio plane."),
                   effect="restart", advanced=True),
            _field(f"{prefix}.host", "devices", "text",
                   {"zh": f"{name} · 地址", "en": f"{name} · host"},
                   _t("音箱在局域网里的地址或主机名（SSH/连接目标）。换 IP 时改这里；"
                      "改完重启音频面，旧连接会断。",
                      "The speaker's LAN address or host name (SSH/connect target). Change "
                      "it on an IP change; restarting drops the old connection."),
                   effect="restart"),
            _field(f"{prefix}.room", "devices", "text",
                   {"zh": f"{name} · 房间", "en": f"{name} · room"},
                   _t("房间名，显示在控制台和语音回答里（「客厅的灯」这类指代会用到）。"
                      "只影响命名，不影响音频；改完重启音频面。",
                      "Room name shown in the console and used in spoken references. "
                      "Naming only; restart the audio plane."),
                   effect="restart"),
            _field(f"{prefix}.enabled", "devices", "bool",
                   {"zh": f"{name} · 启用", "en": f"{name} · enabled"},
                   _t("关掉 = 不驱动这台音箱（配置保留，随时开回）。适合临时拿掉一台；"
                      "改完重启音频面。",
                      "Off stops driving this speaker while keeping its config. Restart "
                      "the audio plane."),
                   effect="restart"),
            _field(f"{prefix}.wake_words", "devices", "list",
                   {"zh": f"{name} · 唤醒词", "en": f"{name} · wake words"},
                   _t("唤醒词列表（最多 3 个，每个不超过 12 个字）。保存会自动重新生成 "
                      "keywords.txt；音频面重启后生效，旧的唤醒词随即失效。",
                      "Wake words (at most 3, 12 characters each). Saving regenerates "
                      "keywords.txt; the audio plane must restart before they work, and the "
                      "old words stop working."),
                   effect="restart"),
            _field(f"{prefix}.volume", "devices", "number",
                   {"zh": f"{name} · 音量目标", "en": f"{name} · volume target"},
                   _t("音量目标（0–100；0 = 归原厂不接管，86 约等于设备 220 档）。音频面按它"
                      "对齐设备混音器；厂商会自己改音量，所以设备实际值看「设备」页。"
                      "改完重启音频面。",
                      "Volume target (0-100; 0 = let the vendor own it, 86 is about device "
                      "level 220). The plane reconciles the mixer to it, though the vendor "
                      "rewrites it - read the real value on the Devices page. Restart the "
                      "audio plane."),
                   effect="restart", unit=_t("%", "%"), minimum=0, maximum=100),
            _field(f"{prefix}.listen_port", "devices", "number",
                   {"zh": f"{name} · 监听端口", "en": f"{name} · listen port"},
                   _t("入站监听端口（音箱客户端拨进来的端口）。留空 = 用 adapter 默认；"
                      "改完重启音频面，改错音箱会连不上。",
                      "Inbound port the speaker client dials. Empty = adapter default; a "
                      "wrong value stops the speaker from connecting. Restart the plane."),
                   effect="restart", minimum=1, maximum=65535, advanced=True,
                   nullable=True),
            _field(f"{prefix}.token_env", "devices", "text",
                   {"zh": f"{name} · 设备令牌变量（只读）", "en": f"{name} · device token variable (read-only)"},
                   _t("设备访问令牌所在的变量名（不是令牌本身）。改完重启音频面。",
                      "Variable holding the device access token (not the token itself). "
                      "Restart the audio plane."),
                   effect="restart", advanced=True),
            _field(f"{prefix}.allow_no_token", "devices", "bool",
                   {"zh": f"{name} · 允许无令牌连接", "en": f"{name} · allow tokenless connections"},
                   _t("允许不带 token 的局域网连接：同网段任何人都能打开麦克风。只在音箱客户端"
                      "改不了 URL 时临时用，平时保持关闭。改完重启音频面。",
                      "Allow tokenless LAN connections: anyone on the LAN could open the "
                      "microphone. Only for clients whose URL cannot be edited, and never "
                      "as a default. Restart the audio plane."),
                   effect="restart", advanced=True),
            _field(f"{prefix}.led.enabled", "devices", "bool",
                   {"zh": f"{name} · 灯环开关", "en": f"{name} · LED ring"},
                   _t("18 灯灯环总开关。关掉 = 音箱灯完全归厂商；打开后我们按状态上色。"
                      "改完重启音频面。",
                      "Master switch for the 18-LED ring. Off = the vendor owns the light; "
                      "on = we paint it per state. Restart the audio plane."),
                   effect="restart"),
            _field(f"{prefix}.led.gain", "devices", "number",
                   {"zh": f"{name} · 灯环亮度倍数", "en": f"{name} · LED gain"},
                   _t("灯环亮度倍数（1.9 是实测值；2.45 是旧部署调的）。太亮会刺眼、太暗看不清。"
                      "改完重启音频面。",
                      "Ring brightness multiplier (1.9 measured here; the old deployment "
                      "used 2.45). Restart the audio plane."),
                   effect="restart", minimum=0.1, maximum=5.0, advanced=True),
            _field(f"{prefix}.led.night_factor", "devices", "number",
                   {"zh": f"{name} · 夜间亮度比例", "en": f"{name} · LED night factor"},
                   _t("夜间时段内的亮度比例（0.05–1.0）。夜里太亮影响睡觉时调小；"
                      "改完重启音频面。",
                      "Brightness ratio during the night window (0.05-1.0). Lower it when "
                      "the ring is too bright at night. Restart the audio plane."),
                   effect="restart", minimum=0.05, maximum=1.0, advanced=True),
            _field(f"{prefix}.led.night_start", "devices", "number",
                   {"zh": f"{name} · 夜间开始", "en": f"{name} · night start"},
                   _t("夜间时段开始小时（0–23）。开始大于结束表示跨午夜；开始=结束会被校验拒绝，"
                      "想常亮就把夜间比例设为 1.0。改完重启音频面。",
                      "Night window start hour (0-23). Start > end crosses midnight; "
                      "start == end is rejected (set the night factor to 1.0 instead). "
                      "Restart the audio plane."),
                   effect="restart", minimum=0, maximum=23, advanced=True,
                   unit=_t("点", "h")),
            _field(f"{prefix}.led.night_end", "devices", "number",
                   {"zh": f"{name} · 夜间结束", "en": f"{name} · night end"},
                   _t("夜间时段结束小时（0–23，不含）。和开始时间一起决定夜间亮度何时生效；"
                      "改完重启音频面。",
                      "Night window end hour (0-23, exclusive), paired with the start. "
                      "Restart the audio plane."),
                   effect="restart", minimum=0, maximum=23, advanced=True,
                   unit=_t("点", "h")),
            _field(f"{prefix}.led.fade_ms", "devices", "number",
                   {"zh": f"{name} · 灯色淡入", "en": f"{name} · LED fade"},
                   _t("切换灯色时的淡入毫秒数（0 = 直接切换）。调大更柔和但状态变化显得慢。"
                      "改完重启音频面。",
                      "Colour cross-fade in ms (0 = instant). Larger is softer but reads as "
                      "slower state changes. Restart the audio plane."),
                   effect="restart", minimum=0, maximum=600, advanced=True,
                   unit=_t("ms", "ms")),
        ])
        fields.extend([
            _field(f"{prefix}.audio.aec.who", "devices", "select",
                   {"zh": f"{name} · AEC 归属", "en": f"{name} · AEC ownership"},
                   _t("这台设备的回声消除由谁做：device = 音箱自己做（RUI 不跑 AEC），"
                      "controller = RUI 做（默认），both = 两边都做（会级联削音），"
                      "none = 都不做（放歌时唤醒会听见自己）。留空 = 继承全局 audio.aec.who。"
                      "改完重启音频面。",
                      "Which side cancels echo for this speaker: device = the speaker does "
                      "(RUI AEC off), controller = RUI does (default), both = cascaded "
                      "(audible damage), none = neither (it hears itself over music). Empty "
                      "= inherit the global audio.aec.who. Restart the audio plane."),
                   effect="restart",
                   options=["", "device", "controller", "both", "none"], nullable=True,
                   advanced=True),
            _field(f"{prefix}.audio.aec.enabled", "devices", "bool",
                   {"zh": f"{name} · RUI 回声消除", "en": f"{name} · RUI AEC"},
                   _t("是否在这台设备的音频面里跑 RUI 的 AEC。AEC 在设备侧（who=device）时应关掉，"
                      "否则两级消音会吃掉人声。这是显式覆盖；要恢复继承全局请改回配置文件。"
                      "改完重启音频面。",
                      "Whether RUI's AEC runs in this speaker's plane. With device-side AEC "
                      "(who=device) turn it off or two cancellers eat the voice. This is an "
                      "explicit override; edit the YAML to inherit again. Restart the plane."),
                   effect="restart", advanced=True),
            _field(f"{prefix}.audio.voice.post_playback_mute_ms", "devices", "number",
                   {"zh": f"{name} · 播放后静音窗口", "en": f"{name} · post-playback mute"},
                   _t("播放排空后再丢弃麦克风多少毫秒（0 = 不丢；设备侧 AEC 建议 2500）。"
                      "播放中的 barge 不受影响；窗口大于等于 followup_delay_ms 时跳过续听门。"
                      "留空 = 继承全局。改完重启音频面。",
                      "Microphone discarded for this many ms after the speaker drains "
                      "(0 = off; device-side AEC wants about 2500). Playback-time barge still "
                      "works; a window >= followup_delay_ms skips the follow-up gate. Empty "
                      "= inherit the global value. Restart the plane."),
                   effect="restart", minimum=0, maximum=10000, nullable=True,
                   unit=_t("ms", "ms")),

            _field(f"{prefix}.audio.voice.ack_echo_ignore_ms", "devices", "number",
                   {"zh": f"{name} · 唤醒应答静音窗口", "en": f"{name} · ack echo window"},
                   _t("设备自己播完「在呢」后，麦克风忽略多少毫秒以躲开自己的回声尾"
                      "（0 = 不忽略；设备侧 AEC 的机型建议 2500；上限 5000）。留空 = 继承全局 "
                      "ack.echo_ignore_ms。窗口太小会把自己的应答当成请求（0.5.3 的假回合），"
                      "太大会吞掉紧接着说的第一个字。改完重启音频面。",
                      "How long this speaker's microphone ignores its own spoken cue after "
                      "it plays (0 = no window; a device-side AEC model wants about 2500; max "
                      "5000). Empty = inherit the global ack.echo_ignore_ms. Too small and it "
                      "hears its own cue as a request (the 0.5.3 false turn); too large and it "
                      "eats the first word after the cue. Restart the audio plane."),
                   effect="restart", minimum=0, maximum=5000, nullable=True,
                   unit=_t("ms", "ms")),
            _field(f"{prefix}.audio.voice.followup_delay_ms", "devices", "number",
                   {"zh": f"{name} · 续听窗口", "en": f"{name} · follow-up window"},
                   _t("回答结束后仍可无唤醒词续听的毫秒数（留空 = 继承全局）。回声拖尾长的设备"
                      "要等更久；改完重启音频面。",
                      "How long the room keeps listening after an answer without the wake word "
                      "(empty = inherit the global value). A speaker with a long echo tail "
                      "needs longer. Restart the plane."),
                   effect="restart", minimum=0, maximum=10000, nullable=True,
                   unit=_t("ms", "ms"), advanced=True),
            _field(f"{prefix}.audio.voice.followup_replay", "devices", "bool",
                   {"zh": f"{name} · 续听回放", "en": f"{name} · follow-up replay"},
                   _t("续听时是否把这句开头之前捕获的音频补进语音段（显式覆盖；关掉会吞掉"
                      "开口的第一个字）。改完重启音频面。",
                      "Replay the audio captured just before the follow-up speech into the "
                      "segment (explicit override; off eats the first syllable). Restart the "
                      "audio plane."),
                   effect="restart", advanced=True),
            _field(f"{prefix}.audio.playback.prebuffer_ms", "devices", "number",
                   {"zh": f"{name} · 播放预缓冲", "en": f"{name} · playback prebuffer"},
                   _t("给这台设备的首包预缓冲毫秒数（留空 = 继承全局；转发/批处理型 adapter "
                      "需要更大的值）。改完重启音频面。",
                      "Prebuffer before the first block for this speaker (empty = inherit the "
                      "global value; a batching adapter needs more). Restart the plane."),
                   effect="restart", minimum=0, maximum=1000, nullable=True,
                   unit=_t("ms", "ms"), advanced=True),
            _field(f"{prefix}.audio.capture.rate", "devices", "number",
                   {"zh": f"{name} · 采集采样率", "en": f"{name} · capture rate"},
                   _t("这台设备送来的采样率（留空 = 继承全局 audio.capture.rate）。换机型才需要"
                      "改，填错会变调或没声音。改完重启音频面。",
                      "Capture sample rate this speaker sends (empty = inherit). Only a "
                      "different model needs this; a wrong value pitch-shifts or silences "
                      "the input. Restart the plane."),
                   effect="restart", minimum=8000, maximum=96000, nullable=True,
                   unit=_t("Hz", "Hz"), advanced=True),
            _field(f"{prefix}.audio.capture.channels", "devices", "number",
                   {"zh": f"{name} · 采集通道数", "en": f"{name} · capture channels"},
                   _t("采集总通道数（留空 = 继承全局）。必须等于 mic_channels + ref_channels，"
                      "否则合并后的配置校验会拒绝。改完重启音频面。",
                      "Total capture channels (empty = inherit). It must equal mic_channels "
                      "+ ref_channels or the merged config is rejected. Restart the plane."),
                   effect="restart", minimum=1, maximum=16, nullable=True, advanced=True),
            _field(f"{prefix}.audio.capture.mic_channels", "devices", "number",
                   {"zh": f"{name} · 麦克风通道", "en": f"{name} · mic channels"},
                   _t("前几个通道是麦克风（留空 = 继承全局；参考机型有 6 路）。改完重启音频面。",
                      "Leading channels that are microphones (empty = inherit; the reference model has 6). "
                      "Restart the plane."),
                   effect="restart", minimum=1, maximum=16, nullable=True, advanced=True),
            _field(f"{prefix}.audio.capture.ref_channels", "devices", "number",
                   {"zh": f"{name} · 参考通道", "en": f"{name} · reference channels"},
                   _t("末尾几个通道是播放参考（留空 = 继承全局；0 = 设备侧 AEC，没有参考）。"
                      "改错会让 AEC 消掉人声。改完重启音频面。",
                      "Trailing channels that are the playback reference (empty = inherit; "
                      "0 = device-side AEC, no reference). A wrong value makes AEC cancel the "
                      "voice. Restart the plane."),
                   effect="restart", minimum=0, maximum=16, nullable=True, advanced=True),
            _field(f"{prefix}.audio.capture.bits", "devices", "select",
                   {"zh": f"{name} · 采样位深", "en": f"{name} · capture bits"},
                   _t("采集样本的位深（16 或 32；留空 = 继承全局；参考机型为 32 bit）。改完重启音频面。",
                      "Capture sample width (16 or 32; empty = inherit; the reference model is 32 bit). Restart "
                      "the plane."),
                   effect="restart", options=[16, 32], nullable=True, advanced=True),
            _field(f"{prefix}.audio.capture.mic_shift", "devices", "number",
                   {"zh": f"{name} · 麦移位", "en": f"{name} · mic shift"},
                   _t("把麦克风样本左移多少位到 32 bit（留空 = 继承全局；参考机型为 12）。"
                      "填错会削顶或声音很小。改完重启音频面。",
                      "Left shift applied to mic samples into 32-bit (empty = inherit; the reference "
                      "model is 12). A wrong value clips or goes quiet. Restart the plane."),
                   effect="restart", minimum=0, maximum=31, nullable=True, advanced=True),
            _field(f"{prefix}.audio.capture.ref_shift", "devices", "number",
                   {"zh": f"{name} · 参考移位", "en": f"{name} · reference shift"},
                   _t("把参考样本左移多少位到 32 bit（留空 = 继承全局；参考机型为 16）。"
                      "填错会让 AEC 的参考比例不对。改完重启音频面。",
                      "Left shift applied to reference samples into 32-bit (empty = inherit; "
                      "the reference model is 16). A wrong value mis-scales the AEC reference. Restart."),
                   effect="restart", minimum=0, maximum=31, nullable=True, advanced=True),
        ])
        for state in LED_STATES:
            fields.append(_field(
                f"{prefix}.led.colors.{state}", "devices", "text",
                {"zh": f"{name} · {state} 颜色", "en": f"{name} · {state} colour"},
                _t(f"「{state}」状态的灯色（#RRGGBB 六位十六进制）。填错会被校验拒绝；"
                   "改完重启音频面。",
                   f"The ring colour for the {state} state (#RRGGBB). A malformed value is "
                   "rejected; restart the audio plane."),
                effect="restart", advanced=True))
    return fields


def _multi_device_fields(cfg: Config) -> list[dict]:
    """The multi_device block: one arbiter, one turn, one output owner (K1-K6).

    Only knobs the arbiter actually reads are writable. arbitration.mode,
    prefer_playing_room and on_no_speech are frozen (score / true / end) and have no
    console input on purpose: a knob that changes nothing is the "fake knob" failure this
    repo keeps paying for.
    """
    device_ids = [device.id for device in cfg.devices]
    output_options = [{
        "value": "",
        "label": _t("（未设置：回落最近活跃房间）", "(unset: most recently active room)"),
    }]
    output_options += [
        {"value": device.id,
         "label": {"zh": f"{device.id} · {device.room or '（无房间）'}",
                   "en": f"{device.id} · {device.room or '(no room)'}"}}
        for device in cfg.devices
    ]
    return [
        _field("multi_device.enabled", "multi_device", "bool",
               _t("启用多设备", "Multi-device"),
               _t("为每台 enabled 的入站音箱各起一个音频面，唤醒由控制面统一仲裁：全屋同时刻"
                  "只有一个回合、一个输出 owner。关闭 = 0.4.x 单平面行为（回滚路径）。"
                  "开启时每台设备都必须填房间。改完重启服务。",
                  "Starts one audio plane per enabled inbound speaker and arbitrates wakes in "
                  "the control plane: one turn and one output owner for the house. Off = the "
                  "0.4.x single-plane behaviour (rollback path). Every device needs a room "
                  "when this is on. Restart the service."),
               effect="restart"),
        _field("multi_device.max_planes", "multi_device", "number",
               _t("音频面上限", "Max audio planes"),
               _t("最多同时运行几个音频面（1–8，默认 4）。超出的设备不会启动、也不排队，"
                  "/health 会点名被拒的设备；每多一台就多一份 KWS/VAD/AEC 的 CPU 与内存。"
                  "改完重启服务。",
                  "How many planes may run at once (1-8, default 4). Extra devices do not "
                  "start and are not queued; /health names them. Each plane adds a "
                  "KWS/VAD/AEC CPU and memory footprint. Restart the service."),
               effect="restart", minimum=1, maximum=8),
        _field("multi_device.default_output", "multi_device", "select",
               _t("默认输出房间", "Default output"),
               _t("没有来源房间的提示音/提醒/通知走这里（K4）；留空 = 回落最近活跃房间。"
                  "它只影响无来源输出，不参与唤醒归属。改完重启服务。",
                  "Where reminders and notifications with no source room go (K4); empty = "
                  "the most recently active room. It never decides wake ownership. Restart "
                  "the service."),
               effect="restart", options=output_options, nullable=True),
        _field("multi_device.arbitration.source_priority", "multi_device", "list",
               _t("唤醒来源优先级", "Wake source priority"),
               _t("不同来源之间只比来源组顺序、不比分数（设备侧唤醒与 RUI KWS 的分数不可比）："
                  "默认设备侧唤醒优先。列表不能为空、不能重复。改完重启服务。",
                  "Sources are ordered by group, never by score (a device OWW score and RUI "
                  "KWS are not comparable): the device-side wake wins by default. The list "
                  "must be non-empty and duplicate-free. Restart the service."),
               effect="restart", options=["device_wake", "kws"]),
        _field("multi_device.arbitration.candidate_merge_window_ms", "multi_device",
               "number",
               _t("候选合并窗口", "Candidate merge window"),
               _t("同一句话被多台设备听到时，控制面等这么久再裁决（100–2000 ms，默认 400）。"
                  "太小会漏掉稍慢的那台；太大会让唤醒→应答变慢。改完重启服务。",
                  "How long the control plane collects candidates for the same utterance "
                  "before deciding (100-2000 ms, default 400). Too small misses a slower "
                  "speaker; too large delays the answer. Restart the service."),
               effect="restart", minimum=100, maximum=2000, unit=_t("ms", "ms")),
        _field("multi_device.arbitration.duplicate_window_ms", "multi_device", "number",
               _t("同词去重窗口", "Duplicate window"),
               _t("同一唤醒词在这个窗口内再出现算重复、不再开新回合（50–10000 ms，默认 2000）。"
                  "与合并窗口是两个不同的东西。改完重启服务。",
                  "The same wake word inside this window is a duplicate, not a new turn "
                  "(50-10000 ms, default 2000). This is a different window from the merge "
                  "window. Restart the service."),
               effect="restart", minimum=50, maximum=10000, unit=_t("ms", "ms")),
        _field("multi_device.arbitration.room_priority", "multi_device", "list",
               _t("房间优先级", "Room priority"),
               _t("显式优先的房间，按设备 id 排序（排在前面的先赢）。只在有多个候选时用作第一步；"
                  "不填就按来源/分数/最近活跃/配置顺序。改完重启服务。",
                  "Explicit room priority as device ids (earlier wins). It is only the first "
                  "tie-break when several candidates exist; empty falls back to source, score, "
                  "recent activity and config order. Restart the service."),
               effect="restart", options=device_ids),
        _field("multi_device.arbitration.last_active_window_s", "multi_device", "number",
               _t("最近活跃窗口", "Last-active window"),
               _t("平票时「最近说过话的房间」有效多久（1–3600 s，默认 120）。超出窗口就不算"
                  "活跃信号，继续按配置顺序。改完重启服务。",
                  "How long a room counts as recently active when candidates tie (1-3600 s, "
                  "default 120). Outside the window it carries no signal and config order "
                  "decides. Restart the service."),
               effect="restart", minimum=1, maximum=3600, unit=_t("秒", "s")),
    ]


def field_specs(cfg: Config) -> list[dict]:
    """Every directory entry for this deployment, in panel order."""
    fields = [
        *_assistant_fields(),
        *_session_fields(),
        *_foundation_fields(),
        *_security_fields(),
        *_audio_fields(),
        *_device_fields(cfg),
        *_multi_device_fields(cfg),
        *_provider_fields(cfg),
        *_tool_fields(cfg),
        *_music_fields(),
    ]
    # Duplicate paths would make the dotted writer ambiguous; fail here, not at save time.
    seen: set[str] = set()
    for field in fields:
        if field["path"] in seen:
            raise ValueError(f"duplicate catalog path: {field['path']}")
        seen.add(field["path"])
    fields.sort(key=lambda f: (_GROUPS[f["group"]][1], _GROUPS[f["group"]][2], f["path"]))
    return fields


KEYWORD_DEFAULTS = {"threshold": 0.15, "boost": 2.00}

# The music credentials file, relative to data_dir when the config's path is empty
# (schema default). `PUT/GET /api/v1/music/credentials` writes/reads this file.
MUSIC_CREDENTIALS_FILE = "lan_media.json"


def keyword_file(cfg: Config) -> Path | None:
    """Where the wake keywords (and the real threshold) live, if the config names one."""
    voice = cfg.audio.voice
    if voice.kws_keywords_file:
        return Path(voice.kws_keywords_file).expanduser()
    if voice.kws_model_dir:
        return Path(voice.kws_model_dir).expanduser() / "keywords.txt"
    return None


def keyword_settings(cfg: Config) -> dict[str, Any]:
    """The gate values that actually run, read from keywords.txt (defaults if absent)."""
    path = keyword_file(cfg)
    out: dict[str, Any] = {
        "file": str(path) if path is not None else "",
        "exists": bool(path is not None and path.is_file()),
        "threshold": KEYWORD_DEFAULTS["threshold"],
        "boost": KEYWORD_DEFAULTS["boost"],
        "keywords": 0,
    }
    if path is not None and path.is_file():
        try:
            from ..audio.kws import keyword_meta  # lazy: config must not import audio at load

            meta = keyword_meta(path)
        except Exception:  # noqa: BLE001 - a broken file means "defaults", never a 500
            return out
        if meta.get("threshold") is not None:
            out["threshold"] = float(meta["threshold"])  # type: ignore[arg-type]
        if meta.get("boost") is not None:
            out["boost"] = float(meta["boost"])  # type: ignore[arg-type]
        out["keywords"] = int(meta.get("keywords") or 0)
    return out


def music_credentials_path(cfg: Config) -> Path:
    """Where the music source credentials live; never part of the config file."""
    if cfg.music.secrets_path.strip():
        return Path(cfg.music.secrets_path).expanduser()
    return Path(cfg.system.data_dir).expanduser() / MUSIC_CREDENTIALS_FILE


def _read_music_credentials_file(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v not in (None, "")}


def music_credentials(cfg: Config) -> dict[str, str]:
    """The stored credentials, or an empty dict. Never logged, never returned by the API.

    The file layout is flat and keyed by the active preset's credential slots
    (`url/user/password/token` today). A legacy config that still carries
    `music.url/user/password` is merged in without writing anything; see
    `migrate_legacy_music_credentials` for the one-time move to the 0600 file.
    """
    data = _read_music_credentials_file(music_credentials_path(cfg))
    for key, value in cfg.music.legacy_credentials().items():
        if not data.get(key):
            data[key] = value
    return data


class MusicCredentialsWriteError(OSError):
    """The music credentials file cannot be written (permissions / bad parent).

    Subclasses OSError so the startup migration's existing ``except OSError`` keeps
    its behaviour (log and keep the inline values). The API layer turns it into a 409
    with this same actionable sentence instead of a 500 traceback.
    """

    def __init__(self, path: Path, error: OSError) -> None:
        self.path = Path(path)
        self.error = error
        reason = getattr(error, "strerror", None) or str(error) or type(error).__name__
        super().__init__(
            f"music credentials file is not writable: {self.path} ({reason}); "
            "fix the directory permissions or set music.secrets_path to a writable "
            "location (for example under system.data_dir)"
        )


def write_music_credentials(path: Path, data: dict[str, str]) -> None:
    """Atomic 0600 write of the music credentials JSON (never into config.yaml).

    A path that cannot be written raises `MusicCredentialsWriteError` (an OSError)
    carrying the path, the OS reason and the `music.secrets_path` fix, so the console
    answers 4xx with an actionable message instead of a 500.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.chmod(tmp, 0o600)      # chmod before the first byte, umask-proof
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise MusicCredentialsWriteError(path, exc) from exc


def migrate_legacy_music_credentials(cfg: Config) -> bool:
    """Move inline `music.url/user/password/token` into the write-only JSON.

    Returns True when legacy values were present (whether or not the file changed).
    Raises OSError when the file cannot be written; the caller logs it and leaves the
    in-memory values alone rather than losing the only copy. On success the inline
    fields are cleared, so the next `save_config` writes a file with no plaintext.
    """
    legacy = cfg.music.legacy_credentials()
    if not legacy:
        return False
    path = music_credentials_path(cfg)
    stored = _read_music_credentials_file(path)
    changed = False
    for key, value in legacy.items():
        if not stored.get(key):
            stored[key] = value
            changed = True
    if changed:
        write_music_credentials(path, stored)
    for key in ("url", "user", "password", "token"):
        setattr(cfg.music, key, "")
    # A config that relied on inline credentials had no secrets_path; after the move
    # it must point at the file, or the next load rejects `enabled` with no path.
    if not cfg.music.secrets_path.strip():
        cfg.music.secrets_path = str(path)
    return True


def music_credentials_state(cfg: Config) -> dict[str, Any]:
    """Presence-only view for the console: never a credential value."""
    from ..music.sources.presets import get_preset, missing_credentials

    preset = get_preset(cfg.music.source)
    data = music_credentials(cfg)
    if preset is None:
        missing = ("url", "user", "password")
        keys = ("url", "user", "password", "token")
    else:
        missing = missing_credentials(preset, data)
        keys = tuple(item.key for item in preset.credential_fields)
    return {
        "present": music_credentials_path(cfg).is_file(),
        "ready": not missing,
        "missing": list(missing),
        "fields": {key: bool(str(data.get(key) or "").strip()) for key in keys},
    }


def has_music_credentials(cfg: Config) -> bool:
    return bool(music_credentials_state(cfg)["ready"])


# Credential categories the console can reason about without guessing from a name.
# `provider` carries the stage/provider on its consumer; the rest name the one
# subsystem that reads the value.
SECRET_CATEGORIES = ("provider", "search", "music", "ha", "device", "console", "other")


def _secret_consumer(kind: str, ident: str, zh: str, en: str, *,
                     href: str = "", **fields: str) -> dict:
    """One typed reader of a credential - the replacement for front-end regexes.

    `kind` is one of `SECRET_CATEGORIES` (provider/search/music/ha/device/console);
    consumers with a stage/provider/device also carry those ids so a page can filter
    without parsing a display string.
    """
    consumer = {"id": ident, "kind": kind, "category": kind, "label": _t(zh, en)}
    if href:
        consumer["href"] = href
    for key, value in fields.items():
        if value:
            consumer[key] = value
    return consumer


def _secret_entry(entries: dict[str, dict], name: str, consumer: dict,
                  zh: str, en: str, *, writable: bool = True) -> None:
    name = (name or "").strip()
    if not name:
        return
    entry = entries.setdefault(name, {
        "has_value": False,
        "used_by": [],
        "consumers": [],
        "hint": _t(zh, en),
        # A value stored outside `secrets.yaml` (music) is still listed, but the page
        # must link to its writer instead of offering an env-var input that cannot work.
        "writable": writable,
    })
    if consumer["id"] not in entry["used_by"]:
        entry["used_by"].append(consumer["id"])
    if all(item["id"] != consumer["id"] for item in entry["consumers"]):
        entry["consumers"].append(consumer)


def secret_names(cfg: Config) -> list[str]:
    """Every secret name the configured chains/devices actually use."""
    return sorted(secret_presence(cfg))


def secret_presence(cfg: Config) -> dict[str, dict]:
    """`{name: {has_value, category, consumers, used_by, hint, writable}}`.

    Derived from the *configured chains* (not a hard-coded dev list), so the production
    template's `RUI_*` names and a dev config's `DEEPSEEK_API_KEY` / `XFYUN_*`
    both render. A key that is configured in the environment but not referenced by any
    chain is deliberately absent: filling it would do nothing, and showing it would
    promise an effect it cannot have.

    `category` + `consumers[]` are typed so the console never guesses from a name:
    provider consumers carry `stage`/`provider`, device consumers carry `device`, and
    every consumer names the page that writes the value (`href`). Music credentials
    live in their own 0600 file, so that row is `writable=false` and links to the music
    page instead of pretending `POST /api/v1/secrets` can set it.
    """
    entries: dict[str, dict] = {}
    for stage in ("asr", "tts", "llm"):
        for provider_name in getattr(cfg.providers, stage).chain:
            provider = cfg.providers.catalog.get(provider_name)
            if provider is None:  # a broken chain is reported by schema validation
                continue
            for attr, what_zh, what_en in (
                ("api_key_env", "API key", "API key"),
                ("app_id_env", "App ID", "App ID"),
                ("api_secret_env", "API Secret", "API secret"),
            ):
                _secret_entry(
                    entries, str(getattr(provider, attr, "") or ""),
                    _secret_consumer(
                        "provider", f"{stage}:{provider_name}",
                        f"{provider_name}（{stage.upper()} 链）",
                        f"{provider_name} ({stage} chain)",
                        href="/panel/providers", stage=stage, provider=provider_name,
                    ),
                    f"{provider_name}（{stage.upper()} 链）的 {what_zh}；值在「凭据（只写）」页填。",
                    f"{what_en} for {provider_name} ({stage} chain); enter it on the "
                    "Credentials page.",
                )
    _secret_entry(
        entries, cfg.tools.search.api_key_env,
        _secret_consumer("search", "tool:search", "联网搜索", "Web search",
                         href="/panel/integrations"),
        "联网搜索（tools.search）的 key；值在「凭据（只写）」页填。",
        "Key for web search (tools.search); enter it on the Credentials page.",
    )
    _secret_entry(
        entries, cfg.tools.ha.token_env,
        _secret_consumer("ha", "tool:ha", "Home Assistant", "Home Assistant",
                         href="/panel/integrations"),
        "Home Assistant 长期令牌（经 MCP）；值在「凭据（只写）」页或 «工具与集成» 的 MCP 卡片填。",
        "Home Assistant long-lived token (MCP); enter it on the Credentials page or the "
        "MCP card under Tools & integrations.",
    )
    for device in cfg.devices:
        _secret_entry(
            entries, device.token_env,
            _secret_consumer("device", f"device:{device.id}",
                             f"音箱 {device.id}", f"Speaker {device.id}",
                             href="/panel/devices", device=device.id),
            f"音箱 {device.id} 的入站 token；音箱客户端用 ?token= 拨入时校验。"
            "留空会拒绝连接（除非 allow_no_token）。",
            f"Inbound token for speaker {device.id}; the client's ?token= is checked "
            "against it. Empty refuses the connection (unless allow_no_token).",
        )
    _secret_entry(
        entries, "web.auth_token",
        _secret_consumer("console", "console:auth", "控制台", "Console",
                         href="/panel/system"),
        "控制台访问令牌；空 = 局域网无鉴权（只建议在可信网络）。写入走 "
        "POST /api/v1/secrets（name=web.auth_token），永不回显。",
        "Console access token; empty = no auth on the LAN (trusted networks only). "
        "Written via POST /api/v1/secrets (name=web.auth_token); never echoed.",
    )
    # Music is a consumer too, but its credentials are not env vars. Listing the row
    # with `writable=false` keeps `/secrets` honest (and links to the real writer)
    # without folding lan_media.json into secrets.yaml.
    music_state = music_credentials_state(cfg)
    entries["music.credentials"] = {
        "has_value": bool(music_state["ready"]),
        "category": "music",
        "used_by": ["music:source"],
        "consumers": [_secret_consumer(
            "music", "music:source", "音乐音源", "Music source",
            href="/panel/music", source=cfg.music.source)],
        "hint": _t(
            "音乐音源的凭据存在独立的 0600 文件里；在 «音乐 → 音源与凭据» 页填写，永不回显。",
            "Music source credentials live in their own 0600 file; enter them on the "
            "Music → Source & credentials page. Never echoed.",
        ),
        "writable": False,
    }
    for name, entry in entries.items():
        if name == "web.auth_token":
            entry["has_value"] = bool(cfg.web.auth_token)
        elif name == "music.credentials":
            entry["has_value"] = bool(music_state["ready"])
        else:
            entry["has_value"] = bool(env_get(name)) or secrets.has(name)
        entry["used_by"].sort()
        consumers = entry.get("consumers") or []
        entry["category"] = str(consumers[0]["kind"]) if consumers else "other"
        if "href" not in entry and consumers and consumers[0].get("href"):
            entry["href"] = consumers[0]["href"]
        entry.setdefault("writable", True)
    return dict(sorted(entries.items()))


def values(cfg: Config) -> dict[str, Any]:
    """Current value for every non-secret catalog path, from the redacted config."""
    data = redacted(cfg)
    out: dict[str, Any] = {}
    for field in field_specs(cfg):
        try:
            out[field["path"]] = get_path(data, field["path"])
        except (KeyError, IndexError):
            # An unset nullable override has no key on the dumped config (a device with no
            # audio block, for example). Show it empty instead of omitting the field, or
            # the console cannot edit it back from inherit. Virtual fields (keywords) are
            # filled below and overwrite this placeholder.
            out[field["path"]] = None
    settings = keyword_settings(cfg)
    out["audio.voice.keywords.threshold"] = settings["threshold"]
    out["audio.voice.keywords.boost"] = settings["boost"]
    out["music.has_credentials"] = has_music_credentials(cfg)
    return out


def document(cfg: Config) -> dict:
    """The `GET /api/v1/config/catalog` payload (docs/config-catalog-contract.md §1)."""
    return {
        "tiers": [dict(tier) for tier in TIERS],
        "groups": group_specs(),
        "fields": field_specs(cfg),
        "values": values(cfg),
        "secrets": secret_presence(cfg),
        "restart_command": RESTART_COMMAND,
        "writable_root": list(WRITABLE_ROOT),
    }
