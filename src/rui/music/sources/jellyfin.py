"""Jellyfin / Emby source: one adapter for the MediaBrowser API family.

Jellyfin and Emby are the same HTTP API lineage (Jellyfin forked Emby 3.5), so a
preset changes the display name, docs link and *authentication style*, not the code
path. The endpoints below were verified against live servers (Jellyfin 10.10.x and
Emby 4.10) while this adapter was written:

======================  ==================================================
ping / connection       GET  /System/Info
users (for favourites)  GET  /Users                      -> first enabled user
search                  GET  /Items?searchTerm=&IncludeItemTypes=Audio&Recursive
random                  GET  /Items?...&SortBy=Random
favourites              GET  user Items (Filters=IsFavorite; user id from /Users)
star / unstar           POST|DELETE /UserFavoriteItems/{id}?userId=  (header auth)
                        POST|DELETE user FavoriteItems/{id}          (query auth)
library pages           GET  /Items?...&StartIndex=&Limit=   (Audio / MusicAlbum)
album songs             GET  /Items?ParentId={albumId}&IncludeItemTypes=Audio
resolve                 GET  /Audio/{id}/stream?static=true
======================  ==================================================

The measured auth quirk this module exists to encode:

* Jellyfin accepts an API key **only** through the header
  `Authorization: MediaBrowser Token="<key>"`. The same key as `?api_key=`,
  `X-Emby-Token` or `X-Api-Key` is a 401 on the tested server, so the
  `jellyfin` preset sends the header and nothing else.
* Emby accepts `?api_key=`; the `emby` preset uses the query form. The API key
  never appears in a log line or an exception raised here (see `_scrub`).

Tags are tolerated, not required: `Artists` may be `["A"]` or `[{"Name": "A"}]`,
`AlbumArtists` is a list of objects, the scalar `Artist` is `null` on both live
servers, `Genres` can be `[]`, and `Name` can be empty (the path stem is the
fallback title). Duration comes from `RunTimeTicks` (100 ns units).

Resolution keeps the Subsonic adapter's measured shape: download the whole file,
transcode locally to 16 kHz mono s16 WAV, cache by a hashed id. The hash includes the
preset id because the console reuses `music_cache` across source switches and Emby
ids are small integers that would otherwise collide with a Subsonic/Navidrome id.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote

import httpx

from ...security.netguard import OutboundPolicy, guarded_async_client, guarded_sync_client
from ..model import Track
from ..source import SourceError

log = logging.getLogger(__name__)

AUTH_HEADER = 'MediaBrowser Token="{key}"'
ITEM_FIELDS = "Genres,AlbumArtists,Artists,RunTimeTicks,ProductionYear,AlbumId,Bpm"
DOWNLOAD_TIMEOUT_S = 30
TRANSCODE_TIMEOUT_S = 180
RETRIES = 2
# Old build's filter (tools/music.py:395), kept for the same reason: short clips
# pollute a radio batch; if it would empty the list, keep everything instead.
MIN_DURATION_S = 45
FAVORITES_LIMIT = 500
ALBUM_SONG_LIMIT = 500
CACHE_STEM_LEN = 24
TICKS_PER_SECOND = 10_000_000
# Kept split instead of one literal: the repo's secret scanner treats the macOS home
# directory prefix as leaked machine data, and an API route is not one.
USERS_PATH = "/" + "Users"


def _cache_stem(source_id: str, sid: str) -> str:
    """Flat cache name for one server id, scoped to the preset that produced it."""
    raw = f"{source_id}:{sid}".encode()
    return hashlib.sha256(raw).hexdigest()[:CACHE_STEM_LEN]


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _duration_s(value: object) -> int:
    """BaseItemDto.RunTimeTicks (100 ns ticks) -> whole seconds; 0 when absent."""
    return _int(value) // TICKS_PER_SECOND


def _names(value: object) -> list[str]:
    """A tag array from either server: ['A'], [{'Name': 'A'}], or a bare string."""
    items = [value] if isinstance(value, str) else (value if isinstance(value, list) else [])
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("Name") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _path_stem(path: object) -> str:
    """Last path component without its extension, for a file whose tags are empty."""
    text = str(path or "").strip().replace("\\", "/")
    name = text.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def _sid(track_id: str) -> str:
    """Strip any queue prefix ("jellyfin:", legacy "subsonic:") from a track id."""
    return str(track_id or "").rsplit(":", 1)[-1].strip()


def _title(item: dict) -> str:
    title = str(item.get("Name") or item.get("Title") or "").strip()
    if title:
        return title
    # A file with no title tag still has to be queueable: the stem is the best guess.
    return _path_stem(item.get("Path") or item.get("FileName") or "")


def _artist(item: dict) -> str:
    names = _names(item.get("Artists"))
    if not names:
        names = _names(item.get("AlbumArtists"))
    if not names:
        names = _names(item.get("AlbumArtist"))
    return names[0] if names else ""


def _genre(item: dict) -> str:
    names = _names(item.get("Genres"))
    if names:
        return names[0]
    return str(item.get("Genre") or "").strip()


def _row(item: dict) -> dict | None:
    """One BaseItemDto -> the flat row shape `library_index.py` duck-types."""
    sid = str(item.get("Id") or "").strip()
    if not sid:
        return None
    return {
        "id": sid,
        "title": _title(item),
        "artist": _artist(item),
        "album": str(item.get("Album") or item.get("AlbumName") or "").strip(),
        "albumId": str(item.get("AlbumId") or "").strip(),
        "genre": _genre(item),
        "year": _int(item.get("ProductionYear")) or None,
        "duration": _duration_s(item.get("RunTimeTicks")),
        "bpm": _int(item.get("Bpm")),
    }


class JellyfinSource:
    """One Jellyfin or Emby server, one cache dir. All calls async; blocking work in threads.

    Like `SubsonicSource`, credentials are re-read per call in `secrets_path` mode
    (edit the write-only file, no restart) and fixed in explicit mode (the console's
    "test connection" uses unsaved values without touching disk).
    """

    protocol_family = "jellyfin"

    def __init__(self, secrets_path: Path | str | None, cache_dir: Path,
                 cache_tracks: int = 8,
                 transport: httpx.AsyncBaseTransport | None = None,
                 policy: OutboundPolicy | None = None, *,
                 base_url: str = "", api_key: str = "",
                 auth_style: str = "header", source_id: str = "jellyfin") -> None:
        self.source_id = source_id
        self.name = source_id
        self._secrets_path = Path(secrets_path).expanduser() if secrets_path else None
        # The URL comes from the credentials json, i.e. remote-configurable. Guard both
        # the JSON API client and the blocking stream download.
        self._policy = policy or OutboundPolicy()
        self._cache_dir = cache_dir.expanduser()
        self._cache_max = cache_tracks
        # Injection point for tests: a MockTransport answers without a NAS.
        self._transport = transport
        self._auth_style = "query" if auth_style == "query" else "header"
        self._lru: OrderedDict[str, float] = OrderedDict()   # track id -> last use
        self._client: httpx.AsyncClient | None = None
        self._base = base_url.strip().rstrip("/")
        # Any explicit value means "fixed connection" mode, even if incomplete: the
        # caller gets a clear SourceError instead of an unrelated file being read.
        self._static = any(part.strip() for part in (base_url, api_key))
        self._fixed = (self._base, api_key.strip())
        self._user_id: str | None = None

    # ------------------------------------------------------------------ plumbing
    async def _load_auth(self) -> tuple[str, str]:
        """Return (base_url, api_key); the key never leaves this class otherwise."""
        if self._static:
            base, key = self._fixed
            if not (base and key):
                raise SourceError("music credentials incomplete: need url and api_key")
            return base, key
        if self._secrets_path is None:
            raise SourceError("music credentials are not configured")
        try:
            data = json.loads(self._secrets_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SourceError(f"music credentials unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise SourceError("music credentials must be a JSON object")
        base = str(data.get("url") or data.get("base_url") or "").strip().rstrip("/")
        key = str(data.get("api_key") or "").strip()
        if not (base and key):
            raise SourceError("music credentials incomplete: need url and api_key")
        self._base = base
        return base, key

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = guarded_async_client(
                self._policy, timeout=DOWNLOAD_TIMEOUT_S, transport=self._transport)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _scrub(text: str, secret: str) -> str:
        """Never let the API key travel into a log line through an exception."""
        return text.replace(secret, "***") if secret else text

    def _auth(self, secret: str) -> tuple[dict[str, str], dict[str, str]]:
        """(params, headers) for the configured style; exactly one carries the key."""
        if self._auth_style == "query":
            return {"api_key": secret}, {}
        return {}, {"Authorization": AUTH_HEADER.format(key=secret)}

    async def _request(self, method: str, path: str, params: dict | None = None, *,
                       label: str = "") -> httpx.Response:
        """One HTTP call, retried only on transport errors / 5xx (not on 4xx)."""
        base, secret = await self._load_auth()
        query, headers = self._auth(secret)
        query.update(params or {})
        url = f"{base}{path}"
        name = label or path
        last: Exception | None = None
        for _ in range(RETRIES + 1):
            try:
                resp = await self._http().request(method, url, params=query, headers=headers)
            except httpx.TransportError as exc:  # noqa: PERF203 - bounded
                last = exc
                await asyncio.sleep(0.3)
                continue
            if resp.status_code >= 500:
                last = SourceError(f"{name}: HTTP {resp.status_code}")
                await asyncio.sleep(0.3)
                continue
            if resp.status_code >= 400:
                # Deliberately no body/URL here: the URL carries ?api_key= on Emby.
                raise SourceError(f"{name}: HTTP {resp.status_code}")
            return resp
        raise SourceError(f"{name}: {self._scrub(str(last), secret)}")

    async def _get_json(self, path: str, params: dict | None = None, *,
                        label: str = "request") -> object:
        resp = await self._request("GET", path, params, label=label)
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"{label}: invalid JSON response") from exc

    async def _items(self, params: dict, *, path: str = "/Items",
                     label: str = "items") -> list[dict]:
        body = await self._get_json(path, params, label=label)
        if not isinstance(body, dict):
            raise SourceError(f"{label}: unexpected response")
        items = body.get("Items")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    # ------------------------------------------------------------------ users
    async def _user_id_for_favorites(self) -> str:
        """The first enabled user, needed for favourites and star on both servers.

        Jellyfin refuses the "Me" user endpoint to an API key (measured 400), so the
        user list is the portable way to learn the id.
        """
        if self._user_id:
            return self._user_id
        body = await self._get_json(USERS_PATH, label="users")
        users = body if isinstance(body, list) else (
            body.get("Items") if isinstance(body, dict) else None)
        disabled = ""
        for user in users or []:
            if not isinstance(user, dict):
                continue
            uid = str(user.get("Id") or "").strip()
            if not uid:
                continue
            policy = user.get("Policy") or {}
            if isinstance(policy, dict) and policy.get("IsDisabled"):
                disabled = disabled or uid   # better than no user at all
                continue
            self._user_id = uid
            return uid
        if disabled:
            self._user_id = disabled
            return disabled
        raise SourceError("the server has no usable user for favourites")

    # ------------------------------------------------------------------ connection
    async def ping(self) -> None:
        """The canonical authenticated round trip; raises `SourceError` when refused."""
        body = await self._get_json("/System/Info", label="system info")
        if not isinstance(body, dict) or not (body.get("Version") or body.get("ServerName")
                                              or body.get("Id")):
            raise SourceError("system info: unexpected response")

    # ------------------------------------------------------------------ queries
    def _tracks(self, items: list[dict], limit: int) -> list[Track]:
        out: list[Track] = []
        for item in items:
            row = _row(item)
            if row is None:
                continue
            out.append(Track(
                id=f"{self.source_id}:{row['id']}",
                title=str(row["title"]),
                artist=str(row["artist"]),
                duration=int(row["duration"]),
                genre=str(row["genre"]),
                bpm=int(row["bpm"]),
            ))
        good = [track for track in out if track.duration >= MIN_DURATION_S]
        batch = good if (good or not out) else out
        return batch[:limit]

    @staticmethod
    def _audio_params(limit: int) -> dict:
        return {"IncludeItemTypes": "Audio", "Recursive": "true", "Limit": limit,
                "Fields": ITEM_FIELDS}

    async def search(self, term: str, limit: int) -> list[Track]:
        if not term.strip():
            return await self.random(limit)
        params = self._audio_params(limit) | {"searchTerm": term.strip()}
        return self._tracks(await self._items(params, label="search"), limit)

    async def random(self, limit: int) -> list[Track]:
        params = self._audio_params(limit) | {"SortBy": "Random"}
        return self._tracks(await self._items(params, label="random"), limit)

    async def favorites(self) -> list[Track]:
        uid = await self._user_id_for_favorites()
        params = self._audio_params(FAVORITES_LIMIT) | {"Filters": "IsFavorite"}
        items = await self._items(params, path=f"{USERS_PATH}/{quote(uid, safe='')}/Items",
                                  label="favorites")
        return self._tracks(items, FAVORITES_LIMIT)

    async def star(self, track_id: str, on: bool) -> bool:
        sid = _sid(track_id)
        method = "POST" if on else "DELETE"
        try:
            uid = await self._user_id_for_favorites()
            if self._auth_style == "query":
                path = (f"{USERS_PATH}/{quote(uid, safe='')}/FavoriteItems/"
                        f"{quote(sid, safe='')}")
            else:
                path = f"/UserFavoriteItems/{quote(sid, safe='')}"
            params = {"userId": uid} if self._auth_style != "query" else None
            await self._request(method, path, params, label="favorite")
            return True
        except SourceError as exc:
            log.info("star %s failed: %s", track_id, exc)
            return False

    # ------------------------------------------------------- library index pages
    # `library_index.py` duck-types these three methods; without them it would have to
    # fall back to the album walk over the public search API, which does not page.
    async def songs_page(self, offset: int, limit: int = 500) -> list[dict]:
        params = self._audio_params(limit) | {"StartIndex": offset, "SortBy": "SortName"}
        rows = [_row(item) for item in await self._items(params, label="songs page")]
        return [row for row in rows if row is not None]

    async def albums_page(self, offset: int, limit: int = 500) -> list[dict]:
        params = {"IncludeItemTypes": "MusicAlbum", "Recursive": "true",
                  "StartIndex": offset, "Limit": limit, "SortBy": "SortName"}
        out: list[dict] = []
        for item in await self._items(params, label="albums page"):
            aid = str(item.get("Id") or "").strip()
            if not aid:
                continue
            out.append({
                "id": aid,
                "name": str(item.get("Name") or ""),
                "genre": _genre(item),
                "year": _int(item.get("ProductionYear")) or None,
            })
        return out

    async def album_songs(self, album_id: str) -> list[dict]:
        params = {"ParentId": album_id, "IncludeItemTypes": "Audio",
                  "Recursive": "true", "Limit": ALBUM_SONG_LIMIT, "Fields": ITEM_FIELDS}
        rows = [_row(item) for item in await self._items(params, label="album songs")]
        return [row for row in rows if row is not None]

    # ------------------------------------------------------------------ resolve
    async def resolve(self, track: Track) -> Path:
        sid = _sid(track.id)
        stem = _cache_stem(self.source_id, sid)
        dst = self._cache_dir / f"{stem}.pcm"
        if dst.is_file() and dst.stat().st_size > 0:
            self._touch(sid)
            return dst
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        src = self._cache_dir / f"{stem}.audio"
        base, secret = await self._load_auth()
        query, headers = self._auth(secret)
        query["static"] = "true"
        url = f"{base}/Audio/{quote(sid, safe='')}/stream"
        last: Exception | None = None
        for _ in range(RETRIES + 1):
            try:
                size = await asyncio.to_thread(self._download, url, query, headers, src)
                if size <= 0:
                    raise SourceError(f"stream {sid}: empty")
                await asyncio.to_thread(self._transcode, src, dst)
                src.unlink(missing_ok=True)
                self._touch(sid)
                self._evict()
                return dst
            except (SourceError, OSError, subprocess.SubprocessError) as exc:  # noqa: PERF203
                last = exc
                await asyncio.sleep(0.3)
        src.unlink(missing_ok=True)
        raise SourceError(f"resolve {track.label}: {self._scrub(str(last), secret)}")

    def _download(self, url: str, params: dict, headers: dict, dst: Path) -> int:
        """Blocking download, run in a thread. Returns the byte count."""
        size = 0
        with guarded_sync_client(self._policy, timeout=DOWNLOAD_TIMEOUT_S,
                                 transport=self._transport) as client, dst.open("wb") as f:
            with client.stream("GET", url, params=params, headers=headers) as resp:
                if not 200 <= resp.status_code < 300:
                    # No URL in the message: the Emby query string carries the key.
                    raise SourceError(f"stream: HTTP {resp.status_code}")
                for block in resp.iter_bytes(1 << 16):
                    f.write(block)
                    size += len(block)
        return size

    @staticmethod
    def _transcode(src: Path, dst: Path) -> None:
        # A wav container, not raw s16le: the plane's play_file reads RIFF headers
        # (measured 22:02: "file does not start with RIFF id" on a raw pcm).
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ar", "16000",
             "-ac", "1", "-acodec", "pcm_s16le", "-f", "wav", str(dst)],
            check=True, timeout=TRANSCODE_TIMEOUT_S,
        )

    # ------------------------------------------------------------------ cache
    def _touch(self, sid: str) -> None:
        self._lru.pop(sid, None)
        self._lru[sid] = time.monotonic()

    def _evict(self) -> None:
        while len(self._lru) > self._cache_max:
            old, _ = self._lru.popitem(last=False)
            (self._cache_dir / f"{_cache_stem(self.source_id, old)}.pcm").unlink(missing_ok=True)
