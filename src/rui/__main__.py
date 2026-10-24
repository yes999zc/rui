"""Command line entry point: serve / doctor / setup."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from . import __version__
from .config import (
    Config,
    ConfigError,
    default_config_path,
    load_config,
    load_with_recovery,
    restore,
)
from .doctor import run_doctor
from .env import get as env_get
from .log_setup import LOG_FILE_NAME, setup_logging

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx


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

    text = resources.files("rui.config").joinpath("template.yaml").read_text(
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
        print("  (the console asks for it as X-Auth-Token; `rui token` prints it later)")
    print(f"wrote {path}")
    print("next: put your speaker under devices[], fill .env, run `rui fetch-models`")
    return 0


SENSEVOICE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSEVOICE_FILES = ("model.int8.onnx", "tokens.txt")
# Silero VAD, 2 MB. Without it the turn gate never exists, so the pre-wake replay, the
# acknowledgement hold and `gate_*` metrics are all inert while everything still looks up -
# the same "works on the dev Mac, silently dead on the NAS" shape as a missing model.
# The HuggingFace repo behind hf-mirror disappeared (401/404 on 2026-10-24), which
# silently cost every cold install its wake word: the KWS tarball is only fetched
# after the VAD succeeds, and install.sh is best-effort. Keep a GitHub release asset
# as a fallback so one upstream outage cannot break the one-command install again.
VAD_REPO = "csukuangfj/sherpa-onnx-vad"
VAD_FILE = "silero_vad.onnx"
VAD_URLS = (
    # The HuggingFace VAD repo is gone (401 on 2026-10-24), and the GitHub release asset
    # is the remaining source. Try the proxy mirror first: direct GitHub stalled here.
    "https://ghproxy.net/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "asr-models/silero_vad.onnx",
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
    "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "asr-models/silero_vad.onnx",
)
# The wake model (32.9 MB archive). GitHub releases is the only place sherpa-onnx
# publishes these; the tarball holds several chunk variants and we use chunk 8.
KWS_REPO = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
KWS_URLS = (
    # The direct GitHub release asset stalls on this NAS (33 MB partial, then no bytes);
    # the proxy mirrors served the full tarball. Try the mirror first, GitHub last.
    f"https://ghproxy.net/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"kws-models/{KWS_REPO}.tar.bz2",
    f"https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"kws-models/{KWS_REPO}.tar.bz2",
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{KWS_REPO}.tar.bz2",
)
# The Docker image bakes the two small models in at /opt/seed-models so a cold install on
# a mainland network never has to reach a GitHub release asset. `fetch-models` copies them
# first and only falls back to the network when the seed is absent (older image) or
# incomplete. An empty/nonexistent --seed-dir disables the copy.
DEFAULT_SEED_DIR = "/opt/seed-models"
# Self-hosted mirrors: each replaces the first URL of the respective list. The built-in
# mirrors stay as fallbacks, so a stale/offline mirror still ends the same way as today.
VAD_URL_ENV = "RUI_VAD_URL"
KWS_URL_ENV = "RUI_KWS_URL"
# ASR is a full-file URL (model.int8.onnx); tokens.txt is tried from the same directory
# first, then the default hf-mirror base. The 239 MB ASR model still comes from the network.
ASR_URL_ENV = "RUI_ASR_URL"
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
            "LAN. Run `rui setup` (it generates a token), set web.auth_token, "
            "or set web.allow_anonymous: true on a trusted development machine.")


def _new_web_token() -> str:
    import secrets as _secrets

    return _secrets.token_urlsafe(24)


# fetch-models pulls up to ~272 MB (239 MB when VAD/KWS come from the image seed).
# `timeout=None` meant a stalled connection never failed, so `_download_first` could not
# move on to the next mirror. Connect is capped tightly; read is generous because it is
# the gap *between* 1 MiB blocks, and a slow-but-live large download must still finish.
DOWNLOAD_CONNECT_TIMEOUT_S = 10.0
DOWNLOAD_READ_TIMEOUT_S = 60.0


# The NAS has no global IPv6 route. hf-mirror/GitHub resolve to AAAA records too, and
# httpx would then hang the TLS handshake until the connect timeout. Binding the
# transport to 0.0.0.0 forces IPv4, which is the only route that works there.
def _download_client(timeout: httpx.Timeout) -> httpx.Client:
    import httpx

    return httpx.Client(
        transport=httpx.HTTPTransport(local_address="0.0.0.0"),
        follow_redirects=True,
        timeout=timeout,
    )


def _download_to(url: str, target: Path, *, force: bool = False) -> int:
    """Stream one file to disk atomically; returns its size in bytes."""
    import httpx

    if target.exists() and not force:
        print(f"have {target.name} ({target.stat().st_size / 1e6:.1f} MB)")
        return target.stat().st_size
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    print(f"fetching {url}")
    timeout = httpx.Timeout(DOWNLOAD_READ_TIMEOUT_S, connect=DOWNLOAD_CONNECT_TIMEOUT_S)
    with _download_client(timeout) as client, client.stream("GET", url) as resp:
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


def _download_first(urls: tuple[str, ...], target: Path, *, force: bool = False) -> int:
    """Download from the first working mirror; raise the last error if none do."""
    last: Exception | None = None
    for url in urls:
        try:
            return _download_to(url, target, force=force)
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            last = exc
            print(f"  mirror failed: {url} ({type(exc).__name__}: {exc})")
    assert last is not None
    raise last


def _url_list(env_name: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    """Put the env override first and keep the built-in mirrors as fallbacks."""
    override = env_get(env_name).strip()
    if not override:
        return defaults
    return (override, *(url for url in defaults if url != override))


def _sibling_url(url: str, name: str) -> str:
    """The same-directory URL for another file (a self-hosted mirror's tokens.txt)."""
    parts = urlsplit(url)
    parent, _, _ = parts.path.rpartition("/")
    path = f"{parent}/{name}" if parent else name
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _asr_urls(base: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(model URLs, tokens URLs); RUI_ASR_URL is a full model-file URL."""
    model = f"{base}/model.int8.onnx"
    tokens = f"{base}/tokens.txt"
    override = env_get(ASR_URL_ENV).strip()
    if not override:
        return (model,), (tokens,)
    model_urls = _url_list(ASR_URL_ENV, (model,))
    # A mirror that serves model.int8.onnx serves tokens.txt next to it. Fall back to the
    # default base if the sibling is missing; never download the model bytes as tokens.
    tokens_mirror = _sibling_url(override, "tokens.txt")
    tokens_urls = (tokens_mirror,) if tokens_mirror == tokens else (tokens_mirror, tokens)
    return model_urls, tokens_urls


def _seed_models(
    seed_dir: str, vad_target: Path, kws_dest: Path, *, force: bool, include_kws: bool = True
) -> tuple[bool, bool]:
    """Copy the image-baked VAD and wake model before any network access.

    Returns (vad_seeded, kws_seeded). A missing/incomplete seed is not an error: the
    caller falls through to the normal download path, which is what an older image (no
    /opt/seed-models) does. The copy is atomic for the single VAD file and overwrites
    files in place for the KWS directory, so an interrupted install never leaves a
    half-written VAD.
    """
    if not seed_dir:
        return False, False
    seed = Path(seed_dir)
    if not seed.is_dir():
        return False, False

    vad_seeded = False
    source_vad = seed / VAD_FILE
    if source_vad.is_file() and (force or not vad_target.exists()):
        vad_target.parent.mkdir(parents=True, exist_ok=True)
        part = vad_target.with_suffix(vad_target.suffix + ".seed")
        shutil.copyfile(source_vad, part)
        part.replace(vad_target)
        print(f"seeded {vad_target.name} from {source_vad}")
        vad_seeded = True

    kws_seeded = False
    source_kws = seed / "kws" / KWS_REPO
    target_kws = kws_dest / KWS_REPO
    if include_kws and (source_kws / "tokens.txt").is_file() and (
        force or not (target_kws / "tokens.txt").exists()
    ):
        kws_dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_kws, target_kws, dirs_exist_ok=True)
        print(f"seeded wake model from {source_kws}")
        kws_seeded = True
    return vad_seeded, kws_seeded


def _fetch_kws_model(args: argparse.Namespace) -> int:
    """Download + unpack the wake model; the keywords file is written by setup.

    --kws-url wins over RUI_KWS_URL, which wins over the built-in mirrors.
    """
    import tarfile

    dest = Path(args.kws_dest)
    model_dir = dest / KWS_REPO
    tar_path = dest / f"{KWS_REPO}.tar.bz2"
    if not (model_dir / "tokens.txt").exists() or args.force:
        dest.mkdir(parents=True, exist_ok=True)
        kws_urls = (args.kws_url,) if args.kws_url else _url_list(KWS_URL_ENV, KWS_URLS)
        _download_first(kws_urls, tar_path, force=args.force)
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

    The ASR model lives with the data and is fetched once (~239 MB); the smaller VAD
    and wake model are baked into the image at /opt/seed-models and copied from there
    before any network access, so a Docker cold install never waits on a GitHub release
    asset. A host without the seed (older image, source install, `--seed-dir ""`) keeps
    the previous download path with the hf-mirror / proxy fallbacks.
    """
    from .providers.sherpa_asr import model_paths

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    vad_target = Path(args.vad_dest)
    seed_dir = getattr(args, "seed_dir", DEFAULT_SEED_DIR)
    vad_seeded, kws_seeded = _seed_models(
        seed_dir, vad_target, Path(args.kws_dest), force=args.force,
        include_kws=not args.no_kws)

    base = args.base or f"https://hf-mirror.com/{SENSEVOICE_REPO}/resolve/main"
    model_urls, token_urls = _asr_urls(base)
    wanted = {
        "model.int8.onnx": dest / "model.int8.onnx",
        "tokens.txt": dest / "tokens.txt",
    }
    _download_first(model_urls, wanted["model.int8.onnx"], force=args.force)
    _download_first(token_urls, wanted["tokens.txt"], force=args.force)
    if not vad_seeded:
        # --vad-base remains an explicit single URL (no silent fallback), as before.
        vad_urls = (args.vad_base,) if args.vad_base else _url_list(VAD_URL_ENV, VAD_URLS)
        _download_first(vad_urls, vad_target, force=args.force)
    onnx, tokens = model_paths(dest)  # recompute: the int8 file did not exist before
    if not onnx.exists() or not tokens.exists():
        print(f"model incomplete in {dest}")
        return 1
    print(f"local asr ready in {dest} - keep it as providers.catalog.<name>.model")
    if not args.no_kws and not kws_seeded:
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
        print(f"no config at {path}; run `rui setup` first")
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
    cfg, recovery = load_with_recovery(path) if path.exists() else (Config(), None)
    if recovery is not None:
        print(f"config recovery: {recovery.mode}; bad file kept at {recovery.bad_path or '<none>'}")
    host = args.host or cfg.web.host
    port = args.port or cfg.web.port
    reason = insecure_bind(host, cfg.web.auth_token, cfg.web.allow_anonymous)
    if reason:
        print(f"config error: {reason}")
        return 2
    setup_logging(cfg.system.log_level,
                  Path(cfg.system.data_dir) / LOG_FILE_NAME)
    app = create_app(cfg, config_path=path if path.exists() else None,
                     start_audio=not args.no_audio, recovery=recovery)
    uvicorn.run(app, host=host, port=port, log_level=cfg.system.log_level.lower())
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    path = _config_path(args)
    mode = "last-good" if args.last_good else "defaults"
    if path.exists():
        cfg, _recovery = load_with_recovery(path)
    else:
        cfg = Config()
    message = restore(path, cfg, mode)
    print(message)
    print("restart the service to apply it")
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
    # --config is accepted before and after the subcommand: `rui --config X serve`
    # and `rui serve --config X` both work, which is what a container entrypoint
    # and a shell alias each expect.
    parser = argparse.ArgumentParser(
        prog="rui", description=f"rui {__version__}", parents=[_config_parent(None)]
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
    p_fetch.add_argument("--seed-dir", default=DEFAULT_SEED_DIR,
                         help="copy VAD + wake model from this image directory first "
                              "(default /opt/seed-models; empty disables)")
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

    p_cfg = _leaf("config", "config recovery commands")
    p_cfg_sub = p_cfg.add_subparsers(dest="config_command")
    p_cfg_restore = p_cfg_sub.add_parser("restore", help="restore last-good or schema defaults",
                                         parents=[_config_parent(argparse.SUPPRESS)])
    mode = p_cfg_restore.add_mutually_exclusive_group(required=True)
    mode.add_argument("--last-good", action="store_true",
                      help="restore config.last-good.yaml")
    mode.add_argument("--defaults", action="store_true",
                      help="write schema defaults (does not delete secrets/models)")
    p_cfg_restore.set_defaults(func=cmd_config)

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
