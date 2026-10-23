"""Run the Telegram / WeChat channel gateway as a separate process."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx

from .base import ChannelGateway, ControlClient
from .telegram import TelegramAdapter
from .weixin import WeixinAdapter

log = logging.getLogger(__name__)


def _split_ids(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


async def _login_weixin(data_dir: Path) -> int:
    adapter = WeixinAdapter(data_dir)
    def show(qrcode: str, image: str) -> None:
        print("用微信扫描下面的二维码登录（二维码内容）：", flush=True)
        if image:
            print(image, flush=True)
        else:
            print(qrcode, flush=True)
    try:
        account = await adapter.qr_login(on_qr=show)
        adapter.save_login(account)
        print(f"登录成功：{account.get('accountId')} -> {data_dir / 'account.json'}", flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"微信登录失败：{exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        await adapter.stop()


def _read_channel_config(path: str) -> dict:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001 - a missing config means "use env flags"
        return {}
    return data if isinstance(data, dict) else {}


async def _run(args: argparse.Namespace) -> int:
    control = ControlClient(args.control_url, args.control_token)
    saved = _read_channel_config(args.channels_config)
    tg_cfg = saved.get("telegram") if isinstance(saved.get("telegram"), dict) else {}
    wx_cfg = saved.get("weixin") if isinstance(saved.get("weixin"), dict) else {}
    reply_mode = args.reply_mode
    if reply_mode == "text" and isinstance(tg_cfg, dict) and tg_cfg.get("reply_mode") in {"text", "voice", "both"}:
        reply_mode = str(tg_cfg["reply_mode"])
    gateway = ChannelGateway(control, reply_mode=reply_mode)
    adapters: list[object] = []
    tg_token = args.telegram_token
    tg_enabled = bool(tg_cfg.get("enabled", True)) if tg_cfg else bool(args.telegram_token)
    if tg_token and tg_enabled:
        allow = ",".join(str(x) for x in tg_cfg.get("allowed_chat_ids", [])) or args.telegram_allow
        tg = TelegramAdapter(
            tg_token,
            allowed_chat_ids=_split_ids(allow),
            state_path=Path(args.data_dir) / "telegram.json",
        )
        gateway.add(tg)
        adapters.append(tg)
    wx_data = args.weixin_data or (str(wx_cfg.get("data_dir") or "") if wx_cfg else "")
    wx_enabled = bool(wx_cfg.get("enabled", False)) if wx_cfg else bool(args.weixin_data)
    if wx_data and wx_enabled:
        allow = ",".join(str(x) for x in wx_cfg.get("allowed_user_ids", [])) or args.weixin_allow
        wx = WeixinAdapter(Path(wx_data), allowed_user_ids=_split_ids(allow))
        gateway.add(wx)
        adapters.append(wx)
    if not adapters:
        print("没有启用任何 channel：至少给 --telegram-token 或 --weixin-data", file=sys.stderr)
        await control.close()
        return 2
    try:
        await gateway.start()
        print("channel gateway 已启动；Ctrl-C 退出", flush=True)
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await gateway.stop()
        await control.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="house-ear-channels")
    parser.add_argument("--control-url", default=os.getenv("HOUSE_EAR_CONTROL_URL",
                                                           "http://127.0.0.1:8080"))
    parser.add_argument("--control-token", default=os.getenv("HOUSE_EAR_CONSOLE_TOKEN", ""))
    parser.add_argument("--data-dir", default=os.getenv("HOUSE_EAR_CHANNEL_DATA",
                                                        str(Path.home() / ".house-ear" / "channels")))
    parser.add_argument("--telegram-token", default=os.getenv("HOUSE_EAR_TELEGRAM_TOKEN", ""))
    parser.add_argument("--telegram-allow", default=os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", ""))
    parser.add_argument("--weixin-data", default=os.getenv("HOUSE_EAR_WEIXIN_DATA", ""))
    parser.add_argument("--weixin-allow", default=os.getenv("WEIXIN_ALLOWED_USER_IDS", ""))
    parser.add_argument("--channels-config", default=os.getenv("HOUSE_EAR_CHANNELS_CONFIG", ""))
    parser.add_argument("--reply-mode", choices=("text", "voice", "both"), default="text")
    parser.add_argument("--login-weixin", action="store_true",
                        help="only run the WeChat QR login and save credentials")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.login_weixin:
        data = Path(args.weixin_data or args.data_dir) / "weixin"
        return asyncio.run(_login_weixin(data))
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 0
    except httpx.HTTPError as exc:
        print(f"channel gateway HTTP error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
