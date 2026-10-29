# Bridge developer quickstart: a fake device in five minutes

This walkthrough takes a laptop with a rui checkout from zero to a **fake device
connected to rui**: the audio plane consumes PCM from it (capture) and hands it
PCM to play (playback). No speaker, no models, no provider keys and no network
device are involved.

What it proves: the manifest, the adapter loader, the audio-plane lifecycle and
the capture/playback wire. What it does not prove: acoustics, wake-word recall,
VAD/ASR/LLM/TTS, a device-side client or a real radio link. Those need real
hardware and are covered by the device's own acceptance (see
[`decision-tree.md`](decision-tree.md) and [`debugging.md`](debugging.md)).

Everything below was run end to end on 2026-10-29 with rui `0.8.6` on a temporary
directory and a temporary loopback port; the log excerpts are verbatim.

---

## 0. Prerequisites

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) (the checkout's supported
  path), or an installed `rui` command from a release.
- A rui source checkout. The commands below assume you are in it (`house-ear/`),
  so `uv run rui …` uses the local source.
- One free loopback TCP port (the console/API); no other port is bound in this
  example because the fake device is an **outbound** adapter: rui dials the
  adapter (in process), not the other way round.

Nothing in this example needs ASR/LLM/TTS credentials. The console will log
`no ready provider - this stage stays idle` for those stages; that is expected
and does not affect capture or playback.

---

## 1. Create the plugin directory

A plug is a directory with `plugin.yaml`, one or more `devices/*.yaml`, and
(optionally) the adapter module. Install it under
`<data_dir>/plugs/<plug-dir>/` so the console/catalog sees the same manifest the
runtime uses:

```text
$DATA_DIR/
  plugs/
    fake-device-plug/
      plugin.yaml
      devices/
        fake.yaml
      fake_device.py
  tone.wav                 # the demo signal, see step 3
```

Create it, then save the three files below into `$DATA_DIR/plugs/fake-device-plug/`
(the `$PLUG_DIR` used by the commands):

```bash
WORK=/tmp/rui-bridge-demo
DATA_DIR="$WORK/data"
PLUG_DIR="$DATA_DIR/plugs/fake-device-plug"
mkdir -p "$PLUG_DIR/devices"
```

The commands below assume this default `WORK`. If you change it, also update
`system.data_dir` in the config (YAML does not expand shell variables) and the
`tone.wav`/`--config` paths in the commands.

`<data_dir>/plugs/<id>/plugin.yaml` is a **catalog** location: it is what the
loader discovers ([`src/rui/plugins/loader.py:287`](../../src/rui/plugins/loader.py#L287))
and what the console, wizard and `/api/v2/plugins` read. It does not execute
anything by itself. Adapter code runs only when it is named through
`RUI_ADAPTER_PLUGINS` (or the `rui.adapters` entry-point group) and backed by a
manifest — see [`install-paths.md`](install-paths.md) for the full matrix.

### `plugin.yaml`

```yaml
schema: rui-plugin/v0
id: fake-device-plug
name: "Fake Device Plug (quickstart)"
version: 1.0.0
rui_compat: ">=0.8.1 <0.9.0"
license: MIT
maintainer: "quickstart example"
homepage: "https://example.invalid/fake-device-plug"
trust: trusted
devices:
  - devices/fake.yaml
```

- `rui_compat` is the **only** plugin-level version gate; the loader compares it
  with `rui.__version__` and refuses the plug when out of range
  ([`src/rui/plugins/loader.py:53-80`](../../src/rui/plugins/loader.py#L53-L80)).
  `>=0.8.1 <0.9.0` is the current dual window (see
  [`compatibility.md`](compatibility.md)).
- `trust: trusted` is what permits `runtime.mode: in-process`. It is a
  self-assertion, not an authorization boundary — the adapter runs inside the rui
  process with the process's full privileges. `third-party` is refused for
  in-process adapters ([`src/rui/plugins/models.py:70-74`](../../src/rui/plugins/models.py#L70-L74),
  [`src/rui/device/loader.py:66`](../../src/rui/device/loader.py#L66),
  [`src/rui/plugins/loader.py:231-236`](../../src/rui/plugins/loader.py#L231-L236)).

### `devices/fake.yaml`

```yaml
schema: rui-device/v1
id: fake-speaker
model: "FAKE-01"
name: "Fake Speaker"
vendor: "quickstart example"
class: A
transport: outbound
protocol: {kind: fake, version: "1"}
capabilities: [playback, volume, led]
runtime:
  mode: in-process
  entrypoint: "fake_device:build"
  listen_port: 4499
  token_env: RUI_DEVICE_TOKEN
params:
  - {key: host, label: "Device address", type: string, required: true}
```

- `transport: outbound` means rui calls the adapter; no listener is bound, so
  `listen_port` is unused here. It is still required in any `runtime` block
  ([`src/rui/plugins/models.py:190-208`](../../src/rui/plugins/models.py#L190-L208))
  and it is what an **inbound** device would dial
  ([`src/rui/web/api/device_rules.py:51-76`](../../src/rui/web/api/device_rules.py#L51-L76)).
- `token_env` (default `RUI_DEVICE_TOKEN`) is the *name* of the environment
  variable that holds a device token, never the token. Only inbound adapters use
  it for authentication.
- `runtime.entrypoint` is `module:function`, validated as text and imported only
  when the device's plane starts ([`src/rui/plugins/models.py:205-222`](../../src/rui/plugins/models.py#L205-L222)).
- Capabilities are a promise: the fake adapter's `probe()` must report the same
  values or rui degrades to the intersection
  ([`src/rui/device/contract.py:47-97`](../../src/rui/device/contract.py#L47-L97)).

---

## 2. Add the fake device script

Save this as `$DATA_DIR/plugs/fake-device-plug/fake_device.py`. It is the
smallest useful `DeviceAdapter`: it synthesises 20 ms of silence per capture
frame, accepts playback into a counter, and reports the optional mixer/light
surface.

```python
"""A self-contained fake speaker: the smallest DeviceAdapter that can make noise.

Copy this file next to plugin.yaml, rename it if you like, and point
runtime.entrypoint: "fake_device:build" at it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from rui.device.contract import AudioFrame, Capability, DeviceInfo, Transport

log = logging.getLogger("fake-device")
FRAME_MS = 20
RATE = 16000


class FakeServices:
    """The optional shell/mixer/light surface. Only the mixer is implemented."""

    def __init__(self, adapter: "FakeSpeaker") -> None:
        self._adapter = adapter

    async def shell(self, script: str, timeout: float = 3.0) -> str:
        raise NotImplementedError("the fake device has no shell")

    async def read_volume(self) -> int | None:
        return self._adapter.device_volume

    async def set_volume(self, volume: int) -> None:
        self._adapter.device_volume = max(0, min(255, int(volume)))

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        self._adapter.last_led = effect


class FakeSpeaker:
    def __init__(self, device: object) -> None:
        self.info = DeviceInfo(
            id=str(getattr(device, "id", "fake")),
            adapter=str(getattr(device, "adapter", "fake-speaker")),
            model="FAKE-01",
            host=str(getattr(device, "host", "127.0.0.1")),
            room=str(getattr(device, "room", "")),
            capabilities=frozenset({Capability.PLAYBACK, Capability.VOLUME, Capability.LED}),
            transport=Transport.OUTBOUND,
            channels=1,
            sample_rate=RATE,
            bit_depth=16,
            mic_channels=1,
            ref_channels=0,
        )
        self.services = FakeServices(self)
        self._open = False
        self._seq = 0
        self.device_volume = 60
        self.last_led = ""
        self.capture_frames = 0
        self.playback_bytes = 0

    async def probe(self) -> DeviceInfo:
        return self.info

    async def open(self) -> None:
        self._open = True
        log.info("fake device: opened id=%s transport=%s", self.info.id, self.info.transport)

    async def close(self) -> None:
        self._open = False
        log.info(
            "fake device: closed after %d capture frame(s), %d playback byte(s)",
            self.capture_frames,
            self.playback_bytes,
        )

    async def frames(self) -> AsyncIterator[AudioFrame]:
        if not self._open:
            raise RuntimeError("adapter is not open")
        while self._open:
            self._seq += 1
            self.capture_frames += 1
            if self._seq % 50 == 1:
                log.info(
                    "fake device: capture frame #%d (20 ms, %d samples, silence)",
                    self._seq,
                    RATE * FRAME_MS // 1000,
                )
            yield AudioFrame(
                device_id=self.info.id,
                seq=self._seq,
                ts_ms=time.monotonic() * 1000.0,
                pcm=b"\x00\x00" * (RATE * FRAME_MS // 1000),
                channels=1,
                sample_rate=RATE,
            )
            await asyncio.sleep(FRAME_MS / 1000.0)

    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        self.playback_bytes += len(pcm)
        log.info(
            "fake device: playback chunk %d bytes @ %d Hz / %d ch (total %d bytes)",
            len(pcm),
            sample_rate,
            channels,
            self.playback_bytes,
        )

    async def stop_playback(self, fade_ms: int = 120) -> None:
        log.info("fake device: stop_playback(fade_ms=%d)", fade_ms)

    async def set_volume(self, percent: int) -> None:
        self.device_volume = max(0, min(255, round(int(percent) * 255 / 100)))

    async def read_volume(self) -> int | None:
        return self.device_volume

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        self.last_led = effect

    def drain_signals(self) -> list:
        return []

    def drain_wakes(self) -> list:
        return []

    def health(self) -> dict:
        # The console's device page and the action endpoints read this; a real
        # adapter reports its transport link, not a constant.
        return {"connected": True, "connected_at": time.time()}


def build(device: object = None, config: object = None) -> FakeSpeaker:
    return FakeSpeaker(device)
```

Notes:

- `probe()` must describe real capture layout: `channels == mic_channels + ref_channels`
  ([`src/rui/device/contract.py:292-325`](../../src/rui/device/contract.py#L292-L325)).
- `play()` receives interleaved int16 at the effective playback wire. The plane
  logs `playback: <name> <seconds> s at <rate> Hz` before the first block
  ([`src/rui/audio/process.py:2518`](../../src/rui/audio/process.py#L2518)).
- `health()` is **not** part of `DeviceAdapter`; it is the optional diagnostics
  hook the console and the action endpoints read
  ([`src/rui/web/api/app.py:2247-2282`](../../src/rui/web/api/app.py#L2247-L2282)).
  The demo returns `connected: True` so the `audio.play` action passes its live-link
  gate; a real outbound adapter should derive this from its own link state.
- `drain_signals()` / `drain_wakes()` have protocol defaults, but the contract kit
  requires the methods to be present ([`src/rui/device/contract.py:423-439`](../../src/rui/device/contract.py#L423-L439),
  [`src/rui/plugins/contract.py:100-112`](../../src/rui/plugins/contract.py#L100-L112)).

---

## 3. Minimal config and a playback file

Save this as `/tmp/rui-bridge-demo/config.yaml` (the directory was created in
step 1):

```yaml
version: 3
system:
  data_dir: "/tmp/rui-bridge-demo/data"
  log_level: info
web:
  host: 127.0.0.1
  port: 8088
  auth_token: ""          # loopback only; keep this empty ONLY on 127.0.0.1
devices:
  - id: fake
    adapter: fake-speaker # must match the name the adapter registers under
    host: 127.0.0.1
    room: "Quickstart lab"
    volume: 60
audio:
  voice:
    kws_enabled: false    # this demo does not exercise wake/VAD/ASR
    vad_model_path: ""    # an empty path only logs "vad off:"; the plane still runs
  capture:
    rate: 16000
    channels: 1
  playback:
    rate: 16000
    channels: 1
```

- `version` is the config schema version; the current value is `3`
  ([`src/rui/config/schema.py:25`](../../src/rui/config/schema.py#L25)).
- `system.data_dir` is where plugs, `service.log` and this demo's `tone.wav`
  live.
- `web.auth_token: ""` is accepted on a loopback bind only; a non-loopback bind
  without a token refuses to start
  ([`src/rui/__main__.py:180-191`](../../src/rui/__main__.py#L180-L191)). If you set
  a token, send it as the `X-Auth-Token` header on API calls.
- `devices[].adapter` is a registration name, not a model: for a plug installed
  through `RUI_PLUGINS_DIR` the image/operator loader registers it as the device
  manifest `id`; for `RUI_ADAPTER_PLUGINS` it is the `name=` prefix. Both must
  agree with the config. See [`install-paths.md`](install-paths.md).

Create a 0.5 s mono tone at the playback rate. The `audio.play` action only
accepts files inside `data_dir` **or** `music.cache_dir` (default
`/tmp/rui-music`) — [`src/rui/web/api/v2_api.py:2987-3005`](../../src/rui/web/api/v2_api.py#L2987-L3005):

```bash
DATA_DIR=/tmp/rui-bridge-demo/data
python3 - "$DATA_DIR/tone.wav" <<'PY'
import sys, wave, math, struct
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 16000)))
        for i in range(8000)))
PY
```

---

## 4. Start rui with the plug registered

From the rui checkout:

```bash
DATA_DIR=/tmp/rui-bridge-demo/data
PLUG_DIR="$DATA_DIR/plugs/fake-device-plug"

PYTHONPATH="$PLUG_DIR" \
RUI_DATA_DIR="$DATA_DIR" \
RUI_ADAPTER_PLUGINS="fake-speaker=fake_device:build" \
  uv run rui serve --config /tmp/rui-bridge-demo/config.yaml
```

`RUI_CONFIG=/tmp/rui-bridge-demo/config.yaml uv run rui serve` is equivalent;
`--config` is accepted before or after the subcommand
([`src/rui/__main__.py:618-645`](../../src/rui/__main__.py#L618-L645)).

Why the three environment variables:

| Variable | What it does here |
| --- | --- |
| `PYTHONPATH` | Makes `fake_device` importable by `RUI_ADAPTER_PLUGINS`. A real deployment would normally install the adapter as a package instead. |
| `RUI_ADAPTER_PLUGINS` | `name=module:attr`: imports `fake_device:build` at startup and registers it as `fake-speaker`. The control plane and the audio-plane child process both call the loader ([`src/rui/device/loader.py:113-147`](../../src/rui/device/loader.py#L113-L147), [`src/rui/audio/process.py:3994-4000`](../../src/rui/audio/process.py#L3994-L4000)). |
| `RUI_DATA_DIR` | Where `load_adapter_plugins()` looks for `<data_dir>/plugs/*` manifests when the module is not co-located with its `plugin.yaml` ([`src/rui/device/loader.py:50-54`](../../src/rui/device/loader.py#L50-L54), [`src/rui/device/loader.py:292-377`](../../src/rui/device/loader.py#L292-L377)). Here the manifest is also co-located, so this is belt and braces. |

Alternative: point `RUI_PLUGINS_DIR` at `$DATA_DIR/plugs` and drop
`RUI_ADAPTER_PLUGINS`; then the plug is treated as an image-style root and its
`runtime.entrypoint` is registered automatically as the device id
(`fake-speaker`). Both routes are valid and documented in
[`install-paths.md`](install-paths.md).

---

## 5. What you should see

The console (uvicorn) log and the audio-plane child share stdout/stderr in this
mode; plane lines carry a `plane:` prefix because the supervisor relays the
child's stderr ([`src/rui/audio/client.py:545-566`](../../src/rui/audio/client.py#L545-L566)).
Excerpts from the verified run:

```text
INFO rui.device.loader: adapter plugins: registered fake-speaker
INFO rui.audio.client: plane: ... INFO rui.device.loader: adapter plugins: registered fake-speaker
INFO rui.audio.client: plane: ... INFO fake-device: fake device: opened id=fake transport=outbound
INFO rui.audio.client: plane: ... INFO __main__: audio wire (fake): nothing the device or manifest states
INFO rui.web.api.app: audio plane up: pid=32312 device=fake capture=16000 Hz/1 ch
INFO rui.audio.client: plane: ... INFO fake-device: fake device: capture frame #1 (20 ms, 320 samples, silence)
INFO rui.audio.client: plane: ... INFO fake-device: capture frame #51 (20 ms, 320 samples, silence)
```

| Log line | Meaning | Code |
| --- | --- | --- |
| `adapter plugins: registered fake-speaker` | The env entry passed the manifest/trust gate and registered. Appears once in the control process and once in the plane. | [`src/rui/device/loader.py:145-147`](../../src/rui/device/loader.py#L145-L147) |
| `audio plane up: pid=… device=… capture=16000 Hz/1 ch` | The control plane received `Ready`; the room is drivable. | [`src/rui/web/api/app.py:3968-3978`](../../src/rui/web/api/app.py#L3968-L3978) |
| `audio wire (fake): nothing the device or manifest states` | No playback expectation because the manifest declares no `audio_defaults`. Add playback facts later to make this a checked line. | [`src/rui/audio/process.py:960-982`](../../src/rui/audio/process.py#L960-L982) |
| `capture frame #N` | The plane is consuming `frames()`. This is the capture half of the demo. | adapter's own log |
| `playback: tone.wav 0.5 s at 16000 Hz (from 0.0 s)` and `playback finished: tone.wav` | The plane read the file, resampled it and streamed it to `play()`. | [`src/rui/audio/process.py:2518-2546`](../../src/rui/audio/process.py#L2518-L2546) |
| `fake device: playback chunk …` | The fake device accepted the PCM. This is the playback half. | adapter's own log |

---

## 6. Verify over HTTP

The console API is on `http://127.0.0.1:8088` in this example. With an empty
`auth_token`, no header is needed; otherwise add `-H "X-Auth-Token: $TOKEN"`.

```bash
curl -s http://127.0.0.1:8088/health | python3 -m json.tool
```

Expect (values trimmed):

```json
{
  "status": "ok",
  "version": "0.8.6",
  "devices": 1,
  "device_errors": 0,
  "adapter_plugins": {"registered": ["fake-speaker"], "warnings": 0},
  "audio_plane": true,
  "speaker_connected": true,
  "speaker_device": "fake"
}
```

`adapter_plugins.registered` is the honest answer of the loader and
`warnings` is a count, not text; full warning text is in the log
([`src/rui/web/api/app.py:4247-4251`](../../src/rui/web/api/app.py#L4247-L4251)).

The device detail builds the status from the adapter probe plus the plane, and
shows the effective capability set (manifest ∩ probe for probe-bound values):

```bash
curl -s http://127.0.0.1:8088/api/v2/devices/fake | python3 -m json.tool
```

```json
{
  "id": "fake",
  "status": "ready",
  "ready": true,
  "capabilities": [
    {"id": "playback", "declared": true, "probed": true, "effective": true, "known": true, "reason": "ok"},
    {"id": "led", "declared": true, "probed": null, "effective": true, "known": true, "reason": "manifest_only"},
    {"id": "volume", "declared": true, "probed": null, "effective": true, "known": true, "reason": "manifest_only"}
  ],
  "listen_port": 4499
}
```

(`led`/`volume` are manifest-authoritative because `DeviceServices` is the
evidence for them, not `probe()`;
[`src/rui/device/contract.py:47-87`](../../src/rui/device/contract.py#L47-L87).)

Now trigger one playback through the public action endpoint:

```bash
curl -s -X POST http://127.0.0.1:8088/api/v2/devices/fake/actions \
  -H 'Content-Type: application/json' \
  -d '{"action":"audio.play","args":{"file":"/tmp/rui-bridge-demo/data/tone.wav"}}'
```

```json
{"action":"audio.play","ok":true,"code":"ok","result":{"playing":"/tmp/rui-bridge-demo/data/tone.wav"},"device_status":{"ready":true,"connected":true}}
```

and the log shows:

```text
INFO rui.audio.client: plane: ... INFO __main__: playback requested: /tmp/rui-bridge-demo/data/tone.wav loop=False gain=1.00
INFO rui.audio.client: plane: ... INFO __main__: playback: tone.wav 0.5 s at 16000 Hz (from 0.0 s)
INFO rui.audio.client: plane: ... INFO fake-device: fake device: playback chunk 3200 bytes @ 16000 Hz / 1 ch (total 3200 bytes)
...
INFO rui.audio.client: plane: ... INFO fake-device: fake device: playback chunk 3200 bytes @ 16000 Hz / 1 ch (total 16000 bytes)
INFO rui.audio.client: plane: ... INFO __main__: playback finished: tone.wav
```

The action gates on capability and a live link; the fake `health()` is what
satisfies the link check here. A real outbound adapter must report its own link
([`src/rui/web/api/v2_api.py:2836-2870`](../../src/rui/web/api/v2_api.py#L2836-L2870)).

Capture needs no HTTP call: watch the `capture frame #N` lines, or check that
`/api/v2/devices/fake` keeps reporting `ready: true` and the plane's `pid` in
`/health.audio_devices` stays the same.

---

## 7. Run the contract kit

```bash
uv run rui plug-test "$PLUG_DIR" --adapter "$PLUG_DIR/fake_device.py:build"
```

Expected tail (verified):

```text
PASS manifest.load                  loaded by rui.plugins.load_plugin (0.8.6)
PASS manifest.compatibility         requires >=0.8.1 <0.9.0; this build is 0.8.6
...
PASS adapter.methods                DeviceAdapter surface incl. drain_wakes
PASS device.probe_layout            probe() layout matches the manifest
PASS capture.frames                 3 frame(s), ~20.0 ms each
PASS playback.sink                  accepted 640 bytes at 16000 Hz/1ch; stop_playback twice
SKIP wake.inject                    device_wake is not effective (manifest/probe); no safe wake injection
...
summary: pass=20 fail=0 warn=0 skip=1 -> PASS
```

The SKIP is correct: this manifest declares no `device_wake`, so there is
nothing to inject. The kit imports the adapter in your process — it is a
developer tool, not a sandbox ([`src/rui/plugins/contract.py:1-30`](../../src/rui/plugins/contract.py#L1-L30)).

---

## 8. Stop and clean up

`Ctrl-C` the server (or kill the PID you started). The plane child is stopped by
the supervisor; the fake adapter logs `fake device: closed after … capture
frame(s), … playback byte(s)`. Then delete the temporary directory.

---

## Where to go next

- Choose the right route for a real device: [`decision-tree.md`](decision-tree.md).
- Learn the three install/registration mechanisms: [`install-paths.md`](install-paths.md).
- When nothing connects or the plane refuses to start: [`debugging.md`](debugging.md).
- Version windows and the `trust` boundary: [`compatibility.md`](compatibility.md).
- Full contract text: [`docs/socket-plug-v1.md`](../socket-plug-v1.md),
  [`docs/plug-test-kit.md`](../plug-test-kit.md).
- For a fake speaker that speaks a **network** protocol instead of the in-process
  contract, [`scripts/measure_turn_pipeline.py`](../../scripts/measure_turn_pipeline.py)
  drives the LX06 8-channel websocket wire without a room or a microphone.
- An EchoMuse-capable Dot 2 needs no adapter at all; see
  [`decision-tree.md`](decision-tree.md).
