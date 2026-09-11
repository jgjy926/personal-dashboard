#!/usr/bin/env python3
"""Unit tests for the promo selection rule in tools/scrape_campaign.py — no network.

    python -m unittest discover -s tools -p 'test_*.py'

The bug this guards: the bank adds new promos at the TOP of its listing, so a fixed
"first N cards" window pushed older promos out, and the merge then deleted them
along with the summaries written for them — while they were still live. The feed
sat at exactly 15 even after 19 promos had been summarised.
"""
from __future__ import annotations

import os
import sys
import types
import unittest

# The test job installs no scraper deps; selection is pure, so stub the imports.
for mod in ("requests", "bs4"):
    if mod not in sys.modules:
        try:
            __import__(mod)
        except ImportError:
            stub = types.ModuleType(mod)
            stub.BeautifulSoup = object
            sys.modules[mod] = stub

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scrape_campaign as sc  # noqa: E402


def cards(n):
    return [{"id": f"p{i}", "title": f"Promo {i}"} for i in range(n)]


class TestSelectPromos(unittest.TestCase):
    def test_window_alone_keeps_the_top_of_the_listing(self):
        got = sc.select_promos(cards(30), 15, set())
        self.assertEqual([c["id"] for c in got], [f"p{i}" for i in range(15)])

    def test_summarised_promo_pushed_past_the_window_is_kept(self):
        # Two new promos land on top; the summarised p13/p14 slide to 15/16.
        listed = [{"id": "new1"}, {"id": "new2"}] + cards(20)
        got = [c["id"] for c in sc.select_promos(listed, 15, {"p13", "p14", "p19"})]
        self.assertEqual(len(got), 18)
        for pid in ("new1", "new2", "p13", "p14", "p19"):
            self.assertIn(pid, got)
        self.assertNotIn("p15", got, "unsummarised promos past the window stay out")

    def test_listing_order_is_preserved(self):
        got = [c["id"] for c in sc.select_promos(cards(20), 3, {"p10", "p5"})]
        self.assertEqual(got, ["p0", "p1", "p2", "p5", "p10"])

    def test_summarised_promo_the_bank_delisted_is_not_resurrected(self):
        got = [c["id"] for c in sc.select_promos(cards(5), 15, {"gone"})]
        self.assertNotIn("gone", got)

    def test_zero_window_means_everything(self):
        self.assertEqual(len(sc.select_promos(cards(40), 0, set())), 40)


if __name__ == "__main__":
    unittest.main()
