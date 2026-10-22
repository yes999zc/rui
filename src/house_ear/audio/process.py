"""Audio plane: AEC, KWS, VAD and playback, in their own process.

Everything here is driven by the frames an adapter hands over. The plane never reads
`audio.capture` to decide a layout - a frame says its own `channels`, `sample_rate` and
`reference_channels`, so an LX06 (48 kHz / 8 ch / 6 mic + 2 ref) and a future ESP32
(16 kHz / 1 ch / no reference) run through the same code path with different adapters.
Config values are expectations the doctor checks, not assumptions the plane bakes in.

Pipeline today: decimate to the voice rate -> echo-cancel each mic against the reference
-> emit the channel that survived best -> hand one mono stream to the control plane.
Playback (the debug wav loop, CLI only) travels the other way through the adapter.

Run standalone:
    python -m house_ear.audio.process --config config.yaml --device living
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import math
import os
import signal
import sys
import tempfile
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

from ..config import Config, load_config
from ..device import build_adapters
from ..device.contract import AudioFrame, DeviceAdapter
from ..log_setup import setup_logging
from ..trace import now_ms
from .ack import render_cached as render_ack
from .aec import AecChain, AecUnavailable
from .decimate import Decimator
from .kws import KwsUnavailable, WakeHit, WakeProbe, WakeWord
from .protocol import (
    CONTROL,
    DeviceVolume,
    DeviceVolumeResult,
    Error,
    FrameType,
    Hello,
    Metrics,
    PlayFile,
    QueryMetrics,
    Ready,
    ShellCommand,
    ShellResult,
    SpeakFinished,
    SpeakStart,
    StopAudio,
    VendorSignalMsg,
    Wake,
    decode_pcm,
    encode_json,
    encode_pcm,
    read_frame,
)
from .resample import resample
from .ring import ArrivalStats, PcmRing
from .vad import GateConfig, UtteranceGate, VadUnavailable
from .wav import read_wav_mono

METRICS_EVERY_S = 2.0
PLAY_CHUNK_MS = 100
# Reply pacing. Pushing a block and then waiting exactly its own duration leaves the
# device's buffer empty the whole time: any scheduler hiccup (1-3 ms per send, GC, a
# busy VM) and the speaker underruns - heard as stutter, invisible in every counter.
# So: hand it PREBUFFER_S of audio up front, keep writing CATCHUP faster than real time
# so the lead can only grow, and clamp so a long answer never runs a second ahead (a
# barge-in would then take that long to be heard).
# The device chain is a pipe into `aplay --buffer-size 4800 --period-size 960` (300 ms of
# ALSA buffer, 60 ms periods). Pushing well past that only fills the pipe: the client's
# write then blocks, and a write that blocks past its timeout is dropped - measured in
# /tmp/client.log as "slow write 112 ms (3200 bytes)" repeating through a news read, which
# is the roughness. So: a lead the device can actually hold, and no creep.
PREBUFFER_S = 0.25
CATCHUP = 1.0
MAX_LEAD_S = 0.32
# A producer that ignores SpeakFinished cannot grow the queue without bound.
MAX_SPEAK_QUEUE_S = 120.0
# 12 ms of ramp at the start of playback. Speech that begins on a hard sample boundary
# clicks, and on this speaker a click reads as a stray 「叮」 at the front of the answer.
# 12 ms is short enough to be inaudible as a fade and long enough to remove the step.
FADE_IN_MS = 12


class Pacer:
    """Keeps the speaker's buffer full instead of pacing it to empty.

    ``due`` is when the audio already handed to the device finishes playing, so
    ``due - now`` is the buffer depth. ``target`` is depth we insist on: it starts at
    the prebuffer and creeps up by ``catchup - 1`` per block, which covers a device
    clock that runs a hair fast, and is capped so a barge-in is never queued behind a
    second of speech. The wait is the only thing that ever changed.
    """

    def __init__(
        self,
        prebuffer: float = PREBUFFER_S,
        catchup: float = CATCHUP,
        max_lead: float = MAX_LEAD_S,
    ) -> None:
        self.prebuffer = prebuffer
        self.catchup = catchup
        self.max_lead = max_lead
        self.due = 0.0
        self.target = prebuffer
        self.lead = 0.0

    def sleep_for(self, now: float, seconds: float) -> float:
        """Record a block of ``seconds`` just sent, return how long to wait."""
        self.due = max(self.due, now) + seconds
        self.target = min(self.target + (self.catchup - 1.0) * seconds, self.max_lead)
        self.lead = self.due - now
        return max(0.0, self.due - self.target - now)


def fade_in(samples: np.ndarray, rate: int, ms: float = FADE_IN_MS) -> np.ndarray:
    """Ramp the first `ms` of a playback session; a hard onset clicks."""
    n = min(int(rate * ms / 1000), samples.size)
    if n <= 1:
        return samples
    out = samples.copy()
    out[:n] *= np.linspace(0.0, 1.0, n, dtype=np.float32)
    return out


# The device buffer keeps sounding after the last frame is sent (~300 ms at 48 kHz), so a
# tone needs a tail floor before the gate may open. The old build used 250 ms.
ACK_TAIL_MS = 250
log = logging.getLogger(__name__)


def _dbfs(samples) -> float:
    """Level of one block in dBFS. Floored at -100 so it survives a JSON round trip."""
    peak = float(np.sqrt(np.mean(np.square(np.asarray(samples, dtype=np.float64)))))
    return 20.0 * math.log10(max(peak, 1e-5))


class AudioPlane:
    def __init__(self, cfg: Config, device_id: str, socket_path: Path) -> None:
        self.cfg = cfg
        self.device_id = device_id
        self.socket_path = socket_path
        self.adapter: DeviceAdapter | None = None
        self.ring = PcmRing(capacity_ms=cfg.audio.playback.queue_ms, frame_ms=cfg.audio.voice.frame_ms)
        self.arrivals = ArrivalStats()
        self.aec: AecChain | None = None
        self.aec_error = ""
        self.wake: WakeWord | None = None
        self.probe: WakeProbe | None = None
        self.probe_hits = 0
        self.kws_error = ""
        self.wake_count = 0
        self.last_wake: Wake | None = None
        self.gate: UtteranceGate | None = None
        self.vad_error = ""
        self.utterances = 0
        # Unbounded on purpose. The control plane hands a whole sentence over in a burst
        # (TTS is much faster than real time), so a chunk-count limit truncates the
        # sentence: the log said "speak queue full, dropping a chunk" a dozen times a
        # turn and the listener heard the gaps. The budget below is seconds of audio,
        # which is the thing that actually has a right answer.
        self._speak_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        # Armed by SpeakStart, consumed by the next PLAY_PCM frame: the one declared
        # sentence boundary in the current reply. The old source of truth - seq == 0 -
        # fired on every speak() call, i.e. on every TTS chunk, and faded mid-word.
        self._segment_pending = False
        self._speak_queued_s = 0.0
        self.frames_in = 0
        self.frames_out = 0
        self.frames_dropped = 0
        self.no_consumer = 0
        self.cpu_s = 0.0
        self.audio_s = 0.0
        # Levels are the first thing to look at when "it hears nothing": a silent mic and a
        # mic that hears but does not understand look identical from the counters alone.
        self.voice_rms_db: float | None = None
        # Peak of the last 10 s of the KWS input: "how loud is he actually talking" had
        # no answer anywhere, so every threshold discussion was a guess. Cleared on read
        # by the metrics snapshot so each window is a fresh measurement.
        self.voice_peak_db: float | None = None
        self._peak_hold: float = 0.0
        self.ref_rms_db: float | None = None
        self.playback_s = 0.0
        self.playback_lead_s = 0.0
        # The speaker's own mixer value, read over the shell channel. Loudness on this box
        # belongs to the vendor's `mysoftvol`; every ERLE or barge number is only
        # comparable when this value is known.
        self.device_volume: int | None = None
        self.barges = 0
        self.last_barge_s = 0.0
        self.playback_path = ""
        self.last_playback_error = ""
        self._playback_task: asyncio.Task | None = None
        # Where the current file is, and whether speech paused it. One device stream means
        # a reply and a song cannot both be written: the old build paused the song for
        # speech and resumed it after the turn, and the same is needed here because two
        # writers interleaved their PCM into one player (heard on 2026-10-22).
        # One device stream, one owner. Measured 2026-10-22: a reply written while a song
        # was playing interleaved two PCM streams into the same player and the words came
        # out stuttering. The plane enforces the lock; the coordinator decides what to ask
        # for by sending speak / play_file / cue.
        self._owner: str | None = None
        self._pending_play: PlayFile | None = None
        self._file_position = 0
        self._file_cfg: tuple[Path, bool, float] | None = None
        self._speech_paused = False
        self._reply_open = False
        self.started = time.monotonic()
        self._stop = asyncio.Event()
        # Pre-wake audio. The streaming KWS reports a hit ~0.5 s after the wake word ends,
        # so "你好小瑞，现在几点" said in one breath loses 现在几点 unless the ring is
        # replayed into the gate. Kept as int16 voice-rate blocks, oldest first.
        self._preroll: deque[bytes] = deque()
        self._preroll_samples = 0
        self.preroll_replayed_ms = 0.0
        self.preroll_replays = 0
        # Wake acknowledgement (S5a). Fire-and-forget: awaiting it here would block the
        # frame consumer for the cue's whole length and drop microphone audio.
        self._ack_task: asyncio.Task | None = None
        # Four counters because one number cannot describe four outcomes: a cue that was
        # asked for, one that started, one that finished and one that was superseded.
        self.ack_attempts = 0
        self.ack_plays = 0
        self.ack_skipped = 0
        self.ack_failed = 0
        self.ack_mode_effective = cfg.ack.mode
        self.ack_error = ""

    # ---------------------------------------------------------------- adapter
    async def _open_adapter(self) -> DeviceAdapter:
        adapters = build_adapters(self.cfg)
        if self.device_id in adapters.errors:
            raise RuntimeError(adapters.errors[self.device_id])
        adapter = adapters.adapters.get(self.device_id)
        if adapter is None:
            raise RuntimeError(f"no such device: {self.device_id}")
        await adapter.open()
        return adapter

    def _build_aec(self, mic_channels: int, ref_channels: int) -> None:
        if not self.cfg.audio.aec.enabled:
            return
        if ref_channels < 1:
            self.aec_error = "device has no reference channel: AEC and barge-in unavailable"
            log.warning("aec off: %s", self.aec_error)
            return
        try:
            self.aec = AecChain(
                channels=mic_channels,
                rate=self.cfg.audio.voice.rate,
                taps=self.cfg.audio.aec.filter_len,
                cancel=self.cfg.audio.aec.cancel,
            )
        except AecUnavailable as exc:
            self.aec_error = str(exc)
            log.warning("aec off: %s", exc)

    def _build_kws(self) -> None:
        voice = self.cfg.audio.voice
        if not voice.kws_enabled:
            return
        if not voice.kws_model_dir:
            self.kws_error = "kws_enabled but kws_model_dir is empty"
            log.warning("kws off: %s", self.kws_error)
            return
        try:
            self.wake = WakeWord(
                model_dir=voice.kws_model_dir,
                keywords_file=voice.kws_keywords_file or None,
                chunk=voice.kws_chunk,
                rate=voice.rate,
            )
            if voice.kws_probe_file:
                # Its own decoder state: one stream cannot be fed to two spotters.
                self.probe = WakeProbe(
                    model_dir=voice.kws_model_dir,
                    keywords_file=voice.kws_probe_file,
                    chunk=voice.kws_chunk,
                    rate=voice.rate,
                )
                log.info("kws probe on: %s", voice.kws_probe_file)
        except KwsUnavailable as exc:
            self.kws_error = str(exc)
            log.warning("kws off: %s", exc)

    def _build_vad(self) -> None:
        voice = self.cfg.audio.voice
        if not voice.vad_model_path:
            self.vad_error = "vad_model_path is empty: wake fires but nothing is transcribed"
            log.warning("vad off: %s", self.vad_error)
            return
        try:
            self.gate = UtteranceGate(
                GateConfig(
                    rate=voice.rate,
                    min_speech_ms=voice.vad_min_speech_ms,
                    end_silence_ms=voice.vad_end_silence_ms,
                    skip_after_wake_ms=voice.wake_skip_ms,
                    preroll_ms=voice.vad_preroll_ms,
                ),
                voice.vad_model_path,
            )
        except VadUnavailable as exc:
            self.vad_error = str(exc)
            log.warning("vad off: %s", exc)

    # ------------------------------------------------------------- pipeline
    def _frame_to_voice(self, frame: AudioFrame, decimator: Decimator) -> bytes:
        """Frame -> one mono voice-rate block, echo-cancelled when a reference exists."""
        if not frame.pcm or frame.mic_channels < 1:
            return b""
        samples = np.frombuffer(frame.pcm, dtype="<i2")
        usable = frame.frame_samples * frame.channels
        # Normalise before anything else: the decimator, the canceller and the KWS all
        # work in [-1, 1]. Feeding them int16 magnitudes and multiplying back at the end
        # clips every sample of the output - which sounds like noise to a wake word and
        # is invisible in a byte count.
        block = decimator.process(
            samples[:usable].reshape(-1, frame.channels).astype(np.float32) / 32768.0
        )

        raw_mic = block[:, : frame.mic_channels]
        if self.aec is not None and frame.reference_channels > 0:
            ref = block[:, frame.mic_channels]
            mono = self.aec.process(raw_mic, ref)
            self.ref_rms_db = _dbfs(ref)
        else:
            mono = block[:, 0]
        self.voice_rms_db = _dbfs(raw_mic.max(axis=1) if raw_mic.ndim > 1 else raw_mic)
        if mono.size:
            peak = float(np.abs(mono).max())
            self._peak_hold = max(self._peak_hold, peak)
        return np.clip(mono * 32768.0, -32768, 32767).astype("<i2").tobytes()

    async def _capture(self, connected: asyncio.Event, writer: asyncio.StreamWriter) -> None:
        assert self.adapter is not None
        info = await self.adapter.probe()
        self._build_aec(info.mic_channels, info.ref_channels)
        self._build_kws()
        self._build_vad()
        decimator = Decimator(
            factor=max(1, info.sample_rate // self.cfg.audio.voice.rate),
            channels=info.channels,
        )
        last_ts: float | None = None
        async for frame in self.adapter.frames():
            if self._stop.is_set():
                break
            t0 = time.perf_counter()
            self.frames_in += 1
            self.audio_s += frame.duration_ms / 1000.0
            if last_ts is not None:
                self.arrivals.add(frame.ts_ms - last_ts)
            last_ts = frame.ts_ms
            pcm = self._frame_to_voice(frame, decimator)
            if self.probe is not None and pcm:
                for _ in self.probe.accept(pcm, ts_ms=frame.ts_ms, rms_db=self.voice_rms_db):
                    # Counted so the console can show "the probe heard it, production did
                    # not" - that gap is what a threshold change fixes; both missing means
                    # the audio never got there.
                    self.probe_hits += 1
            if self.wake is not None and pcm:
                for hit in self.wake.accept(pcm, ts_ms=frame.ts_ms):
                    self.wake_count += 1
                    await self._on_wake(hit, writer, connected)
            if pcm:
                # After the wake check: the frame that fired the wake is replayed from the
                # ring, and feeding it again here would duplicate 20 ms inside the first
                # word - exactly where the replay exists to protect the onset.
                self._keep_preroll(pcm, frame.ts_ms)
            if self.gate is not None and pcm:
                await self._feed_gate(pcm, frame.ts_ms, writer, connected)
            for vendor in self._drain_signals():
                if connected.is_set():
                    try:
                        writer.write(encode_json(VendorSignalMsg(
                            kind=vendor.kind, text=vendor.text, ts_ms=float(vendor.ts)
                        )))
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        connected.clear()
            if not connected.is_set():
                self.no_consumer += 1
            elif not self.ring.push(frame.seq, now_ms(), pcm):
                self.frames_dropped += 1
            self.cpu_s += time.perf_counter() - t0

    def _drain_signals(self) -> list:
        """Vendor events the adapter saw (or none, for adapters that cannot report them)."""
        drain = getattr(self.adapter, "drain_signals", None)
        return list(drain()) if callable(drain) else []

    # ---------------------------------------------------- wake, preroll, ack
    def _keep_preroll(self, pcm: bytes, ts_ms: float) -> None:
        """Hold the last `wake_preroll_ms` of voice-rate audio for the next wake.

        The frame timestamp travels with the block: a replay that stamps every block with
        the wake time erases the timeline the trace needs.
        """
        limit = int(self.cfg.audio.voice.rate * self.cfg.audio.voice.wake_preroll_ms / 1000)
        if limit <= 0:
            return
        self._preroll.append((ts_ms, pcm))
        self._preroll_samples += len(pcm) // 2
        while self._preroll_samples > limit and len(self._preroll) > 1:
            self._preroll_samples -= len(self._preroll.popleft()[1]) // 2

    async def _on_wake(
        self, hit: WakeHit, writer: asyncio.StreamWriter, connected: asyncio.Event
    ) -> None:
        """One KWS hit: cue the speaker, replay what came before, then listen.

        Order is the whole point. The cue is chosen first because its length decides how
        long the gate must stay shut; the pre-wake ring is replayed before the hold starts
        because it is the user's own speech, captured before the microphone went quiet.
        """
        # Barge-in first: a wake while the speaker is still talking means "stop".
        # Everything already handed to the device (up to MAX_LEAD_S) cannot be recalled,
        # and the client's own ALSA buffer sits behind that - the old build measured the
        # same lag and hard-coded it as PLAY_LAG_SECONDS = 0.55.
        # A cue is not an answer. Two wakes 100 ms apart must leave one cue playing, so
        # only a reply or a file counts as "the speaker is talking" for barge purposes.
        barged = self._speak_queued_s > 0.0 or (
            self._playback_task is not None and not self._playback_task.done()
        )
        barged_s = self._speak_queued_s
        if barged:
            self.barges += 1
            self.last_barge_s = barged_s
            log.info("barge: %d wake during playback, dropped %.1f s queued", self.barges, barged_s)
            self._flush_speech()
            # A wake word interrupts speech, not the song: pause it where it is and let
            # the turn's end bring it back (old build: paused_by_barge + auto-resume).
            # The device still has up to a buffer of audio in hand, so it is told to drop
            # it - that is the same stop_play a barge always needed.
            self._pause_for_speech()
            if self.adapter is not None:
                try:
                    await self.adapter.stop_playback(self.cfg.audio.playback.fade_out_ms)
                except Exception as exc:  # noqa: BLE001 - the speaker may be gone
                    log.info("barge: stop failed: %s", exc)

        audio, mode, note = render_ack(self.cfg)
        self.ack_mode_effective = mode
        # Cleared on success: an error that sticks forever makes the console report a
        # problem that was fixed an hour ago.
        self.ack_error = note
        cue_ms = audio.size * 1000.0 / self.cfg.audio.playback.rate if audio.size else 0.0
        # A spoken cue mutes the device microphone; a tone rings out through the device's
        # own 300 ms buffer. Both need more than their nominal length, so the hold carries
        # a floor - the old build used 250 ms for exactly this - and anything arriving
        # meanwhile is buffered, not dropped.
        hold_ms = cue_ms + (float(self.cfg.ack.echo_ignore_ms) if mode == "tts" else ACK_TAIL_MS)

        event = Wake(
            ts_ms=hit.ts_ms,
            keyword=hit.keyword,
            erle_db=self.aec.erle_db if self.aec is not None else None,
            ref_active=bool(self.aec.ref_active) if self.aec is not None else False,
            playback_s=round(self.playback_s, 1),
            voice_rms_db=None if self.voice_rms_db is None else round(self.voice_rms_db, 1),
            ref_rms_db=None if self.ref_rms_db is None else round(self.ref_rms_db, 1),
            ack_mode=mode,
            barge=barged,
            barged_s=round(barged_s, 2),
        )
        self.last_wake = event

        if self.gate is not None:
            if self.gate.armed:
                # A second wake while a turn was open: arm() resets the buffer, so count it
                # instead of letting a whole utterance disappear silently.
                self.gate.interrupted += 1
                log.info("wake while a turn was open: %.0f ms discarded", self.gate.speech_ms)
            self.gate.arm()
            replayed = 0
            for ts, block in list(self._preroll):
                await self._feed_gate(block, ts, writer, connected)
                replayed += len(block) // 2
            self._preroll.clear()
            self._preroll_samples = 0
            self.preroll_replayed_ms = round(replayed * 1000.0 / self.cfg.audio.voice.rate, 1)
            self.preroll_replays += 1
            # From here on it is live audio, and the cue may still be sounding.
            self.gate.hold(hold_ms)

        if audio.size:
            self.ack_attempts += 1
            self._start_ack(audio)

        if connected.is_set():
            try:
                writer.write(encode_json(event))
                await writer.drain()
                log.debug("wake event sent to the control plane")
            except (ConnectionResetError, BrokenPipeError):
                log.warning("wake event NOT sent: the control plane socket is gone")
                connected.clear()
        else:
            log.warning("wake event NOT sent: no control plane connected")

    async def _feed_gate(
        self, pcm: bytes, ts_ms: float, writer: asyncio.StreamWriter, connected: asyncio.Event
    ) -> None:
        gate = self.gate
        if gate is None:
            return
        finished = gate.feed(pcm, ts_ms=ts_ms)
        if finished is None or not connected.is_set():
            return
        utt, ts = finished
        self.utterances += 1
        try:
            writer.write(encode_pcm(FrameType.UTTERANCE_PCM, self.utterances, ts, utt))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            connected.clear()

    def _start_ack(self, audio: np.ndarray) -> None:
        """Fire and forget. Awaiting the cue here would block the frame consumer."""
        self._claim("cue")
        if self._ack_task is not None and not self._ack_task.done():
            # The previous cue belongs to a wake we have already moved past; playing it out
            # would make the hold describe a cue nobody hears.
            self._ack_task.cancel()
            self.ack_skipped += 1
        self._ack_task = asyncio.create_task(self._play_ack(audio), name="ack")

    async def _play_ack(self, audio: np.ndarray) -> None:
        adapter = self.adapter
        if adapter is None:
            return
        started = time.monotonic()
        rate = self.cfg.audio.playback.rate
        step = max(1, rate * PLAY_CHUNK_MS // 1000)
        try:
            for start in range(0, audio.size, step):
                chunk = audio[start : start + step]
                if self._stop.is_set():
                    return
                await adapter.play(
                    np.round(chunk * 32767.0).astype("<i2").tobytes(),
                    sample_rate=rate,
                    channels=1,
                )
                self.playback_s += chunk.size / rate
            # No pacing. A cue is 320 ms and exists to be heard *now*: paced at real time
            # it took 320 ms to arrive, and while the device's buffer was under its 300 ms
            # start threshold it stayed silent - then came out together with the answer
            # (2026-10-22). Bursting fills the buffer in milliseconds.
            self.ack_plays += 1
            # The cue is the first thing the user hears; if it is sent 300 ms after the
            # wake but sounds two seconds later, that gap is the device, not us.
            log.info("cue: %.0f ms of audio handed over in %.0f ms",
                     audio.size * 1000.0 / rate, (time.monotonic() - started) * 1000)
            # Hold the speaker for the cue's *duration*: releasing when the last byte was
            # sent let a fast reply start writing 100 ms later, and the two came out of
            # the speaker together.
            played_for = time.monotonic() - started
            remaining = audio.size / rate - played_for
            if remaining > 0:
                await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a broken cue must not kill the plane
            self.ack_failed += 1
            self.ack_error = f"{type(exc).__name__}: {exc}"
            log.info("ack: %s", self.ack_error)
        finally:
            self._release("cue")

    async def _send_audio(self, writer: asyncio.StreamWriter, connected: asyncio.Event) -> None:
        """Consume the ring at real time and hand each block to the control plane."""
        period = self.cfg.audio.voice.frame_ms / 1000.0
        while not self._stop.is_set():
            await asyncio.sleep(period)
            if not connected.is_set():
                continue
            item = self.ring.pop()
            if item is None:
                continue
            t0 = time.perf_counter()
            seq, ts_ms, pcm = item
            writer.write(encode_pcm(FrameType.VOICE_PCM, seq, ts_ms, pcm))
            self.frames_out += 1
            self.cpu_s += time.perf_counter() - t0
            try:
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError) as exc:
                log.warning("voice pump stopped: %s", exc)
                connected.clear()
                return

    # -------------------------------------------------------------- playback
    def _start_playback(self, msg: PlayFile) -> str:
        if not self._claim("music"):
            # A reply or a cue owns the speaker. Hold one request (not a queue): the user
            # asked for one song, and it starts the moment the answer ends.
            self._pending_play = msg
            log.info("playback queued behind %s: %s", self._owner, msg.path)
            return msg.path
        if self._playback_task is not None and not self._playback_task.done():
            self._playback_task.cancel()
        self.playback_path = msg.path
        self._file_position = 0
        self._file_cfg = (Path(msg.path), msg.loop, msg.gain)
        self._speech_paused = False
        self._playback_task = asyncio.create_task(
            self._play_file(Path(msg.path), msg.loop, msg.gain), name="playback"
        )
        return msg.path

    async def _play_file(self, path: Path, loop: bool, gain: float,
                         offset: int = 0) -> None:
        try:
            pcm, rate = read_wav_mono(path)
        except (OSError, wave.Error) as exc:
            log.warning("playback: cannot read %s: %s", path, exc)
            return
        target = self.cfg.audio.playback.rate
        audio = np.clip(resample(pcm, rate, target) * float(gain), -1.0, 1.0)
        adapter = self.adapter
        if adapter is None:
            return
        if offset:
            offset = int(min(max(offset, 0), max(audio.size - 1, 0)))
            audio = audio[offset:]
            self._file_position = offset
        else:
            audio = fade_in(audio, target)
            self._file_position = 0
        log.info("playback: %s %.1f s at %s Hz (from %.1f s)", path.name,
                 audio.size / target, target, offset / target)
        try:
            await self._stream_file(audio, target, adapter, loop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed speaker must not kill the plane
            self.last_playback_error = f"{type(exc).__name__}: {exc}"
            log.warning("playback failed: %s", self.last_playback_error)
            return
        log.info("playback finished: %s", path.name)

    async def _stream_file(self, audio: np.ndarray, target: int, adapter: DeviceAdapter,
                           loop: bool) -> None:
        step = max(1, target * PLAY_CHUNK_MS // 1000)
        pacer = Pacer()
        while not self._stop.is_set():
            for start in range(0, audio.size, step):
                if self._stop.is_set():
                    return
                chunk = audio[start:start + step]
                await adapter.play(
                    np.round(chunk * 32767.0).astype("<i2").tobytes(),
                    sample_rate=target,
                    channels=1,
                )
                self.playback_s += chunk.size / target
                self._file_position += chunk.size
                if loop and audio.size:
                    # A looping file's position is measured against the track, not a
                    # running total: resuming a 20 s snippet "at 37 s" played nothing.
                    self._file_position %= audio.size
                # Same prebuffer as replies: a file paced at exactly 1.0x starves the
                # device buffer, and a song is long enough for that to be audible.
                now = time.monotonic()
                wait = pacer.sleep_for(now, chunk.size / target)
                self.playback_lead_s = pacer.lead
                await asyncio.sleep(wait)
            if not loop:
                break

    async def _speak_loop(self) -> None:
        """Play the reply PCM the control plane pushes.

        PLAY_PCM carries mono PCM at the *voice* rate - every TTS provider is built with
        `voice.rate`. Handing that straight to a 48 kHz device plays each answer three
        times too fast: byte counts, pacing stats and tests all look right, and only the
        ear catches it. Resampled here, once, and paced by the resulting duration.
        """
        target = self.cfg.audio.playback.rate
        source = self.cfg.audio.voice.rate
        pacer = Pacer()
        while not self._stop.is_set():
            try:
                chunk = await asyncio.wait_for(self._speak_queue.get(), timeout=0.5)
            except TimeoutError:
                # Nothing more is coming: that is the end of the answer, so hand the song
                # back. Checking only after a send missed the case where the last chunk
                # was already out and the loop sat waiting.
                if self._speak_queue.empty():
                    self._reply_open = False
                    self._release("dialogue")
                    self._resume_after_speech()
                    # The player is deliberately left alone here: the next reply closes
                    # it and opens a fresh one (see the first-chunk branch below). Closing
                    # it now would cut the tail still playing out of the device buffer.
                continue
            if chunk is None:
                continue
            if isinstance(chunk, tuple):
                chunk, segment = chunk
            else:  # a raw block: treat it as a sentence start
                segment = True
            # A reply owns the device stream while it speaks; the song waits where it is.
            # A cue in flight is 320 ms: wait for it rather than cut it, but never wait
            # forever - an owner that is not releasing is a bug, not a reason to go silent.
            waited = 0.0
            while not self._claim("dialogue"):
                await asyncio.sleep(0.02)
                waited += 0.02
                if waited > 2.0:
                    log.warning("output: %s never released in 2 s, taking it", self._owner)
                    self._owner = "dialogue"
                    break
            self._pause_for_speech()
            self._speak_queued_s = max(
                0.0, self._speak_queued_s - len(chunk) / 2 / self.cfg.audio.voice.rate
            )
            adapter = self.adapter
            if adapter is None:
                continue
            audio = resample(
                np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0, source, target
            )
            if segment:
                # Each sentence begins on a hard sample boundary, and the junction between
                # the cached filler and the answer was one too: that step is the click 9哥
                # heard as 「像是叮，也可能是吞音」 at the front of the answer.
                audio = fade_in(audio, target)
                if not self._reply_open:
                    self._reply_open = True
                    # Open a fresh player for every reply, as the old build does (one Sink
                    # per speak_stream). A kept-alive aplay underruns during the silence
                    # before an answer - measured on the device: "underrun!!! (at least
                    # 5373.550 ms long)" exactly at the cue-to-answer gap - and the first
                    # block written into a drained device is eaten by its restart, heard
                    # as a swallowed first syllable plus a click. Closing the player here
                    # makes the first play() below issue start_play, which respawns aplay
                    # and settles 80 ms before the first chunk (0b).
                    await adapter.stop_playback()
                    log.info("reply: first chunk at %d Hz", target)
            pcm = np.round(np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            try:
                await adapter.play(pcm, sample_rate=target, channels=1)
            except Exception as exc:  # noqa: BLE001 - the speaker may be gone
                log.info("speak: %s", exc)
                continue
            seconds = audio.size / target
            self.playback_s += seconds
            # Pace against a deadline: sending costs 1-3 ms per chunk, and adding that to
            # every chunk drifts a 20 s answer half a second past what the device plays.
            # The prebuffer is the lead the device keeps in hand; CATCHUP makes it grow
            # 2 % per chunk so it cannot drain to zero; MAX_LEAD stops a long answer from
            # running so far ahead that a barge-in is heard late.
            now = time.monotonic()
            wait = pacer.sleep_for(now, seconds)
            self.playback_lead_s = pacer.lead
            # Waiting until the buffer would drop to the target, not until it is empty:
            # that wait is the whole fix.
            await asyncio.sleep(wait)
            if self._speak_queued_s <= 0.0 and self._speak_queue.empty():
                # One short grace period: a sentence boundary is not the end of the answer,
                # and resuming the song between two sentences would be worse than a pause.
                await asyncio.sleep(0.4)
                if self._speak_queue.empty():
                    self._resume_after_speech()
        # drain on shutdown
        while not self._speak_queue.empty():
            self._speak_queue.get_nowait()

    def _flush_speech(self) -> None:
        self._release("dialogue")
        self._speak_queued_s = 0.0
        while not self._speak_queue.empty():
            try:
                self._speak_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    # 0 is the most important. A cue is 320 ms and must be heard over anything; a reply
    # preempts music; a reminder waits for the reply and preempts music.
    PRIORITY = {"cue": 0, "dialogue": 1, "reminder": 2, "music": 3}

    def _claim(self, owner: str) -> bool:
        """Take the speaker. True if this owner now holds it (preempting a lower one)."""
        current = self._owner
        if current is not None and self.PRIORITY[owner] > self.PRIORITY[current]:
            return False
        if current is not None and current != owner:
            log.info("output: %s preempts %s", owner, current)
            if current == "music":
                self._pause_for_speech()
        self._owner = owner
        return True

    def _release(self, owner: str) -> None:
        if self._owner == owner:
            self._owner = None
            if self._pending_play is not None:
                pending, self._pending_play = self._pending_play, None
                self._start_playback(pending)

    # A song has hold music on the wire; dropping it loses up to this much audio.
    PLAY_LAG_SECONDS = 0.55

    def _pause_for_speech(self) -> None:
        """Stop feeding the device while a reply speaks, remembering where the song is.

        The old build measured what is already in flight as 0.55 s (aplay pipe plus one
        ALSA buffer); resuming from the last *sent* sample would skip that much, so the
        position is rewound by it, exactly as `rewind()` did there.
        """
        task = self._playback_task
        if task is None or task.done() or self._file_cfg is None:
            return
        target = self.cfg.audio.playback.rate
        self._file_position = max(0, self._file_position - int(self.PLAY_LAG_SECONDS * target))
        task.cancel()
        self._speech_paused = True
        log.info("playback paused for speech at %.1f s", self._file_position / target)

    def _resume_after_speech(self) -> None:
        if not self._speech_paused or self._file_cfg is None:
            return
        path, loop, gain = self._file_cfg
        self._speech_paused = False
        self._playback_task = asyncio.create_task(
            self._play_file(path, loop, gain, offset=self._file_position),
            name="playback",
        )
        log.info("playback resumed after speech at %.1f s",
                 self._file_position / self.cfg.audio.playback.rate)

    async def _stop_playback(self) -> None:
        # A cue is playback too: StopAudio and shutdown must not leave it sounding.
        if self._ack_task is not None and not self._ack_task.done():
            self._ack_task.cancel()
            await asyncio.gather(self._ack_task, return_exceptions=True)
        self._ack_task = None
        if self._playback_task is not None and not self._playback_task.done():
            self._playback_task.cancel()
            try:
                await self._playback_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._playback_task = None
        self._speech_paused = False
        self._file_cfg = None
        if self.adapter is not None:
            try:
                await self.adapter.stop_playback(self.cfg.audio.playback.fade_out_ms)
            except Exception as exc:  # noqa: BLE001 - the speaker may be gone
                log.info("playback: stop failed: %s", exc)

    def metrics(self) -> Metrics:
        snapshot = self._build_metrics()
        self._peak_hold = 0.0
        return snapshot

    def _build_metrics(self) -> Metrics:
        return Metrics(
            uptime_s=round(time.monotonic() - self.started, 1),
            aec_enabled=self.aec is not None,
            aec_erle_db=self.aec.erle_db if self.aec is not None else None,
            aec_channel=self.aec.selected if self.aec is not None else 0,
            ref_active=bool(self.aec.ref_active) if self.aec is not None else False,
            wake_count=self.wake_count,
            kws_enabled=self.wake is not None,
            kws_error=self.kws_error,
            vad_enabled=self.gate is not None,
            utterances=self.utterances,
            gate_armed=bool(self.gate.armed) if self.gate is not None else False,
            gate_discarded=self.gate.discarded if self.gate is not None else 0,
            gate_interrupted=self.gate.interrupted if self.gate is not None else 0,
            speaker_connected=bool(getattr(self.adapter, "health", lambda: {})().get("connected"))
            if self.adapter is not None
            else False,
            playback_s=round(self.playback_s, 1),
            playback_lead_ms=round(self.playback_lead_s * 1000.0, 1),
            device_volume=self.device_volume,
            output_owner=self._owner or "",
            voice_peak_db=(
                None if self._peak_hold <= 1e-6
                else round(20.0 * float(np.log10(self._peak_hold)), 1)
            ),
            barge_count=self.barges,
            probe_hits=self.probe_hits,
            ack_mode=self.ack_mode_effective,
            ack_attempts=self.ack_attempts,
            ack_plays=self.ack_plays,
            ack_skipped=self.ack_skipped,
            ack_failed=self.ack_failed,
            ack_error=self.ack_error,
            preroll_replayed_ms=self.preroll_replayed_ms,
            preroll_replays=self.preroll_replays,
            voice_rms_db=None if self.voice_rms_db is None else round(self.voice_rms_db, 1),
            ref_rms_db=None if self.ref_rms_db is None else round(self.ref_rms_db, 1),
            frames_in=self.frames_in,
            frames_out=self.frames_out,
            frames_dropped=self.frames_dropped,
            no_consumer=self.no_consumer,
            jitter_p50_ms=round(self.arrivals.p50(), 2),
            jitter_p99_ms=round(self.arrivals.p99(), 2),
            cpu_s=round(self.cpu_s, 3),
            audio_s=round(self.audio_s, 2),
            rtf=round(self.cpu_s / self.audio_s, 4) if self.audio_s else 0.0,
        )

    async def _read_device_volume(self) -> None:
        adapter = self.adapter
        read = getattr(adapter, "read_volume", None)
        if read is None:
            return
        try:
            self.device_volume = await read()
        except Exception as exc:  # noqa: BLE001 - a shell hiccup must not touch audio
            log.debug("volume read failed: %s", exc)

    async def _metrics_loop(self, writer: asyncio.StreamWriter, connected: asyncio.Event) -> None:
        ticks = 0
        while not self._stop.is_set():
            await asyncio.sleep(METRICS_EVERY_S)
            ticks += 1
            if ticks % 8 == 1:  # every ~16 s: cheap RPC, and the vendor can change it
                await self._read_device_volume()
            if not connected.is_set():
                continue
            try:
                snapshot = self.metrics()
                # A number nobody can read is not a measurement: every threshold argument
                # so far compared hit levels with nothing. Log the window's peak whenever
                # there was speech in it, so "normal voice" has a value in the log.
                if snapshot.voice_peak_db is not None and snapshot.voice_peak_db > -55.0:
                    log.info("[level] voice peak %.1f dBFS over %.0f s",
                             snapshot.voice_peak_db, METRICS_EVERY_S)
                writer.write(encode_json(snapshot))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                connected.clear()
                return

    # ---------------------------------------------------------------- server
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connected = asyncio.Event()
        tasks: list[asyncio.Task] = []
        try:
            self.adapter = await self._open_adapter()
            info = await self.adapter.probe()
            writer.write(
                encode_json(
                    Ready(
                        pid=os.getpid(),
                        device_id=info.id,
                        capture_rate=info.sample_rate,
                        capture_channels=info.channels,
                        mic_channels=info.mic_channels,
                        reference_channels=info.ref_channels,
                        voice_rate=self.cfg.audio.voice.rate,
                    )
                )
            )
            await writer.drain()
            connected.set()
            tasks = [
                asyncio.create_task(self._capture(connected, writer), name="capture"),
                asyncio.create_task(self._send_audio(writer, connected), name="send-audio"),
                asyncio.create_task(self._metrics_loop(writer, connected), name="metrics"),
                asyncio.create_task(self._speak_loop(), name="speak"),
            ]
            while not self._stop.is_set():
                kind, payload = await read_frame(reader)
                if kind is FrameType.PLAY_PCM:
                    seq, _ts, pcm = decode_pcm(payload)
                    if pcm:
                        seconds = len(pcm) / 2 / self.cfg.audio.voice.rate
                        if self._speak_queued_s + seconds > MAX_SPEAK_QUEUE_S:
                            # Only reachable if a producer ignores SpeakFinished and keeps
                            # pushing: a leak guard, not pacing.
                            log.warning(
                                "speak queue over %.0f s, dropping a chunk", MAX_SPEAK_QUEUE_S
                            )
                        else:
                            self._speak_queued_s += seconds
                            # The ramp belongs only where the control plane declares a
                            # sentence start (SpeakStart); everything else is a slice of
                            # one continuous synthesis and must join untouched.
                            self._speak_queue.put_nowait((pcm, self._segment_pending))
                            self._segment_pending = False
                    continue
                if kind is not FrameType.CONTROL:
                    continue
                msg = CONTROL.validate_json(payload)
                if msg.type == "shutdown":
                    break
                if msg.type == "hello":
                    assert isinstance(msg, Hello)
                    # The decimator, the AEC and the gate froze their rate when the adapter
                    # opened; retuning the live config here used to make the preroll window,
                    # the replay length and the gate disagree about what a second is.
                    if msg.voice_rate != self.cfg.audio.voice.rate:
                        log.warning(
                            "control plane asked for %s Hz, the plane runs %s Hz: ignored",
                            msg.voice_rate,
                            self.cfg.audio.voice.rate,
                        )
                elif isinstance(msg, ShellCommand):
                    stdout, error = "", ""
                    try:
                        if self.adapter is None:
                            raise RuntimeError("no device connected")
                        stdout = await self.adapter.shell(msg.script)
                    except Exception as exc:  # noqa: BLE001 - report, never crash the plane
                        error = f"{type(exc).__name__}: {exc}"
                    writer.write(encode_json(ShellResult(
                        request_id=msg.request_id, stdout=stdout, error=error,
                    )))
                    await writer.drain()
                elif isinstance(msg, DeviceVolume):
                    volume, error = None, ""
                    try:
                        if self.adapter is None:
                            raise RuntimeError("no device connected")
                        services = self.adapter.services
                        if msg.action == "set":
                            if msg.value is None:
                                raise ValueError("set needs a value")
                            await services.set_volume(int(msg.value))
                        volume = await services.read_volume()
                        self.device_volume = volume
                    except Exception as exc:  # noqa: BLE001 - report, never crash
                        error = f"{type(exc).__name__}: {exc}"
                    writer.write(encode_json(DeviceVolumeResult(volume=volume, error=error)))
                    await writer.drain()
                elif isinstance(msg, QueryMetrics):
                    writer.write(encode_json(self.metrics()))
                    await writer.drain()
                elif isinstance(msg, PlayFile):
                    path = self._start_playback(msg)
                    log.info("playback requested: %s loop=%s gain=%.2f", path, msg.loop, msg.gain)
                elif isinstance(msg, StopAudio):
                    self._flush_speech()
                    await self._stop_playback()
                elif isinstance(msg, SpeakStart):
                    self._segment_pending = True
                elif isinstance(msg, SpeakFinished):
                    log.info("speak finished, %s chunks still queued (%.1f s of audio)",
                             self._speak_queue.qsize(), self._speak_queued_s)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 - reported to the client, then the process exits
            try:
                writer.write(encode_json(Error(message=str(exc))))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
            raise
        finally:
            self._stop.set()
            await self._stop_playback()
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if self.wake is not None:
                self.wake.close()
                self.wake = None
            if self.aec is not None:
                self.aec.close()
                self.aec = None
            if self.adapter is not None:
                await self.adapter.close()
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def serve(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.handle, path=str(self.socket_path))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:  # pragma: no cover - non-POSIX
                pass
        async with server:
            await self._stop.wait()
        self.socket_path.unlink(missing_ok=True)


def default_socket_path() -> Path:
    env = os.environ.get("HOUSE_EAR_AUDIO_SOCK")
    if env:
        return resolve_socket_path(Path(env))
    return resolve_socket_path(Path.home() / ".local" / "state" / "house-ear" / "audio.sock")


def resolve_socket_path(path: Path) -> Path:
    """Keep the path inside the platform's AF_UNIX limit (104 bytes on macOS).

    A deep `system.data_dir` would otherwise fail at bind time with a bare
    "AF_UNIX path too long". The short path is derived from the requested one, so
    both sides of the boundary resolve the same name without extra configuration.
    """
    text = str(path)
    if len(text.encode()) <= 96:
        return path
    digest = hashlib.sha1(text.encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"house-ear-{digest}.sock"


def build_plane(cfg: Config, device_id: str, socket_path: Path | None = None) -> AudioPlane:
    return AudioPlane(cfg, device_id, resolve_socket_path(socket_path or default_socket_path()))


def _configure_logging(level: str) -> None:
    """The plane is a separate process with its own stderr: without this, `system.log_level`
    does nothing and every gate/playback diagnostic is invisible."""
    setup_logging(level)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="house-ear-audio", description="house-ear audio plane")
    parser.add_argument("--config", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--socket", default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    _configure_logging(cfg.system.log_level)
    plane = build_plane(cfg, args.device, Path(args.socket) if args.socket else None)
    try:
        asyncio.run(plane.serve())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
