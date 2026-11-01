#!/usr/bin/env python3
"""Unit tests for tools/write_status.py — the per-run source record the Status
tab grades from. No network.

    python -m unittest discover -s tools -p 'test_*.py'

The record must survive the runs that do not touch a source (the push fast path
skips the promo scrape), and a failure must say what broke, in the step's own
words, rather than just "failed".
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import write_status as ws  # noqa: E402


class TestNormalise(unittest.TestCase):
    def test_outcomes_and_exit_codes(self):
        self.assertEqual(ws.normalise("success"), "ok")
        self.assertEqual(ws.normalise("0"), "ok")
        self.assertEqual(ws.normalise("failure"), "fail")
        self.assertEqual(ws.normalise("cancelled"), "fail")
        self.assertEqual(ws.normalise("1"), "fail")
        self.assertIsNone(ws.normalise("skipped"))
        self.assertIsNone(ws.normalise(""))


class TestLogLines(unittest.TestCase):
    def test_error_prefers_the_failing_line_over_the_hint_after_it(self):
        lines = ["[uob] campaigns failed: HTTPError: 503 Server Error",
                 "[uob] wrote data/promos/uob*.draft.json. Next: python tools/merge_campaign.py"]
        self.assertEqual(ws.error_line(lines), lines[0])

    def test_error_takes_a_tracebacks_last_line(self):
        lines = ["Traceback (most recent call last):", '  File "x.py", line 1',
                 "requests.exceptions.ConnectTimeout: timed out"]
        self.assertEqual(ws.error_line(lines), lines[-1])

    def test_listings_moved_message_is_the_error(self):
        lines = ["[scrape] listing: https://www.pbebank.com/en/promotions/credit-cards-promotions/",
                 "[scrape] only 3/49 already-summarised promos are still in the listings (31 cards "
                 "read) — the site has probably moved its listings. NOT writing a draft"]
        self.assertIn("moved its listings", ws.error_line(lines))

    def test_note_is_the_first_tagged_line_with_a_count(self):
        lines = ["[scrape] listing: https://www.pbebank.com/en/promotions/credit-cards-promotions/",
                 "[scrape] 136 cards listed; keeping 53 (top 15 of each listing …)",
                 "[scrape] 17/53 promos have usable text"]
        self.assertTrue(ws.note_line(lines).startswith("[scrape] 136 cards listed"))


class TestUpdate(unittest.TestCase):
    NOW = "2026-10-19T13:00:00+00:00"

    def test_skipped_keeps_the_prior_record(self):
        prior = {"result": "ok", "at": "2026-10-18T13:00:00+00:00"}
        self.assertIs(ws.update(prior, None, [], self.NOW), prior)

    def test_failures_count_up_and_keep_last_ok(self):
        prior = {"result": "fail", "last_ok_at": "2026-10-16T13:00:00+00:00", "fail_streak": 2}
        rec = ws.update(prior, "fail", ["[hlb] boom error"], self.NOW)
        self.assertEqual(rec["fail_streak"], 3)
        self.assertEqual(rec["last_ok_at"], "2026-10-16T13:00:00+00:00")
        self.assertEqual(rec["error"], "[hlb] boom error")

    def test_success_resets_the_streak(self):
        rec = ws.update({"fail_streak": 4}, "ok", [], self.NOW)
        self.assertEqual((rec["fail_streak"], rec["last_ok_at"]), (0, self.NOW))
        self.assertNotIn("error", rec)


class TestIngestGrade(unittest.TestCase):
    """The engine exits 0 however little it ingested, so its log decides the grade."""
    NOW = "2026-10-31T18:20:00+00:00"
    PREFIX = "2026-10-31 18:19:58,938 "
    # The shape of run 41202388458's log.
    BAD = ["Successfully installed pandas-3.0.0",
           PREFIX + "ERROR   [ingest] PERMIT: FAILED -- FRED PERMIT: TimeoutError: The read operation timed out",
           PREFIX + "ERROR   [ingest] HOUST: FAILED -- FRED HOUST: TimeoutError: The read operation timed out",
           PREFIX + "INFO    [ingest] 1/84 series OK, 0 new obs, 1 revisions, FAILED: PERMIT, HOUST",
           PREFIX + "INFO    [main] 2026-10-31 | LATE_CYCLE | 12m recession 12.9%"]
    GOOD = [PREFIX + "INFO    [ingest] GOLD via Yahoo GC=F (2514 obs) -- FRED ids retired",
            PREFIX + "INFO    [ingest] 84/84 series OK, 0 new obs, 0 revisions"]

    def test_most_series_failing_is_degraded_and_says_why(self):
        rec = ws.update({}, "ok", self.BAD, self.NOW)
        self.assertEqual(rec["result"], "degraded")
        self.assertIn("1/84", rec["error"])
        self.assertIn("TimeoutError", rec["error"])

    def test_degraded_counts_as_a_failed_run_across_runs(self):
        prior = {"result": "degraded", "last_ok_at": "2026-10-25T11:45:00+00:00", "fail_streak": 2}
        rec = ws.update(prior, "ok", self.BAD, self.NOW)
        self.assertEqual(rec["fail_streak"], 3)
        self.assertEqual(rec["last_ok_at"], "2026-10-25T11:45:00+00:00")

    def test_full_ingest_stays_ok_and_clears_the_streak(self):
        rec = ws.update({"result": "degraded", "fail_streak": 5}, "ok", self.GOOD, self.NOW)
        self.assertEqual((rec["result"], rec["fail_streak"], rec["last_ok_at"]), ("ok", 0, self.NOW))
        self.assertEqual(rec["note"], "[ingest] 84/84 series OK, 0 new obs, 0 revisions")
        self.assertNotIn("error", rec)

    def test_threshold_is_half_the_series(self):
        at = lambda n: ws.update({}, "ok", [f"[ingest] {n}/84 series OK, 0 new obs"], self.NOW)["result"]
        self.assertEqual((at(41), at(42)), ("degraded", "ok"))

    def test_partial_failure_stays_ok_but_names_them_in_the_note(self):
        rec = ws.update({}, "ok", ["[ingest] 80/84 series OK, 0 new obs, 0 revisions, FAILED: A, B"], self.NOW)
        self.assertEqual(rec["result"], "ok")
        self.assertIn("FAILED: A, B", rec["note"])

    def test_a_step_failure_is_not_softened_to_degraded(self):
        self.assertEqual(ws.update({}, "fail", self.BAD, self.NOW)["result"], "fail")

    def test_a_step_without_an_ingest_summary_is_untouched(self):
        rec = ws.update({}, "ok", ["[hlb] 7 card promos -> data/promos/hlb.draft.json"], self.NOW)
        self.assertEqual(rec["result"], "ok")
        self.assertTrue(rec["note"].startswith("[hlb] 7 card promos"))

    def test_main_records_degraded_from_the_engine_log(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "status.json")
            with open(os.path.join(d, "log-engine.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(self.BAD) + "\n")
            ws.main(["--out", out, "--step", "engine=success", "--logdir", d])
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["sources"]["engine"]["result"], "degraded")


class TestMain(unittest.TestCase):
    def test_round_trip_carries_skipped_sources(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "status.json")
            with open(os.path.join(d, "outcomes"), "w") as f:
                f.write("pbb=0\nhlb=1\n")
            with open(os.path.join(d, "log-hlb.txt"), "w", encoding="utf-8") as f:
                f.write("[hlb] no promo cards found — the page may have changed.\n")
            ws.main(["--out", out, "--step", "macro=success", "--outcomes",
                     os.path.join(d, "outcomes"), "--logdir", d, "--trigger", "schedule"])
            # A fast-path run: no scrape, macro fails.
            ws.main(["--out", out, "--step", "macro=failure", "--step", "engine=skipped",
                     "--trigger", "push"])
            with open(out, encoding="utf-8") as f:
                doc = json.load(f)
        src = doc["sources"]
        self.assertEqual(src["pbb"]["result"], "ok")              # untouched by run 2
        self.assertEqual(src["hlb"]["fail_streak"], 1)
        self.assertIn("may have changed", src["hlb"]["error"])
        self.assertEqual((src["macro"]["result"], src["macro"]["fail_streak"]), ("fail", 1))
        self.assertTrue(src["macro"]["last_ok_at"])
        self.assertNotIn("engine", src)
        self.assertEqual(doc["run"]["trigger"], "push")


class TestMergeDriver(unittest.TestCase):
    """Two overlapping CI runs both rewrite status.json; the merge keeps the newer
    record per source instead of conflicting (and losing the later run's commit)."""
    FAST = {"generated_at": "2026-10-19T14:25:00+00:00", "run": {"trigger": "push"},
            "sources": {"macro": {"result": "ok", "at": "2026-10-19T14:25:00+00:00"}}}
    FULL = {"generated_at": "2026-10-19T14:26:00+00:00", "run": {"trigger": "workflow_dispatch"},
            "sources": {"macro": {"result": "fail", "at": "2026-10-19T14:24:00+00:00"},
                        "pbb": {"result": "ok", "at": "2026-10-19T14:26:00+00:00"}}}

    def test_newer_record_wins_per_source(self):
        m = ws.merge_docs(self.FAST, self.FULL)
        self.assertEqual(m["sources"]["macro"]["result"], "ok")     # the fast run's is newer
        self.assertEqual(m["sources"]["pbb"]["result"], "ok")       # only the full run had it
        self.assertEqual(m["run"]["trigger"], "workflow_dispatch")  # the later run's header

    def test_order_does_not_matter(self):
        self.assertEqual(ws.merge_docs(self.FAST, self.FULL), ws.merge_docs(self.FULL, self.FAST))

    def test_rebase_conflict_resolves_through_the_driver(self):
        import shutil
        import subprocess
        git = shutil.which("git")
        if not git:
            self.skipTest("git not installed")
        script = os.path.abspath(ws.__file__).replace("\\", "/")
        with tempfile.TemporaryDirectory() as d:
            def run(*a):
                subprocess.run([git, *a], cwd=d, check=True, capture_output=True)

            def put(doc):
                with open(os.path.join(d, "status.json"), "w", encoding="utf-8") as f:
                    json.dump(doc, f, indent=2)
                run("add", "status.json")
                run("commit", "-qm", "x")
            run("init", "-q", "-b", "main")
            for k, v in (("user.name", "t"), ("user.email", "t@t"), ("core.autocrlf", "false"),
                         ("merge.status-json.driver", f'"{sys.executable}" "{script}" --merge-driver %A %B')):
                run("config", k, v)
            with open(os.path.join(d, ".gitattributes"), "w") as f:
                f.write("status.json merge=status-json\n")
            run("add", ".gitattributes")
            put({"generated_at": "", "sources": {}})
            run("checkout", "-qb", "full")
            put(self.FULL)
            run("checkout", "-q", "main")
            put(self.FAST)
            run("checkout", "-q", "full")
            run("rebase", "main")           # raises CalledProcessError on a conflict
            with open(os.path.join(d, "status.json"), encoding="utf-8") as f:
                got = json.load(f)
        self.assertEqual(set(got["sources"]), {"macro", "pbb"})
        self.assertEqual(got["sources"]["macro"]["result"], "ok")


if __name__ == "__main__":
    unittest.main()
