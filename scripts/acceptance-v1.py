#!/usr/bin/env python3
"""Guided acceptance recorder for rui v1 (wake N/M and barge latency).

This is a *recorder*, not a measurement device that invents numbers:

* every human judgement is labelled `manual` and stored with its prompt;
* every server number is read from the running API and stored with its endpoint;
* a failed/absent API reading is stored as null with a reason, never as a guess;
* the output is JSON + Markdown, so the acceptance day can paste the table into
  docs/acceptance.md without retyping.

Usage (run where it can reach the control plane; token comes from an env var):

    export RUI_TOKEN="$(docker compose exec -T rui rui token)"
    python3 scripts/acceptance-v1.py wake  --url http://127.0.0.1:8080 --trials 20
    python3 scripts/acceptance-v1.py barge --url http://127.0.0.1:8080 --trials 3

Both modes are interactive on purpose: the operator speaks, listens and answers
y/n. Nothing here can replace the ears.

Wake mode: 20 trials, 80% music playing. Pass criterion is 95% (19/20); use
`--no-gate` for the optional 3-4 m far-field run (recorded, not a release gate).

Barge mode: server-side `silence_after_ms` from GET /api/v1/traces is
`barge_in -> silence` (the moment the service stops pushing audio). It does NOT
include the speaker's own buffer drain. Manual stopwatch values are accepted only
when no trace exists, and are labelled `manual` - do not mix the two when judging
<=300 ms.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SCHEMA = "rui-acceptance/1"
SCRIPT_VERSION = "1"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ApiError(RuntimeError):
    def __init__(self, path: str, status: int, detail: str) -> None:
        super().__init__(f"{path}: HTTP {status}: {detail[:200]}")
        self.path = path
        self.status = status


class Api:
    """Tiny stdlib client; the token is sent as a header and never printed."""

    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def _request(self, path: str, method: str = "GET", body: dict | None = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["X-Auth-Token"] = self.token
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            raise ApiError(path, exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{path}: cannot reach {self.base}: {exc.reason}") from exc
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise RuntimeError(f"{path}: response is not JSON") from exc

    def get(self, path: str) -> dict:
        return self._request(path)

    def post(self, path: str, body: dict) -> dict:
        return self._request(path, method="POST", body=body)


def choose(prompt: str, allowed: set[str]) -> str:
    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            answer = "q"
        if answer in allowed:
            return answer
        print(f"  please answer one of: {', '.join(sorted(allowed))}")


def ask_int(prompt: str, *, allow_empty: bool = True) -> int | None:
    while True:
        try:
            raw = input(prompt).strip()
        except EOFError:
            raw = ""
        if not raw and allow_empty:
            return None
        if raw.isdigit():
            return int(raw)
        print("  enter a whole number of milliseconds, or leave empty to skip")


def metrics_of(api: Api) -> dict:
    """Flatten /api/v1/audio into the few fields the acceptance record needs."""
    try:
        status = api.get("/api/v1/audio")
    except ApiError as exc:
        return {"error": str(exc)}
    metrics = status.get("metrics") or {}
    return {
        "running": bool(status.get("running")),
        "error": status.get("error") or "",
        "speaker_connected": metrics.get("speaker_connected"),
        "wake_count": metrics.get("wake_count"),
        "gate_discarded": metrics.get("gate_discarded"),
        "gate_interrupted": metrics.get("gate_interrupted"),
        "barge_count": metrics.get("barge_count"),
        "voice_peak_db": metrics.get("voice_peak_db"),
        "aec_erle_db": metrics.get("aec_erle_db"),
        "ref_active": metrics.get("ref_active"),
        "ack_mode": metrics.get("ack_mode"),
        "playback_lead_ms": metrics.get("playback_lead_ms"),
        "output_owner": metrics.get("output_owner"),
    }


def health_of(api: Api) -> dict:
    try:
        return api.get("/health")
    except (ApiError, RuntimeError) as exc:
        return {"error": str(exc)}


def volume_of(api: Api) -> dict:
    try:
        return api.get("/api/v1/device/volume")
    except (ApiError, RuntimeError) as exc:
        return {"error": str(exc)}


def write_outputs(mode: str, payload: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(out_dir, 0o700)
    except OSError:
        pass
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"acceptance-{mode}-{stamp}.json"
    md_path = out_dir / f"acceptance-{mode}-{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    md_path.write_text(render_markdown(mode, payload), encoding="utf-8")
    for path in (json_path, md_path):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return json_path, md_path


def render_markdown(mode: str, p: dict) -> str:
    lines = [f"# rui acceptance - {mode}", ""]
    lines.append(f"- schema: `{p['schema']}`")
    lines.append(f"- started/finished: {p['started_at']} / {p['finished_at']}")
    lines.append(f"- url: `{p['url']}`")
    lines.append(f"- label: {p.get('label', '')}")
    lines.append(f"- health: `{json.dumps(p.get('health'), ensure_ascii=False)}`")
    lines.append("")
    trials = p.get("trials", [])
    if mode == "wake":
        lines.append("| # | manual result | wake_count Δ | voice_peak dB | ERLE dB | ack | note |")
        lines.append("|---|---|---|---|---|---|---|")
        for t in trials:
            lines.append("| {i} | {r} | {d} | {v} | {e} | {a} | {n} |".format(
                i=t["trial"], r=t["operator_result"],
                d=_fmt(t.get("wake_delta")), v=_fmt(t.get("voice_peak_db")),
                e=_fmt(t.get("aec_erle_db")), a=_fmt(t.get("ack_mode")),
                n=(t.get("note") or "").replace("|", "/")))
        s = p.get("summary", {})
        if s.get("gate") is False:
            verdict = "INFO (recorded, not a release gate)"
        elif s.get("passed") is True:
            verdict = "PASS"
        elif s.get("passed") is False:
            verdict = "FAIL"
        else:
            verdict = "INCOMPLETE"
        lines += ["", f"**Result: {s.get('hits')}/{s.get('total')} ({verdict})**",
                  "", f"- criterion: {s.get('criterion')}",
                  f"- skipped: {s.get('skipped')}", f"- warnings: {s.get('warnings')}"]
    else:
        lines.append("| # | server silence_after_ms | source | barge_count Δ | note |")
        lines.append("|---|---|---|---|---|")
        for t in trials:
            lines.append("| {i} | {v} | {s} | {d} | {n} |".format(
                i=t["trial"], v=_fmt(t.get("silence_after_ms")),
                s=t.get("source"), d=_fmt(t.get("barge_delta")),
                n=(t.get("note") or "").replace("|", "/")))
        s = p.get("summary", {})
        lines += ["", f"**Result: {s.get('verdict')}**", "",
                  f"- server values: {s.get('server_values_ms')}",
                  f"- manual/unavailable: {s.get('manual_trials')}",
                  f"- max server value: {s.get('max_ms')} ms",
                  "", "Limit: the trace measures `barge_in -> silence` on the server "
                       "(stop pushing). The device buffer drain is separate and must be "
                       "judged by ear; do not add the two into a claimed number."]
    lines += ["", "## Limitations", "",
              "- `manual` fields are operator-entered; they are not instrument measurements.",
              "- API fields are read from the running service and labelled by endpoint in "
              "the JSON payload.",
              "- No number is inferred when the API or the operator did not supply one.", ""]
    return "\n".join(lines)


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def run_wake(api: Api, args: argparse.Namespace) -> dict:
    health = health_of(api)
    volume = volume_of(api)
    metrics = metrics_of(api)
    print(f"health: {json.dumps(health, ensure_ascii=False)[:300]}")
    print(f"volume: {json.dumps(volume, ensure_ascii=False)[:200]}")
    if health.get("status") != "ok":
        print("WARNING: /health is not ok; the numbers below may be unusable.")
    if metrics.get("speaker_connected") is not True:
        print("WARNING: speaker_connected is not true; check the speaker before counting.")
    print("\nPreparation (script does not do this for you):")
    print("  1. play music on the speaker and set the volume to the 80% band "
          "(170-220 mysoftvol per docs/acceptance.md);")
    print("  2. stand at the agreed distance, quiet room beyond the music;")
    print(f"  3. {args.trials} trials: say '你好小瑞' + one short question, then answer "
          "y (heard the answer), n (no answer), s (skip), q (stop).")
    input("\nready? press Enter (Ctrl-C aborts and still writes partial output) ...")

    trials: list[dict] = []
    interrupted = False
    try:
        for i in range(1, args.trials + 1):
            before = metrics_of(api)
            started = now_iso()
            print(f"\n[round {i}/{args.trials}] say the wake word + one question now")
            answer = choose("  result: [y] heard answer [n] no answer [s] skip [q] quit: ",
                            {"y", "n", "s", "q"})
            if answer == "q":
                interrupted = True
                break
            after = metrics_of(api)
            before_count = before.get("wake_count")
            after_count = after.get("wake_count")
            delta = None
            if isinstance(before_count, int) and isinstance(after_count, int):
                delta = after_count - before_count
            note = ""
            if answer == "s":
                note = "operator skipped"
            else:
                if answer == "n" and delta:
                    note = "wake_count increased but no answer: wake fired, turn/playback failed"
                elif answer == "y" and not delta:
                    note = "operator heard an answer but wake_count did not increase (metrics lag?)"
                extra = input("  note (optional, Enter to skip): ").strip()
                if extra:
                    note = (note + "; " if note else "") + extra
            trials.append({
                "trial": i,
                "started_at": started,
                "operator_result": {"y": "hit", "n": "miss", "s": "skip"}.get(answer, answer),
                "manual": True,
                "wake_count_before": before_count,
                "wake_count_after": after_count,
                "wake_delta": delta,
                "voice_peak_db": after.get("voice_peak_db"),
                "aec_erle_db": after.get("aec_erle_db"),
                "ref_active": after.get("ref_active"),
                "ack_mode": after.get("ack_mode"),
                "gate_discarded": after.get("gate_discarded"),
                "note": note,
                "source": {"operator_result": "manual", "metrics": "/api/v1/audio"},
            })
    except KeyboardInterrupt:
        interrupted = True
        print("\ninterrupted; writing partial output")

    hits = sum(1 for t in trials if t["operator_result"] == "hit")
    total = sum(1 for t in trials if t["operator_result"] in ("hit", "miss"))
    skipped = sum(1 for t in trials if t["operator_result"] == "skip")
    required = math.ceil(args.criterion * total) if total else None
    passed: bool | None
    if args.no_gate:
        passed = None
    elif total < args.trials:
        passed = None
    else:
        passed = bool(required is not None and hits >= required)
    warnings = []
    for t in trials:
        if "wake_count increased" in (t.get("note") or ""):
            warnings.append(f"trial {t['trial']}: wake fired but no answer")
    payload = {
        "schema": SCHEMA,
        "script_version": SCRIPT_VERSION,
        "mode": "wake",
        "label": args.label or "near-field",
        "url": args.url,
        "started_at": now_iso() if not trials else trials[0]["started_at"],
        "finished_at": now_iso(),
        "health": health,
        "volume": volume,
        "precheck_metrics": metrics,
        "criterion": {
            "ratio": args.criterion,
            "required_hits": required,
            "gate": not args.no_gate,
            "note": "acceptance target 19/20 at 80% music; far-field run is recorded only",
        },
        "trials": trials,
        "summary": {
            "hits": hits,
            "total": total,
            "skipped": skipped,
            "required": required,
            "passed": passed,
            "warnings": warnings,
            "interrupted": interrupted,
            "gate": not args.no_gate,
            "manual_fields": ["operator_result", "note"],
        },
        "limitations": [
            "hit/miss is operator-entered; the script does not hear the speaker",
            "wake_delta/metrics are read from /api/v1/audio, not inferred",
            "music volume is recorded from /api/v1/device/volume but not enforced",
        ],
    }
    return payload


def run_barge(api: Api, args: argparse.Namespace) -> dict:
    health = health_of(api)
    print(f"health: {json.dumps(health, ensure_ascii=False)[:300]}")
    if health.get("audio_plane") is not True:
        print("WARNING: audio_plane is not running; barge cannot be measured.")
    print("\nHow this is measured:")
    print("  server trace = GET /api/v1/traces -> silence_after_ms")
    print("                 (barge_in -> silence, i.e. when the service stops pushing).")
    print("  device buffer = your ear; judge it separately, do not add it to the trace.")
    if args.auto_say:
        print(f"  auto-start: the script will POST /api/v1/debug/say with {args.say_text!r}")
    else:
        print("  start a long answer yourself (console 试一试, or ask by voice), "
              "then barge in during playback.")
    try:
        known_ids = {t.get("turn_id") for t in
                     (api.get("/api/v1/traces?limit=100").get("turns") or [])}
    except (ApiError, RuntimeError) as exc:
        known_ids = set()
        print(f"warning: cannot read traces before starting: {exc}")

    trials: list[dict] = []
    try:
        for i in range(1, args.trials + 1):
            print(f"\n[round {i}/{args.trials}]")
            trial_started = now_iso()
            before = metrics_of(api)
            thread = None
            if args.auto_say:
                thread = threading.Thread(
                    target=lambda: _safe_post(api, "/api/v1/debug/say",
                                              {"text": args.say_text}), daemon=True)
                thread.start()
            else:
                input("  start the long answer, then when it is audible press Enter ...")
            print("  now say '你好小瑞' during playback (barge-in) ...")
            started = time.monotonic()
            found: dict | None = None
            while time.monotonic() - started < args.timeout:
                try:
                    turns = api.get("/api/v1/traces?limit=100").get("turns") or []
                except (ApiError, RuntimeError) as exc:
                    print(f"  traces unavailable: {exc}")
                    break
                fresh = [t for t in turns
                         if t.get("turn_id") not in known_ids
                         and t.get("silence_after_ms") is not None]
                if fresh:
                    found = fresh[-1]
                    break
                time.sleep(0.25)
            if thread is not None:
                thread.join(timeout=2.0)
            after = metrics_of(api)
            barge_before, barge_after = before.get("barge_count"), after.get("barge_count")
            delta = (barge_after - barge_before
                     if isinstance(barge_before, int) and isinstance(barge_after, int)
                     else None)
            note = ""
            if found is not None:
                known_ids.add(found.get("turn_id"))
                source = "server-trace"
                value = found.get("silence_after_ms")
                stages = (found.get("stages_ms") or {})
                note = f"stages_ms barge_in={stages.get('barge_in')} silence={stages.get('silence')}"
            else:
                print("  no new server trace with silence_after_ms.")
                manual = ask_int("  measured stopwatch value in ms (empty = unavailable, not a pass): ")
                if manual is None:
                    source = "unavailable"
                    value = None
                else:
                    source = "manual-operator"
                    value = manual
                    note = "manual stopwatch; measurement method must be stated in the record"
            trials.append({
                "trial": i,
                "started_at": trial_started,
                "silence_after_ms": value,
                "source": source,
                "barge_count_before": barge_before,
                "barge_count_after": barge_after,
                "barge_delta": delta,
                "trace_turn_id": (found or {}).get("turn_id"),
                "trace": found,
                "note": note,
                "manual": source == "manual-operator",
            })
    except KeyboardInterrupt:
        print("\ninterrupted; writing partial output")
    server_values = [t["silence_after_ms"] for t in trials
                     if t["source"] == "server-trace" and t["silence_after_ms"] is not None]
    manual_trials = [t["trial"] for t in trials if t["source"] != "server-trace"]
    if not server_values:
        verdict = "incomplete (no server trace values)"
    elif manual_trials:
        verdict = "incomplete (some trials lack a server trace; see manual/unavailable)"
    elif max(server_values) <= 300:
        verdict = "PASS (server stop-push; device buffer separate)"
    else:
        verdict = "FAIL (server stop-push > 300 ms)"
    return {
        "schema": SCHEMA,
        "script_version": SCRIPT_VERSION,
        "mode": "barge",
        "label": args.label or "barge",
        "url": args.url,
        "started_at": now_iso() if not trials else trials[0].get("started_at", now_iso()),
        "finished_at": now_iso(),
        "health": health,
        "trials": trials,
        "summary": {
            "verdict": verdict,
            "server_values_ms": server_values,
            "max_ms": max(server_values) if server_values else None,
            "median_ms": round(statistics.median(server_values), 1) if server_values else None,
            "manual_trials": manual_trials,
            "criterion": 300,
            "criterion_scope": "server stop-push only; device buffer judged by ear separately",
        },
        "limitations": [
            "silence_after_ms comes from the server trace and excludes device buffer drain",
            "manual stopwatch values are operator-entered and are not pass evidence",
            "no value is invented when the trace is missing",
        ],
    }


def _safe_post(api: Api, path: str, body: dict) -> None:
    try:
        api.post(path, body)
    except Exception as exc:  # noqa: BLE001 - the waiter reports the missing trace
        print(f"  auto-say failed: {exc}")


def run_check(api: Api) -> int:
    for path in ("/health", "/api/v1/audio", "/api/v1/traces?limit=5"):
        try:
            body = api.get(path)
            print(f"OK  {path}  {json.dumps(body, ensure_ascii=False)[:240]}")
        except (ApiError, RuntimeError) as exc:
            print(f"ERR {path}  {exc}")
            return 1
    return 0


def _default_out() -> str:
    """Host-visible output dir: containers often run on a read-only image layer.

    The mounted data volume (/var/lib/rui) survives a container recreate; a path
    under /data or the cwd does not (an earlier run lost its barge JSON that way).
    """
    env = (os.environ.get("RUI_ACCEPTANCE_OUT")
           or os.environ.get("HOUSE_EAR_ACCEPTANCE_OUT") or "").strip()
    if env:
        return env
    mounted = Path("/var/lib/rui")
    try:
        if mounted.is_dir() and os.access(mounted, os.W_OK):
            return str(mounted / "acceptance-runs")
    except OSError:
        pass
    return "acceptance-runs"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--url", default="http://127.0.0.1:8080",
                       help="control-plane base URL (default loopback)")
        p.add_argument("--token-env", default="RUI_TOKEN",
                       help="env var holding X-Auth-Token (default RUI_TOKEN)")
        p.add_argument("--out", default=_default_out(),
                       help="directory for JSON/Markdown (default: ./acceptance-runs, or "
                            "/var/lib/rui/acceptance-runs inside the container; "
                            "override with RUI_ACCEPTANCE_OUT)")
        p.add_argument("--label", default="", help="label, e.g. near-field or far-field")
        p.add_argument("--check", action="store_true",
                       help="only GET /health, /audio, /traces and exit")

    wake = sub.add_parser("wake", help="guided 20-trial wake N/M")
    common(wake)
    wake.add_argument("--trials", type=int, default=20)
    wake.add_argument("--criterion", type=float, default=0.95,
                      help="pass ratio (default 0.95 = 19/20)")
    wake.add_argument("--no-gate", action="store_true",
                      help="record only (far-field run); do not compute PASS/FAIL")

    barge = sub.add_parser("barge", help="guided barge latency from server traces")
    common(barge)
    barge.add_argument("--trials", type=int, default=3)
    barge.add_argument("--timeout", type=float, default=20.0,
                       help="seconds to wait for a new trace per trial")
    barge.add_argument("--auto-say", action="store_true",
                       help="start the long answer with POST /api/v1/debug/say")
    barge.add_argument("--say-text", default="背一遍出师表",
                       help="text used with --auto-say")

    args = ap.parse_args()
    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"not set: {args.token_env}. Get it with:\n"
              "  docker compose exec -T rui rui token\n"
              "then: export RUI_TOKEN=...   (do not paste the token into chat/logs)",
              file=sys.stderr)
        # Loopback/dev may allow anonymous; continue and let the API answer.
    api = Api(args.url, token)
    if args.check:
        return run_check(api)

    if args.mode == "wake":
        payload = run_wake(api, args)
    else:
        payload = run_barge(api, args)
    paths = write_outputs(args.mode, payload, Path(args.out))
    print("\n".join(f"wrote {p}" for p in paths))
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\naborted")
        raise SystemExit(130) from None
