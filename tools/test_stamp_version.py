#!/usr/bin/env python3
"""Unit tests for tools/stamp_version.py — no network, no git required."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stamp_version as sv  # noqa: E402

HTML = """<meta name="build-id" content="dev" />
<link rel="stylesheet" href="styles.css?v=19" />
<script src="config.js?v=19"></script>
<script src="app.js?v=19"></script>
<a href="https://example.com/?v=19">untouched: not a .js/.css asset</a>"""


class TestStamp(unittest.TestCase):
    def setUp(self):
        self.env = {"GITHUB_EVENT_NAME": "schedule", "GITHUB_SERVER_URL": "https://github.com",
                    "GITHUB_REPOSITORY": "me/dash", "GITHUB_RUN_ID": "42"}
        self.info = sv.build_info(datetime(2026, 10, 11, 4, 20, tzinfo=timezone.utc), self.env)

    def test_version_is_malaysia_time(self):
        self.assertEqual(self.info["version"], "2026.10.11-1220")
        self.assertEqual(self.info["deployed_at"], "2026-10-11T04:20:00Z")
        self.assertEqual(self.info["trigger"], "scheduled refresh")
        self.assertEqual(self.info["run_url"], "https://github.com/me/dash/actions/runs/42")

    def test_html_gets_build_id_and_cache_busters(self):
        out = sv.stamp_html(HTML, self.info)
        short = self.info["commit"]["short"]
        self.assertIn(f'content="{self.info["build_id"]}"', out)
        self.assertIn(f"styles.css?v={short}", out)
        self.assertIn(f"app.js?v={short}", out)
        self.assertIn(f"config.js?v={short}", out)
        self.assertIn("https://example.com/?v=19", out)


if __name__ == "__main__":
    unittest.main()
