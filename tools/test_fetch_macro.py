#!/usr/bin/env python3
"""Unit tests for the pure half of tools/fetch_macro.py — no network.

    python tools/test_fetch_macro.py     (or: python -m unittest discover tools)

The fetch_* functions are the only ones that touch the network and are excluded
deliberately; everything below is parse/merge/assemble logic, which is where the
staleness bugs actually lived:

  * Yahoo bars dated in UTC instead of the exchange's own clock, which moved every
    USD/JPY observation one calendar day early.
  * A Reiwa-only date pattern that discarded every pre-2019 row of the MoF history.
  * No way to extend a slow FRED series with a faster source without clobbering
    the settled history behind it (`splice`).
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_macro as fm  # noqa: E402


class TestSplice(unittest.TestCase):
    """splice() is what lets an unofficial fast source extend an authoritative
    slow one. It must never rewrite a settled observation."""

    BASE = [("2026-09-01", 1.0), ("2026-09-02", 2.0)]

    def test_appends_only_strictly_newer(self):
        extra = [("2026-09-02", 99.0), ("2026-09-03", 3.0), ("2026-09-04", 4.0)]
        merged, added = fm.splice(self.BASE, extra)
        self.assertEqual(added, 2)
        # 2026-09-02 keeps FRED's 2.0; Yahoo's 99.0 for that day is ignored.
        self.assertEqual(merged, self.BASE + [("2026-09-03", 3.0), ("2026-09-04", 4.0)])

    def test_empty_base_degenerates_to_old_fallback(self):
        """A total FRED outage must still yield the Yahoo series, as before."""
        extra = [("2026-09-03", 3.0)]
        self.assertEqual(fm.splice([], extra), (extra, 1))

    def test_empty_extra_is_a_no_op(self):
        self.assertEqual(fm.splice(self.BASE, []), (self.BASE, 0))

    def test_extra_entirely_older_changes_nothing(self):
        self.assertEqual(fm.splice(self.BASE, [("2026-08-01", 7.0)]), (self.BASE, 0))


def _yahoo(gmtoffset, timestamps, closes, reg_start, reg_end, market_time):
    return json.dumps({"chart": {"result": [{
        "meta": {"gmtoffset": gmtoffset,
                 "currentTradingPeriod": {"regular": {"start": reg_start, "end": reg_end}},
                 "regularMarketTime": market_time},
        "timestamp": list(timestamps),
        "indicators": {"quote": [{"close": list(closes)}]}}]}})


class TestParseYahooChart(unittest.TestCase):
    # A London-clock session (JPY=X) opens at 23:00 UTC the day BEFORE the
    # calendar day it belongs to, so a UTC read dates every point one day early.
    # These are real JPY=X bar boundaries: 2026-09-03 23:00Z opens the session
    # that IS Friday 2026-09-04 in London.
    _utc = lambda *a: int(datetime.datetime(*a, tzinfo=datetime.timezone.utc).timestamp())
    PREV_OPEN = _utc(2026, 9, 2, 23)
    CUR_OPEN = _utc(2026, 9, 3, 23)
    CUR_CLOSE = _utc(2026, 9, 4, 22, 59, 59)

    def _local(self, ts, offset=3600):
        return datetime.datetime.fromtimestamp(
            ts + offset, datetime.timezone.utc).strftime("%Y-%m-%d")

    def test_dates_use_the_exchange_clock_not_utc(self):
        pts, _ = fm.parse_yahoo_chart(_yahoo(
            3600, [self.PREV_OPEN, self.CUR_OPEN], [155.66, 154.01],
            self.CUR_OPEN, self.CUR_CLOSE, self.CUR_OPEN + 50000))
        self.assertEqual([d for d, _ in pts],
                         [self._local(self.PREV_OPEN), self._local(self.CUR_OPEN)])
        utc_dates = [datetime.datetime.fromtimestamp(
            t, datetime.timezone.utc).strftime("%Y-%m-%d")
            for t in (self.PREV_OPEN, self.CUR_OPEN)]
        self.assertNotEqual([d for d, _ in pts], utc_dates)  # the bug being pinned

    def test_open_session_is_reported_as_provisional(self):
        _, prov = fm.parse_yahoo_chart(_yahoo(
            3600, [self.PREV_OPEN, self.CUR_OPEN], [155.66, 154.01],
            self.CUR_OPEN, self.CUR_CLOSE, self.CUR_OPEN + 50000))
        self.assertEqual(prov, self._local(self.CUR_OPEN))

    def test_closed_session_is_not_provisional(self):
        _, prov = fm.parse_yahoo_chart(_yahoo(
            3600, [self.PREV_OPEN, self.CUR_OPEN], [155.66, 154.01],
            self.CUR_OPEN, self.CUR_CLOSE, self.CUR_CLOSE))
        self.assertIsNone(prov)

    def test_null_closes_are_skipped(self):
        pts, _ = fm.parse_yahoo_chart(_yahoo(
            0, [self.PREV_OPEN, self.CUR_OPEN], [None, 154.01],
            self.CUR_OPEN, self.CUR_CLOSE, self.CUR_CLOSE))
        self.assertEqual(len(pts), 1)

    def test_empty_result_is_not_an_error(self):
        self.assertEqual(fm.parse_yahoo_chart('{"chart":{"result":[]}}'), ([], None))


class TestParseMofJgb(unittest.TestCase):
    CSV = ("国債金利情報\n"
           "基準日,1年,10年\n"
           "S49.9.24,10.327,8.5\n"     # Showa 49  -> 1974
           "H31.4.1,0.1,-0.05\n"       # Heisei 31 -> 2019
           "R8.9.1,1.527,2.9\n"        # Reiwa 8   -> 2026
           "※ダウンロードできない場合,,\n")

    def test_every_imperial_era_parses(self):
        self.assertEqual(fm.parse_mof_jgb(self.CSV, "10年"),
                         [("1974-09-24", 8.5), ("2019-04-01", -0.05), ("2026-09-01", 2.9)])

    def test_footer_rows_are_dropped_not_trusted(self):
        self.assertTrue(all(d[0].isdigit() for d, _ in fm.parse_mof_jgb(self.CSV, "1年")))

    def test_unknown_tenor_returns_empty(self):
        self.assertEqual(fm.parse_mof_jgb(self.CSV, "40年"), [])


class TestParseFredCsv(unittest.TestCase):
    def test_missing_values_are_gaps_not_zeros(self):
        csv_ = "observation_date,DGS10\n2026-09-02,4.79\n2026-09-03,.\n2026-09-04,4.77\n"
        self.assertEqual(fm.parse_fred_csv(csv_),
                         [("2026-09-02", 4.79), ("2026-09-04", 4.77)])

    def test_empty_input(self):
        self.assertEqual(fm.parse_fred_csv(""), [])


class TestSnapshotCard(unittest.TestCase):
    def test_change_is_day_over_day_and_provenance_is_carried(self):
        card = fm.snapshot_card(
            "USDJPY", [("2026-09-07", 156.2), ("2026-09-08", 154.0)],
            {"source": "FRED DEXJPUS + Yahoo JPY=X", "provisional": "2026-09-08"})
        self.assertEqual(card["change"], -2.2)
        self.assertEqual(card["as_of"], "2026-09-08")
        self.assertEqual(card["source"], "FRED DEXJPUS + Yahoo JPY=X")
        self.assertTrue(card["provisional"])

    def test_settled_card_carries_no_live_flag(self):
        card = fm.snapshot_card("DGS10", [("2026-09-02", 4.79), ("2026-09-03", 4.77)],
                                {"source": "FRED DGS10"})
        self.assertNotIn("provisional", card)

    def test_stale_provisional_date_does_not_flag_a_newer_point(self):
        """provisional only applies to the point it was observed on."""
        card = fm.snapshot_card("GOLD", [("2026-09-07", 1.0), ("2026-09-08", 2.0)],
                                {"source": "Yahoo GC=F", "provisional": "2026-09-07"})
        self.assertNotIn("provisional", card)


class TestBuildPayload(unittest.TestCase):
    RAW = {"DFII10": [("2026-08-31", 2.4), ("2026-09-08", 2.42)],
           "GOLD": [("2026-08-31", 4400.0), ("2026-09-08", 4443.3)],
           "UNRATE": [("2026-08-01", 4.1)]}

    def test_latest_observation_ignores_monthly_series(self):
        p = fm.build_payload(self.RAW, years=1)
        self.assertEqual(p["meta"]["latest_observation"], "2026-09-08")

    def test_absent_series_are_reported_not_guessed(self):
        p = fm.build_payload(self.RAW, years=1)
        self.assertIn("DGS10", p["meta"]["missing_series"])
        self.assertNotIn("GOLD", p["meta"]["missing_series"])

    def test_refuses_to_overwrite_with_nothing(self):
        with self.assertRaises(SystemExit):
            fm.build_payload({"DGS10": []}, years=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
