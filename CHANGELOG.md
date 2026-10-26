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

## [0.7.8] - 2026-10-27

**Theme: four cards in one release — the TLS install path, per-device plane
visibility, the LX06 plug leaving core, and the v2 console becoming the default.
No API contract change.**

> **0.7.5–0.7.7 ship inside 0.7.8; none of them gets its own tag.** Their exit
> conditions (Done 6 on hardware, the built-in-vs-external dual run, and the
> upgrade/rollback pass) all need the complete 0.7.8 tree and one hardware window.
> Tagging them after the fact would claim a verification that never happened.
> **0.7.8 is the single acceptance node**; this section is the per-version record for
> 0.7.5–0.7.8.
>
> （口径原文：0.7.5–0.7.7 的开发合并进 0.7.8，未单独发版；原因是它们的验收项依赖 0.7.8 的完整代码与真机窗口。）

**Release artifacts:** core **0.7.8** (`pyproject.toml`, `src/rui/__init__.py`,
`uv.lock`); `rui-dot2-bridge` stays **1.3.1** and `rui-xiaoai-bridge` stays **0.2.2**
(no code change in this release).

### 0.7.5 — TLS install path and per-device plane visibility

**Added**

- **TLS material ships with the two-speaker example** (`43d18df`):
  `examples/two-speakers/compose.yaml` mounts `./secrets` at `/opt/rui-secrets`
  (`DOT2_SECRETS_DIR`), `scripts/make_dev_tls.sh` self-signs `server.pem` /
  `server.key`, and the install prompts of all three repos gained the TLS step.
  When dot2's `listen_port` / `tls_port` is on, certs are mandatory: missing material
  gives `audio_plane=false` with `missing TLS material`, not a silent half-start.
- **Per-device plane visibility** (`5e16026`): `GET /health.planes` returns one row
  per enabled device (`device`, `room`, `state` = `running|error|not_started`,
  `reason` = `multi_device_disabled|plane_not_started|audio_not_started`, `error`,
  `multi_device`); the old `audio_plane` boolean stays as the aggregate. `rui doctor`
  warns when 2+ devices are enabled and `multi_device` is off ("only the first
  configured device gets an audio plane; set `multi_device.enabled: true` and
  `max_planes >= 2`"), and `config.example.yaml` carries the example. Contract C-002
  A-8. 7 new tests; the 0.7.5 close-out baseline was **1659/17**.

**Measured** (`258d72a`; `docs/audit/tls-required-2026-10.md`, three matrices × 2 runs)

- dot2 with `tls_port` on and no `secrets/server.pem|key` → `audio_plane=false`,
  first error `missing TLS material`.
- lx06 + dot2 with no TLS and an empty `RUI_DEVICE_TOKEN` → `false`,
  `RUI_DEVICE_TOKEN is empty` (the default single plane starts only the first
  configured device).
- Certs + both tokens, dot2 first → `true`, `dot2 link: wss listening on 8871`.
- Self-signed only proves the audio plane can start; whether the speaker accepts it
  depends on the CA the device trusts, and the original controller certificate cannot
  be recreated.

### 0.7.6 — the LX06 bridge leaves core

**Changed**

- **No in-repo device plug, no adapter alias module.** `plugs/xiaoai/` is gone
  (moved to `tests/fixtures/legacy_lx06`, which is not shipped) and
  `src/rui/adapter_aliases.py` is deleted (`c713916`). The Dockerfile no longer
  `COPY plugs`; `/opt/rui-plugins` is owned by `rui:rui` and its writability is
  checked at build time. The 0.7.6 B1a close-out baseline was **1654/17**.
- **`adapter: xiaoai` migrates on load** (`c713916`): rewritten to `lx06` with a
  deprecation WARNING, surfaced by `/health.adapter_plugins.warnings` and, per device,
  as `legacy_adapter_spelling`. The default `adapter` is now `lx06` (same behaviour,
  no warning). The compatibility module `rui/config/legacy_adapters.py` (old spellings
  plus the implicit 4412 listen-port default) is the only file allowed to mention the
  old names and is deleted in 0.8.0.
- **Vendor wake-word fallback is data-driven** (`c2ed7fd`): the new optional
  `devices[].wake_words_residual` list declares the phrases the device itself says;
  ASR matching early-yields them. **Known behaviour change:** a manifest that does not
  declare the field no longer early-yields vendor phrases — only `is_echo` / `is_noise`
  apply. The built-in lx06 manifest declares three, so its behaviour is unchanged.
  Documented in `docs/socket-plug-v1.md`.
- **54 device-semantics tests moved to the bridge repo** (`300b2a9` / bridge
  `575b443`), byte-identical: credit 5, device-services 7, kws 5, led 18, playback 1,
  turn-audio 6, vendor 11, console-secret 1. Core went 1655/17 → **1612/6**; the bridge
  suite went 44 → **87 passed / 11 skipped** (+54 collected); the 20 generic
  `test_security_fixes` stay in core.

**Verified**

- Core grep gate `xiaoai|lx06|小爱|4412` over `src/**/*.py` is **0 hits** outside
  `legacy_adapters.py` (re-run on this tree).

### 0.7.7 — console audit (C)

**Fixed** (`db179f8`)

- **Element-by-element v2 audit**: **172 interaction rows / 159 usable / 12
  unverified / 0 unusable** (172 = 159 usable + 1 copy-error row + 12 unverified +
  0 unusable), 4 instances, 76 screenshots, no real device and no sound
  (`docs/audit/console-functional.md`). Four copy/validation groups fixed: setup
  first-run copy claiming an empty house, the stale legacy-console hint,
  dead `v2.config.stub*` i18n keys, and seven Chinese validation messages on device
  writes turned into English technical sentences per C-002 §4.4.

### 0.7.8 — the v2 console becomes the default (D)

**Changed** (`efab571`)

- **`web.console` defaults to `v2`.** A fresh install — and **an existing
  `config.yaml` that never wrote a `console` key — now silently serves v2 at `/`**
  (302 to `/v2/`, query preserved). Nothing in the config file changes; only the
  landing page does. `config.example.yaml` and `src/rui/config/template.yaml` now
  spell `console: v2` out. `CONFIG_VERSION` stays 3 and the contract window stays
  `>=0.6.0 <0.8.0`.
- **The 15 old console links answer 302 instead of the old shell**
  (`/panel/overview` … `/panel/music`, plus `/setup`, `/secrets`, `/diagnostics`,
  `/channels`). Mapping table and per-link evidence: `docs/audit/console-functional.md`
  §8.2; implementation `src/rui/web/api/legacy_links.py`. The three v1 pages v2 does
  not have yet (`providers`, `system`, `channels`) land on `/v2/?from=<old path>`,
  which the v2 SPA does not consume today (it redirects to `/v2/devices`).
- **The old console is now `/legacy` and marked deprecated.** A bilingual banner at
  the top says it is removed in 0.8.0; the shell is otherwise unchanged. With
  `web.console: legacy`, `/` and all 15 old links keep answering the old shell — that
  is the one-release rollback.
- **Docs**: `docs/deploy.md` §3.8 rewritten for the new default; README zh/en heroes
  both show the v2 devices page.

**Added**

- **Tests**: `tests/test_legacy_links.py` (the 15-path matrix plus query strings, tab
  sub-paths, unknown panel ids and the `legacy`-mode 200s) and an updated
  `tests/test_console_switch.py` (default v2, rollback, `/health.console` in both
  modes, shipped config examples).

### Evidence (final tree)

- house-ear **1642 passed / 6 skipped**; `ruff check .` green; `cd web && npm run
  typecheck && npm run build` green; `git diff --check` clean.
- Gates: 0.7.5 task-9/10/11 **PASS + VERIFIED**; 0.7.6 task-12/13/14 **PASS +
  VERIFIED**; 0.7.7 audit **PASS + VERIFIED** (reviewer-audit rounds R1/R2 closed);
  0.7.8 task-17 reviewer-d **PASS** + verifier-tests **VERIFIED** (`efab571`).
- Rollback verified live: with `web.console: legacy`, `/health.console = legacy` and
  `/` plus all 15 old links return 200 with the old shell (no redirect).

### Known limitations

- `providers`, `system` and `channels` have no v2 page in 0.7.8; their old links stop
  at the device page with `?from=` in the URL (marker only, not consumed — audit §8.9).
- Unchanged from 0.7.4: cue-echo edges, the single-session runner, non-recallable
  goodbye tails, per-room wake words, concurrent multi-device untested.
- Music credentials on a non-writable path now return **409** with a fixable message (was 500; fixed in 0.7.8)
  (conditional); v1-only gaps (phrase editor, provider cards/audition, music playback
  buttons, system/web/security fields, channels, tool registry, device probe) stay out
  of v2 for now; the setup id-collision editor is unreachable in the normal flow.
- From 0.7.6: a third-party manifest that omits `wake_words_residual` may let vendor
  phrases into a turn (see the behaviour change above).

## [0.7.4] - 2026-10-26

**Theme: P0/P1 fixes. One new status code (413); no other contract change.**

### Fixed

- **rui#18 - request bodies are bounded before parsing.** A pure ASGI middleware
  rejects a declared `Content-Length` over the cap (1 MiB default,
  `RUI_MAX_REQUEST_BYTES`) with **413**, and counts streamed bodies as they arrive
  so a chunked upload cannot grow memory past the cap either. Small bodies still
  reach auth first: missing/bad token stays **401**, invalid JSON with a valid token
  stays **422**. The 31 v1 handlers were not moved or rewritten.
- **rui#17 - `_greet` no longer blocks the event loop.** The wait for the plane's
  socket now yields every 10 ms, and the handshake `read_frame()` takes the remaining
  greet deadline as one absolute timeout (header + payload share it). A child that
  accepts the socket and then drops it (Errno 57 / reset / short read) is retried
  within the budget; if it exited, the error keeps its exit code and the last stderr
  lines. Measured: ticker 875 turns/s while waiting; cold-start overhead stays within
  the <=50 ms budget (repeated runs 1.5-3.6 ms).
- **rui#28 - the WeChat channel survives one bad message.** The getupdates cursor is
  saved in `finally` (a bad message no longer replays forever), voice downloads are
  capped at 10 MiB with a warning, and `pilk`/`ffmpeg` decoding runs in
  `asyncio.to_thread` instead of blocking the channel loop.
- **rui#25 / xiaoai#1 - reconnecting resets the player.** Replacing the speaker
  socket now resets `_playing` / `consumed` / `sink_generation`; a `play()` that was
  suspended in `start_play` cannot resurrect the old generation, and events from the
  replaced socket cannot advance the new player. Mirrored in
  `rui-xiaoai-bridge` **0.2.2** (version bumped; code only, no contract change).

### Changed

- `docs/contracts/C-002-console-v2-api.md` A-7 documents the 413/401/422 boundary.
- `rui-xiaoai-bridge` is **0.2.2** (reconnect reset, mirrored); `rui-dot2-bridge` stays **1.3.1** (no code change).
- No contract/window change. 413 is the only new status code; the runtime
  behaviour changes are the four fixes above. `rui_compat` stays
  `>=0.6.0 <0.8.0`.

### Evidence

- house-ear **1652 passed / 17 skipped**; rui-xiaoai-bridge **13 passed**;
  rui-dot2-bridge **145 passed**; `ruff` green in all three.
- Reverse mutations: removing any guard makes its targeted test fail (413 cap,
  cursor `finally`, `to_thread`, reconnect reset, suspended-play generation,
  replaced-socket events).

### Known limitations

Unchanged from 0.7.3: cue-echo edges, single-session dialogue runner, non-recallable
spoken goodbye, per-room wake-word isolation, untested concurrent multi-device
behaviour. New in 0.7.4: the WeChat **streamed** over-limit path is not covered by a
test (only `Content-Length` is); `getupdates` responses have no size cap; the 413
response carries the usual security headers except `Permissions-Policy`.

## [0.7.3] - 2026-10-26

**Theme: test baseline and reproducibility. No behaviour change.**

### Added

- **Coverage is now produced by every CI test job.** `pytest-cov` is in the `dev`
  extra; the Python 3.11 / 3.12 test legs upload `coverage.json`,
  `pytest-report.xml` and `python-version.txt` as `coverage-<py>` artifacts.
  `scripts/coverage_report.py` turns them into the package table and the
  machine-readable `docs/audit/coverage-baseline.json`; the 0.7.3 baseline is
  **80.95% line / 69.55% branch** (local Python 3.13, 120 files).
- **An independent `rui plug-test` smoke** runs in CI against
  `tests/fixtures/sample-plug` (a minimal manifest + fake adapter). It does not
  depend on `plugs/xiaoai`, which is scheduled for removal in 0.7.5.
- **Batch-1 unverified items are now automated tests**: HE-03 (a null
  `first_response_ms` stays "evidence insufficient", never 0), HE-12 (the
  source-run `plugs/` fallback authorises an in-process adapter) and D2-06
  (dot2 `stop_playback` on an active stream must not swallow the next reply).

### Changed

- Test counts are reported in one canonical form: collected / passed / skipped /
  xfail. For the 0.7.3 tree that is **1651 / 1634 / 17 / 0** for house-ear,
  **145 passed** for rui-dot2-bridge and **9 passed** for rui-xiaoai-bridge.
- `docs/audit/unverified-inventory.md` gained the per-package coverage ledger
  with a three-way gap classification, 26 real-device protocols (command /
  criterion / record table), and the eight document-contradiction resolutions.
- No runtime behaviour change. `rui_compat` stays `>=0.6.0 <0.8.0`; both bridges
  keep their current versions (1.3.1 / 0.2.1).

### Evidence

- Coverage: 80.95% line (18429/22767) / 69.55% branch (5158/7416); CI artifacts
  at `d21bb5f` reported 76.59% (3.11) and 77.59% (3.12) on their own interpreters.
- `ruff` green in all three repos; full suites green (numbers above).
- CI run at `d21bb5f`: `test (3.11)`, `test (3.12)`, `image` and the workflow all
  succeeded; `coverage-3.11` / `coverage-3.12` artifacts exist.

### Known limitations

Unchanged from 0.7.2 (see `docs/release/v0.7.2.md` §已知限制): cue-echo edges,
single-session dialogue runner, non-recallable spoken goodbye, per-room wake-word
isolation, untested concurrent multi-device behaviour.

## [0.7.2] - 2026-10-26

### Fixed

- **ack cue no longer reaches ASR.** The gate matches the cue envelope across the skip
  window *and* the hold buffer, learns the device's playback lag (EMA, updated only on
  first-pass hits), retries a narrow `EMA±200 ms` window when the first pass misses, and
  requires silence right after the cue (`_quiet_after`) so a TTS reply is never mistaken
  for it. Clipped captures fall back to a conservative `cue+1000 ms` excision. Measured on
  the device: cue leakage 0/10 (previously 13 of 26 turns).
- **Playback state and turn ownership are scoped per room.** One room playing music used to
  reject every other room's wake with `lost_playing` (18 rejections in six minutes of
  two-room sampling). Cross-room wakes are now admitted, `cross_echo` still guards a second
  plane inside the playing room, and `turn_open` carries the room that owns it.
- **Our own stale speech is dropped when a wake arrives.** A plain wake that landed while
  the goodbye TTS was still synthesised left those chunks queued, so the next ASR segment
  began with our own voice. The plane now flushes queued speech on an accepted wake, blocks
  further `PLAY_PCM` until that turn's utterance lands, and clears the block on every gate
  close path (idle, timeout disarm, our-own-audio disarm). Farewell synthesis no longer
  clears the abort flag, so an aborted goodbye stays aborted.

### Changed

- Release tooling: `rui_compat` window stays `>=0.6.0 <0.8.0`.

### Known limitations

- Cue echo can still reach ASR on a cold start with no clipping and >800 ms device latency,
  or when the user overlaps the cue by less than 110 ms.
- The dialogue runner is single-session: a wake on one device aborts a reply being
  synthesised for another.

## [0.6.0] - 2026-10-26

The device-agnostic core: rui names no speaker any more. The LX06 adapter became an image
plug with the same manifest loader as the Dot 2, the core audio defaults are neutral, and a
device's manifest, the global config and the per-device override merge into one effective
audio block that every consumer (plane, AEC, doctor) reads.

### Added

- rui-device/v1 `audio_defaults` (capture, playback, ack_echo_ignore_ms): the bottom layer of
  a three-layer merge (device default < global config < devices[].audio).
- Image plugs under /opt/rui-plugins are imported from their runtime.entrypoint and listed
  beside each other; RUI_PLUGINS_DIR adds roots without replacing the image directory.
- The setup wizard asks which device to configure when more than one plug is installed
  (409 plug_required carries the list); a single plug auto-selects.
- /api/v1/devices[+id] expose the merged per-device audio and the explicit override paths.

### Changed

- Core capture defaults are neutral (16000/1ch/0ref); the LX06 manifest declares
  48000/8ch/6mic/2ref. Existing configs keep their effective values (golden tests).
- Every output is routed by room: session/LED follow the turn, reminders go to
  multi_device.default_output, music starts where it was asked and transport follows the
  owner; stop only releases the owner from its own room.
- save_config no longer materializes neutral audio defaults over the plug layer.

### Removed

- src/rui/device/xiaoai and the v0 reference plugin package. `adapter: xiaoai` still resolves
  to the same plug through adapter_aliases.py (one minor, gone no earlier than 0.7.0).

## [0.5.3] - 2026-10-25

Stabilization release for the first multi-device production install (LX06 + Echo Dot 2).
Every item below was measured on that install and has a regression test. No config-schema
break (one nullable per-device field), no plug-contract change.

### Fixed

- **A device's own audio settings are the ones that play.** The plane's playback path read
  the global `audio.playback.rate`, so a per-device override (Dot 2 = 48 kHz) was raised at
  speak time: the turn finished, the ring said `speaking`, no sound. The play path, the wake
  cue and the pacer now use the device's effective audio; the pre-rendered cue is resampled.
- **The house-wide session speaks in the room that spoke.** The idle goodbye and its LED used
  the primary client, so the LX06 said farewell for a Dot 2 turn. Session speak / LED / stop
  follow `turn_device`, and a new wake cancels the goodbye instead of racing it.
- **A goodbye can no longer lock the house.** A goodbye between wake detection and admission
  disarmed the new wake gate, so `on_wake_idle` never ran and `turn_open` stayed true: every
  later wake was rejected. The idle loop skips while a wake is in flight or a turn is open,
  and clears `turn_open` after the goodbye.
- **A speaker no longer answers itself.** The wake cue used a global 1.2 s echo window; a
  device-AEC speaker leaks longer, and the Dot 2 turned its own `在呢。` into a request.
  `devices[].audio.voice.ack_echo_ignore_ms` lets a device declare its own window (Dot 2:
  2500 ms), and the goodbye now sends `reply_finished` so the post-playback mute arms.

### Added

- **Doctor rows for the three failures this install hit**: `audio_rates` (wire rate vs
  effective playback rate per device), `channels` (gateway last error, e.g. a Telegram 409
  from a second poller), `console_token` (`console-token.txt` vs `web.auth_token`).
- `docs/deploy.md`: multi-device template (rooms, per-device rate/AEC/mute, `max_planes`,
  `default_output`, one bot per poller).

## [0.5.2] - 2026-10-25

Patch release for the install path and for satellite management in the console. Found
by dot2's 0.5.1 clean-install retest (#15) and by adding Dot 2 satellites next to the
LX06 in the device manager (#7). The audio/arbitration code and the plug contract are
identical to 0.5.1: no config-schema change, no migration.

### Fixed

- **The documented `curl … | sh` install no longer dies halfway (dot2 #15).**
  `docker compose exec -T` attaches stdin, and with a piped installer stdin *is* the
  rest of the script, so the health probe ate the remaining bytes: the run printed
  `healthy`, then `Syntax error: Unterminated quoted string`, exit 2, and no
  `console-token.txt`. Both exec sites (the probe and the `rui token` read) now
  redirect `</dev/null`. A regression test runs the shipped `install.sh` from a pipe
  against a fake docker that drains stdin and asserts exit 0, the closing block and a
  0600 token file; a second test fails if any future `docker compose exec` forgets the
  redirect. The one-liner is unchanged, because it now works.
- **A connected satellite is `ready` even when its token lives outside rui (dot2 #7).**
  `GET /api/v1/devices/{id}/status` used to report `ready=false` + `token_missing` for
  a device whose adapter owns the credential (the dot2 plug reads a 0600 file). A live
  link is proof that authentication succeeded, so the row now carries
  `token_managed_by_adapter: true` instead of a blocking error.

### Added

- **The connection test follows the transport.** An inbound speaker (LX06, Dot 2)
  dials us and runs no SSH on the Dot 2, so probing `:22` used to fail a perfectly
  healthy satellite. The host row is now `not_applicable` for inbound devices (the
  port and the live link are the evidence), the test waits up to 3 s for a device that
  is still dialling in after a save/restart, and the response reports `transport`,
  `listen_port` and `waited_ms`. Outbound adapters keep the TCP + SSH-banner probe.
- **Adding a second speaker prefills a free id.** The console used to start every new
  device at `living`, which collides with the main speaker; it now picks `living`, then
  `satellite`, then `satelliteN`.

## [0.5.1] - 2026-10-25

Patch release for the install path, found by dot2's clean 0.5.0 install. There is no
runtime, config-schema or plug-contract change: the audio/arbitration code is identical
to 0.5.0. `install.sh` no longer executes backticks from its `.env` template (which could
start an unrelated compose project from the caller's directory) and never prints the
console token; `fetch-models` retries transient 429/5xx, the installer fails non-zero
instead of booting without ASR, and `rui doctor` reports local ASR/VAD/KWS readiness.
The README also gains a copy-paste agent-install prompt and refreshed 0.5.0 console
screenshots.

### Added

- **Agent-friendly install entry point.** The README now carries a copy-paste prompt
  (zh + en): a command-capable agent is told to read the public docs first, install with
  the official `install.sh` at a pinned tag, ask the operator for the required settings
  and keys, and verify with `rui-healthcheck` plus one real turn.
- **Doctor reports model readiness.** A `models` row checks the local sherpa ASR model +
  tokens, the configured VAD file and the KWS `tokens.txt`; missing models surface as
  `degraded` instead of only a WARNING in the install log (dot2 #14.3).

### Fixed

- **`install.sh` wrote `.env` from an unquoted heredoc.** Backticks in the template
  comments were command-substituted during install - on a machine whose CWD was another
  compose project that started it - and the printed setup output leaked the console token
  to stdout. The template is now a quoted heredoc (values appended by `printf`),
  `rui setup` never prints the token, the installer still persists it to
  `console-token.txt` (0600), and tests pin the byte-identical template + no canary
  execution + no token on stdout (dot2 #13).
- **`fetch-models` could fail silently and the installer still exited 0.** A transient
  hf-mirror 429 aborted the ASR download with only a WARNING, so the container looked
  green while ASR was missing. Downloads now retry 429/5xx up to 4 times (1/2/4 s
  backoff, 404/401 fail fast to the next mirror); the installer fails non-zero after
  printing remediation, with `RUI_SKIP_MODELS=1` as the documented opt-out (dot2 #14).
## [0.5.0] - 2026-10-25

Multi-device 0.5: one control plane, one audio plane per room, one arbiter for the
house. Config v3 adds per-device audio overrides and the `multi_device` block (off by
default), Phase 2 wires the fleet and the wake candidate -> admit/reject chain, and the
console gains per-plane health plus the restart-effect knobs behind it. Adapter and
manifest contracts, `drain_wakes()` timing and the `DeviceWake` six fields are
unchanged; downstream plugs only need their `rui_compat` ceiling raised to `<0.6.0`.

### Added

- **Config v3, Phase 1 — per-device audio overrides, still one active plane.**
  `devices[].audio` can now override the global `audio` block for one device: `aec`
  (including the new `aec.who: device｜controller｜both｜none`), `voice.followup_delay_ms`
  / `voice.followup_replay`, `capture` and `playback.prebuffer_ms`. Unknown keys are
  rejected (`extra="forbid"`; EQ/limiter stay in plug `params`, not in the core).
  `effective_audio()` merges an override over the global defaults and re-validates the
  merged block, so a partial capture override that no longer adds up fails at load time
  instead of crashing the plane. Frozen decisions: `docs/multi-device-design.md` §9.
- **`multi_device` config block (parsing and validation only).** `enabled` (default
  `false`), `max_planes` (default 4), `default_output`, and `arbitration`
  (`source_priority` default `[device_wake, kws]`, `candidate_merge_window_ms` default
  400, `duplicate_window_ms` default 2000, `room_priority`, `prefer_playing_room`,
  `last_active_window_s`, `on_no_speech`). Unknown ids in `default_output` /
  `room_priority`, duplicate priorities and `room`-less devices with
  `multi_device.enabled=true` are rejected; `config_warnings()` reports legal-but-wrong
  combinations (double AEC cancellation, and more enabled devices than `max_planes`).
- **v2 configs stay v2.** A v2 file loads with identical behaviour and is only written
  as `version: 3` once a v3-only field is actually used, so rolling back to a 0.4.x
  image keeps working without editing the file.
- **Read-only config view on the System page.** `GET /api/v1/config/raw` returns the
  effective config (schema defaults + `config.yaml`) as YAML with credential values
  replaced server-side by `<redacted>` and named in `redacted_keys`; the System panel
  shows it in a read-only monospace block with copy, path + mtime and a redaction note.
  The endpoint is GET-only — there is no write path — and the payload is capped at
  200,000 characters with a valid YAML notice instead of a truncated document.
  `tests/test_config_raw.py`.
- **Home Assistant "Test connection" accepts the token typed in the form.** The probe
  used to resolve only the *stored* secret, so a first-time setup — or a replacement
  token — always failed with 401 until the operator saved a token they could not test.
  `POST /api/v1/mcp/test {"base_url", "token"}` now probes with the one-shot token and
  never stores it; an empty token still falls back to `RUI_HA_TOKEN`. The console passes
  the typed value, so "fill in address + token → Test" works before saving.
  MCP transport failures are also unwrapped for the UI: `401 Unauthorized` instead of
  `unhandled errors in a TaskGroup (1 sub-exception)`.
- **Save-and-reconnect keeps its update semantics.** The HA card's save writes the URL
  and (when typed) the token, replaces the stored secret, closes the old session and
  reconnects — that card is the one place to rotate the HA token. New regression tests
  pin it: a replacement token overwrites the old one, leaving the token field empty
  keeps the stored value, and a new URL + token apply in a single save.

- **Phase 2 — one audio plane per room, arbitrated in the control plane.** With
  `multi_device.enabled: true` the app starts a `PlaneFleet` (one supervised child per
  enabled inbound device, each with its own socket and crash watchdog) and wires every
  plane into the turn path through `_wire_plane()`; `false` keeps the 0.4.x
  single-plane path unchanged. Planes report `wake_candidate` without arming anything;
  the `WakeArbiter` (room priority -> source group -> score margin -> last active ->
  device order) and `WakeCoordinator` answer `wake_admit`/`wake_reject`, so one house
  has one turn and one output owner. Barge is only accepted from the playing owner; a
  second device in the same room is suppressed as cross-echo; the same word inside
  `duplicate_window_ms` is a duplicate; a plane that gets no verdict drops its
  candidate (`admit_timeout`) instead of starting a turn on its own; `max_planes`
  refuses extras with an explicit warning (no queueing), and one crashed plane does not
  take the other rooms down.
- **Per-device audio that the plane can win with.** `devices[].audio.aec.who` decides
  which side cancels echo (`device`/`controller`/`both`/`none`), and
  `devices[].audio.voice.post_playback_mute_ms` drops the microphone for a window after
  the speaker drains (default 0; the plane skips the follow-up gate when the window is
  at least `followup_delay_ms`). Barge during playback is unaffected.
- **Health and status per plane, without breaking the old contract.** `/health` keeps
  every old top-level field (`devices` is still the count) and adds `audio_devices[]`
  rows (`running`, `speaker_connected`, `restarts`, `error`, `wake_candidates`,
  `wake_admitted`, `wake_admit_timeouts`, `wake_rejected`, `utterances`) plus a
  `multi_device` block. A room whose plane is down produces
  `plane_not_running`/`fleet_start_failed`/`over_max_planes` warnings instead of the
  old `multiple_enabled_inbound` notice, and `/api/v1/audio` gained a `planes` map.
  `speaker_connected` means *any* room is connected, so one dead room does not mark the
  container unhealthy. Device rows, status and the connection test read that device's
  own plane instead of the aggregate.
- **Console visibility for multi-device, and the knobs behind it.** The Health page now
  has a Multi-device card: "off" with the 0.4.x single-plane explanation when
  `multi_device.enabled` is false, and otherwise one row per plane (running/connected,
  restarts, wake candidates/admitted), the devices refused over `max_planes`, and any
  fleet start error. The config directory gained the `multi_device` group (`enabled`,
  `max_planes`, `default_output`, source priority, both arbitration windows, room
  priority, last-active window) and per-device `audio.*` overrides (AEC ownership and
  switch, post-playback mute, follow-up window/replay, playback prebuffer, capture
  layout) - all restart-effect. `arbitration.mode`, `prefer_playing_room` and
  `on_no_speech` are deliberately not exposed: the arbiter freezes them
  (score/true/end) and a switch that changes nothing is a fake knob. An empty override
  means "inherit", and writing one into a device with no audio block now creates it.
- **Healthcheck names the root cause instead of suggesting a restart.** A downstream
  adapter that cannot be built (`device_errors > 0`), a fleet that failed to start
  (`multi_device.error`) and a specific room whose plane is down (`audio_devices[]` with
  the child's error) each produce their own sentence; only a plain dead plane still
  suggests `docker compose restart rui`. The success line also reports
  `planes=up/total connected=n`. `GET /api/v1/doctor` gained a
  `container_healthcheck` row that re-evaluates the same `/health` body, so the doctor
  and the container probe cannot disagree (dot2 #12 C3/C4).
- **Upgrade hints that cannot silently move the pin.** Re-running `deploy/install.sh`
  over an existing `.env` now prints the pinned `RUI_IMAGE` and says that upgrading is
  an explicit edit (`docker compose pull && docker compose up -d`); the final summary
  points at `docker image prune -f` for old rollback images (dot2 #12 C2/K2).
- **Doctor annotates `container_runtime: none`** as expected inside the image (it ships
  no docker CLI) instead of telling the operator to install Docker in the container
  (dot2 #12 K5). The 0.4.2 release note's leftover verification placeholder comment is
  gone (K1).
- **Downstream upgrade guide.** `docs/deploy.md` §7.1 covers upgrading an install with
  an out-of-tree adapter/plug: healthcheck failure semantics, `rui_compat` alignment
  with a compatibility table, customised compose files, vanilla ↔ downstream image
  switching (idle `DOT2_*` variables are harmless on the vanilla image), and the one
  allowed volume conversion helper (dot2 #12 C1/C5).

### Changed

- **`rui_compat` ceiling raised to `<0.6.0`** for the built-in LX06 plug, the example
  plug and their tests: without it a 0.5.0 core would refuse the bundled plug and fail
  to start. The matching semantics (version range) are unchanged.

### Fixed

- **`PlaneHandle` lost `on_wake_idle` on every plane restart.** The callback was set on
  the facade but never re-applied to the new connection, so the "wake heard nothing"
  path went silent after a crash; `on_wake_candidate` is rebound the same way now.

<!-- Add changes here as they land; move them under the next version at release time. -->

## [0.4.2] - 2026-10-25

Console IA Phase 4 cleanup. Navigation now comes from one backend manifest, the seven
compatibility-only panels and their redirects are retired, and the homepage
documentation is slimmer. No config-schema change and no audio/runtime change.

### Added

- **One navigation manifest.** Every `PanelSpec` from `GET /api/v1/panels` now carries
  `nav: {section, order, hidden, icon}` (sections `run` / `configure`). The rail and
  the page kicker read it directly, and a panel this build does not know is placed by
  the backend instead of being guessed into "configure". Nav icons are validated
  against the shell's `AppIcon` vocabulary.
- **Multi-device design document.** `docs/multi-device-design.md` is a design-only
  proposal for rooms, the primary/satellite question and double-wake arbitration:
  current 0.4.1 code facts, four options, a recommended hub arbitration with one audio
  plane per device, a `config v3` sketch, a phased plan and open decisions. No runtime
  change ships with it.
- **README 0.4.2 section** covering the Phase 4 result and the new navigation model.

### Changed

- **Eleven panels, seven retired.** The compatibility-only declarations
  `assistant-session`, `speaker`, `devices-admin`, `tools`, `mcp`, `foundation` and
  `phrases` were removed after their one-release window in 0.4.0/0.4.1. Their content
  lives on the merged destinations (voice-assistant, devices, integrations, system);
  `/panel/credentials` and `/secrets` remain for the write-only ledger.
- **Legacy console URLs are gone, intentionally.** Old `/panel/<legacy-id>` URLs
  (including `/panel/settings`) no longer redirect; they render the console's
  "No such panel" state with a link back to the overview. The removed top-level
  aliases (`/overview`, `/devices`, `/logs`, `/assistant`, `/settings`, `/voice`,
  `/models`, `/tools`) fall through the SPA catch-all to `/panel/overview`. Update
  bookmarks; this ends the compatibility window announced in 0.4.0.
- **Providers page renamed** from "外部资源 / Providers" to
  "模型与语音服务 / Models & voice services"; the id `/panel/providers` is unchanged.
- **Duplicate navigation code removed.** The id→group map, hidden-panel set, active
  alias map and per-id title overrides (`PANEL_NAV_GROUPS`, `HIDDEN_NAV_PANELS`,
  `NAV_ACTIVE_ALIASES`, `NAV_GROUPS`, `PANEL_TITLE_KEYS`, `GROUP_KEY`) are deleted.
  `web/src/nav.ts` keeps only the section titles and the three hand-written utility
  routes (channels, diagnostics, first-run setup).
- **README slimmed** from 512 to 366 lines: install, wizard and troubleshooting detail
  moved to `docs/first-run.md`; the screenshot refresh is deferred to the next content
  pass. The compact docs index and the 0.4.2 "what's new" block replace the long page.

### Fixed

- **Navigation drift.** A panel's rail placement, page kicker and title used to be
  declared in three places (backend group, frontend mapping, i18n override), so a
  backend change could leave the rail and the page disagreeing (the Logs case found in
  the IA audit). The backend `nav` object and backend titles are now the only inputs.
- **No setting lost its home.** The fields the deleted pages used to edit still render
  on the merged pages: assistant/session on the voice-assistant reply tab, speaker
  hardware on the devices audio tab, process/console/SSRF in `/panel/system`, and
  `system.log_level` on the logs page.
- **Unknown nav icons** now fail the panel contract tests instead of silently rendering
  a blank icon in the rail.

### Upgrade notes

- **From 0.4.1:** no config migration, no chown and no config-schema change. Pull and
  recreate; this release changes the console and docs only. Audio, providers, music and
  device behaviour are unchanged.
- **Bookmarks:** the old panel URLs now show "No such panel" (intentional). Use
  `/panel/voice-assistant` (assistant/session/phrases), `/panel/devices`
  (status/audio/manage), `/panel/integrations` (tools/MCP/HA) and `/panel/system`
  (process/console/SSRF).
- **API:** `/api/v1/*` paths are unchanged. `GET /api/v1/panels` gains the additive
  `nav` object and no longer lists the seven retired panel ids; integrations that
  enumerate panels should read `nav` and tolerate a smaller list.
- **Rollback:** the config is unchanged, so a 0.4.1 image can run the same data
  directory; old bookmarks regain their redirects if you roll back.

## [0.4.1] - 2026-10-25

Compatibility polish for the 0.4.0 console: Jellyfin and Emby join the music-source
registry, the first-login console token can be recovered without scrolling back through
the install log, and external plugs such as dot2 report the port and token environment
they actually use.

### Added

- **Jellyfin and Emby music sources.** A second protocol family
  (`JellyfinSource`, MediaBrowser HTTP API) behind two presets: `jellyfin` (API key
  only in the `Authorization: MediaBrowser Token="…"` header) and `emby` (API key in
  `?api_key=`). Search, random, favourites, star/unstar, library paging, album songs and
  stream/resolve; the adapter discovers the user id itself and needs only `url` plus a
  write-only `api_key`. The registry grows to seven presets. Verified against live
  Jellyfin 12.3.0 and Emby 4.10.2 servers.
- **Console-token recovery (issue #4).** After the service is healthy,
  `deploy/install.sh` saves the console token to `~/rui/console-token.txt` (mode 600).
  A rerun keeps an existing file byte for byte and warns when it no longer matches the
  running token. The 401 login panel now shows the `rui token` /
  `cat console-token.txt` recovery commands and links to the deploy docs.
- **Follow-up window controls.** `audio.voice.followup_delay_ms` (0–10000 ms, default
  1000) and `audio.voice.followup_replay` (default on) are editable in the config
  center. Defaults keep the measured LX06 behaviour; a device-AEC plug (no reference
  channel, RUI AEC off) can use a longer window and skip replaying the gated tail.
- **Cold-start device_wake re-probe.** When a trusted plug manifest declares
  `wake.source=device` but the first probe ran before the device dialled in, the audio
  plane now re-probes in the background (bounded: 12 attempts, 5 s apart) and arms the
  v1 device-wake path once the adapter proves it.
- **First-run documentation.** New bilingual `docs/first-run.md` covers the device
  checklist, install commands, the 8-step wizard, where each parameter comes from,
  connect troubleshooting, post-install commands and common failures. README rewritten
  around the current 0.4 state.

### Changed

- **Device management reports effective plug defaults.** `GET /api/v1/devices`,
  `/{id}`, `/{id}/status`, `/{id}/connect` and the setup wizard's `connect` evidence
  resolve `listen_port` and `token_env` the same way the adapter builder does:
  explicit config → plug manifest `runtime` → loader-cached default → LX06 literals
  (4412 / `RUI_DEVICE_TOKEN`). Dot2 therefore reports `8870` / `DOT2_EM_TOKEN` even
  when the config leaves both unset.
- **Token rotation writes to the effective environment name**, so rotating a dot2 device
  stores the secret under `DOT2_EM_TOKEN` rather than `RUI_DEVICE_TOKEN`.
- **Port-conflict detection uses the effective manifest port.** Dot2 on 8870 and an LX06
  on 4412 no longer report a false conflict; two devices resolving to the same manifest
  port still conflict.
- **README, deploy, runbook and music docs refreshed** for the current release, the
  pinned image version, the token file and both music protocol families (auth styles,
  credential slots, cache isolation, test commands).

### Fixed

- **First-login console token:** the only copy used to be one line in the install log; it
  is now persisted 0600 and reachable from the 401 dialog.
- **Dot2 status/connect/rotation** fell back to `RUI_DEVICE_TOKEN` and `4412` when the
  plug manifest carried its own runtime defaults.
- **Dot2 cold start** could leave RUI's own KWS in charge for the whole process lifetime
  when `probe()` only reports device_wake after the device connects.
- **Follow-up gate** no longer hard-codes a 1 s delay and a 2 s capture cap, so a
  device-AEC plug's longer window is not truncated.
- **Jellyfin cache collisions:** the PCM cache key includes the preset id, so Emby's
  small integer ids cannot collide with Subsonic numeric ids when switching sources.

### Security

- **Jellyfin/Emby API keys are write-only** and sent only where the preset requires
  (header for Jellyfin, query string for Emby); error text and logs redact `api_key`
  and query strings, and no GET returns the value.
- **The console token file is mode 600**, is never overwritten by a rerun, and is
  explicitly separate from the device token in `.env`.

### Upgrade notes

- **From 0.4.0:** no config migration and no chown. Pull and recreate; the new
  `followup_*` fields stay absent until set and their defaults preserve the LX06
  behaviour (1 s gate, replay on).
- **Existing installs that want the token file:** create it once with
  `cd ~/rui && docker compose exec -T rui rui token > console-token.txt && chmod 600 console-token.txt`.
  Re-running `install.sh` will not overwrite an existing file and warns on a mismatch.
- **Music:** Subsonic credentials keep working. Switching to Jellyfin/Emby changes the
  credential slots to `url` + `api_key`; the old `user`/`password` fields are
  ignored for those presets.
- **Dot2:** update the plug and restart the service so the manifest runtime defaults are
  picked up. Set `audio.voice.followup_delay_ms` to about 2500 (or turn
  `followup_replay` off) if device-side AEC leaves residual echo in the follow-up
  window.
- **Rollback:** back up `config/config.yaml` first. A 0.4.0 build ignores the new
  optional fields; `console-token.txt` is not read by 0.4.0.

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

[Unreleased]: https://github.com/yes999zc/rui/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/yes999zc/rui/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/yes999zc/rui/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/yes999zc/rui/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/yes999zc/rui/compare/v0.4.0...v0.4.1
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