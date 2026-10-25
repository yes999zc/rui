#!/usr/bin/env python3
"""Poll the RUI upstream (GitHub + GHCR) and print only what changed.

State file: --state (default /tmp/rui-upstream-state.json). Keeps a snapshot of
open PRs, open issues, releases and GHCR semver tags. Use in a scheduled task:

    python3 scripts/watch-rui-upstream.py
    python3 scripts/watch-rui-upstream.py --mark

`--mark` rewrites the snapshot without printing a diff (use after you've acted).
Token: $GITHUB_TOKEN, else the github.com entry of ~/.git-credentials (read-only).
Exit codes: 0 success (changes or not), 2 network/API failure (state untouched).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.request

REPO = "yes999zc/rui"


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    try:
        line = next(
            l for l in pathlib.Path.home().joinpath(".git-credentials").read_text().splitlines()
            if "github.com" in l
        )
        return line.split("//", 1)[1].split("@", 1)[0].split(":", 1)[1]
    except Exception:
        return ""


def _get(url: str, token: str) -> object:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "rui-upstream-watch"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.load(r)


def _ghcr_tags() -> list[str]:
    with urllib.request.urlopen(
        "https://ghcr.io/token?scope=repository:yes999zc/rui:pull&service=ghcr.io", timeout=20
    ) as r:
        token = json.load(r)["token"]
    req = urllib.request.Request(
        "https://ghcr.io/v2/yes999zc/rui/tags/list",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "rui-upstream-watch"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        tags = json.load(r).get("tags") or []
    return sorted(t for t in tags if not t.startswith("sha-"))


def snapshot(token: str) -> dict:
    prs = _get(f"https://api.github.com/repos/{REPO}/pulls?state=open&per_page=50", token)
    issues = [
        i for i in _get(f"https://api.github.com/repos/{REPO}/issues?state=open&per_page=50", token)
        if "pull_request" not in i
    ]
    releases = _get(f"https://api.github.com/repos/{REPO}/releases?per_page=10", token)
    commits = _get(f"https://api.github.com/repos/{REPO}/commits?per_page=5", token)
    return {
        "prs": {str(p["number"]): {"title": p["title"], "user": p["user"]["login"], "head": p["head"]["ref"]} for p in prs},
        "issues": {str(i["number"]): i["title"] for i in issues},
        "releases": [r["tag_name"] for r in releases],
        "commits": [c["sha"][:12] for c in commits],
        "images": _ghcr_tags(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="/tmp/rui-upstream-state.json")
    ap.add_argument("--mark", action="store_true")
    args = ap.parse_args()
    token = _token()
    try:
        new = snapshot(token)
    except Exception as exc:
        print(f"WATCH_FAILED: {type(exc).__name__}: {exc}")
        return 2
    path = pathlib.Path(args.state)
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text())
        except Exception:
            old = {}
    if args.mark or not old:
        path.write_text(json.dumps(new, ensure_ascii=False, indent=2))
        if not old:
            print("WATCH_BASELINE 已建立")
        else:
            print("WATCH_MARKED")
        return 0
    changed = False
    for key, label in (("prs", "PR"), ("issues", "Issue")):
        before, after = old.get(key, {}), new.get(key, {})
        for num, info in after.items():
            if num not in before:
                changed = True
                title = info["title"] if isinstance(info, dict) else info
                extra = f" by {info['user']}" if isinstance(info, dict) else ""
                print(f"+ 新 {label} #{num}: {title}{extra}")
        for num, info in before.items():
            if num not in after:
                changed = True
                title = info["title"] if isinstance(info, dict) else info
                print(f"- 已关闭 {label} #{num}: {title}")
    for tag in new["releases"]:
        if tag not in old.get("releases", []):
            changed = True
            print(f"+ 新 Release: {tag}")
    if new["commits"] and new["commits"][0] not in old.get("commits", []):
        changed = True
        print(f"+ main 新提交: {new['commits'][0]}")
    for tag in new["images"]:
        if tag not in old.get("images", []):
            changed = True
            print(f"+ 新镜像 tag: {tag}")
    if not changed:
        print("NO_CHANGES")
    path.write_text(json.dumps(new, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
