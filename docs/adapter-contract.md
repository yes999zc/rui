# Adapter contract

Adding a speaker is writing one file. This is the promise the design has to keep.

## Capabilities, not promises

| capability | meaning | what breaks without it |
|---|---|---|
| `mic_array` | more than one mic channel | no beamforming; single-mic AEC still works |
| `reference` | playback loopback channel | **no barge-in while music plays** (physical limit, not a bug) |
| `playback` | we can push PCM | no voice output |
| `led` / `keys` / `volume` | physical feedback and control | cosmetic |
| `native_kws` | vendor wake engine usable | we run our own KWS on CPU |

The core reads capabilities and degrades. The console shows the missing ones
instead of pretending the device is fully supported.

## Two directions, one interface

| transport | who dials | example |
|---|---|---|
| `outbound` | we dial the device | a USB/ALSA bridge, an HTTP audio endpoint |
| `inbound` | the device dials us | LX06: `ws://<server>:<listen_port>/?token=...` |

An inbound adapter's `open()` starts accepting on `devices[].listen_port` and
`frames()` yields from the connected client; there is nothing to `probe()` over
the network until that client says hello, so `probe()` reports what the device
declared at handshake plus what we verified over SSH — never an optimistic set.
The token value lives in the environment (`devices[].token_env`), never in config.

## Interface

```python
class DeviceAdapter(Protocol):
    async def probe(self) -> DeviceInfo: ...
    async def open(self) -> None: ...
    async def close(self) -> None: ...
    def frames(self) -> AsyncIterator[AudioFrame]: ...
    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None: ...
    async def stop_playback(self, fade_ms: int = 120) -> None: ...
    async def set_volume(self, volume: int) -> None: ...
    async def set_led(self, effect: str, color: str = "") -> None: ...
```

`play()` takes the layout because playback is not always mono 16 kHz: the LX06
amplifies 2 ch at 48 kHz, and a mono buffer at the wrong rate is not an error —
it is silence or a pitch shift.

`stop_playback()` means *inaudible*, not *stop sending*: fade locally, flush the
queue, and tell the device to drop what it has already buffered.

An `AudioFrame` describes one interleaved block: `channels`, `sample_rate`,
`reference_channels` (trailing channels carrying the loopback), PCM already scaled
to 16-bit. Reference audio never arrives as a separate frame.

## Tiers of support

1. **SSH + ALSA** — any Linux speaker. Capture and playback through ALSA. No
   reference channel, so barge-in is unavailable; software AEC only.
2. **Native audio service** — vendor exposes mic + reference streams. Full
   features, including barge-in. Where the LX06 adapter sits.
3. **Deep takeover** — firmware/root access to LEDs, keys, wake engine.

Document the tier in the adapter docstring. Do not advertise tier 1 as tier 2.

## Checklist for a new adapter

- [ ] `probe()` returns real capabilities, not the optimistic set
- [ ] channel layout, sample rates and bit depth documented in the docstring
- [ ] decimation to the voice rate happens once, in the adapter
- [ ] `stop_playback()` fades, never clicks, and actually flushes the device buffer
- [ ] reconnect with backoff; a dead speaker must not take the process down
- [ ] one integration test against the mock adapter, one manual test on hardware
- [ ] an adapter this build cannot drive reports `unavailable`, it does not crash startup
