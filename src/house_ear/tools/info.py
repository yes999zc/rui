"""The three information tools a voice assistant needs on day one.

Weather and news are keyless; search needs a key, which is read through
`house_ear.secrets` (environment first, then the write-only file), never from the config.

Every handler returns a string that goes back to the model verbatim, so the wording is
part of the contract: a failure says "查不到，直接说查不到" instead of an English exception.
The old build returned `工具出错：<urlopen error timed out>`, and a model that reads that
tends to answer the question anyway.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any

import httpx

from .. import secrets
from ..config.schema import Config
from .contract import Permission, ToolSpec

log = logging.getLogger(__name__)

# WMO weather codes, the subset open-meteo actually uses for daily values.
_WMO = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "小毛雨", 53: "毛雨", 55: "大毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨", 71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "强阵雨", 82: "暴雨", 85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}

DIGEST_URLS = (
    "https://60s-static.viki.moe/60s/{date}.json",
    "https://60s-static-host.vercel.app/60s/{date}.json",
    "https://raw.githubusercontent.com/vikiboss/60s-static-host/main/static/60s/{date}.json",
    "https://cdn.jsdelivr.net/gh/vikiboss/60s-static-host@main/static/60s/{date}.json",
)

WEATHER_SPEC = ToolSpec(
    name="local.weather",
    description="查天气。问天气、要不要带伞、穿什么、周末天气时用。不填城市就用配置里的默认城市。",
    input_schema={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名，例如 沈阳、上海"},
            "days": {"type": "integer", "description": "1=今天，最多 3 天，默认 1"},
        },
    },
    permission=Permission.READ,
)

NEWS_SPEC = ToolSpec(
    name="local.news",
    description="念今天的新闻摘要（每天更新的公开日报），问新闻、今天发生了什么、有什么大事时用。",
    input_schema={
        "type": "object",
        "properties": {
            "count": {"type": "integer", "description": "要几条，默认 5，最多 10"},
        },
    },
    permission=Permission.READ,
)

SEARCH_SPEC = ToolSpec(
    name="local.web_search",
    description="联网搜索。问实时信息、价格、比分、某个具体事实、你不知道的事时用它查，而不是凭印象回答。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索词，越具体越好"},
            "count": {"type": "integer", "description": "结果条数，最多 6 条，默认 6"},
        },
        "required": ["query"],
    },
    permission=Permission.READ,
)


def _today() -> dt.date:
    return dt.datetime.now().astimezone().date()


async def _get_json(url: str, timeout: float, params: dict | None = None) -> Any:
    """One GET, retried once on a transport blip.

    open-meteo fails intermittently from this network with an exception whose
    str() is empty (measured 09:17); a second attempt a moment later succeeds.
    The retry is cheap and only for transport errors - a 4xx is a real answer.
    """
    last: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url, params=params,
                                        headers={"User-Agent": "house-ear/0.1"})
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError:
            raise
        except (httpx.TransportError, httpx.HTTPError, OSError) as exc:
            last = exc
            if attempt == 0:
                await asyncio.sleep(0.4)
    raise last if last is not None else RuntimeError("no attempt ran")


async def _geocode(city: str, timeout: float) -> tuple[float, float, str] | None:
    data = await _get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        timeout,
        {"name": city, "count": 1, "language": "zh", "format": "json"},
    )
    results = (data or {}).get("results") or []
    if not results:
        return None
    first = results[0]
    return float(first["latitude"]), float(first["longitude"]), first.get("name") or city


_FORECAST_TTL_S = 300.0
_FORECAST_CACHE: dict[tuple, tuple[float, str]] = {}


async def weather(cfg: Config, args: dict) -> str:
    """Today's (or a few days') forecast from open-meteo. No key, no quota."""
    conf = cfg.tools.weather
    city = str(args.get("city") or conf.city or "").strip()
    days = max(1, min(int(conf.days_max), int(args.get("days") or 1)))
    lat, lon = conf.latitude, conf.longitude
    place = city or "这里"
    if lat is None or lon is None:
        if not city:
            return "没给城市：直接问用户是哪个城市，不要猜。"
        try:
            found = await _geocode(city, conf.timeout_s)
        except Exception as exc:  # noqa: BLE001 - callers get words, not tracebacks
            log.warning("weather: geocode failed: %s", exc)
            return "天气没查到（城市没解析出来），直接说查不到，让用户稍后再问。"
        if found is None:
            return f"没有找到城市「{city}」，直接说没查到这个地方。"
        lat, lon, place = found
    key = (round(lat, 3), round(lon, 3), days)
    hit = _FORECAST_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _FORECAST_TTL_S:
        return hit[1]
    try:
        data = await _get_json(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            f"&timezone=auto&forecast_days={days}",
            conf.timeout_s,
        )
    except Exception as exc:  # noqa: BLE001
        # Include the type: transport errors here can have an empty message.
        log.warning("weather: forecast failed: %s: %r", type(exc).__name__, exc)
        return "天气没查到，直接说查不到，让用户稍后再问。"
    daily = (data or {}).get("daily") or {}
    dates = daily.get("time") or []
    if not dates:
        return "天气没查到，直接说查不到。"
    lines = []
    for i, day in enumerate(dates[:days]):
        code = int((daily.get("weather_code") or [0])[i] or 0)
        hi = (daily.get("temperature_2m_max") or [None])[i]
        lo = (daily.get("temperature_2m_min") or [None])[i]
        rain = (daily.get("precipitation_probability_max") or [None])[i]
        when = "今天" if i == 0 else ("明天" if i == 1 else f"{day[5:]}")
        bits = [f"{when}{_WMO.get(code, '未知天气')}"]
        if hi is not None and lo is not None:
            bits.append(f"{round(lo)}到{round(hi)}度")
        if rain is not None:
            bits.append(f"降水概率{round(rain)}%")
        lines.append("，".join(bits))
    answer = f"{place}：" + "；".join(lines) + "。"
    _FORECAST_CACHE[key] = (time.monotonic(), answer)
    return answer


async def news(cfg: Config, args: dict) -> str:
    conf = cfg.tools.news
    count = max(1, min(10, int(args.get("count") or 5)))
    today = _today()
    for offset in (0, 1):  # today, then yesterday: the mirrors publish on their own clock
        date = (today - dt.timedelta(days=offset)).isoformat()
        for template in DIGEST_URLS:
            try:
                data = await _get_json(template.format(date=date), conf.timeout_s)
            except Exception:  # noqa: BLE001 - the next mirror is the fix
                continue
            items = [str(x).strip() for x in (data or {}).get("news") or [] if str(x).strip()]
            if not items:
                continue
            picked = items[:count]
            when = "今天" if offset == 0 else "昨天"
            head = f"{when}的新闻（{date}）："
            return head + "；".join(f"{i}. {t}" for i, t in enumerate(picked, 1))[:900]
    return "今天的新闻没取到，直接说没取到，让用户过一会儿再问。"


async def web_search(cfg: Config, args: dict) -> str:
    conf = cfg.tools.search
    if not conf.enabled or conf.kind == "none":
        return "搜索没有开启，直接说查不了。"
    query = str(args.get("query") or "").strip()
    if not query:
        return "没给搜索词：直接问用户要查什么。"
    key = secrets.get(conf.api_key_env)
    if not key:
        return f"搜索没有配置密钥（{conf.api_key_env}），直接说查不了。"
    count = max(1, min(conf.count, int(args.get("count") or conf.count)))
    payload = {
        "query": query[:128],
        "count": count,
        "ori_question": query[:128],
        "need_content": False,
    }
    try:
        async with httpx.AsyncClient(timeout=conf.timeout_s) as client:
            resp = await client.post(
                conf.base_url.rstrip("/") + "/v1/tools/search",
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - a failed search is a sentence, not a crash
        log.warning("search: %s", exc)
        return "搜索没查成（网络或服务不可用），直接说没查到，不要凭印象回答。"
    results = (data or {}).get("results") or []
    if not results:
        return f"没有搜到「{query}」的结果，直接说没查到。"
    lines = []
    for item in results[:6]:
        summary = (item.get("summary") or item.get("snippet") or "").replace("\n", " ")
        site = item.get("site_name") or ""
        lines.append(f"{item.get('title', '')}（{site}）：{summary[:450]}")
    return ("搜索结果：" + "｜".join(lines))[:3000]


def build_tools(cfg: Config) -> list[tuple[ToolSpec, Any]]:
    """The enabled information tools, in the order the model should see them."""
    out: list[tuple[ToolSpec, Any]] = [(WEATHER_SPEC, lambda a: weather(cfg, a))]
    if cfg.tools.news.enabled:
        out.append((NEWS_SPEC, lambda a: news(cfg, a)))
    if cfg.tools.search.enabled and cfg.tools.search.kind != "none":
        out.append((SEARCH_SPEC, lambda a: web_search(cfg, a)))
    return out
