"""Six microphones into one stream — weighted, never "pick the loudest".

The device has six correlated microphones (pairwise correlation 0.93-0.97, inter-channel
delay under one sample). Averaging coherent speech keeps its level while independent room
noise drops by ~8 dB; the old deployment measured the plain mean beating the best single
channel by 1.6 dB on this hardware. Picking one channel therefore throws away the only
array gain this speaker has, and picking it by *residual* power - what this build did -
picks the channel whose echo cancellation worked worst.

So: weight each channel by how far it stands above its own noise floor, then sum. Two
details are inherited from the old deployment because they are cheap and they fix real
failures:

* **Per-channel noise floor with a fast drop and a slow rise.** A floor that rises as
  fast as it falls follows the speech itself, and the weights stop discriminating.
* **Freeze the weights while the speaker is emitting.** The mic signals then are
  dominated by echo, so the weights would chase whichever channel is loudest in the room
  rather than whoever is talking - and they would change mid-word, which is audible to
  the wake-word model even when it is inaudible to us.

An empty block must not poison the floor with NaN: that failure made the old build deaf
for the rest of the process, with no error anywhere.
"""
from __future__ import annotations

import numpy as np

# How far above its own floor a channel has to be before it earns weight. 1.5x is the
# old deployment's value; below it, everything is "floor" and the weights are uniform.
FLOOR_RATIO = 1.5
# Floor tracking: a real noise floor drops fast (a fan switching off) and rises slowly
# (a fan switching on), because the slow direction must not mistake speech for noise.
FLOOR_DROP_FAST = 0.90
FLOOR_DROP_SLOW = 0.10
FLOOR_RISE_FAST = 0.9995
FLOOR_RISE_SLOW = 0.0005
FLOOR_MIN = 1e-6


class ChannelMixer:
    """Per-channel noise floor, weights, and the weighted sum."""

    def __init__(
        self,
        channels: int,
        *,
        floor_ratio: float = FLOOR_RATIO,
        freeze_last: bool = True,
    ) -> None:
        self.channels = channels
        self.floor_ratio = floor_ratio
        self.freeze_last = freeze_last
        self.floor: np.ndarray | None = None
        self.weights: np.ndarray | None = None
        self.frozen = False

    def update(self, block: np.ndarray, *, freeze: bool = False) -> np.ndarray:
        """Weights for this block. `freeze` keeps the previous ones (playback)."""
        if block.ndim != 2 or block.shape[1] != self.channels or block.shape[0] == 0:
            return self._uniform()
        rms = np.sqrt(np.square(block.astype(np.float64)).mean(axis=0)) + 1e-9
        if not np.all(np.isfinite(rms)):
            return self.weights if self.weights is not None else self._uniform()

        if self.floor is None:
            self.floor = rms.copy()
        else:
            down = rms < self.floor
            floor = np.where(
                down,
                self.floor * FLOOR_DROP_FAST + rms * FLOOR_DROP_SLOW,
                self.floor * FLOOR_RISE_FAST + rms * FLOOR_RISE_SLOW,
            )
            self.floor = np.where(np.isfinite(floor), np.maximum(floor, FLOOR_MIN), rms)

        self.frozen = bool(freeze and self.freeze_last and self.weights is not None)
        if self.frozen:
            return self.weights if self.weights is not None else self._uniform()

        above = np.maximum(rms - self.floor * self.floor_ratio, 0.0)
        total = float(above.sum())
        if total < 1e-9:
            self.weights = self._uniform()
        else:
            w = above**2
            self.weights = w / w.sum()
        return self.weights

    def mix(self, block: np.ndarray, weights: np.ndarray) -> np.ndarray:
        """Weighted sum with weights that already sum to one (so level is preserved)."""
        if block.shape[1] != weights.size:
            return np.zeros(block.shape[0], dtype=np.float32)
        return (block.astype(np.float32) * weights.astype(np.float32)).sum(axis=1)

    def _uniform(self) -> np.ndarray:
        w = np.full(self.channels, 1.0 / self.channels, dtype=np.float64)
        self.weights = w
        return w

    def reset(self) -> None:
        self.floor = None
        self.weights = None
        self.frozen = False
