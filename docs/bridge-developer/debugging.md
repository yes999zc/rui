# Debugging a bridge: registration, capability, wire and link

Four failures cover almost every "my bridge does not work" report. Check them in
this order — each step assumes the previous one passed:

```text
1. Did the adapter register?      → "adapter plugins: registered …" / "adapter plugin: …"
2. Is the device described?       → /api/v2/devices/<id> (plug_unresolved, adapter_unavailable)
3. Is the audio wire acceptable?  → audio_config_mismatch refusal / audio_wire
4. Is there a live link?          → not_connected, token_missing, speaker_rejected
```

Where the logs are: the control process writes `<data_dir>/service.log` (0600,
rotating) and stderr ([`src/rui/__main__.py:524-525`](../../src/rui/__main__.py#L524-L525),
[`src/rui/log_setup.py:1-20`](../../src/rui/log_setup.py#L1-L20)). The audio-plane
child's stderr is relayed into the same log with a `plane:` prefix
([`src/rui/audio/client.py:545-566`](../../src/rui/audio/client.py#L545-L566)); its
own log level follows `system.log_level` ([`src/rui/audio/process.py:4003-4006`](../../src/rui/audio/process.py#L4003-L4006)).

Set `TOKEN` in the commands below when `web.auth_token` is configured and send it
as `X-Auth-Token`; on a loopback bind with an empty token, no header is needed
([`src/rui/__main__.py:180-191`](../../src/rui/__main__.py#L180-L191)).

---

## 1. Registration rejected

**Symptom.** `/health.adapter_plugins.registered` does not contain your adapter
(and usually `warnings` is > 0), or the device page says the adapter is not
registered. Every failure is **fail-soft**: startup continues, one bounded
warning is logged, and `/health` keeps only names and a count.

**Where to look.** Every loader warning is logged with the prefix
`adapter plugin:` ([`src/rui/device/loader.py:992-998`](../../src/rui/device/loader.py#L992-L998));
success logs `adapter plugins: registered <names>`
([`src/rui/device/loader.py:145-147`](../../src/rui/device/loader.py#L145-L147)).

Real strings from the four common causes (verbatim, one line each):

| Cause | Log line |
| --- | --- |
| No manifest backs the name | `adapter plugin: RUI_ADAPTER_PLUGINS fake_device:build: no plug manifest backs adapter 'fake-speaker'; the env/entry-point hook requires a rui-plugin manifest with trust: builtin|trusted (ship plugin.yaml next to the adapter, or install it under <data_dir>/plugs/ and set RUI_DATA_DIR)` |
| Third-party asks in-process (the manifest itself is rejected) | `adapter plugin: RUI_ADAPTER_PLUGINS fake_device:build: manifest …/plugin.yaml cannot be loaded: PluginError: …/devices/fake.yaml: device 'fake-speaker' asks for runtime.mode 'in-process', but the plugin trust level is 'third-party'; third-party code must run as 'sidecar'. Set mode: sidecar, or have the plugin reviewed as builtin/trusted.` |
| Third-party manifest is loadable but the trust gate refuses it | `adapter plugin: RUI_ADAPTER_PLUGINS fake_device:build: plug 'fake-device-plug' is trust='third-party'; in-process adapters require trust: builtin or trusted (third-party is sidecar-only and 0.3.x has no sidecar runner)` |
| The module cannot be imported | `adapter plugin: RUI_ADAPTER_PLUGINS not_a_module:build: cannot import 'not_a_module': ModuleNotFoundError: No module named 'not_a_module'` |

The gates these come from: manifest required and trust in `builtin|trusted`
([`src/rui/device/loader.py:242-289`](../../src/rui/device/loader.py#L242-L289)),
third-party + in-process rejected in the plugin loader
([`src/rui/plugins/loader.py:205-244`](../../src/rui/plugins/loader.py#L205-L244)),
built-in names protected ([`src/rui/device/registry.py:173-192`](../../src/rui/device/registry.py#L173-L192)).

**Minimal commands.**

```bash
# 1. Does the manifest + adapter load at all? (same checks, synchronous output)
rui plug-test /path/to/plug --adapter /path/to/plug/adapter.py:build

# 2. Print the loader's warnings without starting the service
PYTHONPATH=/path/to/plug RUI_DATA_DIR=/var/lib/rui \
RUI_ADAPTER_PLUGINS="my-name=my_module.adapter:build" \
python - <<'PY'
from rui.device.loader import load_adapter_plugins
r = load_adapter_plugins()
print("registered:", r.registered)
for warning in r.warnings:
    print("warning:", warning)
PY

# 3. The app's own line for a device whose adapter is missing
grep "configured but not drivable" /var/lib/rui/service.log
```

`rui plug-test` runs the *same* manifest loader (`load_plugin`) and the same
v1 checks, so a manifest error appears there first and with more context
([`src/rui/plugins/contract.py:1851-1925`](../../src/rui/plugins/contract.py#L1851-L1925)).

**Fixes.**

- Put `plugin.yaml` next to the adapter package/module, or install it under
  `<data_dir>/plugs/<id>/` and set `RUI_DATA_DIR`
  ([`src/rui/device/loader.py:292-377`](../../src/rui/device/loader.py#L292-L377)).
- Use `trust: trusted` only for code you control; third-party plugs cannot run
  in-process and the 0.8.x line has no sidecar runner.
- Make the `name=` prefix (or plugin-returned name) equal `devices[].adapter`.
- Check `PYTHONPATH` and that the module imports outside rui:
  `python -c "import my_module.adapter"`.

---

## 2. Capability missing (declared ≠ effective)

**Symptom.** The device connects, but a feature/action is refused, or the device
page shows a capability as `effective: false`. The action endpoint answers
`409 capability_unsupported`:

```json
{"error":{"code":"capability_unsupported","message":"device 'fake' does not declare 'playback'","hint":"The action is hidden by the UI when the capability is missing.","details":{"capability":"playback","declared":["playback","volume","led"]},"retryable":false}}
```

and the device detail explains why per capability:

```json
{"id": "playback", "declared": true, "probed": false, "effective": false, "known": true, "reason": "probe_missing"}
```

**What it means.** For `mic_array`, `reference`, `playback`, `device_wake` and
`barge_via_device` the effective set is **manifest ∩ probe()**: either side
missing means the capability is not effective. `led`/`keys`/`volume` are
manifest-authoritative — `DeviceServices` is the runtime evidence, so a probe that
omits them does not downgrade a working speaker
([`src/rui/device/contract.py:26-97`](../../src/rui/device/contract.py#L26-L97)).
The action gate is the same effective set
([`src/rui/web/api/v2_api.py:2836-2870`](../../src/rui/web/api/v2_api.py#L2836-L2870)).

**Minimal commands.**

```bash
curl -s http://127.0.0.1:8080/api/v2/devices/<device-id> \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["capabilities"], indent=2))'

# Which actions each capability advertises, and the gate rules:
curl -s http://127.0.0.1:8080/api/v2/capabilities | python3 -m json.tool

# The same discrepancy, before starting the service:
rui plug-test /path/to/plug --adapter /path/to/plug/adapter.py:build
```

`rui plug-test` reports `device.probe_layout` PASS/FAIL and marks missing
capabilities; it also FAILs `adapter.methods` if `drain_wakes()`/`drain_signals()`
are absent even though the protocol provides defaults
([`src/rui/plugins/contract.py:100-112`](../../src/rui/plugins/contract.py#L100-L112)).

**Fixes.**

- Make `probe()` return the manifest's capabilities **for probe-bound values**,
  and keep `channels == mic_channels + ref_channels`
  ([`src/rui/device/contract.py:167-203`](../../src/rui/device/contract.py#L167-L203),
  [`src/rui/device/contract.py:292-325`](../../src/rui/device/contract.py#L292-L325)).
- Do not declare capabilities the adapter cannot actually serve: the manifest is
  the upper bound, so an over-claim only produces a degraded UI.
- Missing `device_wake` is not an error: the device stays on rui's own KWS. Barge
  additionally needs `reference` + `playback`, or `device_wake` +
  `barge_via_device` + `playback`
  ([`src/rui/device/contract.py:185-203`](../../src/rui/device/contract.py#L185-L203)).

---

## 3. Audio wire mismatch (`audio_config_mismatch`)

**Symptom.** The room never becomes ready; the plane refuses to start and the
device page turns red. This is a **deterministic configuration error**, not a
network blip: waiting does not fix it.

**Where to look.** The plane compares the effective wire with the device before
`Ready` ([`src/rui/audio/process.py:905-974`](../../src/rui/audio/process.py#L905-L974)).
A mismatch logs (verbatim from a forced failure):

```text
ERROR __main__: audio wire (fake): configuration does not match the device: playback.rate: configured 16000, device 48000 (source: model manifest fake-speaker (fake-device-plug)); fix: set devices[].audio.playback.rate to 48000 (the rate the adapter accepts), or fix audio_defaults.playback.rate
```

The plane raises `RuntimeError(f"{WIRE_MISMATCH_PREFIX}{wire.as_error()}")`
([`src/rui/audio/process.py:3791-3796`](../../src/rui/audio/process.py#L3791-L3796)),
so the supervisor and watchdog add their wrappers:

```text
WARNING rui.web.api.app: audio plane did not start: audio plane refused: audio_config_mismatch: playback.rate: configured 16000, device 48000 (source: model manifest …); fix: …
WARNING rui.audio.client: audio plane is down (AudioPlaneError: audio plane refused: audio_config_mismatch: …); restarting in 1 s
```

`WIRE_MISMATCH_PREFIX` is `"audio_config_mismatch: "` and is matched anywhere in
the supervisor's text on purpose
([`src/rui/audio/probe.py:61-74`](../../src/rui/audio/probe.py#L61-L74)).

API view of the same fact:

```bash
curl -s http://127.0.0.1:8080/health | python3 -c \
  'import json,sys; print(json.dumps(json.load(sys.stdin)["planes"], indent=2))'
curl -s http://127.0.0.1:8080/api/v2/devices/<device-id> | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(d["status_reason"]); print(json.dumps(d["audio_wire"], indent=2))'
```

```json
{"code":"audio_config_mismatch","message":"the configuration does not match the device","hint":"Open the audio tab; both values, their source and the fix are listed."}
{"ok":false,"checked":["playback.rate","playback.channels"],
 "message":"playback.rate: configured 16000, device 48000 (source: model manifest fake-speaker (fake-device-plug)); fix: set devices[].audio.playback.rate to 48000 (the rate the adapter accepts), or fix audio_defaults.playback.rate",
 "mismatches":[{"path":"playback.rate","configured":16000,"device":48000,"source":"model manifest …","fix":"…"}]}
```

**Which side is right.**

- **Capture** is compared only for `inbound` devices, because the device dials us
  and is told the wire. An `outbound` adapter owns its capture path and delivers
  self-describing frames; a capture difference there is advisory.
- **Playback** is compared for every transport, using `audio_defaults.playback`
  from the model manifest — `DeviceInfo` has no playback field
  ([`src/rui/audio/probe.py:53-60`](../../src/rui/audio/probe.py#L53-L60),
  [`src/rui/audio/probe.py:190-218`](../../src/rui/audio/probe.py#L190-L218)).
- Precedence is `core default < global config < manifest hardware facts <
  devices[].audio`; an explicit device override is the only way a 16 kHz room
  can legitimately override a 48 kHz model
  ([`src/rui/config/schema.py:561-596`](../../src/rui/config/schema.py#L561-L596)).

**Fixes.** Set `devices[].audio.playback.rate` (and `channels`) to the wire the
adapter accepts, or fix `audio_defaults.playback` in the manifest. Never "fix" it
by removing the manifest: the comparison then silently disappears and the first
`play()` fails at runtime.

**Related failure.** The adapter rejecting a block is also classified as a wire
mismatch: a `ValueError` from `play()` stops the plane with
`audio_config_mismatch: playback rejected by the adapter: …` and one
`playback error (play) #N: …` line
([`src/rui/audio/process.py:977-1015`](../../src/rui/audio/process.py#L977-L1015)).
Check `/health.audio_devices[].playback_errors` / `playback_error`
([`src/rui/audio/process.py:3628-3629`](../../src/rui/audio/process.py#L3628-L3629)).

---

## 4. Device not registered, or not connected

### 4a. The adapter is not registered / the plug does not resolve

**Log line** (control process, at startup):

```text
WARNING rui.web.api.app: device ghost is configured but not drivable: unknown adapter: 'ghost-adapter' (this build has: fake-speaker, mock); install the plug that owns this device under /opt/rui-plugins (or register it through RUI_ADAPTER_PLUGINS) and set devices[].adapter to the id its manifest declares
```

That message comes from [`src/rui/web/api/app.py:3256-3262`](../../src/rui/web/api/app.py#L3256-L3262)
and [`src/rui/device/registry.py:214-226`](../../src/rui/device/registry.py#L214-L226).

**API view.**

```bash
curl -s http://127.0.0.1:8080/api/v2/devices/ghost | python3 -m json.tool
```

```json
{"status":"unavailable","ready":false,
 "status_reason":{"code":"plug_unresolved","message":"no installed plug describes this device","hint":"Install the plug that owns this model, or remove and re-add the device."},
 "capabilities":[]}
```

`plug_unresolved` means no manifest describes the device; `adapter_unavailable`
means the manifest resolved but the adapter name is not registered in this
process. Both are computed from independent checks, and the reason code priority
is fixed ([`src/rui/web/api/app.py:2712-2766`](../../src/rui/web/api/app.py#L2712-L2766),
[`src/rui/web/api/v2_api.py:364-407`](../../src/rui/web/api/v2_api.py#L364-L407),
[`src/rui/web/api/v2_api.py:1348-1389`](../../src/rui/web/api/v2_api.py#L1348-L1389)).
When no manifest resolves, `plug_unresolved` wins even if the adapter is also
missing — the fix is the same install step.

**Fix.** Install/register the plug (see [`install-paths.md`](install-paths.md)),
then restart. Verify with `/health.adapter_plugins.registered` and the device
detail.

### 4b. The device has not dialled in

**Symptom** (inbound transport): the plane is running, the adapter is registered,
but `connected` is false and `status_reason.code` is `not_connected`, or
`token_missing` when no token is configured for the device.

```json
{"status":"disconnected","ready":false,
 "status_reason":{"code":"not_connected","message":"the device has not dialled in","hint":"Start the device-side client and confirm it points at this server and port."}}
```

```json
{"status_reason":{"code":"token_missing","message":"no device token is configured","hint":"Rotate a token on the device page, or set the token environment variable."}}
```

How the status is built: the adapter/plane link report plus token configuration
([`src/rui/web/api/app.py:2742-2766`](../../src/rui/web/api/app.py#L2742-L2766));
`not_connected` requires a configured token and a running plane, so a wrong token
shows up as `speaker_rejected` rather than "not connected"
([`src/rui/web/api/app.py:2674-2686`](../../src/rui/web/api/app.py#L2674-L2686)).

**What to check, in order:**

1. **Port.** `devices[].listen_port` wins; otherwise the manifest's
   `runtime.listen_port` (required in any runtime block); 0.8.x has no core
   fallback port ([`src/rui/web/api/device_rules.py:51-76`](../../src/rui/web/api/device_rules.py#L51-L76),
   [`src/rui/plugins/models.py:190-208`](../../src/rui/plugins/models.py#L190-L208)).
   Confirm the device points at that host/port and the port is reachable:

   ```bash
   curl -s http://127.0.0.1:8080/api/v2/devices/<device-id> | python3 -c \
     'import json,sys; d=json.load(sys.stdin); print(d.get("listen_port"), d.get("transport"), d.get("status_reason"))'
   nc -vz <rui-host> <listen_port>
   ```

2. **Token name and value.** `runtime.token_env` (default `RUI_DEVICE_TOKEN`)
   names the *variable*, not the value; the manifest default fills it when the
   config does not set one
   ([`src/rui/web/api/device_rules.py:78-101`](../../src/rui/web/api/device_rules.py#L78-L101),
   [`src/rui/config/schema.py:267-275`](../../src/rui/config/schema.py#L267-L275)).
   The token value lives in the environment/rui's secret store, never in the
   manifest. A missing token means the adapter must fail closed; a wrong token on
   an inbound connection is rejected by the adapter (for the LX06 family this is
   `xiaoai: rejected <peer> (bad or missing token)`; the contract requires code
   1008 — [`docs/xiaoai-protocol.md:78-88`](../xiaoai-protocol.md#L78-L88)).
   The plane counts those rejections as `speaker_rejected`
   ([`src/rui/audio/process.py:3601`](../../src/rui/audio/process.py#L3601),
   [`src/rui/web/api/app.py:1336-1370`](../../src/rui/web/api/app.py#L1336-L1370)).
   For a development client that cannot carry a token, `allow_no_token: true`
   opens the listener deliberately and logs it
   ([`src/rui/config/schema.py:271-275`](../../src/rui/config/schema.py#L271-L275)).

3. **Model manifest (hardware facts).** If the log says

   ```text
   WARNING __main__: audio facts (startup): device=fake has no usable model manifest (<reason>); hardware facts fall back to the global/core values - playback/ack/capture may be wrong
   ```

   then the plane can drive the adapter but cannot check the wire or use the
   model's `audio_defaults`; install the manifest where the plane resolves it
   (co-located with the adapter or `<data_dir>/plugs` + `RUI_DATA_DIR`)
   ([`src/rui/audio/process.py:820-845`](../../src/rui/audio/process.py#L820-L845)).

4. **One-shot diagnostics.** The device action endpoint runs the wizard's checks
   against the live process:

   ```bash
   curl -s -X POST http://127.0.0.1:8080/api/v2/devices/<device-id>/test \
     | python3 -m json.tool
   ```

   It reports per-check evidence and the same stable codes
   ([`src/rui/web/api/v2_api.py:2805-2811`](../../src/rui/web/api/v2_api.py#L2805-L2811)).

**Fixes.** Correct the address/port/token, start the device-side client, reload
the adapter/plane (restart rui after an install change), and re-check
`/health.speaker_connected` and `speaker_rejected`.

---

## Quick reference

| Symptom | First place to look | Code |
| --- | --- | --- |
| `/health.adapter_plugins.registered` lacks the name | `grep "adapter plugin:" service.log` | [`src/rui/device/loader.py:992-998`](../../src/rui/device/loader.py#L992-L998) |
| Device `unavailable` / `capabilities: []` | `/api/v2/devices/<id>`, `plug_unresolved` vs `adapter_unavailable` | [`src/rui/web/api/v2_api.py:1348-1389`](../../src/rui/web/api/v2_api.py#L1348-L1389) |
| Action refused with 409 | capability matrix + `probe()` capabilities | [`src/rui/web/api/v2_api.py:2836-2870`](../../src/rui/web/api/v2_api.py#L2836-L2870) |
| Plane refuses to start / room never ready | `audio wire` error + `/health.planes[].error` | [`src/rui/audio/process.py:905-974`](../../src/rui/audio/process.py#L905-L974) |
| `playback_errors` grows while playing | adapter rejects the playback wire or the sink | [`src/rui/audio/process.py:977-1015`](../../src/rui/audio/process.py#L977-L1015) |
| `not_connected` with a running plane | port, device client, token, `speaker_rejected` | [`src/rui/web/api/device_rules.py:51-101`](../../src/rui/web/api/device_rules.py#L51-L101) |
