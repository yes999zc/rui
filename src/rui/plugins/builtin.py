"""Built-in Socket & Plug manifests, validated in code rather than read from disk.

Only the LX06 reference plug lives here for now. Keeping the manifest in code means a
fresh install has a working plug before ``data/plugs/`` exists, and the same Pydantic
models validate it as an on-disk ``plugin.yaml``.

Nothing in this module imports or executes ``runtime.entrypoint``; it is a string, as
required by ``docs/socket-plug.md``.
"""
from __future__ import annotations

from pathlib import Path

from .models import (
    DEVICE_SCHEMA,
    PLUGIN_SCHEMA,
    DeviceManifest,
    Plugin,
)

__all__ = ["BUILTIN_PLUGINS", "LX06_PLUG_ID", "builtin_plugin", "builtin_plugins"]

LX06_PLUG_ID = "xiaoai-lx06"
# The device manifest id is also the adapter name used by ``devices[].adapter``.
LX06_ADAPTER = "xiaoai"

_SOURCE_DIR = Path(__file__).resolve().parent

_XIAOAI_LX06 = {
    "schema": DEVICE_SCHEMA,
    "id": LX06_ADAPTER,
    "name": "Xiaomi AI Speaker Pro (LX06)",
    "vendor": "Xiaomi",
    "class": "A",
    "transport": "inbound",
    "protocol": {"kind": "ws", "version": "1"},
    "capabilities": ["mic_array", "reference", "playback", "led", "keys", "volume"],
    "device_side": {
        "artifact": "client",
        "name": "open-xiaoai client",
        "source": "https://github.com/coderzc/open-xiaoai",
        "version_min": "v0.x",
        "install_steps": [
            "Enable SSH on the speaker (see the open-xiaoai README).",
            "Install the open-xiaoai client and confirm it can reconnect.",
        ],
        "rollback": "Stop and uninstall the open-xiaoai client; the stock firmware is untouched.",
    },
    "runtime": {
        "mode": "in-process",
        "entrypoint": "rui.device.xiaoai:build",
        "listen_port": 4412,
        "token_env": "RUI_DEVICE_TOKEN",
    },
    "params": [
        {
            "key": "host",
            "label": "音箱地址",
            "type": "string",
            "required": True,
            "placeholder": "<speaker-ip>",
        },
        {
            "key": "room",
            "label": "房间名",
            "type": "string",
            "default": "客厅",
        },
        {
            "key": "wake_words",
            "label": "唤醒词",
            "type": "list",
            "default": ["你好小瑞"],
            "max": 3,
        },
    ],
    "compatibility": {
        "rui_min": "0.2.0",
        "tested_with": ["0.2.0"],
        "notes": "A-class coexistence with the stock firmware; open-xiaoai client required.",
    },
    "acceptance": {
        "verified": True,
        "last_verified": "2026-10-24",
        "evidence": "Real LX06 wake/playback/yield checks; see the plug README.",
    },
}

_XIAOAI_LX06_PLUGIN = {
    "schema": PLUGIN_SCHEMA,
    "id": LX06_PLUG_ID,
    "name": "Xiaomi AI Speaker Pro (LX06)",
    "version": "0.2.0",
    "rui_compat": ">=0.2.0 <0.3.0",
    "license": "MIT",
    "maintainer": "rui maintainers",
    "homepage": "https://github.com/coderzc/open-xiaoai",
    "trust": "builtin",
}


def _build() -> tuple[Plugin, ...]:
    device = DeviceManifest.model_validate(_XIAOAI_LX06)
    plugin = Plugin.model_validate(
        {**_XIAOAI_LX06_PLUGIN, "devices": [device], "source_dir": _SOURCE_DIR}
    )
    return (plugin,)


BUILTIN_PLUGINS: tuple[Plugin, ...] = _build()


def builtin_plugins() -> list[Plugin]:
    """Every plug compiled into this build, in stable order.

    A fresh list is returned so a caller cannot mutate the registry.
    """
    return list(BUILTIN_PLUGINS)


def builtin_plugin(plugin_id: str) -> Plugin | None:
    """The built-in plug with ``plugin_id``, or None."""
    for plugin in BUILTIN_PLUGINS:
        if plugin.id == plugin_id:
            return plugin
    return None
