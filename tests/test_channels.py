from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx

from house_ear.channels.base import ChannelGateway, ControlClient, Dedupe, Inbound, Reply
from house_ear.channels.telegram import TelegramAdapter
from house_ear.channels.telegram import split_text as tg_split
from house_ear.channels.weixin import AccountStore, parse_aes_key
from house_ear.channels.weixin import split_text as wx_split


def test_dedupe_is_bounded_and_time_aware() -> None:
    d = Dedupe(ttl_s=100, max_items=2)
    assert not d.seen("a")
    assert d.seen("a")
    assert not d.seen("b")
    assert not d.seen("c")
    assert not d.seen("a")  # evicted by the max size, not silently remembered


def test_text_split_keeps_content() -> None:
    text = "第一段。" * 1000
    chunks = tg_split(text)
    assert all(len(chunk) <= 4096 for chunk in chunks)
    assert "".join(chunks) == text
    assert wx_split("短消息") == ["短消息"]
    assert len(wx_split("啊" * 5000)[0]) <= 2000


def test_weixin_aes_key_accepts_both_encodings() -> None:
    raw = bytes.fromhex("00112233445566778899aabbccddeeff")
    assert parse_aes_key(base64.b64encode(raw).decode()) == raw
    assert parse_aes_key(base64.b64encode(raw.hex().encode()).decode()) == raw


class FakeAdapter:
    name = "fake"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.typing: list[str] = []
        self.audio: list[tuple[str, int]] = []

    async def start(self, on_inbound):  # noqa: ANN001
        self.on_inbound = on_inbound

    async def stop(self) -> None:
        pass

    async def send_text(self, peer: str, text: str) -> None:
        self.sent.append((peer, text))

    async def send_audio(self, peer: str, pcm: bytes, rate: int) -> None:
        self.audio.append((peer, len(pcm)))

    async def send_typing(self, peer: str) -> None:
        self.typing.append(peer)


class FakeControl:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.reply = Reply(text="好的")

    async def chat(self, text: str, **kwargs):  # noqa: ANN003
        self.calls.append({"text": text, **kwargs})
        return self.reply

    async def asr(self, pcm: bytes, sample_rate: int = 16000) -> str:
        return "语音转写"


def test_gateway_ignores_unauthorized_and_runs_text() -> None:
    async def run() -> None:
        adapter = FakeAdapter()
        control = FakeControl()
        gw = ChannelGateway(control, allow=lambda _c, peer: peer == "ok")
        gw.add(adapter)
        await gw.start()
        await gw.handle(Inbound(channel="fake", peer_id="bad", message_id="1", text="hi"))
        assert not control.calls
        await gw.handle(Inbound(channel="fake", peer_id="ok", message_id="2", text="hi"))
        assert control.calls[0]["text"] == "hi"
        assert adapter.sent == [("ok", "好的")]
        await gw.stop()

    asyncio.run(run())


def test_gateway_voice_note_calls_asr() -> None:
    async def run() -> None:
        adapter = FakeAdapter()
        control = FakeControl()
        gw = ChannelGateway(control)
        gw.add(adapter)
        await gw.start()
        await gw.handle(Inbound(channel="fake", peer_id="p", message_id="1",
                                audio_pcm=b"\x00\x00" * 160))
        assert control.calls[0]["text"] == "语音转写"
        await gw.stop()

    asyncio.run(run())


def test_control_client_posts_chat_and_decodes_audio() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/chat":
            return httpx.Response(200, json={
                "reply": "hi", "audio_b64": base64.b64encode(b"pcm").decode(),
                "sample_rate": 16000,
            })
        return httpx.Response(200, json={"text": "ok"})

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        client.headers["X-Auth-Token"] = "tok"
        ctl = ControlClient("http://test", "tok")
        await ctl.close()
        ctl._client = client  # test injection
        reply = await ctl.chat("hello", channel="t", peer_id="p", want_audio=True)
        assert reply.text == "hi" and reply.audio_pcm == b"pcm"
        assert seen[0].headers["x-auth-token"] == "tok"
        await ctl.close()

    asyncio.run(run())


def test_telegram_adapter_roundtrip_with_fake_api(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "bot"}})
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(200, json={"ok": True, "result": {}})

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        adapter = TelegramAdapter("token", allowed_chat_ids={"1"},
                                  state_path=tmp_path / "tg.json",
                                  api_base="https://tg.test", client=client)
        await adapter.start(lambda m: _noop(m))
        await asyncio.sleep(0.05)
        await adapter.stop()
        await client.aclose()

    asyncio.run(run())
    assert "getMe" in calls and "getUpdates" in calls


async def _noop(_message: Inbound) -> None:
    pass


def test_account_store_is_0600(tmp_path: Path) -> None:
    store = AccountStore(tmp_path)
    store.save_account({"token": "secret"})
    assert oct(store.account_path.stat().st_mode & 0o777) == "0o600"


def test_weixin_qr_login_and_text_roundtrip(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        if method == "get_bot_qrcode":
            return httpx.Response(200, json={"qrcode": "qr1", "qrcode_img_content": "https://qr"})
        if method == "get_qrcode_status":
            return httpx.Response(200, json={
                "status": "confirmed", "bot_token": "bot-token", "ilink_bot_id": "bot@im.bot",
                "ilink_user_id": "user@im.wechat", "baseurl": "https://wx.test",
            })
        if method == "getupdates":
            return httpx.Response(200, json={
                "ret": 0,
                "msgs": [{
                    "message_id": 1, "from_user_id": "user@im.wechat",
                    "context_token": "ctx",
                    "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
                }],
                "get_updates_buf": "next",
            })
        if method == "sendmessage":
            return httpx.Response(200, json={"ret": 0})
        return httpx.Response(200, json={"ret": 0})

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        from house_ear.channels.weixin import WeixinAdapter
        wx = WeixinAdapter(tmp_path / "wx", allowed_user_ids={"user@im.wechat"},
                           client=client, base_url="https://wx.test")
        account = await wx.qr_login()
        wx.save_login(account)
        seen: list[Inbound] = []

        async def got(msg: Inbound) -> None:
            seen.append(msg)

        await wx.start(got)
        await asyncio.sleep(0.1)
        await wx.stop()
        assert seen and seen[0].text == "你好"
        assert wx.context_tokens["user@im.wechat"] == "ctx"
        await wx.send_text("user@im.wechat", "好的")
        assert "sendmessage" in calls
        await client.aclose()
        assert oct((tmp_path / "wx" / "account.json").stat().st_mode & 0o777) == "0o600"

    asyncio.run(run())
