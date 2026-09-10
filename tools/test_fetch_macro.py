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
  * Treasury's own curve CSV read as if it shared FRED's conventions — it is
    newest-first, dated MM/DD/YYYY, and spells the tenor "10 Yr" on one file and
    "10 YR" on the other, any of which silently yields a stale or empty series.
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


class TestParseTreasuryCurve(unittest.TestCase):
    """Treasury's daily curve is the same data FRED redistributes as DGS*/DFII*,
    but published same-day — it is what unsticks the four yield cards. Its CSV
    conventions are the opposite of FRED's in three separate ways, so each one
    gets a test rather than a comment."""

    NOMINAL = ('Date,"1 Mo","3 Mo","1 Yr","10 Yr","20 Yr","30 Yr"\n'
               '09/09/2026,3.81,3.95,4.17,4.83,5.28,5.28\n'
               '09/08/2026,3.80,3.94,4.15,4.80,5.25,5.25\n'
               '09/04/2026,3.79,3.92,4.14,4.76,5.21,5.20\n')
    REAL = ('Date,"5 YR","10 YR","30 YR"\n'
            '09/09/2026,2.20,2.46,2.98\n'
            '09/08/2026,2.18,2.43,2.96\n')

    def test_rows_come_back_ascending_not_newest_first(self):
        # splice() treats the LAST point as the cutoff, so a newest-first read
        # would take 09/04 as the newest and append nothing at all.
        got = fm.parse_treasury_curve(self.NOMINAL, "10 Yr")
        self.assertEqual([d for d, _ in got],
                         ["2026-09-04", "2026-09-08", "2026-09-09"])
        self.assertEqual(got[-1], ("2026-09-09", 4.83))

    def test_tenor_match_is_case_insensitive_across_the_two_files(self):
        # The nominal file says "10 Yr", the real file "10 YR". One SERIES table
        # feeds both; an exact match would return [] for whichever it got wrong
        # — and an empty extra is a silent no-op inside splice().
        self.assertTrue(fm.parse_treasury_curve(self.REAL, "10 YR"))
        self.assertEqual(fm.parse_treasury_curve(self.REAL, "10 yr"),
                         fm.parse_treasury_curve(self.REAL, "10 YR"))
        self.assertEqual(fm.parse_treasury_curve(self.NOMINAL, "30 YR")[-1],
                         ("2026-09-09", 5.28))

    def test_us_dates_are_month_first(self):
        # 09/04/2026 is 4 September, not 9 April. Read the other way round every
        # September row lands five months early and sorts to the front.
        self.assertEqual(fm.parse_treasury_curve(
            'Date,"10 Yr"\n12/01/2026,4.10\n', "10 Yr"), [("2026-12-01", 4.10)])

    def test_blank_cells_are_gaps_not_zeros(self):
        # The 30Y was not issued 2002-2006; those rows carry an empty cell.
        got = fm.parse_treasury_curve(
            'Date,"10 Yr","30 Yr"\n09/09/2026,4.83,\n09/08/2026,4.80,5.25\n', "30 Yr")
        self.assertEqual(got, [("2026-09-08", 5.25)])

    def test_unknown_column_and_empty_input_are_not_errors(self):
        # A renamed header must degrade to "no extension", leaving FRED's series
        # intact, rather than raise and take the whole run down.
        self.assertEqual(fm.parse_treasury_curve(self.NOMINAL, "7 Yr"), [])
        self.assertEqual(fm.parse_treasury_curve("", "10 Yr"), [])

    def test_treasury_extends_fred_without_rewriting_it(self):
        # The end-to-end shape of the fix: FRED stops at 09-04, Treasury already
        # has 09-08 and 09-09, and FRED's settled 09-04 value is not overwritten.
        fred = [("2026-09-03", 4.70), ("2026-09-04", 4.76)]
        merged, added = fm.splice(fred, fm.parse_treasury_curve(self.NOMINAL, "10 Yr"))
        self.assertEqual(added, 2)
        self.assertEqual(merged[1], ("2026-09-04", 4.76))
        self.assertEqual(merged[-1], ("2026-09-09", 4.83))


class TestReleaseCadence(unittest.TestCase):
    """A daily series can be published weekly (the Fed's H.10) or report the
    month before last (Case-Shiller). Those cards were amber for running exactly
    on time, so the cadence and its own stale threshold ride on the card."""

    def test_cadence_rides_on_the_card_when_declared(self):
        card = fm.snapshot_card("DTWEXBGS", [("2026-08-28", 117.9), ("2026-09-04", 118.07)])
        self.assertEqual(card["stale_after"], fm.SERIES["DTWEXBGS"]["stale_after"])
        self.assertEqual(card["release"], "Fed H.10, weekly (Mon)")

    def test_ordinary_series_declare_nothing_and_keep_the_blanket_rule(self):
        card = fm.snapshot_card("DGS10", [("2026-09-08", 4.80), ("2026-09-09", 4.83)])
        self.assertNotIn("stale_after", card)
        self.assertNotIn("release", card)

    def test_dxy_is_a_separate_card_never_spliced_onto_the_broad_index(self):
        # ICE's six-currency index and the Fed's 26-currency one are different
        # numbers on different bases; welding one onto the other would publish a
        # level neither the Fed nor ICE ever printed.
        self.assertNotIn("yahoo", fm.SERIES["DTWEXBGS"])
        self.assertEqual(fm.SERIES["DXY"]["yahoo"], "DX-Y.NYB")
        self.assertEqual(fm.SERIES["DXY"]["ids"], [])


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
