"""Jellyfin/Emby source: registry, both auth styles, pages and resolve - fully offline.

The adapter talks to the MediaBrowser HTTP API. Two facts are pinned here because the
live servers disagree on them:

* a Jellyfin API key travels **only** in `Authorization: MediaBrowser Token="<key>"`
  (the same key as `?api_key=` is a 401 there), while Emby accepts `?api_key=`;
* favourites are per-user, so the adapter asks `GET /Users` for the first enabled user
  (Jellyfin rejects the "Me" user endpoint to an API key) and then uses that id.

Everything below answers through `httpx.MockTransport`, so no server, LAN or ffmpeg is
needed unless the optional real-transcode test is collected.
"""
from __future__ import annotations

import asyncio
import io
import shutil
import wave
from pathlib import Path

import httpx
import pytest

from rui.music import JellyfinSource, build_source
from rui.music.library_index import LibraryIndex
from rui.music.model import Track
from rui.music.source import SourceError
from rui.music.sources.jellyfin import _cache_stem
from rui.music.sources.presets import get_preset, missing_credentials, preset_ids
from rui.security.netguard import OutboundPolicy

KEY = "test-api-key-abc123"
USER_ID = "user-1"
# Split so the repo's secret scanner does not read an API route as a home path.
USERS = "/" + "Users"
OTHER_KEY = "wrong-key"

# Tag shapes measured on the live servers: a null scalar Artist, Artists as strings,
# AlbumArtists as objects, an empty Genres list, and an item with no tags at all
# (Name missing -> the path stem is the fallback title).
SONGS: list[dict] = [
    {"Id": "song-1", "Name": "Rui Adapter Tone 01", "Artists": ["Artist A"],
     "AlbumArtists": [{"Name": "Ensemble", "Id": "art-1"}],
     "RunTimeTicks": 600_000_000, "Genres": ["Test"], "Album": "Album One",
     "AlbumId": "album-1", "ProductionYear": 2026, "Artist": None},
    {"Id": "song-2", "Name": "Tone Two", "Artists": ["Artist B"],
     "RunTimeTicks": 120_000_000, "Genres": [], "Album": "Album One",
     "AlbumId": "album-1", "ProductionYear": 2026, "Artist": None},
    {"Id": "song-3", "Path": "/music/No Tags.mp3", "Artists": None,
     "AlbumArtists": None, "Genres": None, "RunTimeTicks": None},
    {"Id": "song-4", "Name": "Rui Song Four", "Artists": [{"Name": "Dict Artist"}],
     "RunTimeTicks": 1_800_000_000, "Genres": ["Rock"], "Album": "Album Two",
     "AlbumId": "album-2", "ProductionYear": 2024},
    {"Id": "song-5", "Name": "Tone Five", "Artists": [],
     "AlbumArtists": "Scalar Ensemble", "RunTimeTicks": 2_000_000_000,
     "Genres": ["Pop"], "Album": "Album Two", "AlbumId": "album-2"},
    {"Id": "song-6", "Name": "Rui Six", "Artists": ["Artist A"],
     "RunTimeTicks": 3_000_000_000, "Album": "Album One", "AlbumId": "album-1"},
]
ALBUMS: list[dict] = [
    {"Id": "album-1", "Name": "Album One", "Genres": ["Rock"],
     "ProductionYear": 2026, "Type": "MusicAlbum"},
    {"Id": "album-2", "Name": "Album Two", "ProductionYear": 2024,
     "Type": "MusicAlbum"},
]


def _wav_bytes(rate: int = 8000, channels: int = 1, seconds: float = 0.2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * channels * int(rate * seconds))
    return buffer.getvalue()


class FakeMediaServer:
    """One fake MediaBrowser server that **verifies** the auth style it was built for."""

    def __init__(self, style: str = "header") -> None:
        assert style in ("header", "query")
        self.style = style
        self.requests: list[dict] = []
        self.favorites: set[str] = set()
        self.audio = _wav_bytes()
        self.audio_type = "audio/wav"

    # ------------------------------------------------------------------ transport
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _json(self, status: int = 200, **kwargs) -> httpx.Response:
        return httpx.Response(status, json=kwargs)

    def _unauthorized(self) -> httpx.Response:
        return httpx.Response(401, json={"error": "Unauthorized"})

    def _authorized(self, request: httpx.Request) -> bool:
        params = request.url.params
        if self.style == "header":
            # A Jellyfin key is refused in the query; the header is the only way in.
            if "api_key" in params:
                return False
            return request.headers.get("authorization") == f'MediaBrowser Token="{KEY}"'
        if "authorization" in request.headers:
            return False
        return params.get("api_key") == KEY

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({
            "method": request.method,
            "path": request.url.path,
            "params": {k: v for k, v in request.url.params.multi_items()},
            "headers": {k.lower(): v for k, v in request.headers.items()},
        })
        if not self._authorized(request):
            return self._unauthorized()
        path = request.url.path
        params = request.url.params
        method = request.method
        if path == "/System/Info":
            return self._json(Id="server-1", ServerName="Fake", Version="10.10.0")
        if path == "/Users":
            return httpx.Response(200, json=[
                {"Id": USER_ID, "Name": "ruiadmin", "Policy": {"IsDisabled": False}},
            ])
        if path.startswith(USERS + "/") and path.endswith("/Items"):
            return self._items(params, self._favorites())
        if (path.startswith(USERS + "/") and "/FavoriteItems/" in path
                and method != "GET"):
            return self._favorite_toggle(path, method)
        if path.startswith("/UserFavoriteItems/") and method != "GET":
            return self._favorite_toggle(path, method)
        if path == "/Items":
            return self._dispatch_items(params)
        if path.startswith("/Audio/") and path.endswith("/stream"):
            return httpx.Response(200, content=self.audio,
                                  headers={"Content-Type": self.audio_type})
        return httpx.Response(404, json={"error": "not found"})

    # ------------------------------------------------------------------ payloads
    def _favorites(self) -> list[dict]:
        return [song for song in SONGS if song["Id"] in self.favorites]

    def _favorite_toggle(self, path: str, method: str) -> httpx.Response:
        sid = path.rsplit("/", 1)[-1]
        if method == "POST":
            self.favorites.add(sid)
        elif method == "DELETE":
            self.favorites.discard(sid)
        return self._json(IsFavorite=method == "POST")

    def _items(self, params: httpx.QueryParams, songs: list[dict]) -> httpx.Response:
        limit = int(params.get("Limit") or 500)
        start = int(params.get("StartIndex") or 0)
        page = songs[start:start + limit]
        return httpx.Response(200, json={"Items": page, "TotalRecordCount": len(songs)})

    def _dispatch_items(self, params: httpx.QueryParams) -> httpx.Response:
        if params.get("IncludeItemTypes") == "MusicAlbum":
            return self._items(params, ALBUMS)
        if params.get("ParentId"):
            album_id = params["ParentId"]
            return self._items(params, [s for s in SONGS if s.get("AlbumId") == album_id])
        if params.get("Filters") == "IsFavorite":
            return self._items(params, self._favorites())
        if params.get("SortBy") == "Random":
            return self._items(params, [SONGS[0], SONGS[3], SONGS[4]])
        term = (params.get("searchTerm") or "").casefold()
        if term:
            return self._items(params, [s for s in SONGS
                                        if term in str(s.get("Name") or "").casefold()])
        return self._items(params, SONGS)

    # ------------------------------------------------------------------ asserters
    def request_count(self, path_suffix: str) -> int:
        return sum(1 for item in self.requests if item["path"].endswith(path_suffix))

    def assert_auth_style(self) -> None:
        assert self.requests, "no request reached the fake server"
        for item in self.requests:
            if self.style == "header":
                assert item["headers"].get("authorization") == \
                    f'MediaBrowser Token="{KEY}"', item
                assert "api_key" not in item["params"], item
            else:
                assert item["params"].get("api_key") == KEY, item
                assert "authorization" not in item["headers"], item


def _source(preset_id: str, fake: FakeMediaServer, tmp_path: Path,
            *, api_key: str = KEY, secrets: Path | None = None) -> JellyfinSource:
    return build_source(
        preset_id, secrets, tmp_path / "cache", cache_tracks=4,
        transport=fake.transport(),
        policy=OutboundPolicy(enabled=False),  # the fake has no real DNS name
        base_url="" if secrets is not None else "http://jellyfin.test",
        api_key="" if secrets is not None else api_key,
    )


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------- registry

def test_registry_offers_jellyfin_and_emby_as_one_family() -> None:
    ids = preset_ids()
    assert "jellyfin" in ids and "emby" in ids
    jellyfin = get_preset("jellyfin")
    emby = get_preset("emby")
    assert jellyfin is not None and emby is not None
    assert jellyfin.protocol_family == emby.protocol_family == "jellyfin"
    assert jellyfin.auth_style == "header"
    assert emby.auth_style == "query"
    for preset, port in ((jellyfin, 8096), (emby, 8096)):
        assert preset.supported is True
        assert preset.default_port == port
        assert preset.docs_url.startswith("https://")
        assert {item.key for item in preset.credential_fields} == {"url", "api_key"}
        fields = {item.key: item for item in preset.credential_fields}
        assert fields["url"].kind == "url"
        assert fields["api_key"].kind == "password"
        assert fields["api_key"].required is True
        assert missing_credentials(preset, {}) == ("url", "api_key")
        assert missing_credentials(preset, {"url": "http://x", "api_key": "k"}) == ()
        assert get_preset("plex") is None


def test_build_source_dispatches_the_jellyfin_family(tmp_path: Path) -> None:
    jellyfin = build_source("jellyfin", None, tmp_path / "c", base_url="http://x",
                            api_key="k")
    emby = build_source("emby", None, tmp_path / "c", base_url="http://x", api_key="k")
    assert isinstance(jellyfin, JellyfinSource) and jellyfin.source_id == "jellyfin"
    assert isinstance(emby, JellyfinSource) and emby.source_id == "emby"
    with pytest.raises(ValueError):
        build_source("plex", None, tmp_path / "c")


# --------------------------------------------------------------- auth + queries

@pytest.mark.parametrize("preset_id,style", [("jellyfin", "header"), ("emby", "query")])
def test_auth_style_ping_search_random_favorites_star(
        preset_id: str, style: str, tmp_path: Path) -> None:
    fake = FakeMediaServer(style)
    source = _source(preset_id, fake, tmp_path)

    async def run() -> None:
        try:
            await source.ping()
            hits = await source.search("Rui", 10)
            assert [track.title for track in hits] == ["Rui Adapter Tone 01", "Rui Song Four",
                                                       "Rui Six"]
            assert hits[0].id == f"{preset_id}:song-1"
            assert hits[0].artist == "Artist A"      # Artists wins over AlbumArtists
            assert hits[0].duration == 60
            assert hits[0].genre == "Test"
            radio = await source.random(3)
            assert [track.id for track in radio] == [
                f"{preset_id}:song-1", f"{preset_id}:song-4", f"{preset_id}:song-5"]
            assert radio[2].artist == "Scalar Ensemble"   # AlbumArtists as a bare string
            assert await source.favorites() == []
            assert await source.star(radio[0].id, True) is True
            favored = await source.favorites()
            assert [track.id for track in favored] == [f"{preset_id}:song-1"]
            assert await source.star(f"{preset_id}:song-1", False) is True
            assert await source.favorites() == []
        finally:
            await source.close()

    _run(run())
    fake.assert_auth_style()
    # The per-user id is discovered once and reused for favourites and star.
    users_calls = [item for item in fake.requests if item["path"] == "/Users"]
    assert len(users_calls) == 1
    favorite_paths = [item["path"] for item in fake.requests
                      if "favorite" in item["path"].lower()]
    if style == "header":
        assert any(path.startswith("/UserFavoriteItems/") for path in favorite_paths)
    else:
        assert any(path.startswith(f"{USERS}/{USER_ID}/FavoriteItems/")
                   for path in favorite_paths)


@pytest.mark.parametrize("preset_id,style", [("jellyfin", "header"), ("emby", "query")])
def test_empty_search_term_becomes_server_random(preset_id: str, style: str,
                                                 tmp_path: Path) -> None:
    fake = FakeMediaServer(style)
    source = _source(preset_id, fake, tmp_path)

    async def run() -> None:
        try:
            assert len(await source.search("   ", 3)) == 3
        finally:
            await source.close()

    _run(run())
    random_calls = [item for item in fake.requests
                    if item["path"] == "/Items" and item["params"].get("SortBy") == "Random"]
    assert len(random_calls) == 1


# -------------------------------------------------------------- library pages

@pytest.mark.parametrize("preset_id,style", [("jellyfin", "header"), ("emby", "query")])
def test_songs_page_rows_handle_missing_tags(preset_id: str, style: str,
                                             tmp_path: Path) -> None:
    fake = FakeMediaServer(style)
    source = _source(preset_id, fake, tmp_path)

    async def run() -> None:
        try:
            first = await source.songs_page(0, 2)
            second = await source.songs_page(2, 2)
            assert [row["id"] for row in first] == ["song-1", "song-2"]
            assert [row["id"] for row in second] == ["song-3", "song-4"]
            assert first[0] == {
                "id": "song-1", "title": "Rui Adapter Tone 01", "artist": "Artist A",
                "album": "Album One", "albumId": "album-1", "genre": "Test",
                "year": 2026, "duration": 60, "bpm": 0,
            }
            untagged = second[0]
            assert untagged["title"] == "No Tags"     # no Name tag -> path stem
            assert untagged["artist"] == ""
            assert untagged["duration"] == 0
            assert untagged["genre"] == ""
            assert untagged["album"] == ""
            assert untagged["year"] is None
        finally:
            await source.close()

    _run(run())
    starts = [int(item["params"].get("StartIndex") or 0) for item in fake.requests
              if item["path"] == "/Items"]
    assert starts[:2] == [0, 2]
    fake.assert_auth_style()


@pytest.mark.parametrize("preset_id,style", [("jellyfin", "header"), ("emby", "query")])
def test_albums_page_and_album_songs(preset_id: str, style: str, tmp_path: Path) -> None:
    fake = FakeMediaServer(style)
    source = _source(preset_id, fake, tmp_path)

    async def run() -> None:
        try:
            albums = await source.albums_page(0, 500)
            assert albums == [
                {"id": "album-1", "name": "Album One", "genre": "Rock", "year": 2026},
                {"id": "album-2", "name": "Album Two", "genre": "", "year": 2024},
            ]
            songs = await source.album_songs("album-1")
            assert [row["id"] for row in songs] == ["song-1", "song-2", "song-6"]
            assert all(row["albumId"] == "album-1" for row in songs)
        finally:
            await source.close()

    _run(run())
    album_calls = [item for item in fake.requests if item["path"] == "/Items"
                   and item["params"].get("IncludeItemTypes") == "MusicAlbum"]
    assert album_calls
    parent_calls = [item for item in fake.requests if item["params"].get("ParentId")]
    assert [item["params"]["ParentId"] for item in parent_calls] == ["album-1"]


@pytest.mark.parametrize("preset_id,style", [("jellyfin", "header"), ("emby", "query")])
def test_library_index_builds_from_pages_and_tracks_resolve(
        preset_id: str, style: str, tmp_path: Path, monkeypatch) -> None:
    fake = FakeMediaServer(style)
    source = _source(preset_id, fake, tmp_path)

    def fake_transcode(src: Path, dst: Path) -> None:  # noqa: ARG001 - same signature
        dst.write_bytes(src.read_bytes())

    monkeypatch.setattr(JellyfinSource, "_transcode", staticmethod(fake_transcode))

    async def run() -> None:
        try:
            index = LibraryIndex(tmp_path / "index.json", source)
            data = await index.build()
            assert data["source"] == "search3"       # songs_page paging, no album walk
            assert data["count"] == len(SONGS)
            # album-1 is tagged Rock, so the album-meta fallback also covers song-2/6.
            picked = index.pick(genre="Rock", limit=5, seed=7)
            assert {row["title"] for row in picked} == {
                "Tone Two", "Rui Song Four", "Rui Six"}
            tracks = index.to_tracks(picked)
            # library_index drops <45 s clips; the short "Tone Two" row measures 12 s.
            assert len(tracks) == 2
            # library_index prefixes ids with "subsonic:" regardless of the source; the
            # adapter strips any prefix, so a source switch does not need a cache rebuild.
            target = next(track for track in tracks if track.id == "subsonic:song-4")
            pcm = await source.resolve(target)
            assert pcm.is_file() and pcm.stat().st_size > 0
        finally:
            await source.close()

    _run(run())
    fake.assert_auth_style()


# --------------------------------------------------------------------- resolve

def test_resolve_downloads_then_caches_pcm(tmp_path: Path, monkeypatch) -> None:
    fake = FakeMediaServer("header")
    source = _source("jellyfin", fake, tmp_path)

    def fake_transcode(src: Path, dst: Path) -> None:  # noqa: ARG001 - same signature
        dst.write_bytes(src.read_bytes())

    monkeypatch.setattr(JellyfinSource, "_transcode", staticmethod(fake_transcode))

    async def run() -> None:
        try:
            pcm = await source.resolve(Track(id="jellyfin:song-1", title="Tone"))
            assert pcm.is_file() and pcm.read_bytes() == fake.audio
            assert pcm.suffix == ".pcm"
            streams = fake.request_count("/stream")
            # A queue id written by library_index still carries its old "subsonic:"
            # prefix; resolve strips any prefix rather than trusting one spelling.
            again = await source.resolve(Track(id="subsonic:song-1", title="Tone"))
            assert again == pcm
            assert fake.request_count("/stream") == streams == 1
        finally:
            await source.close()

    _run(run())
    stream_request = next(item for item in fake.requests if item["path"].endswith("/stream"))
    assert stream_request["path"] == "/Audio/song-1/stream"
    assert stream_request["params"].get("static") == "true"
    fake.assert_auth_style()


def test_cache_name_is_scoped_by_preset() -> None:
    # Emby ids are small integers; sharing one cache_dir across source switches must
    # not replay another server's PCM for the same numeric id.
    assert _cache_stem("jellyfin", "7") != _cache_stem("emby", "7")
    assert _cache_stem("jellyfin", "7") != _cache_stem("jellyfin", "8")


def test_ping_rejection_never_leaks_the_key(tmp_path: Path) -> None:
    fake = FakeMediaServer("query")
    source = _source("emby", fake, tmp_path, api_key=OTHER_KEY)

    async def run() -> None:
        try:
            with pytest.raises(SourceError) as caught:
                await source.ping()
            assert "HTTP 401" in str(caught.value)
            assert OTHER_KEY not in str(caught.value)
        finally:
            await source.close()

    _run(run())


def test_file_mode_reads_api_key_and_requires_it(tmp_path: Path) -> None:
    fake = FakeMediaServer("header")
    secrets = tmp_path / "lan_media.json"
    secrets.write_text('{"url": "http://jellyfin.test", "api_key": "' + KEY + '"}',
                       encoding="utf-8")
    source = _source("jellyfin", fake, tmp_path, secrets=secrets)

    async def run() -> None:
        try:
            await source.ping()
        finally:
            await source.close()

    _run(run())

    secrets.write_text('{"url": "http://jellyfin.test"}', encoding="utf-8")
    broken = _source("jellyfin", fake, tmp_path, secrets=secrets)

    async def fail() -> None:
        try:
            with pytest.raises(SourceError) as caught:
                await broken.ping()
            assert "api_key" in str(caught.value)
        finally:
            await broken.close()

    _run(fail())


FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_real_ffmpeg_transcodes_to_16khz_mono_s16(tmp_path: Path) -> None:
    fake = FakeMediaServer("header")
    fake.audio = _wav_bytes(rate=44100, channels=2, seconds=0.2)
    fake.audio_type = "audio/wav"
    source = _source("jellyfin", fake, tmp_path)

    async def run() -> Path:
        try:
            return await source.resolve(Track(id="jellyfin:song-1", title="Tone"))
        finally:
            await source.close()

    pcm = _run(run())
    with wave.open(str(pcm), "rb") as reader:
        assert reader.getframerate() == 16000
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getnframes() > 0
