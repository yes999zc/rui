"""Navidrome compatibility entry point.

Navidrome was the first source, so its module and class name are part of the contract
for tests and any embedder. The implementation is now the generic
`SubsonicSource`: Navidrome, Airsonic, Gonic and Ampache speak the same API, and the
preset registry decides which id the runtime passes in. Keep this module thin - a new
Subsonic-family preset does not need a new subclass.
"""
from __future__ import annotations

from .subsonic import (  # re-exported for the original import path
    API_VERSION,
    CLIENT_NAME,
    MIN_DURATION_S,
    RETRIES,
    SourceError,
    SubsonicSource,
    _cache_stem,
    _token_auth,
)

__all__ = [
    "API_VERSION",
    "CLIENT_NAME",
    "MIN_DURATION_S",
    "NavidromeSource",
    "RETRIES",
    "SourceError",
    "_cache_stem",
    "_token_auth",
]


class NavidromeSource(SubsonicSource):
    """The `navidrome` source id on the generic Subsonic implementation."""

    name = "navidrome"
    protocol_family = "subsonic"

    def __init__(self, secrets_path, cache_dir, cache_tracks: int = 8,
                 transport=None, policy=None, *, source_id: str = "navidrome",
                 **kwargs) -> None:
        super().__init__(
            secrets_path, cache_dir, cache_tracks, transport, policy,
            source_id=source_id, **kwargs,
        )
