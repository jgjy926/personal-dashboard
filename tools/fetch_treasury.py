#!/usr/bin/env python3
"""
fetch_treasury.py — US Treasury supply calendar -> data/treasury.json

Two free, keyless US government feeds, both verified working:

  1. UPCOMING AUCTIONS (new issuance / reopenings) — TreasuryDirect's auction
     web service. Genuinely forward-looking: it lists auctions that have been
     announced but not yet held, with auction date, settlement/issue date,
     security type + term, and (for reopenings) the coupon.
         https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json

  2. RECENT BUYBACKS (Treasury repurchasing its own debt) — Fiscal Data's
     buybacks_operations dataset: operation date, settlement, operation type
     (Liquidity Support / Cash Management), maturity bucket, and par accepted.

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
BUYBACKS_URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
                "v1/accounting/od/buybacks_operations?page[size]=8&sort=-operation_date")
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


def parse_buybacks(payload: dict, limit: int = 6) -> list[dict]:
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    out = []
    for r in (payload or {}).get("data", [])[:limit]:
        out.append({
            "operation_date": r.get("operation_date") or "",
            "settlement_date": r.get("settlement_date") or "",
            "operation_type": r.get("operation_type") or "",
            "security_type": r.get("security_type") or "",
            "maturity_bucket": r.get("maturity_bucket") or "",
            "par_accepted": num(r.get("total_par_amt_accepted")),
        })
    return out


def build_payload(upcoming: list[dict], buybacks: list[dict]) -> dict:
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": {
                "auctions": "TreasuryDirect (upcoming auctions)",
                "buybacks": "Treasury Fiscal Data (buybacks_operations)",
            },
            "note": "Upcoming auctions are genuinely forward-looking (announced, not yet held). "
                    "Buybacks are COMPLETED operations only — Treasury publishes forward buyback "
                    "schedules solely inside quarterly-refunding PDFs, not as a structured feed, "
                    "so no 'next buyback date' is claimed here.",
        },
        "upcoming_auctions": upcoming,
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

    buybacks: list[dict] = []
    try:
        buybacks = parse_buybacks(_get_json(BUYBACKS_URL))
        print(f"[fetch_treasury] recent buybacks: {len(buybacks)}"
              + (f" (latest {buybacks[0]['operation_date']})" if buybacks else ""))
    except Exception as e:
        print(f"[fetch_treasury] buybacks FAILED: {e}", file=sys.stderr)

    if not upcoming and not buybacks:
        print("[fetch_treasury] both feeds failed — refusing to overwrite.", file=sys.stderr)
        return 1

    payload = build_payload(upcoming, buybacks)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[fetch_treasury] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
