"""Built-in Socket & Plug manifests, validated in code rather than read from disk.

Only the LX06 reference plug lives here for now. Keeping the manifest in code means a
fresh install has a working plug before ``data/plugs/`` exists, and the same Pydantic
models validate it as an on-disk ``plugin.yaml``.

Nothing in this module imports or executes ``runtime.entrypoint``; it is a string, as
required by ``docs/socket-plug.md``.

The LX06 model is mirrored as the on-disk reference plug in
``plugins/rui-plugin-xiaoai/`` (v0, RUI KWS). That package is for contract testing,
not a runtime path: this in-code registry stays the fresh-install default until the
0.4 extraction. ``tests/test_reference_plugin.py`` pins capability/param parity so
the two copies cannot drift silently.
"""
from __future__ import annotations

from pathlib import Path

import rui

from .models import (
    DEVICE_SCHEMA_V1,
    PLUGIN_SCHEMA,
    DeviceManifest,
    Plugin,
)
from .semver import parse_range

__all__ = ["BUILTIN_PLUGINS", "LX06_PLUG_ID", "builtin_plugin", "builtin_plugins"]

LX06_PLUG_ID = "xiaoai-lx06"
# The device manifest id is also the adapter name used by ``devices[].adapter``.
LX06_ADAPTER = "xiaoai"

_SOURCE_DIR = Path(__file__).resolve().parent

_XIAOAI_LX06 = {
    # 0.6: the built-in LX06 manifest is rui-device/v1 so it can declare the hardware
    # defaults the core no longer hard-codes (docs/roadmap-0.6.md M1). It still wakes
    # through RUI KWS: no `wake` block means `source: rui` by the v1 default, and the
    # manifest never declares device_wake / barge_via_device.
    "schema": DEVICE_SCHEMA_V1,
    "id": LX06_ADAPTER,
    "name": "Xiaomi AI Speaker Pro (LX06)",
    "vendor": "Xiaomi",
    "class": "A",
    "transport": "inbound",
    "protocol": {"kind": "ws", "version": "1"},
    "capabilities": ["mic_array", "reference", "playback", "led", "keys", "volume"],
    # What the core used to bake into CaptureConfig (0.5 and earlier): S32_LE, 8
    # interleaved channels at 48 kHz, ch0..ch5 mics, ch6/ch7 playback reference, and
    # the measured shifts. This is the bottom layer of the audio merge: a config that
    # states its own values (the production template asks the client for 16 kHz to save
    # wifi bandwidth) keeps them.
    "audio_defaults": {
        "capture": {
            "rate": 48000,
            "channels": 8,
            "mic_channels": 6,
            "ref_channels": 2,
            "bits": 32,
            "mic_shift": 12,
            "ref_shift": 16,
        },
        # The LX06's measured own-cue echo tail (AckConfig documents 1200 ms).
        "ack_echo_ignore_ms": 1200,
    },
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
        "rui_min": "0.3.0",
        "tested_with": ["0.3.2", "0.5.3"],
        # Socket & Plug v1: the LX06 is a closed device (open-xiaoai client only), so
        # there is no device-side RUI wake model. Omitting `wake` means
        # `wake.source: rui`; the manifest must never declare device_wake.
        "notes": "A-class coexistence with the stock firmware; open-xiaoai client required. "
                 "Wake is RUI KWS (v1 default when wake is omitted), not device_wake. "
                 "audio_defaults declares the 8 ch / 6 mic / 2 ref / 48 kHz layout.",
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
    # v1 matrix (docs/socket-plug-v1.md §7): a rui-device/v1 manifest is only loadable
    # from 0.3.0 on, so the range floor moves with the schema. The builtins bypass
    # ``load_plugin``'s compat check, so this is also asserted in ``_build`` below and
    # in tests/test_plugins.py.
    "rui_compat": ">=0.3.0 <0.6.0",
    "license": "MIT",
    "maintainer": "rui maintainers",
    "homepage": "https://github.com/coderzc/open-xiaoai",
    "trust": "builtin",
}


def _build() -> tuple[Plugin, ...]:
    # A builtin bypasses load_plugin()'s compat gate, so check its own range here: an
    # image whose manifest excludes its own version is a release bug, not a warning.
    if not parse_range(_XIAOAI_LX06_PLUGIN["rui_compat"]).matches(rui.__version__):
        raise RuntimeError(
            f"builtin plug {_XIAOAI_LX06_PLUGIN['id']!r} declares rui_compat "
            f"{_XIAOAI_LX06_PLUGIN['rui_compat']!r}, which excludes this build "
            f"({rui.__version__})"
        )
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
