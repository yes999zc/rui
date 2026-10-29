"""Music source adapters and the preset-driven factory."""
from __future__ import annotations

from pathlib import Path

from ..source import MusicSource
from .external import (
    SourceContext,
    load_external_source,
    lookup_external,
    parse_registry,
)
from .jellyfin import JellyfinSource
from .presets import PRESETS, MusicSourcePreset, get_preset, preset_ids, presets
from .subsonic import SubsonicSource

__all__ = [
    "JellyfinSource",
    "MusicSourcePreset",
    "NavidromeSource",
    "PRESETS",
    "SourceContext",
    "SubsonicSource",
    "build_source",
    "get_preset",
    "load_external_source",
    "lookup_external",
    "parse_registry",
    "preset_ids",
    "presets",
]


def build_source(preset_id: str, secrets_path: Path | str | None, cache_dir: Path,
                 cache_tracks: int = 8, transport=None, policy=None, *,
                 base_url: str = "", user: str = "", password: str = "",
                 token: str = "", api_key: str = "") -> MusicSource:
    """Build the runtime source for one preset id, or a registered external id.

    Built-in presets win over the environment registry, so an external entry can
    never silently replace Navidrome/Jellyfin. An id in neither place raises
    `ValueError` so the API can answer 422 instead of assembling a source that cannot
    work. The explicit `base_url`/`user`/`password`/`token`/`api_key` arguments are
    the console's fixed-connection mode (unsaved draft credentials), never file mode.

    External sources are named by `RUI_MUSIC_SOURCES` (`id=module:factory`); the
    factory receives a read-only `SourceContext` and its result is validated against
    the five-method interface and the `music_sources_compat` version window. Any
    failure is a `ValueError` naming the source id and the original exception.
    """
    preset = get_preset(preset_id)
    if preset is None:
        spec = lookup_external(preset_id)
        if spec is None:
            raise ValueError(f"unknown music source: {preset_id!r}")
        context = SourceContext(
            # The registry is matched case-insensitively; hand the source the same
            # canonical id `music.source` is compared against.
            source_id=spec.source_id.strip().lower(),
            secrets_path=(Path(secrets_path).expanduser() if secrets_path else None),
            cache_dir=Path(cache_dir).expanduser(),
            cache_tracks=cache_tracks,
            policy=policy,
            base_url=base_url,
            user=user,
            password=password,
            token=token,
            api_key=api_key,
        )
        return load_external_source(spec, context)
    if preset.protocol_family == "jellyfin":
        return JellyfinSource(
            secrets_path, cache_dir, cache_tracks, transport, policy,
            base_url=base_url, api_key=api_key, auth_style=preset.auth_style,
            source_id=preset.id,
        )
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
