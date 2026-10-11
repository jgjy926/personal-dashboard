#!/usr/bin/env python3
"""
stamp_version.py — stamp the CODE version into a STAGED copy of the site.

    python tools/stamp_version.py <staged_site_dir>

Run by every deploy after the site is copied out of the checkout, so the repo's
own index.html is never touched.

The version is the dashboard's CODE, not its data. It comes from the last
commit that changed a file the page itself is built from, skipping DATA_PATHS
(feeds, the tools that write them, workflows, workers, docs). The daily feed
commits from refresh.yml therefore leave it alone: a data refresh shows up as a
fresher "as of" line on its tab, and a new version means new code.

It does three things:

  1. Writes <site>/version.json — the live code version (commit date + hash,
     subject, link) plus when/why this deploy ran, for reference.
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


def code_commit() -> dict:
    """The newest commit that touched the page's code (see DATA_PATHS)."""
    excludes = [f":(exclude,glob){p}" if "*" in p else f":(exclude){p}" for p in DATA_PATHS]
    fmt = "%H%x1f%s%x1f%cI"
    line = _git("log", "-1", f"--format={fmt}", "--", ".", *excludes) or _git("log", "-1", f"--format={fmt}")
    sha, subject, date = (line.split("\x1f") + ["", "", ""])[:3]
    return {"sha": sha, "subject": subject, "date": date}


def version_of(commit_date: str, short: str) -> str:
    """'2026-10-11T17:45:00+08:00', 'abc1234' -> '2026.10.11-abc1234' (MYT date)."""
    try:
        day = datetime.fromisoformat(commit_date).astimezone(MYT).strftime("%Y.%m.%d")
    except ValueError:
        day = "unknown"
    return f"{day}-{short}"


def build_info(now: datetime | None = None, env=os.environ, commit: dict | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    commit = commit or code_commit()
    sha = commit.get("sha") or env.get("GITHUB_SHA", "")
    short = sha[:7] or "unknown"
    repo_url = (f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}"
                if env.get("GITHUB_SERVER_URL") and env.get("GITHUB_REPOSITORY") else "")
    run_url = f"{repo_url}/actions/runs/{env['GITHUB_RUN_ID']}" if repo_url and env.get("GITHUB_RUN_ID") else ""
    return {
        "version": version_of(commit.get("date", ""), short),
        # What the page compares against its own <meta name="build-id">.
        "build_id": short,
        "commit": {
            "sha": sha,
            "short": short,
            "subject": commit.get("subject", ""),
            "date": commit.get("date", ""),
            "url": f"{repo_url}/commit/{sha}" if repo_url and sha else "",
        },
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
    print(f"[stamp_version] v{info['version']} ({info['commit']['subject'][:60]}) · {info['trigger']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
