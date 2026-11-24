#!/usr/bin/env python3
"""Unit tests for the pure half of tools/fetch_dalio.py — no network.

    python tools/test_fetch_dalio.py     (or: python -m unittest discover tools)

Covers the pieces whose mistakes would be silent on the page: the quadrant
assignment and its flat band, gap filling (October 2025 CPI was never
published), the walk-forward model falling back to persistence, the debt-stage
rules, the easing-cycle yardstick, and the engine comparison.
"""
from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_dalio as fd  # noqa: E402


def monthly_series(start: str, values: list[float]) -> dict[str, float]:
    return {fd.ym_shift(start, i): v for i, v in enumerate(values)}


class TestDates(unittest.TestCase):
    def test_ym_shift_crosses_years(self):
        self.assertEqual(fd.ym_shift("2026-01", -1), "2025-12")
        self.assertEqual(fd.ym_shift("2025-11", 14), "2027-01")

    def test_pct_change_and_diff(self):
        m = {"2025-01": 100.0, "2026-01": 103.0}
        self.assertAlmostEqual(fd.pct_change(m, "2026-01", 12), 3.0)
        self.assertAlmostEqual(fd.diff(m, "2026-01", 12), 3.0)
        self.assertIsNone(fd.pct_change(m, "2026-02", 12))

    def test_ordinal(self):
        self.assertEqual([fd.ordinal(x) for x in (0.91, 0.92, 0.93, 0.11, 0.77)],
                         ["91st", "92nd", "93rd", "11th", "77th"])


class TestFillGaps(unittest.TestCase):
    def test_fills_a_single_missing_month(self):
        m = monthly_series("2025-01", [float(i) for i in range(12)])
        del m["2025-10"]                          # the month BLS never published
        out = fd.fill_gaps(m)
        self.assertAlmostEqual(out["2025-10"], 9.0)
        self.assertEqual(len(out), 12)

    def test_leaves_long_holes_alone(self):
        m = monthly_series("2025-01", [1.0] * 6)
        m["2026-06"] = 2.0                      # 12-month hole is real absence, not a gap
        self.assertEqual(fd.fill_gaps(m), m)

    def test_quarterly_series_untouched(self):
        q = {"2025-01": 1.0, "2025-04": 2.0, "2025-07": 3.0, "2025-10": 4.0}
        self.assertEqual(fd.fill_gaps(q), q)

    def test_latest_yoy_skips_a_month_without_a_base(self):
        m = monthly_series("2024-09", [100 + i for i in range(26)])
        del m["2025-10"]                      # unpublished month: Oct 2026 has no YoY base
        ym, v = fd.latest_yoy(m)
        self.assertEqual(ym, "2026-09")
        self.assertAlmostEqual(v, (124 / 112 - 1) * 100)


class TestQuadrant(unittest.TestCase):
    def test_four_quadrants(self):
        self.assertEqual(fd.quadrant_of(0.5, -0.5), "goldilocks")
        self.assertEqual(fd.quadrant_of(0.5, 0.5), "reflation")
        self.assertEqual(fd.quadrant_of(-0.5, 0.5), "stagflation")
        self.assertEqual(fd.quadrant_of(-0.5, -0.5), "deflation")

    def test_flat_band(self):
        self.assertEqual(fd.direction_word(0.05, fd.FLAT_GROWTH), "flat")
        self.assertEqual(fd.direction_word(-0.3, fd.FLAT_GROWTH), "falling")
        self.assertEqual(fd.direction_word(0.3, fd.FLAT_INFLATION), "rising")

    def test_momentum_is_3m_avg_minus_3m_avg_k_ago(self):
        level = monthly_series("2020-01", [float(i) for i in range(24)])
        mom = fd.momentum(level, 6)
        self.assertAlmostEqual(mom["2021-12"], 6.0)    # a straight line rises 6 in 6 months
        self.assertNotIn("2020-05", mom)               # needs 8 months of history


class TestLogistic(unittest.TestCase):
    def test_recovers_a_clear_relationship(self):
        X = [[x / 10] for x in range(-50, 51)]
        y = [1 if x[0] > 0 else 0 for x in X]
        w = fd.fit_logistic(X, y)
        self.assertGreater(fd.predict(w, [2.0]), 0.9)
        self.assertLess(fd.predict(w, [-2.0]), 0.1)

    def test_solve(self):
        self.assertEqual([round(v, 6) for v in fd._solve([[2, 0], [0, 4]], [2, 8])], [1.0, 2.0])
        self.assertIsNone(fd._solve([[0, 0], [0, 0]], [1, 1]))


class TestDirectionModel(unittest.TestCase):
    def _trend_world(self):
        # A level that keeps trending up in 3-year swings: persistence is right
        # most of the time and a random 'lead' carries no information.
        n = 400
        level = monthly_series("1990-01", [math.sin(i / 18.0) * 3 for i in range(n)])
        lead = monthly_series("1990-01", [((i * 7919) % 13) / 6.0 - 1 for i in range(n)])
        return level, lead

    def test_falls_back_to_persistence_when_model_is_worse(self):
        level, lead = self._trend_world()
        out = fd.direction_model(level, lead, test_from="2005-01", flat=0.01)
        sk = out["skill"]
        self.assertGreater(sk["n"], 50)
        if sk["persistence_hit_rate"] > sk["hit_rate"]:
            self.assertEqual(out["method"], "persistence")
            self.assertIn(out["p_up"], (sk["persistence_hit_rate"], round(1 - sk["persistence_hit_rate"], 3), 0.5))
        else:
            self.assertEqual(out["method"], "model")

    def test_out_of_sample_history_has_known_outcomes_only(self):
        level, lead = self._trend_world()
        out = fd.direction_model(level, lead, test_from="2005-01")
        last = out["history"][-1]["date"]
        self.assertLessEqual(last, fd.ym_shift(max(level), -fd.HORIZON))


class TestPolicy(unittest.TestCase):
    def test_easing_cycles_measure_peak_to_trough(self):
        ff = monthly_series("2005-01", [5.0] * 36 + [3.0] * 6 + [0.25] * 30)
        rec = {k: 0 for k in ff}
        rec["2007-12"] = 0
        for k in fd.ym_range("2008-01", "2009-06"):
            rec[k] = 1
        cycles = fd.easing_cycles(ff, rec)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["recession_start"], "2008-01")
        self.assertAlmostEqual(cycles[0]["cut"], 4.75)

    def test_lever_rules(self):
        ff = monthly_series("1990-01", [6.0] * 24 + [1.0] * 24 + [4.0] * 12)
        rec = {k: 0 for k in ff}
        for k in fd.ym_range("1992-01", "1992-06"):
            rec[k] = 1
        M = {"FEDFUNDS": ff, "USREC": rec}
        out = fd.build_policy(M, core_pce_yoy=3.0)
        self.assertEqual(out["median_cut"], 5.0)
        self.assertEqual(out["lever"], "MP1 → MP2")          # 4.0 < 5.0
        self.assertAlmostEqual(out["real_rate"], 1.0)
        M["FEDFUNDS"] = dict(ff, **{max(ff): 0.25})
        self.assertEqual(fd.build_policy(M, 3.0)["lever"], "MP2/MP3")


def debt_world(fed_path, priv=140.0, rate_bn=1000.0, gdp_growth=0.06):
    """Quarterly toy economy: GDP compounding at `gdp_growth`, federal debt/GDP
    following `fed_path`, interest at `rate_bn` a year."""
    qs = [fd.ym_shift("2016-01", 3 * i) for i in range(len(fed_path))]
    gdp = {q: 20000 * (1 + gdp_growth) ** (i / 4) for i, q in enumerate(qs)}
    fed = dict(zip(qs, fed_path))
    return {
        "QUSPAM770A": {q: priv for q in qs}, "QUSHAM770A": {q: 70.0 for q in qs},
        "QUSNAM770A": {q: 70.0 for q in qs}, "GFDEGDQ188S": fed, "GDP": gdp,
        "GFDEBTN": {q: fed[q] / 100 * gdp[q] * 1000 for q in qs},
        "A091RC1Q027SBEA": {q: rate_bn for q in qs},
        "W018RC1Q027SBEA": {q: 5000.0 for q in qs},
        "CRDQUSAPABIS": {q: 30000 * (1.03) ** (i / 4) for i, q in enumerate(qs)},
        "FYFSGDA188S": {"2025-01": -5.8}, "FYOIGDA188S": {"2025-01": 3.9},
        "FDHBFIN": {q: 8000.0 for q in qs},
    }


class TestDebt(unittest.TestCase):
    POLICY = {"fed_funds": 3.9, "room_ratio": 0.7, "real_rate": 0.8}

    def test_deleveraging_via_nominal_growth(self):
        M = debt_world([130 - i for i in range(40)], rate_bn=400.0)   # cheap debt, falling ratio
        out = fd.build_debt(M, self.POLICY)
        self.assertEqual(out["long_term"]["stage"], "Deleveraging via nominal growth")
        self.assertLess(out["metrics"]["r_minus_g"], 0)

    def test_late_stage_when_high_and_expensive(self):
        path = [60 + i * 0.5 for i in range(40)]                       # rising to a record
        M = debt_world(path, rate_bn=3000.0, gdp_growth=0.02)          # r well above g
        out = fd.build_debt(M, self.POLICY)
        self.assertTrue(out["long_term"]["stage"].startswith("Late"), out["long_term"])
        self.assertGreater(out["metrics"]["r_minus_g"], 0)

    def test_pressure_is_mean_of_scored_components(self):
        out = fd.build_debt(debt_world([100.0] * 40), self.POLICY)
        comps = [c["score"] for c in out["long_term"]["components"] if c["score"] is not None]
        self.assertAlmostEqual(out["long_term"]["pressure"], round(sum(comps) / len(comps), 3))
        self.assertTrue(all(0 <= s <= 1 for s in comps))

    def test_deficit_warning_uses_dalio_marker(self):
        out = fd.build_debt(debt_world([100.0] * 40), self.POLICY)
        self.assertTrue(any("3% marker" in w for w in out["long_term"]["warnings"]))


class TestOutlookAndCompare(unittest.TestCase):
    QUAD = {"available": True,
            "current": {"key": "stagflation", **fd.QUADRANTS["stagflation"], "as_of": "2026-09",
                        "growth_momentum": -0.4, "inflation_momentum": 0.5, "growth_word": "falling",
                        "inflation_word": "rising", "borderline": False, "months_in_quadrant": 3},
            "forward": {"horizon_m": 6, "p_growth_up": 0.3, "p_inflation_up": 0.7,
                        "probs": {"goldilocks": 0.09, "reflation": 0.21, "stagflation": 0.49, "deflation": 0.21},
                        "most_likely": "stagflation", "growth_components": [], "inflation_components": []}}

    def test_assets_follow_the_quadrant_map(self):
        out = fd.build_outlook(self.QUAD, {"available": False}, {"available": False},
                               {"available": False}, {})
        by = {a["asset"]: a for a in out["assets"]}
        self.assertGreater(by["Gold"]["score"], by["US Equities"]["score"])
        self.assertTrue(out["clear_signal"])
        self.assertTrue(any("Stagflation" in r for r in out["risks"]))

    def test_no_clear_lead_when_odds_are_even(self):
        q = {**self.QUAD, "forward": {**self.QUAD["forward"],
             "probs": {"goldilocks": 0.25, "reflation": 0.25, "stagflation": 0.25, "deflation": 0.25}}}
        out = fd.build_outlook(q, {"available": False}, {"available": False}, {"available": False}, {})
        self.assertFalse(out["clear_signal"])
        self.assertIn("no clear lead", out["headline"])
        self.assertIn("near 25%", out["narrative"])

    def test_debt_overlay_is_zero_at_neutral_pressure(self):
        debt = {"available": True, "long_term": {"stage": "x", "pressure": 0.5},
                "short_term": {"stage": "Mid — steady"}, "metrics": {}}
        out = fd.build_outlook(self.QUAD, debt, {"available": False}, {"available": False}, {})
        self.assertTrue(all(a["overlay"] == 0 for a in out["assets"]))

    def test_engine_compare(self):
        engine = {"meta": {"as_of": "2026-11-06"},
                  "headline": {"regime": "SLOWDOWN", "regime_display": "Slowdown",
                               "composite_momentum": -0.2, "recession_12m": 0.13},
                  "regime": {"dimensions": {"growth": "SLUGGISH", "inflation": "STABLE"}},
                  "factors": [{"factor": "policy", "detail": {"stance": "NEUTRAL"}}]}
        debt = {"available": True, "short_term": {"stage": "Late — tightening"},
                "long_term": {"stage": "Late — debt-service squeeze", "pressure": 0.7}}
        policy = {"available": True, "lever": "MP1", "room_ratio": 1.2}
        out = fd.compare_engine(engine, self.QUAD, debt, policy)
        rows = {r["topic"]: r for r in out["rows"]}
        self.assertTrue(rows["Cycle position"]["agree"])         # slowdown ~ late
        self.assertTrue(rows["Growth direction"]["agree"])       # both falling
        self.assertFalse(rows["Inflation direction"]["agree"])   # stable vs rising
        self.assertIsNone(rows["Long-term debt cycle"]["agree"])
        self.assertFalse(fd.compare_engine(None, self.QUAD, debt, policy)["available"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
