#!/usr/bin/env python3
"""Minimal HTTP snapshot service for a Mac USB camera (standard library only).

GET /snapshot.jpg grabs one ffmpeg frame through a stdout pipe (never to disk)
and returns it as image/jpeg. Other paths are 404; capture failure is 503.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

DEFAULT_FFMPEG = "/opt/homebrew/bin/ffmpeg"
LOG = logging.getLogger("usb-snapshot")
LOCK = threading.Lock()  # one avfoundation grab at a time


def capture(args: argparse.Namespace) -> bytes | None:
    """Return one JPEG frame, or None when ffmpeg fails or times out."""
    cmd = [
        args.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "avfoundation",
        "-framerate", args.framerate, "-pixel_format", args.pixel_format,
        "-video_size", args.size, "-i", args.device,
        "-frames:v", "1", "-f", "mjpeg", "-q:v", "3", "pipe:1",
    ]
    started = time.monotonic()
    with LOCK:
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:  # subprocess.run has killed and reaped it
            LOG.warning("capture timeout device=%s after %.1fs", args.device, args.timeout)
            return None
    frame = proc.stdout or b""
    if proc.returncode != 0 or not frame:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[:300]
        LOG.warning("capture failed device=%s rc=%s: %s", args.device, proc.returncode, detail)
        return None
    LOG.info("capture device=%s bytes=%d elapsed_ms=%.0f",
             args.device, len(frame), (time.monotonic() - started) * 1000)
    return frame


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # required name from BaseHTTPRequestHandler
        if urlsplit(self.path).path != "/snapshot.jpg":
            self._send(404, b"not found\n")
            return
        try:
            frame = capture(self.server.args)  # type: ignore[attr-defined]
        except Exception as exc:  # keep serving instead of dropping the request
            LOG.warning("capture error device=%s type=%s",
                        self.server.args.device, type(exc).__name__)  # type: ignore[attr-defined]
            frame = None
        if frame is None:
            self._send(503, b"camera capture failed (busy, unauthorized, or timed out)\n")
            return
        self._send(200, frame, "image/jpeg")

    def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # keep default access logs out of INFO
        LOG.debug("%s %s", self.address_string(), fmt % args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve GET /snapshot.jpg from a Mac USB camera via ffmpeg.")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8766,
                        help="TCP port (default 8766)")
    parser.add_argument("--device", default="0", help="avfoundation video device index or name")
    parser.add_argument("--size", default="1280x720", help="capture size WxH (default 1280x720)")
    parser.add_argument("--framerate", default="30", help="input framerate (default 30)")
    parser.add_argument("--pixel-format", default="uyvy422",
                        help="input pixel format (default uyvy422)")
    parser.add_argument("--ffmpeg", default="",
                        help=f"ffmpeg path (default {DEFAULT_FFMPEG}, else PATH)")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="capture timeout in seconds (default 10)")
    args = parser.parse_args(argv)

    if not args.ffmpeg:
        args.ffmpeg = DEFAULT_FFMPEG if os.access(DEFAULT_FFMPEG, os.X_OK) else shutil.which("ffmpeg")
    if not args.ffmpeg:
        parser.error("ffmpeg not found; pass --ffmpeg /path/to/ffmpeg")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        LOG.error("cannot bind %s:%d (%s); try another --port", args.host, args.port, exc.strerror)
        return 1
    server.daemon_threads = True
    server.args = args  # type: ignore[attr-defined]
    LOG.info("listening http://%s:%d/snapshot.jpg device=%s size=%s",
             args.host, args.port, args.device, args.size)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
