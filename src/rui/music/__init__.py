"""Music: sources behind one interface, one queue, tools that only speak intent.

Layering (docs/music.md): `query` turns the raw utterance into a plan, `sources`
answer plans with Tracks and resolve them to PCM, `service` owns the queue and
talks to the audio plane, `tools` exposes it all to the model. Nothing here
touches a speaker directly.
"""
from __future__ import annotations

from .history import PlayHistory
from .model import QueryPlan, ResolvedTrack, Track
from .service import MusicService
from .source import MusicSource, SourceError
from .sources import (
    PRESETS,
    JellyfinSource,
    MusicSourcePreset,
    NavidromeSource,
    SubsonicSource,
    build_source,
    get_preset,
    preset_ids,
    presets,
)

__all__ = [
    "JellyfinSource",
    "MusicService",
    "MusicSource",
    "MusicSourcePreset",
    "NavidromeSource",
    "PRESETS",
    "PlayHistory",
    "QueryPlan",
    "ResolvedTrack",
    "SourceError",
    "SubsonicSource",
    "Track",
    "build_source",
    "get_preset",
    "preset_ids",
    "presets",
]
