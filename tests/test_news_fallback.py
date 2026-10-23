"""The daily digest must survive its mirrors being down (measured 2026-10-23 13:25).

All four 60s mirrors were unreachable for about a minute and the assistant answered
「新闻没取到」. A digest cached from the last successful fetch is a better answer
than an apology - and the whole probe is bounded so the tool call cannot hit the
20 s turn timeout while trying eight URLs.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest

from house_ear.config import Config
from house_ear.tools import info


@pytest.fixture()
def cfg(tmp_path):
    conf = Config()
    conf.system.data_dir = str(tmp_path / "data")
    return conf


def test_success_writes_the_cache(cfg, tmp_path, monkeypatch) -> None:
    today = dt.date.today().isoformat()

    async def fake_get(url, timeout, params=None):
        return {"news": ["A 事件", "B 事件"], "date": today}

    monkeypatch.setattr(info, "_get_json", fake_get)
    brief = asyncio.run(info.news(cfg, {"count": 2}))
    assert brief is not None and brief.count == 2
    cache = tmp_path / "data" / info.NEWS_CACHE_FILE
    assert cache.exists()
    assert json.loads(cache.read_text(encoding="utf-8"))["date"] == today


def test_mirrors_down_serves_the_cached_digest(cfg, tmp_path, monkeypatch) -> None:
    today = dt.date.today().isoformat()
    cache = tmp_path / "data" / info.NEWS_CACHE_FILE
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"date": today, "data": {"news": ["缓存新闻一", "缓存新闻二"]}},
                                ensure_ascii=False), encoding="utf-8")

    async def down(url, timeout, params=None):
        raise OSError("network unreachable")

    monkeypatch.setattr(info, "_get_json", down)
    brief = asyncio.run(info.news(cfg, {"count": 2}))
    assert brief is not None
    assert "缓存新闻一" in brief.speech_text


def test_no_cache_and_down_still_answers_honestly(cfg, monkeypatch) -> None:
    async def down(url, timeout, params=None):
        raise OSError("network unreachable")

    monkeypatch.setattr(info, "_get_json", down)
    answer = asyncio.run(info.news(cfg, {"count": 2}))
    assert isinstance(answer, str)
    assert "没取到" in answer


def test_a_stale_cache_is_not_read(cfg, tmp_path, monkeypatch) -> None:
    old = (dt.date.today() - dt.timedelta(days=5)).isoformat()
    cache = tmp_path / "data" / info.NEWS_CACHE_FILE
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"date": old, "data": {"news": ["旧闻"]}},
                                ensure_ascii=False), encoding="utf-8")

    async def down(url, timeout, params=None):
        raise OSError("network unreachable")

    monkeypatch.setattr(info, "_get_json", down)
    answer = asyncio.run(info.news(cfg, {"count": 2}))
    assert isinstance(answer, str)
    assert "旧闻" not in answer


def test_search_backs_the_digest_when_mirrors_and_cache_are_down(cfg, monkeypatch) -> None:
    """The search API is the endpoint the turns already rely on; use it as a fallback."""
    from house_ear.session.briefing import from_news

    async def down(url, timeout, params=None):
        raise OSError("network unreachable")

    async def fallback(_cfg, count):
        return from_news(["搜索到的头条一", "搜索到的头条二"], date=_today(), limit=count)

    monkeypatch.setattr(info, "_get_json", down)
    monkeypatch.setattr(info, "_news_from_search", fallback)
    brief = asyncio.run(info.news(cfg, {"count": 2}))
    assert brief is not None
    assert "搜索到的头条一" in brief.speech_text


def _today() -> str:
    import datetime as _dt

    return _dt.date.today().isoformat()
