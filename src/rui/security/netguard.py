"""Outbound URL guard: the one place an SSRF check is made.

The console and its operator are trusted: whoever can edit `config.yaml` already owns
the box. The threat here is *data* reaching an HTTP client: an MCP tool description, a
Home Assistant entity name, a music-server response, or a search result that convinces
the model (or a stored config) to make the assistant fetch something it should not -
typically a cloud metadata endpoint or a service on an internal-only network.

The policy is deliberately a *deny list plus policy switches*, not an allow list by
default. Rui is self-hosted and most deployments talk to a NAS, Home Assistant or
another LAN service, so `allow_private` defaults to true. Loopback is different: it is
where the console, model servers and admin ports live, so it defaults to false.

What is always blocked, regardless of `allow_private`:

* non-``http``/``https`` schemes, and credentials embedded in the URL;
* cloud metadata addresses (169.254.169.254, fd00:ec2::254, 100.100.100.200, ...);
* link-local, multicast, unspecified and reserved addresses.

DNS rebinding is a best-effort problem: this module resolves a hostname immediately
before the request and validates every returned address, but httpx resolves it again
inside its transport. A hostile resolver can answer differently on the second lookup.
There is no second resolution after the check; asking for one would not close the gap.
The redirect path is covered because httpx calls the event hook again for every hop.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import ipaddress
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import SplitResult, urlsplit

import httpx

# Resolvers return IP literals, not getaddrinfo tuples. A sync callable runs in a worker
# thread from `acheck_url`; an async callable is awaited directly.
Resolver = Callable[[str], Sequence[str]]
AsyncResolver = Callable[[str], Any]

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Cloud metadata services. 169.254.0.0/16 is link-local and would be caught below;
# the explicit entries document *why* it is special and cover the non-link-local
# addresses (AWS IPv6, Alibaba Cloud's CGNAT address) as well.
_METADATA_NETWORKS: tuple[_IPNetwork, ...] = (
    ipaddress.ip_network("169.254.169.254/32"),
    ipaddress.ip_network("169.254.170.2/32"),      # AWS ECS task credentials
    ipaddress.ip_network("100.100.100.200/32"),    # Alibaba Cloud metadata
    ipaddress.ip_network("fd00:ec2::254/128"),     # AWS IMDS over IPv6
)
# NAT64 prefixes can carry an IPv4 SSRF target inside an apparently global IPv6
# address. Validate the embedded IPv4 address as well.
_NAT64_NETWORKS: tuple[_IPNetwork, ...] = (
    ipaddress.ip_network("64:ff9b::/96"),
)
# Deprecated 6to4 (`2002::/16`) embeds its IPv4 destination in bits 16..47.
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

# Hostnames, not addresses, that only ever mean "cloud metadata". Resolution would
# normally catch these, but a hostname deny list catches a broken/hostile resolver
# before it can return something else.
DEFAULT_BLOCKED_HOSTS = frozenset({
    "instance-data",
    "instance-data.ec2.internal",
    "metadata",
    "metadata.azure.com",
    "metadata.google.internal",
    "metadata.goog",
    "metadata.oraclecloud.com",
    "metadata.tencentyun.com",
})

_SCHEMES = frozenset({"http", "https"})


class OutboundBlocked(RuntimeError):
    """An outbound URL was refused. `reason` is stable for callers and tests."""

    def __init__(self, reason: str, detail: str = "", *,
                 url: str = "", address: str = "") -> None:
        self.reason = reason
        self.detail = detail
        self.url = url
        self.address = address
        message = f"outbound URL blocked ({reason}): {detail or url}"
        super().__init__(message)


@dataclass(frozen=True)
class OutboundPolicy:
    """The runtime form of `security.outbound` (schema defaults match these)."""

    enabled: bool = True
    # Self-hosted LAN services are the normal case; private ranges stay allowed.
    allow_private: bool = True
    # Loopback is where the console and model servers listen. Off by default.
    allow_loopback: bool = False
    extra_allowed_hosts: Iterable[str] = field(default_factory=frozenset)
    extra_allowed_cidrs: Iterable[str] = field(default_factory=tuple)
    blocked_hosts: Iterable[str] = field(default_factory=frozenset)
    blocked_cidrs: Iterable[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Accept lists from config/tests and normalize once, before any request.
        object.__setattr__(
            self, "extra_allowed_hosts",
            frozenset(_normalize_host(item) for item in _items(self.extra_allowed_hosts)
                      if _normalize_host(item)),
        )
        object.__setattr__(
            self, "blocked_hosts",
            DEFAULT_BLOCKED_HOSTS
            | frozenset(_normalize_host(item) for item in _items(self.blocked_hosts)
                        if _normalize_host(item)),
        )
        object.__setattr__(
            self, "extra_allowed_cidrs",
            tuple(_network(item, "extra_allowed_cidrs")
                  for item in _items(self.extra_allowed_cidrs)),
        )
        object.__setattr__(
            self, "blocked_cidrs",
            tuple(_network(item, "blocked_cidrs") for item in _items(self.blocked_cidrs)),
        )

    @classmethod
    def from_config(cls, config: Any) -> OutboundPolicy:
        """Build from `Config.security` or `SecurityConfig.outbound`."""
        # Accept Config, SecurityConfig or the outbound block itself.
        config = getattr(config, "security", config)
        block = getattr(config, "outbound", config)
        return cls(
            enabled=bool(getattr(block, "enabled", True)),
            allow_private=bool(getattr(block, "allow_private", True)),
            allow_loopback=bool(getattr(block, "allow_loopback", False)),
            extra_allowed_hosts=getattr(block, "extra_allowed_hosts", ()) or (),
            extra_allowed_cidrs=getattr(block, "extra_allowed_cidrs", ()) or (),
            blocked_hosts=getattr(block, "blocked_hosts", ()) or (),
            blocked_cidrs=getattr(block, "blocked_cidrs", ()) or (),
        )


def policy_from_config(config: Any) -> OutboundPolicy:
    """Module-level spelling used by the clients (`OutboundPolicy.from_config`)."""
    return OutboundPolicy.from_config(config)


def _items(value: Any) -> tuple[Any, ...]:
    """A bare string is one item, not an iterable of characters."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _normalize_host(host: str) -> str:
    return str(host or "").strip().lower().rstrip(".")


def _network(value: Any, field_name: str) -> _IPNetwork:
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError as exc:
        raise ValueError(f"{field_name}: invalid CIDR {value!r}") from exc


def _select_policy(policy: OutboundPolicy | None,
                   allow_private: bool | OutboundPolicy | None) -> OutboundPolicy:
    """`check_url(url, allow_private=False)` and a full policy are both accepted."""
    if isinstance(allow_private, OutboundPolicy):
        policy, allow_private = allow_private, None
    elif allow_private is not None and not isinstance(allow_private, bool):
        # Also accept a `security.outbound`/`Config.security` object for convenience.
        policy, allow_private = OutboundPolicy.from_config(allow_private), None
    if policy is None:
        return OutboundPolicy(allow_private=True if allow_private is None else allow_private)
    if isinstance(allow_private, bool) and allow_private != policy.allow_private:
        return replace(policy, allow_private=allow_private)
    return policy


def _split(url: str) -> tuple[SplitResult, str, _IPAddress | None]:
    """Parse and reject the URL-shape attacks before any DNS work."""
    if not isinstance(url, str) or not url.strip():
        raise OutboundBlocked("invalid", "empty URL", url=str(url))
    # Raw CR/LF/space in a URL is either a header-injection attempt or a broken caller.
    if any(char.isspace() for char in url) or "\\" in url:
        raise OutboundBlocked("invalid", "URL contains whitespace or a backslash", url=url)
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        # Accessing `.port` validates a malformed port and raises ValueError.
        _ = parts.port
    except ValueError as exc:
        raise OutboundBlocked("invalid", f"malformed URL: {exc}", url=url) from exc
    scheme = parts.scheme.lower()
    if scheme not in _SCHEMES:
        raise OutboundBlocked("scheme", f"scheme {scheme or '(none)'!r} is not http(s)",
                              url=url)
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise OutboundBlocked("credentials", "URL carries user:password credentials",
                              url=url)
    if not host:
        raise OutboundBlocked("invalid", "URL has no hostname", url=url)
    if "%" in host:
        # IPv6 zone ids and percent-encoded host tricks are not needed by any client
        # here; refusing them avoids a resolver/transport parser disagreement.
        raise OutboundBlocked("invalid", "hostname contains a percent escape", url=url)
    host = _normalize_host(host)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    return parts, host, literal


def _effective_ip(address: _IPAddress) -> _IPAddress:
    """Unwrap IPv4-mapped and NAT64 IPv6 addresses before classification."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return address.ipv4_mapped
        for prefix in _NAT64_NETWORKS:
            if address in prefix:
                return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        if address in _SIX_TO_FOUR:
            return ipaddress.IPv4Address((int(address) >> 80) & 0xFFFFFFFF)
    return address


def _is_privateish(address: _IPAddress) -> bool:
    return bool(address.is_private or address in _CGNAT)


def _is_metadata(address: _IPAddress) -> bool:
    return any(address.version == network.version and address in network
               for network in _METADATA_NETWORKS)


def _matches_any(address: _IPAddress, networks: Iterable[_IPNetwork]) -> bool:
    return any(address.version == network.version and address in network
               for network in networks)


def _reason_for_hard_block(address: _IPAddress) -> str:
    if _is_metadata(address):
        return "metadata"
    if address.is_link_local:
        return "link_local"
    if address.is_multicast:
        return "multicast"
    if address.is_unspecified:
        return "unspecified"
    # IPv6 loopback is also flagged "reserved"; loopback is a policy switch, not a
    # hard block, so it must win here.
    if address.is_loopback:
        return ""
    if address.is_reserved:
        return "reserved"
    return ""


def _validate_addresses(url: str, host: str, addresses: Iterable[_IPAddress],
                        policy: OutboundPolicy) -> None:
    """Validate every resolved address; one bad answer refuses the whole hostname."""
    extra_host = host in policy.extra_allowed_hosts
    for raw in addresses:
        address = _effective_ip(raw)
        if _matches_any(address, policy.blocked_cidrs):
            raise OutboundBlocked("blocked", f"{host} resolves to blocked CIDR {address}",
                                  url=url, address=str(address))
        reason = _reason_for_hard_block(address)
        if reason:
            raise OutboundBlocked(reason, f"{host} resolves to {address}",
                                  url=url, address=str(address))
        # An explicit allow entry bypasses the private/loopback switches, but never the
        # hard block above (metadata, link-local, multicast, unspecified, reserved).
        if extra_host or _matches_any(address, policy.extra_allowed_cidrs):
            continue
        if address.is_loopback:
            if not policy.allow_loopback:
                raise OutboundBlocked("loopback", f"{host} resolves to loopback {address}",
                                      url=url, address=str(address))
            continue
        if _is_privateish(address) and not policy.allow_private:
            raise OutboundBlocked("private", f"{host} resolves to private {address}",
                                  url=url, address=str(address))


def _ips_from_getaddrinfo(infos: Iterable[tuple]) -> list[str]:
    return [str(info[4][0]).split("%", 1)[0] for info in infos if len(info) >= 5]


def _resolve_host(host: str) -> list[str]:
    """Default DNS resolver, patched in tests. Returns IP literal strings."""
    return _ips_from_getaddrinfo(
        socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))


def _coerce_resolved(values: Iterable[Any]) -> list[_IPAddress]:
    """Accept IP strings (and tolerate getaddrinfo tuples from custom resolvers)."""
    addresses: list[_IPAddress] = []
    for value in values:
        raw = value
        if isinstance(raw, tuple) and len(raw) >= 5:
            raw = raw[4][0]
        try:
            address = ipaddress.ip_address(str(raw).strip().split("%", 1)[0])
        except ValueError as exc:
            raise OutboundBlocked("dns", f"resolver returned a non-IP value {raw!r}") from exc
        if address not in addresses:
            addresses.append(address)
    return addresses


def check_url(url: str, *,
              policy: OutboundPolicy | None = None,
              allow_private: bool | OutboundPolicy | None = None,
              resolver: Resolver | None = None) -> str:
    """Validate one outbound URL; raises `OutboundBlocked` with a stable reason.

    `policy` is the normal form. For small call sites `allow_private=False` is accepted
    as a shortcut, and an `OutboundPolicy` there is treated as the policy itself.
    Hostnames are resolved and *every* A/AAAA answer is checked.
    """
    selected = _select_policy(policy, allow_private)
    if not selected.enabled:
        return url
    _parts, host, literal = _split(url)
    if host in selected.blocked_hosts:
        raise OutboundBlocked("blocked", f"host {host!r} is explicitly blocked", url=url)
    if literal is not None:
        _validate_addresses(url, host, [literal], selected)
        return url
    try:
        resolved: Iterable[Any] = (resolver(host) if resolver is not None
                                   else _resolve_host(host))
        addresses = _coerce_resolved(resolved)
    except OutboundBlocked:
        raise
    except (OSError, ValueError) as exc:
        raise OutboundBlocked("dns", f"cannot resolve {host}: {exc}", url=url) from exc
    if not addresses:
        raise OutboundBlocked("dns", f"{host} resolved to no addresses", url=url)
    _validate_addresses(url, host, addresses, selected)
    return url


async def acheck_url(url: str, *,
                     policy: OutboundPolicy | None = None,
                     allow_private: bool | OutboundPolicy | None = None,
                     resolver: AsyncResolver | None = None) -> str:
    """Async form used by the httpx event hooks.

    Resolution runs in a worker thread (the default resolver is blocking). An async
    `resolver` is awaited directly.
    """
    if resolver is not None and inspect.iscoroutinefunction(resolver):
        selected = _select_policy(policy, allow_private)
        if not selected.enabled:
            return url
        _parts, host, literal = _split(url)
        if host in selected.blocked_hosts:
            raise OutboundBlocked("blocked", f"host {host!r} is explicitly blocked", url=url)
        if literal is not None:
            _validate_addresses(url, host, [literal], selected)
            return url
        try:
            addresses = _coerce_resolved(await resolver(host))
        except OutboundBlocked:
            raise
        except (OSError, ValueError) as exc:
            raise OutboundBlocked("dns", f"cannot resolve {host}: {exc}", url=url) from exc
        if not addresses:
            raise OutboundBlocked("dns", f"{host} resolved to no addresses", url=url)
        _validate_addresses(url, host, addresses, selected)
        return url
    # A patched `_resolve_host` (tests) works here too: check_url owns the sync path.
    return await asyncio.to_thread(
        check_url, url, policy=policy, allow_private=allow_private, resolver=resolver)


def _add_hook(kwargs: dict[str, Any], name: str, hook: Callable[..., Any]) -> None:
    hooks = dict(kwargs.pop("event_hooks", None) or {})
    existing = hooks.get(name) or []
    if callable(existing):
        existing = [existing]
    hooks[name] = [*existing, hook]
    kwargs["event_hooks"] = hooks


def guarded_async_client(policy: OutboundPolicy | None = None, *,
                         allow_private: bool | OutboundPolicy | None = None,
                         resolver: AsyncResolver | None = None,
                         **kwargs: Any) -> httpx.AsyncClient:
    """`httpx.AsyncClient` with an SSRF check on the request hook.

    httpx invokes a request hook for the first request **and every redirect hop**, so
    `follow_redirects=True` stays safe. The client is a normal AsyncClient; pass
    `headers`, `timeout`, `transport`, ... exactly as before.
    """
    selected = _select_policy(policy, allow_private)
    client_kwargs = dict(kwargs)
    if selected.enabled:
        async def _check(request: httpx.Request) -> None:
            await acheck_url(str(request.url), policy=selected, resolver=resolver)

        _add_hook(client_kwargs, "request", _check)
    return httpx.AsyncClient(**client_kwargs)


def guarded_sync_client(policy: OutboundPolicy | None = None, *,
                        allow_private: bool | OutboundPolicy | None = None,
                        resolver: Resolver | None = None,
                        **kwargs: Any) -> httpx.Client:
    """Sync twin of `guarded_async_client`, for blocking downloads in worker threads."""
    selected = _select_policy(policy, allow_private)
    client_kwargs = dict(kwargs)
    if selected.enabled:
        def _check(request: httpx.Request) -> None:
            check_url(str(request.url), policy=selected, resolver=resolver)

        _add_hook(client_kwargs, "request", _check)
    return httpx.Client(**client_kwargs)


async def guarded_get(url: str, *,
                      policy: OutboundPolicy | None = None,
                      allow_private: bool | OutboundPolicy | None = None,
                      resolver: AsyncResolver | None = None,
                      **kwargs: Any) -> httpx.Response:
    """One-shot guarded GET. For repeated calls build a `guarded_async_client`."""
    client = guarded_async_client(policy, allow_private=allow_private,
                                  resolver=resolver)
    try:
        return await client.get(url, **kwargs)
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()


# Convenience alias for callers that expect the async spelling next to `check_url`.
check_url_async = acheck_url

__all__ = [
    "DEFAULT_BLOCKED_HOSTS",
    "OutboundBlocked",
    "OutboundPolicy",
    "acheck_url",
    "check_url",
    "check_url_async",
    "guarded_async_client",
    "guarded_get",
    "guarded_sync_client",
    "policy_from_config",
]
