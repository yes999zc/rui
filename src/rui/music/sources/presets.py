"""Music source presets: the friendly list behind `music.source`.

The audit (§2.1/§5 Phase 3) asked for a *registry*, not another free-text field. A
preset is a shape, not a second configuration system: it names the protocol family,
where that family's documentation lives, sensible connection defaults, and the exact
credential fields the console may show. The source itself is still selected by id in
`music.source`; this module only says which ids exist and what each one needs.

Everything in this module is pure data (stdlib only) so `config/catalog.py` can import
it lazily while building the select options and `web/api/app.py` can use the same list
to validate a save. A preset is offered only when the family has a runtime adapter:
the Subsonic family is implemented (`SubsonicSource`), and the custom entry is the
same protocol pointed at a self-hosted URL.

No secret value is stored here. A credential field is a *slot*: key, label, whether it
is required, and how the console should render it. Values live in the write-only
credentials file, keyed by these slot names.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "CredentialField",
    "MusicSourcePreset",
    "PRESETS",
    "credential_keys",
    "default_preset",
    "get_preset",
    "missing_credentials",
    "preset_document",
    "preset_ids",
    "presets",
]

# Protocol families with a runtime builder. `subsonic` covers Navidrome/Airsonic/
# Gonic/Ampache: they all expose the same /rest/*.view API and token auth.
SUBSONIC = "subsonic"
SUPPORTED_FAMILIES = frozenset({SUBSONIC})


@dataclass(frozen=True)
class CredentialField:
    """One write-only credential slot the console may render."""

    key: str
    label_zh: str
    label_en: str
    kind: str = "text"          # text | url | password
    required: bool = True
    placeholder_zh: str = ""
    placeholder_en: str = ""
    hint_zh: str = ""
    hint_en: str = ""

    @property
    def secret(self) -> bool:
        return self.kind == "password"

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "required": self.required,
            "secret": self.secret,
            "label": {"zh": self.label_zh, "en": self.label_en},
            "placeholder": {"zh": self.placeholder_zh, "en": self.placeholder_en},
            "hint": {"zh": self.hint_zh, "en": self.hint_en},
        }


@dataclass(frozen=True)
class MusicSourcePreset:
    """One selectable source shape. `id` is what `music.source` stores."""

    id: str
    name_zh: str
    name_en: str
    protocol_family: str
    docs_url: str
    default_port: int | None
    default_path: str
    description_zh: str
    description_en: str
    credential_fields: tuple[CredentialField, ...] = field(default_factory=tuple)
    custom: bool = False

    @property
    def supported(self) -> bool:
        """Whether a runtime adapter exists for this preset's protocol family."""
        return self.protocol_family in SUPPORTED_FAMILIES

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": {"zh": self.name_zh, "en": self.name_en},
            "protocol_family": self.protocol_family,
            "docs_url": self.docs_url,
            "default_port": self.default_port,
            "default_path": self.default_path,
            "description": {"zh": self.description_zh, "en": self.description_en},
            "custom": self.custom,
            "available": self.supported,
            "credential_fields": [item.as_dict() for item in self.credential_fields],
        }


def _subsonic_fields(port: int | None, host: str = "nas") -> tuple[CredentialField, ...]:
    """The four slots every Subsonic-family server uses.

    `password` and `token` are alternatives: token auth (t/s) is the same mechanism
    with a fixed shared secret some deployments call an API token. A preset requires
    one of them, not both.
    """
    url_placeholder = (f"http://{host}:{port}" if port else f"http://{host}")
    return (
        CredentialField(
            key="url",
            label_zh="服务器地址",
            label_en="Server URL",
            kind="url",
            placeholder_zh=url_placeholder,
            placeholder_en=url_placeholder,
            hint_zh="含协议和端口，指向服务根路径（不要自己加 /rest）。",
            hint_en="Scheme and port, pointing at the server root (do not append /rest).",
        ),
        CredentialField(
            key="user",
            label_zh="用户名",
            label_en="Username",
            hint_zh="曲库登录用户名。",
            hint_en="The library login user.",
        ),
        CredentialField(
            key="password",
            label_zh="密码",
            label_en="Password",
            kind="password",
            required=False,
            placeholder_zh="密码（只写不读）",
            placeholder_en="Password (write-only)",
            hint_zh="只写不读；保存后不回显。与 API token 二选一。",
            hint_en="Write-only; never returned after saving. Use a password or an API token.",
        ),
        CredentialField(
            key="token",
            label_zh="API token（可选）",
            label_en="API token (optional)",
            kind="password",
            required=False,
            placeholder_zh="用 token 代替密码时填写",
            placeholder_en="Fill when a token replaces the password",
            hint_zh="有些反向代理/服务用固定 token 代替密码；留空就用密码。",
            hint_en="Some gateways use a fixed token instead of a password; leave blank to use the password.",
        ),
    )


PRESETS: tuple[MusicSourcePreset, ...] = (
    MusicSourcePreset(
        id="navidrome",
        name_zh="Navidrome",
        name_en="Navidrome",
        protocol_family=SUBSONIC,
        docs_url="https://www.navidrome.org/docs/developers/subsonic-api/",
        default_port=4533,
        default_path="/",
        description_zh="轻量自托管音乐服务器，NAS 上最常用；Subsonic 兼容。",
        description_en="Lightweight self-hosted music server, common on NAS; Subsonic-compatible.",
        credential_fields=_subsonic_fields(4533),
    ),
    MusicSourcePreset(
        id="airsonic",
        name_zh="Airsonic",
        name_en="Airsonic",
        protocol_family=SUBSONIC,
        docs_url="https://airsonic.github.io/docs/",
        default_port=4040,
        default_path="/",
        description_zh="经典 Subsonic 分支，适合已有曲库的 NAS/Docker 部署。",
        description_en="Classic Subsonic fork for an existing library on NAS/Docker.",
        credential_fields=_subsonic_fields(4040),
    ),
    MusicSourcePreset(
        id="gonic",
        name_zh="Gonic",
        name_en="Gonic",
        protocol_family=SUBSONIC,
        docs_url="https://github.com/sentriz/gonic",
        default_port=4747,
        default_path="/",
        description_zh="Go 写的小型 Subsonic 服务器，资源占用低。",
        description_en="Small Go Subsonic server with a low footprint.",
        credential_fields=_subsonic_fields(4747),
    ),
    MusicSourcePreset(
        id="ampache",
        name_zh="Ampache",
        name_en="Ampache",
        protocol_family=SUBSONIC,
        docs_url="https://ampache.org/api/",
        default_port=80,
        default_path="/",
        description_zh="老牌 Web 音乐库；启用 Subsonic API 后走同一套接口。",
        description_en="Long-standing web music library; its Subsonic API uses the same endpoints.",
        credential_fields=_subsonic_fields(80, host="ampache.local"),
    ),
    MusicSourcePreset(
        id="custom",
        name_zh="自定义 Subsonic 兼容服务",
        name_en="Custom Subsonic-compatible",
        protocol_family=SUBSONIC,
        docs_url="https://www.subsonic.org/pages/api.jsp",
        default_port=None,
        default_path="/",
        description_zh="任何实现了 Subsonic API 的自建服务：填它的根地址、用户名和密码/token。",
        description_en="Any self-hosted server implementing the Subsonic API: root URL, user and password/token.",
        credential_fields=_subsonic_fields(None, host="music.local"),
        custom=True,
    ),
)

_BY_ID = {preset.id: preset for preset in PRESETS}
DEFAULT_ID = "navidrome"


def presets() -> tuple[MusicSourcePreset, ...]:
    return PRESETS


def preset_ids() -> tuple[str, ...]:
    return tuple(preset.id for preset in PRESETS)


def get_preset(preset_id: str) -> MusicSourcePreset | None:
    return _BY_ID.get(str(preset_id or "").strip().lower())


def default_preset() -> MusicSourcePreset:
    return _BY_ID[DEFAULT_ID]


def credential_keys(preset: MusicSourcePreset) -> tuple[str, ...]:
    return tuple(item.key for item in preset.credential_fields)


def missing_credentials(preset: MusicSourcePreset, values: dict[str, str]) -> tuple[str, ...]:
    """Required slots that are absent. Password and token count as alternatives."""
    missing = [item.key for item in preset.credential_fields
               if item.required and not str(values.get(item.key) or "").strip()]
    if preset.protocol_family == SUBSONIC:
        if not (str(values.get("password") or "").strip()
                or str(values.get("token") or "").strip()):
            missing.append("password")
    return tuple(dict.fromkeys(missing))


def preset_document(preset: MusicSourcePreset) -> dict:
    """The public shape `GET /api/v1/music/sources` returns for one preset."""
    return preset.as_dict()
