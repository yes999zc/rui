"""Control-plane API. The browser never sees a secret."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from ... import __version__, secrets
from ...audio.ack import ack_wav_path
from ...audio.ack_render import render_ack_wav, render_filler_pcm
from ...audio.client import AudioPlaneError, AudioPlaneSupervisor
from ...audio.kws import keyword_meta
from ...audio.protocol import PlayFile, StopAudio
from ...config.schema import CONFIG_VERSION, LED_STATES, Config
from ...config.store import ConfigError, load_config, redacted, save_config
from ...device.registry import build_adapters
from ...doctor import run_doctor
from ...session import rules
from ...session.ack import ensure_ack_wav
from ...session.text import strip_wake
from ...session.turn import TurnRunner
from ...session.vendor import VendorWindow
from ...tools import build_local_tools, build_scheduler
from ...tools.registry import ToolRegistry
from ...trace import TraceLog, TurnTrace

START = time.time()
log = logging.getLogger(__name__)
_DEFAULT_WEB_DIST = Path(__file__).resolve().parents[3].parent / "web" / "dist"
WEB_DIST = Path(os.environ.get("HOUSE_EAR_WEB_DIR", _DEFAULT_WEB_DIST))

FALLBACK = """<!doctype html><html><head><meta charset="utf-8"><title>house-ear</title>
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;max-width:640px;margin:12vh auto;padding:0 24px;color:#111}
code{background:#f4f4f5;padding:2px 6px;border-radius:4px}</style></head>
<body><h1>house-ear</h1><p>Control plane is running. The console is not built yet:</p>
<p><code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code></p>
<p>API: <code>/health</code>, <code>/api/v1/doctor</code>, <code>/api/v1/config</code></p></body></html>"""


def _light(app: FastAPI, effect: str, repaint: bool = False) -> None:
    """Paint the ring without blocking the caller.

    The shell request shares the websocket with the audio stream; the old build measured
    a 3 s resume delay from awaiting one animation, so this is always a task.
    """
    supervisor = getattr(app.state, "audio", None)
    live = supervisor.client if supervisor is not None else None
    if live is None:
        return
    log.info("led: %s%s", effect, " (repaint)" if repaint else "")
    task = asyncio.create_task(live.set_led(effect, repaint=repaint), name="led")
    tasks = getattr(app.state, "led_tasks", None)
    if tasks is not None:
        tasks.add(task)
        task.add_done_callback(tasks.discard)


def _light_after_audio(app: FastAPI, audio_bytes: int, rate: int, effect: str = "listening") -> None:
    """Paint `effect` when the audio just queued has finished playing.

    run() returns when the last chunk is queued, not played; output_owner is released
    at the same moment (the plane frees the speaker when its queue empties), so neither
    is a "the answer is over" signal. The duration is exact: PCM bytes / 2 / rate. The
    three repaints after it match what the vendor LED service needs to stay repainted.
    """
    seconds = max(0.0, audio_bytes / 2 / max(1, rate))

    async def run() -> None:
        await asyncio.sleep(seconds + 0.6)
        _light(app, effect)
        if effect != "listening":
            return
        for _ in range(3):
            await asyncio.sleep(1.2)
            _light(app, effect, repaint=True)

    asyncio.create_task(run(), name="led-after-audio")


def _speaking(app: FastAPI, live: object):
    """A speak callable that turns the ring cyan on the first block of a turn."""
    turned = {"on": False}

    async def say(pcm: bytes, **kwargs: object) -> None:
        if not turned["on"]:
            turned["on"] = True
            _light(app, "speaking")
        await live.speak(pcm, **kwargs)  # type: ignore[attr-defined]

    return say


def create_app(
    cfg: Config,
    config_path: Path | None = None,
    start_audio: bool = False,
) -> FastAPI:
    secrets.configure(cfg.system.data_dir)
    registry = ToolRegistry()
    # One assembly for everything local: information tools and reminders. Reminders used
    # to be reachable only through tools.reminders.build_tools, which nothing imported -
    # the tools existed, the scheduler existed, and the model could never call them.
    local_tools = build_local_tools(cfg)
    for spec, _handler in local_tools:
        registry.register(spec)
    adapters = build_adapters(cfg)
    traces = TraceLog()
    for dev_id, err in adapters.errors.items():
        log.warning("device %s is configured but not drivable: %s", dev_id, err)

    async def _render_cue() -> None:
        try:
            app.state.ack_note = await asyncio.wait_for(ensure_ack_wav(cfg), timeout=15)
        except TimeoutError:
            app.state.ack_note = "tts timed out: the plane plays whatever ack.wav holds"
        except Exception as exc:  # noqa: BLE001 - a cue is never worth an outage
            app.state.ack_note = f"{type(exc).__name__}: {exc}; the plane will use a tone"
        log.info("ack: %s", app.state.ack_note)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        spoken = rules.SpokenMemory()
        runner = TurnRunner(
            cfg.providers,
            sample_rate=cfg.audio.voice.rate,
            assistant=cfg.assistant,
            history_turns=cfg.session.history_rounds,
            wake_words=[w for d in cfg.devices for w in d.wake_words],
            tools=local_tools,
            spoken=spoken,
            session=cfg.session,
        )
        app.state.spoken = spoken
        # Render the spoken wake cue now, not on the wake: synthesising it inside the wake
        # path cost the old build 1.5 s and swallowed the first request (\`ack_warmer\`).
        app.state.ack_task = asyncio.create_task(render_ack_wav(cfg), name="ack-render")

        async def _warm_filler() -> None:
            # Three phrases, not one: 9哥's expected flow is 「我查一下」 for a lookup,
            # 「我想想」 for thinking, 「好的，我看看」 for a device - each pre-rendered so
            # the choice costs nothing at the moment it is spoken.
            from ...session.filler import DEVICE_TEXT, LOOK_TEXT, THINK_TEXT

            rendered: dict[str, bytes] = {}
            for line in (LOOK_TEXT, THINK_TEXT, DEVICE_TEXT):
                pcm = await render_filler_pcm(cfg, line)
                if pcm:
                    rendered[line] = pcm
            runner.filler_pcm = rendered
            app.state.filler_pcm = rendered
            log.info("filler: %d phrases ready", len(rendered))

        # Same reason as the ack: a filler that has to be synthesised when it is needed
        # costs the latency it exists to hide.
        app.state.filler_task = asyncio.create_task(_warm_filler(), name="filler-render")
        app.state.runner = runner
        app.state.last_turn = None
        vendor = VendorWindow(cfg.session.vendor_quiet_s)
        app.state.vendor = vendor
        # Warm the instances the turns will use. This used to build its own chain and
        # warm that, which loaded the ASR model twice and left the real one cold.
        await runner.warm()
        app.state.ack_note = ""
        app.state.led_tasks = set()

        if start_audio:
            supervisor = AudioPlaneSupervisor(cfg, config_path=config_path)
            try:
                await supervisor.start()
                ready = supervisor.client.ready if supervisor.client else None
                log.info(
                    "audio plane up: pid=%s device=%s capture=%s Hz/%s ch",
                    supervisor.proc.pid if supervisor.proc else "?",
                    ready.device_id if ready else "?",
                    ready.capture_rate if ready else "?",
                    ready.capture_channels if ready else "?",
                )
            except AudioPlaneError as exc:
                supervisor.error = str(exc)
                log.warning("audio plane did not start: %s", exc)
            app.state.audio = supervisor
            client = supervisor.client
            if client is not None:
                async def on_utterance(pcm: bytes) -> None:
                    if vendor.muted():
                        # The speaker's own assistant is answering; answering too would
                        # double-reply to one sentence.
                        log.info("vendor dialog, dropping our turn (%.1fs left)",
                                 vendor.remaining())
                        return
                    trace = TurnTrace(device_id=client.ready.device_id if client.ready else "")
                    if client.last_wake is not None:
                        trace.mark("wake", at_ms=client.last_wake.ts_ms)
                    text, asr_report = await runner.transcribe(pcm, trace)
                    clean = strip_wake(text, runner.wake_words)
                    if "小爱同学" in clean or clean.strip() in {"小爱", "小爱小爱"}:
                        # Text-level backstop for the vendor wake: its StartCapture signal
                        # may be missing on some client builds, and its final transcript
                        # arrives after our own ASR has already decided to answer.
                        log.info("vendor wake word in our transcript; yielding: %r", clean)
                        vendor.vendor_spoke()
                        return
                    if not clean.strip():
                        # Only the wake word arrived: the wake residue case, not a request.
                        log.info("utterance was wake residue only; no turn")
                        return
                    if app.state.spoken.is_echo(clean):
                        # Our own answer came back through the arrays: measured 2026-10-22,
                        # the speaker answered itself once per weather question.
                        log.info("own voice heard back, no turn: %r", clean)
                        return
                    if rules.is_noise(clean):
                        # 「嗯。」 after the wake word: measured on the real speaker, and it
                        # used to become a whole turn ("嗯，你说。").
                        log.info("noise, no turn: %r", clean)
                        return
                    if rules.stop_intent(clean, cfg.session.end_words):
                        spoken = await runner.say(rules.stop_reply(), _speaking(app, client), trace)
                        # No reply_finished here: the session is closing, so the plane
                        # must not open a follow-up window after the goodbye.
                        _light_after_audio(app, spoken, cfg.audio.voice.rate, "off")
                        app.state.last_turn = {
                            "transcript": clean, "reply": "", "session_action": "stop",
                            "audio_bytes": spoken, "error": "", "tools": [], "stages": {},
                        }
                        traces.add(trace)
                        log.info("stop intent: %r -> session closed", clean)
                        return
                    _light(app, "thinking")
                    try:
                        result = await runner.run(pcm, _speaking(app, client), trace, transcript=clean)
                    except Exception as exc:  # noqa: BLE001 - a broken turn must not kill the app
                        log.warning("turn failed: %s", exc)
                        return
                    if asr_report is not None:
                        result.stages.setdefault("asr", asr_report)
                    app.state.last_turn = result.to_dict()
                    traces.add(trace)
                    await client.reply_finished()
                    _light_after_audio(app, result.audio_bytes, cfg.audio.voice.rate)
                    log.info("turn: %s", result.to_dict())

                client.on_utterance = on_utterance
                client.on_vendor = vendor.vendor_spoke
                def on_wake(msg: object) -> None:
                    vendor.ours_spoke()
                    _light(app, "wake")
                    # A wake opens a conversation window; the countdown to the farewell
                    # starts again from here (and from the end of every turn).
                    runner.idle.start()
                    if getattr(msg, "barge", False):
                        # The plane already stopped the speaker and dropped its queue;
                        # this stops the answer that is still being synthesised from
                        # refilling it.
                        log.info("barge-in: %.1f s of queued speech dropped",
                                 getattr(msg, "barged_s", 0.0))
                        runner.abort()

                client.on_wake = on_wake
        # Session end. Polled rather than timed: one loop, 1 s resolution, and the
        # goodbye can never fire mid-turn (check_idle refuses while a turn is open).
        async def idle_watch() -> None:
            live = getattr(app.state, "audio", None)
            while True:
                await asyncio.sleep(1.0)
                client = getattr(live, "client", None) if live is not None else None
                if client is None:
                    continue
                # Playback is not silence: a 15 s news read or a song must not be
                # interrupted by the goodbye (the old build hit this and fixed it).
                def busy(state=live) -> bool:
                    metrics = getattr(getattr(state, "client", None), "metrics", None)
                    return bool(metrics is not None and metrics.output_owner)

                try:
                    line = await runner.check_idle(
                        _speaking(app, client), TurnTrace(device_id="session"), busy=busy
                    )
                except Exception as exc:  # noqa: BLE001 - a watchdog must not die
                    log.warning("idle watch: %s", exc)
                    continue
                if line:
                    _light(app, "off")
                    log.info("session closed: %r", line)

        app.state.idle_task = asyncio.create_task(idle_watch(), name="idle-watch")

        # P10. The reminders were a complete island: tools with 44 tests, a scheduler
        # that walks due items, and no caller anywhere. One tick fires them, one queue
        # speaks them. A due reminder must not talk over a turn or over whatever already
        # owns the speaker (a cue, an answer, a song): it waits, because its fired state
        # is already on disk and waiting loses nothing.
        reminder_queue: asyncio.Queue[str] = asyncio.Queue()
        scheduler = build_scheduler(cfg)
        scheduler.add_listener(lambda item: reminder_queue.put_nowait(item.text))
        app.state.reminders = scheduler

        async def reminder_tick() -> None:
            while True:
                await asyncio.sleep(1.0)
                try:
                    for item in scheduler.collect_due():
                        log.info("reminder due: %s", item.text)
                        reminder_queue.put_nowait(item.text)
                except Exception as exc:  # noqa: BLE001 - a tick must not die
                    log.exception("reminders: tick failed: %s", exc)

        async def reminder_speak() -> None:
            while True:
                text = await reminder_queue.get()
                waited = 0.0
                spoken = False
                while waited < 120.0:
                    supervisor = getattr(app.state, "audio", None)
                    client = supervisor.client if supervisor is not None else None
                    metrics = client.metrics if client is not None else None
                    owner = metrics.output_owner if metrics is not None else ""
                    if client is not None and not runner.busy and not owner:
                        await runner.say(
                            text, client.speak, TurnTrace(device_id="reminder")
                        )
                        spoken = True
                        break
                    await asyncio.sleep(0.5)
                    waited += 0.5
                if not spoken:
                    log.warning("reminder not spoken (no free speaker): %s", text)

        app.state.reminder_tick = asyncio.create_task(reminder_tick(), name="reminder-tick")
        app.state.reminder_task = asyncio.create_task(reminder_speak(), name="reminder-speak")

        # The cue is rendered *after* the plane is up and in the background: it is a nicety,
        # and a vendor that hangs must not delay /health or the speaker's reconnect. The
        # plane re-reads ack.wav on every wake, so a file that lands a second later is
        # picked up without a restart.
        if cfg.ack.mode == "tts":
            app.state.ack_task = asyncio.create_task(_render_cue(), name="ack-cue")
        yield
        cue_task = getattr(app.state, "ack_task", None)
        if cue_task is not None and not cue_task.done():
            cue_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await cue_task
        for name in ("reminder_tick", "reminder_task"):
            task = getattr(app.state, name, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        supervisor = getattr(app.state, "audio", None)
        if supervisor is not None:
            await supervisor.stop()

    app = FastAPI(title="house-ear", version=__version__, lifespan=lifespan)
    app.state.tools = registry  # the console and tests read the same registry turns use
    app.state.trace_log = traces  # scripts and tests read the same log the API serves

    def auth(x_auth_token: str | None = Header(default=None)) -> None:
        if not cfg.web.auth_token:
            return
        if x_auth_token != cfg.web.auth_token:
            raise HTTPException(status_code=401, detail="bad or missing X-Auth-Token")

    @app.get("/health")
    async def health() -> dict:
        supervisor = getattr(app.state, "audio", None)
        return {
            "status": "ok",
            "version": __version__,
            "config_version": cfg.version,
            "config_version_supported": CONFIG_VERSION,
            "uptime_s": round(time.time() - START, 1),
            "devices": len(adapters),
            "device_errors": len(adapters.errors),
            "audio_plane": bool(supervisor is not None and supervisor.running),
            "vendor": {
                "quiet_s": cfg.session.vendor_quiet_s,
                "muted_for": round(getattr(app.state, "vendor", None).remaining(), 1)
                if getattr(app.state, "vendor", None) else 0.0,
                "vendor_wakes": getattr(getattr(app.state, "vendor", None), "vendor_wakes", 0),
                "yielded_turns": getattr(getattr(app.state, "vendor", None), "yielded_turns", 0),
            },
            # What the plane will actually play on a wake, and why, if it was downgraded.
            "ack": {
                "mode": cfg.ack.mode,
                "note": getattr(app.state, "ack_note", ""),
                "wav": str(ack_wav_path(cfg)) if cfg.ack.mode == "tts" else "",
                "wav_exists": ack_wav_path(cfg).is_file() if cfg.ack.mode == "tts" else False,
            },
        }

    @app.get("/api/v1/doctor", dependencies=[Depends(auth)])
    async def doctor() -> dict:
        return run_doctor(cfg).to_dict()

    @app.get("/api/v1/config", dependencies=[Depends(auth)])
    async def get_config() -> dict:
        return redacted(cfg)

    # The two conversational sections are the ones a person tunes from the console. Audio
    # and providers stay on disk: changing them under a running audio plane is a different
    # problem (restart, device reconnect), not a text field.
    WRITABLE = ("assistant", "session")

    @app.put("/api/v1/config", dependencies=[Depends(auth)])
    async def put_config(patch: Annotated[dict, Body()]) -> dict:
        """Merge a patch into the sections people edit, then write it.

        Three rules, each of them a bug if dropped:
        - the file is re-read first, so a console page that never saw `providers` cannot
          write a stale copy of it back over the real one;
        - the merged result is validated as a whole `Config` before anything is written;
        - the write is `save_config` (timestamped backup + atomic replace), never a
          hand-rolled open/write.
        """
        unknown = sorted(set(patch) - set(WRITABLE))
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"only {list(WRITABLE)} can be written from the console, got {unknown}",
            )
        if config_path is None or not Path(config_path).exists():
            raise HTTPException(status_code=409, detail="there is no config file on disk to write")
        try:
            current = load_config(config_path)
        except ConfigError as exc:
            raise HTTPException(
                status_code=409, detail=f"config on disk is not readable: {exc}"
            ) from exc
        merged = current.model_dump(mode="json")
        for section, values in patch.items():
            if not isinstance(values, dict):
                raise HTTPException(status_code=400, detail=f"{section} must be an object")
            merged[section] = {**merged.get(section, {}), **values}
        try:
            updated = Config.model_validate(merged)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail=exc.errors(include_url=False)
            ) from exc
        save_config(config_path, updated)
        log.info("config written from the console: %s", sorted(patch))
        # Applied live, in two places that both matter: the app's own copy (otherwise the
        # next GET /api/v1/config - and a page reload - shows the value from startup) and
        # the runner (the prompt is rebuilt per turn, so an edited persona or band takes
        # effect on the next turn without a restart).
        cfg.assistant = updated.assistant
        cfg.session = updated.session
        # A new acknowledgement text or voice has to exist as audio before the next wake.
        previous = getattr(app.state, "ack_task", None)
        if previous is not None and not previous.done():
            previous.cancel()
        app.state.ack_task = asyncio.create_task(render_ack_wav(updated), name="ack-render")
        runner = getattr(app.state, "runner", None)
        if runner is not None:
            runner.assistant = updated.assistant
            runner.history_turns = updated.session.history_rounds
            # timeout / farewell / history live in `session`, and an edited window must
            # take effect without a restart - the console writes that section.
            runner.apply_session(updated.session)
        return redacted(updated)

    @app.get("/api/v1/device/volume", dependencies=[Depends(auth)])
    async def device_volume() -> dict:
        """The speaker's own mixer value (mysoftvol, 0-255).

        Not a duplicate of the config: `devices[].volume = 0` means the vendor owns it,
        and the vendor rewrites it (old deployment: 160 -> 255 -> 160 within minutes). The
        only way to know what the room is actually doing is to ask the device.
        """
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise HTTPException(status_code=409, detail="no audio plane connected")
        try:
            value = await client.device_volume()
        except Exception as exc:  # noqa: BLE001 - the device may be gone
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc
        if value is None:
            raise HTTPException(status_code=502, detail="the device did not report a mixer value")
        return {"volume": value, "range": "0-255"}

    @app.put("/api/v1/device/volume", dependencies=[Depends(auth)])
    async def set_device_volume(patch: Annotated[dict, Body()]) -> dict:
        """Write the mixer. Used by the tests that must know the volume they ran at."""
        value = patch.get("value")
        if not isinstance(value, int) or not 0 < value <= 255:
            raise HTTPException(status_code=422, detail="value must be an int in 1..255")
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise HTTPException(status_code=409, detail="no audio plane connected")
        try:
            applied = await client.set_device_volume(value)
        except Exception as exc:  # noqa: BLE001 - the device may be gone
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc
        log.info("device volume set to %s from the API", applied)
        return {"volume": applied}

    @app.post("/api/v1/debug/play", dependencies=[Depends(auth)])
    async def debug_play(patch: Annotated[dict, Body()]) -> dict:
        """Play a wav through the running plane (music for AEC/barge tests).

        `house-ear debug-play` spawns its own plane and cannot be used while a server is
        up, which is exactly when these tests run.
        """
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise HTTPException(status_code=409, detail="no audio plane connected")
        if patch.get("stop"):
            await client.send(StopAudio())
            return {"playing": None}
        path = str(patch.get("file") or "")
        if not Path(path).is_file():
            raise HTTPException(status_code=404, detail=f"no such file: {path}")
        await client.send(PlayFile(path=path, loop=bool(patch.get("loop", False)),
                                   gain=float(patch.get("gain", 1.0))))
        return {"playing": path}

    @app.post("/api/v1/debug/say", dependencies=[Depends(auth)])
    async def debug_say(patch: Annotated[dict, Body()]) -> dict:
        """Run one text turn as if the person had said it (the console's 试一试).

        Typing a request is the cheapest way to test a turn - no microphone, no wake
        word, no standing next to the speaker - and it exercises the same runner, tools,
        TTS and playback path as a spoken turn.
        """
        text = str(patch.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="text is required")
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise HTTPException(status_code=409, detail="no audio plane connected")
        runner = app.state.runner
        if runner.busy:
            raise HTTPException(status_code=409, detail="a turn is already running")
        trace = TurnTrace(device_id="debug")
        _light(app, "thinking")
        result = await runner.run(b"", _speaking(app, client), trace, transcript=text)
        await client.reply_finished()
        _light_after_audio(app, result.audio_bytes, cfg.audio.voice.rate)
        app.state.last_turn = result.to_dict()
        traces.add(trace)
        log.info("debug say: %r -> %r", text, result.reply)
        return {
            "reply": result.reply,
            "error": result.error,
            "tools": result.tools,
            "stages": result.stages,
            "audio_bytes": result.audio_bytes,
        }

    @app.post("/api/v1/debug/led", dependencies=[Depends(auth)])
    async def debug_led(patch: Annotated[dict, Body()]) -> dict:
        """Paint the ring directly. The only way to check colours without waking the box."""
        effect = str(patch.get("effect") or "")
        if effect not in LED_STATES:
            raise HTTPException(status_code=422, detail=f"effect must be one of {list(LED_STATES)}")
        supervisor = getattr(app.state, "audio", None)
        client = supervisor.client if supervisor is not None else None
        if client is None:
            raise HTTPException(status_code=409, detail="no audio plane connected")
        await client.set_led(effect)
        return {"effect": effect}

    @app.get("/api/v1/voice/threshold", dependencies=[Depends(auth)])
    async def voice_threshold() -> dict:
        """The threshold the wake model actually uses, read from the keywords file.

        `audio.voice.kws_threshold` is inert; showing the real file (and what it says)
        is the only way a person can tell whether changing a number did anything.
        """
        voice = cfg.audio.voice
        path = (
            Path(voice.kws_keywords_file)
            if voice.kws_keywords_file
            else Path(voice.kws_model_dir) / "keywords.txt"
        )
        return keyword_meta(path)

    @app.get("/api/v1/audio", dependencies=[Depends(auth)])
    async def audio_status() -> dict:
        """Live numbers from the audio plane: frames, drops, jitter, rtf.

        Asks for a fresh snapshot first - the periodic message is up to 2 s old, and a
        stale counter is exactly the kind of number this project refuses to print.
        """
        supervisor = getattr(app.state, "audio", None)
        if supervisor is None:
            return {"running": False, "error": "audio plane not started"}
        if supervisor.client is not None:
            try:
                await supervisor.client.request_metrics(timeout_s=0.5)
            except (ConnectionError, TimeoutError):
                pass
        return supervisor.status()

    @app.get("/api/v1/devices", dependencies=[Depends(auth)])
    async def devices() -> list[dict]:
        """Every configured device, including the ones this build cannot drive yet."""
        out: list[dict] = []
        seen: set[str] = set()
        for adapter in adapters.adapters.values():
            info = await adapter.probe()
            seen.add(info.id)
            out.append(
                {
                    "id": info.id,
                    "adapter": info.adapter,
                    "model": info.model,
                    "room": info.room,
                    "host": info.host,
                    "transport": info.transport.value,
                    "status": "ready",
                    "capabilities": sorted(info.capabilities),
                    "missing_for_barge_in": [c.value for c in info.missing_for_barge_in()],
                }
            )
        for dev in cfg.devices:
            if not dev.enabled or dev.id in seen:
                continue
            out.append(
                {
                    "id": dev.id,
                    "adapter": dev.adapter,
                    "model": "",
                    "room": dev.room,
                    "host": dev.host,
                    "transport": "unknown",
                    "status": "unavailable",
                    "error": adapters.errors.get(dev.id, "adapter not available"),
                    "capabilities": [],
                    "missing_for_barge_in": [],
                }
            )
        return out

    # ---------------------------------------------------------------- secrets
    @app.get("/api/v1/secrets", dependencies=[Depends(auth)])
    async def list_secrets() -> dict:
        """Which keys are configured. Names only - there is no endpoint that returns a value."""
        return {"names": secrets.stored_names()}

    @app.post("/api/v1/secrets", dependencies=[Depends(auth)])
    async def put_secret(payload: Annotated[dict, Body()]) -> dict:
        """Store a key typed in the console. An empty value deletes it.

        The value is never echoed, never logged, and never written into config.yaml: it
        goes to `secrets.yaml` (0600) next to the runtime state, which the providers and the
        search tool read through `secrets.get()`.
        """
        name = str(payload.get("name") or "").strip()
        value = str(payload.get("value") or "").strip()
        try:
            secrets.put(name, value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Providers read the key at call time, so nothing needs rebuilding: the next search
        # or LLM request sees it.
        return {"name": name, "has_key": secrets.has(name)}

    @app.get("/api/v1/tools", dependencies=[Depends(auth)])
    async def tools() -> dict:
        return {"count": len(registry.names()), "names": registry.names()}

    @app.get("/api/v1/turns", dependencies=[Depends(auth)])
    async def last_turn() -> dict:
        """The last turn as it ran: transcript, reply, per-stage provider and timing."""
        return getattr(app.state, "last_turn", None) or {"error": "no turn yet"}

    @app.get("/api/v1/traces", dependencies=[Depends(auth)])
    async def recent_traces(limit: int = 20) -> dict:
        """Latency marks for recent turns. Empty until a turn runs through the plane."""
        return {"limit": max(1, min(limit, 200)), "turns": traces.recent(limit)}

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:
            return FALLBACK

    return app
