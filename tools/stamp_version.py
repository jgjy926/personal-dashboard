#!/usr/bin/env python3
"""
stamp_version.py — stamp the CODE version into a STAGED copy of the site.

    python tools/stamp_version.py <staged_site_dir>

Run by every deploy after the site is copied out of the checkout, so the repo's
own index.html is never touched.

Two versions, each "<MYT date>-<n>" where n counts that kind of commit on that
day (2026.10.11-3 = the third change that day):

  code  the last commit that changed a file the page itself is built from,
        skipping DATA_PATHS (feeds, the tools that write them, workflows,
        workers, docs). The daily feed commits leave it alone.
  data  the last commit that changed anything under data/ (the daily refresh,
        a summary published from the Console).

The build id (what an open tab compares to spot new code) stays the code
commit's hash; only the code version can make a tab say "reload".

It does three things:

  1. Writes <site>/version.json — both versions (date, count, hash, subject,
     link) plus when/why this deploy ran, for reference.
  2. Fills <meta name="build-id"> in index.html with that code commit, so a tab
     left open can tell it's running older code than what's live now.
  3. Rewrites the asset cache-busters (styles.css?v=…, app.js?v=…) to the same
     hash, so a code change always reaches the browser and a data-only deploy
     never forces a re-download.

Needs the checkout's history (actions/checkout `fetch-depth: 0`) to look past
the bot's data commits; with a shallow clone it falls back to HEAD.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

MYT = timezone(timedelta(hours=8))
TRIGGERS = {
    "schedule": "scheduled refresh",
    "push": "code push",
    "workflow_dispatch": "manual run",
}
# Not part of the page's code: changing only these is not a new version.
DATA_PATHS = ["data", "tools", "worker", ".github", ".claude", "*.md", ".gitignore"]


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True, encoding="utf-8",
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _myt_day(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(MYT).strftime("%Y.%m.%d")
    except ValueError:
        return "unknown"


def latest_commit(*pathspec: str) -> dict:
    """The newest commit touching `pathspec`, and `seq`: its position among the
    commits touching it on the same MYT day (1 = the first that day)."""
    fmt = "%H%x1f%s%x1f%cI"
    lines = _git("log", "-n", "300", f"--format={fmt}", "--", *pathspec).splitlines()
    rows = [(ln.split("\x1f") + ["", "", ""])[:3] for ln in lines if ln]
    if not rows:
        return {"sha": "", "subject": "", "date": "", "seq": 0}
    sha, subject, date = rows[0]
    day = _myt_day(date)
    # Newest first, so the same-day run at the top is everything up to this one.
    seq = 0
    for _, _, d in rows:
        if _myt_day(d) != day:
            break
        seq += 1
    return {"sha": sha, "subject": subject, "date": date, "seq": seq}


def code_commit() -> dict:
    """The newest commit that touched the page's code (see DATA_PATHS)."""
    excludes = [f":(exclude,glob){p}" if "*" in p else f":(exclude){p}" for p in DATA_PATHS]
    got = latest_commit(".", *excludes)
    return got if got["sha"] else latest_commit(".")


def data_commit() -> dict:
    """The newest commit that changed a feed under data/."""
    return latest_commit("data")


def version_of(commit_date: str, seq: int) -> str:
    """'2026-10-11T17:45:00+08:00', 3 -> '2026.10.11-3' (MYT date, nth that day)."""
    return f"{_myt_day(commit_date)}-{seq or '?'}"


def _commit_block(commit: dict, repo_url: str, fallback_sha: str = "") -> dict:
    sha = commit.get("sha") or fallback_sha
    return {
        "version": version_of(commit.get("date", ""), commit.get("seq", 0)),
        "sha": sha,
        "short": sha[:7] or "unknown",
        "seq": commit.get("seq", 0),
        "subject": commit.get("subject", ""),
        "date": commit.get("date", ""),
        "url": f"{repo_url}/commit/{sha}" if repo_url and sha else "",
    }


def build_info(now: datetime | None = None, env=os.environ, commit: dict | None = None,
               data: dict | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    commit = commit or code_commit()
    data = data or data_commit()
    repo_url = (f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}"
                if env.get("GITHUB_SERVER_URL") and env.get("GITHUB_REPOSITORY") else "")
    run_url = f"{repo_url}/actions/runs/{env['GITHUB_RUN_ID']}" if repo_url and env.get("GITHUB_RUN_ID") else ""
    code = _commit_block(commit, repo_url, env.get("GITHUB_SHA", ""))
    return {
        "version": code["version"],
        # What the page compares against its own <meta name="build-id">.
        "build_id": code["short"],
        "commit": code,
        "data": _commit_block(data, repo_url),
        # Reference only — a deploy can carry new data with the same code.
        "deployed_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "trigger": TRIGGERS.get(env.get("GITHUB_EVENT_NAME", ""), env.get("GITHUB_EVENT_NAME") or "local"),
        "run_url": run_url,
    }


def stamp_html(html: str, info: dict) -> str:
    html = re.sub(r'(<meta name="build-id" content=")[^"]*(")',
                  lambda m: m.group(1) + info["build_id"] + m.group(2), html)
    return re.sub(r'(\.(?:css|js))\?v=[\w.-]+', lambda m: f'{m.group(1)}?v={info["build_id"]}', html)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print("usage: python tools/stamp_version.py <staged_site_dir>", file=sys.stderr)
        return 2
    site = argv[1]
    info = build_info()
    with open(os.path.join(site, "version.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1, ensure_ascii=False)
    index = os.path.join(site, "index.html")
    with open(index, encoding="utf-8") as f:
        html = f.read()
    with open(index, "w", encoding="utf-8") as f:
        f.write(stamp_html(html, info))
    print(f"[stamp_version] code v{info['version']} ({info['commit']['subject'][:50]}) · "
          f"data {info['data']['version']} · {info['trigger']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
