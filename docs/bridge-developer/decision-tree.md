# Decision tree: reuse the EchoMuse bridge or write an adapter?

Two routes connect a speaker to rui. Pick one **before** writing code; the
wrong one costs a week.

```text
Does a bridge already speak this device's protocol?
├─ yes → mount that bridge. Do not write a new adapter.
└─ no → Can the device run Linux + a Go controller (EchoMuse-class hardware)?
         ├─ yes → EchoMuse route: reuse rui-dot2-bridge (no adapter code)
         └─ no  → adapter route: write one DeviceAdapter + manifest
```

---

## Route 1 — EchoMuse route (no adapter code)

**Use when** the device is an Echo Dot 2 (RS03QR) that you can unlock and flash
with EchoMuse firmware, i.e. it can run Linux and the EchoMuse Go controller.
The controller owns the device's microphone array, hardware AEC, speaker and LED;
rui only decides turns and playback. `rui-dot2-bridge` is the finished plug for
exactly this combination, so the work is installation, not code.

### Prerequisites

| # | Requirement | Why |
| --- | --- | --- |
| 1 | Echo Dot 2 / RS03QR, unlocked, EchoMuse firmware installed | The bridge talks to the EchoMuse controller; it does not flash or unlock anything. |
| 2 | Device and rui on the same LAN, device able to reach the rui host on the bridge's listen port | The controller dials out to the bridge (inbound transport). |
| 3 | rui core >= 0.8.1, < 0.9.0 | `rui_compat: ">=0.8.1 <0.9.0"` in the bridge manifest; the loader refuses anything outside the window ([`src/rui/plugins/loader.py:74-80`](../../src/rui/plugins/loader.py#L74-L80)). |
| 4 | A device token (EchoMuse controller token) and the per-device host/port values | The bridge authenticates the device; values are yours, never committed. |
| 5 | Optional: TLS material if the device's `tls_port` is enabled | The device's trusted CA decides whether a self-signed certificate is accepted. |

Python 3.11+ is already inside the core image. The bridge repository's README
(`rui-dot2-bridge`) carries the current device-side onboarding steps and the
`scripts/onboard_device.py` helper (backup → write → byte-for-byte read-back →
restart → wait for `connected`).

### Install (external plug, no downstream image)

Mount the bridge package as the plug and register it explicitly:

```bash
# On the rui host; paths are examples.
mkdir -p /vol1/rui/plugins
cp -r rui-dot2-bridge/src/rui_dot2_bridge /vol1/rui/plugins/

export PYTHONPATH=/vol1/rui/plugins
export RUI_ADAPTER_PLUGINS="dot2=rui_dot2_bridge.adapter:build"
# Device token lives only in the environment / rui's secret store:
read -rs DOT2_EM_TOKEN && export DOT2_EM_TOKEN
rui serve        # or the container entrypoint
```

The two channels are independent and both are required:

- `RUI_ADAPTER_PLUGINS` executes the adapter in process
  ([`src/rui/device/loader.py:1-28`](../../src/rui/device/loader.py#L1-L28));
- the manifest next to the module (`src/rui_dot2_bridge/plugin.yaml`) gates it:
  `trust` must be `builtin` or `trusted`, and the manifest must resolve for the
  registered name ([`src/rui/device/loader.py:242-289`](../../src/rui/device/loader.py#L242-L289)).

If you instead copy the package under `/opt/rui-plugins/<plug>/`, the image
catalog picks it up as an operator root; `RUI_PLUGINS_DIR` is the same mechanism
outside the image ([`src/rui/plugins/builtin.py:43-75`](../../src/rui/plugins/builtin.py#L43-L75)).
See [`install-paths.md`](install-paths.md) for the trade-offs.

Run the verification commands below **in the same shell** (or `export
PYTHONPATH=/vol1/rui/plugins` again first): `rui plug-test` imports the bridge
package, and a new shell without that `PYTHONPATH` will not find it.

### Verify

1. Manifest + adapter contract:

   ```bash
   rui plug-test /vol1/rui/plugins/rui_dot2_bridge \
     --adapter rui_dot2_bridge.adapter:build
   ```

   Expect `summary: pass=… fail=0 … -> PASS`.

2. rui sees the adapter and the room:

   ```bash
   curl -s http://127.0.0.1:8080/health | python3 -m json.tool
   ```

   Expect `adapter_plugins.registered` to include `dot2`, `audio_plane: true`,
   `device_errors: 0`, and `planes[].state: "running"` for the dot2 room.

3. Device detail reports the link:

   ```bash
   curl -s http://127.0.0.1:8080/api/v2/devices/<device-id> | python3 -m json.tool
   ```

   Expect `status: "ready"`, `ready: true`. `audio_config_mismatch` means the
   effective playback wire disagrees with the model manifest — see
   [`debugging.md`](debugging.md).

4. Speak the wake word and check one full turn (wake → ASR → reply audio). The
   bridge's own README lists the device-side checks for audio/EQ.

### Roll back

The device's stock firmware and the vendor controller are not modified by rui.
Rollback is host-side: stop rui, remove the plug directory / the
`RUI_ADAPTER_PLUGINS` entry, and restart with the previous version. Restore the
device's own controller configuration from the backup the onboarding script
made. Keeping two versioned plug directories and switching between them is the
safest form (see [`install-paths.md`](install-paths.md)).

---

## Route 2 — write an adapter

**Use when** the device does not run EchoMuse (or a compatible controller), or
no existing bridge speaks its protocol. You implement the device contract and
ship a manifest; the core stays unchanged.

### Prerequisites

| # | Requirement | Why |
| --- | --- | --- |
| 1 | A way to move audio to/from the device: a local SDK, a network protocol, a client you can install, ALSA, or a bridge process | The adapter is the only device-specific code rui loads. |
| 2 | Python 3.11+ in the rui environment and a directory to develop in | Adapters are in-process Python modules. |
| 3 | rui >= 0.8.1 (this documentation set) | `rui_compat` must match the build. |
| 4 | A decision: `inbound` (the device dials rui on `listen_port`) or `outbound` (rui dials the device) ([`src/rui/device/contract.py:8-13`](../../src/rui/device/contract.py#L8-L13)) | Determines who binds a port and who owns the transport. |
| 5 | A decision: A class (coexist with the vendor, low privilege) or B class (root/firmware, needs a rollback path) ([`docs/socket-plug-v1.md`](../socket-plug-v1.md) §1) | The manifest must declare a rollback for class B. |
| 6 | The full `DeviceAdapter` surface | `probe`, `open`, `close`, `frames`, `play`, `stop_playback`, `set_volume`, `set_led`, plus `drain_signals`/`drain_wakes` ([`src/rui/device/contract.py:374-459`](../../src/rui/device/contract.py#L374-L459)). |

### What you write

```text
my-device-plug/
  plugin.yaml            # id, version, rui_compat, trust, devices[]
  devices/<model>.yaml   # transport, capabilities, runtime.entrypoint, params
  adapter.py             # build(dev, cfg) -> DeviceAdapter
  README.md              # device-side install/rollback, human-read only
```

`runtime.mode: in-process` requires `trust: builtin|trusted`; the loader refuses
`trust: third-party` + in-process outright
([`src/rui/plugins/loader.py:205-244`](../../src/rui/plugins/loader.py#L205-L244)).
There is no sidecar runner in the 0.8.x line: a `sidecar` runtime is reported as
not implemented when the device is built
([`src/rui/device/registry.py:286-310`](../../src/rui/device/registry.py#L286-L310)).
The practical consequence: an in-process adapter is **your own audited code**;
`trust: trusted` is a self-assertion, not an authorization check
([`compatibility.md`](compatibility.md)).

### Verify, in order

1. Manifest + adapter, no hardware needed:

   ```bash
   rui plug-test my-device-plug --adapter my_device_plug.adapter:build
   ```

   Fix every FAIL before touching a speaker. `SKIP` for `wake.inject` is normal
   when the manifest declares no `device_wake`.

2. Wire it to rui with the fake-device loop from the quickstart and confirm
   capture + playback before replacing the fake transport with the real one:
   [`quickstart.md`](quickstart.md).

3. With the real device: `/health` must show
   `audio_plane: true`, `device_errors: 0` and the room `running`; the device
   detail must show `status: "ready"` and the capabilities you expect.

4. Fill `compatibility.tested_with` and `acceptance` in the manifest only after
   a real-device test; those fields are display/evidence, not runtime gates
   ([`src/rui/plugins/models.py:330-386`](../../src/rui/plugins/models.py#L330-L386)).

---

## Route comparison

| | EchoMuse route | Adapter route |
| --- | --- | --- |
| Code you write | none (install and configure) | manifest + Python adapter |
| Typical device | Echo Dot 2 / RS03QR with EchoMuse | everything else |
| Transport | inbound, controller dials the bridge | inbound or outbound, your choice |
| Device-side work | unlock + flash + token/address onboarding | install/point your client or SDK |
| Verification | `rui plug-test` on the bridge, `/health` planes, one spoken turn | `rui plug-test`, fake-device loop, then `/health` + device detail |
| Rollback | remove/replace plug directory, restore device controller backup | remove/replace plug directory; device-side rollback for class B |
| Weakest assumption | device firmware and network reachability | your adapter's real device I/O and capability honesty |

Never mix the two: do not fork the dot2 bridge to add a model it does not
support, and do not write a second adapter for a Dot 2 that already has a working
EchoMuse bridge. Either extend the bridge (new model manifest) or start a new
plug.

Next: [`install-paths.md`](install-paths.md) to register the plug,
[`debugging.md`](debugging.md) when it does not connect.
