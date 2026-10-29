"""D8: the v2 notification-channels page and the API surface it drives.

The v2 console has no JS test runner (and this repo does not add one), so the
view half mirrors `tests/test_devices_view_grid.py`: read the web source and
assert the contract the page must keep — both cards render from one GET, saving
goes through PUT then a GET read-back, allowlist ids are edited one row at a
time, token/account state is read-only, and the 503/422 copy exists in both
languages. `npm/pnpm run typecheck && build` remain the executable frontend
gates.

The API half reuses the `create_app` + `TestClient` fixture style of
`tests/test_api_v2_write.py` and pins what the page relies on: PUT then GET
read-back, `channels.json` at 0600, 422 validation, and the 503 when the control
plane has no channel supervisor.
"""
from __future__ import annotations

import json
import re
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui.config import Config, save_config
from rui.web.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "web/src/v2/views/ChannelsView.vue"
API_SRC = ROOT / "web/src/v2/api.ts"
ROUTER = ROOT / "web/src/v2/router.ts"
APP = ROOT / "web/src/v2/App.vue"
I18N = ROOT / "web/src/v2/i18n.ts"
STYLE = ROOT / "web/src/v2/style.css"
DOT2_DOC = ROOT / "docs/analysis/dot2-data-plane-1011.md"

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture()
def console(tmp_path, monkeypatch):
    """A TestClient over a fresh config; channel endpoints never need the lifespan."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("RUI_TELEGRAM_TOKEN", raising=False)

    def build() -> tuple[TestClient, Config]:
        config = Config(web={"auth_token": TOKEN}, devices=[])
        config.system.data_dir = str(tmp_path / "data")
        config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
        path = tmp_path / "config.yaml"
        save_config(path, config)
        return TestClient(create_app(config, config_path=path)), config

    return build


def _channels_file(config: Config) -> Path:
    return Path(config.system.data_dir) / "channels.json"


# — view source contract ———————————————————————————————————


def test_one_get_renders_two_channel_cards_and_the_gateway_card() -> None:
    view = _read(VIEW)

    # A single resource GET; both cards come from one list, never one request each.
    assert view.count("api.channels()") == 1
    assert "v-for=\"card in cards\"" in view
    assert view.count("class=\"v2-card v2-channel-card\"") == 1

    cards = view[view.index("const cards: ChannelCard[]"):view.index("const resource")]
    assert "name: 'telegram'" in cards
    assert "name: 'weixin'" in cards
    assert "testid: 'channels-telegram'" in cards
    assert "testid: 'channels-weixin'" in cards

    # Gateway is its own card with the three managed actions.
    assert 'data-testid="channels-gateway"' in view
    for action in ("start", "stop", "restart"):
        assert f'data-testid="channels-gateway-{action}"' in view


def test_save_puts_then_reads_back_and_compares() -> None:
    view = _read(VIEW)
    api = _read(API_SRC)

    assert "api.saveChannels(body)" in view
    save_at = view.index("await api.saveChannels(body)")
    readback_at = view.index("await resource.load()", save_at)
    assert save_at < readback_at
    # The read-back decides the copy; the PUT echo alone is not "saved".
    assert "matchesExpected(next, name, expected)" in view
    assert "v2.channels.savedVerified" in view
    assert "v2.channels.savedMismatch" in view

    # Only the two allowed top-level keys, and only the editable fields.
    for field in ("allowed_chat_ids", "allowed_user_ids", "reply_mode", "enabled"):
        assert field in view
    assert "saveChannels: (patch: ChannelsPatch)" in api
    assert "method: 'PUT'" in api
    assert "'/api/v1/channels'" in api


def test_allowlist_ids_are_rows_that_add_trim_and_remove() -> None:
    view = _read(VIEW)

    assert "function addId(name: ChannelName)" in view
    assert "drafts[name].ids.push('')" in view
    assert "function removeId(name: ChannelName, index: number)" in view
    assert "drafts[name].ids.splice(index, 1)" in view
    assert "drafts[name].ids[index] = (event.target as HTMLInputElement).value" in view
    # Blank rows are allowed in the draft; the payload trims and drops them.
    assert "map((id) => id.trim()).filter(Boolean)" in view
    assert "v-for=\"(id, index) in drafts[card.name].ids\"" in view
    assert "-add-id`" in view
    assert "-remove-" in view


def test_token_and_wechat_account_state_are_read_only() -> None:
    view = _read(VIEW)

    # Telegram: presence only, no token input, with the env/secrets hint.
    assert "state.telegram.token_configured" in view
    assert "v2.channels.telegram.tokenConfigured" in view
    assert "v2.channels.telegram.tokenMissing" in view
    assert "v2.channels.telegram.tokenMissingHint" in view
    assert "TELEGRAM_BOT_TOKEN" in _read(I18N)
    assert "v-model=\"token" not in view
    assert "type=\"password\"" not in view

    # WeChat: login status + account ids, "未登录" when the account is absent.
    assert "state.weixin.logged_in" in view
    assert "state.weixin.account_id" in view
    assert "state.weixin.user_id" in view
    assert "v2.channels.weixin.notLoggedIn" in view
    assert "v2.channels.weixin.noAccount" in view


def test_error_copy_splits_503_by_context_and_covers_422_and_network() -> None:
    view = _read(VIEW)
    i18n = _read(I18N)

    # 503 has two contexts: gateway actions say "not ready", channels load/save
    # say "service unavailable". A single blanket 503 copy is the bug this guards.
    assert "type ErrorScope = 'gateway' | 'service'" in view
    assert "scope === 'gateway'" in view
    assert "t('v2.channels.gateway.error503')" in view
    assert "t('v2.channels.serviceUnavailable')" in view
    assert "errorText(gatewayError, 'gateway')" in view
    assert "errorText(loadErrorCopy, 'service')" in view
    assert "errorText(copy, 'service')" in view
    assert "if (copy.status === 503) return" not in view
    assert "'v2.channels.serviceUnavailable': '服务暂不可用（503），请稍后重试。'" in i18n
    assert (
        "'v2.channels.serviceUnavailable': 'Service unavailable (503); try again later.'"
        in i18n
    )
    # `saveFailed` was defined but unused; it is now the save-failure title.
    assert "t('v2.channels.saveFailed')" in view

    assert "copy.status === 422" in view
    assert "v2.channels.error422" in view
    assert "{ detail: copy.detail || copy.title }" in view
    # Network/unknown failures go through the shared translator, which owns the
    # "无法连接服务" fallback; the view must not swallow them into a bare String(err).
    assert "describeError(" in view
    assert "'v2.error.network': '无法连接服务'" in i18n
    assert "'v2.error.network': 'Cannot reach the service'" in i18n
    # The gateway 503 copy names the supervisor/gateway, not a generic failure.
    assert "supervisor" in i18n
    assert "503" in i18n


def test_route_nav_and_both_locales_are_wired() -> None:
    router = _read(ROUTER)
    app = _read(APP)
    i18n = _read(I18N)
    view = _read(VIEW)

    assert "path: '/channels'" in router
    assert "import('./views/ChannelsView.vue')" in router
    # B1 removed the legacy console; the page must not resurrect its route.
    assert "/legacy" not in router

    subnav = app[app.index("const configSubnav"):app.index("const section")]
    assert "{ key: 'channels', to: '/channels', labelKey: 'v2.nav.channels' }" in subnav
    assert "'v2.nav.channels': '通知通道'" in i18n
    assert "'v2.nav.channels': 'Channels'" in i18n
    assert "route.path.startsWith('/channels')" in app
    assert "t('v2.nav.channels')" in app

    # Every v2.channels.* key the view uses exists in BOTH dictionaries; the
    # `en: Record<MsgKey, string>` type keeps typecheck honest about the rest.
    used = sorted(set(re.findall(r"'(v2\.channels\.[a-zA-Z0-9._]+)'", view)))
    assert used
    for key in used:
        assert i18n.count(f"'{key}':") == 2, key


def test_page_uses_v2_card_grid_style_not_a_new_design_system() -> None:
    view = _read(VIEW)
    css = _read(STYLE)

    assert 'class="v2-card v2-channel-card"' in view
    assert 'class="field-row"' in view
    assert "class=\"switch\"" in view
    assert "class=\"v2-channels-grid\"" in view
    assert ".v2-channels-grid {" in css
    marker = css.index(".v2-channels-grid {")
    phone = css[css.index("@media (max-width: 900px)", marker):]
    phone = phone[:phone.index("}") + 1]
    assert ".v2-channels-grid" in phone
    assert "grid-template-columns: minmax(0, 1fr);" in phone


def test_dot2_analysis_example_is_one_real_event_from_table_12() -> None:
    """The §10 example must not splice rx/tx from one event with another's count."""
    doc = _read(DOT2_DOC)

    assert "| 12 | 12:16:08 | 300.0 | 9329320 | 0 | 305.1 |" in doc
    assert "rx=9329320B tx=0B close#12 interval=305.1s dropped=0; data plane down" in doc
    assert "数值取自 §2.2 表 **#12**" in doc
    # The old example mixed §2.1 event #1's rx/tx with a different close count.
    assert "rx=5597592B tx=356440B close#7" not in doc
    # The field explanation (close#N = this process's N-th counted close) stayed.
    assert "本进程启动以来第 N 次" in doc


# — API behaviour (the fixture is the real app, no mocks) —————————


def test_get_defaults_shape(console) -> None:
    client, config = console()
    response = client.get("/api/v1/channels", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["telegram"] == {
        "enabled": False,
        "allowed_chat_ids": [],
        "reply_mode": "text",
        "token_configured": False,
    }
    assert body["weixin"]["enabled"] is False
    assert body["weixin"]["allowed_user_ids"] == []
    assert body["weixin"]["reply_mode"] == "text"
    assert body["weixin"]["data_dir"] == str(
        Path(config.system.data_dir) / "channels" / "weixin"
    )
    assert body["weixin"]["logged_in"] is False
    assert body["gateway"]["command"] == "rui-channels"
    assert body["gateway"]["config_path"] == str(_channels_file(config))
    assert body["gateway"]["status"]["running"] is False


def test_put_then_get_reads_back_and_the_file_is_0600(console) -> None:
    client, config = console()
    put = client.put(
        "/api/v1/channels",
        headers=AUTH,
        json={
            "telegram": {
                "enabled": True,
                "allowed_chat_ids": [" 111 ", "222", "  "],
                "reply_mode": "both",
            }
        },
    )
    assert put.status_code == 200, put.text
    # The server trims and drops blank ids before storing.
    assert put.json()["telegram"]["allowed_chat_ids"] == ["111", "222"]

    fresh = client.get("/api/v1/channels", headers=AUTH).json()
    assert fresh["telegram"] == put.json()["telegram"]
    assert fresh["weixin"] == put.json()["weixin"]

    path = _channels_file(config)
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved) == {"telegram", "weixin"}
    assert saved["telegram"]["enabled"] is True
    assert saved["telegram"]["allowed_chat_ids"] == ["111", "222"]
    assert saved["telegram"]["reply_mode"] == "both"
    # Saving one channel must not wipe the other channel's stored state.
    assert saved["weixin"]["enabled"] is False

    second = client.put(
        "/api/v1/channels",
        headers=AUTH,
        json={
            "weixin": {
                "enabled": True,
                "allowed_user_ids": ["wx-user-1"],
                "reply_mode": "voice",
            }
        },
    )
    assert second.status_code == 200, second.text
    merged = client.get("/api/v1/channels", headers=AUTH).json()
    assert merged["weixin"]["allowed_user_ids"] == ["wx-user-1"]
    assert merged["weixin"]["reply_mode"] == "voice"
    assert merged["telegram"]["allowed_chat_ids"] == ["111", "222"]


@pytest.mark.parametrize(
    "body",
    [
        {"telegram": {"reply_mode": "video"}},
        {"weixin": {"reply_mode": "sound"}},
        {"telegram": {"allowed_chat_ids": "123"}},
        {"telegram": {"allowed_chat_ids": [123]}},
        {"weixin": {"allowed_user_ids": [1]}},
        {"telegram": []},
        {"token": "not-allowed-here"},
    ],
)
def test_put_rejects_invalid_bodies_with_422(console, body) -> None:
    client, config = console()
    response = client.put("/api/v1/channels", headers=AUTH, json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]
    # Nothing is written when validation fails.
    assert not _channels_file(config).exists()


def test_gateway_returns_503_when_the_supervisor_is_missing(console) -> None:
    client, _ = console()
    client.app.state.channel_supervisor = None
    for action in ("start", "stop", "restart"):
        response = client.post(f"/api/v1/channels/gateway/{action}", headers=AUTH)
        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "channel supervisor is not ready"
    # With no supervisor the GET still works and reports an unknown status.
    assert client.get("/api/v1/channels", headers=AUTH).json()["gateway"]["status"] is None


def test_gateway_actions_are_safe_without_an_enabled_channel(console) -> None:
    client, _ = console()
    started = client.post("/api/v1/channels/gateway/start", headers=AUTH)
    assert started.status_code == 200, started.text
    body = started.json()
    # Nothing is enabled, so the supervisor refuses to spawn instead of failing.
    assert body["running"] is False
    assert body["last_error"] == "no channel is enabled"

    stopped = client.post("/api/v1/channels/gateway/stop", headers=AUTH)
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False

    restarted = client.post("/api/v1/channels/gateway/restart", headers=AUTH)
    assert restarted.status_code == 200
    assert restarted.json()["running"] is False
    assert client.get("/api/v1/channels", headers=AUTH).json()["gateway"]["status"][
        "running"
    ] is False


def test_token_presence_is_reported_but_the_value_never_leaves(console, monkeypatch) -> None:
    token_value = "123456:telegram-secret-value"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token_value)
    client, _ = console()
    response = client.get("/api/v1/channels", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["telegram"]["token_configured"] is True
    assert token_value not in response.text


def test_weixin_login_state_reads_account_json_without_leaking_the_token(console) -> None:
    client, config = console()
    wx_dir = Path(config.system.data_dir) / "channels" / "weixin"
    wx_dir.mkdir(parents=True, exist_ok=True)
    (wx_dir / "account.json").write_text(
        json.dumps(
            {"token": "wx-secret-token", "accountId": "wx-account", "userId": "wx-user"}
        ),
        encoding="utf-8",
    )
    response = client.get("/api/v1/channels", headers=AUTH)
    assert response.status_code == 200
    weixin = response.json()["weixin"]
    assert weixin["logged_in"] is True
    assert weixin["account_id"] == "wx-account"
    assert weixin["user_id"] == "wx-user"
    assert "wx-secret-token" not in response.text
