#!/usr/bin/env python3
"""Unit tests for the pure half of tools/fetch_apims.py — no network.

    python -m unittest discover -s tools -p 'test_*.py'

The failure these guard against is a quiet one: APIMS spells "no reading" as
null, "", "NA", "N/A" or a negative number, and a lax parse turns any of those
into 0 — which the page would then show as a station with perfect air.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_apims as fa  # noqa: E402


class TestApiValue(unittest.TestCase):
    def test_every_spelling_of_missing(self):
        for empty in (None, "", "  ", "NA", "N/A", "n/a", "null", "-", -1, "-99", True, "abc"):
            self.assertIsNone(fa.api_value(empty), f"{empty!r} should read as no reading")

    def test_zero_is_a_reading(self):
        self.assertEqual(fa.api_value(0), 0)

    def test_numbers_and_numeric_strings(self):
        self.assertEqual(fa.api_value(47), 47)
        self.assertEqual(fa.api_value("151"), 151)
        self.assertEqual(fa.api_value(99.6), 100)


class TestCategory(unittest.TestCase):
    def test_band_edges_are_inclusive(self):
        cases = {0: "Good", 50: "Good", 51: "Moderate", 100: "Moderate",
                 101: "Unhealthy", 200: "Unhealthy", 201: "Very Unhealthy",
                 300: "Very Unhealthy", 301: "Hazardous", 500: "Hazardous"}
        for api, want in cases.items():
            self.assertEqual(fa.category(api), want, api)

    def test_missing_is_not_good(self):
        self.assertEqual(fa.category(None), "N/A")


class TestTime(unittest.TestCase):
    def test_naive_upstream_time_is_malaysia_time(self):
        self.assertEqual(fa.myt_iso("2026-10-11T12:00:00"), "2026-10-11T12:00:00+08:00")

    def test_garbage(self):
        self.assertIsNone(fa.myt_iso(""))
        self.assertIsNone(fa.myt_iso(None))
        self.assertIsNone(fa.myt_iso("not a date"))


MAP = {"data": [
    {"STATE_ID": 9, "STATE_NAME": "Perlis", "STATION_STATUS": 1, "STATION_ID": "CA01R",
     "STATION_LOCATION": "Kangar, PERLIS", "LONGITUDE": 100.21, "LATITUDE": 6.43,
     "API": 47, "PARAM_SYMBOL": "**", "CLASS": "Good", "RECORD_DT": "2026-10-11T11:00:00"},
    {"STATE_ID": 1, "STATE_NAME": "Johor", "STATION_STATUS": 1, "STATION_ID": "CA34J",
     "STATION_LOCATION": "Pasir Gudang, JOHOR", "LONGITUDE": 103.9, "LATITUDE": 1.47,
     "API": "NA", "PARAM_SYMBOL": "", "CLASS": "N/A", "RECORD_DT": "2026-10-11T11:00:00"},
]}
TABLE = {"api_table_hourly": [
    # Newest first, as upstream sends it.
    {"STATION_ID": "CA01R", "DATETIME": "2026-10-11T12:00:00", "API": 52, "PARAM_SYMBOL": "*"},
    {"STATION_ID": "CA01R", "DATETIME": "2026-10-11T11:00:00", "API": 47, "PARAM_SYMBOL": "**"},
    {"STATION_ID": "CA01R", "DATETIME": "2026-10-11T10:00:00", "API": -1, "PARAM_SYMBOL": "**"},
]}


class TestParseAndMerge(unittest.TestCase):
    def test_map(self):
        st = fa.parse_map(MAP)
        self.assertEqual(st["CA01R"]["location"], "Kangar")
        self.assertEqual(st["CA01R"]["pollutant"], "PM2.5")   # "**" is PM2.5, not PM10
        self.assertEqual(st["CA01R"]["at"], "2026-10-11T11:00:00+08:00")
        self.assertIsNone(st["CA34J"]["api"])
        self.assertIsNone(st["CA34J"]["pollutant"])
        self.assertEqual(st["CA34J"]["category"], "N/A")

    def test_shouted_names_are_title_cased(self):
        self.assertEqual(fa._title_location("JOHAN SETIA, Selangor"), "Johan Setia")
        self.assertEqual(fa._title_location("Seri Manjung, PERAK"), "Seri Manjung")

    def test_table_is_oldest_first_with_gaps_kept(self):
        h = fa.parse_table(TABLE)["CA01R"]
        self.assertEqual([x["api"] for x in h], [None, 47, 52])

    def test_newer_table_hour_wins_over_map(self):
        out = {s["id"]: s for s in fa.merge(fa.parse_map(MAP), fa.parse_table(TABLE))}
        s = out["CA01R"]
        self.assertEqual((s["api"], s["pollutant"], s["category"]), (52, "PM10", "Moderate"))
        self.assertEqual(s["at"], "2026-10-11T12:00:00+08:00")
        self.assertEqual(len(s["history"]), 3)

    def test_older_table_does_not_overwrite_map(self):
        stations = fa.parse_map(MAP)
        stations["CA01R"]["at"] = "2026-10-11T13:00:00+08:00"
        stations["CA01R"]["api"] = 60
        out = {s["id"]: s for s in fa.merge(stations, fa.parse_table(TABLE))}
        self.assertEqual(out["CA01R"]["api"], 60)

    def test_payload_counts_and_as_of(self):
        p = fa.build_payload(fa.merge(fa.parse_map(MAP), fa.parse_table(TABLE)), [])
        self.assertEqual(p["meta"]["station_count"], 2)
        self.assertEqual(p["meta"]["reporting"], 1)     # the "NA" station isn't reporting
        self.assertEqual(p["meta"]["readings_as_of"], "2026-10-11T12:00:00+08:00")
        self.assertEqual(p["meta"]["categories"], {"Moderate": 1, "N/A": 1})


if __name__ == "__main__":
    unittest.main()
