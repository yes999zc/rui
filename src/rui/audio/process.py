"""Audio plane: AEC, KWS, VAD and playback, in their own process.

Everything here is driven by the frames an adapter hands over. The plane never reads
`audio.capture` to decide a layout - a frame says its own `channels`, `sample_rate` and
`reference_channels`, so an array speaker (48 kHz / 8 ch / 6 mic + 2 ref) and a future ESP32
(16 kHz / 1 ch / no reference) run through the same code path with different adapters.
Config values are expectations the doctor checks, not assumptions the plane bakes in.

Pipeline today: decimate to the voice rate -> echo-cancel each mic against the reference
-> emit the channel that survived best -> hand one mono stream to the control plane.
Playback (the debug wav loop, CLI only) travels the other way through the adapter.

Run standalone:
    python -m rui.audio.process --config config.yaml --device living
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import logging
import math
import os
import signal
import sys
import tempfile
import time
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import secrets
from ..config import Config, load_config
from ..config.schema import (
    device_audio_defaults,
    effective_ack_echo_ignore_ms,
    effective_audio,
)
from ..device import build_adapters
from ..device.contract import (
    AudioFrame,
    Capability,
    DeviceAdapter,
    DeviceInfo,
    DeviceWake,
    effective_capabilities,
    wake_validation_error,
)
from ..env import get as env_get
from ..log_setup import setup_logging
from ..trace import now_ms
from .ack import render_cached as render_ack
from .aec import AecChain, AecUnavailable
from .decimate import Decimator
from .kws import KwsUnavailable, WakeHit, WakeProbe, WakeWord
from .probe import safe_probe
from .protocol import (
    CONTROL,
    DeviceVolume,
    DeviceVolumeResult,
    DeviceWakeMsg,
    Error,
    FrameType,
    Hello,
    LedState,
    Metrics,
    PauseMusic,
    PlayFile,
    PlayFinished,
    QueryMetrics,
    Ready,
    ReplyFinished,
    ResumeMusic,
    ShellCommand,
    ShellResult,
    SpeakFinished,
    SpeakStart,
    StopAudio,
    VendorSignalMsg,
    Wake,
    WakeAdmit,
    WakeCandidate,
    WakeIdle,
    WakeReject,
    decode_pcm,
    encode_json,
    encode_pcm,
    read_frame,
)
from .resample import resample
from .ring import ArrivalStats, PcmRing
from .vad import GateConfig, UtteranceGate, VadUnavailable
from .volume_keys import VOLUME_KEY_GRACE_S, volume_key_target
from .wav import read_wav_mono

METRICS_EVERY_S = 2.0
# The [level] line used to be logged on every metrics tick, i.e. every 2 s - the audit
# called it log noise (P2). Keep the measurement (it is how a threshold change is judged)
# but one line per 30 s: the console's log filter still keys on "voice peak".
LEVEL_LOG_EVERY = 15
# Volume reconciliation tolerance, in low-level mixer steps (0-255, the unit of
# `DeviceServices.set_volume`/`read_volume`). The vendor's read-back is quantised: a
# 219 write can report back as 218/220, and the old exact comparison then rewrote the
# same level every metrics cadence (~16 s) plus a `volume: reconciled` line each time
# (d.7, measured 2026-10-28). One step is ~0.4 % of full scale, so it cannot hide a
# drift a person would hear; a new target (config change / `volume.set`) is still
# written immediately because this tolerance only applies to the read-back check.
VOLUME_RECONCILE_TOLERANCE = 1
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
# P2.9: how long a reply waits for `audio.playback.prebuffer_ms` of audio before it
# starts anyway. The wait is the prebuffer itself plus this margin: long enough to let a
# producer deliver the second batch the issue measured (~400 ms), short enough that a
# short reply is never held hostage by a prebuffer it cannot fill. A config prebuffer of
# 0 disables the wait entirely.
PREBUFFER_TIMEOUT_EXTRA_S = 0.25
PREBUFFER_POLL_S = 0.01
# --- Socket & Plug v1: device_wake policy (docs/socket-plug-v1.md §5) ----------------
# Same event (or a ts_ms within this window of the last accepted one) is a duplicate:
# a device model can fire twice on one phrase while its own debounce settles.
DEVICE_WAKE_DEBOUNCE_MS = 500.0
# ts_ms shares AudioFrame's monotonic origin. An event older than this is a late
# delivery; one from the future means the adapter's clock is wrong. Neither can open a
# turn, and neither updates the debounce clock (a bad event must not block a good one).
DEVICE_WAKE_STALE_MS = 1500.0
DEVICE_WAKE_FUTURE_MS = 500.0
# Single-device rate limit: 5 accepted events per 10 s. Exceeding it drops without
# falling back to KWS - a fallback here could produce two turns for one phrase.
DEVICE_WAKE_RATE_MAX = 5
DEVICE_WAKE_RATE_WINDOW_S = 10.0
# Ten invalid events in a row from an adapter means its mapping is wrong for this
# session; stop polling and let KWS carry the room (a restart retries).
DEVICE_WAKE_MAX_INVALID = 10
# A drain_wakes() that raises is contained, but a defective adapter should not cost a
# log line (or a validation attempt) every 20 ms.
DEVICE_WAKE_ERROR_LOG_S = 30.0
DEVICE_WAKE_MAX_CONSECUTIVE_ERRORS = 5
# Cold start: the first probe runs before an inbound device has dialled in, so a plug
# whose probe() only reports device_wake once the link is up (dot2 waits for the device)
# would keep RUI on its own KWS for the whole process lifetime. While the manifest says
# device_wake is wanted, re-probe a bounded number of times in the background; the first
# probe that verifies it arms the v1 path, a spent budget leaves KWS untouched.
DEVICE_WAKE_REPROBE_S = 5.0
DEVICE_WAKE_REPROBE_MAX = 12
# P2-1: a broken adapter can return a list of 100 k events (or an endless iterator) from
# one drain_wakes()/drain_signals() call. Only this many are acted on per frame tick; the
# overflow is counted and logged (once per interval), and the frame loop stays on time.
MAX_DRAIN_EVENTS = 64
# For a non-sequence iterator there is no length to truncate against; consume at most this
# many items so an endless generator cannot hang capture. The rest is counted as dropped.
MAX_DRAIN_SCAN = 2 * MAX_DRAIN_EVENTS
DRAIN_DROP_LOG_S = 30.0
# A vendor signal's text is diagnostic; 512 chars is what the log line already shows and
# keeps a chatty adapter from pushing a 64 KiB control frame through the socket (P2-8).
MAX_SIGNAL_TEXT = 512
# P1-2/P1-3: an adapter whose frames()/probe()/drain_signals() fails must not leave a
# "running but deaf" plane. Capture retries a bounded number of times with backoff; when
# that budget is spent the plane reports an Error and closes the control socket so the
# supervisor rebuilds it (a fresh adapter/process is the only reliable reopen for an
# inbound device that lost its socket).
CAPTURE_MAX_RESTARTS = 3
CAPTURE_RESTART_BACKOFF_S = 0.25
CAPTURE_RESTART_BACKOFF_MAX_S = 2.0
CAPTURE_ERROR_LOG_S = 30.0
# A lead that goes negative by less than this is scheduler jitter (the loop wakes a few
# ms late, the device still has audio), not an audible underrun. The issue's gap is
# ~400 ms, an order of magnitude above the floor.
PLAYBACK_GAP_MIN_MS = 20.0
# A producer that ignores SpeakFinished cannot grow the queue without bound.
MAX_SPEAK_QUEUE_S = 120.0
# 12 ms of ramp at the start of playback. Speech that begins on a hard sample boundary
# clicks, and on this speaker a click reads as a stray 「叮」 at the front of the answer.
# 12 ms is short enough to be inaudible as a fade and long enough to remove the step.
FADE_IN_MS = 12
# A turn that ends without a reply (a failed LM round, a dead tool) still gives the
# music back after this long - the old build's silent-wake watchdog used 4 s for the
# same "do not keep the song down forever" rule. 4 s was too short for a slow tool:
# measured 08:41:57, a weather lookup ran 9 s and the song came back mid-turn.
# 12 s covers a slow tool call; a genuinely dead turn gives the music back by then.
NO_REPLY_RESUME_S = 12.0
# While a conversation is open the song stays down: a follow-up window is armed
# right after every reply, and music may only return once that window has closed.
# Without the hold the reply drained first and the song blipped back in between
# turns (measured 08:30-08:35, "对话间隙音乐出声音").


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


class Credit:
    """How far ahead of the speaker playback may run, counted in blocks.

    ``sent - acked`` is the queue depth from the device's point of view: the client
    reports every block it has handed to aplay, so this follows the speaker's own clock
    instead of ours. The wall-clock Pacer kept a fixed *duration* ahead and ignored the
    device; when the device's clock ran a little fast that duration became a full pipe,
    and a write that blocks past its timeout is dropped - heard as roughness. The old
    deployment paced on exactly this number (window=2) and measured the difference:
    11 blocked writes in 14 s before, none after.
    """

    def __init__(self, window: int = 2, timeout: float = 1.5) -> None:
        self.window = window
        self.timeout = timeout
        self.sent = 0
        self.acked = 0
        self.dead = False
        self.lead = 0.0

    def reset(self) -> None:
        self.sent = 0
        self.acked = 0
        self.dead = False
        self.lead = 0.0

    def ack(self, n: int) -> None:
        # Clamped to what was sent: a report from the previous player (arriving after a
        # reset) must not open a window that does not exist.
        self.acked = max(self.acked, min(int(n), self.sent))

    def note_sent(self, seconds: float) -> None:
        self.sent += 1
        self.lead = (self.sent - self.acked) * seconds

    async def wait(self, adapter: DeviceAdapter, seconds: float) -> bool:
        """Block until one more block may go out. False when the device stopped reporting."""
        if self.window <= 0:
            # No leak bound: the clock alone paces, for deployments that want the
            # shallowest possible device buffer.
            self.lead = (self.sent - self.acked) * seconds
            return True
        while not self.dead and self.sent - self.acked >= self.window:
            got = await adapter.wait_consumed(self.acked, self.timeout)
            if got <= self.acked:
                # One timeout is enough: waiting 1.5 s per block would make playback
                # fifteen times slower in silence. Fall back to the clock for this
                # player instead (a device that cannot report does the same from the start).
                self.dead = True
                log.info("credit: no play-head report for %.1f s, pacing by the clock",
                         self.timeout)
                return False
            self.ack(got)
        self.lead = (self.sent - self.acked) * seconds
        return not self.dead


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
# How long after the cue is *handed over* a device can still start playing it. Measured
# on the Dot 2 (2026-10-28 11:01:30): 460 ms handed over at 30.495, audible from ~31.24,
# i.e. 740-850 ms later. The gate's `ack_echo_ignore_ms` is a post-playback window, so the
# hold has to carry this lead before the cue's own length: otherwise the "ignore" clock
# starts while the device is still playing and the cue's echo tail falls outside it.
# One conservative value for *every* device (including the 16 kHz device, whose
# measured lead is lower): the cost is +850 ms of buffered turn delay and +850 ms of
# device-barge suppression on each wake. Buffered audio is delayed, not dropped, and
# only a device whose own lead exceeds a measured 850 ms would need more.
ACK_PLAYOUT_LEAD_MS = 850
log = logging.getLogger(__name__)


def _dbfs(samples) -> float:
    """Level of one block in dBFS. Floored at -100 so it survives a JSON round trip."""
    peak = float(np.sqrt(np.mean(np.square(np.asarray(samples, dtype=np.float64)))))
    return 20.0 * math.log10(max(peak, 1e-5))


@dataclass(frozen=True)
class _VendorEvent:
    """One validated, bounded `drain_signals()` item.

    The adapter's own object is not passed on: it may be malformed or mutable, and the
    capture loop only needs these three fields.
    """

    kind: str
    text: str
    ts: float


@dataclass(frozen=True)
class _WakePolicy:
    """What the configured device's plug allows for device_wake (P1-5/P1-6/P2-4).

    ``present`` distinguishes "no manifest at all" (the documented dev case, where the
    probe is authoritative) from "a manifest was found/requested". For the latter the
    manifest is the upper bound: its capabilities are intersected with the probe's,
    ``wake.barge_via_device`` is ANDed with the capability, and a third-party plug is
    treated as sidecar-equivalent for wake (no standardised wake RPC).
    """

    source: str | None = None        # "device" | "rui" | "sidecar" | None (no manifest)
    present: bool = False            # a manifest resolved (or an explicit plug failed)
    trusted: bool = True             # builtin/trusted, or no manifest (dev adapter)
    barge_enabled: bool = False      # manifest `wake.barge_via_device`
    capabilities: frozenset[Capability] = frozenset()
    error: str = ""


def _capability_set(values: object) -> frozenset[Capability]:
    """Manifest capability strings -> the contract enum; unknown values are ignored."""
    out: set[Capability] = set()
    for value in values or ():
        try:
            out.add(Capability(str(value)))
        except ValueError:
            continue
    return frozenset(out)


def _bounded_drain(pending: object, cap: int) -> tuple[list, int]:
    """At most ``cap`` items from an adapter drain, plus how many were dropped.

    For a sequence the newest ``cap`` win (an overflow means the adapter produced a
    backlog; the freshest events describe the room now). A generator cannot be sliced
    without consuming it, so at most ``MAX_DRAIN_SCAN`` items are read and the excess is
    reported as dropped - enough to keep a hostile adaptive iterator from hanging the
    frame loop.
    """
    if pending is None:
        return [], 0
    if isinstance(pending, (list, tuple)):
        if len(pending) <= cap:
            return list(pending), 0
        return list(pending[-cap:]), len(pending) - cap
    try:
        iterator = iter(pending)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(
            f"drain returned {type(pending).__name__}, which is not iterable"
        ) from exc
    scanned = list(itertools.islice(iterator, max(cap + 1, MAX_DRAIN_SCAN)))
    if len(scanned) <= cap:
        return scanned, 0
    # The exact overflow of an iterator is unknowable without draining it; one is the
    # honest lower bound, and the counter still makes the defect visible.
    return scanned[:cap], len(scanned) - cap



class AudioPlane:
    def __init__(self, cfg: Config, device_id: str, socket_path: Path) -> None:
        self.cfg = cfg
        self.device_id = device_id
        self.socket_path = socket_path
        # Config v3 (Phase 1) resolved once: this device's overrides on top of the
        # global audio block. 0.6 adds the plug manifest's `audio_defaults` underneath
        # both (device default < global config < devices[].audio), resolved once here
        # because the ack window needs the same layer and a plug catalog scan is not
        # something to repeat per turn.
        device_cfg = next((d for d in cfg.devices if d.id == device_id), None)
        self.device_cfg = device_cfg
        self.audio_defaults = device_audio_defaults(device_cfg, cfg.system.data_dir)
        self.effective = effective_audio(
            cfg.audio, device_cfg.audio if device_cfg else None, self.audio_defaults
        )
        # Phase 2: with multi_device.enabled the plane only reports candidates and waits
        # for the control plane's admit/reject; it never arms its own gate or barges on
        # its own. With it off (the default) the old local path is byte-identical.
        self.arbitrate = bool(cfg.multi_device.enabled)
        self._pending_wakes: dict[str, asyncio.Future[tuple[bool, str]]] = {}
        self._active_wake_id = ""
        self._wake_seq = 0
        self.wake_candidates = 0
        self.wake_admitted = 0
        self.wake_admit_timeouts = 0
        self.wake_admit_latency_ms: float | None = None
        self.wake_rejections: dict[str, int] = {}
        self._admit_timeout_s = (
            cfg.multi_device.arbitration.candidate_merge_window_ms + 500
        ) / 1000.0
        # Post-playback mute (device-AEC plugs): frames are dropped from here on.
        self._mic_mute_until = 0.0
        # Physical mute (task-18): device-sovereign state reported by drain_signals()
        # as mute/unmute. Muted suppresses the wake cue but never the turn: the user
        # still gets transcribed, and the ring stays red until it is unmuted.
        self.device_muted = False
        self._muted_known = False
        self.key_events: dict[str, int] = {}
        self.last_key: tuple[str, str] | None = None
        # task-19: monotonic time of the last physical volume key. `_reconcile_volume`
        # stays silent for VOLUME_KEY_GRACE_S afterwards, while the device's own ramp
        # (and the bridge's reported level) settle; after that the target is the new
        # config value the key wrote back, never the pre-key one.
        self.last_user_volume_at: float | None = None
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
        # Socket & Plug v1 (§4.3/§5). `device_wake_active` is resolved from
        # probe().capabilities + a real drain_wakes(); without both the plane is exactly
        # the 0.2 plane. `device_wake_barge_active` additionally needs barge_via_device.
        self.device_wake_active = False
        self.device_wake_barge_active = False
        self.device_wake_configured = False
        self.device_wakes = 0
        self.device_wake_turns = 0
        self.device_wake_barges = 0
        self.device_wake_debounced = 0
        self.device_wake_invalid = 0
        self.device_wake_suppressed = 0
        self.device_wake_barge_suppressed = 0
        self.device_wake_rate_limited = 0
        self.device_wake_conflicts = 0
        self.device_wake_interrupts = 0
        self.device_wake_no_vad = 0
        self.device_wake_fallbacks = 0
        self.device_wake_degraded = False
        # Cold-start re-evaluation: the manifest wants device_wake but the first probe
        # ran before the device connected. Bounded background re-probes (see below).
        self.device_wake_reeval_wanted = False
        self.device_wake_reprobes = 0
        self._device_wake_reeval_at = 0.0
        self._device_wake_reeval_task: asyncio.Task[None] | None = None
        # P2-1: events dropped because one drain exceeded MAX_DRAIN_EVENTS.
        self.device_wake_dropped = 0
        # P1-7: a device wake whose replay anchor was older than the retained history.
        self.device_wake_replay_missed = 0
        self.device_wake_last_score: float | None = None
        self._device_wake_invalid_streak = 0
        self._device_wake_error_streak = 0
        self._device_wake_error_logged = 0.0
        self._drain_drop_logged = 0.0
        self._last_accepted_wake_ts: float | None = None
        self._device_wake_accept_times: deque[float] = deque()
        # P1-2/P1-3: capture supervision. `capture_degraded` means frames are (or just
        # were) failing to arrive; `capture_last_error` is why. A successful frame clears
        # it, so the console never reports an old failure as current.
        self.capture_degraded = False
        self.capture_errors = 0
        self.capture_restarts = 0
        self.capture_last_error = ""
        self._capture_fail_streak = 0
        self._capture_error_logged = 0.0
        # A side task (metrics/send-audio/speak) that dies while the socket is open is a
        # partial outage; this makes it visible instead of an unretrieved exception.
        self.task_errors = 0
        self.task_last_error = ""
        # P1-2: drain_signals() failures/oversize are contained like drain_wakes().
        self.signal_errors = 0
        self.signal_dropped = 0
        self.signal_invalid = 0
        self.signal_last_error = ""
        self._signal_error_logged = 0.0
        # Wall-clock end of the cue/echo hold opened by the last accepted wake: a barge
        # hint inside this window is our own audio, not a person (§5 "ACK mute/gate hold").
        self._wake_hold_until = 0.0
        # Local mirror of the control plane's VendorWindow: a vendor wake puts the room
        # in "we are quiet" for `session.vendor_quiet_s`; our own accepted wake clears it.
        self._vendor_quiet_until = 0.0
        self.gate: UtteranceGate | None = None
        self.vad_error = ""
        self.utterances = 0
        # Unbounded on purpose. The control plane hands a whole sentence over in a burst
        # (TTS is much faster than real time), so a chunk-count limit truncates the
        # sentence: the log said "speak queue full, dropping a chunk" a dozen times a
        # turn and the listener heard the gaps. The budget below is seconds of audio,
        # which is the thing that actually has a right answer.
        self._speak_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        # Device-reported pacing (0c): the plane never runs more than `window` blocks
        # ahead of the newest report. Shared by speech and file playback - one speaker,
        # one owner, so only one of them is writing at a time.
        self._credit = Credit(window=cfg.audio.playback.credit_window)
        self._credit_generation = 0
        # Armed by SpeakStart, consumed by the next PLAY_PCM frame: the one declared
        # sentence boundary in the current reply. The old source of truth - seq == 0 -
        # fired on every speak() call, i.e. on every TTS chunk, and faded mid-word.
        self._segment_pending = False
        # Set by ReplyFinished: the turn is over, not merely between TTS chunks.
        self._reply_finished = False
        self._speak_queued_s = 0.0
        # Set by an accepted wake: PLAY_PCM arriving before this turn's own utterance
        # belongs to the previous turn (e.g. an idle goodbye still being streamed).
        # It is dropped instead of played, so its echo cannot reach the gate/ASR
        # (2026-10-26 07:12: the farewell's own words became the user's turn).
        self._stale_speech_blocked = False
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
        # P2.9: audible gaps, cumulative for metrics and per reply for the turn trace/log.
        self.playback_gap_count = 0
        self.playback_max_gap_ms = 0.0
        self._reply_gap_count = 0
        self._reply_max_gap_ms = 0.0
        # When the audio already sent will run out, if the reply queue was empty at the
        # last send. None = the queue was not starved, or the reply is not active.
        self._starvation_due: float | None = None
        # True once a PLAY_PCM burst is enqueued and no SpeakFinished has arrived since:
        # an empty queue then means "producer still working", not "the answer ended".
        self._speak_finished = True
        # Incremented by _flush_speech so a chunk already pulled off the queue cannot
        # still reach the speaker after a barge (the start gate widens that window).
        self._speak_generation = 0
        # P0b-A: last known bridge-side link state. The purge belongs to the
        # live -> lost transition, not to the state: a plane started while the speaker is
        # still connecting must not treat every metrics tick as a fresh loss.
        self._speaker_link_up: bool | None = None
        # Whether any audio has been handed to the device since the last explicit stop.
        # The pre-start stop exists to restart a drained aplay; when this is False the
        # device player is already stopped, so the control message is redundant - and on
        # adapters with split control/data sockets it is also a race with the first PCM.
        self._device_audio_pending = False
        # The speaker's own mixer value, read over the shell channel. Loudness on this box
        # belongs to the vendor's `mysoftvol`; every ERLE or barge number is only
        # comparable when this value is known.
        self.device_volume: int | None = None
        self.barges = 0
        self.last_barge_s = 0.0
        # Server-side barge latency: wake handled -> the device was told to stop and the
        # queued reply was flushed. The trace's `silence_after_ms` had no production
        # number (audit P1); this is that number, logged on every barge. The device's own
        # ~300 ms ALSA buffer is not in it - the server cannot see inside the box.
        self.last_barge_stop_ms: float | None = None
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
        # The user said 「暂停」: parked by hand, so no automatic resume path may lift
        # it - only ResumeMusic or a fresh play_file (old build: pause(for_speech=False),
        # the one pause that survived every hook).
        self._paused_explicit = False
        # An utterance fired since the music was paused for a wake, and no reply has
        # finished since. The turn is alive between end-of-speech and the answer
        # (ASR + LLM + tools), and the music must stay down through it - the gate is
        # already disarmed there, so "gate closed" alone cannot tell this window from
        # the end of a turn (measured 21:07: the song blipped back in for 0.9 s
        # between the question and 现在是晚上9点07分).
        self._turn_pending = False
        self._turn_pending_ts = 0.0
        # Wall-clock deadline of a follow-up window that is holding the song down
        # (None = not holding). A deadline, not "gate is closed": the reply's own
        # first block disarms the gate, so gate state alone would release the song
        # before the window ever armed.
        self._music_hold_until: float | None = None
        # Per-turn override of the music window (ReplyFinished.hold_music_s).
        self._reply_hold: float | None = None
        # The control-plane socket, for plane -> control events (PlayFinished).
        self._ctl_writer: asyncio.StreamWriter | None = None
        self._ctl_connected: asyncio.Event | None = None
        self._reply_open = False
        self.started = time.monotonic()
        self._stop = asyncio.Event()
        # Pre-wake audio. The streaming KWS reports a hit ~0.5 s after the wake word ends,
        # so "你好小瑞，现在几点" said in one breath loses 现在几点 unless the ring is
        # replayed into the gate. Kept as (monotonic ts_ms, int16 voice-rate block),
        # oldest first. The history is longer than `wake_preroll_ms` because a device
        # wake may be delivered up to DEVICE_WAKE_STALE_MS late (P1-7); the replay window
        # is selected by timestamp from this deque.
        self._preroll: deque[tuple[float, bytes]] = deque()
        self._preroll_samples = 0
        self.preroll_replayed_ms = 0.0
        self.preroll_replays = 0
        # The follow-up gate opens one second after the device finishes the reply; the
        # old build kept that echo-tail window and replayed it. Without this, an answer
        # spoken immediately after the reply lost its first syllable ("紧着说明天呢"
        # heard as nothing).
        self._followup_tail: deque[tuple[float, bytes]] = deque()
        self._followup_samples = 0
        self._followup_capture = False
        self._followup_start = 0.0
        self._followup_end = 0.0
        self._followup_replayed_ms = 0.0
        # The newest LED paint owns the ring (task-17). Every LedState used to become
        # its own fire-and-forget task, so a slow "listening" animation could finish
        # after a later "off" and leave the ring green after the session closed.
        self._led_task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected: asyncio.Event | None = None
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
        aec = self.effective.aec
        if aec.who == "device":
            # The plug already cancelled (device-side AEC, mono uplink). Running a second
            # canceller here would cascade; `who=device` is the explicit hand-off.
            self.aec_error = "aec.who=device: the plug cancels, RUI AEC is off"
            log.info("aec off: %s", self.aec_error)
            return
        if not aec.enabled:
            return
        if ref_channels < 1:
            self.aec_error = "device has no reference channel: AEC and barge-in unavailable"
            log.warning("aec off: %s", self.aec_error)
            return
        try:
            self.aec = AecChain(
                channels=mic_channels,
                rate=self.effective.voice.rate,
                taps=aec.filter_len,
                cancel=aec.cancel,
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
                num_threads=voice.kws_num_threads,
                rate=voice.rate,
            )
            if voice.kws_probe_file:
                # Its own decoder state: one stream cannot be fed to two spotters.
                self.probe = WakeProbe(
                    model_dir=voice.kws_model_dir,
                    keywords_file=voice.kws_probe_file,
                    chunk=voice.kws_chunk,
                    num_threads=voice.kws_num_threads,
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

    # ------------------------------------------------------ Phase 2 arbitration
    @property
    def _post_playback_mute_s(self) -> float:
        return self.effective.voice.post_playback_mute_ms / 1000.0

    @property
    def ack_echo_ignore_ms(self) -> int:
        """Spoken-cue echo window: device default < global ack < device override.

        A device-AEC plug keeps the cue's residual echo on its microphone far longer
        than the reference speaker needs; reading the global value here is what let its own cue open a
        turn (0.5.3 self-hearing fix). 0.6 lets the plug declare its own measured tail.
        """
        return effective_ack_echo_ignore_ms(
            self.cfg.ack, self.device_cfg, self.audio_defaults
        )

    def _ack_gate_windows(self, audio: np.ndarray, mode: str) -> tuple[float, float]:
        """(cue_ms, hold_ms) for this wake's ack, in the gate's audio clock.

        `render_ack` produces the cue at the **global** playback rate; `_play_ack` then
        resamples it for a device that overrides `playback.rate` (the Dot 2 runs 48 kHz).
        Dividing the rendered sample count by the device rate made a 460 ms cue read as
        153 ms there, so the gate skipped 403 ms instead of 710 and replayed the cue's
        tail into ASR (2026-10-28 11:01:30: `cue_gate: cue=[0,460] dropped=[0,403]` ->
        `[seg] 1.18 s` -> turn 「在呢？」, 4.18 s wasted). So cue_ms uses the render rate.

        The hold is the post-playback echo window, counted from the cue's **end**: the
        device starts it `ACK_PLAYOUT_LEAD_MS` after the handover, then it sounds for
        cue_ms, then `ack_echo_ignore_ms` keeps the gate shut. A tone keeps its 250 ms
        floor instead of the spoken-cue window.
        """
        cue_ms = (
            audio.size * 1000.0 / self.cfg.audio.playback.rate if audio.size else 0.0
        )
        tail_ms = float(self.ack_echo_ignore_ms) if mode == "tts" else ACK_TAIL_MS
        if cue_ms <= 0.0:
            return 0.0, tail_ms
        return cue_ms, cue_ms + ACK_PLAYOUT_LEAD_MS + tail_ms

    def _mic_is_muted(self) -> bool:
        """True while a post-playback mute window is open (no VAD/KWS at all)."""
        return self._mic_mute_until > 0.0 and time.monotonic() < self._mic_mute_until

    def _followup_delay_from(self, pacer_due: float) -> float:
        """Drain wait + post-playback mute + the configured follow-up window.

        Starting the mute at the moment the device buffer drains is what keeps barge
        working: during playback the microphone is still live, only the echo tail after
        it is dropped. Without a mute (the default) this is exactly the old delay.
        """
        mute_s = self._post_playback_mute_s
        if mute_s:
            self._mic_mute_until = max(time.monotonic(), pacer_due) + mute_s
        return (
            max(0.0, pacer_due - time.monotonic())
            + mute_s
            + self.effective.voice.followup_delay_ms / 1000.0
        )

    async def _arbitrate_candidate(
        self, *, source: str, keyword: str, barge: bool, playing: bool,
        writer: asyncio.StreamWriter, connected: asyncio.Event,
        score: float | None = None, threshold: float | None = None,
        level: float | None = None, erle_db: float | None = None,
        ref_active: bool = False,
    ) -> bool:
        """Report one candidate and wait for the control plane's verdict.

        Returns True only on `wake_admit`. Everything else (reject, timeout, no control
        plane) returns False and the caller must not arm a gate or speak: a timeout is
        an explicit `admit_timeout`, never a local fallback, because two planes falling
        back to "I'll take it" is exactly the double turn the arbiter exists to stop.
        """
        if not self.arbitrate:
            return True
        if not connected.is_set():
            log.warning("wake candidate dropped: no control plane connected")
            return False
        wake_id = f"{self.device_id}-{time.monotonic_ns()}"
        future: asyncio.Future[tuple[bool, str]] = asyncio.get_running_loop().create_future()
        self._pending_wakes[wake_id] = future
        self.wake_candidates += 1
        started = time.monotonic()
        try:
            writer.write(encode_json(WakeCandidate(
                wake_id=wake_id,
                ts_ms=now_ms(),
                source=source,  # type: ignore[arg-type]
                keyword=keyword,
                score=score,
                threshold=threshold,
                level=level,
                barge=barge,
                playing=playing,
                erle_db=erle_db,
                ref_active=ref_active,
            )))
            await writer.drain()
            admitted, reason = await asyncio.wait_for(future, timeout=self._admit_timeout_s)
            self.wake_admit_latency_ms = round((time.monotonic() - started) * 1000.0, 1)
            if admitted:
                self.wake_admitted += 1
                self._active_wake_id = wake_id
                log.info("wake candidate %s admitted after %.0f ms",
                         wake_id, self.wake_admit_latency_ms)
            else:
                self.wake_rejections[reason] = self.wake_rejections.get(reason, 0) + 1
                log.info("wake candidate %s rejected: %s", wake_id, reason)
            return admitted
        except TimeoutError:
            self.wake_admit_timeouts += 1
            self.wake_rejections["admit_timeout"] = (
                self.wake_rejections.get("admit_timeout", 0) + 1
            )
            log.info("wake candidate %s: no verdict in %.0f ms; dropped",
                     wake_id, self._admit_timeout_s * 1000.0)
            return False
        finally:
            self._pending_wakes.pop(wake_id, None)

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

    async def _capture(
        self,
        connected: asyncio.Event,
        writer: asyncio.StreamWriter,
        info: DeviceInfo | None = None,
    ) -> None:
        """The frame loop, supervised: adapter failures degrade, they never go silent.

        `handle()` already probed the adapter for `Ready`; reuse that answer instead of
        probing twice (P1-3). Any exception out of `frames()` - or a generator that ends
        without a stop - is retried a bounded number of times with backoff. When the
        retry budget is spent the control socket gets an `Error` and is closed: the
        supervisor then rebuilds the plane (P1-2/P1-3) instead of leaving a process that
        looks healthy but hears nothing.
        """
        assert self.adapter is not None
        if info is None:
            result = await safe_probe(self.adapter)
            if not result.ok:
                raise RuntimeError(f"device probe failed ({result.as_error()})")
            info = result.info
        self._writer = writer
        self._connected = connected
        self._build_aec(info.mic_channels, info.ref_channels)
        self._build_kws()
        self._build_vad()
        self._configure_device_wake(info)
        while not self._stop.is_set():
            try:
                await self._capture_frames(info, connected, writer)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - adapter input is untrusted
                if self._stop.is_set():
                    return
                refreshed = await self._recover_capture(exc, writer, info)
                if refreshed is None:
                    return
                info = refreshed
            else:
                if self._stop.is_set():
                    return
                # A generator that simply ends while the plane is supposed to be up is
                # the same deafness as one that raises; retry/surface it the same way.
                refreshed = await self._recover_capture(
                    RuntimeError("adapter.frames() ended without a stop"), writer, info
                )
                if refreshed is None:
                    return
                info = refreshed

    async def _capture_frames(
        self,
        info: DeviceInfo,
        connected: asyncio.Event,
        writer: asyncio.StreamWriter,
    ) -> None:
        decimator = Decimator(
            factor=max(1, info.sample_rate // self.cfg.audio.voice.rate),
            channels=info.channels,
        )
        last_ts: float | None = None
        async for frame in self.adapter.frames():
            if self._stop.is_set():
                return
            self._note_capture_frame()
            if (self.device_wake_reeval_wanted and not self.device_wake_active
                    and not self.device_wake_degraded):
                self._maybe_schedule_device_wake_reeval()
            t0 = time.perf_counter()
            self.frames_in += 1
            self.audio_s += frame.duration_ms / 1000.0
            if last_ts is not None:
                self.arrivals.add(frame.ts_ms - last_ts)
            last_ts = frame.ts_ms
            pcm = self._frame_to_voice(frame, decimator)
            if self._mic_is_muted():
                # Post-playback mute: a device-AEC plug's residual outlives its AEC and
                # VAD/ASR would turn it into a request. Playback itself is over here, so
                # barge (which needs the live mic) is unaffected.
                continue
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
            # v1 §4.3: vendor signals are read first, then device wakes, in the same
            # tick. A vendor wake this tick wins the conflict; the device event is
            # counted and dropped instead of producing a second turn.
            vendor_wake = False
            for vendor in self._drain_signals():
                # task-18: mute is applied before the wake branch below, so a key
                # that arrives in this tick already suppresses this tick's cue.
                self._handle_device_key(vendor)
                if vendor.kind == "wake":
                    vendor_wake = True
                    # Mirror the control plane's VendorWindow locally: a device barge
                    # hint inside it must not stop our playback (§5). Our own accepted
                    # wake clears it in `_begin_wake_turn`.
                    self._vendor_quiet_until = time.monotonic() + max(
                        0.0, self.cfg.session.vendor_quiet_s
                    )
                if connected.is_set():
                    try:
                        writer.write(encode_json(VendorSignalMsg(
                            kind=vendor.kind, text=vendor.text, ts_ms=vendor.ts
                        )))
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        connected.clear()
            if self.device_wake_active:
                await self._drain_device_wakes(
                    vendor_wake=vendor_wake, writer=writer, connected=connected
                )
            if pcm:
                # After the wake check: the frame that fired the wake is replayed from the
                # ring, and feeding it again here would duplicate 20 ms inside the first
                # word - exactly where the replay exists to protect the onset.
                self._keep_preroll(pcm, frame.ts_ms)
            if pcm:
                self._capture_followup_tail(pcm, frame.ts_ms)
            if self.gate is not None and pcm:
                await self._feed_gate(pcm, frame.ts_ms, writer, connected)
            if not connected.is_set():
                self.no_consumer += 1
            elif not self.ring.push(frame.seq, now_ms(), pcm):
                self.frames_dropped += 1
            self.cpu_s += time.perf_counter() - t0

    def _note_capture_frame(self) -> None:
        """A frame arrived: clear a previous capture-degraded state (and log recovery)."""
        if self.capture_degraded:
            log.info("capture recovered after %d failure(s)", self._capture_fail_streak)
        self.capture_degraded = False
        self._capture_fail_streak = 0

    async def _recover_capture(
        self,
        exc: BaseException,
        writer: asyncio.StreamWriter,
        info: DeviceInfo,
    ) -> DeviceInfo | None:
        """Retry capture, or surface the failure and return None when out of budget."""
        self.capture_errors += 1
        self._capture_fail_streak += 1
        self.capture_last_error = f"{type(exc).__name__}: {exc}"[:300]
        now = time.monotonic()
        if now - self._capture_error_logged >= CAPTURE_ERROR_LOG_S:
            self._capture_error_logged = now
            log.warning(
                "capture: %s; retry %d/%d",
                self.capture_last_error, self._capture_fail_streak,
                CAPTURE_MAX_RESTARTS,
            )
        if self._capture_fail_streak > CAPTURE_MAX_RESTARTS:
            self.capture_degraded = True
            self._fail_plane(
                f"capture failed {self._capture_fail_streak} times: "
                f"{self.capture_last_error}",
                writer,
            )
            return None
        self.capture_degraded = True
        delay = min(
            CAPTURE_RESTART_BACKOFF_S * (2 ** (self._capture_fail_streak - 1)),
            CAPTURE_RESTART_BACKOFF_MAX_S,
        )
        await asyncio.sleep(delay)
        if self._stop.is_set():
            return None
        self.capture_restarts += 1
        # Refresh the layout on a retry: a reconnect can change channels/rate. A probe
        # failure is not fatal here - frames() may still work with the old layout.
        result = await safe_probe(self.adapter)
        return result.info if result.ok else info

    def _fail_plane(self, reason: str, writer: asyncio.StreamWriter | None = None) -> None:
        """Surface an unrecoverable plane failure to the control plane, then stop.

        The supervisor treats a closed control socket as "the plane died" and rebuilds
        it; the alternative - a live process with a dead capture task - is the silent
        deafness this fix exists to remove.
        """
        if self._stop.is_set():
            return
        self.capture_degraded = True
        self.capture_last_error = reason[:300]
        log.error("audio plane degraded: %s; stopping so the supervisor rebuilds it", reason)
        target = writer if writer is not None else self._writer
        if target is not None:
            try:
                target.write(encode_json(Error(message=f"audio plane degraded: {reason}")))
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
            close = getattr(target, "close", None)
            if callable(close):
                try:
                    close()
                except (OSError, RuntimeError):
                    pass
        self._stop.set()

    def _watch_task(
        self,
        name: str,
        task: asyncio.Task,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Done-callback supervision for the side tasks (metrics, send-audio, speak).

        A side task that dies while the control socket is open is the same class of
        failure as a dead capture task: the plane keeps answering but some part of the
        pipeline is gone. Report it instead of letting the exception sit unretrieved.
        """

        def _done(finished: asyncio.Task) -> None:
            if finished.cancelled() or self._stop.is_set():
                return
            exc = finished.exception()
            self.task_errors += 1
            detail = (
                f"{type(exc).__name__}: {exc}" if exc is not None
                else "exited unexpectedly"
            )
            self.task_last_error = f"{name}: {detail}"[:300]
            self._fail_plane(f"{name} task {detail}", writer)

        task.add_done_callback(_done)

    def _drain_signals(self) -> list[_VendorEvent]:
        """Vendor events the adapter saw, validated and bounded (P1-2/P2-8).

        Adapter input is untrusted: an exception, a non-sequence, a malformed item, a
        non-finite timestamp or an oversized text is counted and dropped here so the
        frame loop (and RUI KWS) keeps running.
        """
        drain = getattr(self.adapter, "drain_signals", None)
        if not callable(drain):
            return []
        try:
            pending = drain()
        except Exception as exc:  # noqa: BLE001 - adapter input is untrusted
            self.signal_errors += 1
            self.signal_last_error = f"{type(exc).__name__}: {exc}"[:300]
            now = time.monotonic()
            if now - self._signal_error_logged >= DEVICE_WAKE_ERROR_LOG_S:
                self._signal_error_logged = now
                log.warning(
                    "drain_signals() failed (%s); capture and RUI KWS continue",
                    self.signal_last_error,
                )
            return []
        try:
            events, dropped = _bounded_drain(pending, MAX_DRAIN_EVENTS)
        except TypeError as exc:
            self.signal_invalid += 1
            self.signal_last_error = str(exc)[:300]
            log.debug("drain_signals: %s", exc)
            return []
        if dropped:
            self.signal_dropped += dropped
            self._log_drain_drop("drain_signals", dropped)
        out: list[_VendorEvent] = []
        for item in events:
            kind = getattr(item, "kind", None)
            text = getattr(item, "text", "")
            ts = getattr(item, "ts", 0.0)
            if not isinstance(kind, str) or not kind:
                self.signal_invalid += 1
                continue
            if not isinstance(text, str):
                self.signal_invalid += 1
                continue
            if not self._finite(ts):
                self.signal_invalid += 1
                continue
            out.append(_VendorEvent(
                kind=kind[:64], text=text[:MAX_SIGNAL_TEXT], ts=float(ts)
            ))
        return out

    def _log_drain_drop(self, what: str, dropped: int) -> None:
        now = time.monotonic()
        if now - self._drain_drop_logged < DRAIN_DROP_LOG_S:
            return
        self._drain_drop_logged = now
        log.warning(
            "%s returned too many events: dropped %d (per-tick cap %d)",
            what, dropped, MAX_DRAIN_EVENTS,
        )


    # ------------------------------------------------- device_wake (v1 §5)
    def _configure_device_wake(self, info: object, *, reeval: bool = False) -> None:
        """Decide once, from manifest ∩ probe + the adapter's methods, whether v1 is usable.

        Three independent gates, all required (P1-5/P1-6/P2-4):

        * a resolvable, trusted (in-process) plug that declares `wake.source: device`;
        * effective capabilities are the intersection of the manifest's declaration and
          what `probe()` actually reports;
        * a callable `drain_wakes()`.

        `wake.barge_via_device` is ANDed with the `barge_via_device` capability and
        `playback`: a capability says the device *can*, the wake block says this plug
        *enables* it. Any miss falls back to RUI KWS with one warning - it must never
        look like a device that simply cannot hear.
        """
        self.device_wake_configured = True
        probe_caps = set(getattr(info, "capabilities", frozenset()) or ())
        policy = self._wake_policy()

        def fallback(message: str, *args: object) -> None:
            """Count+log a runtime downgrade; a re-probe repeats it silently."""
            if reeval:
                return
            self.device_wake_fallbacks += 1
            log.warning(message, *args)

        # Cold start: the manifest wants device_wake and the adapter has the method, but
        # the device may not have dialled in when probe() ran. Keep a bounded re-probe
        # budget alive instead of caching "off" for the whole process lifetime.
        self.device_wake_reeval_wanted = bool(
            policy.present
            and policy.trusted
            and policy.source == "device"
            and Capability.DEVICE_WAKE in set(policy.capabilities or ())
            and callable(getattr(self.adapter, "drain_wakes", None))
        )
        effective = effective_capabilities(
            policy.capabilities if policy.present else None, probe_caps
        )
        if policy.present and not policy.trusted:
            # Q8: v1 only supports device_wake for in-process/trusted adapters. `trust`
            # is self-declared metadata, not authorization, but a third-party manifest is
            # still a signal the operator asked for a sidecar; do not run its wake path
            # in-process merely because an env adapter advertises the capability.
            if Capability.DEVICE_WAKE in probe_caps:
                fallback(
                    "device_wake: third-party plug requests wake.source=device, but "
                    "v1 only supports that for trusted/in-process adapters (Q8); "
                    "falling back to RUI KWS (%s)",
                    policy.error or "trust=third-party",
                )
            return
        if Capability.DEVICE_WAKE not in effective:
            if Capability.DEVICE_WAKE in probe_caps:
                # The manifest is the upper bound (§2). This covers both a v0/builtin
                # manifest without the capability and a manifest whose intersection
                # removed it; the probe alone is not enough (P2-3).
                fallback(
                    "device_wake: probe advertises it but the plug manifest does not "
                    "(effective capabilities are manifest ∩ probe); falling back to "
                    "RUI KWS"
                )
            elif policy.source == "device":
                # The manifest declared it but probe() did not verify it: runtime
                # downgrade, one warning, KWS unchanged (§4.3).
                fallback(
                    "device_wake: manifest declares wake.source=device, but probe() "
                    "does not advertise it; falling back to RUI KWS"
                )
            return
        if policy.source == "rui":
            fallback(
                "device_wake: probe advertises it but the device's plug manifest does "
                "not; falling back to RUI KWS"
            )
            return
        if policy.source == "sidecar":
            # Frozen Q8: v1 only supports device_wake for in-process/trusted adapters.
            # A sidecar has no standardised wake RPC yet, so it degrades to KWS rather
            # than pretending an unverified proxy channel is authoritative.
            fallback(
                "device_wake: sidecar adapters are not supported in v1 (Q8); "
                "falling back to RUI KWS"
            )
            return
        drain = getattr(self.adapter, "drain_wakes", None)
        if not callable(drain):
            fallback(
                "device_wake: adapter %r declares the capability but has no "
                "drain_wakes(); RUI KWS continues unchanged",
                getattr(info, "adapter", "?"),
            )
            return
        self.device_wake_active = True
        self.device_wake_reeval_wanted = False
        barge = Capability.BARGE_VIA_DEVICE in effective
        if barge and policy.present and not policy.barge_enabled:
            # P1-5: capability = can, wake block = enables. A plug that explicitly left
            # the hint off must not get it through a probe-only capability.
            log.warning(
                "device_wake: manifest wake.barge_via_device=false; the probe's "
                "barge capability does not enable barge hints"
            )
            barge = False
        elif (
            policy.present
            and policy.barge_enabled
            and Capability.BARGE_VIA_DEVICE not in probe_caps
        ):
            # The inverse: the manifest enables a hint the probe does not verify.
            log.warning(
                "device_wake: manifest enables barge_via_device but probe() does not "
                "advertise it; barge hints disabled"
            )
            barge = False
        elif (
            policy.present
            and Capability.BARGE_VIA_DEVICE in probe_caps
            and Capability.BARGE_VIA_DEVICE not in effective
        ):
            log.warning(
                "device_wake: barge_via_device is probe-only and absent from the "
                "manifest's capability list; barge hints disabled"
            )
            barge = False
        if barge and Capability.PLAYBACK not in effective:
            log.warning(
                "device_wake: barge_via_device declared without playback; "
                "barge hints will not be honoured"
            )
            barge = False
        self.device_wake_barge_active = barge
        log.info(
            "device_wake on (probe: device_wake=true, barge_via_device=%s)",
            barge,
        )

    # ------------------------------------------ device_wake cold-start re-evaluation
    def _maybe_schedule_device_wake_reeval(self) -> None:
        """Background re-probe while the v1 path is wanted but not yet verified.

        Called once per frame tick (a few attribute checks); at most one probe task runs
        at a time, DEVICE_WAKE_REPROBE_S apart, for a bounded number of attempts. The
        frame loop never awaits the probe, so a slow probe cannot eat capture frames.
        """
        if not self.device_wake_reeval_wanted or self.device_wake_active:
            return
        task = self._device_wake_reeval_task
        if task is not None and not task.done():
            return
        now = time.monotonic()
        if self._device_wake_reeval_at == 0.0:
            self._device_wake_reeval_at = now + DEVICE_WAKE_REPROBE_S
            return
        if now < self._device_wake_reeval_at:
            return
        if self.device_wake_reprobes >= DEVICE_WAKE_REPROBE_MAX:
            log.info(
                "device_wake: re-probe budget spent (%d attempts); RUI KWS stays in "
                "charge", self.device_wake_reprobes,
            )
            self.device_wake_reeval_wanted = False
            self._device_wake_reeval_at = 0.0
            return
        self.device_wake_reprobes += 1
        self._device_wake_reeval_at = now + DEVICE_WAKE_REPROBE_S
        self._device_wake_reeval_task = asyncio.create_task(
            self._reevaluate_device_wake(), name="device-wake-reprobe"
        )

    async def _reevaluate_device_wake(self) -> None:
        """Probe again and arm the v1 path if the device now proves device_wake."""
        probe = getattr(self.adapter, "probe", None)
        if not callable(probe):
            self.device_wake_reeval_wanted = False
            return
        try:
            info = await probe()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - adapter input is untrusted
            log.info(
                "device_wake: re-probe failed (%s: %s); retrying in %.0f s",
                type(exc).__name__, exc, DEVICE_WAKE_REPROBE_S,
            )
            return
        if self._stop.is_set():
            return
        self._configure_device_wake(info, reeval=True)
        if self.device_wake_active:
            log.info(
                "device_wake on after re-probe (attempt %d of %d)",
                self.device_wake_reprobes, DEVICE_WAKE_REPROBE_MAX,
            )

    def _wake_policy(self) -> _WakePolicy:
        """Resolve the configured device's plug into the wake upper bound (P1-5/P1-6).

        ``present=False`` is the documented dev case: no manifest was configured or a
        manifest simply does not exist, so the probe is authoritative. If a manifest was
        explicitly requested but cannot be resolved/loaded, the policy fails closed
        (`source="rui"`) instead of silently letting the probe enable device wake (P2-4).
        Reading the catalog here is once per connection, not per event.
        """
        device = next((item for item in self.cfg.devices if item.id == self.device_id), None)
        if device is None:
            return _WakePolicy()
        params = getattr(device, "params", None)
        requested = (
            str(params.get("plug_id") or "").strip() if isinstance(params, dict) else ""
        )
        try:
            from ..plugins.resolve import resolve_device_plug

            resolved = resolve_device_plug(device, self.cfg.system.data_dir)
        except Exception as exc:  # noqa: BLE001 - a broken plug must not block a mic
            error = f"plug_resolve_failed: {type(exc).__name__}: {exc}"[:200]
            log.warning("device_wake: could not resolve the plug manifest: %s", error)
            if requested:
                # An explicitly requested plug that cannot be read is not "no manifest":
                # fail closed to RUI KWS instead of letting the probe enable device wake.
                return _WakePolicy(
                    source="rui", present=True, trusted=True, error=error
                )
            return _WakePolicy(error=error)
        if resolved is None:
            if requested:
                log.warning(
                    "device_wake: plug %r is missing or invalid; failing closed to "
                    "RUI KWS",
                    requested,
                )
                return _WakePolicy(
                    source="rui", present=True, trusted=True, error="plug_unresolved"
                )
            return _WakePolicy()
        runtime = getattr(resolved.device, "runtime", None)
        if runtime is not None and str(getattr(runtime, "mode", "")) == "sidecar":
            # Q8: no standardised wake RPC inside a sidecar yet.
            source = "sidecar"
        else:
            source = str(getattr(resolved.device, "wake_source", "rui"))
        trust = str(getattr(resolved.plugin, "trust", "third-party"))
        return _WakePolicy(
            source=source,
            present=True,
            trusted=trust in ("builtin", "trusted"),
            barge_enabled=bool(
                getattr(resolved.device, "barge_via_device_enabled", False)
            ),
            capabilities=_capability_set(
                getattr(resolved.device, "capabilities", ())
            ),
        )

    def _manifest_wake_source(self) -> str | None:
        """Compatibility seam: the manifest ``wake.source``, or None without one."""
        return self._wake_policy().source

    async def _drain_device_wakes(
        self, *, vendor_wake: bool, writer: asyncio.StreamWriter,
        connected: asyncio.Event,
    ) -> None:
        """Poll the adapter once per frame tick and hand events to the policy.

        Exceptions are contained here: a defective adapter must not stop the frame loop
        or RUI KWS. Five consecutive failures stop the polling for this session; the
        next connection retries.
        """
        drain = getattr(self.adapter, "drain_wakes", None)
        if not callable(drain):
            return
        try:
            pending = drain()
        except Exception as exc:  # noqa: BLE001 - adapter input is untrusted
            self._device_wake_error_streak += 1
            if self._device_wake_error_streak == 1:
                self.device_wake_fallbacks += 1
            now = time.monotonic()
            if now - self._device_wake_error_logged >= DEVICE_WAKE_ERROR_LOG_S:
                self._device_wake_error_logged = now
                log.warning(
                    "device_wake: drain_wakes() failed (%s: %s); RUI KWS continues",
                    type(exc).__name__, exc,
                )
            if self._device_wake_error_streak >= DEVICE_WAKE_MAX_CONSECUTIVE_ERRORS:
                self.device_wake_active = False
                self.device_wake_degraded = True
                log.warning(
                    "device_wake degraded: %d consecutive drain_wakes() failures; "
                    "RUI KWS continues for this session",
                    self._device_wake_error_streak,
                )
            return
        self._device_wake_error_streak = 0
        if pending is None:
            return
        try:
            events, dropped = _bounded_drain(pending, MAX_DRAIN_EVENTS)
        except TypeError:
            # A bad adapter returning a bare DeviceWake (or a scalar) must not take the
            # frame loop down; count it as an invalid event and keep KWS running.
            self._note_device_wake_invalid(
                f"drain_wakes returned {type(pending).__name__}, not a list"
            )
            return
        if dropped:
            # P2-1: act on a bounded number per tick; the excess is counted (the freshest
            # events won for a sequence) instead of stalling the 20 ms frame loop.
            self.device_wake_dropped += dropped
            self._log_drain_drop("drain_wakes", dropped)
        for event in events:
            await self._handle_device_wake(
                event, vendor_wake=vendor_wake, writer=writer, connected=connected
            )

    @staticmethod
    def _finite(value: object) -> bool:
        # bool is an int in Python: `score=True` must be invalid, not 1.0 (P2-2).
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )

    def _device_wake_problem(self, wake: DeviceWake) -> str:
        """Why this event cannot be acted on (§5 "无效条件"), or "" when it can.

        Shape/type/range/staleness live in the contract helper so the adapter kit and
        the plane agree on what "invalid" means (P2-2). A barge hint when barge is
        disabled is *not* invalid here: the playback branch suppresses it and counts
        `device_wake_barge_suppressed`, while the idle wake may still be valid (P1-5).
        """
        return wake_validation_error(
            wake,
            now_ms=now_ms(),
            stale_ms=DEVICE_WAKE_STALE_MS,
            future_ms=DEVICE_WAKE_FUTURE_MS,
        ) or ""

    def _device_wake_is_debounced(self, wake: DeviceWake) -> bool:
        last = self._last_accepted_wake_ts
        return last is not None and abs(float(wake.ts_ms) - last) < DEVICE_WAKE_DEBOUNCE_MS

    def _device_wake_rate_ok(self) -> bool:
        now = time.monotonic()
        times = self._device_wake_accept_times
        while times and now - times[0] > DEVICE_WAKE_RATE_WINDOW_S:
            times.popleft()
        return len(times) < DEVICE_WAKE_RATE_MAX

    def _note_device_wake_invalid(self, reason: str) -> None:
        self.device_wake_invalid += 1
        self._device_wake_invalid_streak += 1
        log.debug("device_wake dropped: %s", reason)
        if self._device_wake_invalid_streak >= DEVICE_WAKE_MAX_INVALID:
            if not self.device_wake_degraded:
                self.device_wake_degraded = True
                self.device_wake_active = False
                self.device_wake_fallbacks += 1
                log.warning(
                    "device_wake degraded after %d invalid events (last: %s); "
                    "RUI KWS continues for this session",
                    self._device_wake_invalid_streak, reason,
                )

    def _device_wake_playing(self) -> bool:
        return self._speak_queued_s > 0.0 or (
            self._playback_task is not None and not self._playback_task.done()
        )

    def _device_wake_barge_allowed(self) -> bool:
        """Every gate that can still say no after the event said `barge=True`.

        Adapter input is advice: RUI's own state decides. Barge-in off (or half-duplex),
        the plug not enabling the hint, a hand-parked song, the cue/echo hold, or the
        vendor talking all suppress it - the event is counted, the playback continues.
        """
        if not self.cfg.audio.voice.barge_in:
            return False
        if not self.device_wake_barge_active:
            return False
        if self._paused_explicit:
            return False
        if time.monotonic() < self._wake_hold_until:
            return False
        if time.monotonic() < self._vendor_quiet_until:
            return False
        adapter = self.adapter
        if (
            adapter is not None
            and getattr(adapter, "vendor_playing", False)
            and time.monotonic() - getattr(adapter, "vendor_playing_ts", 0.0) < 90.0
        ):
            return False
        return True

    async def _handle_device_wake(
        self, event: object, *, vendor_wake: bool, writer: asyncio.StreamWriter,
        connected: asyncio.Event,
    ) -> None:
        """Apply §5 to one drained event. Never raises for adapter-supplied input."""
        if not isinstance(event, DeviceWake):
            self._note_device_wake_invalid(f"not a DeviceWake: {type(event).__name__}")
            return
        problem = self._device_wake_problem(event)
        if problem:
            self._note_device_wake_invalid(problem)
            return
        self._device_wake_invalid_streak = 0
        self.device_wakes += 1
        self.device_wake_last_score = round(float(event.score), 4)
        await self._send_device_wake_msg(event, writer, connected)

        if vendor_wake:
            # §4.3: the vendor's wake wins the tick; ours is observed and dropped.
            self.device_wake_conflicts += 1
            log.info("device_wake conflict: vendor wake wins this tick (score=%.2f)",
                     event.score)
            return
        if self._device_wake_is_debounced(event):
            self.device_wake_debounced += 1
            log.debug("device_wake debounced (ts_ms=%.0f)", event.ts_ms)
            return
        if not self._device_wake_rate_ok():
            self.device_wake_rate_limited += 1
            log.info("device_wake rate limited (%d accepted in %.0f s)",
                     DEVICE_WAKE_RATE_MAX, DEVICE_WAKE_RATE_WINDOW_S)
            return
        self._last_accepted_wake_ts = float(event.ts_ms)
        self._device_wake_accept_times.append(time.monotonic())

        if self.gate is None:
            # The event only opens ears; without VAD there is no turn. Never call the
            # LLM from the event alone.
            self.device_wake_no_vad += 1
            log.info("device_wake: no VAD available; event cannot form a turn")
            return

        playing = self._device_wake_playing()
        if self.arbitrate:
            admitted = await self._arbitrate_candidate(
                source="device", keyword="",
                barge=bool(playing and event.barge), playing=playing,
                writer=writer, connected=connected,
                score=float(event.score), threshold=float(event.threshold),
                level=float(event.level) if event.level is not None else None,
            )
            if not admitted:
                return
        barged_s = self._speak_queued_s if playing else 0.0
        if playing:
            if not event.barge:
                self.device_wake_suppressed += 1
                log.info("device_wake during playback ignored (barge=false)")
                return
            if not self._device_wake_barge_allowed():
                self.device_wake_suppressed += 1
                self.device_wake_barge_suppressed += 1
                log.info(
                    "device_wake barge suppressed (barge_in=%s, capability=%s, "
                    "parked=%s, cue_hold=%s)",
                    self.cfg.audio.voice.barge_in, self.device_wake_barge_active,
                    self._paused_explicit, time.monotonic() < self._wake_hold_until,
                )
                return
            action = "barge"
            self.device_wake_barges += 1
            await self._stop_for_barge(barged_s)
        else:
            action = "interrupt" if self.gate.armed else "turn"
            if action == "interrupt":
                self.device_wake_interrupts += 1
                log.info("device_wake while a turn is open: gate buffer discarded")
        self.device_wake_turns += 1
        await self._begin_wake_turn(
            ts_ms=float(event.ts_ms),
            keyword="",
            barge=playing,
            barged_s=round(barged_s, 2),
            source="device",
            score=event.score,
            threshold=event.threshold,
            barge_hint=event.barge,
            action=action,
            # §5: a device wake's phrase tail is not in RUI's stream to skip; use 0
            # and align by ts_ms instead of the KWS path's 300 ms.
            skip_after_wake_ms=0,
            writer=writer,
            connected=connected,
        )

    async def _send_device_wake_msg(
        self, event: DeviceWake, writer: asyncio.StreamWriter, connected: asyncio.Event
    ) -> None:
        """Observability only: the control plane may see an event that became no turn."""
        if not connected.is_set():
            return
        try:
            writer.write(encode_json(DeviceWakeMsg(
                ts_ms=event.ts_ms, score=event.score, threshold=event.threshold,
                barge=event.barge, level=event.level,
            )))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            connected.clear()

    # ---------------------------------------------------- wake, preroll, ack
    def _preroll_history_ms(self) -> int:
        """How much timestamped audio to retain for wake replay (P1-7).

        A device wake may arrive up to `DEVICE_WAKE_STALE_MS` late, so the history has to
        reach back past the event timestamp; the replay window itself is still
        `wake_preroll_ms` long and is selected from this deque by timestamp.
        """
        preroll_ms = max(0, int(self.cfg.audio.voice.wake_preroll_ms))
        if not self.device_wake_active:
            return preroll_ms
        return max(preroll_ms, int(DEVICE_WAKE_STALE_MS) + preroll_ms)

    def _keep_preroll(self, pcm: bytes, ts_ms: float) -> None:
        """Hold timestamped voice-rate audio for the next wake.

        The frame timestamp travels with the block: a replay that stamps every block with
        the wake time erases the timeline the trace needs. With device wakes enabled the
        ring is longer than `wake_preroll_ms` (see `_preroll_history_ms`).
        """
        limit = int(self.cfg.audio.voice.rate * self._preroll_history_ms() / 1000)
        if limit <= 0:
            return
        self._preroll.append((ts_ms, pcm))
        self._preroll_samples += len(pcm) // 2
        while self._preroll_samples > limit and len(self._preroll) > 1:
            self._preroll_samples -= len(self._preroll.popleft()[1]) // 2

    def _take_preroll(
        self, anchor_ts: float, preroll_ms: int, *, fallback_all: bool
    ) -> tuple[list[tuple[float, bytes]], bool]:
        """Drain the history and select the replay window anchored at `anchor_ts`.

        P1-7: the old code replayed the newest `wake_preroll_ms` at handling time, so an
        event accepted 1.5 s late replayed the wrong audio and lost the onset it exists
        to protect. Here the window starts at `anchor_ts - preroll_ms`, so a late event
        replays the audio around its own timestamp (and the intervening live audio), not
        "the last N ms from now".

        Returns ``(blocks, missed)``. ``missed`` means the anchor is older than every
        retained block; the caller counts it rather than silently mis-replaying the
        newest window. `fallback_all` keeps the KWS path's historical "replay what
        remains" behavior.
        """
        history = list(self._preroll)
        self._preroll.clear()
        self._preroll_samples = 0
        if not history:
            return [], False
        lower = float(anchor_ts) - max(0, int(preroll_ms))
        selected = [(ts, block) for ts, block in history if ts >= lower]
        if selected:
            return selected, False
        if fallback_all:
            return history, False
        return [], True

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
        if barged and not self.cfg.audio.voice.barge_in:
            # §5/§7: barge-in disabled (or half-duplex) means a wake during playback
            # must not stop the speaker. Ignoring it is safer than arming a gate over
            # our own audio; the wake word works again once the answer drains.
            log.info("wake during playback ignored: voice.barge_in is off")
            return
        if self.arbitrate:
            admitted = await self._arbitrate_candidate(
                source="rui", keyword=hit.keyword, barge=barged,
                playing=self._device_wake_playing() or self._speak_queued_s > 0.0,
                writer=writer, connected=connected,
                ref_active=bool(self.aec.ref_active) if self.aec is not None else False,
            )
            if not admitted:
                return
        barged_s = self._speak_queued_s
        if barged:
            await self._stop_for_barge(barged_s)
        await self._begin_wake_turn(
            ts_ms=hit.ts_ms,
            keyword=hit.keyword,
            barge=barged,
            barged_s=barged_s,
            source="rui",
            score=None,
            threshold=None,
            barge_hint=False,
            action="barge" if barged else "turn",
            skip_after_wake_ms=None,
            writer=writer,
            connected=connected,
        )

    async def _stop_for_barge(self, barged_s: float) -> None:
        """Flush the reply, park the song and tell the device to drop its buffer.

        Shared by the KWS barge and a device `barge=true` hint: one stop path, one
        measurement (`last_barge_stop_ms`), so the acceptance number means the same
        thing whichever detector fired.
        """
        self.barges += 1
        self.last_barge_s = barged_s
        log.info("barge: %d wake during playback, dropped %.1f s queued", self.barges, barged_s)
        # Time the server's own stop: flush the queue, pause the song, tell the
        # device to drop what it holds. The acceptance number (<=300 ms) had only
        # ever been measured in tests; this makes every production barge report it.
        stop_started = time.monotonic()
        self._flush_speech()
        # A wake word interrupts speech, not the song: pause it where it is and let
        # the turn's end bring it back (old build: paused_by_barge + auto-resume).
        # The device still has up to a buffer of audio in hand, so it is told to drop
        # it - that is the same stop_play a barge always needed.
        self._pause_for_speech()
        if self.adapter is not None:
            try:
                await self.adapter.stop_playback(self.cfg.audio.playback.fade_out_ms)
                self._device_audio_pending = False
            except Exception as exc:  # noqa: BLE001 - the speaker may be gone
                log.info("barge: stop failed: %s", exc)
        self.last_barge_stop_ms = (time.monotonic() - stop_started) * 1000.0
        log.info(
            "barge: silence_after_ms=%.0f (server-side: queue flushed, stop_play sent; "
            "device ALSA buffer ~300 ms not included)",
            self.last_barge_stop_ms,
        )

    async def _begin_wake_turn(
        self,
        *,
        ts_ms: float,
        keyword: str,
        barge: bool,
        barged_s: float,
        source: str,
        score: float | None,
        threshold: float | None,
        barge_hint: bool,
        action: str,
        skip_after_wake_ms: int | None,
        writer: asyncio.StreamWriter,
        connected: asyncio.Event,
    ) -> None:
        """One accepted wake -> cue, replay, gate, then the control-plane event.

        Shared by RUI KWS (`source="rui"`) and `drain_wakes()` (`source="device"`).
        The VAD gate is the hard requirement in both paths: the event only opens the
        ears, the person's speech forms the turn. `skip_after_wake_ms=None` keeps the
        KWS path's configured 300 ms; a device wake passes 0 because its phrase tail is
        not in our stream (the adapter's ts_ms is the alignment). A `barge=True` wake
        skips the cue entirely: that person is already talking (see the comment below).
        """
        # §5: our own accepted wake clears the vendor's quiet window (the control plane
        # does the same in VendorWindow.ours_spoke).
        self._vendor_quiet_until = 0.0
        # P2-2 (task-19): a wake starts a new turn. Whatever the previous turn had
        # queued (a goodbye/answer still being streamed) is dropped now, and anything
        # that arrives before this turn's own utterance is ignored (see
        # `_stale_speech_blocked`). The ack cue is local and unaffected.
        self._flush_speech()
        # Nothing will clear this if the gate is absent: there is no turn to form.
        self._stale_speech_blocked = self.gate is not None
        # Takeover: our wake outranks a running vendor dialog. The old build stayed
        # deaf while the vendor player ran (mic gated, assistant_v3.py:3115), so its
        # wake never had to stop the vendor assistant; ours hears 你好小瑞 mid-answer, and taking the
        # line means the vendor's voice stops - otherwise both assistants talk at
        # once and the vendor's own answer leaks into our ASR as the next request
        # (measured 19:54:06 on 2026-10-22). Fire-and-forget: the wake path must not
        # wait on a shell RPC. The 90 s freshness guard is the old build's, against
        # a sticky "playing" from a lost idle event.
        adapter = self.adapter
        if (adapter is not None
                and getattr(adapter, "vendor_playing", False)
                and time.monotonic() - getattr(adapter, "vendor_playing_ts", 0.0) < 90.0):
            log.info("vendor takeover: pausing the vendor player for our wake")
            asyncio.create_task(adapter.pause_vendor_player("wake takeover"))

        # A cue answers a wake nobody is talking over. A barge is the opposite: the
        # user is already mid-sentence, so 「在呢」 talks over their first words and its
        # echo comes back through the microphone as the next turn - measured 2026-10-26:
        # 12:07:03 barge -> cue -> 12:07:05 seg 「在呢。」 -> 12:07:07 reply -> 12:07:12
        # barge again. No cue also means no cue-length gate hold and no cue/echo window
        # below, so the speech the barge interrupted reaches ASR from its first word.
        if barge:
            audio = np.zeros(0, dtype="<i2")
            mode = "none"
            cue_ms = 0.0
            hold_ms = 0.0
        elif self.device_muted:
            # task-18: silent ring, not a silent assistant. The mic keeps feeding the
            # gate and ASR; only our own cue is suppressed (no cue also means no
            # cue/echo hold, so the request after the wake keeps its first word).
            audio = np.zeros(0, dtype="<i2")
            mode = "none"
            cue_ms = 0.0
            hold_ms = 0.0
            self.ack_skipped += 1
            log.info("wake: device muted, ack skipped; the turn still opens")
        else:
            audio, mode, note = render_ack(self.cfg)
            self.ack_mode_effective = mode
            # Cleared on success: an error that sticks forever makes the console report a
            # problem that was fixed an hour ago.
            self.ack_error = note
            cue_ms, hold_ms = self._ack_gate_windows(audio, mode)
            # A spoken cue mutes the device microphone; a tone rings out through the device's
            # own 300 ms buffer. Both need more than their nominal length, so the hold carries
            # a floor - the old build used 250 ms for exactly this - and anything arriving
            # meanwhile is buffered, not dropped. `cue_ms`/`hold_ms` carry the render rate
            # and the playout lead (see `_ack_gate_windows`).
            # A device barge hint inside this window is our own cue/echo, not a person;
            # `_device_wake_barge_allowed` consults this deadline (§5 ACK mute/gate hold).
            self._wake_hold_until = max(
                self._wake_hold_until, time.monotonic() + hold_ms / 1000.0
            )

        event = Wake(
            ts_ms=ts_ms,
            keyword=keyword,
            erle_db=self.aec.erle_db if self.aec is not None else None,
            ref_active=bool(self.aec.ref_active) if self.aec is not None else False,
            playback_s=round(self.playback_s, 1),
            voice_rms_db=None if self.voice_rms_db is None else round(self.voice_rms_db, 1),
            ref_rms_db=None if self.ref_rms_db is None else round(self.ref_rms_db, 1),
            ack_mode=mode,
            barge=barge,
            barged_s=round(barged_s, 2),
            source=source,  # type: ignore[arg-type]
            score=None if score is None else round(float(score), 4),
            threshold=None if threshold is None else round(float(threshold), 4),
            barge_hint=barge_hint,
            action=action,
        )
        self.last_wake = event

        if self.gate is not None:
            if self.gate.armed:
                # A second wake while a turn was open: arm() resets the buffer, so count it
                # instead of letting a whole utterance disappear silently.
                self.gate.interrupted += 1
                log.info("wake while a turn was open: %.0f ms discarded", self.gate.speech_ms)
            self.gate.arm(skip_after_wake_ms=skip_after_wake_ms)
            self._followup_capture = False
            self._followup_tail.clear()
            self._followup_samples = 0
            # P1-7: anchor the replay at this event's own timestamp. A device wake can
            # be up to DEVICE_WAKE_STALE_MS late; replaying "the last N ms from now"
            # would hand the gate the wrong audio and lose the onset the preroll exists
            # to protect. The KWS path keeps its "replay what remains" fallback.
            blocks, missed = self._take_preroll(
                float(ts_ms),
                max(0, int(self.cfg.audio.voice.wake_preroll_ms)),
                fallback_all=source != "device",
            )
            if missed:
                self.device_wake_replay_missed += 1
                log.warning(
                    "device_wake: ts_ms=%.0f is older than the retained audio; "
                    "replay skipped instead of mis-aligned",
                    float(ts_ms),
                )
            replayed = 0
            for ts, block in blocks:
                await self._feed_gate(block, ts, writer, connected)
                replayed += len(block) // 2
            self.preroll_replayed_ms = round(replayed * 1000.0 / self.cfg.audio.voice.rate, 1)
            self.preroll_replays += 1
            # From here on it is live audio, and the cue may still be sounding. The cue's
            # own playing time is skipped rather than replayed: that window is the box's
            # voice, and keeping it made ASR transcribe 「在呢」 as a user turn.
            # Skip the cue plus its device-buffer tail (the speaker keeps sounding ~300 ms
            # after the last frame). What remains is the echo/answer window after the cue:
            # long enough for a request starting right after 「在呢」, short enough that the
            # cue itself never reaches ASR.
            # A barge played no cue, so there is nothing to skip: holding here would swallow
            # the first words of the sentence the barge interrupted.
            if not barge and audio.size:
                # Hand the gate the cue we are about to play. A device's playback latency
                # can push the cue past the skip window; the gate then measures how much
                # was still unplayed and drops only that tail (2026-10-26 12:33: the
                # replayed 「在呢」 echo opened a turn). A device that mutes its microphone
                # during the cue simply matches nothing.
                self.gate.set_echo_reference(audio, self.cfg.audio.playback.rate)
                self.gate.hold(hold_ms, skip_ms=min(hold_ms, cue_ms + ACK_TAIL_MS))

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
        before_idle = getattr(gate, "idle_events", 0)
        was_armed = bool(getattr(gate, "armed", False))
        finished = gate.feed(pcm, ts_ms=ts_ms)
        idle_events = getattr(gate, "idle_events", 0)
        if was_armed and not getattr(gate, "armed", False):
            # armed -> disarmed covers the task-15 silent timeout: when a cue was
            # excised the gate disarms without incrementing `idle_events`, so the
            # idle branch below never runs. Same assignment, so the two paths are
            # idempotent (this runs on the event loop, single-writer).
            self._stale_speech_blocked = False
        if idle_events != before_idle:
            # The listening window closed without speech (`wake without speech` /
            # WakeIdle / disarm after the timeout). Nothing of this wake is waiting for
            # a reply any more, so stop suppressing playback: a later reminder or a
            # console/debug `say` PLAY_PCM must sound (2026-10-26 review).
            self._stale_speech_blocked = False
            # A wake over a song pauses it. If the wake then hears nothing, the song must
            # come back - a parked song waiting for a reply that will never come is the
            # "music stopped after I woke it" symptom. An explicit 「暂停」 stays paused.
            if self._speech_paused and not self._paused_explicit:
                self._resume_after_speech()
        if idle_events != before_idle and connected.is_set():
            # The wake heard nothing: tell the control plane to close the session
            # silently, otherwise the light/session stay in listening.
            try:
                writer.write(encode_json(WakeIdle(waited_ms=float(gate.cfg.max_wait_ms),
                                               wake_id=self._active_wake_id)))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                connected.clear()
        if finished is None or not connected.is_set():
            return
        utt, ts = finished
        self.utterances += 1
        # The duck now belongs to this turn: the reply (or its failure) ends it.
        self._turn_pending = True
        self._turn_pending_ts = time.monotonic()
        # This turn's own speech has begun; replies from here are legitimate.
        self._stale_speech_blocked = False
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
        rate = self.effective.playback.rate
        # The cue is rendered once at the *global* rate by the control plane; a plane
        # whose device overrides playback.rate must resample it, not relabel it.
        source = self.cfg.audio.playback.rate
        if source != rate:
            audio = resample(audio, source, rate)
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
                self._device_audio_pending = True
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
        # Our song outranks a running vendor dialog, same as our wake does (old build:
        # vendor_stop_player before every audible start). Fire-and-forget: playback
        # must not wait on a shell RPC.
        adapter = self.adapter
        if (adapter is not None
                and getattr(adapter, "vendor_playing", False)
                and time.monotonic() - getattr(adapter, "vendor_playing_ts", 0.0) < 90.0):
            log.info("vendor takeover: pausing the vendor player for our music")
            asyncio.create_task(adapter.pause_vendor_player("music start"))
        self.playback_path = msg.path
        self._file_position = 0
        self._file_cfg = (Path(msg.path), msg.loop, msg.gain)
        self._speech_paused = False
        self._paused_explicit = False
        self._playback_task = asyncio.create_task(
            self._play_file(Path(msg.path), msg.loop, msg.gain), name="playback"
        )
        return msg.path

    def _credit_sync(self, adapter: DeviceAdapter) -> bool:
        """True when the device reports its play head and the credit is still usable."""
        if not getattr(adapter, "reports_consumption", False):
            return False
        generation = getattr(adapter, "sink_generation", 0)
        if generation != self._credit_generation:
            # A new player restarted the device's chunk count at 1.
            self._credit_generation = generation
            self._credit.reset()
        return not self._credit.dead

    async def _credit_wait(self, adapter: DeviceAdapter, seconds: float) -> bool:
        """Wait until the device has drained enough for one more block."""
        if not self._credit_sync(adapter):
            return False
        return await self._credit.wait(adapter, seconds)

    def _credit_note(self, adapter: DeviceAdapter, seconds: float) -> bool:
        """Record one block sent (after play(), which may have opened a new player)."""
        if not self._credit_sync(adapter):
            return False
        self._credit.note_sent(seconds)
        self.playback_lead_s = self._credit.lead
        return True

    @staticmethod
    def playback_reached_end(position: int, offset: int, size: int, step: int) -> bool:
        """True only when the file was actually streamed to its end.

        Measured 2026-10-23 15:53:28: the speaker's websocket dropped right after a resumed
        song started; the stream ended within 0.7 s of a 207 s track, yet the plane emitted
        PlayFinished and the queue advanced to the next song (and 「继续」 then said nothing
        was paused). A stream that stops short must not look like a finished song.
        """
        return position >= offset + size - max(step, 1)

    async def _play_file(self, path: Path, loop: bool, gain: float,
                         offset: int = 0) -> None:
        try:
            pcm, rate = read_wav_mono(path)
        except (OSError, wave.Error) as exc:
            log.warning("playback: cannot read %s: %s", path, exc)
            self.last_playback_error = str(exc)
            # The claim must die with the task: a file that cannot be read once left
            # owner=music forever, and every later reply waited on a dead player.
            self._release("music")
            return
        target = self.effective.playback.rate
        audio = np.clip(resample(pcm, rate, target) * float(gain), -1.0, 1.0)
        adapter = self.adapter
        if adapter is None:
            return
        # A song asked for while a follow-up window is open would hand its lyrics to the
        # gate as the next request - same hole as the farewell, without the race.
        self._disarm_gate_for_our_audio()
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
        finally:
            # A finished song stops holding the speaker. Without this the owner stayed
            # "music" forever: the next reply waited 2 s before taking it, and the idle
            # timer saw a busy speaker and never said goodbye. A pause for speech keeps
            # the claim (the dialogue took it; resume claims it back).
            if not self._speech_paused:
                self._release("music")
        if loop:
            return
        step = max(1, target * PLAY_CHUNK_MS // 1000)
        if not self.playback_reached_end(self._file_position, offset, audio.size, step):
            # The speaker went away mid-song: do not tell the queue the song finished, or
            # it starts the next one while the device is reconnecting.
            log.warning(
                "playback ended early at %.1f s of %.1f s (%s); queue not advanced",
                max(0, self._file_position - offset) / target,
                audio.size / target, path.name)
            return
        log.info("playback finished: %s", path.name)
        # Only a natural end of a non-looping file says so: the queue advances on this,
        # and a pause (which cancels the task above) must not look like a finish.
        self._emit_control(PlayFinished(path=str(path)))

    def _emit_control(self, msg: object) -> None:
        """Best-effort plane -> control message; the plane outlives reconnects."""
        writer, connected = self._ctl_writer, self._ctl_connected
        if writer is None or connected is None or not connected.is_set():
            return
        try:
            writer.write(encode_json(msg))  # type: ignore[arg-type]
            asyncio.create_task(writer.drain(), name="ctl-drain")
        except (ConnectionResetError, BrokenPipeError):
            connected.clear()

    async def _stream_file(self, audio: np.ndarray, target: int, adapter: DeviceAdapter,
                           loop: bool) -> None:
        step = max(1, target * PLAY_CHUNK_MS // 1000)
        pacer = Pacer()
        lead_max = 0.0
        chunks = 0
        while not self._stop.is_set():
            for start in range(0, audio.size, step):
                if self._stop.is_set():
                    return
                chunk = audio[start:start + step]
                seconds = chunk.size / target
                credited = await self._credit_wait(adapter, seconds)
                await adapter.play(
                    np.round(chunk * 32767.0).astype("<i2").tobytes(),
                    sample_rate=target,
                    channels=1,
                )
                self.playback_s += seconds
                self._device_audio_pending = True
                self._file_position += chunk.size
                if loop and audio.size:
                    # A looping file's position is measured against the track, not a
                    # running total: resuming a 20 s snippet "at 37 s" played nothing.
                    self._file_position %= audio.size
                if credited:
                    # The report says "accepted by aplay's pipe", not "played": it bounds
                    # how far the server may run ahead, but the pipe itself buffers up to
                    # ~2 s. Pacing by the clock as well keeps that pipe shallow, which is
                    # what keeps writes instant; measured on the device, credit-only
                    # playback blocked 228 writes per 30 s vs 17 per 35 s with the clock.
                    self._credit_note(adapter, seconds)
                now = time.monotonic()
                wait = pacer.sleep_for(now, seconds)
                self.playback_lead_s = pacer.lead
                chunks += 1
                lead_max = max(lead_max, self.playback_lead_s)
                await asyncio.sleep(wait)
            if not loop:
                break
        log.info("playback: %d chunks, lead max %.0f ms", chunks, lead_max * 1000)

    # ------------------------------------------------------- P2.9 reply start gate
    async def _await_reply_prebuffer(self, prebuffer_s: float) -> bool:
        """Hold a reply's first block until `prebuffer_s` of audio is queued.

        The issue (#1) measured a transport that delivers the first PCM batch, then the
        rest ~400 ms later: the device drained the 250-320 ms the Pacer kept in hand and
        the listener heard a gap mid-reply. Waiting at the *start* needs no change to the
        steady-state pacing, and it is the only place where there is still a choice - once
        the first block is on the wire the speaker owns the clock.

        Bounded by design: `prebuffer_s + PREBUFFER_TIMEOUT_EXTRA_S`, so a reply shorter
        than the prebuffer (or a producer that never sends more) starts anyway. Returns
        True when the target was actually reached.
        """
        if prebuffer_s <= 0.0:
            return True
        started = time.monotonic()
        deadline = started + prebuffer_s + PREBUFFER_TIMEOUT_EXTRA_S
        while not self._stop.is_set():
            if self._speak_queued_s >= prebuffer_s:
                return True
            now = time.monotonic()
            if now >= deadline:
                return False
            await asyncio.sleep(min(PREBUFFER_POLL_S, deadline - now))
        return False

    def _record_playback_gap(self, gap_s: float) -> None:
        """Count an audible underrun (`gap_s` seconds past the last queued audio)."""
        gap_ms = gap_s * 1000.0
        if gap_ms < PLAYBACK_GAP_MIN_MS:
            return
        self.playback_gap_count += 1
        self.playback_max_gap_ms = max(self.playback_max_gap_ms, gap_ms)
        if self._reply_open:
            self._reply_gap_count += 1
            self._reply_max_gap_ms = max(self._reply_max_gap_ms, gap_ms)

    async def _prepare_reply_player(self, adapter: DeviceAdapter) -> bool:
        """Stop the old player before a reply only when it may still hold audio.

        The stop is what makes `play()` issue `start_play` again after a long silence
        (the aplay underrun fix, docs/playback-path.md §3 A). When the plane has already
        sent an explicit stop and nothing since, the device player is stopped and the
        next `play()` opens it anyway: sending another stop buys nothing and, on an
        adapter whose control and data paths are separate sockets, puts a control frame
        in the race with the first PCM (issue #1 root cause (a)).
        """
        if not self._device_audio_pending:
            return False
        try:
            await adapter.stop_playback(self.cfg.audio.playback.fade_out_ms)
        except Exception as exc:  # noqa: BLE001 - the speaker may be gone
            log.info("reply: pre-start stop failed: %s", exc)
            return False
        self._device_audio_pending = False
        return True

    async def _speak_loop(self) -> None:
        """Play the reply PCM the control plane pushes.

        PLAY_PCM carries mono PCM at the *voice* rate - every TTS provider is built with
        `voice.rate`. Handing that straight to a 48 kHz device plays each answer three
        times too fast: byte counts, pacing stats and tests all look right, and only the
        ear catches it. Resampled here, once, and paced by the resulting duration.
        """
        target = self.effective.playback.rate
        source = self.cfg.audio.voice.rate
        # The configured prebuffer is the *start* gate (below) and, when set, the
        # steady-state lead too: a reply that must hold 450 ms of audio before it begins
        # also wants the Pacer to keep ~450 ms in hand, or the device drains to the old
        # 320 ms cap. With the config at 0 the historical 250/320 ms pacing is unchanged.
        prebuffer_s = self.effective.playback.prebuffer_ms / 1000.0
        pacer = Pacer(
            prebuffer=max(PREBUFFER_S, prebuffer_s),
            max_lead=max(MAX_LEAD_S, prebuffer_s),
        )
        reply_chunks = 0
        reply_lead_max = 0.0
        while not self._stop.is_set():
            try:
                chunk = await asyncio.wait_for(self._speak_queue.get(), timeout=0.5)
            except TimeoutError:
                # Nothing more is coming: that is the end of the answer, so hand the song
                # back. Checking only after a send missed the case where the last chunk
                # was already out and the loop sat waiting.
                if self._speak_queue.empty():
                    now = time.monotonic()
                    if self._starvation_due is not None and not self._speak_finished:
                        # A producer that stalls for the whole grace window is an
                        # underrun too, not an end: SpeakFinished has not arrived.
                        self._record_playback_gap(now - self._starvation_due)
                    self._starvation_due = None
                    self._reply_open = False
                    # The lead is the number to read when a reply sounds rough; without
                    # this line the only copy of it was the console, 2 s stale. Only when
                    # something was actually spoken - the idle loop lands here too.
                    if reply_chunks:
                        log.info("reply: %d chunks, lead max %.0f ms",
                                 reply_chunks, reply_lead_max * 1000)
                        if self._reply_gap_count:
                            # One line per reply, never per chunk.
                            log.info("reply: %d gaps, max %.0f ms",
                                     self._reply_gap_count, self._reply_max_gap_ms)
                    self._reply_gap_count = 0
                    self._reply_max_gap_ms = 0.0
                    finished_reply = self._reply_finished
                    self._reply_finished = False
                    reply_hold, self._reply_hold = self._reply_hold, None
                    reply_chunks = 0
                    reply_lead_max = 0.0
                    self._release("dialogue")
                    # Keep the microphone open for the next sentence, the way the old
                    # build did inside a session: 0.5 s end point, no wake word. Only
                    # after a reply that actually spoke - this branch also runs on every
                    # idle timeout, and re-arming there left the gate open forever
                    # (measured: a turn 42 s after the last one, with no wake word).
                    if (finished_reply and self.gate is not None and not self.gate.armed
                            and not self._speech_paused):
                        # The last block was *sent*; the device is still playing what is
                        # in its buffer. Opening the gate now makes the assistant answer
                        # its own voice - measured 2026-10-22: reply queued at 18:23:13,
                        # a 1.58 s "segment" at 18:23:15 that became a turn. Wait for the
                        # pacer to drain, then the old build's 1 s echo tail.
                        # With a song waiting the block below arms it with the music
                        # window instead (shorter max_wait, and the song stays down).
                        delay = self._followup_delay_from(pacer.due)
                        if self.effective.voice.followup_replay:
                            self._start_followup_capture(
                                max(0.0, pacer.due - time.monotonic()), delay)
                        asyncio.create_task(
                            self._arm_followup_later(delay, reason="reply drained"),
                            name="followup-gate"
                        )
                    # The song waits out the whole turn. An empty queue alone once meant
                    # "speech over", so the music came back 0.4 s after the wake - over
                    # the user's request, and the resumed music then closed the freshly
                    # armed gate (measured 20:54:03: wake, cue, resume, deaf). It comes
                    # back only when the gate has closed AND the turn is over: the reply
                    # drained (ReplyFinished clears the pending flag), the wake heard no
                    # speech at all, or the no-reply grace ran out - a turn that died
                    # without an answer must not keep the music down forever (the old
                    # build's vendor_silence_watchdog, assistant_v3.py:2185).
                    if finished_reply:
                        self._turn_pending = False
                    if finished_reply and self._speech_paused and self.gate is not None:
                        # The turn ended with a song waiting: hold it for the follow-up
                        # window instead of resuming between turns. A turn may shorten
                        # or cancel its own window (volume confirmations: 0).
                        delay = self._followup_delay_from(pacer.due)
                        hold_ms = (int(self.cfg.music.resume_hold_ms)
                                   if reply_hold is None
                                   else max(0, int(reply_hold * 1000)))
                        self._music_hold_until = (
                            time.monotonic() + delay + hold_ms / 1000.0)
                        if hold_ms > 0:
                            self._start_followup_capture(
                                max(0.0, pacer.due - time.monotonic()), delay)
                            asyncio.create_task(
                                self._arm_followup_later(delay, max_wait_ms=hold_ms,
                                                         reason="music hold"),
                                name="followup-gate")
                        else:
                            self._music_hold_until = None   # resume on this very tick
                    now = time.monotonic()
                    holding = self._music_hold_until is not None and (
                        now < self._music_hold_until
                        or (self.gate is not None and self.gate.armed))
                    if not holding:
                        self._music_hold_until = None
                        if self.gate is None or not self.gate.armed:
                            waiting = (
                                self._turn_pending
                                and now - self._turn_pending_ts < NO_REPLY_RESUME_S
                            )
                            if not waiting:
                                self._resume_after_speech()
                    # The player is deliberately left alone here: the next reply closes
                    # it and opens a fresh one (see the first-chunk branch below). Closing
                    # it now would cut the tail still playing out of the device buffer.
                continue
            if chunk is None:
                continue
            generation = self._speak_generation
            if self._starvation_due is not None:
                # The queue was empty at the last send; compare the arrival against the
                # moment the device ran out, not against the queue-empty moment. A block
                # that arrives while the lead is still positive is not a gap.
                self._record_playback_gap(time.monotonic() - self._starvation_due)
                self._starvation_due = None
            if isinstance(chunk, tuple):
                chunk, segment = chunk
            else:  # a raw block: treat it as a sentence start
                segment = True
            if segment and not self._reply_open and prebuffer_s > 0.0:
                # P2.9: do not put the first block on the wire until the producer has
                # handed over the configured prebuffer. Bounded; see the helper.
                await self._await_reply_prebuffer(prebuffer_s)
                if generation != self._speak_generation or self._stop.is_set():
                    # A barge/StopAudio flushed the queue while the gate was waiting:
                    # this already-pulled chunk belongs to the cancelled reply.
                    continue
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
                    # A fresh reply owns its own gap accounting.
                    self._reply_gap_count = 0
                    self._reply_max_gap_ms = 0.0
                    self._starvation_due = None
                    # Whatever we are about to sound - an answer, the farewell, a reminder -
                    # must close a gate the previous turn left open (see below).
                    self._disarm_gate_for_our_audio()
                    # Open a fresh player for every reply, as the old build does (one Sink
                    # per speak_stream). A kept-alive aplay underruns during the silence
                    # before an answer - measured on the device: "underrun!!! (at least
                    # 5373.550 ms long)" exactly at the cue-to-answer gap - and the first
                    # block written into a drained device is eaten by its restart, heard
                    # as a swallowed first syllable plus a click. Closing the player here
                    # makes the first play() below issue start_play, which respawns aplay
                    # and settles 80 ms before the first chunk (0b). The stop is skipped
                    # when the plane already stopped the device and sent nothing since:
                    # on a split control/data transport that redundant frame raced the
                    # first PCM (issue #1).
                    stopped = await self._prepare_reply_player(adapter)
                    log.info("reply: first chunk at %d Hz%s", target,
                             "" if stopped else " (player already stopped)")
            pcm = np.round(np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            seconds = audio.size / target
            # Pace by the device's play head when it reports one; the wall clock is only
            # the fallback (a device that cannot report, or one that stopped reporting).
            credited = await self._credit_wait(adapter, seconds)
            try:
                await adapter.play(pcm, sample_rate=target, channels=1)
            except Exception as exc:  # noqa: BLE001 - the speaker may be gone
                log.info("speak: %s", exc)
                continue
            self.playback_s += seconds
            self._device_audio_pending = True
            if credited:
                # Bound only. The device's report means "aplay accepted the block into
                # its pipe", so pacing by it alone keeps that pipe permanently full and
                # every write waits for a drain (measured: 228 blocked writes / 30 s).
                # The clock keeps the pipe shallow; the report stops us ever running
                # more than `window` blocks ahead of the device's own queue.
                self._credit_note(adapter, seconds)
            # Pace against a deadline: sending costs 1-3 ms per chunk, and adding that
            # to every chunk drifts a 20 s answer half a second past what the device
            # plays. The prebuffer is the lead the device keeps in hand; CATCHUP makes
            # it grow 2 % per chunk so it cannot drain to zero; MAX_LEAD stops a long
            # answer from running so far ahead that a barge-in is heard late.
            now = time.monotonic()
            wait = pacer.sleep_for(now, seconds)
            self.playback_lead_s = pacer.lead
            reply_chunks += 1
            reply_lead_max = max(reply_lead_max, self.playback_lead_s)
            # Waiting until the buffer would drop to the target, not until it is
            # empty: that wait is the whole fix.
            await asyncio.sleep(wait)
            # P2.9: remember when the device will run dry if the producer stays silent.
            # Stamped *before* the sentence-boundary grace: a batch that arrives during
            # that 0.4 s was already a gap at the speaker, and the grace must not erase
            # the deadline it is measured against. A block that lands while the lead is
            # still positive is not counted.
            if reply_chunks and self._reply_open and self._speak_queue.empty():
                self._starvation_due = pacer.due
            else:
                self._starvation_due = None
            if self._speak_queued_s <= 0.0 and self._speak_queue.empty():
                # One short grace period: a sentence boundary is not the end of the answer,
                # and resuming the song between two sentences would be worse than a pause.
                await asyncio.sleep(0.4)
                if self._speak_queue.empty():
                    self._resume_after_speech()
        # drain on shutdown
        while not self._speak_queue.empty():
            self._speak_queue.get_nowait()

    def _start_followup_capture(self, due_in: float, delay: float) -> None:
        """Remember microphone frames from the end of device playback until the gate arms."""
        now = time.monotonic()
        self._followup_tail.clear()
        self._followup_samples = 0
        self._followup_start = now + max(0.0, due_in)
        self._followup_end = self._followup_start + max(0.0, delay)
        self._followup_capture = True

    def _capture_followup_tail(self, pcm: bytes, ts_ms: float) -> None:
        if not self._followup_capture or self.gate is None or self.gate.armed:
            return
        now = time.monotonic()
        if now < self._followup_start:
            return
        if now > self._followup_end + 0.25:
            self._followup_capture = False
            return
        # Bound by the configured window plus a margin for a late task, not the old
        # hard-coded 2 s: a device-AEC plug may use a several-second followup_delay_ms.
        window_s = max(1.0, self._followup_end - self._followup_start) + 0.25
        limit = int(self.cfg.audio.voice.rate * window_s)
        if self._followup_samples + len(pcm) // 2 > limit:
            while self._followup_tail and self._followup_samples + len(pcm) // 2 > limit:
                _ts, old = self._followup_tail.popleft()
                self._followup_samples -= len(old) // 2
        self._followup_tail.append((ts_ms, pcm))
        self._followup_samples += len(pcm) // 2

    async def _replay_followup_tail(self) -> None:
        if not self.effective.voice.followup_replay:
            self._followup_tail.clear()
            self._followup_samples = 0
            self._followup_capture = False
            return
        tail = list(self._followup_tail)
        self._followup_tail.clear()
        self._followup_samples = 0
        self._followup_capture = False
        if not tail or self._writer is None or self._connected is None:
            return
        for ts_ms, pcm in tail:
            await self._feed_gate(pcm, ts_ms, self._writer, self._connected)
        self._followup_replayed_ms = round(
            sum(len(pcm) // 2 for _ts, pcm in tail) * 1000.0
            / self.cfg.audio.voice.rate, 1)
        log.info("follow-up gate replayed %.0f ms of gated tail",
                 self._followup_replayed_ms)

    def _followup_wait_ms(self, override_ms: int | None = None) -> int:
        """The finite follow-up window: min(session timeout, voice cap, path override).

        The gate used to be armed for the whole session timeout (15 s), which made a
        closed session look like an open listening window (04:15:43 speech, no wake).
        `followup_max_wait_ms` (8 s by default) is the ceiling; a shorter music hold or
        a shorter session timeout still wins. `session.timeout_s=0` means the session
        never times out, so the cap alone is the window: it must not turn into 0 ms.
        The follow-up cap itself being 0 means "do not open a window at all".
        """
        cap_ms = int(self.effective.voice.followup_max_wait_ms)
        # A disabled session timeout is "forever", not "zero seconds": multiplying it
        # by 1000 and taking min() with the cap made every follow-up window 0 ms.
        wait_ms = (cap_ms if self.cfg.session.timeout_s <= 0
                   else min(int(self.cfg.session.timeout_s * 1000), cap_ms))
        if override_ms is not None:
            wait_ms = min(wait_ms, max(0, int(override_ms)))
        return max(0, wait_ms)

    async def _arm_followup_later(self, delay: float,
                                  max_wait_ms: int | None = None,
                                  reason: str = "reply drained") -> None:
        """Open the follow-up gate after the speaker has finished, not after sending."""
        try:
            await asyncio.sleep(max(0.0, delay))
        except asyncio.CancelledError:
            raise
        gate = self.gate
        if self._owner is not None:
            # A song resumed behind the reply, or a reminder took the line: the follow-up
            # window must not open over our own audio, or its lyrics become the next
            # request. The wake word is the way in while anything owns the speaker.
            log.info("follow-up gate stays shut: %s owns the speaker", self._owner)
            self._music_hold_until = None   # nothing is held; resume next tick
            return
        wait_ms = self._followup_wait_ms(max_wait_ms)
        if wait_ms <= 0:
            # A zero cap is a real choice: every sentence needs the wake word. Do not
            # arm at all (arming for 0 would still accept the first block before the
            # gate's own timeout check runs).
            log.info("follow-up gate stays shut: follow-up window disabled (0 s)")
            if max_wait_ms is not None:
                self._music_hold_until = None   # nothing is held; resume next tick
            return
        if gate is not None and not gate.armed and not self._stop.is_set():
            log.info("follow-up gate open (%.1f s, %s)", wait_ms / 1000.0, reason)
            gate.arm(followup=True, max_wait_ms=wait_ms)
            await self._replay_followup_tail()

    def _disarm_gate_for_our_audio(self) -> None:
        """Our own playback closes an armed follow-up gate.

        The delayed re-arm in `_speak_loop` protects the window a reply *opens*; it says
        nothing about the window the *previous* reply left open when a farewell or a
        reminder starts sounding 15 s later. Measured 2026-10-22 20:16: the goodbye's
        echo survived the AEC residual, VAD closed a segment on it, ASR heard it as
        「没事的话，我先下吧。」 and the model answered its own farewell. Disarm rather
        than hold: a held gate replays its buffer when it lifts, and the newest 600 ms of
        a two-second goodbye is still our voice.
        """
        if self._disarm_gate("our own audio is about to sound"):
            log.info("gate: disarmed, our own audio is about to sound")

    def _disarm_gate(self, reason: str = "") -> bool:
        """Close the listening gate through one choke point.

        Every disarm also ends a wake's stale-audio block: a reminder or a console
        announcement reaching an armed gate disarms it without an utterance or an
        `idle_events` change, and the previous turn's queued speech must not stay
        suppressed afterwards (2026-10-26 review, fourth/fifth paths). Repeated calls
        and the `_feed_gate` armed->disarmed check all assign the same boolean on the
        event loop, so they are idempotent and cannot race.
        """
        gate = self.gate
        if gate is None:
            return False
        if gate.armed:
            gate.disarm()
            self._stale_speech_blocked = False
            return True
        return False

    async def _apply_led(self, effect: str, repaint: bool = False) -> None:
        adapter = self.adapter
        if adapter is None:
            return
        try:
            await adapter.set_led(effect, repaint=repaint)
        except Exception as exc:  # noqa: BLE001 - a light is never worth an outage
            log.info("led: %s", exc)

    def _handle_device_key(self, event: _VendorEvent) -> None:
        """Apply one physical-key signal (task-18; volume writeback task-19).

        Mute is device-sovereign: ``mute``/``unmute`` update the state, paint the
        red ring (or hand the ring back on unmute) and make the next wake skip its
        cue. A volume key already moved the device's mixer, so it is adopted into
        the in-memory config target (``_apply_volume_key``) and the control plane
        persists the same step; without that the reconciler would put the pre-key
        level back on its next tick.
        """
        kind = event.kind
        text = event.text
        if kind in ("mute", "unmute"):
            muted = kind == "mute"
            if text in ("muted", "unmuted"):
                muted = text == "muted"
            self.device_muted = muted
            self._muted_known = True
            self.key_events[kind] = self.key_events.get(kind, 0) + 1
            self.last_key = (kind, text)
            log.info(
                "device key: %s (observed=%s); wake cue %s, ring %s",
                kind,
                text or "?",
                "suppressed" if muted else "restored",
                "red" if muted else "restored",
            )
            if muted:
                self._request_led("muted", repaint=True)
            else:
                gate = self.gate
                listening = bool(gate is not None and getattr(gate, "armed", False))
                self._request_led("listening" if listening else "off", repaint=True)
        elif kind in ("volume_up", "volume_down"):
            self.key_events[kind] = self.key_events.get(kind, 0) + 1
            self.last_key = (kind, text)
            self._apply_volume_key(kind, text)
        elif kind == "action":
            self.key_events[kind] = self.key_events.get(kind, 0) + 1
            self.last_key = (kind, text)
            log.info("device key: action button (observed=%s)", text or "?")

    def _apply_volume_key(self, kind: str, text: str = "") -> None:
        """Adopt one physical volume key into this process's config target (task-19).

        The key itself already moved the mixer; this records where it landed, so
        ``_reconcile_volume`` targets the new level instead of pulling the mixer
        back to the pre-key config. The write is deliberately *not* repeated on the
        device here - a second ramp on top of the button's own would fight it.

        Persistence is the control plane's job (it owns config.yaml): the plane
        already forwards every drained signal as ``VendorSignalMsg``, and the app
        applies the identical step from ``volume_keys.volume_key_target`` to the
        same config value. ``volume = 0`` stays vendor-owned (see that helper).
        """
        self.last_user_volume_at = time.monotonic()
        dev = next((d for d in self.cfg.devices if d.id == self.device_id), None)
        if dev is None:
            return
        percent = volume_key_target(dev.volume, kind)
        if percent is None:
            log.info(
                "device key: %s (observed=%s) ignored (volume 0 = vendor owns the mixer)",
                kind, text or "?",
            )
            return
        before = dev.volume
        if percent == before:
            log.info(
                "device key: %s (observed=%s) at the %d%% limit", kind, text or "?", percent
            )
            return
        dev.volume = percent
        # The best current estimate for /health and for the read-back comparison:
        # the key moved the mixer to (about) the new target, and the next metrics
        # read replaces it with the device's own report.
        self.device_volume = round(percent * 255 / 100)
        log.info(
            "device key: %s (observed=%s), target %d%% -> %d%% (control plane persists it)",
            kind,
            text or "?",
            before,
            percent,
        )

    def _seed_device_muted(self) -> None:
        """Adopt the adapter's reported mute state before the first key event."""
        adapter = self.adapter
        if adapter is None or self._muted_known:
            return
        try:
            health = dict(adapter.health() or {})
        except Exception:  # noqa: BLE001 - diagnostics must not block capture
            return
        value = health.get("muted")
        if value is True:
            # Only "muted" is actionable here: seeding a false as *known* before the
            # device has spoken would make a later real mute_state look unchanged.
            self.device_muted = True
            self._muted_known = True
            log.info("device muted at connect; the wake cue stays off")

    def _request_led(self, effect: str, repaint: bool = False) -> None:
        """Paint the newest LED state and abandon any older paint still in flight.

        `LedState` is fire-and-forget on the control side (an animation must not queue
        behind PLAY_PCM), so starting one independent task per message left the
        completion order up to the adapter. A slow "listening" script could finish
        after a later "off" and leave the ring green after the session closed. The
        newest state is the only one worth painting, so the previous task is cancelled
        as soon as a new one arrives.

        While the device is muted the ring stays red: every later state (listening,
        thinking, a stale off) is replaced by ``muted``, which is the one state the
        key owns until it is pressed again (task-18).
        """
        if self.device_muted:
            effect = "muted"
        previous = self._led_task
        self._led_task = asyncio.create_task(
            self._apply_led(effect, repaint), name="led")
        if previous is not None and not previous.done():
            previous.cancel()

    def _flush_speech(self) -> None:
        self._release("dialogue")
        self._speak_queued_s = 0.0
        # The producer's burst was cancelled: nothing more is expected, and any pending
        # "the device will run dry" deadline describes audio the user just silenced.
        self._speak_finished = True
        self._starvation_due = None
        self._speak_generation += 1
        # The queue is gone; the device's count for the old player is meaningless now.
        # The next reply opens a fresh player (and resets the credit again by generation).
        self._credit.reset()
        while not self._speak_queue.empty():
            try:
                self._speak_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _note_speaker_link(self, connected: bool) -> None:
        """Drop queued speech when the speaker's link goes live -> lost (P0b-A).

        The control plane pushes a whole answer faster than real time, so at the moment
        the speaker drops `_speak_queue` still holds the rest of the sentence. The bridge
        resets its own frame buffer and stream state on link loss (house-ear 9065d19);
        without this purge the plane would replay the half sentence into the next
        session. The purge is `_flush_speech` - the same thing a barge does - and it
        sends nothing to the device: the link is already gone, so an EOS/stop_playback
        would only race the adapter's reconnect.

        Only the transition purges. `_speaker_link_up` starts as None, so a plane that
        boots while the speaker is still connecting records False and stays quiet; the
        metrics loop ticks every 2 s and must not re-purge an empty queue forever.
        """
        was_up = self._speaker_link_up
        self._speaker_link_up = connected
        if was_up is not True or connected:
            return
        chunks = self._speak_queue.qsize()
        seconds = self._speak_queued_s
        self._flush_speech()
        room = str(getattr(self.device_cfg, "room", "") or self.device_id)
        log.info("link lost: dropped %d speech chunks (%.1f s) for room %s",
                 chunks, seconds, room)

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
        if self._speech_paused:
            # Already paused: the wake path cancels, then _claim("cue") cancels again
            # before the first cancel has landed, and a second rewind ate PLAY_LAG
            # twice (5.5 -> 4.9 s measured 20:54:03).
            return
        task = self._playback_task
        if task is None or task.done() or self._file_cfg is None:
            return
        target = self.effective.playback.rate
        self._file_position = max(0, self._file_position - int(self.PLAY_LAG_SECONDS * target))
        task.cancel()
        self._speech_paused = True
        log.info("playback paused for speech at %.1f s", self._file_position / target)

    def _resume_after_speech(self) -> None:
        if self._paused_explicit:
            # 「暂停」 was the user's own hand, not the dialogue's duck: the song waits
            # for 「继续」, and an answer ending must not lift it.
            return
        if not self._speech_paused or self._file_cfg is None:
            return
        path, loop, gain = self._file_cfg
        # The claim first, the state second: the grace branch (0.4 s after the last
        # block was *sent*) can race a reply still holding the speaker. Consuming the
        # pause and starting the task anyway left the music ownerless - audio kept
        # playing (play_file does not need the claim), no later path retried the
        # claim, and the follow-up window then opened over the song's "free" speaker
        # (measured 21:13, reproduced as a test).
        if not self._claim("music"):
            return
        self._speech_paused = False
        self._turn_pending = False
        self._playback_task = asyncio.create_task(
            self._play_file(path, loop, gain, offset=self._file_position),
            name="playback",
        )
        log.info("playback resumed after speech at %.1f s",
                 self._file_position / self.effective.playback.rate)

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
        self._music_hold_until = None
        if self._paused_explicit and self._file_cfg is not None:
            # A hand-parked song makes no sound. StopAudio here is for the speech
            # tail and the device (the vendor took the line mid-song and kills our
            # reply with it); throwing the parked position away would break the
            # give-back (V4). The cue is gone, the park survives.
            if self.adapter is not None:
                try:
                    await self.adapter.stop_playback(self.cfg.audio.playback.fade_out_ms)
                    self._device_audio_pending = False
                except Exception as exc:  # noqa: BLE001 - the speaker may be gone
                    log.info("playback: stop failed: %s", exc)
            return
        self._speech_paused = False
        self._paused_explicit = False
        self._file_cfg = None
        self._turn_pending = False
        if self.adapter is not None:
            try:
                await self.adapter.stop_playback(self.cfg.audio.playback.fade_out_ms)
                self._device_audio_pending = False
            except Exception as exc:  # noqa: BLE001 - the speaker may be gone
                log.info("playback: stop failed: %s", exc)

    def metrics(self) -> Metrics:
        snapshot = self._build_metrics()
        self._peak_hold = 0.0
        return snapshot

    def _build_metrics(self) -> Metrics:
        health: dict = {}
        link_known = False
        if self.adapter is not None:
            try:
                health = dict(self.adapter.health() or {})
                link_known = True
            except Exception:  # noqa: BLE001 - diagnostics must not break the plane
                # An adapter that cannot answer is "unknown", not "lost": a diagnostic
                # hiccup must never purge a reply.
                health = {}
        if link_known:
            self._note_speaker_link(bool(health.get("connected")))
        return Metrics(
            uptime_s=round(time.monotonic() - self.started, 1),
            aec_enabled=self.aec is not None,
            aec_erle_db=self.aec.erle_db if self.aec is not None else None,
            aec_channel=self.aec.selected if self.aec is not None else 0,
            ref_active=bool(self.aec.ref_active) if self.aec is not None else False,
            wake_count=self.wake_count,
            wake_candidates=self.wake_candidates,
            wake_admitted=self.wake_admitted,
            wake_admit_timeouts=self.wake_admit_timeouts,
            wake_admit_latency_ms=self.wake_admit_latency_ms,
            wake_rejected=dict(self.wake_rejections),
            device_wakes=self.device_wakes,
            device_wake_turns=self.device_wake_turns,
            device_wake_barges=self.device_wake_barges,
            device_wake_debounced=self.device_wake_debounced,
            device_wake_invalid=self.device_wake_invalid,
            device_wake_suppressed=self.device_wake_suppressed,
            device_wake_barge_suppressed=self.device_wake_barge_suppressed,
            device_wake_rate_limited=self.device_wake_rate_limited,
            device_wake_conflicts=self.device_wake_conflicts,
            device_wake_interrupts=self.device_wake_interrupts,
            device_wake_no_vad=self.device_wake_no_vad,
            device_wake_fallbacks=self.device_wake_fallbacks,
            device_wake_degraded=self.device_wake_degraded,
            device_wake_dropped=self.device_wake_dropped,
            device_wake_replay_missed=self.device_wake_replay_missed,
            device_wake_last_score=self.device_wake_last_score,
            # Capture/signal supervision (P1-2/P1-3): a plane that is "running" but no
            # longer capturing must be visible in /health, not only in its stderr.
            capture_degraded=self.capture_degraded,
            capture_errors=self.capture_errors,
            capture_restarts=self.capture_restarts,
            capture_last_error=self.capture_last_error,
            signal_errors=self.signal_errors,
            signal_dropped=self.signal_dropped,
            signal_invalid=self.signal_invalid,
            signal_last_error=self.signal_last_error,
            task_errors=self.task_errors,
            task_last_error=self.task_last_error,
            kws_enabled=self.wake is not None,
            kws_error=self.kws_error,
            vad_enabled=self.gate is not None,
            utterances=self.utterances,
            gate_armed=bool(self.gate.armed) if self.gate is not None else False,
            gate_speaking=bool(self.gate.speaking) if self.gate is not None else False,
            gate_discarded=self.gate.discarded if self.gate is not None else 0,
            gate_interrupted=self.gate.interrupted if self.gate is not None else 0,
            speaker_connected=bool(health.get("connected")),
            speaker_rejected=int(health.get("rejected") or 0),
            # Physical mute (task-18): the key's state, or the adapter's report
            # before the first key frame. /health and the v2 console read this.
            muted=bool(self.device_muted if self._muted_known else health.get("muted")),
            playback_s=round(self.playback_s, 1),
            playback_lead_ms=round(self.playback_lead_s * 1000.0, 1),
            playback_gap_count=self.playback_gap_count,
            playback_max_gap_ms=round(self.playback_max_gap_ms, 1),
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
        await self._reconcile_volume()

    async def _reconcile_volume(self) -> None:
        """The configured target wins over whatever the vendor left in the mixer.

        The vendor's own interactions rewrite mysoftvol at will (the old build
        measured 255 -> 160 minutes after setting it, and 9哥's 「音量60%」 being
        undone by the next answer). It reconciled at three call sites; this build
        already reads the value on the metrics cadence, so the check lives here.
        `devices[].volume` is a percent (0 = do not manage), mysoftvol is 0-255.
        The read-back is compared with a one-step tolerance
        (`VOLUME_RECONCILE_TOLERANCE`, in that same 0-255 unit): the vendor quantises,
        and an exact comparison rewrote the same level every ~16 s (d.7). A target that
        actually moved - config edit or `volume.set` - still differs by more than a step
        and is written here; the user path itself never consults this tolerance.

        task-19: a physical volume key is a user choice too. For
        `VOLUME_KEY_GRACE_S` after one, no write happens at all - the mixer is still
        ramping on the device and the bridge can report the pre-key level for one
        cycle. The target itself was already moved by `_apply_volume_key`, so once the
        window closes this method enforces the *new* config value (or finds it already
        within tolerance), never the pre-key one.
        """
        dev = next((d for d in self.cfg.devices if d.id == self.device_id), None)
        target = dev.volume if dev is not None else 0
        if target <= 0 or self.adapter is None or self.device_volume is None:
            return
        if (self.last_user_volume_at is not None
                and time.monotonic() - self.last_user_volume_at < VOLUME_KEY_GRACE_S):
            log.debug(
                "volume: physical key %.1fs ago, not reconciling (target %d%%)",
                time.monotonic() - self.last_user_volume_at,
                target,
            )
            return
        set_vol = getattr(getattr(self.adapter, "services", None), "set_volume", None)
        if set_vol is None:
            return
        want = round(target * 255 / 100)
        if abs(self.device_volume - want) <= VOLUME_RECONCILE_TOLERANCE:
            return
        try:
            await set_vol(want)
            self.device_volume = want
            log.info("volume: reconciled to %d (config %d%%)", want, target)
        except Exception as exc:  # noqa: BLE001 - a silent knob must never be loud
            log.debug("volume reconcile failed: %s", exc)

    async def _metrics_loop(self, writer: asyncio.StreamWriter, connected: asyncio.Event) -> None:
        ticks = 0
        level_peak = 0.0
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
                # so far compared hit levels with nothing. The window's peak is reported
                # once per LEVEL_LOG_EVERY ticks (30 s), not every 2 s - the old cadence
                # was audit P2 log noise. Aggregate in between so the line still describes
                # a full 30 s of speech, not one 2 s window.
                if snapshot.voice_peak_db is not None:
                    level_peak = max(level_peak, snapshot.voice_peak_db)
                if ticks % LEVEL_LOG_EVERY == 0:
                    if level_peak > -55.0:
                        log.info("[level] voice peak %.1f dBFS over %.0f s",
                                 level_peak, METRICS_EVERY_S * LEVEL_LOG_EVERY)
                    level_peak = 0.0
                writer.write(encode_json(snapshot))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                connected.clear()
                return

    def _accept_play_pcm(self, payload: bytes) -> None:
        """Queue one reply chunk; ignore it while the wake's own turn is not formed.

        `_stale_speech_blocked` is set by an accepted wake and cleared when that turn's
        utterance is captured: chunks arriving in between are the previous turn's audio
        (an idle goodbye still streaming) and must not be played - their echo would be
        captured as the user's words (2026-10-26 07:12).
        """
        _seq, _ts, pcm = decode_pcm(payload)
        if not pcm or self._stale_speech_blocked:
            return
        seconds = len(pcm) / 2 / self.cfg.audio.voice.rate
        if self._speak_queued_s + seconds > MAX_SPEAK_QUEUE_S:
            # Only reachable if a producer ignores SpeakFinished and keeps pushing: a
            # leak guard, not pacing.
            log.warning("speak queue over %.0f s, dropping a chunk", MAX_SPEAK_QUEUE_S)
            return
        self._speak_queued_s += seconds
        # More audio is expected: an empty queue from here until SpeakFinished is a
        # producer stall, not the end of the reply.
        self._speak_finished = False
        # The ramp belongs only where the control plane declares a sentence start
        # (SpeakStart); everything else is a slice of one continuous synthesis.
        self._speak_queue.put_nowait((pcm, self._segment_pending))
        self._segment_pending = False

    # ---------------------------------------------------------------- server
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connected = asyncio.Event()
        tasks: list[asyncio.Task] = []
        try:
            self.adapter = await self._open_adapter()
            # A device that booted (or stayed) muted reports it through adapter
            # health; adopt that before the first wake so the cue is not played.
            self._seed_device_muted()
            # One probe for both Ready and capture: probing a second time in _capture
            # was an unguarded adapter call on the critical path (P1-3).
            probe = await safe_probe(self.adapter)
            if not probe.ok:
                raise RuntimeError(f"device probe failed ({probe.as_error()})")
            info = probe.info
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
            self._ctl_writer, self._ctl_connected = writer, connected
            capture = asyncio.create_task(
                self._capture(connected, writer, info), name="capture"
            )
            tasks = [
                capture,
                asyncio.create_task(self._send_audio(writer, connected), name="send-audio"),
                asyncio.create_task(self._metrics_loop(writer, connected), name="metrics"),
                asyncio.create_task(self._speak_loop(), name="speak"),
            ]
            # P1-2/P1-3: a task that dies must not leave the process looking healthy.
            for task in tasks:
                self._watch_task(task.get_name(), task, writer)
            while not self._stop.is_set():
                kind, payload = await read_frame(reader)
                if kind is FrameType.PLAY_PCM:
                    self._accept_play_pcm(payload)
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
                            # Learn it: the user just chose this level. Without it the
                            # reconciler (own copy of the config, separate process)
                            # put the old target back within 16 s - measured 08:57.
                            for dev in self.cfg.devices:
                                if dev.id == self.device_id:
                                    dev.volume = round(int(msg.value) * 100 / 255)
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
                elif isinstance(msg, PauseMusic):
                    self._pause_for_speech()
                    self._paused_explicit = True
                    log.info("playback parked by hand (pause)")
                elif isinstance(msg, ResumeMusic):
                    self._paused_explicit = False
                    if self._speech_paused:
                        log.info("playback resumed by hand")
                    self._resume_after_speech()
                elif isinstance(msg, LedState):
                    # Never awaited: the script runs on the device for up to a couple
                    # of seconds and this loop also carries PLAY_PCM. The newest state
                    # supersedes a paint still in flight (task-17: off must win).
                    self._request_led(msg.effect, msg.repaint)
                elif isinstance(msg, SpeakStart):
                    if not self._stale_speech_blocked:
                        self._segment_pending = True
                elif isinstance(msg, ReplyFinished):
                    self._reply_finished = True
                    self._reply_hold = msg.hold_music_s
                elif isinstance(msg, SpeakFinished):
                    if self._stale_speech_blocked:
                        continue
                    # No more PLAY_PCM is expected for this burst. An empty queue is now
                    # the normal end of the reply, not an underrun.
                    self._speak_finished = True
                    log.info("speak finished, %s chunks still queued (%.1f s of audio)",
                             self._speak_queue.qsize(), self._speak_queued_s)
                elif isinstance(msg, WakeAdmit):
                    pending = self._pending_wakes.get(msg.wake_id)
                    if pending is not None and not pending.done():
                        pending.set_result((True, "admitted"))
                elif isinstance(msg, WakeReject):
                    pending = self._pending_wakes.get(msg.wake_id)
                    if pending is not None and not pending.done():
                        pending.set_result((False, msg.reason))
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
    env = env_get("RUI_AUDIO_SOCK")
    if env:
        return resolve_socket_path(Path(env))
    return resolve_socket_path(Path.home() / ".local" / "state" / "rui" / "audio.sock")


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
    return Path(tempfile.gettempdir()) / f"rui-{digest}.sock"


def build_plane(cfg: Config, device_id: str, socket_path: Path | None = None) -> AudioPlane:
    # The plane is a separate process (spawned by the control plane, or `rui audio`),
    # so it must load the downstream adapters itself; issue #7 Q1 hook. Fail-soft.
    from ..device.loader import load_adapter_plugins

    load_adapter_plugins()
    return AudioPlane(cfg, device_id, resolve_socket_path(socket_path or default_socket_path()))


def _configure_logging(level: str) -> None:
    """The plane is a separate process with its own stderr: without this, `system.log_level`
    does nothing and every gate/playback diagnostic is invisible."""
    setup_logging(level)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rui-audio", description="rui audio plane")
    parser.add_argument("--config", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--socket", default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    # The plane builds the device adapter itself (a second process from the control
    # plane); without this the adapter reads keys from the environment only and a key
    # entered in the console never reaches the device token check (security audit).
    secrets.configure(cfg.system.data_dir)
    _configure_logging(cfg.system.log_level)
    plane = build_plane(cfg, args.device, Path(args.socket) if args.socket else None)
    try:
        asyncio.run(plane.serve())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
