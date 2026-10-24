"""Wav reading and writing shared by playback and the acknowledgement."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    """int16/24/32-bit wav of any channel count -> mono float32 in [-1, 1]."""
    with wave.open(str(path)) as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        data = (v / float(1 << 23)).astype(np.float32)
    else:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def write_wav_mono(path: Path, pcm: bytes, rate: int) -> None:
    """Raw little-endian int16 mono PCM -> a wav any player here can read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    tmp.replace(path)


def trim_silence(pcm: bytes, rate: int, thresh_db: float = -45.0) -> bytes:
    """Drop leading/trailing near-silence from raw int16 mono PCM.

    A TTS vendor pads its output: "我在。" came back 933 ms for a 550 ms utterance, and a
    spoken acknowledgement that is measured with the padding looks like it breaks the cap
    when it does not. The old build trimmed for the same reason.
    """
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    win = max(1, rate // 100)  # 10 ms
    usable = x.size // win * win
    if usable < win * 4:
        return pcm
    frames = x[:usable].reshape(-1, win)
    rms = np.sqrt(np.square(frames).mean(axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    loud = np.where(db > thresh_db)[0]
    if loud.size == 0:
        return pcm
    start = max(0, (int(loud[0]) - 3) * win)
    end = min(x.size, (int(loud[-1]) + 2) * win)
    return (np.clip(x[start:end], -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
