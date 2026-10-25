"""Generic Subsonic-family source.

Navidrome, Airsonic, Gonic and Ampache all expose the same `/rest/*.view` API
(`search3`, `getRandomSongs`, `getStarred2`, `star`, `stream`, `ping`) and the same
token+salt authentication (Subsonic 1.13+). This class is that protocol; a preset only
changes the display name and defaults.

Kept from the original Navidrome source (old build `tools/music.py`): the endpoint set,
whole-file download then local ffmpeg to 16 kHz mono s16, the >=45 s duration filter
with a fall-through when it would empty the result, and "no retry storm on a dead NAS".

Changed, each for a measured reason:

* **token+salt instead of plaintext u&p in the URL.** Every old-build request - stream
  downloads included - carried the password in the query string, which is a log-leak
  surface for zero benefit on a LAN. A `token` credential is used as the same shared
  secret when a deployment calls it an API token.
* **One httpx client with bounded retries.** The old build re-read the secrets file and
  opened a fresh urllib connection per call and never retried.
* **A PCM LRU cache.** The old build deleted the transcoded file as soon as the player
  read it, so replaying one song meant downloading its flac again.
* **Hashed cache names.** The song id comes from the server, so it is hashed before it
  becomes a file name; `../` in an id can no longer leave cache_dir.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets as pysecrets
import subprocess
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlencode

import httpx

from ...security.netguard import OutboundPolicy, guarded_async_client, guarded_sync_client
from ..model import Track
from ..source import SourceError

log = logging.getLogger(__name__)

API_VERSION = "1.16.1"
CLIENT_NAME = "rui"
DOWNLOAD_TIMEOUT_S = 30
TRANSCODE_TIMEOUT_S = 180
# Old build's filter (tools/music.py:395): short clips pollute a radio batch; if the
# filter would empty the list, keep everything rather than answer nothing.
MIN_DURATION_S = 45
RETRIES = 2
# A song id is server-supplied data and was concatenated straight into a cache file
# name; an id like "../../x" escaped cache_dir. Hash it first (96 bits is far beyond
# collision range for a cache) so the name is always a flat slug in this directory.
CACHE_STEM_LEN = 24


def _cache_stem(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()[:CACHE_STEM_LEN]


def _token_auth(user: str, secret: str) -> dict[str, str]:
    """Subsonic 1.13+ token auth: t = md5(secret + salt), never the secret itself."""
    salt = pysecrets.token_hex(8)
    token = hashlib.md5((secret + salt).encode("utf-8")).hexdigest()  # noqa: S324 - the API mandates md5
    return {"u": user, "t": token, "s": salt}


def _credentials_from_mapping(data: dict) -> tuple[str, str, str]:
    """Read a credentials dict written for any Subsonic-family preset.

    `url`/`base_url` and `password`/`token` are accepted because a hand-written or
    migrated file may use either spelling; the console writes the preset slot names.
    """
    base = str(data.get("url") or data.get("base_url") or "").strip().rstrip("/")
    user = str(data.get("user") or data.get("username") or "").strip()
    secret = str(data.get("password") or data.get("token") or "").strip()
    return base, user, secret


class SubsonicSource:
    """One Subsonic-family server, one cache dir. All calls async; blocking work in threads.

    Two modes, deliberately:

    * `secrets_path` given: credentials are re-read per call, so editing the write-only
      file takes effect without a restart (the old build's behaviour).
    * explicit `base_url`/`user`/`password`/`token` given: a fixed connection, used by
      the console's "test connection" so an unsaved draft never touches disk.
    """

    name = "subsonic"
    protocol_family = "subsonic"

    def __init__(self, secrets_path: Path | str | None, cache_dir: Path,
                 cache_tracks: int = 8,
                 transport: httpx.AsyncBaseTransport | None = None,
                 policy: OutboundPolicy | None = None, *,
                 base_url: str = "", user: str = "", password: str = "",
                 token: str = "", source_id: str = "subsonic") -> None:
        self.source_id = source_id
        self.name = source_id
        self._secrets_path = Path(secrets_path).expanduser() if secrets_path else None
        # The URL comes from the credentials json, i.e. remote-configurable. Guard both
        # the JSON API client and the blocking stream download.
        self._policy = policy or OutboundPolicy()
        # expanduser twice over: the config accepts `~` (a literal ~ directory was
        # created under the repo before this, measured 22:02).
        self._cache_dir = cache_dir.expanduser()
        self._cache_max = cache_tracks
        # Injection point for tests: a MockTransport answers without a NAS.
        self._transport = transport
        self._lru: OrderedDict[str, float] = OrderedDict()   # song id -> last use
        self._client: httpx.AsyncClient | None = None
        self._base = base_url.strip().rstrip("/")
        # Any explicit value means "fixed connection" mode, even if incomplete: the
        # caller gets a clear SourceError instead of an unrelated file being read.
        self._static = any(part.strip() for part in (base_url, user, password, token))
        self._fixed = (
            self._base,
            user.strip(),
            (password or token).strip(),
        )

    # ------------------------------------------------------------------ plumbing
    def _load_auth(self) -> tuple[str, dict[str, str]]:
        """Return (base_url, auth params) and never the secret outside this method.

        In file mode the json is read per call, like the old build: edit the file, no
        restart. The secret only travels into the request query as an md5 token.
        """
        if self._static:
            base, user, secret = self._fixed
            if not (base and user and secret):
                raise SourceError(
                    "music credentials incomplete: need url, user and password/token")
            return str(base), _token_auth(str(user), str(secret))
        if self._secrets_path is None:
            raise SourceError("music credentials are not configured")
        try:
            data = json.loads(self._secrets_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SourceError(f"music credentials unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise SourceError("music credentials must be a JSON object")
        base, user, secret = _credentials_from_mapping(data)
        if not (base and user and secret):
            raise SourceError(
                "music credentials incomplete: need url, user and password/token")
        self._base = base
        return base, _token_auth(user, secret)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = guarded_async_client(
                self._policy, timeout=DOWNLOAD_TIMEOUT_S, transport=self._transport)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, method: str, **params: object) -> dict:
        """One REST call, retried only on transport errors (not API errors)."""
        base, auth = self._load_auth()
        query = {"v": API_VERSION, "c": CLIENT_NAME, "f": "json", **auth, **params}
        url = f"{base}/rest/{method}.view?{urlencode(query)}"
        last: Exception | None = None
        for _ in range(RETRIES + 1):
            try:
                resp = await self._http().get(url)
                resp.raise_for_status()
                body = resp.json().get("subsonic-response", {})
                if body.get("status") != "ok":
                    raise SourceError(f"{method}: {body.get('error', {}).get('message', 'not ok')}")
                return body
            except (httpx.TransportError, httpx.HTTPError) as exc:  # noqa: PERF203 - bounded
                last = exc
                await asyncio.sleep(0.3)
        raise SourceError(f"{method}: {last}")

    # ------------------------------------------------------------------ connection
    async def ping(self) -> None:
        """The canonical authenticated round trip; raises `SourceError` when refused."""
        await self._get("ping")

    # ------------------------------------------------------------------ queries
    def _tracks(self, songs: list[dict], limit: int) -> list[Track]:
        out = [
            Track(
                id=f"subsonic:{s['id']}",
                title=str(s.get("title") or ""),
                artist=str(s.get("artist") or ""),
                duration=int(s.get("duration") or 0),
                # The old build's _brief dropped these; the descriptive path needs
                # them (mood by BPM, then by genre tag) and they cost nothing here.
                genre=str(s.get("genre") or ""),
                bpm=int(s.get("bpm") or 0),
            )
            for s in songs
        ]
        good = [t for t in out if t.duration >= MIN_DURATION_S]
        batch = good if (good or not out) else out
        return batch[:limit]

    async def search(self, term: str, limit: int) -> list[Track]:
        if not term.strip():
            return await self.random(limit)
        body = await self._get("search3", query=term.strip(), songCount=limit,
                               artistCount=0, albumCount=0)
        return self._tracks(body.get("searchResult3", {}).get("song", []) or [], limit)

    async def random(self, limit: int) -> list[Track]:
        body = await self._get("getRandomSongs", size=limit)
        return self._tracks(body.get("randomSongs", {}).get("song", []) or [], limit)

    async def by_genre(self, genre: str, limit: int = 40) -> list[Track]:
        """One genre tag's songs - the fallback for a language ask (J-Pop etc.)."""
        body = await self._get("getSongsByGenre", genre=genre, count=limit, offset=0)
        return self._tracks(body.get("songsByGenre", {}).get("song", []) or [], limit)

    # ------------------------------------------------------- library index pages
    # The per-song language index (library_index.py) walks the whole library.
    # Empty search3 is a match-all on Navidrome and pages 500 rows at a time; the
    # album walk is the fallback for a server that refuses an empty query (the old
    # build measured 12 requests / 0.6 s for 5738 songs vs 579 album requests).
    async def songs_page(self, offset: int, limit: int = 500) -> list[dict]:
        body = await self._get("search3", query="", songCount=limit, songOffset=offset,
                               artistCount=0, albumCount=0)
        return list(body.get("searchResult3", {}).get("song", []) or [])

    async def albums_page(self, offset: int, limit: int = 500) -> list[dict]:
        body = await self._get("getAlbumList2", type="alphabeticalByName",
                               size=limit, offset=offset)
        return list(body.get("albumList2", {}).get("album", []) or [])

    async def album_songs(self, album_id: str) -> list[dict]:
        body = await self._get("getAlbum", id=album_id)
        return list(body.get("album", {}).get("song", []) or [])

    async def favorites(self) -> list[Track]:
        body = await self._get("getStarred2")
        songs = body.get("starred2", {}).get("song", []) or []
        return self._tracks(songs, len(songs))

    async def star(self, track_id: str, on: bool) -> bool:
        sid = track_id.split(":", 1)[-1]
        method = "star" if on else "unstar"
        try:
            await self._get(method, id=sid)
            return True
        except SourceError as exc:
            log.info("star %s failed: %s", track_id, exc)
            return False

    # ------------------------------------------------------------------ resolve
    async def resolve(self, track: Track) -> Path:
        sid = track.id.split(":", 1)[-1]
        stem = _cache_stem(sid)
        dst = self._cache_dir / f"{stem}.pcm"
        if dst.is_file() and dst.stat().st_size > 0:
            self._touch(sid)
            return dst
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        src = self._cache_dir / f"{stem}.audio"
        base, auth = self._load_auth()
        query = {"v": API_VERSION, "c": CLIENT_NAME, **auth, "id": sid}
        url = f"{base}/rest/stream.view?{urlencode(query)}"
        last: Exception | None = None
        for _ in range(RETRIES + 1):
            try:
                size = await asyncio.to_thread(self._download, url, src)
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
        raise SourceError(f"resolve {track.label}: {last}")

    def _download(self, url: str, dst: Path) -> int:
        """Blocking download, run in a thread. Returns the byte count."""
        size = 0
        with guarded_sync_client(self._policy,
                                 timeout=DOWNLOAD_TIMEOUT_S) as client, dst.open("wb") as f:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
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
            (self._cache_dir / f"{_cache_stem(old)}.pcm").unlink(missing_ok=True)
