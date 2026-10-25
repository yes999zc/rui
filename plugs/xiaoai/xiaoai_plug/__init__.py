"""Xiaomi AI Speaker Pro (LX06) plug: the device's own client dials us (tier 2).

Since 0.6 this is an image-shipped Socket & Plug package, not core code: the
manifest in the parent directory is the source of truth and the image copies the
directory to `/opt/rui-plugins/xiaoai` (docs/roadmap-0.6.md M2).
"""
from __future__ import annotations

from .adapter import DEFAULT_ADAPTER, DEFAULT_LISTEN_PORT, XiaoaiAdapter, build

__all__ = ["DEFAULT_ADAPTER", "DEFAULT_LISTEN_PORT", "XiaoaiAdapter", "build"]
