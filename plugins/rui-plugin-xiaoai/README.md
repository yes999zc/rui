# rui-plugin-xiaoai — reference Socket & Plug package

This directory is the **reference plug** for the Socket & Plug v1 contract
([`docs/socket-plug-v1.md`](../../docs/socket-plug-v1.md) §3, §4, §6). It describes
the Xiaomi AI Speaker Pro (LX06) exactly as the in-code builtin does, and adds a
thin adapter factory. It exists to prove that a plug can be understood, loaded and
contract-tested without adding a brand branch to RUI core.

It is **not** the runtime default in 0.2.x/0.3.x: a fresh install still uses the
in-code manifest in `src/rui/plugins/builtin.py` and the adapter in
`src/rui/device/xiaoai/`. That fallback stays until the 0.4 extraction.

## Layout

| File | Purpose |
|---|---|
| `plugin.yaml` | `rui-plugin/v0`, id `xiaoai-lx06`, trust `builtin`, `rui_compat: ">=0.2 <0.4"` |
| `devices/lx06.yaml` | `rui-device/v0`, class A, inbound, `ws`, in-process; capabilities `mic_array`, `reference`, `playback`, `led`, `keys`, `volume`; params `host`, `room`, `wake_words` |
| `rui_plugin_xiaoai/adapter.py` | `build(spec, config)` → delegates to `rui.device.xiaoai.build` |
| `README.md` | this file |

The manifest is deliberately **v0**: the LX06 wakes through RUI's own KWS
(`wake.source: rui` by the v0 default). It must not declare the v1 `wake` block or
`device_wake`/`barge_via_device` — see `docs/socket-plug-v1.md` §7.

## Run the contract kit

From the repository root:

```bash
# Manifest only
.venv/bin/python -m rui plug-test plugins/rui-plugin-xiaoai

# Manifest + adapter surface (no speaker, ALSA or network required)
.venv/bin/python -m rui plug-test plugins/rui-plugin-xiaoai \
  --adapter rui_plugin_xiaoai.adapter:build

# Machine-readable evidence
.venv/bin/python -m rui plug-test plugins/rui-plugin-xiaoai \
  --adapter rui_plugin_xiaoai.adapter:build --json > contract-report.json
```

Expected result: exit code `0`, no `FAIL`. `device.open`, `capture.frames`,
`capture.cadence`, `playback.sink` and `wake.inject` are **SKIP**, not failures:

- the LX06 is inbound, so `open()`/`frames()`/`play()` cannot run until the
  open-xiaoai client dials in. The adapter advertises the kit-only hint
  `kit_live_device_required` (documented in
  [`docs/plug-test-kit.md`](../../docs/plug-test-kit.md)), so the kit checks
  `probe()`, the method surface, both drains and idempotent `close()` and skips
  the live-I/O checks;
- `wake.inject` is skipped because the v0 manifest keeps RUI KWS; there is no
  device wake channel to inject into.

`probe()` itself needs no host: it reports the configured LX06 layout (8 channels
at 48 kHz, 6 mic + 2 reference, 16-bit) exactly as the core adapter does.

## What stays in core

- `src/rui/plugins/builtin.py` — default manifest for a fresh install (same plugin
  id; the builtin wins if a user also drops this package into `data/plugs/`).
- `src/rui/device/xiaoai/` — the only LX06 implementation; this package imports it.
- `Capability`, `DeviceInfo`, `DeviceAdapter`, `drain_signals()`/`drain_wakes()` —
  core contracts; the package adds no new dependency and no policy.

0.4 plan: move `src/rui/device/xiaoai/` into this package (or a standalone
distribution), make the on-disk manifest canonical and keep only a compatibility
shim in core. The v0 schema itself is not removed before 0.5; see
`docs/socket-plug-v1.md` §7.
