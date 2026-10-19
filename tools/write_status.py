#!/usr/bin/env python3
"""
write_status.py — record what each data source did on this CI run, for the
dashboard's Status tab. Stdlib only, no network.

Every fetch/scrape step in refresh.yml is best-effort (continue-on-error), and
each refuses to overwrite its feed when it gets nothing back. That keeps the
site up, but it also hides a broken source: the previous feed survives and
looks fine. Freshness catches a feed that stops changing, but not one that is
limping — Public Bank's listings moving on 2026-10-16 still produced a feed,
just with 31 promos missing. So each run writes down, per source:

  result       "ok" | "fail" for the last run that actually tried this source
  at           when that was
  error        the failing line from the step's log (fail only)
  note         the step's own one-line summary, e.g. "136 cards listed" (ok only)
  last_ok_at   the last run it succeeded, carried across runs
  fail_streak  consecutive failed runs, carried across runs

A source the run skipped (the push-triggered fast path skips the promo scrape
and the engine) keeps its previous record untouched, so the page always shows
the last real attempt.

Usage (refresh.yml, after every fetch step, `if: always()`):
  python tools/write_status.py --trigger push \
      --step macro=success --step engine=skipped \
      --outcomes "$RUNNER_TEMP/scrape-outcomes" --logdir "$RUNNER_TEMP"

--step takes a GitHub step outcome (success/failure/cancelled/skipped) or an
exit code; --outcomes is a file of `key=exitcode` lines (the per-bank scrapers
share one step); --logdir holds `log-<key>.txt` files to pull errors/notes from.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "data", "status.json")

_ERROR_HINT = re.compile(r"fail|error|refus|may have changed|moved|not found|timed? ?out|exception", re.I)
# A step's summary line: "[scrape] 136 cards listed; keeping 53 …", "[hlb] 7 card promos -> …",
# "[merge] pbb.json: 53 promos, …". Tagged and carrying a number.
_NOTE_LINE = re.compile(r"^\[\w+\]\s.*\d")
_NOISE = re.compile(r"^(Next:|Optional|hint:|::)", re.I)


def normalise(outcome: str) -> str | None:
    """'ok' / 'fail', or None for a step that did not run this time."""
    o = (outcome or "").strip().lower()
    if o in ("", "skipped"):
        return None
    if o in ("success", "0"):
        return "ok"
    return "fail"   # failure, cancelled, any non-zero exit code


def read_log(logdir: str | None, key: str) -> list[str]:
    if not logdir:
        return []
    path = os.path.join(logdir, f"log-{key}.txt")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        return []


def error_line(lines: list[str]) -> str:
    """The line that says what broke: the last one that sounds like an error
    (a traceback's final line qualifies), else simply the last line."""
    lines = [ln for ln in lines if not _NOISE.match(ln)]
    for ln in reversed(lines):
        if _ERROR_HINT.search(ln):
            return ln[:300]
    return lines[-1][:300] if lines else ""


def note_line(lines: list[str]) -> str:
    for ln in lines:
        if _NOTE_LINE.match(ln) and not _NOISE.match(ln):
            return ln[:200]
    return ""


def update(prior: dict, result: str | None, lines: list[str], now: str) -> dict:
    """The new record for one source. A skipped source keeps its prior record."""
    if result is None:
        return prior
    rec = {"result": result, "at": now,
           "last_ok_at": now if result == "ok" else prior.get("last_ok_at", ""),
           "fail_streak": 0 if result == "ok" else int(prior.get("fail_streak", 0)) + 1}
    if result == "fail":
        rec["error"] = error_line(lines) or "step failed (no log captured)"
    else:
        note = note_line(lines)
        if note:
            rec["note"] = note
    return rec


def parse_pairs(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        k, _, v = item.partition("=")
        if k.strip():
            out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", action="append", default=[], help="key=outcome (repeatable)")
    ap.add_argument("--outcomes", help="file of key=exitcode lines")
    ap.add_argument("--logdir", help="directory holding log-<key>.txt files")
    ap.add_argument("--trigger", default="", help="github.event_name")
    ap.add_argument("--out", default=STATUS)
    args = ap.parse_args(argv)

    steps = parse_pairs(args.step)
    if args.outcomes:
        try:
            with open(args.outcomes, encoding="utf-8") as f:
                steps.update(parse_pairs(f.read().split()))
        except OSError:
            pass   # the scrape step did not run (fast path)

    try:
        with open(args.out, encoding="utf-8") as f:
            prior = json.load(f)
    except (OSError, ValueError):
        prior = {}
    sources = dict(prior.get("sources") or {})

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for key, outcome in steps.items():
        if normalise(outcome) is None and key not in sources:
            continue   # skipped, and never run before: nothing to record yet
        sources[key] = update(sources.get(key) or {}, normalise(outcome),
                              read_log(args.logdir, key), now)

    server, repo, run_id = (os.environ.get(k, "") for k in
                            ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    doc = {
        "generated_at": now,
        "run": {"trigger": args.trigger,
                "url": f"{server}/{repo}/actions/runs/{run_id}" if run_id else ""},
        "sources": dict(sorted(sources.items())),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    ran = {k: v for k, v in steps.items() if normalise(v)}
    failed = [k for k, v in ran.items() if normalise(v) == "fail"]
    print(f"[status] {len(ran)} sources ran, {len(failed)} failed"
          + (f": {', '.join(failed)}" if failed else "") + f" -> {os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
