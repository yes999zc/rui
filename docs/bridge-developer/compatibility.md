# Compatibility windows and the `trust` boundary

Two questions decide whether a plug loads:

1. **Version.** Does the running core satisfy the plug's `rui_compat`, and does it
   also satisfy the device manifest's `compatibility.rui_min`?
2. **Trust.** Is the plug allowed to run its code in the rui process at all?

They are independent. A version-compatible plug that asks to run in-process
with the wrong trust level is still refused.

---

## 1. What is compared, and by which code

`plugin.yaml` has exactly one plugin-level version gate: `rui_compat`
([`src/rui/plugins/models.py:549-589`](../../src/rui/plugins/models.py#L549-L589)).
The loader reads the running version from `rui.__version__`
([`src/rui/__init__.py:1`](../../src/rui/__init__.py#L1)), parses it, and matches
the range; any mismatch refuses the whole plug
([`src/rui/plugins/loader.py:53-80`](../../src/rui/plugins/loader.py#L53-L80)). The refusal
message looks like:

```text
<plugin.yaml>: plugin 'dot2-echomuse' is incompatible: it requires RUI >=0.8.1 <0.9.0, but this build is 0.7.9; refusing to load it
```

The semver implementation is intentionally tiny and has no dependencies
([`src/rui/plugins/semver.py:1-20`](../../src/rui/plugins/semver.py#L1-L20)):

- `x.y.z`; partial versions are allowed **inside a range** (`0.8` = `0.8.0`), but
  a plug's own `version` must be full semver
  ([`src/rui/plugins/models.py:573-580`](../../src/rui/plugins/models.py#L573-L580)).
- Comparators: `>=`, `<=`, `>`, `<`, `==`/`=`, `!=`, separated by spaces or
  commas; `*`/`x` are accepted. There is **no OR** and no `^`/`~` — those are
  errors, not guesses ([`src/rui/plugins/semver.py:192-217`](../../src/rui/plugins/semver.py#L192-L217)).
- A range is a conjunction: every comparator must match
  ([`src/rui/plugins/semver.py:165-173`](../../src/rui/plugins/semver.py#L165-L173)).

A **second, device-level floor** is `compatibility.rui_min` on each
`devices/*.yaml`. It must also hold, or the loader refuses that device
([`src/rui/plugins/models.py:330-357`](../../src/rui/plugins/models.py#L330-L357),
[`src/rui/plugins/loader.py:205-218`](../../src/rui/plugins/loader.py#L205-L218)).
`tested_with` and `notes` are validated and displayed but are **not** runtime
gates — they are evidence you provide.

| Check | Field | Enforced by | Failure |
| --- | --- | --- | --- |
| Plugin window | `plugin.yaml.rui_compat` | `load_plugin()` | plug refused (`PluginError`); discovered plugs become scan errors |
| Device floor | `devices/<model>.yaml.compatibility.rui_min` | loader, per device | device refused |
| Mode/trust consistency | `trust`, `runtime.mode` | loader | plug/device refused |
| Capability/wake consistency | `capabilities`, `wake` | manifest validators | manifest refused |

Capabilities are **not** version-guessed at runtime: they are resolved from the
manifest and `probe()` ([`src/rui/device/contract.py:47-97`](../../src/rui/device/contract.py#L47-L97)).

---

## 2. The 0.8.x policy: lock the minor, not the patch

For the 0.8.x line, a plug should accept **the whole minor**:

```yaml
rui_compat: ">=0.8.1 <0.9.0"
```

- Lower bound: the first core release in the minor line you support and have
  verified end to end (for the 0.8.x line used by this documentation set, 0.8.1).
- Upper bound: the next minor (`0.9.0`), which is not contracted yet. This is
  the "dual window" — never write `<0.10.0` or an open-ended `>=0.8.1`; both
  claim a future you have not tested.
- `<0.9.0` deliberately accepts future 0.8.x patch releases. That is a promise
  from the plug author that the 0.8.x contract stays compatible at patch level,
  not a runtime inference: the loader only does range arithmetic.

This is the window used by the reference bridges. `rui-dot2-bridge` at plugin
version **1.3.4** and **1.3.5** both declare `rui_compat: ">=0.8.1 <0.9.0"`;
1.3.5 is a behaviour-neutral patch (counters + docs) and the window did not
change. Its plugin manifest documents the reasoning verbatim: `rui <= 0.8.0`
validates `AudioDefaults` with `extra="forbid"` and has no `voice` field, so a
model manifest declaring `audio_defaults.voice` would be rejected there; 0.8.1 is
the first release that parses it, and 0.9.0 is not yet contracted.

The same rule is stated in the contract docs: any field removal warns first,
then waits a minor, then removes — never in a patch
([`docs/socket-plug-v1.md:491`](../socket-plug-v1.md#L491)); declaring
`audio_defaults.voice` requires the `rui_compat` floor to be raised to `0.8.1`
([`docs/socket-plug-v1.md:132`](../socket-plug-v1.md#L132)).

### Choosing the floor

| Your plug declares | Safe lower bound | Reason |
| --- | --- | --- |
| only v1 features that exist since 0.3 (`capabilities`, `wake`, `runtime`, …) | `0.3.0` (or whatever you actually tested) | no newer manifest keys |
| `audio_defaults` (0.6+) | `0.6.0` | older loaders reject the key |
| `audio_defaults.voice` (0.8.1+) | `0.8.1` | 0.8.0/0.7.x `AudioDefaults` is `extra="forbid"` and would reject the manifest |
| current contract-kit/quickstart line (this documentation) | `0.8.1` | the window this docs set and the template target |

Do not lower the floor to "make it install on an old core": the manifest will
either be rejected or, worse, load and behave incorrectly. Publishing no
manifest for old cores is the honest option.

### Choosing the ceiling

Use the next minor after the last one you tested. A patch release inside it is
covered by the 0.8.x compatibility promise; a new minor may change manifest
fields or adapter semantics and needs a new release of the plug.

---

## 3. Trust: `trusted` is a self-assertion, not a sandbox

`trust` is a manifest field with three legal values
([`src/rui/plugins/models.py:70-74`](../../src/rui/plugins/models.py#L70-L74)):

| Value | Meaning | May run `runtime.mode: in-process`? |
| --- | --- | --- |
| `builtin` | shipped by the image/rui maintainers | yes |
| `trusted` | operator-reviewed, runs in the rui process | yes |
| `third-party` | anyone else | **no** — rejected for in-process |

The code is explicit that trust is metadata, not authorization: it is "the
manifest's self-asserted metadata, not an authorization boundary" — the
mechanical half is that no manifest (or a third-party one) refuses registration
([`src/rui/device/loader.py:242-289`](../../src/rui/device/loader.py#L242-L289),
[`src/rui/plugins/loader.py:1-15`](../../src/rui/plugins/loader.py#L1-L15)).
`docs/socket-plug-v1.md` §4.4 says the same: real authorization is "the operator
audited the code and put it in the image/directory".

**In-process means full privilege.** A `trust: trusted` adapter is imported into
the rui process and can do anything that process can: read config/secrets, open
network connections, spawn work, modify files. There is no sandbox. The contract
kit has the same model — it imports `--adapter` in your process and is explicitly
"not a sandbox"
([`src/rui/plugins/contract.py:1-30`](../../src/rui/plugins/contract.py#L1-L30),
[`docs/socket-plug-v1.md:395`](../socket-plug-v1.md#L395)).

Consequences for a bridge author:

- Use `trust: trusted` only for code you wrote and reviewed, or code you would be
  comfortable giving the rui process's credentials.
- Do not load untrusted code at runtime, do not evaluate configuration/network
  input as code, and keep dependencies minimal. The loader's own security posture
  is "manifest is data, not code"; adapters are the one place code runs
  ([`src/rui/plugins/loader.py:1-15`](../../src/rui/plugins/loader.py#L1-L15)).
- `third-party` + `in-process` is refused at manifest validation
  ([`src/rui/plugins/loader.py:231-236`](../../src/rui/plugins/loader.py#L231-L236));
  the 0.8.x line has no sidecar runner, so a third-party adapter is reported as
  not implemented rather than silently run
  ([`src/rui/device/registry.py:286-312`](../../src/rui/device/registry.py#L286-L308)).
- The env/entry-point channels are operator-only (`RUI_ADAPTER_PLUGINS`,
  `rui.adapters`). Never build these from console input, device reports or
  `devices[].params` ([`docs/socket-plug-v1.md` §4.4](../socket-plug-v1.md#L274)).

---

## 4. Upgrade checklist

1. Read the core release notes for manifest-contract changes before widening the
   window.
2. Test on the new core: `rui plug-test <plug> --adapter <module>:build`, then a
   real capture + playback on the device.
3. Bump the plugin `version`; adjust `rui_compat` only for a tested range.
4. Update `compatibility.tested_with` (evidence) if the device manifest keeps it.
5. Keep the previous release installable: versioned plug directory + env switch
   (see [`install-paths.md`](install-paths.md)). The config does not need to
   change for a rollback.
