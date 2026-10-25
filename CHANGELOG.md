# Changelog

All notable changes to **rui** are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Curated GitHub Release notes live in [`docs/releases/`](docs/releases/); this file is the
canonical per-version record.

> **Backfill note (2026-10-25).** Versions 0.1.0–0.4.0 were reconstructed from the git
> tags and commit history plus `docs/HANDOFF.md`, the archived development log
> (`docs/archive/HANDOFF-history-to-2026-10-24.md`), `docs/ROADMAP.md`,
> `docs/acceptance.md` and `docs/remaining-work.md`. Dates are tag dates in
> Asia/Shanghai (+08:00). Only shipped, verifiable behaviour is listed; anything that
> could not be confirmed from those sources is marked *(unverified)*.

## [Unreleased]

<!-- Add changes here as they land; move them under the next version at release time. -->

## [0.4.0] - 2026-10-25

Console information-architecture refactor Phase 3. The console can now create providers,
configure Subsonic-family music sources, and manage integrations, credentials and system
settings without hand-editing YAML.

### Added

- **Provider presets and creation.** `GET /api/v1/providers/presets`,
  `POST /api/v1/providers`, `POST /api/v1/providers/probe` and
  `GET /api/v1/providers/status`. 11 LLM presets (OpenAI, DeepSeek, SiliconFlow,
  Moonshot, Zhipu, DashScope, OpenRouter, Groq, Ollama, LM Studio, vLLM), 4 ASR/TTS
  presets (Volc ASR, local sherpa, Volc TTS, XFYUN TTS), and custom OpenAI-compatible
  endpoints. Creation writes the catalog block and chain entry atomically; the draft
  probe writes nothing.
- **Provider readiness and inline keys.** Ready/missing reasons instead of a green badge
  that lies; keys are write-only and held in memory; preset credential names are
  server-owned and custom providers may only reference `RUI_*` environment names.
  Provider changes report `restart_required: true`.
- **Music source presets.** `music.source` is a preset id backed by one Subsonic-family
  adapter. Five presets ship: Navidrome, Airsonic, Gonic, Ampache and custom Subsonic.
  `GET /api/v1/music/sources` and `POST /api/v1/music/test` expose the registry and a
  real `ping` test.
- **Write-only music credentials.** Per-source credential slots are stored in
  `lan_media.json` (flat JSON, `0600`, atomic writes) and are never returned by a GET.
  The console submits only changed fields. Legacy inline `music.url/user/password`
  values are migrated to that file on startup.
- **Integrations, credentials and system pages.** `/panel/credentials` is a typed,
  write-only audit page with consumer metadata; `/panel/system` owns the 13
  runtime/console/outbound fields; MCP servers accept non-secret `headers` and a
  write-only `token_env`; Home Assistant keeps its first-party card and safety policy.

### Changed

- **Console IA Phase 3 completes the two-group navigation.** Merged pages from 0.3.4
  gain their final model, music, credentials and system content; legacy routes and panel
  ids stay reachable as redirects/aliases for at least one release.
- **Provider cards** can set a provider as primary or remove it from the chain by
  editing the chain draft; the normal stage save applies it.
- **Unknown music sources** now load but are reported as `enabled=false` /
  `source_known=false` instead of showing a healthy panel for a service that never
  started.

### Fixed

- **Built-in/reference/example plug manifests** declared a `rui_compat` ceiling below
  0.4.0, so a 0.4.0 image failed to load its own plugs at startup. The ranges now accept
  0.4.x (re-cut).
- **Plugin-compatibility CI fixtures** hard-coded the same sub-0.4.0 ceiling, causing 26
  failing tests; the re-cut derives the ceiling from the running version and adds a
  regression test so future bumps cannot silently break it.

### Security

- No new security-relevant changes in 0.4.0. The release ships the 0.3.2 P3.5 baseline
  unchanged: non-root uid/gid 1000, read-only root filesystem, `cap_drop: ALL`,
  `no-new-privileges`, outbound SSRF guard, and pip-audit/Trivy scanning in CI.

### Upgrade notes

- **From 0.3.2 – 0.3.4:** no data migration and no chown. Pull and recreate; existing
  provider chains, devices and music credentials are untouched.
- **From 0.3.0 – 0.3.1:** images before 0.3.2 ran as root. Back up, then run
  `sudo chown -R 1000:1000 config data` once before recreating.
- **From 0.2.x:** run the same one-time chown. Config is already v2 and the
  house-ear → rui rename is done.
- **From 0.1.x:** run the chown and follow
  [`docs/migration-house-ear-to-rui.md`](docs/migration-house-ear-to-rui.md). A
  `version: 1` config is migrated to v2 in memory and persisted as v2 on the first
  save; the upgrade itself does not rewrite it.
- **Rollback:** back up `config/config.yaml` first. When returning to a pre-0.3.2 root
  image, set `RUI_UID/RUI_GID=0` in `.env`; the volumes do not need to be chowned
  back.

## [0.3.4] - 2026-10-25

Console Phase 2: duplicate pages are merged, with legacy routes redirected.

### Added

- **Merged console pages:** Devices & Audio (`/panel/devices`), Tools & Integrations
  (`/panel/integrations`), Music (`/panel/music`) and Voice Assistant
  (`/panel/voice-assistant`), each with tabs/sections instead of a second destination.
  Legacy routes redirect and old panel ids remain for one release.

### Changed

- **CI builds each architecture on a native runner** (PR #10). arm64 is no longer
  emulated through QEMU, so a tag publish is no longer a 40-minute job that the next
  push cancels.

### Upgrade notes

- No config or API changes. Old panel URLs and ids keep working during the transition.

## [0.3.3] - 2026-10-25

Console IA Phase 1 (P0 fixes) plus the first round of Socket/Plug audit fixes.

### Added

- **Two-group console navigation:** *Run* and *Configure & Maintain*; the setup wizard
  only appears while setup is incomplete.
- **Independent audits** for console IA and for the Socket/Plug contract and
  implementation (`docs/console-ia-audit.md`, `docs/socket-plug-audit.md`,
  `docs/socket-plug-impl-audit.md`).

### Changed

- **Models & voice services** replaces the wizard's step-01 label (step id unchanged).
  Readiness filtering means an unready provider is no longer tried on every sentence;
  the saved provider fallback chain is preserved.
- **Music** no longer hides credentials and switches when playback is disabled;
  `music.source` is a validated select and an unknown value is shown honestly.
- **Socket/Plug audit follow-ups:** trust gate before in-process loading, capability
  intersection with the device manifest, volume contract, DeviceWake behaviour, audio
  supervision, WebSocket limits and timestamp alignment.

### Fixed

- Provider save no longer drops the fallback chain.
- XFYUN is no longer logged as a failure on every sentence when another TTS provider is
  configured; readiness is reported once and the fallback is used silently.
- A disabled music page is no longer a dead end.

### Upgrade notes

- No config migration. Review the new two-group navigation; old routes still resolve.

## [0.3.2] - 2026-10-25

Security release P3.5: outbound SSRF guard, non-root container, CVE scanning.

### Added

- `make security`, `make security-deps` and `make security-image` targets.
- `docs/security.md`, `docs/security-scanning.md`, `scripts/security-pip-audit.sh`.
- `RUI_UID`/`RUI_GID` overrides and an ownership migration in `deploy/install.sh`.

### Security

- **Outbound SSRF guard** (`security.outbound`) is applied to Home Assistant, MCP,
  music and web-search URLs, and to redirects. It always refuses cloud metadata
  (169.254.169.254 et al.), link-local, multicast, unspecified and reserved addresses;
  hostnames are resolved immediately before the request and every A/AAAA answer is
  checked. Defaults allow private LAN peers and refuse loopback.
- **Non-root container:** the image runs as `rui` (uid/gid 1000), with a read-only root
  filesystem, `/tmp` as tmpfs, `cap_drop: ALL` and `no-new-privileges`. State moved
  under the data volume; `install.sh` chowns `config/` and `data/` to the runtime
  user.
- **CVE scanning in CI:** pip-audit over `uv.lock`, Trivy image scan with SARIF upload,
  and dated allowlists (`security/pip-audit-ignore.txt`,
  `security/trivy-ignore.yaml`).

### Upgrade notes

- **One-time, for existing installs:** back up, run
  `sudo chown -R 1000:1000 config data`, then pull and recreate. A root-owned volume
  otherwise fails to write and the container restart-loops.
- **Rollback to a root image:** set `RUI_UID/RUI_GID=0`, recreate; no chown back.

## [0.3.1] - 2026-10-24

Official downstream adapter hook for external device bridges.

### Added

- `RUI_ADAPTER_PLUGINS` environment variable and `rui.adapters` entry points, with
  fail-soft loading. External projects can ship a device adapter out of tree instead of
  forking rui core; the standard targets bridges such as dot2.
- Updated `docs/socket-plug-v1.md` and plug test-kit guidance for the hook.

### Upgrade notes

- No config change for built-in adapters. Plugin authors should read the adapter
  contract before moving an adapter out of tree.

## [0.3.0] - 2026-10-24

Socket/Plug v1 and the first real device-management release.

### Added

- **Socket/Plug v1:** manifest v0/v1, the `device_wake` contract, audio
  policy/metrics/trace, the `rui plug-test` contract kit, the built-in LX06 reference
  plug (`plugins/rui-plugin-xiaoai`) and an example plug. Sidecar-declared plugins fall
  back to RUI KWS because v1 has no standardised wake RPC.
- **Device management:** status, enable/disable, token rotation, port-conflict detection,
  multi-device warnings, and the matching console UI.

### Changed

- Docker base pinned to Debian **bookworm**; trixie dropped `libspeexdsp1`.

### Upgrade notes

- Existing device configs keep working. External adapters should target the v1 contract;
  the official out-of-tree hook arrives in 0.3.1.

## [0.2.2] - 2026-10-24

Installer and setup-wizard hardening; the console starts using human labels.

### Added

- End-to-end install-flow audit (`docs/install-flow-audit.md`).

### Fixed

- **Setup wizard P0/P1:** prepare-before-token deadlock, server-driven setup nudge,
  any-provider key evidence, local ASR test, loopback connect URL, truthful connection
  status, speaker parameter controls and local-key tests.
- **Console labels:** human labels instead of raw environment/config identifiers.
- **Installer:** re-runnable install, pinned-source fallback, model repair, and a
  failing health check now exits non-zero.

### Upgrade notes

- Re-running `deploy/install.sh` is now safe and repairs a partial install; otherwise
  pull the image as usual.

## [0.2.1] - 2026-10-24

Setup-wizard UX redesign.

### Added

- Provider catalog with **real key smoke tests**, connect hints, grouped key labels and
  a prepare checklist (issue #5).
- Read-only connect card and speaker parameter controls.

### Changed

- The wizard is reorganized as a provider table: **use → provider → credentials →
  voice**. Fresh LX06 templates now enable the LED ring by default (previously off).

### Upgrade notes

- No schema migration. If the wizard was left incomplete, re-run it. Existing configs
  keep their current LED setting.

## [0.2.0] - 2026-10-24

**BREAKING:** rename from house-ear to rui, and config schema v2.

### Changed

- **Renamed** the Python package, CLI, environment prefix, host paths and container to
  `rui`; the GitHub repository and image were already named `rui`. Legacy
  `HOUSE_EAR_*` variables resolve through a one-release compatibility bridge.
- **Config schema v2:** the flat audio block is split into `capture`/`voice`/
  `playback`, and provider blocks become catalog entries behind named chains. A v1 file
  is migrated in memory on load and persisted as v2 on the first save.
- Default paths move to `/var/lib/rui` (data), `~/.config/rui` (config),
  `~/.local/state/rui` (state) and `~/rui` (install dir).

### Upgrade notes

- Follow [`docs/migration-house-ear-to-rui.md`](docs/migration-house-ear-to-rui.md):
  update `.env` keys and `config.yaml` paths, replace the compose file, then recreate.
- The environment fallback is one-way: an old image does not understand a config that
  only uses `RUI_*` names, so keep a pre-upgrade copy of `config/`.

## [0.1.3] - 2026-10-24

Model delivery and doctor reliability.

### Added

- VAD and KWS models are baked into the image; own model URLs can be supplied through
  `RUI_ASR_URL`/`RUI_VAD_URL`/`RUI_KWS_URL`.
- `rui doctor` probes the sherpa runtime and surfaces real KWS/VAD load failures
  (issue #2).

### Changed

- The official `latest` image now contains the baked models.

### Fixed

- Model downloads force IPv4 (the NAS has no global IPv6 route).
- The ghproxy mirror is tried before a stalling GitHub release asset; host proxy
  variables are forwarded to `fetch-models`.
- `install.sh` fails cleanly when a container already exists and pins the compose
  project directory.
- Removed the dead hf-mirror VAD URL and prefer the working mirror.

## [0.1.2] - 2026-10-24

Install wizard UI, device management and playback pacing.

### Added

- Manifest-driven 8-step installation wizard UI.
- Device management page: parameter validation, plug resolution, connection test and
  delete.
- Configurable playback prebuffer, gap metrics and ordered start/stop.

### Fixed

- Cloud-only ASR chains no longer require local ASR files.
- The device manifest class is exposed through its documented alias.
- CI cancels superseded runs per ref.

## [0.1.1] - 2026-10-24

Infrastructure reliability, the first Socket & Plug slice, and a licensable ffmpeg.

### Added

- **Socket & Plug v0:** manifest schema and loader, built-in LX06 plug, device params and
  the evidence-driven setup-step API.
- **LGPL-only ffmpeg** built from pinned source, with `THIRD_PARTY.md` and README
  credits.
- EchoMuse device-management research and the install-wizard design.

### Changed

- Image publishing is gated on the test job (P1.5).

### Fixed

- Empty or whitespace-only config now raises `ConfigError`/uses last-good instead of
  schema defaults followed by an `insecure_bind` crash loop.
- Legacy `DEEPSEEK_API_KEY` is accepted when the new LLM key is missing.
- Model downloads have bounded timeouts (connect 10 s / read 60 s) and fall back quickly.
- Acceptance output defaults to the mounted data directory, so results survive a
  container recreate.

## [0.1.0] - 2026-10-24

First public release, after all four v1 acceptance gates passed.

### Added

- **Acceptance baseline:** cold install to first reply in 423 s (gate ≤30 min); wake
  19/20 at 80% volume with music playing (gate ≥19); barge-in 24.2 / 64.5 / 205.2 ms,
  max ≤300 ms; public repository and GHCR.
- **Audio plane:** 16 kHz capture (6 mic + 2 reference channels from 48 kHz hardware),
  AEC (ERLE ≈31.6 dB), KWS/VAD, turn segmentation with preroll and idle goodbye,
  barge-in, playback arbitration and the LED ring.
- **Providers and tools:** SenseVoice local ASR and Volc ASR, DeepSeek LLM, XFYUN and
  Volc TTS, provider chains, Home Assistant, generic MCP, and Navidrome music.
- **Console 2.0:** install wizard, device management, phrasebook editor, providers,
  tools/MCP/HA, channels, turns/memory/logs and the config-recovery banner.
- **Channels:** Telegram and WeChat iLink gateway, managed gateway process and text chat
  API.
- **Operations:** `rui doctor`, backup/restore, last-good config recovery, acceptance
  tooling, runbook and deploy docs.
- **Packaging:** MIT, public repo, multi-arch GHCR `0.1.0`/`0.1`/`latest`.

Quality at release: 749 passed / 17 skipped, ruff clean, web typecheck/build green.

[Unreleased]: https://github.com/yes999zc/rui/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/yes999zc/rui/compare/v0.3.4...v0.4.0
[0.3.4]: https://github.com/yes999zc/rui/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/yes999zc/rui/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/yes999zc/rui/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/yes999zc/rui/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/yes999zc/rui/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/yes999zc/rui/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/yes999zc/rui/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/yes999zc/rui/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/yes999zc/rui/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/yes999zc/rui/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/yes999zc/rui/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/yes999zc/rui/releases/tag/v0.1.0
