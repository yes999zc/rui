# Security: outbound URL guard (SSRF)

Rui is a self-hosted voice endpoint. The **console operator is trusted**: anyone who
holds the console token, edits `config.yaml`, or controls the Docker host can already
read the data volume, change providers, and start MCP stdio processes. This page is
about a narrower threat: a URL or a hostname that reaches an outbound HTTP client as
*data*, not as an explicit operator decision.

## Why this matters

The assistant talks to Home Assistant, generic MCP servers, a Navidrome/Subsonic
server and an optional web-search endpoint. Some of those URLs come from
user/remote-editable places:

* `tools.ha.base_url` and `tools.mcp_servers[].url`;
* the music credentials JSON (`url` lives outside `config.yaml`);
* `tools.search.base_url`;
* a redirect returned by any of the above (or by a compromised MCP server).

A malicious or compromised remote service can answer with a redirect to
`http://169.254.169.254/latest/meta-data/`, or persuade the model to fetch an
internal admin page. On cloud hosts, the metadata service often returns credentials;
on a NAS it may expose an admin UI that trusts the source IP. That is SSRF, and it is
checked in one place: `src/rui/security/netguard.py`.

A fixed provider endpoint (`providers.catalog.*.base_url`) is treated as
operator-configured, like the console itself. It is not guarded today (see
[Residual risks](#residual-risks)).

## Defaults

`security.outbound` is part of the config schema. Defaults are chosen for the
self-hosted case:

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Check every guarded outbound URL. |
| `allow_private` | `true` | Allow RFC1918, IPv6 ULA and CGNAT peers: NAS, Home Assistant, LAN services. |
| `allow_loopback` | `false` | Refuse `127.0.0.0/8` and `::1`. The console, local model servers and admin ports listen there. |
| `extra_allowed_hosts` | `[]` | Exact hostnames that may bypass the private/loopback switches. |
| `extra_allowed_cidrs` | `[]` | CIDRs matched against every resolved IP. |
| `blocked_hosts` | `[]` | Exact hostnames always refused; wins over every allow entry. |
| `blocked_cidrs` | `[]` | CIDRs always refused; wins over every allow entry. |

Always refused, regardless of policy switches or allow lists:

* schemes other than `http`/`https` (and credentials embedded in the URL);
* cloud metadata: `169.254.169.254`, `169.254.170.2`, `100.100.100.200`,
  `fd00:ec2::254`, and the well-known metadata hostnames;
* link-local (`169.254.0.0/16`, `fe80::/10`), multicast, unspecified
  (`0.0.0.0`, `::`) and reserved addresses.

Hostnames are resolved immediately before the request, and **every** A/AAAA answer
is validated. A name that resolves to both a public and a private address fails when
the policy does not allow that private address. `/etc/hosts` and the system resolver
are respected.

## Configuration

The console exposes the same fields under **Foundation (advanced)**; a save is
written to `config.yaml` and requires a service restart. Editing the file directly is
equivalent:

```yaml
security:
  outbound:
    enabled: true
    allow_private: true       # LAN NAS / Home Assistant stay reachable
    allow_loopback: false     # do not let tool data reach the local admin plane
    extra_allowed_hosts: []   # e.g. ["nas.lan", "ha.internal"]
    extra_allowed_cidrs: []   # e.g. ["192.168.5.0/24"]
    blocked_hosts: []
    blocked_cidrs: []         # e.g. ["192.168.5.170/32"]
```

Then restart:

```bash
docker compose restart
```

### Self-hosted / LAN examples

An exact hostname can bypass the private/loopback switches (it must still resolve in
the container); a CIDR-restricted deployment can allow one range and deny the rest:

```yaml
security:
  outbound:
    allow_private: true
    extra_allowed_hosts: ["bakey-nas.local"]
    extra_allowed_cidrs: ["192.168.5.0/24"]
    blocked_cidrs: ["192.168.5.200/32"]   # an admin host tools must not reach
```

To allow only public services (no LAN access at all):

```yaml
security:
  outbound:
    allow_private: false
```

Loopback is needed only for development against services on the same host. Prefer an
exact host entry over turning `allow_loopback` on:

```yaml
security:
  outbound:
    allow_loopback: true           # all of 127/8 and ::1
    # or, narrower:
    extra_allowed_hosts: ["localhost"]
```

An exact host allow only bypasses the private/loopback switches; metadata and
link-local are still refused.

## What is guarded

The guard is an httpx request event hook, so it runs before the first request and
again for **every redirect hop** when `follow_redirects=True`:

| Client | Call site | Notes |
| --- | --- | --- |
| Home Assistant over its own bridge | `tools/ha.py` (not MCP) | Handshake plus every tool call. |
| Generic MCP servers (HTTP) | `tools/mcp_manager.py` (`open_session`) | One guarded client per session, closed on reconnect. stdio is a local child process, not an outbound URL. |
| Navidrome / Subsonic | `music/sources/navidrome.py` | JSON API client and the blocking stream download. |
| Web search | `tools/info.py` (`tools.search.base_url`) | Both the search tool and the news fallback. |
| Music nickname lookup | `music/names.py` (`DEEPSEEK_BASE_URL`) | Optional model call when a nickname misses the library. |

Fixed-endpoint clients (open-meteo, the public news mirrors, Telegram, WeChat, the
container healthcheck) are not user-URL driven and keep their existing clients.

## What remains

* **DNS rebinding is best-effort.** The guard resolves and validates the name, then
  httpx resolves it again inside its transport. A hostile resolver can answer
  differently on the second lookup. The guard cannot close that TOCTOU window
  without pinning the IP in the transport or implementing DNS-over-HTTPS; the
  current choice keeps the client stack simple and is documented here rather than
  overclaimed.
* **Redirects are re-checked only through the guarded clients.** A caller that
  extracts a `Location` header and fetches it with a bare `httpx` client bypasses the
  hook. All production call sites use the guarded clients.
* **The operator can always disable the guard** (`enabled: false`) or allow a range,
  exactly like any other config edit. That is an explicit trust decision, not a
  remote bypass.
* **Provider endpoints are catalog/admin-controlled** and are not guarded today.
  Treat a malicious provider config the same as a malicious console user.
* **Private networks are allowed by default.** If the box is on a shared or hostile
  LAN, set `allow_private: false` or narrow `extra_allowed_cidrs`; the guard does not
  know which internal services are sensitive.
* **No DNSSEC / pinned DNS.** The system resolver's answer is trusted for the
  validation step. If a local resolver is compromised, the resolved set can be
  wrong; rebinding is the residual above.
* **Uncommon IPv6 translation prefixes** other than IPv4-mapped `::ffff:0:0/96`,
  NAT64 `64:ff9b::/96` and deprecated 6to4 `2002::/16` are not unwrapped before
  classification.
* **Proxy environments:** if `HTTP(S)_PROXY` is set, httpx may send the request to
  the proxy; the guard still validates the target URL, but the proxy performs its own
  DNS resolution. Keep proxy configuration under operator control.

## Container and runtime hardening

The image runs as the unprivileged `rui` user (uid/gid **1000**): `/app` is root-owned
and read-only to the service, the root filesystem is read-only (`/tmp` is a small
tmpfs), only the `config/` and `data/` mounts persist writes, and the service starts
with `cap_drop: ALL` + `no-new-privileges` (no gosu or privilege-entry binary).

* Persistent state lives under `/var/lib/rui` (for example
  `/var/lib/rui/state`); `system.data_dir` moves it. The audio-plane socket is
  `/var/lib/rui/state/audio.sock` (`RUI_AUDIO_SOCK`) and channel-gateway data is
  `/var/lib/rui/state/channels` (`RUI_CHANNEL_DATA`); compose derives both from
  `RUI_STATE_DIR`. The old `/root/.local/state/rui` was in the container layer and is
  not migrated (copy it with `docker cp` before recreating if channel logins matter).
* Existing installs upgrading to the non-root image need one ownership migration:
  `sudo chown -R 1000:1000 config data`. The installer does this automatically.
* `RUI_UID` / `RUI_GID` override the runtime identity when 1000 is taken; the
  installer and compose templates apply them. Rolling back to a root image means
  `RUI_UID=0 RUI_GID=0`; the volumes do not need to be chowned back.
* The install/rollback procedure is in [deploy.md](deploy.md) and
  [runbook.md](runbook.md) §5/§9.

Dependency and image CVEs are scanned in CI (`pip-audit` on every push/PR, Trivy on
the amd64 image); see [security-scanning.md](security-scanning.md).

## Tests

`tests/test_netguard.py` covers scheme/credential/parser cases, metadata and
link-local blocks, private/loopback policy, allow/deny precedence, DNS answers (with a
monkeypatched resolver), redirect re-checks, the sync/async guarded clients, and the
Navidrome/MCP call sites. Run:

```bash
.venv/bin/python -m pytest -o addopts="" -q tests/test_netguard.py
```
