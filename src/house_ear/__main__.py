"""Command line entry point: serve / doctor / setup."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, default_config_path, load_config, save_config
from .doctor import run_doctor


def _config_path(args: argparse.Namespace) -> Path:
    path = getattr(args, "config", None)
    return Path(path) if path else default_config_path()


def cmd_doctor(args: argparse.Namespace) -> int:
    path = _config_path(args)
    cfg = load_config(path) if path.exists() else None
    report = run_doctor(cfg)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.to_text())
    return 0 if report.verdict != "unsupported" else 2


def cmd_setup(args: argparse.Namespace) -> int:
    path = _config_path(args)
    if path.exists() and not args.force:
        print(f"config already exists: {path} (use --force to overwrite)")
        return 1
    save_config(path, Config())
    print(f"wrote {path}")
    print("next: add your speaker under devices[], then run `house-ear doctor`")
    return 0


SENSEVOICE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSEVOICE_FILES = ("model.int8.onnx", "tokens.txt")
# Silero VAD, 2 MB. Without it the turn gate never exists, so the pre-wake replay, the
# acknowledgement hold and `gate_*` metrics are all inert while everything still looks up -
# the same "works on the dev Mac, silently dead on the NAS" shape as a missing model.
VAD_REPO = "csukuangfj/sherpa-onnx-vad"
VAD_FILE = "silero_vad.onnx"


def cmd_fetch_models(args: argparse.Namespace) -> int:
    """Download the local ASR model to a data volume.

    The image stays small and the model lives with the data: a fresh install fetches 239 MB
    once, and a reinstall keeps it. Local ASR is the first entry in the ASR chain when this
    has run; without it the chain falls through to the cloud entry.
    """
    import httpx

    from .providers.sherpa_asr import model_paths

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    base = args.base or f"https://hf-mirror.com/{SENSEVOICE_REPO}/resolve/main"
    wanted = {
        "model.int8.onnx": dest / "model.int8.onnx",
        "tokens.txt": dest / "tokens.txt",
    }
    for name, target in wanted.items():
        if target.exists() and not args.force:
            print(f"have {name} ({target.stat().st_size / 1e6:.0f} MB)")
            continue
        url = f"{base}/{name}"
        part = target.with_suffix(target.suffix + ".part")
        print(f"fetching {url}")
        with httpx.stream("GET", url, follow_redirects=True, timeout=None) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with part.open("wb") as fh:
                for block in resp.iter_bytes(1 << 20):
                    fh.write(block)
                    done += len(block)
                    if total and done % (25 << 20) < (1 << 20):
                        print(f"  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", flush=True)
        if total and done != total:
            part.unlink(missing_ok=True)
            print(f"incomplete download: {done} of {total} bytes")
            return 1
        part.replace(target)
        print(f"  {name}: {done / 1e6:.1f} MB")
    vad_target = Path(args.vad_dest)
    if vad_target.exists() and not args.force:
        print(f"have {VAD_FILE} ({vad_target.stat().st_size / 1e6:.1f} MB)")
    else:
        vad_target.parent.mkdir(parents=True, exist_ok=True)
        url = f"{args.vad_base or 'https://hf-mirror.com/' + VAD_REPO + '/resolve/main'}/{VAD_FILE}"
        print(f"fetching {url}")
        with httpx.stream("GET", url, follow_redirects=True, timeout=None) as resp:
            resp.raise_for_status()
            part = vad_target.with_suffix(vad_target.suffix + ".part")
            with part.open("wb") as fh:
                for block in resp.iter_bytes(1 << 20):
                    fh.write(block)
            part.replace(vad_target)
        print(f"  {VAD_FILE}: {vad_target.stat().st_size / 1e6:.1f} MB")
    onnx, tokens = model_paths(dest)  # recompute: the int8 file did not exist before
    if not onnx.exists() or not tokens.exists():
        print(f"model incomplete in {dest}")
        return 1
    print(f"local asr ready in {dest} - keep it as providers.catalog.<name>.model")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .web.api.app import create_app

    path = _config_path(args)
    cfg = load_config(path) if path.exists() else Config()
    host = args.host or cfg.web.host
    port = args.port or cfg.web.port
    app = create_app(cfg, config_path=path if path.exists() else None,
                     start_audio=not args.no_audio)
    uvicorn.run(app, host=host, port=port, log_level=cfg.system.log_level.lower())
    return 0


def cmd_audio(args: argparse.Namespace) -> int:
    import asyncio

    from .audio.process import build_plane

    path = _config_path(args)
    cfg = load_config(path)
    plane = build_plane(cfg, args.device, Path(args.socket) if args.socket else None)
    try:
        asyncio.run(plane.serve())
    except KeyboardInterrupt:
        return 0
    return 0


def cmd_debug_play(args: argparse.Namespace) -> int:
    """Play a local wav at the speaker so AEC and wake-word tests have sound."""
    import asyncio

    from .audio.client import AudioPlaneError, AudioPlaneSupervisor
    from .audio.protocol import PlayFile, StopAudio

    path = _config_path(args)
    cfg = load_config(path)
    file = Path(args.file)
    if not file.exists():
        print(f"no such file: {file}")
        return 2

    async def run() -> int:
        supervisor = AudioPlaneSupervisor(cfg, config_path=path, device_id=args.device)
        try:
            await supervisor.start()
        except AudioPlaneError as exc:
            print(f"audio plane did not start: {exc}")
            return 2
        try:
            client = supervisor.client
            assert client is not None
            deadline = time.monotonic() + args.wait
            while time.monotonic() < deadline:
                metrics = await client.request_metrics()
                if metrics is not None and metrics.speaker_connected:
                    break
                await asyncio.sleep(0.5)
            else:
                print("speaker did not connect (is its client pointed at this host?)")
                return 3
            await client.send(PlayFile(path=str(file), loop=args.loop, gain=args.gain))
            print(f"playing {file.name} gain={args.gain} loop={args.loop}; ctrl-c to stop")
            elapsed = 0.0
            while elapsed < args.seconds:
                await asyncio.sleep(min(1.0, args.seconds - elapsed))
                elapsed += 1.0
                metrics = await client.request_metrics()
                if metrics is not None:
                    print(
                        f"  t={metrics.uptime_s:6.1f}s played={metrics.playback_s:6.1f}s "
                        f"ref={metrics.ref_active} erle={metrics.aec_erle_db}",
                        flush=True,
                    )
            await client.send(StopAudio())
            return 0
        finally:
            await supervisor.stop()

    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


def _config_parent(default: object) -> argparse.ArgumentParser:
    """A parent parser carrying --config with a chosen default.

    The main parser uses None; subparsers use SUPPRESS so that a value given *before*
    the subcommand is not reset by the subparser's own default.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", help="path to config.yaml", default=default)
    return parent


def build_parser() -> argparse.ArgumentParser:
    # --config is accepted before and after the subcommand: `house-ear --config X serve`
    # and `house-ear serve --config X` both work, which is what a container entrypoint
    # and a shell alias each expect.
    parser = argparse.ArgumentParser(
        prog="house-ear", description=f"house-ear {__version__}", parents=[_config_parent(None)]
    )
    sub = parser.add_subparsers(dest="command")

    def _leaf(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, parents=[_config_parent(argparse.SUPPRESS)])

    p_serve = _leaf("serve", "run the control plane")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)
    p_serve.add_argument("--no-audio", action="store_true",
                         help="control plane only; do not spawn the audio plane")
    p_serve.set_defaults(func=cmd_serve)

    p_play = _leaf("debug-play", "play a local wav at the speaker (test signal for AEC/KWS)")
    p_play.add_argument("--device", required=True)
    p_play.add_argument("--file", required=True)
    p_play.add_argument("--seconds", type=float, default=10.0)
    p_play.add_argument("--gain", type=float, default=1.0)
    p_play.add_argument("--loop", action="store_true")
    p_play.add_argument("--wait", type=float, default=60.0, help="seconds to wait for the speaker")
    p_play.set_defaults(func=cmd_debug_play)

    p_audio = _leaf("audio", "run the audio plane in the foreground")
    p_audio.add_argument("--device", required=True)
    p_audio.add_argument("--socket")
    p_audio.set_defaults(func=cmd_audio)

    p_fetch = _leaf("fetch-models", "download the local ASR and VAD models to a data volume")
    p_fetch.add_argument("--dest", default="/data/models/sensevoice")
    p_fetch.add_argument("--vad-dest", default="/data/models/silero_vad.onnx")
    p_fetch.add_argument("--vad-base", default="", help="override the VAD download base URL")
    p_fetch.add_argument("--base", default="", help="override the download base URL")
    p_fetch.add_argument("--force", action="store_true")
    p_fetch.set_defaults(func=cmd_fetch_models)

    p_doc = _leaf("doctor", "probe hardware, network and audio headroom")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)

    p_setup = _leaf("setup", "write a default config")
    p_setup.add_argument("--force", action="store_true")
    p_setup.set_defaults(func=cmd_setup)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
