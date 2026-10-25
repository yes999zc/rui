"""The read-only, redacted YAML view of the effective config (docs/console.md §12.2c).

`/panel/system` shows what the process is *actually running with*: the schema defaults
merged with the values from `config.yaml`. The document is built from
`Config.model_dump(mode="json")`, so it needs no second parser and cannot drift from the
schema. Two rules keep it safe to ship:

1. Credential-shaped values never leave the server. Every non-empty string under a
   secret-looking name is replaced by `REDACTED` and its dotted path is reported in
   `redacted_keys`, so the page can say exactly what it hid instead of leaving a gap.
2. `*_env` slots are *names*, not values (`RUI_LLM_KEY`, `RUI_HA_TOKEN`). They are kept
   verbatim: pointing at an environment variable is not a secret, and the console needs
   the name to explain which key is missing.

The legacy inline music credentials (`music.url/user/password/token`) are
`Field(exclude=True)` in the schema, i.e. absent from every dump by design. They are
normally migrated to the 0600 credentials JSON and cleared at startup; if a migration
failed, the values are still effective, so the view re-adds them as placeholders rather
than pretend they do not exist. Values are still redacted by the same walk.
"""
from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from ...config.schema import Config

REDACTED = "<redacted>"

# A hard bound on the response body. A normal config is a few KB; the cap only exists
# so a pathological free-text field (a 10 MB `assistant.persona`) cannot turn a console
# request into a large download. Over the limit the view answers a valid YAML document
# that says so instead of a truncated (unparseable) one.
MAX_YAML_CHARS = 200_000

# Names that look credential-shaped after normalisation. `auth` is here for
# `web.auth_token` / `Authorization`; `authorization` is explicit because the word
# itself does not currently contain a segment boundary. Matching is on whole
# underscore-separated segments, so `author`, `authority` or `secrets_path` do not
# match while `api_key`, `X-Api-Key` and `DEEPSEEK_API_KEY` do.
_SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:password|passwd|secret|token|api_?key|access_?key|private_?key|"
    r"credential|authorization|bearer|auth)(?:_|$)"
)

# Credential *references* rather than values: an env var name, a file path, or a
# presence flag. Redacting these would hide the one thing the page must explain.
_NON_SECRET_KEYS = frozenset({
    "token_env", "api_key_env", "app_id_env", "api_secret_env", "password_env",
    "secrets_path", "has_key", "has_device_token", "token_configured", "auth_required",
})

# Legacy inline music credential slots (schema `exclude=True`); see the module docstring.
_LEGACY_MUSIC_KEYS = ("url", "user", "password", "token")

_yaml = YAML()
_yaml.default_flow_style = False
_yaml.width = 120


def _is_secret_name(name: str) -> bool:
    key = str(name).strip().lower()
    if not key or key in _NON_SECRET_KEYS:
        return False
    if key.endswith(("_env", "_path", "_file")):
        return False
    normalised = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return bool(_SECRET_NAME_RE.search(normalised))


def redact_secrets(data: Any) -> tuple[Any, list[str]]:
    """Replace credential values in a config dump; returns (clean data, dotted paths).

    Works on the JSON-mode dump only (str/int/bool/None/dict/list), so there is no
    model-specific knowledge here: a field added to the schema later is covered as soon
    as its name looks like a credential. Only non-empty strings are replaced - an empty
    `auth_token` means "no token", and marking it `<redacted>` would claim otherwise.
    """
    redacted: list[str] = []

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            out: dict[Any, Any] = {}
            for raw_key, value in node.items():
                key = str(raw_key)
                child = f"{path}.{key}" if path else key
                if isinstance(value, str) and value and _is_secret_name(key):
                    out[raw_key] = REDACTED
                    redacted.append(child)
                else:
                    out[raw_key] = walk(value, child)
            return out
        if isinstance(node, list):
            return [walk(item, f"{path}[{index}]") for index, item in enumerate(node)]
        return node

    return walk(data, ""), sorted(redacted)


def _with_legacy_music_credentials(cfg: Config, data: dict[str, Any]) -> None:
    """Put still-effective inline music credentials back into the dump, unredacted.

    They are excluded from `model_dump` because the schema wants plaintext out of every
    serialisation; the redaction walk is what makes showing them safe. Doing it here also
    means the caller reports `music.password`/`music.token` in `redacted_keys`, which is
    honest: an old config still carrying them is a fact the operator should see.
    """
    music = getattr(cfg, "music", None)
    dumped = data.get("music")
    if music is None or not isinstance(dumped, dict):
        return
    for key in _LEGACY_MUSIC_KEYS:
        value = str(getattr(music, key, "") or "").strip()
        if value and not str(dumped.get(key) or "").strip():
            dumped[key] = value


def _dump_yaml(data: Any) -> str:
    stream = StringIO()
    _yaml.dump(data, stream)
    return stream.getvalue()


def _bounded_yaml(data: Any) -> tuple[str, bool]:
    """Serialise to YAML within MAX_YAML_CHARS; (text, truncated)."""
    text = _dump_yaml(data)
    if len(text) <= MAX_YAML_CHARS:
        return text, False
    # A truncated YAML document would not parse, and a half-document is worse than an
    # honest refusal: keep the answer valid and name the limit.
    notice = (
        "# The effective config serialises to "
        f"{len(text)} characters, more than the {MAX_YAML_CHARS}-character view limit.\n"
        "# It was not sent. Large free-text fields (for example assistant.persona) are\n"
        "# the usual reason; the console never edits this view.\n"
        "truncated: true\n"
    )
    return notice, True


def config_raw_document(cfg: Config, config_path: Path | str | None) -> dict:
    """The `/api/v1/config/raw` payload: {path, mtime, yaml, redacted_keys, truncated}."""
    data = cfg.model_dump(mode="json")
    _with_legacy_music_credentials(cfg, data)
    data, redacted_keys = redact_secrets(data)
    yaml_text, truncated = _bounded_yaml(data)
    path = Path(config_path) if config_path else None
    mtime = 0.0
    if path is not None:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            # A config that was loaded from a file which has since been removed still
            # has no viewable origin; the YAML above is the in-memory truth.
            mtime = 0.0
    return {
        "path": str(path) if path is not None else "",
        "mtime": mtime,
        "yaml": yaml_text,
        "redacted_keys": redacted_keys,
        "truncated": truncated,
    }
