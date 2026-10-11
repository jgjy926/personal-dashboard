#!/usr/bin/env python3
"""
stamp_version.py — stamp the build version into a STAGED copy of the site.

    python tools/stamp_version.py <staged_site_dir>

Run by both deploy workflows after the site is copied out of the checkout, so
the repo's own index.html is never touched. It does three things:

  1. Writes <site>/version.json — what is live right now: the code version
     (commit), when this deploy happened, and what triggered it. The page
     re-reads this every few minutes, which is how an open tab learns that a
     newer deploy has gone out.
  2. Fills <meta name="build-id"> in index.html with this deploy's id, so the
     page knows which build IT is (a tab left open since yesterday is an old
     build even though version.json on the server is new).
  3. Rewrites the asset cache-busters (styles.css?v=…, app.js?v=…) to the
     commit hash. They used to be bumped by hand, and a forgotten bump meant
     the browser kept running yesterday's JavaScript against today's data.

The displayed version is the deploy date+time in Malaysia time (v2026.10.11-1220)
plus the short commit — the date says how fresh the site is, the commit says
which code it runs. The hourly air-quality deploy bumps the first, not the second.
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


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def build_info(now: datetime | None = None, env=os.environ) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sha = _git("rev-parse", "HEAD") or env.get("GITHUB_SHA", "")
    short = sha[:7] or "unknown"
    version = now.astimezone(MYT).strftime("%Y.%m.%d-%H%M")
    repo_url = (f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}"
                if env.get("GITHUB_SERVER_URL") and env.get("GITHUB_REPOSITORY") else "")
    run_url = f"{repo_url}/actions/runs/{env['GITHUB_RUN_ID']}" if repo_url and env.get("GITHUB_RUN_ID") else ""
    return {
        "version": version,
        "build_id": f"{version}+{short}",
        "deployed_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "trigger": TRIGGERS.get(env.get("GITHUB_EVENT_NAME", ""), env.get("GITHUB_EVENT_NAME") or "local"),
        "workflow": env.get("GITHUB_WORKFLOW", ""),
        "run_url": run_url,
        "commit": {
            "sha": sha,
            "short": short,
            "subject": _git("log", "-1", "--format=%s"),
            "date": _git("log", "-1", "--format=%cI"),
            "url": f"{repo_url}/commit/{sha}" if repo_url and sha else "",
        },
    }


def stamp_html(html: str, info: dict) -> str:
    html = re.sub(r'(<meta name="build-id" content=")[^"]*(")',
                  lambda m: m.group(1) + info["build_id"] + m.group(2), html)
    return re.sub(r'(\.(?:css|js))\?v=[\w.-]+', lambda m: f'{m.group(1)}?v={info["commit"]["short"]}', html)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
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
    print(f"[stamp_version] v{info['version']} · {info['commit']['short']} · {info['trigger']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
