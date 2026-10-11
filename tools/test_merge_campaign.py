#!/usr/bin/env python3
"""Unit tests for tools/merge_campaign.py — the end_date rules. No network.

    python -m unittest discover -s tools -p 'test_*.py'

end_date decides when the dashboard HIDES a promo, so the parser is deliberately
timid: a period it can't read with confidence yields "" (no badge, never hidden)
rather than a plausible date that would bury an offer that is still running.
The strings below are real periods from the Public Bank feed where possible.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_campaign as mc  # noqa: E402


class TestEndDateFromPeriod(unittest.TestCase):
    def check(self, period, want):
        self.assertEqual(mc.end_date_from_period(period), want, period)

    def test_ranges_seen_in_the_feed(self):
        self.check("15 August to 25 September 2026", "2026-09-25")
        self.check("8 September - 27 December 2026", "2026-12-27")
        self.check("1 September 2026 - 28 February 2027", "2027-02-28")
        self.check("15 August 2026 - 31 January 2027", "2027-01-31")

    def test_open_start(self):
        self.check("Now until 30 June 2027", "2027-06-30")
        self.check("Now - 14 December 2026", "2026-12-14")

    def test_recurring_windows_take_the_last_one(self):
        self.check("8th (8PM) – 11th (11:59PM) Sep 2026; 9th–12th Oct 2026; 10th–13th Nov 2026; "
                   "11th–14th Dec 2026; 1st–4th Feb 2027; 2nd–5th Mar 2027; 3rd–6th Apr 2027",
                   "2027-04-06")

    def test_year_on_the_end_date_only_crossing_new_year(self):
        self.check("1 Dec - 5 Jan 2027", "2027-01-05")

    def test_ambiguous_periods_give_nothing(self):
        for period in ("", "Ongoing", "Valid through 2026",
                       "24th (8PM) – 27th (11:59PM) monthly, from September 2026 to April 2027",
                       "From 1 Sep 2026",                      # a lone START date
                       "1 December 2026 - 5 January 2026",     # end before start
                       "Until 30 February 2027",               # not a date
                       "31 December"):                         # no year anywhere
            self.check(period, "")


class TestMergeFile(unittest.TestCase):
    """merge_file: one draft folded into its own live feed."""

    def merge(self, draft_promos, live_promos, meta=None):
        with tempfile.TemporaryDirectory() as tmp:
            draft = os.path.join(tmp, "x.draft.json")
            live = os.path.join(tmp, "x.json")
            mc.save(draft, {"meta": meta or {"bank": "hlb", "kind": "campaign"}, "promotions": draft_promos})
            mc.save(live, {"promotions": live_promos})
            mc.merge_file(draft, live)
            return mc.load(live)

    def test_hand_written_end_date_wins_and_missing_ones_are_derived(self):
        out = self.merge([
            {"id": "manual", "title": "A", "period": "", "tnc_summary": ""},
            {"id": "derived", "title": "B", "period": "", "tnc_summary": ""},
            {"id": "fresh", "title": "C", "period": "", "tnc_summary": ""},
        ], [
            {"id": "manual", "period": "24th - 27th monthly, from September 2026 to April 2027",
             "end_date": "2027-04-27", "tnc_summary": "s"},
            {"id": "derived", "period": "1 September - 31 December 2026", "tnc_summary": "s"},
        ])
        got = {p["id"]: p["end_date"] for p in out["promotions"]}
        self.assertEqual(got, {"manual": "2027-04-27", "derived": "2026-12-31", "fresh": ""})

    def test_summary_and_first_seen_carry_forward(self):
        out = self.merge([{"id": "a", "title": "A", "period": "", "tnc_summary": ""}],
                         [{"id": "a", "tnc_summary": "kept", "first_seen": "2026-01-02", "tnc_link": "t.pdf"}])
        p = out["promotions"][0]
        self.assertEqual((p["tnc_summary"], p["first_seen"], p["tnc_link"]), ("kept", "2026-01-02", "t.pdf"))

    def test_a_period_the_bank_changed_replaces_the_old_end_date(self):
        out = self.merge([{"id": "a", "period": "18 August to 31 December 2026", "tnc_summary": ""}],
                         [{"id": "a", "period": "29 September 2026-28 October 2026", "end_date": "2026-10-28"}])
        self.assertEqual(out["promotions"][0]["end_date"], "2026-12-31")

    def test_a_deals_scraped_text_replaces_yesterdays(self):
        # Deals carry the bank's own words; when the bank edits them, the edit wins.
        out = self.merge([{"id": "d", "tnc_summary": "RM30 off", "summary_source": "bank", "period": ""}],
                         [{"id": "d", "tnc_summary": "RM20 off", "summary_source": "bank"}],
                         {"bank": "uob", "kind": "deal"})
        self.assertEqual(out["promotions"][0]["tnc_summary"], "RM30 off")
        self.assertEqual(out["meta"]["kind"], "deal")
        self.assertEqual(out["meta"]["bank_name"], "UOB")

    def test_a_promo_the_bank_delisted_is_dropped(self):
        out = self.merge([{"id": "a", "period": ""}], [{"id": "a"}, {"id": "gone", "tnc_summary": "s"}])
        self.assertEqual([p["id"] for p in out["promotions"]], ["a"])


if __name__ == "__main__":
    unittest.main()
