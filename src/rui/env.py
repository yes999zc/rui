"""Environment variable lookup with one rename-friendly rule.

The project used to be called ``house-ear`` and every variable carried a
``HOUSE_EAR_`` prefix. The canonical namespace is now ``RUI_``. A deployment that
upgrades in place still has the old names in its shell, compose file or ``.env``,
so every read goes through :func:`get`: the ``RUI_*`` spelling wins and the
``HOUSE_EAR_*`` spelling is a fallback. This is a bridge for one rename, not a
general alias mechanism; new code must use ``RUI_*`` only.
"""
from __future__ import annotations

import os

CANONICAL_PREFIX = "RUI_"
LEGACY_PREFIX = "HOUSE_EAR_"


def canonical_name(name: str) -> str:
    """The canonical spelling of a variable name.

    A legacy name (``HOUSE_EAR_LLM_KEY``) is translated to its ``RUI_`` equivalent;
    names outside the namespace (``DEEPSEEK_API_KEY``) are returned unchanged.
    """
    name = (name or "").strip()
    if name.startswith(LEGACY_PREFIX):
        return CANONICAL_PREFIX + name[len(LEGACY_PREFIX):]
    return name


def legacy_name(name: str) -> str:
    """The legacy spelling for a canonical name, or ``""`` when there is none."""
    canonical = canonical_name(name)
    if canonical.startswith(CANONICAL_PREFIX):
        return LEGACY_PREFIX + canonical[len(CANONICAL_PREFIX):]
    return ""


def names(name: str) -> tuple[str, ...]:
    """Candidates in lookup order: canonical first, legacy second."""
    canonical = canonical_name(name)
    legacy = legacy_name(canonical)
    return (canonical, legacy) if legacy else (canonical,)


def get(name: str, default: str = "") -> str:
    """Value of ``name`` with ``RUI_*`` before ``HOUSE_EAR_*``; ``default`` if unset."""
    for candidate in names(name):
        value = os.environ.get(candidate)
        if value is not None:
            return value
    return default


def is_set(name: str) -> bool:
    """Whether either spelling of ``name`` exists in the environment."""
    return any(candidate in os.environ for candidate in names(name))
