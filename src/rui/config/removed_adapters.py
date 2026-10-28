"""Adapter spellings removed from the core in 0.8.0, and the sentence that replaces them.

0.7.x read a config that still said ``adapter: xiaoai`` and rewrote it to the plug
manifest's device id on load, with a deprecation warning. 0.8.0 stops rewriting: a
removed spelling is a config error at load time, and the message names the exact
replacement so an operator can fix the file without reading the source.

This module is the only place in the core that names a speaker model. That is
deliberate: the fix sentence has to name the old and the new spelling to be
actionable, and keeping it in one small module means the kernel-decoupling guard
can allow exactly this file (see ``tests/test_kernel_decoupling.py``). It is not a
migration table - there is no read-time rewrite anywhere in 0.8.0.

The same helper guards both ends of the file's life: ``store.migrate`` rejects a
removed spelling on load, and the console write invariant (``device_write``) plus
``store.save_config`` reject it before a write can persist a file the next boot
would refuse.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: Removed spelling -> the plug manifest's device id that replaces it.
REMOVED_ADAPTERS: dict[str, str] = {"xiaoai": "lx06"}


def _text(device: Any, key: str) -> str:
    """One raw field from a config mapping or a validated ``DeviceConfig``."""
    if isinstance(device, Mapping):
        value = device.get(key)
    else:
        value = getattr(device, key, None)
    return str(value or "").strip()


def removed_adapter_error(index: int, device: Any) -> str | None:
    """The 0.8.0 rejection for one ``devices[]`` entry, or None when it is current.

    ``index`` is the position in the YAML list and ``device`` is the raw mapping
    (or a validated ``DeviceConfig``), so the message can use the same path the
    operator sees in their file (``devices[0] (Lab)``). The caller raises this
    before Pydantic validation on load, and before the file is written on a console
    write, which also keeps a removed spelling from being accepted as an arbitrary
    adapter name.
    """
    old = _text(device, "adapter")
    new = REMOVED_ADAPTERS.get(old)
    if new is None:
        return None
    room = _text(device, "room")
    where = f"devices[{index}]" + (f" ({room})" if room else "")
    return (
        f"{where}: adapter {old!r} is no longer accepted in 0.8.0; "
        f"change devices[].adapter to {new!r}"
    )


def removed_adapters_error(devices: Iterable[Any]) -> tuple[int, str] | None:
    """The first removed spelling in a candidate ``devices[]``, with its index."""
    for index, device in enumerate(devices):
        message = removed_adapter_error(index, device)
        if message is not None:
            return index, message
    return None
