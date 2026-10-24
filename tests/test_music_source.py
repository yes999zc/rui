"""NavidromeSource against a MockTransport: no NAS, no network, real auth shape.

The behaviours pinned here are the ones the old build got wrong or never had:
token+salt (never the password on the wire), the >=45 s filter with its fall-
through, and a PCM cache that survives a replay.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from rui.music.sources.navidrome import NavidromeSource, SourceError


def make_source(tmp_path: Path, handler, cache_tracks: int = 2) -> NavidromeSource:
    secrets = tmp_path / "lan_media.json"
    secrets.write_text(json.dumps(
        {"url": "http://nas.local", "user": "u", "password": "p"}), encoding="utf-8")
    return NavidromeSource(secrets, tmp_path / "cache", cache_tracks=cache_tracks,
                           transport=httpx.MockTransport(handler))


def subsonic(body: dict) -> httpx.Response:
    return httpx.Response(200, json={"subsonic-response": body})


def song(sid: str, title: str, artist: str, duration: int) -> dict:
    return {"id": sid, "title": title, "artist": artist, "duration": duration}


def test_auth_is_token_and_salt_never_the_password(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(pair.split("=", 1) for pair in request.url.query.decode().split("&")
                         if "=" in pair))
        return subsonic({"status": "ok", "randomSongs": {"song": []}})

    src = make_source(tmp_path, handler)
    import asyncio
    asyncio.run(src.random(5))

    assert "p" not in seen.get("p", "x") or seen.get("p") != "p", "password on the wire"
    assert "u" in seen and "t" in seen and "s" in seen, seen
    assert "p" not in seen, "plaintext password parameter must not exist"


def test_search_filters_short_clips_but_not_to_empty(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "search3" in str(request.url)
        return subsonic({"status": "ok", "searchResult3": {"song": [
            song("1", "晴天", "周杰伦", 269),
            song("2", "前奏", "某人", 12),      # a clip: filtered out
        ]}})

    src = make_source(tmp_path, handler)
    import asyncio
    hits = asyncio.run(src.search("晴天", 10))
    assert [t.id for t in hits] == ["subsonic:1"]

    def only_clips(request: httpx.Request) -> httpx.Response:
        return subsonic({"status": "ok", "searchResult3": {"song": [
            song("3", "短歌", "某人", 5)]}})

    src2 = make_source(tmp_path, only_clips)
    hits2 = asyncio.run(src2.search("短歌", 10))
    assert [t.id for t in hits2] == ["subsonic:3"], \
        "all-short must fall through rather than answer nothing"


def test_api_error_raises_source_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return subsonic({"status": "failed", "error": {"code": 40, "message": "wrong creds"}})

    src = make_source(tmp_path, handler)
    with pytest.raises(SourceError):
        import asyncio
        asyncio.run(src.random(5))


def test_resolve_caches_and_evicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_download(url: str, dst: Path) -> int:
        calls.append(url)
        dst.write_bytes(b"\x00" * 100)
        return 100

    monkeypatch.setattr(NavidromeSource, "_download", staticmethod(fake_download))
    monkeypatch.setattr(NavidromeSource, "_transcode",
                        staticmethod(lambda src, dst: dst.write_bytes(b"\x01" * 50)))

    src = make_source(tmp_path, lambda r: subsonic({"status": "ok"}), cache_tracks=1)
    from rui.music.model import Track
    a = Track(id="subsonic:aa", title="A", artist="X", duration=100)
    b = Track(id="subsonic:bb", title="B", artist="X", duration=100)

    import asyncio

    async def run() -> None:
        p1 = await src.resolve(a)
        p1b = await src.resolve(a)                  # cache hit: no second download
        p2 = await src.resolve(b)                   # evicts A (cache_tracks=1)
        assert p1 == p1b
        assert p1.exists() is False, "the LRU must actually evict"
        assert p2.exists()
        assert len(calls) == 2, calls               # A once, B once

    asyncio.run(run())
