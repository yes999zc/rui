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
            "记下音箱的局域网 IP，并在路由器里给它做 DHCP 固定租约：IP 一变，SSH 和客户端的回连地址都会失效。",
            "按 open-xiaoai 的刷机/root 说明打开音箱 SSH，记录 root 密码和登录方式"
            "（通常是 ssh root@<speaker-ip>）。",
            "安装 open-xiaoai client，记下它的安装路径（常见为 /data/open-xiaoai/）"
            "以及保存连接配置的文件或启动脚本（server.txt、start.sh 里的 WS_URL 等）。",
            "在 client 配置里填 ws://<server-lan-ip>:4412/?token=<device-token>："
            "device token 由本向导的 connect 步骤给出，服务端和客户端必须用同一个值。",
            "重启 client（重跑启动脚本或重启音箱），在 client 日志里确认出现 "
            "connected / 已连接 ws://...；日志路径或查看命令也要记下来。",
            "回到向导 connect 步骤点「重试」，speaker_connected 变成 true 才继续；"
            "若 client 日志是 401 / close 1008，先核对两边的 token。",
            "记下回滚方法：停止并移除 client、恢复原启动脚本；原厂固件/分区不动。",
        ],
        "rollback": "Stop and remove the open-xiaoai client, restore the stock boot script; "
                    "the stock firmware and partitions are untouched.",
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
