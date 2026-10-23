"""Command line entry point: serve / doctor / setup."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, default_config_path, load_config
from .doctor import run_doctor
from .log_setup import LOG_FILE_NAME, setup_logging


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


def _production_template(config_path: Path) -> str:
    """The cold-install config, with `{config_dir}` resolved for this host.

    A schema-default config is not a working install: empty provider catalog, no
    VAD, no KWS paths, and `devices: []`. That is why `setup` writes this template
    instead - the old build's production values, the model paths `fetch-models`
    fills, and no secrets (keys come from the environment).
    """
    from importlib import resources

    text = resources.files("house_ear.config").joinpath("template.yaml").read_text(
        encoding="utf-8")
    return text.replace("{config_dir}", str(config_path.resolve().parent))


def _keyword_gate(target: Path) -> tuple[float, float]:
    """(threshold, boost) already configured in the file, or the measured defaults.

    Rewriting the keywords file because the wake *words* changed must not silently
    reset a hand-tuned 0.20 gate back to 0.15 (audit #8): the gate is a separate
    setting and the file is its only home.
    """
    from .audio.kws import keyword_meta

    meta = keyword_meta(target)
    threshold = meta.get("threshold")
    boost = meta.get("boost")
    return (
        float(threshold) if threshold is not None else 0.15,
        float(boost) if boost is not None else 2.0,
    )


def _write_wake_words(config_path: Path, words: list[str]) -> Path:
    """Write the keywords file next to the config; returns its path."""
    from .audio.kws import write_keywords_file

    target = config_path.resolve().parent / "keywords.txt"
    threshold, boost = _keyword_gate(target)
    count = write_keywords_file(target, words, threshold=threshold, boost=boost)
    print(f"keywords: {count} wake word(s) -> {target}")
    return target


def cmd_setup(args: argparse.Namespace) -> int:
    path = _config_path(args)
    if path.exists() and not args.force:
        print(f"config already exists: {path} (use --force to overwrite)")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_production_template(path), encoding="utf-8")
    # The plane refuses to start KWS without a keywords file, and that was the
    # last hand-edit between "installed" and "answers": generate it from the
    # wake word the install expects (the wizard regenerates it when the speaker
    # is added).
    _write_wake_words(path, list(args.wake_word or DEFAULT_WAKE_WORDS))
    from .config import load_config, save_config

    cfg = load_config(path)
    if not cfg.web.auth_token and not cfg.web.allow_anonymous:
        cfg.web.auth_token = _new_web_token()
        save_config(path, cfg)
        print("console token: " + cfg.web.auth_token)
        print("  (the console asks for it as X-Auth-Token; `house-ear token` prints it later)")
    print(f"wrote {path}")
    print("next: put your speaker under devices[], fill .env, run `house-ear fetch-models`")
    return 0


SENSEVOICE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSEVOICE_FILES = ("model.int8.onnx", "tokens.txt")
# Silero VAD, 2 MB. Without it the turn gate never exists, so the pre-wake replay, the
# acknowledgement hold and `gate_*` metrics are all inert while everything still looks up -
# the same "works on the dev Mac, silently dead on the NAS" shape as a missing model.
VAD_REPO = "csukuangfj/sherpa-onnx-vad"
VAD_FILE = "silero_vad.onnx"
# The wake model (32.9 MB archive). GitHub releases is the only place sherpa-onnx
# publishes these; the tarball holds several chunk variants and we use chunk 8.
KWS_REPO = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
KWS_URL = (f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
           f"{KWS_REPO}.tar.bz2")
DEFAULT_WAKE_WORDS = ("你好小瑞",)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().strip("[]")
    return h in _LOOPBACK_HOSTS or h.startswith("127.")


def insecure_bind(host: str, auth_token: str, allow_anonymous: bool) -> str:
    """Why this bind would expose the console, or "" when it is safe.

    Security audit 2026-10-23: the console bound 0.0.0.0 with an empty token and
    every acting endpoint answered the LAN. An empty token now refuses a
    non-loopback bind unless the config opts in explicitly (dev machines).
    """
    if _is_loopback_host(host) or auth_token or allow_anonymous:
        return ""
    return (f"serving on {host} without web.auth_token would expose the console to the "
            "LAN. Run `house-ear setup` (it generates a token), set web.auth_token, "
            "or set web.allow_anonymous: true on a trusted development machine.")


def _new_web_token() -> str:
    import secrets as _secrets

    return _secrets.token_urlsafe(24)


def _download_to(url: str, target: Path, *, force: bool = False) -> int:
    """Stream one file to disk atomically; returns its size in bytes."""
    import httpx

    if target.exists() and not force:
        print(f"have {target.name} ({target.stat().st_size / 1e6:.1f} MB)")
        return target.stat().st_size
    target.parent.mkdir(parents=True, exist_ok=True)
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
        raise RuntimeError(f"incomplete download: {done} of {total} bytes")
    part.replace(target)
    print(f"  {target.name}: {done / 1e6:.1f} MB")
    return done


def _fetch_kws_model(args: argparse.Namespace) -> int:
    """Download + unpack the wake model; the keywords file is written by setup."""
    import tarfile

    dest = Path(args.kws_dest)
    model_dir = dest / KWS_REPO
    tar_path = dest / f"{KWS_REPO}.tar.bz2"
    if not (model_dir / "tokens.txt").exists() or args.force:
        dest.mkdir(parents=True, exist_ok=True)
        _download_to(args.kws_url or KWS_URL, tar_path, force=args.force)
        print(f"  unpacking {tar_path.name}")
        with tarfile.open(tar_path, "r:bz2") as tar:
            tar.extractall(dest, filter="data")
        tar_path.unlink(missing_ok=True)
    else:
        print(f"have {model_dir.name}")
    if not (model_dir / "tokens.txt").exists():
        print(f"wake model incomplete in {model_dir}")
        return 1
    print(f"wake model ready in {model_dir}")
    return 0


def cmd_fetch_models(args: argparse.Namespace) -> int:
    """Download the local models to a data volume.

    The image stays small and the models live with the data: a fresh install
    fetches ~272 MB once, and a reinstall keeps it. That is the whole ASR chain
    (SenseVoice first, cloud fallback) and the wake model - before this, KWS was
    a manual download and a cold install could not hear its own wake word.
    """
    from .providers.sherpa_asr import model_paths

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    base = args.base or f"https://hf-mirror.com/{SENSEVOICE_REPO}/resolve/main"
    wanted = {
        "model.int8.onnx": dest / "model.int8.onnx",
        "tokens.txt": dest / "tokens.txt",
    }
    for name, target in wanted.items():
        _download_to(f"{base}/{name}", target, force=args.force)
    vad_target = Path(args.vad_dest)
    _download_to(args.vad_base or f"https://hf-mirror.com/{VAD_REPO}/resolve/main/{VAD_FILE}",
                 vad_target, force=args.force)
    onnx, tokens = model_paths(dest)  # recompute: the int8 file did not exist before
    if not onnx.exists() or not tokens.exists():
        print(f"model incomplete in {dest}")
        return 1
    print(f"local asr ready in {dest} - keep it as providers.catalog.<name>.model")
    if not args.no_kws:
        if _fetch_kws_model(args) != 0:
            return 1
    return 0


def cmd_keywords(args: argparse.Namespace) -> int:
    """Rewrite the keywords file from the config's wake words.

    The file the wake model reads carries the real threshold and is not a config
    field; whoever changes `devices[].wake_words` (console, install wizard) must
    regenerate this or the box keeps listening for the old phrase.
    """
    from .audio.kws import write_keywords_file

    path = _config_path(args)
    words = list(args.wake_word or [])
    if not words:
        cfg = load_config(path) if path.exists() else Config()
        words = [word for device in cfg.devices for word in device.wake_words]
        words = words or list(DEFAULT_WAKE_WORDS)
    target = Path(args.dest) if args.dest else path.resolve().parent / "keywords.txt"
    threshold, boost = _keyword_gate(target)
    count = write_keywords_file(target, words, threshold=threshold, boost=boost)
    print(f"keywords: {count} wake word(s) -> {target}")
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    """Print (or rotate) the console's X-Auth-Token for the headless operator."""
    from .config import load_config, save_config

    path = _config_path(args)
    if not path.exists():
        print(f"no config at {path}; run `house-ear setup` first")
        return 1
    cfg = load_config(path)
    if getattr(args, "rotate", False):
        cfg.web.auth_token = _new_web_token()
        cfg.web.allow_anonymous = False
        save_config(path, cfg)
        print("rotated web.auth_token: " + cfg.web.auth_token)
        return 0
    if not cfg.web.auth_token:
        print("web.auth_token is empty" +
              ("; web.allow_anonymous is on (dev only)" if cfg.web.allow_anonymous else ""))
        return 1
    print(cfg.web.auth_token)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .web.api.app import create_app

    path = _config_path(args)
    cfg = load_config(path) if path.exists() else Config()
    host = args.host or cfg.web.host
    port = args.port or cfg.web.port
    reason = insecure_bind(host, cfg.web.auth_token, cfg.web.allow_anonymous)
    if reason:
        print(f"config error: {reason}")
        return 2
    setup_logging(cfg.system.log_level,
                  Path(cfg.system.data_dir) / LOG_FILE_NAME)
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

    p_fetch = _leaf("fetch-models",
                    "download the local ASR, VAD and wake models to a data volume")
    p_fetch.add_argument("--dest", default="/data/models/sensevoice")
    p_fetch.add_argument("--vad-dest", default="/data/models/silero_vad.onnx")
    p_fetch.add_argument("--vad-base", default="", help="override the VAD download base URL")
    p_fetch.add_argument("--base", default="", help="override the download base URL")
    p_fetch.add_argument("--kws-dest", default="/data/models/kws",
                         help="directory the wake model is unpacked into")
    p_fetch.add_argument("--kws-url", default="", help="override the wake model tarball URL")
    p_fetch.add_argument("--no-kws", action="store_true", help="skip the wake model")
    p_fetch.add_argument("--force", action="store_true")
    p_fetch.set_defaults(func=cmd_fetch_models)

    p_doc = _leaf("doctor", "probe hardware, network and audio headroom")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)

    p_setup = _leaf("setup", "write the production config and its keywords file")
    p_setup.add_argument("--force", action="store_true")
    p_setup.add_argument("--wake-word", action="append",
                         help="wake word for the keywords file (default 你好小瑞)")
    p_setup.set_defaults(func=cmd_setup)

    p_tok = _leaf("token", "print (or --rotate) the console X-Auth-Token")
    p_tok.add_argument("--rotate", action="store_true")
    p_tok.set_defaults(func=cmd_token)

    p_kw = _leaf("keywords", "rewrite keywords.txt from the config's wake words")
    p_kw.add_argument("--dest", default="", help="target file (default: next to the config)")
    p_kw.add_argument("--wake-word", action="append")
    p_kw.set_defaults(func=cmd_keywords)
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
