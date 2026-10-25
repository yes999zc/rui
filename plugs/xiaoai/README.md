# plugs/xiaoai — Xiaomi AI Speaker Pro (LX06) plug

The LX06 support that used to live in `src/rui/device/xiaoai/` is a Socket & Plug
package since 0.6 (docs/roadmap-0.6.md M2). It has the same shape as the Dot 2
downstream plug: a `plugin.yaml`, a `rui-device/v1` manifest in `devices/`, and a
Python adapter package next to them.

| File | Role |
|---|---|
| `plugin.yaml` | `rui-plugin/v0` catalogue entry (id `xiaoai-lx06`, trust: builtin) |
| `devices/lx06.yaml` | `rui-device/v1` manifest, id `lx06`, declares the 8 ch / 48 kHz `audio_defaults` |
| `xiaoai_plug/` | the adapter (adapter, wire protocol, LED ring, mixer services) |

## How it loads

The image copies this directory to `/opt/rui-plugins/xiaoai` and sets
`PYTHONPATH=/opt/rui-plugins/xiaoai`. The core scans the image plug directory
(`RUI_PLUGINS_DIR`, default `/opt/rui-plugins`) through the normal manifest loader, so
the plug appears in `GET /api/v1/plugs` and the wizard without a copy under
`<data_dir>/plugs`. The adapter is registered from the manifest's
`runtime.entrypoint`; no core table mentions the device.

Existing installs need no migration: configs that say `adapter: xiaoai` keep working
for one minor through the alias in `src/rui/adapter_aliases.py`. New configs should
say `adapter: lx06` (the manifest's canonical device id).

## Verify without a speaker

```bash
python -m rui plug-test plugs/xiaoai --adapter xiaoai_plug.adapter:build --json
```

`device.open` / `capture.*` / `playback.sink` / `wake.inject` are SKIP because the
LX06 is an inbound device: it works once the open-xiaoai client dials in.
