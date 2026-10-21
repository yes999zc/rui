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

## Interface

```python
class DeviceAdapter(Protocol):
    async def probe(self) -> DeviceInfo: ...
    async def open(self) -> None: ...
    async def close(self) -> None: ...
    def frames(self) -> AsyncIterator[AudioFrame]: ...
    async def play(self, pcm: bytes, sample_rate: int) -> None: ...
    async def stop_playback(self, fade_ms: int = 120) -> None: ...
    async def set_volume(self, volume: int) -> None: ...
    async def set_led(self, effect: str, color: str = "") -> None: ...
```

## Tiers of support

1. **SSH + ALSA** — any Linux speaker. Capture and playback through ALSA. No
   reference channel, so barge-in is unavailable; software AEC only.
2. **Native audio service** — vendor exposes mic + reference streams. Full
   features, including barge-in. Where the LX06 adapter sits.
3. **Deep takeover** — firmware/root access to LEDs, keys, wake engine.

Document the tier in the adapter docstring. Do not advertise tier 1 as tier 2.

## Checklist for a new adapter

- [ ] `probe()` returns real capabilities, not the optimistic set
- [ ] channel layout and bit depth documented in the docstring
- [ ] `stop_playback()` fades, never clicks
- [ ] reconnect with backoff; a dead speaker must not take the process down
- [ ] one integration test against the mock adapter, one manual test on hardware
