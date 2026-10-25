"""Music source adapters and the preset-driven factory."""
from __future__ import annotations

from pathlib import Path

from .presets import PRESETS, MusicSourcePreset, get_preset, preset_ids, presets
from .subsonic import SubsonicSource

__all__ = [
    "MusicSourcePreset",
    "NavidromeSource",
    "PRESETS",
    "SubsonicSource",
    "build_source",
    "get_preset",
    "preset_ids",
    "presets",
]


def build_source(preset_id: str, secrets_path: Path | str | None, cache_dir: Path,
                 cache_tracks: int = 8, transport=None, policy=None, *,
                 base_url: str = "", user: str = "", password: str = "",
                 token: str = "") -> SubsonicSource:
    """Build the runtime source for one preset id.

    Only families with a real adapter are offered; an unknown id raises `ValueError`
    so the API can answer 422 instead of assembling a source that cannot work.
    """
    preset = get_preset(preset_id)
    if preset is None:
        raise ValueError(f"unknown music source: {preset_id!r}")
    if preset.protocol_family != "subsonic":
        raise ValueError(f"music source {preset.id!r} has no runtime adapter yet")
    return SubsonicSource(
        secrets_path, cache_dir, cache_tracks, transport, policy,
        base_url=base_url, user=user, password=password, token=token,
        source_id=preset.id,
    )


# Imported last: navidrome.py re-exports the generic implementation and importing it
# from this package is the original public path.
from .navidrome import NavidromeSource  # noqa: E402
