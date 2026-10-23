"""One device stream, one owner.

Measured 2026-10-22: a reply spoken while 嘻唰唰 was playing interleaved two PCM streams
into the same player, and the words came out stuttering. The priority ladder is what stops
that from coming back with music, reminders and cues in the mix.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

import numpy as np

from house_ear.audio.ack import tone
from house_ear.audio.process import AudioPlane
from house_ear.audio.protocol import PlayFile
from house_ear.config import Config


class StubAdapter:
    def __init__(self) -> None:
        self.played = 0
        self.stops = 0

    async def play(self, pcm: bytes, sample_rate: int = 48000, channels: int = 1) -> None:
        self.played += 1

    async def stop_playback(self, fade_ms: int = 120) -> None:
        self.stops += 1

    def health(self) -> dict:
        return {"connected": True}


def make_plane(tmp_path: Path) -> tuple[AudioPlane, StubAdapter]:
    cfg = Config()
    cfg.audio.aec.enabled = False
    cfg.system.data_dir = str(tmp_path)
    plane = AudioPlane(cfg, "living", tmp_path / "out.sock")
    adapter = StubAdapter()
    plane.adapter = adapter  # type: ignore[assignment]
    return plane, adapter


def write_wav(path: Path, seconds: float = 1.0) -> None:
    import wave

    rate = 16000
    pcm = (np.zeros(int(rate * seconds), dtype="<i2")).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def test_a_cue_preempts_music_and_a_reply_waits_for_the_cue(tmp_path: Path) -> None:
    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        assert plane._claim("music") is True

        # The cue is 320 ms and must be heard over the song.
        plane._start_ack(tone(plane.cfg))
        assert plane._owner == "cue"

        # A reply must not talk over the cue, and must not be refused forever either.
        assert plane._claim("dialogue") is False
        plane._release("cue")
        assert plane._claim("dialogue") is True

        plane._ack_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await plane._ack_task

    asyncio.run(run())


def test_music_asked_for_during_a_reply_starts_when_the_reply_ends(tmp_path: Path) -> None:
    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        song = tmp_path / "song.wav"
        write_wav(song)

        assert plane._claim("dialogue") is True
        plane._start_playback(PlayFile(path=str(song), loop=False, gain=1.0))
        assert plane._owner == "dialogue", "the reply keeps the speaker"
        assert plane._pending_play is not None, "the request is remembered, not dropped"

        plane._release("dialogue")
        assert plane._owner == "music"
        assert plane._playback_task is not None
        plane._stop.set()
        plane._playback_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await plane._playback_task

    asyncio.run(run())


def test_flushing_an_answer_hands_the_speaker_back(tmp_path: Path) -> None:
    plane, _ = make_plane(tmp_path)
    plane._claim("dialogue")
    plane._flush_speech()
    assert plane._owner is None


def test_the_owner_is_a_metric_not_a_log_line(tmp_path: Path) -> None:
    plane, _ = make_plane(tmp_path)
    plane._claim("music")

    async def read() -> str:
        return plane.metrics().output_owner

    assert asyncio.run(read()) == "music"


class StubGate:
    """The plane only touches `armed`, `arm()` and `disarm()` on the real gate."""

    def __init__(self, *, armed: bool = False) -> None:
        self.armed = armed
        self.arms = 0
        self.disarms = 0

    def arm(self, **_kwargs: object) -> None:
        self.arms += 1
        self.armed = True

    def disarm(self) -> None:
        self.disarms += 1
        self.armed = False


def test_our_own_speech_closes_the_followup_gate(tmp_path: Path) -> None:
    """Measured 2026-10-22 20:16: the farewell sounded into the follow-up window the
    previous reply had left open, the echo came back as 「没事的话，我先下吧。」 and the
    model answered its own goodbye. Whatever we sound closes that window first."""
    async def run() -> None:
        plane, adapter = make_plane(tmp_path)
        gate = StubGate(armed=True)
        plane.gate = gate  # type: ignore[assignment]

        plane._speak_queued_s = 0.1
        plane._speak_queue.put_nowait((np.zeros(1600, dtype="<i2").tobytes(), True))
        task = asyncio.create_task(plane._speak_loop())
        try:
            for _ in range(100):
                if adapter.played:
                    break
                await asyncio.sleep(0.02)
            assert adapter.played >= 1, "the reply never reached the speaker"
            assert gate.disarms == 1 and gate.armed is False
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(run())


def test_music_starting_closes_the_followup_gate(tmp_path: Path) -> None:
    """Same hole without the race: lyrics are continuous, so a song asked for while a
    follow-up window is open would transcribe itself into a turn every time."""
    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        gate = StubGate(armed=True)
        plane.gate = gate  # type: ignore[assignment]
        song = tmp_path / "song.wav"
        write_wav(song, seconds=0.2)

        plane._claim("music")
        await plane._play_file(song, loop=False, gain=1.0)

        assert gate.disarms == 1 and gate.armed is False

    asyncio.run(run())


def test_no_followup_window_opens_over_our_own_music(tmp_path: Path) -> None:
    """A reply that ends with the song resuming behind it must not open the follow-up
    window over the music; the wake word is the way in while music owns the speaker."""
    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        gate = StubGate()
        plane.gate = gate  # type: ignore[assignment]

        plane._claim("music")
        await plane._arm_followup_later(0.01)
        assert gate.arms == 0, "the window opened over the music"

        plane._release("music")
        await plane._arm_followup_later(0.01)
        assert gate.arms == 1, "the window never opened after the music ended"

    asyncio.run(run())


def test_the_song_waits_out_the_whole_turn(tmp_path: Path) -> None:
    """Measured 20:54:03: the wake paused the music, then an empty speak queue brought
    it back 0.4 s later - over the user's request - and the resumed music closed the
    freshly armed gate. An empty queue is not "speech over": the song comes back only
    once the gate has closed (the reply drained, or the no-speech disarm)."""
    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        gate = StubGate(armed=True)
        plane.gate = gate  # type: ignore[assignment]
        song = tmp_path / "song.wav"
        write_wav(song, seconds=0.3)
        plane._file_cfg = (song, True, 1.0)
        plane._speech_paused = True
        plane._file_position = 0

        task = asyncio.create_task(plane._speak_loop())
        try:
            await asyncio.sleep(0.8)   # one empty-queue tick past the 0.5 s poll
            assert plane._speech_paused is True, "the music came back while the gate was armed"

            gate.armed = False         # the turn is over (drained, or no-speech disarm)
            await asyncio.sleep(0.8)
            assert plane._speech_paused is False, "the music never came back after the turn"
            assert plane._playback_task is not None and not plane._playback_task.done()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if plane._playback_task is not None:
                plane._playback_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await plane._playback_task

    asyncio.run(run())


def test_the_song_stays_down_between_the_question_and_the_answer(
        tmp_path: Path, monkeypatch) -> None:
    """Measured 21:07: the gate closes when the utterance ends, and "gate closed" alone
    let the song blip back in for the ASR+LLM second before the answer. A live turn
    (utterance fired, reply not finished) keeps it down; ReplyFinished hands the song
    to the follow-up window, and it returns when that window closes."""
    import house_ear.audio.process as proc

    monkeypatch.setattr(proc, "MUSIC_FOLLOWUP_MS", 300)

    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        gate = StubGate(armed=True)
        plane.gate = gate  # type: ignore[assignment]
        song = tmp_path / "song.wav"
        write_wav(song, seconds=0.3)
        plane._file_cfg = (song, True, 1.0)
        plane._speech_paused = True
        plane._file_position = 0
        # the user spoke; the turn is alive, the gate closed with the utterance
        plane._turn_pending = True
        plane._turn_pending_ts = time.monotonic()
        gate.armed = False

        task = asyncio.create_task(plane._speak_loop())
        try:
            await asyncio.sleep(0.8)   # one empty-queue tick
            assert plane._speech_paused is True, "the song blipped in before the answer"

            plane._reply_finished = True   # ReplyFinished: the follow-up window arms
            await asyncio.sleep(1.6)       # arm delay (~1 s) + the 6 s window would follow
            assert gate.arms >= 1, "the follow-up window never opened"
            gate.armed = False             # the window's max_wait expiry
            await asyncio.sleep(0.7)
            assert plane._speech_paused is False, "the song never came back after the window"
            assert plane._playback_task is not None and not plane._playback_task.done()

            # the grace: a turn that died without a reply still gives the music back
            plane._speech_paused = True
            plane._file_cfg = (song, True, 1.0)
            plane._turn_pending = True
            plane._turn_pending_ts = time.monotonic() - 15.0   # past the 12 s grace
            await asyncio.sleep(0.8)
            assert plane._speech_paused is False, "a dead turn kept the song down forever"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if plane._playback_task is not None:
                plane._playback_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await plane._playback_task

    asyncio.run(run())


def test_pause_for_speech_rewinds_the_lag_once(tmp_path: Path) -> None:
    """The wake path cancels, then _claim("cue") cancels again before the first cancel
    lands; two rewinds ate PLAY_LAG twice (5.5 -> 4.9 s measured 20:54:03)."""
    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        song = tmp_path / "song.wav"
        write_wav(song, seconds=0.3)
        plane._file_cfg = (song, True, 1.0)
        rate = plane.cfg.audio.playback.rate
        plane._file_position = int(5.5 * rate)
        plane._playback_task = asyncio.create_task(asyncio.sleep(10))
        try:
            plane._pause_for_speech()
            plane._pause_for_speech()
            once = int(5.5 * rate) - int(plane.PLAY_LAG_SECONDS * rate)
            assert plane._file_position == once
        finally:
            plane._playback_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await plane._playback_task

    asyncio.run(run())


class RecordingWriter:
    """Collects the control messages the plane emits (PlayFinished and friends)."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


def test_a_natural_end_emits_play_finished_but_a_cancel_never_does(tmp_path: Path) -> None:
    """The music queue advances on play_finished only: a pause or barge cancels the
    task, and a cancelled file must not look finished (service would skip a track)."""

    from house_ear.audio.protocol import parse_control

    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        writer = RecordingWriter()
        connected = asyncio.Event()
        connected.set()
        plane._ctl_writer, plane._ctl_connected = writer, connected   # type: ignore[assignment]
        song = tmp_path / "song.wav"
        write_wav(song, seconds=0.2)
        long_song = tmp_path / "long.wav"
        write_wav(long_song, seconds=2.0)

        await plane._play_file(song, loop=False, gain=1.0)
        events = [parse_control(f[5:]) for f in writer.frames]
        assert [type(m).__name__ for m in events] == ["PlayFinished"], events
        assert events[0].path == str(song)

        # A cancelled one (the pause path) emits nothing. The file must outlive the
        # cancel: shorter than the prebuffer, the whole file streams in one burst and
        # finishes "naturally" before the cancel lands.
        plane._file_cfg = (long_song, False, 1.0)
        task = asyncio.create_task(plane._play_file(long_song, loop=False, gain=1.0))
        await asyncio.sleep(0.05)
        plane._speech_paused = True          # what _pause_for_speech would have set
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert len(writer.frames) == 1, "a cancelled play must not announce a finish"

    asyncio.run(run())


def test_an_explicit_pause_is_not_lifted_by_the_speech_path(tmp_path: Path) -> None:
    """「暂停」 is the user's own hand: the reply-drain resume (and everything else
    automatic) must leave the song parked until ResumeMusic (old build's
    pause(for_speech=False), the one pause that survived every hook)."""
    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        song = tmp_path / "song.wav"
        write_wav(song, seconds=2.0)   # must still be playing when the asserts look
        plane._file_cfg = (song, False, 1.0)
        plane._speech_paused = True
        plane._paused_explicit = True
        plane._file_position = 0

        task = asyncio.create_task(plane._speak_loop())
        try:
            await asyncio.sleep(0.8)   # an empty-queue tick: the auto-resume window
            assert plane._speech_paused is True, "an automatic path lifted 「暂停」"

            plane._paused_explicit = False
            await asyncio.sleep(0.8)
            assert plane._speech_paused is False, "ResumeMusic did not resume"
            assert plane._playback_task is not None and not plane._playback_task.done()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if plane._playback_task is not None:
                plane._playback_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await plane._playback_task

    asyncio.run(run())


def test_music_stays_down_for_the_followup_window(tmp_path: Path,
                                                  monkeypatch) -> None:
    """Measured 08:30-08:35 ("对话间隙音乐出声音"): the reply drained, music
    resumed, and only then did the follow-up window arm. The song now stays
    down for the whole window and returns when it closes."""
    import house_ear.audio.process as proc

    monkeypatch.setattr(proc, "MUSIC_FOLLOWUP_MS", 700)

    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        gate = StubGate(armed=True)
        plane.gate = gate  # type: ignore[assignment]
        song = tmp_path / "song.wav"
        write_wav(song, seconds=30.0)
        plane._file_cfg = (song, False, 1.0)
        plane._file_position = 0
        plane._speech_paused = True          # the wake's barge parked the song
        gate.armed = True                    # the wake armed the gate

        plane._reply_finished = True
        plane._speak_queued_s = 0.2
        plane._speak_queue.put_nowait((np.zeros(3200, dtype="<i2").tobytes(), True))
        task = asyncio.create_task(plane._speak_loop())
        try:
            await asyncio.sleep(1.0)         # send + drain tick
            gate.armed = False               # the utterance closed the gate; window arms
            await asyncio.sleep(1.4)         # arm (~1 s) + window (0.7 s)
            assert plane._speech_paused is True, "the song came back during the window"
            assert gate.arms >= 1, "the follow-up window never opened over the ducked song"
            gate.armed = False               # the stub gate's max_wait expiry, in production
            await asyncio.sleep(0.7)
            assert plane._speech_paused is False, "the song never came back"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if plane._playback_task is not None:
                plane._playback_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await plane._playback_task

    asyncio.run(run())


def test_volume_reconciles_to_the_configured_target(tmp_path: Path) -> None:
    """The vendor rewrites mysoftvol at will; the configured percent wins, on the
    metrics cadence instead of the old build's three call sites. 0 = hands off."""
    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        from house_ear.config.schema import DeviceConfig
        plane.cfg.devices.append(DeviceConfig(id="living", adapter="xiaoai",
                                              host="192.0.2.10", volume=86))
        writes: list[int] = []

        class Svc:
            async def set_volume(self, value: int) -> None:
                writes.append(value)

        async def read() -> int:
            return writes[-1] if writes else 160  # what the vendor left behind

        plane.adapter.services = Svc()            # type: ignore[attr-defined]
        plane.adapter.read_volume = read          # type: ignore[method-assign]

        await plane._read_device_volume()
        assert writes == [219], writes            # 86% * 2.55, one corrective write
        await plane._read_device_volume()
        assert writes == [219], "an in-range volume must not be rewritten"

        plane.cfg.devices[0].volume = 0           # hands off
        plane.device_volume = 5
        await plane._read_device_volume()
        assert writes == [219], "volume 0 means do not manage"

    asyncio.run(run())


def test_a_reply_can_cancel_the_music_window(tmp_path: Path, monkeypatch) -> None:
    """Volume confirmations want the song back the moment the reply drains:
    ReplyFinished(hold_music_s=0) skips both the window and the arm."""
    import house_ear.audio.process as proc

    monkeypatch.setattr(proc, "MUSIC_FOLLOWUP_MS", 5000)

    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        gate = StubGate()
        plane.gate = gate  # type: ignore[assignment]
        song = tmp_path / "song.wav"
        write_wav(song, seconds=30.0)
        plane._file_cfg = (song, False, 1.0)
        plane._file_position = 0
        plane._speech_paused = True
        plane._reply_finished = True
        plane._reply_hold = 0.0        # what ReplyFinished(hold_music_s=0) sets

        task = asyncio.create_task(plane._speak_loop())
        try:
            await asyncio.sleep(0.9)   # one drain tick
            assert gate.arms == 0, "no follow-up window for a 0 s hold"
            assert plane._speech_paused is False, "the song did not come back"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if plane._playback_task is not None:
                plane._playback_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await plane._playback_task

    asyncio.run(run())
