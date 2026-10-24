# Socket & Plug v1 — Implementation & Security Audit

- **Repo:** `01_Code/projects/house-ear`, commit `4b279a7`, RUI 0.3.2
- **Date:** 2026-10-25
- **Mode:** read-only audit. No code, commits, or NAS changes were made. The only file
  created by this audit is this report.
- **Working-tree note:** the checkout was already dirty when audited. Pre-existing
  changes in `src/rui/providers/chain.py`, `src/rui/providers/keycheck.py`,
  `src/rui/session/turn.py`, `web/src/{App.vue,components/SideNav.vue,i18n.ts,panels.ts}`
  and the untracked `docs/socket-plug-audit.md` were not touched. None of the files in
  this audit's scope were modified; line references are to the current working tree.
- **Scope:** `src/rui/device/{contract,loader,registry,mock}.py`,
  `src/rui/plugins/*`, `src/rui/audio/{process,protocol}.py`, `src/rui/trace.py`,
  `plugins/rui-plugin-xiaoai/`, `examples/plugin-example/`,
  `tests/{test_device_wake,test_plug_contract,test_adapter_plugins,test_reference_plugin,test_plugins}.py`,
  plus the LX06 adapter and the CI/security workflows.
- **Method:** source review, targeted inspection of the production call paths, and
  small standalone repros against the checked-out tree (temporary files only under
  `/tmp`). The five named test files (110 tests) pass. The full suite (1007 collected)
  produced one non-scope timing flake: `tests/test_audio_plane.py:67` saw
  `jitter_p50_ms=25.48` against `20 ± 5` on this loaded macOS host; an isolated rerun
  passed. That flake is recorded under P2-17.

## Verdict

**No P0 finding.** The v1 design is sound in its main security decisions: the device
event is advice, the VAD gate is mandatory, manifest v0/v1 is validated strictly, the
YAML loader is safe, `devices:` paths are contained, and the env hook is not reachable
from config/console input. The issues below are concentration points around fail-soft
boundaries (a broken adapter can take down capture or an API), one documented-but-not-
enforced trust rule, and the official downstream registration path failing for the
repository's own two reference adapters.

| Severity | Count | Character |
|---|---|---|
| P0 | 0 | no service-wide compromise or data-loss path found |
| P1 | 9 | broken advertised feature, silent deafness, console 500, policy bypass, LAN DoS |
| P2 | 17 | robustness/observability/test-kit hardening, documentation and CI gaps |

## Trust boundary assessment (short)

- **Can config/user/console input influence `RUI_ADAPTER_PLUGINS`?** No in this tree.
  `device/loader.py:103` calls `env.get()` (`src/rui/env.py:45-51`), which reads
  `os.environ` only. The console writes `secrets.yaml` (`src/rui/secrets.py:131-160`),
  and `env.get()` does not consult it. No `os.environ[...] = ...` write exists in
  `src/rui`; the audio child inherits the environment from the web process
  (`src/rui/audio/client.py:406-418`, no `env=` argument). The host `.env`/container
  environment is therefore the operator boundary, as documented.
- **Residual boundary:** the second in-process source, `rui.adapters` entry points, is
  discovered from every installed distribution and is not tied to manifest trust. See
  P1-9. Any installed wheel is code execution at startup; the documentation should say
  so explicitly or gate/allowlist it.
- **In-process plugins can read all provider/device secrets in the environment.** This
  is documented (`docs/socket-plug-v1.md:235`); it is a real consequence of in-process
  loading, not a code bug.
- **`devices[].params`, `plugin.yaml`, console input and the network never choose the
  adapter module.** The loader reads only the env/entry-point specs. The audio plane does
  read `plugin.yaml` later for the `wake.source` upper bound
  (`src/rui/audio/process.py:697-721`), but never imports it.
- **Fail-soft is only partial.** Import/registration failures are contained
  (`loader.py:95-120`), but runtime failures in `probe()`, `frames()` and
  `drain_signals()` are not (P1-2/P1-3/P1-4). A broken adapter can be silent instead of
  fail-soft.
- **Path traversal:** `devices:` entries are resolved and checked against the plugin
  root, including escaping symlinks (`src/rui/plugins/loader.py:104-130`). The
  `plugin.yaml` file itself is not resolved through that check (P2-9).
- **Size / YAML:** 1 MiB per manifest, UTF-8, top-level mapping, `YAML(typ="safe")`
  (`loader.py:41,133-160`). No total/count limit and a stat/read TOCTOU remain (P2-9).
- **Secrets in logs:** device-wake logs contain score/action, not audio, text or token.
  Loader warnings can contain exception text (a plugin can itself print anything), and
  `/health` exposes only names/counts (`src/rui/web/api/app.py:2047-2050`). The LX06
  adapter logs the peer IP on connect/reject (`src/rui/device/xiaoai/adapter.py:332,341`),
  which is mildly inconsistent with `docs/socket-plug-v1.md:274` ("no full device
  address").
- **Plug-test:** imports the adapter into the CLI process with full environment and no
  sandbox (P2-14). `docs/socket-plug-v1.md:309` says "subprocess/fake environment",
  which is not true; `docs/plug-test-kit.md:22` correctly says in-process.

---

## P1 findings

### P1-1 — `RUI_ADAPTER_PLUGINS` cannot register either bundled adapter (optional-arg builder misclassified as a factory)

**Where:** `src/rui/device/loader.py:337-361` (`_call_kind`), `:221-240`
(`_apply`), `:199-237`; builders at `plugins/rui-plugin-xiaoai/rui_plugin_xiaoai/adapter.py:38`
and `examples/plugin-example/rui_plugin_example.py:160`.

**What:** `_call_kind` treats any callable with no *required* positional argument as a
zero-argument factory. Both reference adapters declare
`def build(dev=None, config=None)`, so at startup the loader calls `build()` with no
device/config, gets an adapter instance, and warns that a factory must return a builder.
The adapter is never registered, and the construction may already have had side effects.
The contract tool (`plugins/contract.py:1149-1193`) passes because it inspects the
parameter count and calls with two fake arguments; the production env hook does not.
The same applies to an entry point pointing at such a builder.

**Repro (confirmed):**

```bash
PYTHONPATH=plugins/rui-plugin-xiaoai \
RUI_ADAPTER_PLUGINS=lxref=rui_plugin_xiaoai.adapter:build \
.venv/bin/python -c 'from rui.device.loader import load_adapter_plugins; print(load_adapter_plugins().warnings)'
# ["... factory returned XiaoaiAdapter, expected a builder ..."], registered == {}
```

Same result for `PYTHONPATH=examples/plugin-example RUI_ADAPTER_PLUGINS=ex=rui_plugin_example:build`.

**Impact:** the official out-of-tree path fails for the exact packages shipped as
examples; a downstream author copying them gets a fail-soft warning instead of a
working adapter, and a builder with side effects is called at the wrong time.

**Fix:** classify by declared role, not only by required arguments. Suggested rule:
`register*` → hook; `factory` → factory; `build`/`builder`/`adapter` with **at least one
positional parameter** (required or defaulted) → builder; a callable with **zero
parameters** → factory. Never call an object that has device/config parameters at
startup. Also include the explicit `name=` in the source string.

**Test:** add a loader test using `build(spec=None, config=None)` (and the two repo
reference plugins through `PYTHONPATH`) and assert
`report.registered == {"lxref": ...}` and `registry.build_adapters(cfg)` returns the
adapter. Add the same case to the entry-point test.

### P1-2 — A broken `drain_signals()` kills the capture task silently (plane stays "up" and deaf)

**Where:** `src/rui/audio/process.py:628-631` (`_drain_signals`), `:588-608` (per-frame
call), `:554-568` (`_capture`), `:2141-2146` (tasks are created but never observed).

**What:** `_drain_signals` does `list(drain())` with no `try/except`, no per-item type
check and no bound. An exception, a non-iterable, a bad item, or a non-finite/invalid
`ts` propagates out of `_capture`. Nothing observes the task's exception; the process,
socket, `Ready` and metrics loop stay alive, so `AudioPlaneSupervisor.running` remains
true and the watchdog never restarts. The room simply goes deaf.
`drain_wakes()` has a contained counterpart at `:723-773`; `drain_signals` does not.

**Repro (confirmed):**

```python
class BadSig(MockAdapter):
    def drain_signals(self):
        raise RuntimeError("vendor signal boom")
# run plane._capture(...); it raises RuntimeError out of the task
```

A wrong item (e.g. `SimpleNamespace()` without `.ts`) or a single `VendorSignal` instead
of a list fails the same way.

**Impact:** a third-party adapter bug (or a transient device-code error) makes the
speaker permanently deaf while all health signals look green. This is the worst
fail-soft failure in the audited surface.

**Fix:** wrap the whole drain, validate each item with `getattr` defaults and finite
checks, drop/count bad items, cap the number processed per tick, and never let adapter
input escape `_capture` (same contract as `drain_wakes`). Additionally, supervise
`_capture`: a done callback should send `Error`, set `_stop`, close the connection, or
restart capture, so the supervisor can rebuild.

**Test:** `test_drain_signals_raising_does_not_stop_capture`: adapter raises on the
first frame; feed several frames and assert the loop continues, `device_wake`/gate
counters advance, and an error counter increments. Add a non-list and a malformed-item
variant.

### P1-3 — `frames()`/`probe()` failure is also a silent capture death

**Where:** `src/rui/audio/process.py:554-568`; task creation `:2141-2146`; cleanup only
on control-loop exit `:2262-2280`.

**What:** `_capture` calls `probe()` a second time (`:556`) and iterates
`adapter.frames()` (`:568`). If either raises (device disconnect, decoder error,
numpy/reshape failure, a plugin bug), the exception dies with the task. The control
loop keeps reading the socket and the metrics loop keeps sending; the supervisor sees a
live process, a live socket and a `Ready`, so `running` is true.

**Repro:** adapter whose `frames()` yields three frames then raises `RuntimeError`;
launch `rui audio` (or call `_capture` directly); process stays up, `/health` stays
green, no voice frames after the exception.

**Impact:** same silent-deafness class as P1-2; a device that reconnects or a decoder
that fails once is never retried. The LX06 is especially exposed because its
`frames()` waits forever with a 1 s timeout (`src/rui/device/xiaoai/adapter.py:169-176`)
and its handler can drop the connection on malformed text (P2-8).

**Fix:** add a `task.add_done_callback` for `capture`, `send-audio`, `metrics` and
`speak`; on unexpected exit, send `Error`, set `_stop` (or restart only capture with a
backoff), and close the control connection so the supervisor restarts the plane. Use the
`info` already obtained in `handle()` instead of probing twice. Also bound `_capture`
background calls (`stop_playback`, `frames`, `probe`) with `wait_for`.

**Test:** `test_frames_exception_marks_plane_down`: fake adapter raises after N frames;
assert the task callback sets an error/stop and `/health` (or supervisor state) no longer
claims a live capture. Add a `probe()` raise variant.

### P1-4 — `probe()` failure makes `/api/v1/devices`, `/{id}` and `/{id}/status` return 500

**Where:** `src/rui/web/api/app.py:3321-3323` (list), `:1003-1005` (status),
`:3788-3790` (detail). `build_adapters` only guards construction
(`src/rui/device/registry.py:82-94`).

**What:** each endpoint awaits `adapter.probe()` without a per-device `try/except`.
One downstream adapter whose probe raises breaks the whole device list/status page.
`_device_link` contains `health()` failures (`app.py:851-862`) but probe is called before
it. This contradicts the registry's own promise that a broken device must not take the
console down.

**Repro (confirmed with `TestClient`):** inject an adapter whose `probe()` raises
`RuntimeError("probe boom")` into `app.state.adapters`, then
`GET /api/v1/devices` → 500; `GET /api/v1/devices/{id}` → 500; status → 500.

**Impact:** a single bad plug/adapter hides all other devices and the enable/disable
controls; the user cannot diagnose or remove the device from the console.

**Fix:** wrap each probe in a helper that returns `info=None` plus a stable
`probe_failed` error (and a bounded message); render the row with
`status="error"`. Reuse the cached `app.state.adapters` info where possible.

**Test:** `test_devices_api_survives_a_raising_probe`: two devices, one raising; GET list,
detail and status return 200, the good device is complete, the bad one has
`probe_failed`; `/health` remains 200.

### P1-5 — `wake.barge_via_device: false` is ignored; barge is enabled from the probe alone

**Where:** `src/rui/audio/process.py:684-691`; manifest property
`src/rui/plugins/models.py:448-456`; documented rule
`docs/socket-plug-v1.md:125,252`.

**What:** `_configure_device_wake` sets `device_wake_barge_active` whenever the probe
advertises `Capability.BARGE_VIA_DEVICE` and `PLAYBACK` (and the event says
`barge=true`). It never consults `resolved.device.barge_via_device_enabled`. The
manifest validator allows the capability without the enabling flag (a legal
`wake.source: device`, `barge_via_device: false` manifest), so a plug that explicitly
did not enable device barge gets it anyway.

**Repro (confirmed):** temp plug with
`capabilities: [playback, device_wake, barge_via_device]` and
`wake: {source: device, barge_via_device: false}`; mock probe with both capabilities;
after `_configure_device_wake`: `device_wake_active=True, device_wake_barge_active=True`.

**Impact:** policy bypass of the documented "capability = can, wake block = enables"
rule. A device/plug author cannot turn the hint off; a stop can happen against the
plug's declared intent.

**Fix:** resolve the plug once into a small policy object
`{source, barge_enabled, sidecar/trust}`; set `device_wake_barge_active` only when
`resolved is None` (dev adapter, if intended) or `barge_via_device_enabled` is true, and
probe has the capability. Log one warning when the manifest says no.

**Test:** temp v1 manifest with `barge_via_device: false` + probe advertising it; a
`barge=true` event during playback must be suppressed, `adapter.stop_playback` not
called, `device_wake_barge_suppressed` incremented. Add the inverse (manifest true,
probe false → barge off, one fallback warning).

### P1-6 — Third-party manifests can enable `device_wake`/device barge (trust not consulted)

**Where:** `src/rui/audio/process.py:697-721` (`_manifest_wake_source`);
`src/rui/plugins/loader.py:212-236` (third-party → sidecar/in-process rules);
`src/rui/plugins/models.py:68-76`.

**What:** `_manifest_wake_source` checks only `runtime.mode == "sidecar"` and
`wake.source`. A `trust: third-party` manifest with **no `runtime` block** (allowed:
"manifest-only" plug) resolves to `wake.source: device`; if an env/entry-point adapter
advertises the capability, the plane enables device_wake and barge. The loader's rule
"third-party must not be in-process" is enforced only for manifests that declare a
runtime; the runtime wake gate never checks trust.

**Repro (confirmed):** temp `trust: third-party`, no runtime, v1 device with
`wake.source: device` and `barge_via_device: true`; mock probe with the capabilities;
`_manifest_wake_source()` returns `"device"`, `device_wake_active=True`,
`device_wake_barge_active=True`.

**Impact:** defeats the frozen Q8 decision ("v1 only supports device_wake for
in-process/trusted adapters") for the manifest-only case. It also makes the trust
declaration cosmetic for the wake path.

**Fix:** return a policy object including `plugin.trust`; treat `third-party` as
sidecar-equivalent for device_wake unless an explicit, reviewed in-process exception
exists. Keep "no manifest at all" → probe authoritative (documented dev case), but
"manifest exists and is third-party" → KWS fallback with one warning.

**Test:** temp third-party manifest + env adapter; `_configure_device_wake` leaves
`device_wake_active=False`, `fallbacks` incremented; a trusted manifest with the same
fields activates.

### P1-7 — Device-wake `ts_ms` allows 1.5 s lateness, but the replay is only the last 350 ms from *now*

**Where:** `src/rui/audio/process.py:110-115` (`DEVICE_WAKE_STALE_MS=1500`),
`:964-976` (`_keep_preroll` limit `wake_preroll_ms`), `:941-943`
(`skip_after_wake_ms=0` with a comment claiming ts alignment), `:1139-1145` (replay of
the current `_preroll`).

**What:** an event accepted up to 1500 ms late is handled at arrival time. The gate
replays the current pre-roll (default 350 ms) and arms; the audio corresponding to
`wake.ts_ms` is no longer in the buffer. A late event therefore loses the beginning of
the request (the exact onset the pre-roll exists to protect), and a device that includes
the wake phrase in the stream replays it with `skip=0`. `ts_ms` is used for validation,
the `Wake` message and the trace, never for replay alignment.

**Repro:** set `audio.voice.wake_preroll_ms=350`, feed 1 s of blocks, inject a valid
device wake with `ts_ms = now - 800`, drain, and inspect the blocks passed to the gate:
they are the newest 350 ms, not the audio around `ts_ms`; the onset at `ts_ms` is gone.
Add a variant where the adapter stream contains the wake phrase: it reaches VAD because
the device path skips 0 ms and pre-roll is replayed.

**Impact:** first-syllable loss and wake-residue turns on slow sensors/transport, the
main quality failure the pre-roll was built for. The code comment
"align by ts_ms" is not implemented.

**Fix:** either reject events older than the pre-roll window (tighten
`DEVICE_WAKE_STALE_MS` to `wake_preroll_ms`), or keep a per-device/timestamped history
long enough for the stale window and replay only blocks with `ts >= event.ts_ms -
margin`, and skip audio in `[event.ts_ms, now]` that belongs to the phrase. Document the
chosen semantics and the default.

**Test:** property-style test with blocks at known ts and an event at a known offset:
assert the replay window is anchored to the event, and that a stale event outside the
retained buffer is rejected rather than silently mis-replayed. Add a real-audio test
(not the `FakeGate`) where the wake phrase is present and must not form a turn.

### P1-8 — LX06 websocket accepts unbounded frames (`max_size=None`) and decodes them

**Where:** `src/rui/device/xiaoai/adapter.py:150-157` (`serve(..., max_size=None)`),
`:380-430` (`_on_text`), `:432-465` (`_on_audio`),
`src/rui/device/xiaoai/protocol.py:82-118` (`decode_stream`).

**What:** the inbound websocket explicitly removes the library's 1 MiB frame limit. A
peer presenting a valid token (or any LAN peer when `allow_no_token` is set) can send a
multi-hundred-MB text/binary frame; websockets buffers it, `json.loads`/`bytes` copies
it, and `_on_audio` may queue a huge `AudioFrame`. There is no size cap on decoded PCM
or on the JSON text field (P2-8 adds a second-order path).

**Repro:** start the adapter on a free port, connect with `?token=...`, send a binary
frame whose JSON `bytes` array is 100 MB (or a text frame of similar size); observe
memory growth/blocking. With `allow_no_token: true`, no token is needed.

**Impact:** LAN denial of service (OOM/kill) on the speaker gateway; the default
configuration is token-gated, but the token travels in a plaintext `ws://` query string,
so a network observer can replay it.

**Fix:** set `max_size` to a small bound (e.g. 256 KiB; `PLAY_CHUNK_MS=100` at 48 kHz is
~9.6 KiB before JSON expansion), reject `record` payloads above the expected
`channels * sample_rate * frame_ms`, cap `decode_stream` output, and consider `wss` or a
header/token that is not in the URL.

**Test:** `test_oversized_ws_frame_is_rejected`: server remains healthy, `bad_frames` or
a close code increments, a normal frame still flows; add a tokenless variant to pin the
`allow_no_token` warning.

### P1-9 — Entry-point channel bypasses the override guard and is not tied to manifest trust

**Where:** `src/rui/device/loader.py:489-529` (entry-point scan), `:298-334` (register
hooks), `:421-449` (`allow_override` is enforced only inside `_register`).

**What:** two gaps in the second in-process source:

1. A register hook is called with the real `registry` module
   (`hook(registry)`), so it can call `registry.register("mock", ...)` directly.
   `_run_register_hook` passes `allow_override=False` but that only affects
   `_register`; the direct call replaces a built-in silently. Confirmed: an entry point
   whose hook does `registry.register("mock", "bogus:build")` leaves
   `_BUILDERS["mock"] == "bogus:build"` with no warning.
2. Entry points are discovered from every installed distribution and loaded without
   consulting any plug manifest/`trust`. A third-party wheel can therefore be an
   in-process adapter even though the loader rejects a third-party `plugin.yaml` with
   `runtime.mode: in-process`.

**Impact:** the documented invariants "entry point does not shadow env/builtin" and
"third-party must be sidecar" are not true for the automatic channel. Any installed
distribution is already code execution at install time, so this is primarily a
defense-in-depth/consistency issue, but it is exactly the kind of accidental channel a
downstream image creates.

**Fix:** pass a restricted registry facade to hooks that records/rejects forbidden
names (or snapshot-and-revert after the hook); validate entry-point names before
executing the hook; add an explicit `RUI_ADAPTER_ENTRY_POINTS=1` opt-in and/or require a
resolvable manifest with `trust: trusted|builtin`. Document that `pip install` is
equivalent to putting code in the image.

**Test:** entry-point hook that calls `registry.register("mock", ...)`; assert the
built-in is unchanged, a warning is emitted, and no `mock` replacement is in
`report.registered`. Entry point for a third-party package without manifest; assert it is
skipped (or requires the opt-in).

---

## P2 findings

### P2-1 — `drain_wakes()` output has no runtime bound

**Where:** `src/rui/audio/process.py:761-773`; contract expectation at
`docs/socket-plug-v1.md:181` (adapter should use a bounded queue of ~32).
**What:** `events = list(pending)` and then one `await`-ing handler per event. A buggy
adapter returning 100 k events (or an infinite generator) stalls the frame loop,
allocates/serializes one `DeviceWakeMsg` per event and can push the credit/audio ring
into drops. The debounce/rate limit only runs *after* the allocation.
**Repro:** adapter `drain_wakes()` returns `[valid_wake] * 100_000`; time `_drain_device_wakes`
and observe frame starvation/latency. A generator that never ends hangs capture.
**Fix:** cap per tick (`MAX_DRAIN_EVENTS`, e.g. 64); drop the excess, count it, log once
per interval; reject non-`list`/`tuple` outputs; consider a deadline around the call.
**Test:** drain returns 10 k valid events: captures bounded messages, increments a
drop counter, returns within a small budget, and the next frame still runs.

### P2-2 — `DeviceWake.barge` is not type-checked; a string `"false"` stops playback

**Where:** `src/rui/audio/process.py:779-804` (`_device_wake_problem`), `:908-924`
(`event.barge` truthiness); contract `src/rui/device/contract.py:68-86`.
**What:** the dataclass is not runtime-validated, and `_device_wake_problem` never
checks `isinstance(barge, bool)`. `DeviceWake(barge="false")` passes validation and is
truthy; a malformed adapter or a JSON-derived value can trigger a barge/stop even though
the sender meant false.
**Repro (confirmed):** active barge + `_speak_queued_s=1.0` +
`DeviceWake(barge="false")` → `device_wake_barges=1`, `stop_playback` called.
**Fix:** require an exact `bool` for `barge`; coerce/ignore other types with an invalid
count. Apply the same to `source` (already) and reject `bool` for numeric fields if
strictness matters.
**Test:** `barge="false"`, `barge=1`, `barge=None` are all invalid; `barge=False/True`
behave as today.

### P2-3 — Console capabilities are manifest ∪ probe, not manifest ∩ probe; the builtin manifest is itself inconsistent

**Where:** `src/rui/web/api/app.py:1005,3323,3790`; rule
`docs/socket-plug-v1.md:48`; builtin `src/rui/plugins/builtin.py:43,115`; adapter
`src/rui/device/xiaoai/adapter.py:120-122`.
**What:** three endpoints merge probe capabilities into the manifest set. The docs say
`mic_array/reference/playback/device_wake` are authoritative only as an intersection.
Example: a v0 LX06 manifest (no `device_wake`) + an env adapter whose probe advertises
it → the console lists `device_wake` although the plane falls back to KWS. Separately,
the builtin LX06 manifest declares `volume`/`keys` that its probe does not, and its
`rui_compat` is `>=0.2.0 <0.3.0` while this build is 0.3.2 (builtins bypass
`load_plugin`'s compat check).
**Repro:** device with builtin `adapter: xiaoai` and a probe reporting
`device_wake`; `GET /api/v1/devices` shows it; `_configure_device_wake` leaves
`device_wake_active=False` (manifest upper bound). Or run `load_plugin` on a copy of the
builtin manifest and see the compat refusal.
**Fix:** compute the advertised set as the intersection for `PROBE_CAPABILITIES`, keep
manifest-only values in a separate `declared_capabilities` field, and show probe-only
values as "not effective". Update `builtin.py` compat to `>=0.2 <0.4` and add a
build-time/model test that validates builtins through `load_plugin` (or a shared
validator).
**Test:** `test_device_api_reports_effective_capabilities`: manifest v0 + probe
`device_wake` → absent from `capabilities`, present as declared/extra; builtin parity
test for compat and capabilities.

### P2-4 — A broken/unresolvable plug manifest fails open (device_wake still allowed)

**Where:** `src/rui/audio/process.py:697-721`; `src/rui/plugins/resolve.py:66-90`.
**What:** `_manifest_wake_source` returns `None` both for "no manifest exists" (the
documented dev case) and for "manifest exists but failed to load" (bad YAML, duplicate
id, unknown capability, unreadable dir). The latter then lets the probe enable
device_wake/barge without the manifest upper bound. A `params.plug_id` that does not
resolve is dropped the same way.
**Repro (confirmed):** a plug with `params.plug_id: broken` and an invalid device
manifest → `_manifest_wake_source() is None`, `device_wake_active=True`.
**Fix:** distinguish "no plug_id and no adapter match" from "a plug was requested /
found but is invalid". In the latter case return a fail-closed marker (KWS + one
warning). Surface `discover_plugins().errors` instead of silently swallowing them.
**Test:** invalid user plug + `plug_id` + probe `device_wake` → inactive, fallback
counted; no manifest → active (documents the dev exception).

### P2-5 — `read_volume` is not in the adapter contract; a contract-only adapter never gets device-volume metrics/reconciliation

**Where:** `src/rui/audio/process.py:2051-2059` (`adapter.read_volume`),
`:2198-2218` (uses `adapter.services.read_volume`), `src/rui/device/contract.py:125-148`
(`DeviceServices.read_volume`), `src/rui/device/xiaoai/adapter.py:285-289`,
`src/rui/device/mock.py:40-46` (mock has it only on services).
**What:** the metrics path asks the adapter object for `read_volume`; the protocol and
the `DeviceVolume` message use `services.read_volume`. An adapter implementing exactly
the documented contract (or the mock) is silently skipped: `device_volume` stays `None`,
so `_reconcile_volume` never runs and the console shows no mixer value. `_build_metrics`
also calls `adapter.health()` (not in the protocol) but catches the `AttributeError`.
**Repro:** adapter with `services.read_volume` only; call `_read_device_volume`; value
never reaches `device_volume`. The mock demonstrates it.
**Fix:** add `read_volume` to the adapter protocol with a default delegating to
`services`, or make all call sites use `services.read_volume`; document `health()` as an
optional duck-typed report.
**Test:** both a `services`-only adapter and an adapter-with-direct-method report volume
and reconcile.

### P2-6 — Observability for suppressed device wakes is not wired

**Where:** `src/rui/audio/protocol.py:233-249` (`DeviceWakeMsg` docstring),
`src/rui/audio/process.py:876-896` (sent before debounce), `src/rui/audio/client.py:143-208`
(`_dispatch` ignores `DeviceWakeMsg`), `client.py:94,174` (`last_barge_ms` has no
reader), `src/rui/trace.py:116-118`, `src/rui/audio/protocol.py:228-230`.
**What:** every valid event is sent as `DeviceWakeMsg`, including debounced/rate-limited/
suppressed/conflict ones, but the control plane never consumes it. Only a turning event
sends `Wake`; therefore there is no trace or API entry for a suppressed event, despite
`docs/socket-plug-v1.md:274,276` ("each accept/suppress/drop has a count and a trace").
`Wake.debounced` is never set true. `last_barge_ms` is assigned but never read. The
`DeviceWakeMsg` docstring also says "already validated/debounced", which is not the
wire semantics (the v1 doc correctly says pre-policy).
**Repro:** inject a duplicate/low-score event; `Metrics` counters change, `TraceLog`
does not; `grep -R last_barge_ms` finds only the assignment. Inspect `_dispatch`: the
message falls through all `elif`s.
**Fix:** either consume `DeviceWakeMsg` in `AudioPlaneClient` (count/last event/optional
trace metadata) or remove the message. Set `Wake.debounced=True` if a suppressed
duplicate is ever reported, or remove the field. Use `last_barge_ms` for the trace mark
or delete it. Correct the protocol docstring.
**Test:** a valid but suppressed event produces a `DeviceWakeMsg` that increments a
client counter; a trace for a turning event records the action; no dead fields.

### P2-7 — Connection lifecycle is not isolated: no single-client guard, shared adapter closed by the wrong handler, `Hello.device_id`/`Ready` unchecked

**Where:** `src/rui/audio/process.py:2119-2124`, `:2262-2280`
(`await self.adapter.close()` in the shared `finally`), `:2175-2185` (Hello ignored),
`src/rui/audio/client.py:520-558` (`_greet` never compares `Ready.device_id`).
**What:** `serve()` accepts any number of Unix-socket clients. A second connection
overwrites `self.adapter`/`_ctl_writer` and starts a second capture loop. If the second
`_open_adapter()` fails (e.g. LX06 port already bound), the `finally` still runs
`await self.adapter.close()` — the first, healthy adapter — and the first client loses
the speaker. `Hello` is ignored and `_greet` accepts any `Ready`, so a supervisor can
believe it is driving device B while the child runs A. `adapter.close()` is also
unguarded; a raising close skips `writer.close()`.
**Repro:** connect two clients to the plane socket; make the second open fail; observe
the first connection's adapter closed. Or launch plane A, greet expecting B; `Ready` for
A is accepted.
**Fix:** bind the adapter to a local in `handle`, close only that object in `finally`
inside `try/except`; reject a second live client (or explicitly replace with a
documented handoff); verify `Ready.device_id == requested` in `_greet`; validate
`Hello.device_id`/rate or remove the redundant field; consider socket mode `0600`.
**Test:** two-client test (second rejected, first still works); failed-second-open closes
nothing; `_greet` mismatch raises; close that raises still closes the writer.

### P2-8 — Unbounded vendor text / malformed device JSON can kill the control-plane reader or the LX06 connection

**Where:** `src/rui/device/xiaoai/protocol.py:178-220`; `src/rui/device/xiaoai/adapter.py:380-430`;
`src/rui/audio/protocol.py:31,67` (`MAX_JSON=64 KiB`); `src/rui/audio/process.py:601-607`
(no text cap).
**What:** `RecognizeResult` text is copied verbatim into `VendorSignal.text`; the plane
wraps it in a `VendorSignalMsg` and writes it. Once the JSON exceeds 64 KiB, the
control plane's `read_frame` raises, `pump` marks the plane dead and the watchdog
restarts it — a device can cause a restart loop. Malformed `events[i].data` shapes
(`results` not a list, `header` a string) raise inside `_on_text`; the LX06 handler
catches that as "connection ended" and drops the speaker.
**Repro:** fake speaker sends `{"Event":{"event":"instruction","data":{"header":{"namespace":"SpeechRecognizer","name":"RecognizeResult"},"payload":{"is_final":true,"results":[{"text":"x"*100000}]}}}}`;
observe the client `ValueError`/plane restart. Send `results: {"x":1}` and observe the
LX06 disconnect.
**Fix:** cap text at the parse boundary (e.g. 512 chars, like the log line), validate
`data`/`header`/`payload` are mappings and `results` is a list, and catch per-message
errors without dropping the connection. Also cap `VendorSignalMsg` before encode.
**Test:** oversized text is truncated/rejected and the reader survives; malformed shapes
increment a bad-message counter and the websocket stays connected; a normal signal still
flows.

### P2-9 — `plugin.yaml` itself is not contained, size check is TOCTOU, and the device list is unbounded

**Where:** `src/rui/plugins/loader.py:64-66` (`root / "plugin.yaml"`), `:104-130`
(only `devices:` entries use `_resolve_member`), `:133-145` (stat then `read_text`),
`:78` (unbounded `devices:` list), `:257-296` (unbounded scan errors).
**What:** a symlinked `plugin.yaml` is followed anywhere. A symlink to `/dev/zero` has
`st_size == 0`, passes the size check, and `read_text()` never ends — startup hangs.
A file replaced between `stat` and `read` can bypass the 1 MiB cap. A manifest can list
an arbitrary number of device files (each up to 1 MiB) and the scan collects unlimited
errors. A local writer of `data/plugs` is already privileged, but this is a cheap
availability/robustness trap, and the "1 MiB" limit is advertised as a boundary.
**Repro:** `ln -s /dev/zero <data>/plugs/x/plugin.yaml`; start `rui serve`/`rui plug-test`;
the read never returns. Or generate a manifest with 10 k `devices:` entries.
**Fix:** resolve `plugin.yaml` with the same containment helper, require a regular file
(`is_file`, optionally `O_NOFOLLOW`/`lstat`), read with a bounded `read(limit+1)` using
`O_NOFOLLOW`, cap device entries and total scan/time/errors.
**Test:** symlinked `plugin.yaml` rejected with a `PluginError`; oversized/growing file
rejected; 1001 device entries rejected; scan error list capped.

### P2-10 — Per-request plug catalog and adapter construction (downstream builders run on every device-list request)

**Where:** `src/rui/plugins/resolve.py:44-62,97-102` (new `PlugCatalog` + full scan per
call), `src/rui/web/api/app.py:925-940` (`_active_device_id`),
`src/rui/audio/client.py:387-393` (`_pick_device` calls `build_adapters`),
`app.py:3311` (list calls `_active_device_id`), `:3796`/`:3824` (detail/status).
**What:** every `GET /api/v1/devices` re-reads all user manifests and calls
`build_adapters`, which invokes every enabled device's builder in the web process. A
downstream builder that has side effects (opens a client, binds a port, starts a task)
runs per request. For inbound LX06 this is only a probe object, but the hook is public.
**Repro:** plugin builder increments a counter/file; call `GET /api/v1/devices` twice;
the counter grows twice (plus once per detail/status call). With 50 plugs, measure list
latency.
**Fix:** cache `PlugCatalog` by `(data_dir, mtimes)`/config revision and use the
already-built `app.state.adapters` keys to choose the active device; do not rebuild
adapters to answer a read.
**Test:** builder-call counter stays constant across repeated list/status calls; cache
invalidates only when a manifest mtime/config version changes.

### P2-11 — No timeout around `stop_playback()` / `play()`; a hung adapter blocks barge and replies

**Where:** `src/rui/audio/process.py:1040-1045` (`_stop_for_barge`), `:1938-1973`
(`_stop_playback`), `:1714` (play), `:1498-1516` (`_prepare_reply_player`).
**What:** these awaits have no deadline. An adapter whose `stop_playback` never returns
(a dead socket, a blocked shell call) freezes the capture loop on barge; the gate never
arms and the user hears the reply continue. `play()` hung stalls the reply loop. The
contract says the methods are best-effort/idempotent but does not bound time.
**Repro:** adapter `async def stop_playback(...): await asyncio.Event().wait()`;
inject a barge; `_handle_device_wake` never completes and `_capture` stops ticking.
**Fix:** `await asyncio.wait_for(adapter.stop_playback(...), 2.0)` with an exception
path that counts a failure but still arms the gate; same for `play` (or run it on a
worker with a watchdog). Keep the `_device_audio_pending` invariant on timeout.
**Test:** stop-hangs test: barge still arms the gate within a bounded time, counter
increments, and the next turn proceeds; play-hangs test.

### P2-12 — `DeviceAdapter` Protocol default bodies silently succeed when a subclass forgets a method

**Where:** `src/rui/device/contract.py:167-224`; `src/rui/device/mock.py:49`,
`src/rui/device/xiaoai/adapter.py:65` (both subclass the Protocol).
**What:** `class Foo(DeviceAdapter)` is a normal base class; an unimplemented
`async def play(...): ...` inherits a coroutine returning `None`. Forgetting `play`,
`stop_playback` or `set_volume` does not fail at import or at runtime; the plane thinks
the call succeeded. The contract kit checks `callable`, not behavior, and cannot catch
this for adapters that subclass.
**Repro:** `class A(DeviceAdapter): pass` (or implement only probe/frames); call
`await a.play(b"\0\0", sample_rate=16000, channels=1)` → returns `None`.
**Fix:** mark required methods `@abstractmethod` (and use a separate structural Protocol
for duck-typed adapters), or have the kit instantiate/verify via `isinstance(adapter,
DeviceAdapter)` plus a "not inherited stub" check. Document that subclassing is
optional.
**Test:** a subclass missing `play` fails construction/contract check; the example
duck-typed adapter still passes.

### P2-13 — Env grammar edge cases: attribute not validated, unlimited warnings, env can replace built-ins, legacy spelling untested

**Where:** `src/rui/device/loader.py:51-55,123-159,421-449,547-552`; `src/rui/env.py:45-51`.
**What:** `name` and `module` are regex-checked, but the `attr` is not (only looked up);
`_entries` has no count/length cap; `report.warnings` grows without limit (env value is
bounded by the OS, so hundreds of entries are possible); `allow_override=True` for env
means `RUI_ADAPTER_PLUGINS=xiaoai=...` silently replaces the builtin with one warning
(the docs only promise entry points will not shadow a builtin); the
`HOUSE_EAR_ADAPTER_PLUGINS` fallback has no test. A canonical empty value shadows the
legacy spelling (`env.get` returns `""`, not the fallback).
**Repro:** `RUI_ADAPTER_PLUGINS=xiaoai=rui.device.mock:build` → `_BUILDERS["xiaoai"]`
becomes the mock builder; `RUI_ADAPTER_PLUGINS=dot2=mod:no such attr` warns but continues;
`RUI_ADAPTER_PLUGINS=,` → no entries.
**Fix:** validate `attr` with an identifier regex; cap entries and warnings; decide and
document whether env may replace builtins (recommended: require an explicit
`!override`/operator flag for builtins); test the legacy fallback and the
empty-canonical/legacy interaction.
**Test:** table-driven `_parse`/`load_adapter_plugins` cases for invalid attr, 100 entries,
builtin replacement warning, legacy env, `RUI_` empty + legacy set.

### P2-14 — `rui plug-test --adapter` executes untrusted code in-process; the v1 doc says otherwise

**Where:** `src/rui/plugins/contract.py:1065-1131` (import/exec),
`:1213-1217` (`_run_adapter`), `:1851-1925` (`run_contract`/`main`);
`pyproject.toml:38` installs `rui-plug-test` in the production image;
`docs/socket-plug-v1.md:309` claims "subprocess/fake environment";
`docs/plug-test-kit.md:22` says in-process.
**What:** a downloaded plugin's `adapter.py` is imported/executed with the CLI's full
environment, cwd, filesystem and network access. There is no warning, confirmation flag
or privilege drop. `_import_target` even inserts the plugin directory at `sys.path[0]`
for the duration of `exec_module` (`:1085-1090`), so it can shadow a stdlib/`rui` import
(and leave `sys.modules` entries behind). This is by design for a dev tool, but the
production image and the contradictory doc make it a real footgun.
**Repro:** `adapter.py` with a top-level
`open(os.path.expanduser("~/pwned"), "w")` / `print(os.environ["RUI_LLM_KEY"])`;
`rui plug-test <dir> --adapter ./adapter.py:build` executes it. The report never says the
adapter was trusted/executed.
**Fix:** require an explicit `--trust-adapter` flag (or a confirmation prompt) and emit
a loud warning in text and `contract-report`; prefer running the adapter in a subprocess
with a minimal env, no network and a temporary cwd; fix `docs/socket-plug-v1.md:309`.
**Test:** without the flag, `--adapter` is refused/never imported; with it, a sentinel
file/environment probe runs and the report carries `adapter.trusted: false/true`; a test
that the doc and the tool agree.

### P2-15 — Downstream Docker recipe does not install the manifest into `data/plugs`, so the manifest upper bound is skipped

**Where:** `docs/plug-test-kit.md:40-75`; `src/rui/plugins/resolve.py:97-102`;
`src/rui/audio/process.py:697-721`; `docs/socket-plug-v1.md:204-235`.
**What:** the recipe copies code to `/opt/rui-plugins` and sets
`PYTHONPATH`/`RUI_ADAPTER_PLUGINS`, but `resolve_device_plug` only reads
`<system.data_dir>/plugs/*`. The manifest shown in the plugin directory is not installed
there (the `./data` bind mount is host-owned). Result: `resolved is None`, so the
`wake.source`/`barge_via_device` upper bound and trust checks are skipped; the adapter
works from probe alone. If the plug is a device_wake plug this may look like success,
but a manifest that says `wake.source: rui` would not be enforced. Conversely, a
`trust: third-party` manifest is irrelevant.
**Repro:** build the recipe image with a v1 `device_wake` plug; do not copy
`plugin.yaml` to `/var/lib/rui/plugs`; check `_manifest_wake_source()` is `None` while
the probe enables device_wake.
**Fix:** document a required `COPY`/volume step for the manifest into
`/var/lib/rui/plugs/<id>/` (and a check in `rui doctor`), or make the loader also scan an
explicit `RUI_PLUGINS_DIR` packaging path. Add an acceptance smoke for the downstream
image (`/api/v1/plugs`, `adapter_plugins.registered`, `_manifest_wake_source`).
**Test:** a fixture builds a plugin dir + data dir, runs the env hook, and asserts
`resolve_device_plug` returns the manifest; a CI/docs test ensures the recipe includes
the manifest installation.

### P2-16 — CI/security workflow action pinning is inconsistent

**Where:** `.github/workflows/ci.yml:14-25`, `.github/workflows/docker-publish.yml:33-73`,
`.github/workflows/secret-scan.yml:18` use floating major tags (`actions/checkout@v4`,
`astral-sh/setup-uv@v5`, `docker/*@v3/v6`); `.github/workflows/security.yml` uses pinned
commit SHAs.
**What:** the security job is pinned, but the PR/build/publish jobs are not; a
compromised tag can reach the image build. `uv sync --extra dev` is not `--locked`, so
CI may resolve outside `uv.lock`.
**Repro:** diff the pinning; no runtime repro needed.
**Fix:** pin all third-party actions to the same SHA style (Dependabot/Renovate), use
`uv sync --locked`, and add a small lint/script that fails on mutable `uses:` refs.
**Test:** a repository policy test or CI step greps workflows for `@v[0-9]` and
`uv sync` without `--locked`.

### P2-17 — Test gaps and timing-sensitive cases

**Where:** named suites; notable examples:
`tests/test_device_wake.py:144-153` (named "degrades after repeats" but calls drain
once), `:286-298` (invalid set lacks NaN/inf/negative threshold/source/non-bool),
`:312-323` (no window-expiry test), `:385-449` (manifest/trust/real sidecar only via
monkeypatch); `tests/test_adapter_plugins.py` (no optional-arg builder, no hook
override, no entry-point load failure, no legacy env, no audio plane);
`tests/test_plug_contract.py` (no untrusted-adapter warning, no oversized frame, no
malformed adapter return); `tests/test_reference_plugin.py` (kit only, never the env
hook); `tests/test_xiaoai_adapter.py`/`test_xiaoai_e2e.py` (no oversized frame, close
idempotence, malformed text, reconnect).
**What:** the five named files pass (110 tests), but the new v1 behavior is mostly tested
through `FakeGate`/`MockAdapter`, not through `_capture`; the concurrency, invalid-value
fuzz, multi-device, sidecar, and downstream-process paths are untested. Timing:
`test_device_wake` uses `now_ms()` with a 1500 ms stale window; a CI stall >1.5 s
between injection and drain turns a fresh event into `stale`, and the duplicate test's
`now-1000/+100` pair can become stale too. The full suite also exposed a real flake:
`tests/test_audio_plane.py:67` asserts `jitter_p50_ms == 20 ± 5`; on this host the full
run measured 25.48 (one failure) and an isolated rerun passed. The jitter assertion is
a scheduler/load measurement, not a product invariant; it should be a warning or a much
wider bound, while the frame-count/drop assertions stay strict.
**Repro:** run the named suite under CPU load/`timeout`; or inject `ts_ms=now` and sleep
1.6 s before drain to force the flake. Add a fake clock or inject `now` for the policy.
**Fix:** add the specific tests listed in each finding; test `_capture` with a real
`UtteranceGate` stub that records replay timestamps; make time injectable
(`now_ms` provider) or pass `at_ms` into the policy; mark long/real-device tests
separately.
**Test additions (minimum):** optional-arg builder via env; drain_signals raise +
frames raise; probe 500; barge manifest false; third-party manifest; broken manifest;
stale-but-valid device wake replay; 100 k drain; non-bool barge; capability
intersection; service-only read_volume; second client/close; oversized ws/vendor text;
plug.yaml symlink/size; entry-point hook override/trust; env legacy/edge; audio-plane
env hook; downstream manifest resolution.

---

## Downstream hook verification (web + audio plane)

| Question | Answer / evidence |
|---|---|
| Web process loads the hook before adapters? | Yes: `src/rui/web/api/app.py:1414` → `build_adapters` `:1415`. |
| Audio plane loads it? | Yes: `src/rui/audio/process.py:2325-2327` in `build_plane`; `main` calls it after `load_config`/`secrets.configure`. The child inherits `PYTHONPATH`/env (`audio/client.py:406-418`). |
| Config/user/console can set the env? | No (`env.get` reads `os.environ`; console writes `secrets.yaml`). Host `.env`/container env is the boundary. |
| Env grammar | `comma` split, optional `name=`, `module[:attr]`, bare module tries `build` then `register`; module/name regex; duplicate env names last-wins with warning; entry points dedupe `(name,value)` and skip an existing name. |
| Entry-point failures/performance | Failures caught per point (`loader.py:489-529`); `metadata.entry_points(group=...)` once per process; `point.load()` has no timeout (an installed module can hang startup). |
| Does a normal `build(dev, cfg)` work? | Yes (the test fixture `BUILDER` at `tests/test_adapter_plugins.py:60-63`). |
| Do the bundled reference adapters work via env? | **No** (P1-1): both use all-optional args and are misclassified as factories. |
| Does the audio plane see the same registry? | It re-runs the loader in the child; entry points/distributions are the same interpreter, so yes. Warnings from the child are not visible in `/health` (web's report only). |
| Restart behavior | Env changes need a container (or process) restart. Plane restarts reload plugins; the web process does not re-run the loader on config save. If the web registry is stale but the child would load, `_pick_device` (`audio/client.py:387-393`) can refuse to start the plane. |
| Manifest resolution | Only `<data_dir>/plugs/*` (P2-15); missing/broken → None (P1-6/P2-4). |
| Sidecar | No sidecar runtime is implemented: `setup_api.py:122` returns `sidecar_not_supported`; `_manifest_wake_source` degrades device_wake to KWS. The v1 doc describes sidecar as the third-party path, so this is a documented 0.4 gap rather than a regression; the contract kit warns when it imports a sidecar adapter in-process (`plugins/contract.py:1009-1012`). |

## Positive controls (held up in review)

- VAD gate is mandatory and is the only path to a turn; a device event cannot call the
  LLM/ASR directly (`process.py:898-903`, tests `test_without_vad...`).
- Manifest v0/v1 dependency table is strict (`models.py:380-446`); v0 cannot smuggle
  v1 fields; `native_kws` rejected in v1; `barge_via_device` capability requires
  device_wake + playback.
- `devices:` path traversal and escaping symlinks are rejected (`plugins/loader.py:104-130`,
  tests `test_device_entry_escaping...`, `test_symlink_escaping...`).
- YAML is `typ="safe"`, 1 MiB/file, top-level mapping; secret params cannot ship
  defaults (`models.py:214-272`).
- Loader/registry startup is fail-soft per entry and per device;
  `/health` exposes names/counts, not warning text (`app.py:2047-2050`).
- Device-wake device is separately counted from KWS; `wake_count` stays KWS
  (`protocol.py:116-134`, `test_metrics...`).
- Barge requires `voice.barge_in`, the capability, not parked, outside cue/vendor
  windows (`process.py:837-861`; tests).
- LX06 token is fail-closed, compared with `hmac.compare_digest`, and
  `allow_no_token` is explicit (`xiaoai/adapter.py:135-158,372-378`).
- Security workflow pins actions and scans locked deps + image
  (`.github/workflows/security.yml`), with dated allowlists.

## Suggested fix order

1. P1-1 (registration) and P1-4/P1-2/P1-3 (fail-soft boundaries): these determine
   whether a downstream adapter or a broken device can be diagnosed at all.
2. P1-5/P1-6/P2-4 (manifest/trust enforcement): make the policy object the single
   source of `source`/`barge`/trust and fail closed when a manifest was requested.
3. P1-8/P2-8 (LX06 input bounds) and P1-9 (entry-point guard).
4. P1-7 (replay alignment), P2-1/P2-2/P2-6/P2-17 (bounded inputs/observability/tests).
5. P2-9/P2-13/P2-14/P2-15/P2-16 (hardening/docs/CI).

## Appendix — commands used

```bash
# Targeted regression (all pass)
.venv/bin/python -m pytest tests/test_device_wake.py tests/test_adapter_plugins.py \
  tests/test_reference_plugin.py tests/test_plug_contract.py tests/test_plugins.py -q

# P1-1 repro
PYTHONPATH=plugins/rui-plugin-xiaoai RUI_ADAPTER_PLUGINS=lxref=rui_plugin_xiaoai.adapter:build \
  .venv/bin/python -c 'from rui.device.loader import load_adapter_plugins; print(load_adapter_plugins().to_dict())'

# P1-4 repro (TestClient) and P1-2/P1-5/P1-6/P1-9/P2-2/P2-4 repros were run with
# temporary /tmp plugins/adapters; see the snippets in each finding.
# No file in the repository was modified by those repros.

# Full suite (1007 collected): one timing flake, then a passing isolated rerun
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_audio_plane.py::test_plane_decimates_a_mock_device_over_a_socket -q
```
