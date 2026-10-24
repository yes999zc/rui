# Example plug (reference fixture for `rui plug-test`)

A valid `rui-device/v1` plugin with a fake adapter. It is used by
`tests/test_plug_contract.py` and is safe to run on a laptop: no speaker, ALSA,
network or audio device is touched.

```bash
# manifest only
python -m rui plug-test examples/plugin-example

# manifest + in-process adapter smoke test
python -m rui plug-test examples/plugin-example \
  --adapter examples/plugin-example/rui_plugin_example.py:build

# machine-readable acceptance evidence
python -m rui plug-test examples/plugin-example \
  --adapter examples/plugin-example/rui_plugin_example.py:build --json \
  > contract-report.json
```

The adapter is a 16 kHz mono fake speaker: `probe()` reports the manifest's
capabilities, `frames()` yields 20 ms silence, `play()` writes to an in-memory
sink, and `drain_wakes()` drains a bounded queue. `inject_wake()` is a kit-only
test hook; a real adapter would feed `drain_wakes()` from its device-side wake
engine instead.

See [`docs/plug-test-kit.md`](../../docs/plug-test-kit.md) for the check list,
JSON schema, CI snippet and limitations.
