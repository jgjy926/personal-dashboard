#!/usr/bin/env python3
"""Unit tests for the pure half of tools/fetch_aq_forecast.py — no network,
no Pillow (a frame is faked as a dict of pixels).

    python -m unittest discover -s tools -p 'test_*.py'

What they guard: the colour bar decoding (a box read one off shifts a whole
band), the black-overlay rule (a wind barb across a station must not read as
'Extreme'), and the day aggregation the tab shows.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_aq_forecast as fq  # noqa: E402


class Frame(dict):
    """px[x, y] over a uniform field, with optional painted pixels."""
    def __init__(self, fill):
        super().__init__()
        self.fill = fill

    def __missing__(self, key):
        return self.fill


def plot(fill=(250, 255, 250)):
    px = Frame(fill)
    for i, c in enumerate(fq.PALETTE):
        px[fq.BAR_X0 + 17 + fq.BAR_W * i, fq.BAR_Y] = c
    x0, y0, x1, y1 = fq.FRAME
    for x, y in ((x0, (y0 + y1) // 2), (x1, (y0 + y1) // 2), ((x0 + x1) // 2, y0), ((x0 + x1) // 2, y1)):
        px[x, y] = (0, 0, 0)
    return px


class TestColourBar(unittest.TestCase):
    def test_labelled_edges_are_exact(self):
        e = fq.box_edges(fq.POLLUTANTS["PM2.5"]["labels"])
        self.assertEqual(len(e), 23)
        self.assertEqual([e[k] for k in fq.LABEL_EDGES], [0, 35, 55, 150, 200, 250, 375, 500])

    def test_box_values(self):
        e = fq.box_edges(fq.POLLUTANTS["PM2.5"]["labels"])
        self.assertEqual(fq.box_value(0, e), 0)                 # below the first level
        self.assertAlmostEqual(fq.box_value(1, e), 4.375)       # 0 .. 8.75
        self.assertAlmostEqual(fq.box_value(5, e), (35 + 35 + 20 / 3) / 2)
        self.assertEqual(fq.box_value(23, e), 500)              # open-ended top box: its floor

    def test_api_on_the_legend_breakpoints(self):
        k = fq.POLLUTANTS["PM2.5"]["api"]
        self.assertEqual(fq.to_api(35, k), 100)
        self.assertEqual(fq.to_api(150, k), 200)
        self.assertEqual(fq.to_api(900, k), 500)
        self.assertEqual(fq.category(fq.to_api(36, fq.POLLUTANTS["PM2.5"]["api"])), "Unhealthy")
        self.assertEqual(fq.category(fq.to_api(351, fq.POLLUTANTS["PM10"]["api"])), "Very Unhealthy")


class TestGeometry(unittest.TestCase):
    def test_axis_ticks(self):
        self.assertEqual(fq.lonlat_to_px(105, 4), (365, 452))
        self.assertEqual(fq.lonlat_to_px(115, -2), (783, 703))

    def test_outside_the_map(self):
        self.assertIsNone(fq.lonlat_to_px(125, 5))
        self.assertIsNone(fq.lonlat_to_px(101, 12))


class TestSampling(unittest.TestCase):
    def test_reads_the_field(self):
        self.assertEqual(fq.sample_box(plot((255, 255, 0)), 400, 400), 7)

    def test_a_barb_is_not_extreme(self):
        px = plot((60, 200, 60))
        for d in range(-4, 5):
            px[400 + d, 400] = (0, 0, 0)
        self.assertEqual(fq.sample_box(px, 400, 400), 3)

    def test_solid_black_is_extreme(self):
        self.assertEqual(fq.sample_box(plot((0, 0, 0)), 400, 400), fq.BLACK)

    def test_unknown_colours_read_as_nothing(self):
        self.assertIsNone(fq.sample_box(plot((1, 2, 3)), 400, 400))


class TestLayout(unittest.TestCase):
    def test_expected_plot(self):
        self.assertIsNone(fq.check_layout(plot(), (1024, 1024)))

    def test_changed_plot_is_refused(self):
        px = plot()
        px[fq.BAR_X0 + 17, fq.BAR_Y] = (1, 1, 1)
        self.assertIn("colour bar", fq.check_layout(px, (1024, 1024)))
        self.assertIn("1024", fq.check_layout(plot(), (800, 600)))


class TestRunAndFrames(unittest.TestCase):
    def test_latest_run_is_the_first_link(self):
        html = '<a href="./display_20261023_12.html">Latest</a><a href="./display_20261023_00.html">'
        self.assertEqual(fq.latest_run(html), "20261023_12")
        self.assertIsNone(fq.latest_run("<html></html>"))

    def test_frames_filter_domain_variable_and_step(self):
        files = ["PM25_TOT_d02_20261024_08LST.png", "PM25_TOT_d02_20261024_09LST.png",
                 "PM25_TOT_d01_20261024_10LST.png", "PM10_d02_20261024_10LST.png",
                 "PM25_TOT_d02_20261024_10LST.png"]
        got = fq.frames({"files": files}, "PM25_TOT", 2)
        self.assertEqual([f for f, _ in got], ["PM25_TOT_d02_20261024_08LST.png", "PM25_TOT_d02_20261024_10LST.png"])
        self.assertEqual(got[0][1].isoformat(), "2026-10-24T08:00:00+08:00")


class TestDaily(unittest.TestCase):
    def t(self, d, h):
        return datetime(2026, 10, d, h, tzinfo=fq.MYT)

    def test_mean_for_pm_peak_for_o3_worst_wins(self):
        s = {"PM2.5": [(self.t(25, h), 10 if h < 12 else 50) for h in range(0, 24, 2)],
             "O3": [(self.t(25, h), 40 if h != 14 else 90) for h in range(0, 24, 2)]}
        (day,) = fq.daily(s, 2)
        self.assertEqual(day["pm25"], 30)                 # mean
        self.assertEqual(day["o3"], 90)                   # peak
        self.assertEqual(day["pollutant"], "O3")          # 90 ppb -> API 136 beats PM2.5 30 -> 86
        self.assertEqual(day["category"], "Unhealthy")
        self.assertEqual(day["hours"], 24)

    def test_short_days_are_dropped(self):
        s = {"PM2.5": [(self.t(24, h), 10) for h in (0, 2, 4)] + [(self.t(25, h), 10) for h in range(0, 24, 2)]}
        self.assertEqual([d["date"] for d in fq.daily(s, 2)], ["2026-10-25"])


if __name__ == "__main__":
    unittest.main()
