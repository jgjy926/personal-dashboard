#!/usr/bin/env python3
"""
fetch_treasury.py — US Treasury supply calendar -> data/treasury.json

Two free, keyless US government feeds, both verified working:

  1. UPCOMING AUCTIONS (new issuance / reopenings) — TreasuryDirect's auction
     web service. Genuinely forward-looking: it lists auctions that have been
     announced but not yet held, with auction date, settlement/issue date,
     security type + term, and (for reopenings) the coupon.
         https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json

  2. AUCTION RESULTS — the same service's `auctioned` path, which is where the
     announcements above end up once the auction has actually been held. This
     answers the question the upcoming table structurally cannot: at what rate
     did it clear, and how much of the bidding got filled.
         https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=N

     Two DIFFERENT percentages come out of it and they are easy to conflate:

       * allotted_at_high (`allocationPercentage`) — of the bids placed AT the
         stop-out rate, the share that got filled. Every winner pays the same
         stop-out price, so this measures how contested that final rung was. It
         swings widely (20%-90% in a normal week) and is the informative one.
       * accepted/tendered — the share of all money bid that got securities.
         This barely moves (~35%), because Treasury fixes the size in advance
         and bidders reliably over-bid about 3x. Shown for completeness, but it
         is close to a constant and should not be read as demand.

     NOT available here: the tail (stop-out minus the when-issued yield at bid
     deadline), the standard cheap/rich measure. When-issued yields are not
     published in this feed, so it is left out rather than approximated.

  3. RECENT BUYBACKS (Treasury repurchasing its own debt) — Fiscal Data's
     buybacks_operations dataset: operation date, settlement, operation type
     (Liquidity Support / Cash Management), maturity bucket, par offered, par
     accepted, and the cap on the operation. The cap matters: a 10% hit rate
     usually means the operation filled its cap, not that offers were refused.

Important honesty note on buybacks: that dataset records COMPLETED operations
only — it is not a forward schedule. Treasury does publish buyback calendars
in advance, but only inside quarterly-refunding PDFs, which is not a
structured feed. So the dashboard shows "most recent operations", never a
"next buyback date" it cannot actually know. Upcoming *auctions*, by contrast,
really are forward-looking, and that's what the panel leads with.

Japan: MoF's JGB auction calendar exists only as per-month HTML sub-pages with
no CSV/Excel/PDF data files (checked), so there's no clean structured feed to
mirror this for Japan. Deliberately not scraped rather than half-built.

Usage:
    python tools/fetch_treasury.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "treasury.json")

UPCOMING_URL = "https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json"
# 21 days keeps a full cycle of every tenor in view (the 52-week bill and the
# 20/30-year bonds are monthly), so the results table stays populated across
# holiday weeks instead of emptying out.
RESULTS_URL = ("https://www.treasurydirect.gov/TA_WS/securities/auctioned"
               "?format=json&days=21")
BUYBACKS_URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
                "v1/accounting/od/buybacks_operations?page[size]=12&sort=-operation_date")
UA = {"User-Agent": "Mozilla/5.0 (macro-dashboard fetcher)", "Accept": "application/json"}


# ── network (isolated so parsing stays pure/testable) ───────────────────────
def _get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ── pure helpers ────────────────────────────────────────────────────────────
def _d(value: str) -> str:
    """TreasuryDirect dates look like '2026-09-10T00:00:00' -> '2026-09-10'."""
    return (value or "")[:10]


def parse_upcoming(rows: list, today: str, limit: int = 8) -> list[dict]:
    """Forward-dated auctions only, soonest first. `rate` is the coupon on a
    reopening (blank for bills/new issues priced at auction)."""
    out = []
    for r in rows or []:
        auction = _d(r.get("auctionDate"))
        if not auction or auction < today:
            continue
        out.append({
            "auction_date": auction,
            "issue_date": _d(r.get("issueDate")),
            "maturity_date": _d(r.get("maturityDate")),
            "security_type": r.get("securityType") or "",
            "term": r.get("securityTerm") or "",
            "rate": (r.get("interestRate") or "").strip(),
            "cusip": r.get("cusip") or "",
        })
    out.sort(key=lambda x: (x["auction_date"], x["term"]))
    return out[:limit]


def _num(v) -> float | None:
    """Both feeds spell "no value" several ways: absent, "", and the literal
    four-character string "null" (Fiscal Data serialises it that way rather than
    as JSON null). All three must read as missing, never as 0.0."""
    if v is None:
        return None
    t = str(v).strip()
    if t == "" or t.lower() == "null":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _first_num(*values) -> float | None:
    """First value that parses as a number. NOT `a or b`: a 0.000% stop-out is a
    real auction result (bills have cleared at zero — 2011, 2015, 2020) and is
    falsy, so `or` would skip past it and drop the auction as unreported."""
    for v in values:
        n = _num(v)
        if n is not None:
            return n
    return None


def parse_results(rows: list, limit: int = 8) -> list[dict]:
    """Held auctions, most recent first, with what they cleared at.

    A row is only included once it HAS a result: TreasuryDirect lists an auction
    here from the moment it is held, but the numbers land a couple of hours
    later, so a row without a stop rate is an auction still in progress, not a
    zero.

    Bills and coupons quote differently and both are kept rather than flattened:
    a bill's headline is a discount rate, whose comparable annualised figure is
    the separate `highInvestmentRate` (3.890% vs 4.023% on a 26-week is the same
    auction, not a discrepancy); notes and bonds quote `highYield` directly."""
    out = []
    for r in rows or []:
        stop = _first_num(r.get("highYield"), r.get("highDiscountRate"))
        if stop is None:
            continue
        tendered, accepted = _num(r.get("totalTendered")), _num(r.get("totalAccepted"))
        out.append({
            "auction_date": _d(r.get("auctionDate")),
            "issue_date": _d(r.get("issueDate")),
            "security_type": r.get("securityType") or "",
            "term": r.get("securityTerm") or "",
            "cusip": r.get("cusip") or "",
            # What it cleared at. `investment_rate` is populated for bills only.
            "stop_rate": stop,
            "investment_rate": _num(r.get("highInvestmentRate")),
            "coupon": _num(r.get("interestRate")),
            "median_rate": _first_num(r.get("averageMedianYield"),
                                      r.get("averageMedianDiscountRate")),
            # How contested it was.
            "bid_to_cover": _num(r.get("bidToCoverRatio")),
            "allotted_at_high": _num(r.get("allocationPercentage")),
            "tendered": tendered,
            "accepted": accepted,
            # Who took it down. Indirect is broadly foreign/central-bank demand;
            # a dealer share that swells means the auction had to be absorbed.
            "indirect_accepted": _num(r.get("indirectBidderAccepted")),
            "direct_accepted": _num(r.get("directBidderAccepted")),
            "dealer_accepted": _num(r.get("primaryDealerAccepted")),
        })
    out.sort(key=lambda x: (x["auction_date"], x["term"]), reverse=True)
    return out[:limit]


def parse_buybacks(payload: dict, limit: int = 6) -> list[dict]:
    """Completed operations only — see the note in build_payload. An operation
    appears in this dataset as soon as it is ANNOUNCED, with its result fields
    still empty, so filtering on a present `total_par_amt_accepted` is what makes
    the "completed" claim true. Without it, the operation running this afternoon
    shows up in the table with a blank amount as though it had settled."""
    out = []
    for r in (payload or {}).get("data", []):
        accepted = _num(r.get("total_par_amt_accepted"))
        if accepted is None:
            continue
        offered = _num(r.get("total_par_amt_offered"))
        out.append({
            "operation_date": r.get("operation_date") or "",
            "settlement_date": r.get("settlement_date") or "",
            "operation_type": r.get("operation_type") or "",
            "security_type": r.get("security_type") or "",
            "maturity_bucket": r.get("maturity_bucket") or "",
            "par_offered": offered,
            "par_accepted": accepted,
            # The size limit Treasury set. A low hit rate against a filled cap is
            # a capped operation, not weak interest — without this the two are
            # indistinguishable on the page.
            "par_cap": _num(r.get("max_par_amt_redeemed")),
        })
        if len(out) >= limit:
            break
    return out


def build_payload(upcoming: list[dict], results: list[dict],
                  buybacks: list[dict]) -> dict:
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": {
                "auctions": "TreasuryDirect (upcoming auctions)",
                "results": "TreasuryDirect (auction results)",
                "buybacks": "Treasury Fiscal Data (buybacks_operations)",
            },
            "note": "Upcoming auctions are genuinely forward-looking (announced, not yet held). "
                    "Results are auctions already HELD and settled at a known rate. "
                    "Buybacks are COMPLETED operations only — Treasury publishes forward buyback "
                    "schedules solely inside quarterly-refunding PDFs, not as a structured feed, "
                    "so no 'next buyback date' is claimed here.",
            "results_note": "Every winning bidder pays the same stop-out rate. "
                            "'Allotted at high' is the share of bids AT that rate which were "
                            "filled — the informative number, since accepted/tendered sits near "
                            "35% by construction (Treasury fixes the size; bidders over-bid ~3x). "
                            "The tail (stop-out vs when-issued) is not published in this feed and "
                            "is deliberately not estimated.",
        },
        "upcoming_auctions": upcoming,
        "recent_results": results,
        "recent_buybacks": buybacks,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    today = date.today().isoformat()

    upcoming: list[dict] = []
    try:
        upcoming = parse_upcoming(_get_json(UPCOMING_URL), today)
        print(f"[fetch_treasury] upcoming auctions: {len(upcoming)}"
              + (f" (next {upcoming[0]['auction_date']})" if upcoming else ""))
    except Exception as e:
        print(f"[fetch_treasury] upcoming auctions FAILED: {e}", file=sys.stderr)

    results: list[dict] = []
    try:
        results = parse_results(_get_json(RESULTS_URL))
        print(f"[fetch_treasury] auction results: {len(results)}"
              + (f" (latest {results[0]['auction_date']} "
                 f"{results[0]['term']} @ {results[0]['stop_rate']}%)" if results else ""))
    except Exception as e:
        print(f"[fetch_treasury] auction results FAILED: {e}", file=sys.stderr)

    buybacks: list[dict] = []
    try:
        buybacks = parse_buybacks(_get_json(BUYBACKS_URL))
        print(f"[fetch_treasury] recent buybacks: {len(buybacks)}"
              + (f" (latest {buybacks[0]['operation_date']})" if buybacks else ""))
    except Exception as e:
        print(f"[fetch_treasury] buybacks FAILED: {e}", file=sys.stderr)

    if not upcoming and not results and not buybacks:
        print("[fetch_treasury] all feeds failed — refusing to overwrite.", file=sys.stderr)
        return 1

    payload = build_payload(upcoming, results, buybacks)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[fetch_treasury] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
