# Socket & Plug v1 — contract/spec audit (read-only)

> Repo: `house-ear` / `yes999zc/rui`, HEAD `4b279a7` ("docs: production upgraded to 0.3.2 non-root"),
> RUI `0.3.2` (`pyproject.toml:3`, `src/rui/__init__.py:1`).
> Scope: spec/contract side only — `docs/socket-plug-v1.md`, `docs/socket-plug.md`,
> `docs/playback-path.md`, `docs/install-wizard-design.md`, GitHub issues #1/#3/#7,
> `src/rui/device/contract.py`, `src/rui/plugins/{models,loader}.py`,
> `src/rui/config/schema.py`, plus the implementation paths needed to check the spec.
> No code, commit, NAS or external post was touched. The only written artifact is this file.
>
> Legend: **MUST** = v1 normative requirement; evidence is `file:line` in this repo.
> Issue comments are quoted from the GitHub API (token read from `~/.git-credentials`; not printed).

---

## 0. Verdict

The v1 *shape* is coherent: `DeviceWake`, `drain_wakes()`, the v0/v1 manifest split, the
device-wake policy and the downstream hook all exist in 0.3.2. But the
**contract is not yet implementable exactly as written** by a third-party plug author, and
several answers given to dot2 in #3/#7 contradict the frozen spec or the code.

| Severity | Count | Meaning |
|---|---:|---|
| P0 | 4 | A plug following the docs gets a broken/rejected integration, or the trust boundary is not enforced. |
| P1 | 12 | Spec and code disagree on a behaviour a plug author can observe; must be resolved before calling v1 stable. |
| P2 | 12 | Naming/doc/validation drift; no immediate integration blocker, but it makes the contract ambiguous. |

The four P0s are: (1) `trust` is self-declared and the documented downstream hook bypasses
it, while the only installable path is to self-declare `trust: trusted` (or `builtin`); (2) volume is specified in
0–100 but delivered as 0–255 through an undocumented `adapter.services`, leaving
`DeviceAdapter.set_volume` dead; (3) issue #7 Q5 tells dot2 to send `device_id/room/playing/
last_active` fields that `DeviceWake` does not have; (4) issue #3 says `wake` may be added to
`rui-device/v0`, which the v1 spec and 0.3 loader reject.

---

## 1. Ranked findings

### P0 — blockers

#### P0-1. Trust is self-asserted; the downstream hook bypasses the manifest trust model and there is no installable third-party path

- `src/rui/plugins/models.py:471` — `trust` is read from the plugin's own `plugin.yaml`
  (default `third-party`). There is no signature, allowlist or operator approval in the
  loader.
- `src/rui/plugins/loader.py:223-228` — the only enforcement is "third-party + explicit
  `runtime.mode: in-process` is rejected"; a plugin that writes `trust: trusted` (one line)
  is accepted as in-process.
- `src/rui/device/loader.py:95-120` — `RUI_ADAPTER_PLUGINS` / `rui.adapters` imports and
  registers builders **without reading `plugin.yaml` at all**; manifest trust and
  `runtime.mode` cannot gate this path.
- `src/rui/web/api/setup_api.py:115-123` — the wizard (`/api/v1/plugs`, setup steps) rejects
  *any* `third-party` plugin (`third_party_not_supported`) and *any* `runtime.mode: sidecar`
  (`sidecar_not_supported`). So a third-party plug cannot be installed via the wizard, and
  there is no sidecar runner either (see P1-9).
- `docs/socket-plug-v1.md:235` and `:476` — the spec tells the operator to use
  `manifest trust: trusted` for the downstream hook, i.e. it instructs the plug to
  self-assert the very label that is treated as the gate. `docs/install-wizard-design.md:251`
  says "only `builtin`/`trusted` are installable", which is the same self-assertion.
- `docs/socket-plug-v1.md:138` — "third-party 默认且只能 sidecar" is therefore not a
  security property; it is a manifest validation rule that the official hook bypasses.

**Impact.** The v1 trust story reads as a review/allowlist boundary but in 0.3.2 it is a
string in a downloaded file. The only supported route for dot2 is: downstream image +
`RUI_ADAPTER_PLUGINS` + `trust: trusted` self-declared, which is exactly the privileged path
the spec says third-party code must not get. This is also the main fairness problem (see §6).

**Spec fix.** State explicitly that `trust` is descriptive metadata, not an authorization
boundary. Add an out-of-band `plugins.allow`/signature mechanism (or an operator-owned
`trusted` directory) before calling it "trust". If 0.3.x cannot do that, say in §4.4 and
§10 Q1 that the hook is an operator-only escape hatch and that third-party sidecar is 0.4.

**Code fix (recommended, not done).** Do not authorize on manifest `trust`; resolve trust
from an allowlist/signature/operator config. At minimum, refuse to register a name via
`RUI_ADAPTER_PLUGINS` when the resolved device manifest is `third-party` unless an explicit
operator allowlist matches, and reject `trust: trusted` from user writable directories.

#### P0-2. Volume contract is wrong: spec says 0–100, core delivers 0–255 through an undocumented `services` object; `DeviceAdapter.set_volume` is dead

- `docs/socket-plug-v1.md:39` — "RUI 0–100，adapter 映射（LX06 为 0–255）".
- `docs/socket-plug-v1.md:151` — lists `set_volume()` as an adapter method.
- `src/rui/device/contract.py:186` — `DeviceAdapter.set_volume(volume: int)` with no range.
- `src/rui/device/contract.py:135-137` — `DeviceServices.read_volume()` is documented as
  **0–255**; `src/rui/device/contract.py:125-148` defines `DeviceServices`, but
  `DeviceAdapter` (`:167-206`) never exposes `services`. It is not in the spec's §4.1 table.
- `src/rui/audio/process.py:2051-2057` — core calls `adapter.read_volume()` (not
  `services.read_volume()`).
- `src/rui/audio/process.py:2075-2083` — core calls `adapter.services.set_volume(want)`
  where `want = round(config_volume_0_100 * 255 / 100)`; i.e. the value at the adapter
  boundary is **0–255**, not 0–100.
- `src/rui/audio/process.py:2203-2215` — the device-volume message handler also calls
  `adapter.services.set_volume` / `read_volume` and converts back `value * 100 / 255`.
- `src/rui/web/api/app.py:2437-2461` — `GET/PUT /api/v1/device/volume` is explicitly
  `0–255` (`value must be an int in 1..255`), while `src/rui/config/schema.py:180` defines
  `devices[].volume` as 0–100. The voice tool converts at `app.py:1778-1810`.
- `src/rui/plugins/contract.py:97-108` — the kit requires `set_volume` and `set_led` but
  **not** `read_volume` or `services`, so an adapter that implements only the documented
  `set_volume` passes the kit and then has no volume in production.

**Impact.** A dot2 adapter that follows the spec's "RUI passes 0–100, adapter maps to its
own scale" will receive 0–255 and will either clamp every write to its maximum or map the
wrong scale. Conversely, an adapter that implements `services` (the actual called object)
is not documented anywhere in v1. The same word "volume" now has four units: config 0–100,
adapter contract unspecified, `DeviceServices` 0–255, HTTP API 1–255.

**Spec fix.** Choose one adapter-boundary unit and write it in §2/§4.1. Recommended:
`DeviceAdapter.set_volume(percent: 0–100)`; core converts to the device mixer; keep
`DeviceServices` as an optional low-level escape with an explicit `0–255` doc. Add
`read_volume()` and `services` to the adapter surface table, or remove `DeviceAdapter.
set_volume` and make `services` the only contract (with a default `NullServices`).

**Code fix.** Either call `adapter.set_volume` (with 0–100) or document/protocolize
`services`; fix the kit; add a unit test at both boundaries. See §8.

#### P0-3. Issue #7 Q5 / v1 §10 Q5 require `DeviceWake` fields that do not exist; dot2 will crash or have fields silently dropped

- `src/rui/device/contract.py:81-86` — `DeviceWake` is exactly
  `ts_ms, score, threshold, barge, level, source` (a frozen dataclass).
- `src/rui/audio/protocol.py:233-249` — `DeviceWakeMsg` has the same six fields; pydantic's
  default `extra=ignore` silently drops anything else.
- `docs/socket-plug-v1.md:480` — Q5 says the adapter must honestly report
  `device_id/room/score/threshold/barge/playing/ts_ms` (optional `last_active`). Q5 is
  repeated in the issue #7 maintainer comment.
- There is no `device_id`, `room`, `playing`, `last_active` in `DeviceWake`/`DeviceWakeMsg`.
  Passing them to the dataclass raises `TypeError`; passing them to `DeviceWakeMsg` is
  silently ignored (verified against the venv: `DeviceWake(... device_id="dot2")` →
  `TypeError`; `DeviceWakeMsg(... device_id="dot2")` → field absent).
- `src/rui/audio/process.py:878-885` — the plane only sends the six fields.
- The one-plane-per-device model means `device_id` is currently implicit, but 0.4
  arbitration cannot be built from a v1 event that has none of the promised fields.

**Impact.** dot2 is being asked to implement a reporting shape that the contract rejects
(or silently discards). If they use the dataclass, they get a runtime crash the moment they
add the fields the answer asks for.

**Spec fix.** Either (a) add optional `device_id: str`, `room: str`, `playing: bool`,
`last_active_ms: float | None` to `DeviceWake`/`DeviceWakeMsg` as an additive v1.1
(the fields do not grant policy), or (b) retract Q5 and say "v1 events carry only
ts/score/threshold/barge/level/source; 0.4 will define a separate arbitration event."
Whichever is chosen, update the issue #7 answer and §10 Q5 (see draft comment).

**Code fix.** If (a): add the fields to the dataclass, the pydantic message, and
`_send_device_wake_msg`; ignore them in the policy. Add a test that extra fields do not
change the decision.

#### P0-4. Issue #3 says `wake` can be added to `rui-device/v0`; v1 §9 Q1, §7 and 0.3 code reject it

- Issue #3 maintainer comment (2026-10-24), contract item 1:
  "`wake: {source: device|rui, barge_via_device: bool}`（`rui-device/v0` 里 optional，
  旧 manifest 不受影响；`extra=forbid` 只拒绝未知 key，加这个块是合法的）".
- `docs/socket-plug-v1.md:459` — Q1 decision: "`rui-device/v1` + 0.3 兼容 v0；v0 不得声明
  `wake`/`device_wake`".
- `docs/socket-plug-v1.md:353` — "v0 不得声明 `wake`/`device_wake`（否则报错，不静默升级）".
- `src/rui/plugins/models.py:385-401` — v0 + `wake` (even `{source: rui}`) or + `model`
  or + `device_wake`/`barge_via_device` raises a validation error.
- `docs/socket-plug-v1.md:110` — "`wake` | mapping / 否 | 仅 v1".

**Impact.** dot2 following the older #3 answer publishes a v0 manifest with `wake`, and
0.3.2 refuses to load it. This is a direct contradiction between an answer and the frozen
spec; it is cheap to fix in text, expensive to discover at integration time.

**Spec fix.** Keep the v1 decision (it is the safer one under `extra=forbid`) and post a
correction to #3; add an explicit "v0 + wake" error example and migration snippet. Do not
silently accept v0+wake.

**Code fix.** None needed if v1 is final; improve the error text to say
"v0 cannot declare wake; publish rui-device/v1 or omit wake and use RUI KWS".

### P1 — spec/code disagreement or completeness blocker

#### P1-1. `wake.barge_via_device` is never consulted; probe capability alone enables device barge

- `docs/socket-plug-v1.md:125` — "能力 `barge_via_device` 表示'设备做得到'，
  `wake.barge_via_device` 表示'本插头启用'；两者都真才允许设备 barge 建议".
- `docs/socket-plug-v1.md:252` — barge allowed only if "manifest `wake.barge_via_device: true`
  且能力已生效".
- `src/rui/plugins/models.py:453-456` — `barge_via_device_enabled` property exists, but it
  is **not used anywhere** in `src` (grep).
- `src/rui/audio/process.py:683-691` — `barge = Capability.BARGE_VIA_DEVICE in capabilities`
  (probe only); no manifest flag. A manifest with
  `capabilities: [device_wake, barge_via_device, playback]` and
  `wake: {source: device, barge_via_device: false}` still sets
  `device_wake_barge_active = True`.
- The kit's `_barge_allowed` (`src/rui/plugins/contract.py:1700-1711`) correctly requires
  both manifest and probe, so the kit passes while the runtime misbehaves.

**Impact.** A plug that intentionally disables barge (e.g. during acceptance) cannot; RUI
will still stop playback on a device `barge=true`. It also means the manifest is not the
upper bound for barge.

**Spec fix.** Keep "both true" and add an explicit "probe ∩ manifest ∩ wake flag" formula
in §2/§5. Add a sentence that a capability without the flag is "can but disabled".

**Code fix.** Resolve `barge_via_device_enabled` from the plug manifest in
`_configure_device_wake` (same path as `_manifest_wake_source`) and AND it into `barge`.

#### P1-2. Manifest ∩ probe capability rule is enforced only for `device_wake`; `reference`/`playback`/`mic_array` are probe/behaviour only

- `docs/socket-plug-v1.md:47-50` — "manifest 是上限：adapter 不得执行未声明能力" and
  "`mic_array`/`reference`/`playback`/`device_wake` 以 manifest ∩ probe 为准".
- `src/rui/audio/process.py:457-470` — AEC is built from `info.mic_channels`/`info.ref_channels`
  (probe), with no manifest lookup.
- `src/rui/audio/process.py:527-548` — voice path uses `frame.mic_channels`/`frame.
  reference_channels`; nothing checks the manifest's `reference`/`mic_array` bit.
- `src/rui/audio/process.py:645` and `:684-685` are the **only** capability checks in the
  plane (`DEVICE_WAKE`, `BARGE_VIA_DEVICE`); `Capability.PLAYBACK` is not checked before
  `play()`, and `Capability.REFERENCE`/`MIC_ARRAY` are not checked anywhere outside
  `models.py`/the kit.
- `src/rui/device/contract.py:62-64` — `missing_for_barge_in()` uses probe capabilities
  only.

**Impact.** A probe that over-reports `reference` gets AEC even if the manifest omits it;
a manifest that omits `playback` still gets TTS. The "manifest is the upper bound" promise
is false for three of the five probe-bound capabilities. A third-party can lie in `probe()`
and the manifest cannot contain it.

**Spec fix.** Either implement "effective = manifest ∩ probe" centrally and use it for
AEC/playback/UI, or narrow §2.2 to "only `device_wake`/`barge_via_device` are manifest-bound
in 0.3; the rest are probe-reported".

**Code fix.** Compute an `EffectiveCapabilities` at connection setup (probe ∩ manifest) and
gate `_build_aec`, `_frame_to_voice` (array vs mono), `play()` and the UI on it.

#### P1-3. `missing_for_barge_in()` reports dot2 as unable to barge even with device-side AEC + `device_wake`

- `src/rui/device/contract.py:62-64` — barge requires `REFERENCE` + `PLAYBACK`.
- `docs/socket-plug-v1.md:35` — `reference` missing means `missing_for_barge_in()` non-empty
  and "不承诺 RUI barge"; but `docs/socket-plug-v1.md:479` (Q4) accepts
  `who=device` with no RUI AEC, and `:41` allows device barge via `barge_via_device`.
- `src/rui/web/api/app.py:3372-3373` and `:4258` expose `missing_for_barge_in` to the UI.
- dot2's preferred path is device-side AEC (v1 `:317` says "无 `reference` 合法"; issue #7
  notes a 2ch reference patch exists but does not remove the device-AEC path), so the
  wizard and device page will report "missing: reference" even when `device_wake` +
  `barge_via_device` + `playback` is a valid barge path.

**Impact.** The UI tells the user a supported barge path is unavailable; the plug author
cannot express "barge via device wake, no reference".

**Spec fix.** Define barge paths explicitly:
`(reference + playback)` **or** `(device_wake + barge_via_device + playback + wake.source:
device)`; update `missing_for_barge_in()` or replace it with `barge_paths()`.

**Code fix.** Make `missing_for_barge_in()` aware of the device-wake path, or expose a
`barge_path` field in `DeviceInfo` and display that.

#### P1-4. Manifest `runtime.listen_port` / `runtime.token_env` are validated but not applied; `/connect` ignores them

- `docs/socket-plug-v1.md:112` and `:137` — `runtime.listen_port` / `token_env` are part of
  the field contract; `docs/socket-plug.md:132` and
  `docs/install-wizard-design.md:247` say the manifest can override the defaults.
- `src/rui/web/api/setup_api.py:189-196` — the setup connect verdict uses
  `device.listen_port` / `device.token_env` **first**, and falls back to the manifest only
  when the device is `None`.
- `src/rui/web/api/app.py:913-916` — `_token_env()` reads only `DeviceConfig.token_env`
  (default `RUI_DEVICE_TOKEN`), never the resolved manifest.
- `src/rui/web/api/app.py:3836-3839` — `_device_ws_url()` uses
  `effective_listen_port(device)`, and `src/rui/web/api/device_rules.py:33-35` returns
  `device.listen_port or 4412`; the manifest's `runtime.listen_port` is never consulted.
- The setup write path only sets `listen_port` if the operator sends it
  (`src/rui/web/api/setup_api.py:914-924`); it never copies `runtime.listen_port` or
  `runtime.token_env` into the new device.
- `src/rui/web/api/setup_api.py:586` prints `runtime.token_env`/`listen_port` in the plug
  detail, so the UI can show 8870 while `/connect` still returns 4412.

**Impact.** A dot2 manifest with `runtime.listen_port: 8870` and a custom `token_env` is
shown as 8870 in the plug page but the generated connect URL uses 4412/default token. The
plug author cannot rely on the documented runtime defaults.

**Spec fix.** State precedence explicitly: "manifest runtime values are defaults applied
at device creation; config always wins after that" or "config wins; manifest runtime values
are display-only". Whichever, make `/connect` and the setup verdict use the same resolution.

**Code fix.** When creating a device from a plug, seed `listen_port`/`token_env` from the
manifest if absent; make `_device_ws_url`/`_token_env` consult the resolved manifest as a
fallback (or remove the misleading fields from the plug detail).

#### P1-5. `device_side` and `params` rules in the spec are not validated; wizard accepts undeclared/loosely typed params

- `docs/socket-plug-v1.md:111` and `:136` — `device_side.source` only http(s)/empty, no
  username/password; `install_steps` ≤20 × ≤500 chars; B-class `rollback` non-empty.
- `src/rui/plugins/models.py:137-147` — `DeviceSide` has **no validators**; the loader adds
  none (`src/rui/plugins/loader.py:66,80`). Verified with the venv:
  `device_side.source: "file:///etc/passwd"` accepted, `https://user:pass@...` accepted,
  21 install steps accepted, B-class with empty `rollback` accepted.
- `docs/socket-plug-v1.md:135` — `list.max` "生效"; `src/rui/plugins/models.py:230` has the
  field but `_default_matches_type` (`:242-272`) never checks length. Verified: a 4-item
  default with `max: 3` is accepted.
- `docs/install-wizard-design.md:269` says unknown top-level fields are ignored; the loader
  uses `extra="forbid"` (`src/rui/plugins/models.py:341`; v1 `:134`), so a forward-compatible
  plugin is rejected.
- `docs/install-wizard-design.md:271` says undeclared param keys are 422; `src/rui/web/api/
  setup_api.py:628-646` `_coerce_params` accepts any lower-case key and any loose Python
  value, and never checks `ParamSpec.type`/`options`/`max` (a `choice` outside options is
  written; an `int` can be a float).
- `docs/install-wizard-design.md:266` and `:374` still say v0 has no `choice.options`;
  `src/rui/plugins/models.py:244-245` requires non-empty options.

**Impact.** A malicious or careless plug can put `file://` or credential-bearing URLs into
`device_side.source` (rendered by the console), a B-class plug can omit its rollback, and
the form's type/`max`/`choice` contract is not enforceable. This is both a safety and a
third-party-author predictability issue.

**Spec fix.** Make the `device_side` and `params` rules normative and point at the model
validators; either remove "unknown fields are ignored" or make `extra` behavior
field-selective with an explicit forward-compat section.

**Code fix.** Add validators to `DeviceSide` (`HttpUrl`/scheme, userinfo rejection,
install_steps length, B+rollback cross-field), enforce `list.max`, and validate submitted
params against the selected manifest `ParamSpec` (declared keys only, types, options,
`max`).

#### P1-6. Version ranges conflict; `rui_compat` is not the only gate; builtin LX06 advertises incompatibility with 0.3.2

- `docs/socket-plug-v1.md:365` — dot2 v1 recommendation: `rui_compat: ">=0.3.0 <0.4.0"`.
- Issue #7 maintainer comment (final line): `rui_compat` 建议 `>=0.3 <0.5`.
- `src/rui/plugins/builtin.py:115` — builtin `xiaoai-lx06` says `rui_compat: ">=0.2.0 <0.3.0"`,
  so the built-in plugin is "incompatible" with the running 0.3.2; it is loaded by
  `builtin_plugins()` without a compat check, so the mismatch is invisible.
- `plugins/rui-plugin-xiaoai/plugin.yaml:5` — reference package says `>=0.2 <0.4`.
- `examples/plugin-example/plugin.yaml:5` — says `>=0.2.0 <0.4.0` while its device manifest
  is `rui-device/v1`, which 0.2.x cannot parse.
- `src/rui/plugins/loader.py:68-74` checks plugin `rui_compat`, but `:204-210` also checks
  device `compatibility.rui_min`; so §7's "`rui_compat` 是唯一插件级版本门" is false (it is
  the only *plugin-level* gate, but there is a second *device-level* gate).
- `src/rui/plugins/builtin.py:110-120` validates the builtin `Plugin` model but never calls
  `load_plugin`/`parse_range`, so builtins bypass the compat check.

**Impact.** A third-party author cannot know which range to trust; the builtin reference
contradicts the running version; the example advertises 0.2 support for a v1 manifest.

**Spec fix.** Publish one compatibility matrix and use it in every example. Recommended:
v1 manifests `>=0.3.0 <0.4.0`; v0 manifests `>=0.2.0 <0.4.0`; note both gates
(`rui_compat` plugin, `compatibility.rui_min` device). Bump the builtin and the reference
package to the same range; fix the example.

#### P1-7. `device_wake` can activate with no resolvable manifest, contrary to §2/§4.3 (but allowed by §7.1/code)

- `docs/socket-plug-v1.md:47-48` and `:192` — manifest must declare `device_wake` and
  `wake.source: device`; manifest is the upper bound.
- `docs/socket-plug-v1.md:401-406` — §7.1 says an adapter with no resolvable manifest
  (mock/dev in-process) has "no upper bound" and uses `probe()`.
- `src/rui/audio/process.py:697-721` — `_manifest_wake_source()` returns `None` when no
  plug resolves; `:644-683` then proceeds to `self.device_wake_active = True` based on
  `probe()` alone.
- `src/rui/audio/process.py:705-716` — the lookup is `cfg.devices` + `resolve_device_plug`;
  an adapter registered only via `RUI_ADAPTER_PLUGINS` and not present in `data/plugs/`
  (or whose `params.plug_id`/adapter does not match a device manifest) falls into the
  `None` branch.

**Impact.** An out-of-tree adapter loaded through the documented hook can turn on
`device_wake` without any manifest declaring `wake.source: device`. The dot2 author may
believe the manifest is the gate; the runtime is not. It also bypasses the manifest's
`barge_via_device` flag (P1-1).

**Spec fix.** Pick one: (a) require a manifest for `device_wake` (no manifest → KWS), or
(b) state the no-manifest exception in §2 and §4.3, not only in the §7.1 implementation
notes. For a "manifest is the upper bound" contract, (a) is more honest.

**Code fix.** If (a): return `"rui"` (or a distinct `"unknown"`) and fall back to KWS when
no manifest resolves; if (b): document it and add a test.

#### P1-8. TLS/wss and plug-specific connect URL/auth cannot be expressed; `/connect` hardcodes `ws(s)://host:port/?token=`

- `docs/socket-plug-v1.md:107-108` — `transport: inbound|outbound`; `protocol.kind` is
  free-form and "其余 key 归 adapter，核心不校验".
- `docs/socket-plug-v1.md:477` — Q2 says Dot2 uses 8870/wss and is not in conflict.
- Issue #7 "our device facts": device dials **TLS WebSocket** with `X-EM-Token`, inner
  `/control` + `/data`; LX06 is plain ws + query token.
- `src/rui/web/api/app.py:3836-3839` — `_device_ws_url` always builds
  `f"{scheme}://{host}:{port}/?token={quote(token)}"`; `request_ws_scheme`
  (`app.py:1331-1336`) only returns `wss` when the *console request* is https/wss. There is
  no per-device scheme/path/token-location field and no certificate/TLS config.
- The setup docs still list "TLS/自建 CA/每设备端口" as "never or P3"
  (`docs/install-wizard-design.md:390`), contradicting the Q2 answer.

**Impact.** On a normal http console, `/connect` produces `ws://…:4412/?token=…`, which is
not a valid dot2 URL (wrong scheme, port, path and token location). dot2 must bypass the
wizard and own its TLS/auth; that is not described in v1. A third-party inbound adapter
cannot tell the operator how to connect through the documented flow.

**Spec fix.** Add a `runtime.connect` block (or `protocol` keys) with `scheme` (ws/wss),
`path`, `token_in` (`query`/`header`/`none`), `token_name`, and optional `tls` (cert
reference, not a secret). State that core never terminates TLS for a plug; the adapter
owns it. Add an outbound/auth example.

**Code fix.** Make `_device_ws_url` consume the resolved manifest's `runtime.connect` (or
at least `protocol.kind`); if no block, keep the current LX06 default. Add `wss` to the
connect UI only when the manifest declares it.

#### P1-9. Sidecar is specified but not implemented; the wizard rejects it, so "third-party default sidecar" is not a runnable path

- `docs/socket-plug-v1.md:112` / `:138` — sidecar is required for `third-party`;
  `:353` says sidecar only standardizes the RUI-side proxy adapter, RPC not standardized.
- `src/rui/web/api/setup_api.py:121-122` — any `runtime.mode: sidecar` is
  `sidecar_not_supported`; therefore a third-party plug (which must be sidecar) is
  double-blocked.
- `src/rui/device/registry.py:59-75` — `build_adapters` builds adapters **in-process**;
  there is no container/socket/health/start/stop runner anywhere in `src` (grep
  `sidecar` returns only models/loader/process comments).
- `src/rui/audio/process.py:664-673` — if a manifest resolves to sidecar, `device_wake`
  degrades to KWS; that is the only sidecar-aware code.

**Impact.** A third-party plug author reading §3.3/§10 Q1 will implement a sidecar and
then find it cannot be installed or run. The only working path is the in-process
`RUI_ADAPTER_PLUGINS` hook with a self-declared `trust: trusted` (P0-1).

**Spec fix.** For 0.3.x, say plainly: "third-party executable plugs are not supported;
only builtin/trusted in-process adapters run. Sidecar is 0.4." Remove or mark §3.3's
"third-party default and only sidecar" as future. For 0.4, specify the sidecar RPC
(transport, health, start/stop, token, wake proxy) before asking authors to build it.

#### P1-10. Third-party contract is incomplete: `AudioFrame`, `DeviceInfo`, `services`, play-head, lifecycle, errors, outbound, TLS/auth, examples

This is the "can a third-party implement from the docs alone?" answer: **not yet**.

- `docs/socket-plug-v1.md:22` says the plug only sees `AudioFrame`, `play()`,
  `stop_playback()`, `DeviceWake`, but the doc never defines `AudioFrame`. The actual
  fields are `device_id, seq, ts_ms, pcm, channels, sample_rate, reference_channels, meta`
  (`src/rui/device/contract.py:89-122`); pcm is int16 LE, already scaled, with reference
  channels trailing. None of that is in the spec.
- `DeviceInfo` is only mentioned through `DeviceInfo.mic_channels` (`v1:34`); the full
  required fields (`id/adapter/model/host/room/capabilities/transport/channels/
  sample_rate/bit_depth/mic_channels/ref_channels`) are at `contract.py:44-57`. An author
  cannot know which are required, which are display-only, or what `transport` defaults to.
- `docs/socket-plug-v1.md:151` lists only `set_volume`/`set_led`; it never mentions
  `services`, `read_volume`, `shell`, `health`, `vendor_playing` or `pause_vendor_player`
  (all used by the plane: `process.py:2051-2083`, `_begin_wake_turn` at `:1088-1093`,
  `_build_metrics` at `:1983-1987`).
- play-head: `v1:153` mentions `reports_consumption`/`wait_consumed`; the actual fields
  `consumed`/`sink_generation` are at `contract.py:218-224`, and the chunk unit/index
  semantics are only in comments. `played_ms` appears only in Q6 (`v1:481`), not in the
  contract/protocol at all.
- lifecycle: `v1:149` says `open()/close()` idempotent; there is no reconnect, open timeout,
  close ordering, error taxonomy, or "what the plane does when `open()` raises" contract.
- outbound: `v1:107` says "outbound 由 `probe()` 连接", but there is no outbound example,
  no host/auth/TLS guidance, and `is_inbound()` treats an unresolved device as inbound
  (`src/rui/web/api/device_rules.py:38-53`).
- examples: `examples/plugin-example` is inbound v1; `plugins/rui-plugin-xiaoai` is
  inbound v0. No sidecar example, no outbound example, no volume/LED-only example, no
  manifest error table.
- the kit requires `set_volume`/`set_led` but not `read_volume`/`services`
  (`src/rui/plugins/contract.py:97-108`), so "passes `rui plug-test`" is not the same as
  "works in production".

**Spec fix.** Add a normative "Adapter at a glance" section:
`DeviceInfo` (all fields, defaults, authority), `AudioFrame` (layout/units/cadence),
`DeviceServices`/`read_volume`/`services`, play-head (chunk unit, generation, timeout),
lifecycle (open/close/reconnect, idempotency, exception policy), auth/TLS, outbound, and
two complete examples (inbound + outbound). See §7.

#### P1-11. The playback-gap / boundary harness and ordered-start barrier promised by v1 do not exist; §6.4 asks dot2 to use them

- `docs/socket-plug-v1.md:305-306` lists `Playback gap harness` and `Boundary harness` as
  P3.0 deliverables; `:313` sets `max_gap_ms <100` as a gate; `:317` (dot2 checklist) says
  the control/data socket plug must pass "§6.4 的 P3 设计并通过 gap harness".
- `docs/plug-test-kit.md:228-229` explicitly says the kit does **not** test "播放 gap
  harness" (or the rest of §5/§6.2 harnesses); it only validates the adapter surface.
- `docs/playback-path.md:213-249` is a design ("本轮不实现") for `Ready.ordered_start`,
  `flush(epoch)`, `flushed(epoch)`, `start(epoch)`, `pcm_stale_dropped`, etc.
- `src/rui/audio/protocol.py:90-100` — `Ready` has no `ordered_start`/epoch capability.
  There is no barrier implementation in the plane.

**Impact.** The v1 acceptance story for dot2 (issue #1) cannot be reproduced with the
published kit, and the spec asks for a barrier that 0.3.2 does not expose. Either dot2
implements its own private workaround (as it did) or it cannot claim §6.4.

**Spec fix.** Either implement and publish the harness + `Ready.ordered_start`/epoch
barrier, or remove the §6.2/§6.4 claims and state "v1 does not define the cross-socket
start/flush barrier; adapter-side workaround required; RUI-side barrier is 0.4". Do not
list a harness as a deliverable if the kit disclaims it.

#### P1-12. The config catalog/API still exposes volume/LED for adapters without those capabilities, contrary to §2

- `docs/socket-plug-v1.md:37` and `:39` — missing `led`/`volume` means "隐藏灯效配置" /
  "隐藏音量 UI".
- `src/rui/config/catalog.py:1269-1295` — the volume field is unconditional for every
  device; `:1301-1335` — the LED fields are unconditional.
- `src/rui/web/api/setup_api.py:960-968` only *defaults* `led.enabled=True` when the
  manifest has `led`; it does not hide the field when absent.
- `src/rui/web/api/app.py:3371-3373` reports capabilities to the client, but there is no
  server-side capability gate on the config catalog fields; if the UI hides them
  client-side, the API still accepts them.

**Impact.** A plug without a mixer or a ring still has a volume/LED form in the generic
config/API; users set values that the adapter cannot apply, which reads as "the plug is
broken". The spec's degradation promise is not met server-side.

**Spec fix.** Either implement capability-driven hiding or change §2's "hide" to
"adapter may ignore; core does not hide in 0.3". See §8 for the code change.

### P2 — naming/doc/validation drift

#### P2-1. Metrics/trace names and counts do not match the spec

- `docs/socket-plug-v1.md:274` lists 8 metrics: `device_wakes`, `device_wake_turns`,
  `device_wake_barges`, `device_wake_debounced`, `device_wake_invalid`,
  `device_wake_suppressed`, `device_wake_fallbacks`, `device_wake_last_score`.
- `src/rui/audio/protocol.py:121-134` has 14: the above plus
  `device_wake_barge_suppressed`, `device_wake_rate_limited`, `device_wake_conflicts`,
  `device_wake_interrupts`, `device_wake_no_vad`, and `device_wake_degraded` (bool).
- `docs/socket-plug-v1.md:191` says `device_wake_conflict` (singular); the field is
  `device_wake_conflicts` (`protocol.py:129`).
- `docs/socket-plug-v1.md:246` says `device_wake_interrupt` (singular); the field is
  `device_wake_interrupts` (`protocol.py:130`).
- `docs/socket-plug-v1.md:244` / `:252` mention `device_wake_barge_suppressed`, but the
  metrics list at `:274` omits it.
- `docs/socket-plug-v1.md:274` says `TurnTrace.meta` writes `device_wake_debounced`; a
  debounced event returns before `_begin_wake_turn` (`src/rui/audio/process.py:886-889`),
  so no `Wake` is produced and `TurnTrace.note_wake` can only record `False` (default at
  `src/rui/audio/protocol.py:228-230`; `src/rui/trace.py:116-118`).
- `device_wake_action` values are `"turn"`, `"barge"`, `"interrupt"` in
  `src/rui/audio/process.py:922-940`, but the spec never enumerates them.
- `wake_source` is only written when `client.last_wake` is present
  (`src/rui/web/api/app.py:1539-1544`); a follow-up turn with no new wake has no
  `wake_source` in the trace, although §5 says the trace carries it.

**Spec fix.** Make the metrics table exhaustive (names, types, semantics, which events
increment), add an `action` enum, and either remove `device_wake_debounced` from trace or
add a separate suppressed-event trace. Add `wake_source` to every turn (default `"rui"`).

#### P2-2. Capability values: spec says "only 8", loader accepts 10

- `docs/socket-plug-v1.md:30` / `:109` — "规范值只有下面 8 个"; `:43` says `native_kws` is
  deprecated and `streaming_asr` is "reserved, no v1 behavior".
- `src/rui/device/contract.py:23-36` — the enum has 10 values, including `native_kws` and
  `streaming_asr`.
- `src/rui/plugins/models.py:353` accepts any `Capability`; `:403-407` rejects only
  `native_kws` in v1. Verified: `streaming_asr` is accepted in v1; `native_kws` and
  `streaming_asr` are accepted in v0.
- The kit (`src/rui/plugins/contract.py:72-89`) distinguishes 8 spec + 2 legacy and warns on
  `streaming_asr`; the loader has no warning.

**Spec fix.** Say "8 normative; `streaming_asr` is accepted but reserved; `native_kws` is
accepted only in v0" and make the loader warn on `streaming_asr`.

#### P2-3. Suppressed/playing events consume the debounce/rate budget

- `src/rui/audio/process.py:886-915` — debounce and rate checks run **before** the
  `playing`/`barge` checks; `_last_accepted_wake_ts` and `_device_wake_accept_times` are
  updated at `:895-896` before `playing` is computed at `:905`.
- `docs/socket-plug-v1.md:245` — playing with `barge=false` is "视为误报忽略"; it should
  not consume the same budget as an accepted wake, otherwise 5 spurious events during
  playback can rate-limit a real wake.

**Spec fix.** State whether suppressed events count; recommended no.

**Code fix.** Move the playing/barge classification before the accepted-event bookkeeping,
or keep a separate suppression budget.

#### P2-4. "Turn open" is detected from `gate.armed` only, not from turn state

- `docs/socket-plug-v1.md:246` — "turn 已打开 → 丢弃当前 gate 缓冲并重新 arm".
- `src/rui/audio/process.py:926` — `action = "interrupt" if self.gate.armed else "turn"`.
  If a turn is in ASR after the gate disarmed, a new `device_wake` is counted as a new turn
  and may not discard the in-flight turn.

**Spec fix.** Define "turn open" explicitly (gate armed? ASR in flight? TTS playing?).

#### P2-5. `drain_wakes()` results are not bounded by the core

- `docs/socket-plug-v1.md:181` — adapter queue is bounded (suggested 32) and overflow drops
  oldest.
- `src/rui/audio/process.py:762-773` — `events = list(pending)` with no length cap; a
  defective adapter can return an unbounded list and block the frame loop while each event
  is validated/sent.

**Spec fix / code fix.** Cap the number of events processed per tick (e.g. 32) and count
the excess; the adapter bound is a SHOULD, core should still defend.

#### P2-6. Deprecation warnings promised by §7 are absent

- `docs/socket-plug-v1.md:367` — v0 removal earliest 0.5, with "至少提前一个 minor 在
  日志/控制台提示".
- `src/rui/plugins/loader.py` has no v0/native_kws/streaming_asr deprecation warning; the
  only v0 handling is the `wake`/`model` rejection. A user upgrading from 0.4 to 0.5 will
  get a hard error without the promised warning.

**Spec fix / code fix.** Emit one structured warning when a v0 device loads (and when
`streaming_asr`/`native_kws` is seen), with the replacement and removal version.

#### P2-7. Documentation contradictions

- `docs/install-wizard-design.md:269` — "manifest 顶层未知字段：忽略（前向兼容）" vs
  `src/rui/plugins/models.py:341` `extra="forbid"` and `docs/socket-plug-v1.md:134`.
- `docs/install-wizard-design.md:266` / `:374` — "v0 缺 choice.options" vs
  `src/rui/plugins/models.py:244-245` (required).
- `docs/install-wizard-design.md:247` — "manifest 可覆盖 listen_port/token_env" vs
  P1-4.
- `docs/install-wizard-design.md:28`, `:252`, `:389` — "0.2 only builtin/trusted; sidecar
  not supported" is still true in 0.3.2, while v1 §3.3 says third-party sidecar; this is
  P1-9, repeated in the wizard doc.
- `docs/install-wizard-design.md:390` — "TLS/自建 CA/每设备端口 永不做或 P3" vs v1 Q2
  per-device port/wss.
- `docs/install-wizard-design.md:376` — "至少有 xiaoai.yaml、mock.yaml、dot2.yaml"; there is
  no `mock` builtin manifest (`src/rui/plugins/builtin.py` only has LX06), and no
  `devices/*.yaml` in `src/rui/device/plugs/` (the directory does not exist).
- `docs/socket-plug.md:40` — "manifest 里写 `source.checksum`（sha256）；可选 `signature`";
  `DeviceSide` (`src/rui/plugins/models.py:137-147`) has neither field and `extra="forbid"`
  rejects them. A v0 author following §3 gets a hard error.
- `docs/socket-plug.md:51` — example `rui_compat: ">=0.1 <0.3"`; 0.3.2 rejects that range
  for a v0 plugin (it excludes 0.3).
- `docs/socket-plug-v1.md:298` — "计划 0.3 P3.0 交付" (P3.0 is done); `:377-387` still
  describes the implementation as "计划", while `:389-416` says it landed.
- `docs/socket-plug-v1.md:88` / `:96` example uses `runtime.mode: sidecar` and
  `listen_port: 4412`; Q2 and the dot2 facts use 8870/wss. An author copying the example
  gets a sidecar that the wizard rejects and the wrong port.

#### P2-8. Fairness / defaults are LX06-shaped

- `src/rui/config/schema.py:175` — `DeviceConfig.adapter` defaults to `"xiaoai"`; a device
  added without an adapter becomes an LX06.
- `src/rui/config/schema.py:212-221` — `audio.capture` defaults to 8 ch / 48 kHz / 6 mic +
  2 ref / S32 with LX06 shifts; a third-party adapter can ignore it (the plane uses the
  frame), but the config UI still presents it as global and an author may treat it as
  authoritative. The plane's own docstring says frames are authoritative
  (`src/rui/audio/process.py:3-7`), which is good but not in the plug spec.
- `src/rui/web/api/device_rules.py:30-35` — `DEFAULT_LISTEN_PORT = 4412` for every inbound
  device; Q2 says 4412 is "not reserved" for LX06, but it is the implicit default.
- `src/rui/plugins/builtin.py:35-70` — only LX06 is builtin; the only `available` plug on
  a fresh install is LX06. The dot2 path needs an operator env var and a self-declared
  `trusted` label (P0-1).
- `docs/socket-plug-v1.md:51` — the named "LX06 exception" is a special case in the
  contract; rule 3 (`led/volume/keys` may be evidenced by `DeviceServices`) is generic and
  already covers it, but naming one vendor in the normative section makes the contract read
  as LX06-specific. Remove the brand and state the generic rule.
- `docs/socket-plug-v1.md:35` vs `:479` — the reference-only `missing_for_barge_in()` is
  the LX06 AEC path; dot2's device-AEC path is reported as missing (P1-3).

#### P2-9. Outbound / mock / multi-device are underspecified or unexercised

- `docs/socket-plug-v1.md:107` — outbound is one line, no example; `examples/` has only
  inbound; `src/rui/device/mock.py` exists but no `mock` manifest, and
  `is_inbound()` treats an unresolved device as inbound
  (`src/rui/web/api/device_rules.py:38-53`), so a mock/outbound device can be counted as a
  second inbound listener in the P3.4 warning.
- `docs/socket-plug-v1.md:103` allows multiple devices per plugin, but
  `src/rui/web/api/setup_api.py:230-260` `_select_plugin`/`_device_manifest` always selects
  `plugin.devices[0]` (or the adapter match); `/api/v1/plugs` summarises the first device
  only (`setup_api.py:140-160`). A multi-model plugin cannot install its second model
  through the wizard.
- `docs/socket-plug-v1.md:477` — Q2 says per-device `listen_port` is supported; the
  config/API do support it, but `AudioPlaneSupervisor._pick_device`
  (`src/rui/audio/client.py:387-393`) picks the first built adapter and only one plane is
  started. The spec does say one plane per device, but "每设备 listen_port 已支持" reads as
  simultaneous binding; clarify "only the active device's port is bound".

#### P2-10. `DeviceWake` source/level validation and `DeviceWakeMsg` observability are fine, but the count semantics are not defined

- `device_wakes` increments for every valid event before conflict/debounce/rate/suppression
  (`src/rui/audio/process.py:876-896`); `device_wake_turns` increments for both `turn` and
  `interrupt` (`:930`). The spec (§5) does not say whether "wakes" means "valid events" or
  "accepted for policy", nor whether an interrupt counts as a turn. Define both; otherwise
  dot2's acceptance numbers (`§6.3`) can be computed two ways.

#### P2-11. `acceptance`/`compatibility`/`params` minor rules

- `docs/socket-plug-v1.md:115` — `acceptance.verified` + `last_verified` + `evidence`;
  `src/rui/plugins/models.py:305-322` accepts `verified: true` with an empty
  `last_verified`; the wizard only shows the badge. Add a cross-field rule if "verified"
  should require a date.
- `src/rui/plugins/models.py:227` — `ParamSpec.default` can be a `list[str]` but the
  `list` branch at `:260-261` only checks `isinstance(self.default, list)`, not that every
  item is a string (pydantic may coerce; add a strict check).
- `src/rui/plugins/models.py:163` — `RuntimeSpec.listen_port` default is 4412; a v1 plug
  that omits it inherits the LX06 port even if it is not an LX06 (P2-8).

#### P2-12. `wake` block / `device_side` examples and error messages

- The error messages for v0 + `wake` say "rui-device/v1 only" but do not offer the
  migration ("remove wake and use KWS, or publish v1"); the v1 `native_kws` error is good.
- `docs/socket-plug-v1.md:124` is a SHOULD; the kit only WARNs
  (`src/rui/plugins/contract.py:1032-1044`), which is correct, but the wizard does not
  surface the warning to the user.

---

## 2. Spec ↔ implementation matrix (question 1)

| Area | Spec | Implementation | Verdict |
|---|---|---|---|
| Capability names | 8 normative; `native_kws` deprecated; `streaming_asr` reserved | enum 10 (`contract.py:23-36`); v1 rejects `native_kws`, accepts `streaming_asr`; v0 accepts both | **Partial / P2-2** |
| DeviceWake fields | `ts_ms/score/threshold/barge/level/source` (`v1:160-165`) | exact (`contract.py:67-86`; `protocol.py:233-249`) | **Match** for the six; **Q5 fields missing** (P0-3) |
| `drain_wakes()` semantics | sync, per-frame, serial, no repeats, bounded, <1ms | per-frame after `drain_signals` (`process.py:588-612`); exceptions bounded (`:736-757`); no core bound on list size (`:762-773`) | **Mostly match; P2-5** |
| Manifest v0/v1 | v1 + v0; v0 no wake/device_wake; v0 no `model` | exact (`models.py:385-401`); v0 + wake rejected | **Match**; contradicts #3 (P0-4) |
| `wake` block rules | dependency table + both-flag rule | dependency table exact; **`wake.barge_via_device` ignored at runtime** | **Gap P1-1** |
| Capability intersection | manifest ∩ probe for mic/reference/playback/device_wake | only `device_wake`/`barge` use it; AEC/playback use probe/frame | **Gap P1-2** |
| `device_wake` enable | manifest + probe + method | exact except no-manifest branch (`process.py:643-683`) | **Inconsistent §7.1** (P1-7) |
| Policy table | §5 rows | mostly exact; `turn open` = `gate.armed`; suppressed events consume budget | **Minor gaps P2-3/P2-4** |
| Metrics/trace | 8 metrics + 6 trace keys | 14 metrics; singular/plural drift; trace debounced impossible | **Gap P2-1** |
| Version negotiation | `rui_compat`; 0.2 rejects v1; 0.3 accepts both; "only gate" | exact for user plugins; `rui_min` second gate; builtins bypass; ranges conflict | **Gap P1-6** |
| Sidecar/trust | third-party → sidecar; trusted → in-process; trust not elevated by wake | loader enforces only manifest; wizard rejects both third-party and sidecar; no runner; hook bypasses manifest | **Gap P0-1/P1-9** |
| `runtime.listen_port/token_env` | manifest can override | validated, not applied to device/connect | **Gap P1-4** |
| Device side / params | scheme/length/B rollback/list.max/choice | none of the device_side rules; no list.max; wizard accepts undeclared | **Gap P1-5** |
| TLS/wss/connect | one free-form `protocol.kind` | fixed `ws(s)://host:port/?token=` | **Gap P1-8** |
| Multi-device | v1 out of scope; 0.4 | one plane, first device, explicit warning | **Match**; Q5 fields missing |
| Playback gap / barrier | §6.2/§6.4 promised | gap counters exist; no harness; no `ordered_start`/epoch | **Gap P1-11** |

---

## 3. Issue #3 / #7 answer consistency (question 2)

### 3.1 #3 — device_wake contract

| #3 answer | v1 spec | Code | Verdict |
|---|---|---|---|
| Q1: accept device wake as first-class in 0.3; `drain_wakes()` not a `VendorSignalMsg.kind` | same (`v1:181-194`) | same | **Consistent** |
| Q1 contract: add `wake` **optionally in `rui-device/v0`** | v1 is `rui-device/v1`; v0 must not declare wake (`v1:459`, `:353`) | v0 + wake rejected (`models.py:385-395`) | **Contradiction (P0-4)** |
| Q2: device gives `barge` as suggestion; RUI decides | same (`v1:252`, `:461`) | `_device_wake_barge_allowed` + `voice.barge_in` (`process.py:837-861`) | **Consistent**, except the manifest flag is ignored (P1-1) |
| Q3: capability in manifest + `DeviceInfo.DEVICE_WAKE`; `extra=forbid` won't reject the new `wake` block | same for v1 (`v1:110`, `:138`); v0 differs | `DeviceInfo` capability + `WakeSpec` (`models.py:98-110`) | **Consistent for v1**; the v0 sentence is wrong |
| Q4: closed LX06 unchanged; open devices can fall back | same (`v1:267`, `:353`) | LX06 probe has no DEVICE_WAKE; KWS unchanged (`xiaoai/adapter.py:121-129`; `process.py:645-663`) | **Consistent** |
| "All device_wake events counted/source in trace/metrics" | `v1:274` | implemented, but names/counts drift (P2-1) | **Partial** |

### 3.2 #7 — Q1…Q7

| #7 answer | Spec | Code | Verdict |
|---|---|---|---|
| Q1: official `RUI_ADAPTER_PLUGINS`/`rui.adapters`; loader does not execute entrypoint; 0.4 formal; downstream image + hook supported; manifest `trust=trusted/local` | §4.4 hook is 0.3; trust is manifest-declared; `local` is not a TrustLevel | hook implemented (`device/loader.py`); loader still doesn't execute `entrypoint`; `TrustLevel` has no `local` (`models.py:68-71`); hook ignores manifest | **Contradiction / security (P0-1)** |
| Q2: per-device `listen_port`; 4412 not reserved; one plane per device; multiple online → warning; arbitration fields `device_id/room/score/threshold/barge/playing/ts_ms` (+last_active) | v1 §10 Q2 says the same; 0.4 arbitration | config/API support port; one plane; warning (`app.py:950-970`); **DeviceWake lacks the fields** (P0-3); `/connect` ignores manifest port (P1-4) | **Partial contradiction** |
| Q3: per-device `token_env`; rotation; RUI token transport, `X-EM-Token` inner; two layers | no formal auth section in v1 | token rotation exists (`app.py:3884-3938`); `_token_env` device-only (P1-4); no adapter auth interface | **Consistent on intent; incomplete contract** |
| Q4: `audio.aec` global; `who=device` means mono/no RUI AEC; `devices[].aec` in config v3; barge via device wake/gating | v1 §10 Q4 says 0.4; `CONFIG_VERSION` is 2 (`schema.py:19`); no `who` field | AEC disabled when `ref_channels<1` (`process.py:457-470`); `missing_for_barge_in` still requires reference (P1-3) | **Consistent as future; contradictory UI signal** |
| Q5: device_wake first-class; adapter reports `device_id/room/score/threshold/barge/playing/ts_ms`; 0.3 single-device | v1 §10 Q5 same | `DeviceWake` has only 6 fields (P0-3) | **Contradiction** |
| Q6: core has per-device volume (0–100) + LED; EQ/limiter/trim in plug `params`; no per-block play-head → coarse stats; `played_ms` optional | v1 §10 Q6 same | volume 0–255 at adapter/services (P0-2); params not typed (P1-5); `played_ms` absent from contract (P1-10) | **Contradiction / incomplete** |
| Q7: contract is v1, tool is `docs/plug-test-kit.md`; multi-device/AEC/config v3 in 0.4 | same | kit exists but disclaims gap harness (P1-11); ranges conflict (P1-6) | **Partial** |

### 3.3 Contradictions between the two answers

1. #3 says `wake` is legal in **v0**; #7 and v1 say v0 is frozen and must not have it.
2. #3's `drain_wakes` fields are only `{ts_ms, score, threshold, barge, level, source}`;
   #7 Q5 adds `device_id/room/playing/last_active`. The v1 `DeviceWake` follows #3, not #7.
3. #3 Q2 says `barge` is a device suggestion; #7 Q5 still asks the adapter to report
   `playing`, which is RUI's own state and not part of the event.
4. #7 Q1 says third-party sidecar; #7 Q4/Q6 rely on `devices[].aec`/config v3; v1 says
   these are 0.4, and the wizard rejects sidecar (P1-9).
5. #7's final line recommends `rui_compat >=0.3 <0.5`; v1 §7 recommends
   `>=0.3.0 <0.4.0`; the reference package says `>=0.2 <0.4`; the builtin says
   `>=0.2.0 <0.3.0` (P1-6).

---

## 4. Contract completeness for a third-party plug author (question 3)

**Can a third-party implement and know everything from the docs alone? No.** The blockers:

1. **No runnable install/trust path** — third-party is rejected by the wizard; sidecar is
   rejected and has no runner; the only path is an operator env var + self-declared
   `trusted` (P0-1/P1-9). The author cannot test the production trust path locally.
2. **Volume** — the adapter boundary unit is wrong/undocumented, `services` is not in the
   spec, `read_volume` is not in the protocol, and the kit does not test them (P0-2).
3. **Adapter surface** — `DeviceInfo` and `AudioFrame` are referenced but not defined;
   `services`/`read_volume`/`health`/play-head fields are undocumented (P1-10).
4. **Lifecycle/error semantics** — `open/close` idempotence is stated; reconnect, timeouts,
   exception classes, and what happens when `open()` fails are not. The plane restarts on
   crash (`src/rui/audio/client.py:376-470`), but an adapter author cannot know whether to
   reconnect inside `frames()` or let the process restart.
5. **TLS/wss/auth** — no connect template, no `wss`/path/header/token-location fields, no
   TLS ownership statement (P1-8). Dot2's `X-EM-Token` + `/control` + `/data` cannot be
   described.
6. **Sidecar** — `runtime.mode: sidecar` is accepted but there is no RPC/health/start/stop
   contract; the spec says "not standardized" and the wizard refuses it (P1-9).
7. **Outbound** — one sentence, no example, no host/auth/TLS guidance; the only shipped
   `mock` is adapter code, not a manifest (P2-9).
8. **Play-head** — `reports_consumption`/`wait_consumed()` are named; the chunk unit,
   `consumed`/`sink_generation` transition, timeout and "what if the device has only
   EOS stats" are not. `played_ms` is only in Q6, not in the contract (P1-10).
9. **Examples/errors** — one v1 manifest example, one fake adapter; no outbound/sidecar/
   volume-only/LED-less example; no error table; the v0 example advertises an
   incompatible `rui_compat` (P1-6).
10. **Capability semantics** — "8 values" vs 10; `streaming_asr` has no behavior; the
    `led/volume/keys` "services evidence" rule is one sentence and not testable (P2-2/
    P1-12).
11. **Multi-device** — explicitly 0.4, but the Q5 fields needed for 0.4 are missing; a
    multi-model plugin only installs its first device through the wizard (P2-9).

**Minimum additions before calling v1 stable:** a normative adapter-surface section
(DeviceInfo/AudioFrame/DeviceServices/play-head), a trust/allowlist story, a sidecar
decision (implement or defer), a connect/TLS block, a volume unit, and an error/lifecycle
table. The current `docs/socket-plug-v1.md` is a good design narrative; it is not yet a
standalone implementer's contract.

---

## 5. Multi-device / AEC / EQ / play-head: scope vs ambiguity (question 4)

### Explicitly out of scope (good)

- Multi-device arbitration, per-device certificates, approve queue, OTA, cloud wake:
  `docs/socket-plug-v1.md:439-442`; #7 Q2/Q5; `docs/install-wizard-design.md:387-390`.
- Per-device AEC and config v3: `docs/socket-plug-v1.md:479`; `CONFIG_VERSION=2`
  (`src/rui/config/schema.py:19`); no `devices[].aec`.
- EQ/loudness service: core only volume/LED (`v1:481`); EQ/limiter/trim in plug params.
- Play-head: optional (`v1:153`, `:481`); no per-block head required.
- Signature/allowlist: `v1:440` says v1 does not do signature enforcement.

### Ambiguous / needs a sentence or a roadmap item

1. **"One audio plane per device" vs "per-device `listen_port` supported"** — the config/API
   supports a port per device, but only the first built adapter is started
   (`src/rui/audio/client.py:387-393`); the other ports are not bound. Add: "only the
   active device's port is bound in 0.3; the others are stored for 0.4."
2. **`who=device`** — there is no `who` field; it is implicit (`ref_channels=0` +
   `audio.aec.enabled=false`). The spec should say how to express it and that
   `missing_for_barge_in()` will still report `reference` missing (P1-3).
3. **Per-device AEC** — `audio.aec.cancel` is global; `AecChain` uses `info.mic_channels`,
   so a second device with a different array shape can run, but only one global
   `filter_len/cancel`. State whether per-device AEC means "config override" or "separate
   AecChain" in 0.4.
4. **EQ/limiter/trim** — "in plug `params`" is not enough: `params` is a form schema, the
   adapter receives a `dict`, and the wizard does not validate types (P1-5). Define which
   keys are reserved, their units, and whether the adapter or the core applies them.
5. **Play-head** — define the chunk index origin, the meaning of `wait_consumed` (already
   in `contract.py:222-224` but not in the spec), the generation reset, and the fallback
   when only EOS stats exist. `played_ms` should either be in the contract as optional or
   removed from Q6.
6. **Room/device identity for 0.4** — `DeviceWake` has no `device_id`/`room`; one plane
   means implicit, but the 0.4 arbitration fields promised in Q5 need a new event/schema
   (P0-3). Add a roadmap line: "0.4 adds `device_id`/`room`/`playing`/`last_active` to the
   wake event; v1 adapters must not send them."
7. **Arbitration policy** — #7 Q5 says "score highest, room/last_active tie-break"; v1
   says "0.4". Put the candidate policy in `docs/ROADMAP.md` and mark it non-normative.
8. **Volume/loudness boundary** — decide whether `devices[].volume` is the only core knob
   (0–100) and whether the adapter exposes a normalized percentage; do not let the HTTP
   API's 0–255 leak into the plug contract (P0-2).

**Roadmap additions (0.4):** per-device audio plane + port binding; `devices[].aec`
(who/reference/channels/same_frame/dealiased/delay); `DeviceWake.device_id/room/playing/
last_active`; arbitration; sidecar RPC; connect/TLS block; capability-driven UI hiding;
gap/barrier harness; v0 removal warning.

---

## 6. Compatibility risk (question 5)

### LX06 / v0

- **Good:** LX06 builtin manifest is v0 (`src/rui/plugins/builtin.py:35-70`), probe has no
  `DEVICE_WAKE` (`src/rui/device/xiaoai/adapter.py:121-129`), no `drain_wakes()`, and
  `_configure_device_wake` returns without touching KWS (`src/rui/audio/process.py:645-654`).
  `wake_count` remains the KWS count (`protocol.py:116-120`). v0 loading works
  (`models.py:343`, `:385-401`).
- **Risks:** the builtin `rui_compat: ">=0.2.0 <0.3.0"` (`builtin.py:115`) is false on
  0.3.2; it is bypassed, so no test catches it. The reference package says `>=0.2 <0.4`
  (`plugins/rui-plugin-xiaoai/plugin.yaml:5`), the spec says `>=0.2 <0.4` (`v1:325`), and
  the example says `>=0.2.0 <0.4.0` with a v1 device (`examples/plugin-example/plugin.yaml:5`).
- `docs/socket-plug.md:40` promises `source.checksum`/`signature`, but the model has no
  such fields (`models.py:137-147`, `extra=forbid`). A v0 author cannot add them.
- v0 removal is planned for 0.5 (`v1:367`) but there is no warning in the loader; a future
  0.5 hard error will surprise users. Add the promised minor-version warning now.
- `native_kws` is accepted in v0, rejected in v1 (`models.py:403-407`); `streaming_asr`
  remains accepted in both. That is consistent with "v0 unchanged", but the spec's
  "8 values" wording is wrong (P2-2).

### v0/v1 deprecation

- The v1 spec says v0 is supported in 0.3.x and removed no earlier than 0.5.0; the loader
  has no deprecation surface. Add a structured warning on v0 load and on `native_kws`
  (v0) / `streaming_asr`; record it in `/api/v1/plugs` as a `deprecated` field.
- The spec says "any field removal: warn, minor, remove"; the `wake`/`model` rejection is
  a v0 *addition* error, which is fine, but the error message should suggest the v1
  migration (P2-12).

### Downstream hook

- The hook itself works and is covered by `tests/test_adapter_plugins.py` (env, entry
  points, fail-soft, health). It is loaded twice (control plane at `app.py:1414`, plane at
  `process.py:2325-2327`), which is deliberate; a plugin with side effects at import time
  runs twice. Document that.
- The hook has **no manifest/trust coupling** (P0-1), no root check (it trusts the
  environment), and no allowlist. The spec's "only root/container operator" is an
  operational assumption, not an enforced control.
- `_parse` uses the first `=`; an adapter name with `=` is impossible by regex, okay. A
  builder with optional/default args is classified as a zero-arg factory
  (`device/loader.py:337-361`) and called at startup; a `build(dev=None, cfg=None)` builder
  would be misclassified. Document "builder must have two required positional args" or
  detect `build` explicitly.

### `rui_compat`

- Three ranges, as above (P1-6). A third-party cannot know which to use; the example and
  builtin both violate the spec's matrix.
- `rui_compat` is not the only gate: `compatibility.rui_min` is enforced in
  `loader.py:204-210`. That is reasonable, but the spec should say "two gates" and define
  precedence (plugin range first, device floor second).
- Builtins bypass `rui_compat`; if the intent is "builtin is always compatible", say so and
  remove the stale value; otherwise bump it to `>=0.3.0 <0.4.0`.

---

## 7. Fairness (question 6)

The core code has no `if xiaoai`/`if dot2` branch, which is good. But the *contract and
defaults* are asymmetric:

1. **Installability.** LX06 is builtin and `available`; a third-party plug is
   `third_party_not_supported`, and a sidecar is `sidecar_not_supported`
   (`setup_api.py:115-123`). The documented escape is to self-declare `trust: trusted`
   (P0-1). That privileges the builtin and makes dot2's path privileged/undocumented.
2. **Barge model.** `missing_for_barge_in()` models only the LX06 reference+playback path;
   dot2's device-AEC + `device_wake` path is reported as "missing reference" (P1-3).
3. **Defaults.** `DeviceConfig.adapter` defaults to `xiaoai` (`schema.py:175`);
   `DEFAULT_LISTEN_PORT` is 4412 (`device_rules.py:30-35`); `audio.capture` defaults to
   the LX06 layout (`schema.py:212-221`); the example/plug docs use `listen_port: 4412`
   (`v1:88`). None blocks dot2, but a fresh install is LX06-shaped and the third-party
   author must know which defaults are authoritative.
4. **Normative text names LX06** in §2.5 ("LX06 例外") and §6.5 (reference plug); the
   generic rule 3 already covers `led/volume/keys` via `DeviceServices`. Remove the brand
   from the normative rule; keep it in an appendix.
5. **`DeviceWake` Q5 fields** are framed around multi-device arbitration (which benefits
   dot2's open-device story) but are missing from the schema (P0-3). The asymmetry is not
   intentional, but it blocks the generic feature.
6. **TLS/wss** is required by dot2 but not expressible; LX06's plain ws is the only
   template (P1-8). A generic inbound transport needs a connect template.
7. **Kit** requires `set_volume`/`set_led` even for devices that do not have them
   (`plugins/contract.py:97-108`), while `NullServices` is the recommended "honest"
   implementation. The kit should accept capability-aware `SKIP` rather than fail; today
   it forces every adapter to expose methods it may not support.

**Recommendation:** make the contract vendor-neutral by (a) a real trust/allowlist path,
(b) capability-driven defaults/UI, (c) a barge-path abstraction, (d) a connect/TLS block,
(e) a second built-in/open example, and (f) removing the LX06-specific default from the
normative spec (keep it in the reference appendix).

---

## 8. Recommended changes

### 8.1 Spec changes (docs/socket-plug-v1.md, docs/socket-plug.md, install-wizard-design.md)

| ID | Change | Addresses |
|---|---|---|
| S1 | Add "Adapter at a glance": full `DeviceInfo`, `AudioFrame` (int16 LE, interleaved, reference trailing, `ts_ms` origin, 20 ms cadence), `DeviceServices` (`shell/read_volume/set_volume/set_led`), `services`, `health`, play-head (`consumed`, `sink_generation`, `wait_consumed`, chunk unit/timeout), `open/close` lifecycle and error policy. | P1-10, P0-2 |
| S2 | Choose one volume unit at the adapter boundary; recommended `DeviceAdapter.set_volume(0–100)` with core conversion; document `DeviceServices` as 0–255 low-level. Add `read_volume` to the adapter surface. | P0-2 |
| S3 | Rewrite §2: "8 normative + `streaming_asr` reserved"; `native_kws` v0-only; effective capabilities = manifest ∩ probe for all five probe-bound capabilities; add a table of what each capability gates in code. | P1-2, P2-2 |
| S4 | Barge: require **both** `wake.barge_via_device` and the capability; define barge paths (`reference+playback` or `device_wake+barge_via_device+playback`); enumerate `action` values; define `missing_for_barge_in` replacement. | P1-1, P1-3, P2-1 |
| S5 | `DeviceWake`/Q5: either add optional `device_id/room/playing/last_active_ms` or retract; state that v1 events do not carry them and 0.4 will. | P0-3 |
| S6 | v0/v1: keep v0 without `wake`; add a v0→v1 migration example; correct issue #3; add a deprecation warning requirement. | P0-4, P2-6 |
| S7 | Trust: say `trust` is metadata, not authorization; add a real allowlist/signature (or an operator-owned dir); define sidecar as 0.4 or implement it; define `RUI_ADAPTER_PLUGINS` as operator-only and not manifest-gated. | P0-1, P1-9 |
| S8 | Version: one matrix (`rui_compat` plugin + `compatibility.rui_min` device); v1 `>=0.3.0 <0.4.0`; v0 `>=0.2.0 <0.4.0`; bump builtin/reference/example; state builtin exception or remove it. | P1-6 |
| S9 | Connect/TLS: add `runtime.connect {scheme, path, token_in, token_name, tls}` or `protocol` keys; state adapter owns TLS; add a wss/outbound example. | P1-8 |
| S10 | `device_side`/`params`: make the rules normative; remove "unknown fields ignored" or make it explicit; define declared-only param submission, types, `options`, `max`; add error examples. | P1-5, P2-7 |
| S11 | Metrics/trace: complete the table (14 fields), fix singular/plural, define `device_wakes`/`device_wake_turns`, add action enum, define suppressed-event trace or drop `device_wake_debounced`. | P2-1, P2-10 |
| S12 | Harness: either deliver gap/boundary and `Ready.ordered_start`/epoch barrier, or remove §6.2/§6.4 claims and state adapter-side workaround is required. | P1-11 |
| S13 | Capability-driven UI: state "hide" only if implemented; otherwise "core does not hide in 0.3". | P1-12 |
| S14 | Fairness: remove the brand from the normative exception; add an outbound example and a second open-device example; make defaults neutral or explicit. | P2-8, P2-9 |
| S15 | Clarify multi-device: only the active device's port binds in 0.3; multi-model plugin selection; 0.4 arbitration event. | P2-9, §5 |
| S16 | `source.checksum`/`signature`: add the fields or remove the v0 claim; align the v0 example with `rui_compat >=0.2 <0.4`. | P2-7 |

### 8.2 Code changes (recommended, not made)

| ID | File / change | Addresses |
|---|---|---|
| C1 | `src/rui/plugins/models.py`: add `DeviceSide` validators (http(s), no userinfo, ≤20/≤500, B rollback); enforce `list.max`; cross-field `acceptance.verified`/`last_verified`; make `trust` metadata (or remove). | P1-5, P0-1 |
| C2 | `src/rui/plugins/loader.py`: add a trust allowlist/signature check; emit v0/native_kws/streaming_asr deprecation warnings; apply `runtime.listen_port`/`token_env` defaults at device creation or remove them from the manifest. | P0-1, P1-4, P2-6 |
| C3 | `src/rui/device/loader.py`: do not register a name whose resolved manifest is `third-party` without an explicit operator allowlist; document the hook as operator-only. | P0-1 |
| C4 | `src/rui/audio/process.py`: compute effective capabilities (manifest ∩ probe); gate AEC/reference/mic_array/playback; AND `wake.barge_via_device`; move suppression before debounce/rate; cap `drain_wakes` events; use `adapter.set_volume` or document `services`. | P1-1, P1-2, P0-2, P2-3, P2-5 |
| C5 | `src/rui/web/api/app.py` / `setup_api.py`: use a single volume unit; `_device_ws_url`/`_token_env` consult manifest runtime/connect; validate params against `ParamSpec` (declared/type/options/max); hide volume/LED when capability absent; allow multi-model selection or document first-only. | P0-2, P1-4, P1-5, P1-8, P1-12, P2-9 |
| C6 | `src/rui/device/contract.py`: add optional `DeviceWake` fields (if S5a); add `read_volume`/`services` to the adapter protocol; define barge paths. | P0-3, P0-2, P1-3 |
| C7 | `src/rui/audio/protocol.py`: add `DeviceWakeMsg` fields; add `Ready.ordered_start`/epoch if S12; complete metrics names. | P0-3, P1-11, P2-1 |
| C8 | `src/rui/plugins/contract.py`: kit check `services`/`read_volume`; check both barge flags; implement gap/boundary harness or remove from docs; accept capability-aware `SKIP` for volume/LED. | P0-2, P1-1, P1-11, P2-8 |
| C9 | `src/rui/plugins/builtin.py`: bump `rui_compat` to `>=0.3.0 <0.4.0` (or match reference); add the missing mock manifest if the wizard doc keeps promising it. | P1-6, P2-9 |
| C10 | `examples/plugin-example/plugin.yaml`: set `rui_compat: ">=0.3.0 <0.4.0"` and `rui_min: "0.3.0"`; avoid advertising 0.2 for a v1 manifest. | P1-6 |
| C11 | Tests: add a v0+wake rejection test (already exists), a `device_side` invalid-source/B-rollback test, a list.max test, a volume unit test at both boundaries, a barge-flag-off test, a manifest-absent device_wake test, and a `DeviceWake` extra-field/Q5 test. | all |

---

## 9. Draft GitHub issue comments (text only — **not posted**)

### 9.1 Draft for issue #3

> ## 契约更正（0.3.2 审计，先不改代码）
>
> 我们回看了 #3 里 2026-10-24 的答复，发现有一处需要更正，避免 dot2 按旧答复实现后被 0.3 loader 拒绝：
>
> 1. **`wake` 块只能在 `rui-device/v1`，不能在 v0。** 原答复说
>    "`rui-device/v0` 里 optional、`extra=forbid` 只拒绝未知 key、加这个块是合法的"。
>    冻结后的 v1（§9 Q1，`docs/socket-plug-v1.md:459`）和 0.3.2 的实现
>    （`src/rui/plugins/models.py:385-401`）是：**v0 声明 `wake`（哪怕
>    `wake: {source: rui}`）、`model`、`device_wake` 或 `barge_via_device` 都直接报错**。
>    请按 v1 manifest 发布，或继续用 v0 但不带 `wake`（走 RUI KWS）。这条是文档/答复错误，
>    不是实现要放宽；`extra=forbid` 的严格性是我们有意保留的。
>
> 2. **`DeviceWake` 字段仍按 #3 的六项**：`ts_ms, score, threshold, barge, level,
>    source`（`src/rui/device/contract.py:67-86`；`DeviceWakeMsg` 相同，
>    `src/rui/audio/protocol.py:233-249`）。#7 Q5 里说的
>    `device_id/room/playing/last_active` **不在这个事件里**，请不要往 `DeviceWake`
>    里塞（dataclass 会 `TypeError`，`DeviceWakeMsg` 会静默丢字段）。多设备仲裁字段是 0.4
>    的事；v1 先按单设备上报，`device_id` 由当前音频面隐式确定。
>
> 3. 其余口径不变：`wake.source: device` 需要 `device_wake` 能力 + `drain_wakes()`；
>    barge 只是建议，RUI 按 `voice.barge_in`/播放状态/限频最终判定；
>    `wake.barge_via_device` 与 `barge_via_device` 能力**两者都真**才启用（当前实现只看了
>    probe 能力，这个我们会在代码侧修正，见审计 P1-1）。
>
> 给 dot2 的 manifest 建议：`schema: rui-device/v1`，
> `capabilities: [..., device_wake, barge_via_device]`，
> `wake: {source: device, barge_via_device: true}`，`rui_compat: ">=0.3.0 <0.4.0"`。

### 9.2 Draft for issue #7

> ## 契约更正（0.3.2 审计，Q1/Q2/Q5/Q6 需要修正；Q3/Q4/Q7 基本不变）
>
> 先说明：下面不是新增特殊分支，而是把 v1 文档/实现已经存在的差异说清楚，避免 dot2 按旧答复返工。
>
> ### Q1 — trust/入口
> - `trust` 是 `plugin.yaml` 里**自述**的字段（`src/rui/plugins/models.py:471`），
>   0.3.2 没有签名/allowlist；`trust: trusted` 不能当作安全边界。
> - `RUI_ADAPTER_PLUGINS` / `rui.adapters` 的钩子**只导入运维方指定的模块**，
>   不读 `plugin.yaml`，因此它不会因为 manifest 写 `third-party` 就拒绝；反过来，
>   manifest 写 `trusted` 也不是“审核通过”。这条路径的正确说法是：
>   **downstream 镜像 + 运维方 env + 运维方审计**，不是“manifest 声明 trusted 即可”。
> - 0.3.2 的向导对 `third-party` 返回 `third_party_not_supported`，对
>   `runtime.mode: sidecar` 返回 `sidecar_not_supported`（`src/rui/web/api/setup_api.py:115-123`）；
>   侧车 runner/RPC 尚未实现。因此 0.3.x 实际可跑的是 **in-process/trusted**
>   （downstream 镜像 + env 钩子）；sidecar 是 0.4 的方向，不是现在可交付的路径。
> - 之前写的 `trust=trusted/local` 里 `local` 不是合法值（只有
>   `builtin/trusted/third-party`，`src/rui/plugins/models.py:68-71`）。
>
> ### Q2 — 多设备/端口
> - `devices[].listen_port` 在 config/API 层支持，但 0.3 只启动一个音频面，
>   只有**当前设备**的端口会 bind；其它设备是“已配置、未驱动”并显式 warning
>   （`src/rui/web/api/app.py:950-970`）。请按单设备跑通，0.4 再谈多平面。
> - `4412` 不是保留给 LX06，但它是所有 inbound 设备的默认值
>   （`src/rui/web/api/device_rules.py:30-35`）；dot2 请在 `devices[].listen_port` 显式写 8870。
> - **`/connect` 目前只生成 `ws(s)://host:port/?token=`**，不会因为 manifest 的
>   `runtime.listen_port`/`protocol` 改变（`src/rui/web/api/app.py:3836-3839`）；
>   `wss` 只在控制台本身是 https/wss 时生成。dot2 的 TLS/`X-EM-Token`/`/control`+
>   `/data` 需要 adapter 自己拥有，0.3 的 `connect` 步骤不能替你生成；我们会在 v1
>   补 connect/TLS 契约（审计 P1-8）。
>
> ### Q5 — device_wake 多设备
> - v1 的 `DeviceWake` 只有六项：`ts_ms/score/threshold/barge/level/source`
>   （`src/rui/device/contract.py:67-86`；`DeviceWakeMsg` 相同）。**没有**
>   `device_id/room/playing/last_active`。请不要按 Q5 旧答复去加这些字段：
>   dataclass 会 `TypeError`，`DeviceWakeMsg` 会静默丢弃。
> - 0.3 单设备语义完整；0.4 的仲裁如果要这些字段，我们会以**新增可选字段**的方式做，
>   到时会提前一个 minor 通知。
>
> ### Q6 — 响度/EQ/播放统计
> - **音量单位需要更正**：v1 文档写的是“RUI 0–100，adapter 映射”，但 0.3.2 实际在
>   adapter/`services` 边界传的是 **0–255**（`src/rui/audio/process.py:2075-2083`、
>   `:2207`；`DeviceServices.read_volume` 文档也是 0–255，`src/rui/device/contract.py:135-137`）。
>   目前 `DeviceAdapter.set_volume` 实际上没有被核心调用，核心调用的是
>   `adapter.services.set_volume`（`process.py:2075`/`:2203`），而 `services` 不在
>   v1 §4.1 的 adapter 表里。这是文档/实现不一致，我们会在 v1.1 统一（审计 P0-2）。
>   在修正前，dot2 请按 **0–255** 实现 `services.set_volume`/`read_volume`，
>   或同时实现两者并兼容 0–255。
> - EQ/limiter/trim 仍放 plug `params`、adapter 自己执行；核心只做每设备 volume + LED。
> - play-head：`played_ms` 不是 v1 契约字段（只在 Q6 出现）；没有 per-block 播放头的
>   设备按 EOS/固定间隔回报粗粒度统计即可，Pacer 会容忍。
>
> ### Q3/Q4/Q7
> - Q3 不变：每设备 `token_env` + 轮换是 0.3 已实现的（`src/rui/web/api/app.py:3884-3938`），
>   RUI token 是传输层准入，`X-EM-Token` 由 adapter 内层校验，两层分离。
> - Q4 不变：`audio.aec` 仍全局，`who=device` 是“adapter 只给 mono、RUI 不跑 AEC”的
>   语义（通过 `ref_channels=0` + AEC 关闭表达），`devices[].aec`/config v3 在 0.4。
>   注意 `missing_for_barge_in()` 目前只认 `reference+playback`，会把 dot2 误报成
>   “缺 reference”，我们会在 v1.1 改成多路径（审计 P1-3）。
> - Q7 文档：契约仍是 `docs/socket-plug-v1.md`，自测 `docs/plug-test-kit.md`；
>   `rui_compat` 建议统一为 `>=0.3.0 <0.4.0`（此前 `<0.5` 的说法以 v1 为准，
>   因为 0.4 会改 AEC/config v3）。gap harness 尚未交付（kit 明确不测），
>   §6.4 的 ordered-start 屏障也还在设计；dot2 暂时保留自己的 flush+pong/预缓冲，
>   等 0.4 契约落地再评估去掉。

---

## 10. Verification notes (read-only)

- `git status --short` before writing: only `?? docs/console-ia-audit.md` (another
  workstream). This audit adds `docs/socket-plug-audit.md`; no source or docs were modified.
- Issue #1/#3/#7 and their comments were fetched with the GitHub REST API using the token
  in `~/.git-credentials` (token value never printed). `gh` is not installed.
- Python checks used `.venv/bin/python` (3.13), not the system 3.9:
  - `DeviceSide` accepts `file:///etc/passwd`, `https://user:pass@…`, 21 install steps and
    an empty B-class `rollback`.
  - `ParamSpec` accepts a 4-item default with `max: 3`.
  - v1 accepts `streaming_asr`; v0 accepts `native_kws`/`streaming_asr`; v0 rejects
    `device_wake`.
  - `DeviceWake(..., device_id=…)` raises `TypeError`; `DeviceWakeMsg(..., device_id=…)`
    silently drops it.
- This is a contract audit, not a runtime acceptance run; no speaker, NAS or Docker was
  touched.
