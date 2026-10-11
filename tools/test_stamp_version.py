#!/usr/bin/env python3
"""Unit tests for tools/stamp_version.py.

The property that matters: the version follows the page's CODE, so the daily
feed commits from the bot must not change it."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stamp_version as sv  # noqa: E402

HTML = """<meta name="build-id" content="dev" />
<link rel="stylesheet" href="styles.css?v=19" />
<script src="config.js?v=19"></script>
<script src="app.js?v=19"></script>
<a href="https://example.com/?v=19">untouched: not a .js/.css asset</a>"""

COMMIT = {"sha": "65c754d0000000000000000000000000000000aa",
          "subject": "feat: something", "date": "2026-10-11T01:30:00+00:00"}


class TestStamp(unittest.TestCase):
    def setUp(self):
        env = {"GITHUB_EVENT_NAME": "schedule", "GITHUB_SERVER_URL": "https://github.com",
               "GITHUB_REPOSITORY": "me/dash", "GITHUB_RUN_ID": "42"}
        self.info = sv.build_info(datetime(2026, 10, 12, 4, 20, tzinfo=timezone.utc), env, COMMIT)

    def test_version_is_the_code_commit_not_the_deploy(self):
        # Committed 01:30 UTC = 09:30 MYT on the 11th; deployed a day later.
        self.assertEqual(self.info["version"], "2026.10.11-65c754d")
        self.assertEqual(self.info["build_id"], "65c754d")
        self.assertEqual(self.info["deployed_at"], "2026-10-12T04:20:00Z")
        self.assertEqual(self.info["run_url"], "https://github.com/me/dash/actions/runs/42")

    def test_html_gets_build_id_and_cache_busters(self):
        out = sv.stamp_html(HTML, self.info)
        self.assertIn('content="65c754d"', out)
        for asset in ("styles.css", "config.js", "app.js"):
            self.assertIn(f"{asset}?v=65c754d", out)
        self.assertIn("https://example.com/?v=19", out)


class TestCodeCommit(unittest.TestCase):
    """Against a throwaway git repo: a data-only commit must not move the version."""

    def git(self, *a):
        subprocess.run(["git", *a], cwd=self.dir, check=True, capture_output=True)

    def write(self, path, text):
        full = os.path.join(self.dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write("app.js", "1")
        self.git("add", "-A"); self.git("commit", "-qm", "code change")
        self.write("data/feed.json", "{}")
        self.write("tools/x.py", "")
        self.write("README.md", "")
        self.git("add", "-A"); self.git("commit", "-qm", "chore: refresh feeds")
        self.cwd = os.getcwd()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_data_commits_are_skipped(self):
        self.assertEqual(sv.code_commit()["subject"], "code change")

    def test_a_code_commit_moves_it(self):
        self.write("styles.css", "x")
        self.git("add", "-A"); self.git("commit", "-qm", "style tweak")
        self.assertEqual(sv.code_commit()["subject"], "style tweak")


if __name__ == "__main__":
    unittest.main()
