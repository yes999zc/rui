"""Outbound SSRF guard: URL shape, address classes, DNS answers, client hooks.

The resolver is monkeypatched (or passed explicitly) so no test depends on the host's
DNS. Address literals never resolve, which also keeps the guarded-client tests offline.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from rui.config import Config
from rui.config.schema import McpServerConfig, OutboundSecurityConfig
from rui.music.sources.navidrome import NavidromeSource
from rui.security import netguard
from rui.security.netguard import (
    OutboundBlocked,
    OutboundPolicy,
    acheck_url,
    check_url,
    guarded_async_client,
    guarded_get,
    guarded_sync_client,
    policy_from_config,
)
from rui.tools import mcp_manager

# Captured before any test replaces the module seam; some existing MCP tests assign
# `mcp_manager.open_session` directly and leave that fake in place for the process.
_REAL_OPEN_SESSION = mcp_manager.open_session


def blocked(url: str, reason: str, **kwargs) -> OutboundBlocked:
    with pytest.raises(OutboundBlocked) as info:
        check_url(url, **kwargs)
    assert info.value.reason == reason, str(info.value)
    return info.value


# ------------------------------------------------------------------ policy defaults


def test_schema_defaults_match_the_self_hosted_policy() -> None:
    outbound = Config().security.outbound
    assert outbound.enabled is True
    assert outbound.allow_private is True
    assert outbound.allow_loopback is False
    assert outbound.extra_allowed_hosts == []
    assert outbound.extra_allowed_cidrs == []
    assert outbound.blocked_hosts == []
    assert outbound.blocked_cidrs == []

    policy = policy_from_config(Config().security)
    assert policy.enabled and policy.allow_private and not policy.allow_loopback
    assert policy.extra_allowed_cidrs == ()
    # A whole Config is accepted too, not only its security block.
    assert OutboundPolicy.from_config(Config()).enabled is True


def test_policy_fields_are_in_the_console_catalog() -> None:
    from rui.config.catalog import document

    fields = {field["path"]: field for field in document(Config())["fields"]}
    expected = {
        "security.outbound.enabled": "bool",
        "security.outbound.allow_private": "bool",
        "security.outbound.allow_loopback": "bool",
        "security.outbound.extra_allowed_hosts": "list",
        "security.outbound.extra_allowed_cidrs": "list",
        "security.outbound.blocked_hosts": "list",
        "security.outbound.blocked_cidrs": "list",
    }
    for path, kind in expected.items():
        assert fields[path]["kind"] == kind, path
        # The console must not claim a live apply for a policy that needs a restart.
        assert fields[path]["effect"] == "restart", path


def test_cidr_and_host_config_are_validated() -> None:
    outbound = OutboundSecurityConfig(
        extra_allowed_cidrs=["192.168.5.9/24"],
        blocked_cidrs=["10.0.0.0/8"],
        extra_allowed_hosts=["NAS.Local."],
        blocked_hosts=["metadata.google.internal."],
    )
    assert outbound.extra_allowed_cidrs == ["192.168.5.0/24"]
    assert outbound.extra_allowed_hosts == ["nas.local"]
    with pytest.raises(ValidationError):
        OutboundSecurityConfig(blocked_cidrs=["not-a-cidr"])
    with pytest.raises(ValidationError):
        OutboundSecurityConfig(extra_allowed_hosts=["http://nas.local"])
    with pytest.raises(ValidationError):
        OutboundSecurityConfig(blocked_hosts=[""])


# ---------------------------------------------------------------------- URL shape


def test_only_http_and_https_are_accepted() -> None:
    for url in ("ftp://example.com/x", "file:///etc/passwd", "gopher://example.com/",
                "ws://example.com/socket"):
        blocked(url, "scheme")
    assert check_url("https://93.184.216.34/") == "https://93.184.216.34/"


def test_credentials_in_the_url_are_refused() -> None:
    for url in ("http://user:pass@93.184.216.34/",
                "http://user@93.184.216.34/",
                "https://:pass@93.184.216.34/"):
        blocked(url, "credentials")


def test_malformed_urls_and_parser_tricks_are_refused() -> None:
    for url in ("http:///x", "http://exa mple.com/", "http://example.com\\@10.0.0.1/",
                "http://example.com:notaport/", "http://[::1/"):
        blocked(url, "invalid")
    blocked("", "invalid")


# --------------------------------------------------------------- address classes


def test_cloud_metadata_is_always_blocked() -> None:
    for url in ("http://169.254.169.254/latest/meta-data/",
                "http://100.100.100.200/latest/meta-data/",
                "http://[fd00:ec2::254]/latest/meta-data/"):
        blocked(url, "metadata")
    blocked("http://metadata.google.internal/", "blocked")


def test_link_local_multicast_unspecified_and_reserved_are_always_blocked() -> None:
    blocked("http://169.254.1.5/", "link_local")
    blocked("http://[fe80::1]/", "link_local")
    blocked("http://224.0.0.1/", "multicast")
    blocked("http://[ff02::1]/", "multicast")
    blocked("http://0.0.0.0/", "unspecified")
    blocked("http://[::]/", "unspecified")
    blocked("http://255.255.255.255/", "reserved")
    blocked("http://240.0.0.1/", "reserved")
    for url in ("http://169.254.169.254/", "http://224.0.0.1/"):
        blocked(url, "metadata" if "169.254" in url else "multicast",
                policy=OutboundPolicy(allow_private=True, allow_loopback=True))


def test_loopback_is_a_policy_switch_not_a_hard_block() -> None:
    blocked("http://127.0.0.1:8080/", "loopback")
    blocked("http://[::1]:8080/", "loopback")
    allowed = OutboundPolicy(allow_loopback=True)
    for url in ("http://127.0.0.1:8080/", "http://[::1]:8080/",
                "http://[::ffff:127.0.0.1]:8080/"):
        assert check_url(url, policy=allowed) == url


def test_private_ranges_follow_allow_private() -> None:
    policy = OutboundPolicy(allow_private=True)
    for url in ("http://10.1.2.3/", "http://172.16.0.9/", "http://192.168.5.10/",
                "http://[fd12::1]/"):
        assert check_url(url, policy=policy) == url

    public_only = OutboundPolicy(allow_private=False)
    for url in ("http://10.1.2.3/", "http://172.16.0.9/", "http://192.168.5.10/",
                "http://100.64.0.1/", "http://[fd12::1]/"):
        blocked(url, "private", policy=public_only)

    # The shorthand form used by small call sites.
    blocked("http://10.1.2.3/", "private", allow_private=False)


def test_nat64_and_mapped_ipv6_are_classified_by_their_ipv4_target() -> None:
    blocked("http://[64:ff9b::7f00:1]/", "loopback")
    allowed = OutboundPolicy(allow_loopback=True)
    assert check_url("http://[64:ff9b::7f00:1]/", policy=allowed)
    blocked("http://[64:ff9b::a9fe:a9fe]/", "metadata")
    # Deprecated 6to4: 2002:<ipv4>::/48
    blocked("http://[2002:7f00:1::1]/", "loopback")
    blocked("http://[2002:a9fe:a9fe::1]/", "metadata")


# ------------------------------------------------------------------ DNS answers


def test_hostname_resolution_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netguard, "_resolve_host", lambda host: ["93.184.216.34"])
    assert check_url("http://example.com/") == "http://example.com/"

    # Every answer is checked, not just the first. A hostname that also resolves to a
    # private address is refused under allow_private=false.
    monkeypatch.setattr(netguard, "_resolve_host",
                        lambda host: ["93.184.216.34", "10.0.0.5"])
    blocked("http://example.com/", "private", policy=OutboundPolicy(allow_private=False))


def test_a_public_name_resolving_to_metadata_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netguard, "_resolve_host", lambda host: ["169.254.169.254"])
    blocked("http://innocent.example/", "metadata")
    monkeypatch.setattr(netguard, "_resolve_host", lambda host: ["127.0.0.1"])
    blocked("http://innocent.example/", "loopback")


def test_dns_failure_and_empty_answers_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(host: str) -> list[str]:
        raise OSError("no such host")

    monkeypatch.setattr(netguard, "_resolve_host", broken)
    blocked("http://missing.example/", "dns")
    monkeypatch.setattr(netguard, "_resolve_host", lambda host: [])
    blocked("http://missing.example/", "dns")
    monkeypatch.setattr(netguard, "_resolve_host", lambda host: ["not-an-ip"])
    blocked("http://missing.example/", "dns")


def test_an_injected_resolver_is_used_instead_of_dns() -> None:
    resolver = lambda host: ["10.0.0.7"]  # noqa: E731 - a one-line test double
    assert check_url("http://nas.local/", policy=OutboundPolicy(allow_private=True),
                     resolver=resolver)
    blocked("http://nas.local/", "private",
            policy=OutboundPolicy(allow_private=False), resolver=resolver)


# -------------------------------------------------------------- allow/deny lists


def test_explicit_allow_lists_bypass_private_and_loopback() -> None:
    policy = OutboundPolicy(
        allow_private=False,
        extra_allowed_hosts=["nas.local"],
        extra_allowed_cidrs=["192.168.5.0/24"],
    )
    assert check_url("http://nas.local/", policy=policy,
                     resolver=lambda host: ["10.0.0.9"])
    assert check_url("http://192.168.5.20/", policy=policy)


def test_extra_allow_never_overrides_a_hard_block() -> None:
    policy = OutboundPolicy(extra_allowed_cidrs=["169.254.0.0/16"],
                            extra_allowed_hosts=["metadata.google.internal"])
    blocked("http://169.254.169.254/", "metadata", policy=policy)
    blocked("http://metadata.google.internal/", "blocked", policy=policy)


def test_blocked_lists_win_over_allow_lists() -> None:
    policy = OutboundPolicy(
        allow_private=False,
        extra_allowed_hosts=["nas.local"],
        extra_allowed_cidrs=["10.0.0.0/8"],
        blocked_hosts=["nas.local"],
        blocked_cidrs=["10.5.0.0/16"],
    )
    blocked("http://nas.local/", "blocked", policy=policy,
            resolver=lambda host: ["10.0.0.9"])
    blocked("http://10.5.1.2/", "blocked", policy=policy)


def test_disabling_the_policy_is_an_explicit_escape_hatch() -> None:
    off = OutboundPolicy(enabled=False)
    assert check_url("ftp://169.254.169.254/", policy=off)
    assert check_url("http://[::1]/", policy=off)


# --------------------------------------------------------------- async / clients


def test_async_check_uses_the_same_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netguard, "_resolve_host", lambda host: ["10.0.0.5"])
    assert asyncio.run(acheck_url("http://nas.local/")) == "http://nas.local/"
    with pytest.raises(OutboundBlocked) as info:
        asyncio.run(acheck_url("http://127.0.0.1/"))
    assert info.value.reason == "loopback"


def test_guarded_async_client_allows_localhost_when_configured() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="ok")

    async def run() -> None:
        client = guarded_async_client(
            OutboundPolicy(allow_loopback=True),
            transport=httpx.MockTransport(handler),
        )
        try:
            response = await client.get("http://127.0.0.1:9/health")
            assert response.status_code == 200
        finally:
            await client.aclose()

    asyncio.run(run())
    assert seen == ["http://127.0.0.1:9/health"]


def test_guarded_async_client_rejects_metadata_before_the_transport() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="should not run")

    async def run() -> None:
        client = guarded_async_client(
            OutboundPolicy(allow_loopback=True),
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(OutboundBlocked) as info:
                await client.get("http://169.254.169.254/latest/meta-data/")
            assert info.value.reason == "metadata"
        finally:
            await client.aclose()

    asyncio.run(run())
    assert calls == []


def test_guarded_async_client_rechecks_every_redirect_hop() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        return httpx.Response(200, text="metadata reached")

    async def run() -> None:
        client = guarded_async_client(
            OutboundPolicy(allow_loopback=True),
            follow_redirects=True,
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(OutboundBlocked) as info:
                await client.get("http://127.0.0.1:9/start")
            assert info.value.reason == "metadata"
        finally:
            await client.aclose()

    asyncio.run(run())
    # The first hop reached the transport; the redirect target never did.
    assert calls == ["http://127.0.0.1:9/start"]


def test_guarded_sync_client_and_guarded_get_use_the_same_guard() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="ok")

    with guarded_sync_client(OutboundPolicy(allow_loopback=True),
                             transport=httpx.MockTransport(handler)) as client:
        assert client.get("http://127.0.0.1:9/download").text == "ok"
        with pytest.raises(OutboundBlocked):
            client.get("http://169.254.169.254/latest/meta-data/")
    assert calls == ["http://127.0.0.1:9/download"]

    with pytest.raises(OutboundBlocked):
        asyncio.run(guarded_get("http://100.100.100.200/latest/meta-data/"))


# ----------------------------------------------------------- applied clients


def _navidrome(tmp_path: Path, url: str, *,
               transport: httpx.AsyncBaseTransport | None = None,
               policy: OutboundPolicy | None = None) -> NavidromeSource:
    secrets = tmp_path / "lan_media.json"
    secrets.write_text(json.dumps({"url": url, "user": "u", "password": "p"}),
                       encoding="utf-8")
    return NavidromeSource(secrets, tmp_path / "cache", 1,
                           transport=transport, policy=policy)


def test_navidrome_rejects_a_metadata_server_by_default(tmp_path: Path) -> None:
    source = _navidrome(tmp_path, "http://169.254.169.254")
    try:
        with pytest.raises(OutboundBlocked) as info:
            asyncio.run(source.random(1))
        assert info.value.reason == "metadata"
    finally:
        asyncio.run(source.close())


def test_navidrome_allows_localhost_only_when_policy_does(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"subsonic-response": {
            "status": "ok", "randomSongs": {"song": []}}})

    blocked_source = _navidrome(tmp_path, "http://127.0.0.1:4533",
                                transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(OutboundBlocked) as info:
            asyncio.run(blocked_source.random(1))
        assert info.value.reason == "loopback"
    finally:
        asyncio.run(blocked_source.close())

    allowed = _navidrome(tmp_path, "http://127.0.0.1:4533",
                         transport=httpx.MockTransport(handler),
                         policy=OutboundPolicy(allow_loopback=True))
    try:
        assert asyncio.run(allowed.random(1)) == []
    finally:
        asyncio.run(allowed.close())


def test_generic_mcp_session_guard_refuses_metadata(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_manager, "open_session", _REAL_OPEN_SESSION)
    server = McpServerConfig(name="meta", transport="http",
                             url="http://169.254.169.254/mcp")

    async def run() -> None:
        async with mcp_manager.open_session(server) as session:
            await session.initialize()

    with pytest.raises(BaseException) as info:
        asyncio.run(run())
    assert _find_blocked(info.value) is not None


def _find_blocked(exc: BaseException) -> OutboundBlocked | None:
    if isinstance(exc, OutboundBlocked):
        return exc
    for child in getattr(exc, "exceptions", ()) or ():
        found = _find_blocked(child)
        if found is not None:
            return found
    cause = exc.__cause__
    if cause is not None:
        return _find_blocked(cause)
    return None
