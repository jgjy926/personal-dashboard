#!/usr/bin/env python3
"""Unit tests for the pure half of tools/fetch_treasury.py — no network.

    python -m unittest discover -s tools -p 'test_*.py'

Both upstream feeds spell "not known yet" in ways that read as real data if you
are not careful, and both would then publish a confident wrong number:

  * TreasuryDirect lists an auction the moment it is HELD, hours before the
    results land — so a row with no stop rate is in progress, not a zero.
  * Fiscal Data lists a buyback the moment it is ANNOUNCED, with result fields
    holding the literal four-character string "null" — which float() rejects but
    a laxer parse would happily turn into 0.0, and which the page would show
    under a heading promising completed operations.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_treasury as ft  # noqa: E402


class TestNum(unittest.TestCase):
    def test_the_three_spellings_of_missing(self):
        for empty in (None, "", "   ", "null", "NULL"):
            self.assertIsNone(ft._num(empty), f"{empty!r} should read as missing")

    def test_zero_is_a_real_value_not_missing(self):
        """somaAccepted is legitimately 0 on most auctions."""
        self.assertEqual(ft._num("0"), 0.0)

    def test_numeric_strings_parse(self):
        self.assertEqual(ft._num("4.4740"), 4.474)
        self.assertEqual(ft._num("157872425100"), 157872425100.0)

    def test_junk_is_missing_not_an_exception(self):
        self.assertIsNone(ft._num("at auction"))


def _auction(**over):
    row = {"auctionDate": "2026-09-08T00:00:00", "issueDate": "2026-09-15T00:00:00",
           "securityType": "Note", "securityTerm": "3-Year", "cusip": "91282CRL7",
           "highYield": "4.4740", "highDiscountRate": "", "highInvestmentRate": "",
           "interestRate": "4.375000", "averageMedianYield": "4.430000",
           "bidToCoverRatio": "2.720000", "allocationPercentage": "29.350000",
           "totalTendered": "157872425100", "totalAccepted": "58000088800",
           "indirectBidderAccepted": "35809000000", "directBidderAccepted": "15525163700",
           "primaryDealerAccepted": "6285000000"}
    row.update(over)
    return row


class TestParseResults(unittest.TestCase):
    def test_a_held_auction_carries_its_clearing_numbers(self):
        r = ft.parse_results([_auction()])[0]
        self.assertEqual(r["stop_rate"], 4.474)
        self.assertEqual(r["allotted_at_high"], 29.35)
        self.assertEqual(r["bid_to_cover"], 2.72)
        self.assertEqual(r["coupon"], 4.375)
        self.assertEqual(r["auction_date"], "2026-09-08")   # timestamp trimmed

    def test_auction_in_progress_is_excluded_not_zeroed(self):
        """Held but not yet reported: no stop rate. Publishing it as 0% would be
        a confident lie; leaving it out is the honest state."""
        pending = _auction(highYield="", highDiscountRate="", allocationPercentage="")
        self.assertEqual(ft.parse_results([pending]), [])

    def test_bill_keeps_both_discount_and_investment_rate(self):
        bill = _auction(securityType="Bill", securityTerm="26-Week", highYield="",
                        highDiscountRate="3.890000", highInvestmentRate="4.023000",
                        interestRate="")
        r = ft.parse_results([bill])[0]
        self.assertEqual(r["stop_rate"], 3.89)
        self.assertEqual(r["investment_rate"], 4.023)
        self.assertIsNone(r["coupon"])

    def test_zero_stop_rate_is_kept(self):
        """A 0.000% bill auction has happened (2020, 2015). It must not be
        mistaken for a missing result by a truthiness check."""
        self.assertEqual(len(ft.parse_results([_auction(highYield="0.000")])), 1)

    def test_newest_first_and_limited(self):
        rows = [_auction(auctionDate=f"2026-09-{d:02d}T00:00:00") for d in (1, 8, 3)]
        got = ft.parse_results(rows, limit=2)
        self.assertEqual([r["auction_date"] for r in got], ["2026-09-08", "2026-09-03"])

    def test_empty_input(self):
        self.assertEqual(ft.parse_results([]), [])
        self.assertEqual(ft.parse_results(None), [])


def _buyback(**over):
    row = {"operation_date": "2026-09-03", "settlement_date": "2026-09-04",
           "operation_type": "Cash Management", "security_type": "Nominal Coupons",
           "maturity_bucket": "1Mo to 2Y", "total_par_amt_offered": "28272000000.00",
           "total_par_amt_accepted": "12500000000.00",
           "max_par_amt_redeemed": "12500000000"}
    row.update(over)
    return row


class TestParseBuybacks(unittest.TestCase):
    def test_completed_operation_carries_offered_accepted_and_cap(self):
        b = ft.parse_buybacks({"data": [_buyback()]})[0]
        self.assertEqual(b["par_offered"], 28272000000.0)
        self.assertEqual(b["par_accepted"], 12500000000.0)
        self.assertEqual(b["par_cap"], 12500000000.0)

    def test_announced_but_unfinished_operation_is_filtered_out(self):
        """The live bug this fixes: today's operation appeared under a heading
        reading 'completed operations' with a blank amount."""
        pending = _buyback(operation_date="2026-09-09", total_par_amt_offered="null",
                           total_par_amt_accepted="null")
        got = ft.parse_buybacks({"data": [pending, _buyback()]})
        self.assertEqual([b["operation_date"] for b in got], ["2026-09-03"])

    def test_limit_counts_completed_rows_only(self):
        """A page of pending operations must not eat the limit and leave the
        table empty."""
        pending = [_buyback(operation_date="2026-09-09", total_par_amt_accepted="null")] * 5
        done = [_buyback(operation_date=f"2026-08-{d:02d}") for d in (25, 20, 18)]
        got = ft.parse_buybacks({"data": pending + done}, limit=2)
        self.assertEqual(len(got), 2)
        self.assertTrue(all(b["par_accepted"] is not None for b in got))

    def test_missing_offered_does_not_drop_the_row(self):
        b = ft.parse_buybacks({"data": [_buyback(total_par_amt_offered="null")]})[0]
        self.assertIsNone(b["par_offered"])
        self.assertEqual(b["par_accepted"], 12500000000.0)

    def test_empty_payload(self):
        self.assertEqual(ft.parse_buybacks({}), [])
        self.assertEqual(ft.parse_buybacks(None), [])


class TestBuildPayload(unittest.TestCase):
    def test_all_three_blocks_are_present_and_named(self):
        p = ft.build_payload([], ft.parse_results([_auction()]),
                             ft.parse_buybacks({"data": [_buyback()]}))
        self.assertEqual(len(p["recent_results"]), 1)
        self.assertEqual(len(p["recent_buybacks"]), 1)
        self.assertIn("results", p["meta"]["sources"])
        self.assertIn("stop-out", p["meta"]["results_note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
