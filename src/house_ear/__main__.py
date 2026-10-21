"""Command line entry point: serve / doctor / setup."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, default_config_path, load_config, save_config
from .doctor import run_doctor


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = None
    path = Path(args.config) if args.config else default_config_path()
    if path.exists():
        cfg = load_config(path)
    report = run_doctor(cfg)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.to_text())
    return 0 if report.verdict != "unsupported" else 2


def cmd_setup(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else default_config_path()
    if path.exists() and not args.force:
        print(f"config already exists: {path} (use --force to overwrite)")
        return 1
    cfg = Config()
    save_config(path, cfg)
    print(f"wrote {path}")
    print("next: edit devices[].host, then run `house-ear doctor`")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .web.api.app import create_app

    path = Path(args.config) if args.config else default_config_path()
    cfg = load_config(path) if path.exists() else Config()
    host = args.host or cfg.web.host
    port = args.port or cfg.web.port
    app = create_app(cfg)
    uvicorn.run(app, host=host, port=port, log_level=cfg.system.log_level.lower())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="house-ear", description=f"house-ear {__version__}")
    parser.add_argument("--config", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="run the control plane")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)
    p_serve.set_defaults(func=cmd_serve)

    p_doc = sub.add_parser("doctor", help="probe hardware, network and audio headroom")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)

    p_setup = sub.add_parser("setup", help="write a default config")
    p_setup.add_argument("--force", action="store_true")
    p_setup.set_defaults(func=cmd_setup)

    args = parser.parse_args()
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
