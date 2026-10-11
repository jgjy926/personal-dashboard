#!/usr/bin/env python3
"""
promo_feed.py — what every card-promo scraper and tool shares.

The Card Promos tab reads one feed per SOURCE, not one big file:

    data/promos/pbb.json         Public Bank card campaigns      (scrape_campaign.py)
    data/promos/hlb.json         Hong Leong Bank "Cards" promos  (scrape_hlb.py)
    data/promos/uob.json         UOB featured card campaigns     (scrape_uob.py)
    data/promos/uob-deals.json   UOB card merchant deals         (scrape_uob.py)

Each scraper writes only its own `<feed>.draft.json` (git-ignored), and
merge_campaign.py folds every draft into its live file. So one bank changing its
page, or being down, can't touch another bank's promos or summaries, and the
high-churn merchant deals don't bury the campaign changes in the git history.

Every feed has the same shape:
    {"meta": {"bank", "bank_name", "kind", "source", "today", "count", ...},
     "promotions": [{"id", "bank", "title", "link", "image", "category",
                     "period", "end_date", "first_seen", "tnc_link",
                     "tnc_summary", ...}]}

kind is "campaign" (a bank-run campaign; tnc_summary is written from the poster
and T&C by summarize_campaign.py or the Console) or "deal" (a merchant offer
whose text the bank publishes on the page itself; summary_source "bank").

No third-party imports here: the unit tests run without the scraper deps.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROMOS_DIR = os.path.join(ROOT, "data", "promos")

BANKS = {"pbb": "Public Bank", "hlb": "Hong Leong Bank", "uob": "UOB"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}

CATEGORY_HINTS = [
    ("Dining", ["dining", "restaurant", "food", "cafe", "hotpot"]),
    ("Travel", ["travel", "flight", "airline", "lounge", "hotel", "destination", "traveloka", "overseas"]),
    ("Fuel", ["fuel", "petrol", "petron", "shell", "petronas"]),
    ("Cashback", ["cashback", "cash back", "rebate"]),
    ("Instalment", ["instalment", "installment", "0%", "flexipay", "extended payment"]),
    ("Online", ["online", "e-commerce", "lazada", "shopee", "contactless", "qr"]),
]


def promo_id(key: str) -> str:
    """Stable 10-hex id. Public Bank's ids were always sha1(link)[:10], so the
    summaries already written against them keep matching."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def guess_category(text: str, default: str) -> str:
    low = text.lower()
    for cat, keys in CATEGORY_HINTS:
        if any(k in low for k in keys):
            return cat
    return default


def feed_path(name: str) -> str:
    return os.path.join(PROMOS_DIR, f"{name}.json")


def draft_path(name: str) -> str:
    return os.path.join(PROMOS_DIR, f"{name}.draft.json")


def live_feeds() -> list[str]:
    """Every published feed file (drafts excluded), in a stable order."""
    return sorted(p for p in glob.glob(os.path.join(PROMOS_DIR, "*.json"))
                  if not p.endswith(".draft.json"))


def load(path: str):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path: str, doc) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")


def prior_promos(name: str) -> dict[str, dict]:
    """id -> promo from the live feed, for first_seen and 'already summarised'."""
    doc = load(feed_path(name)) or {}
    return {p["id"]: p for p in doc.get("promotions", []) if p.get("id")}


_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
        r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_PERIOD_RE = re.compile(
    rf"\b{_DAY}\s+{_MON}(?:,?\s+20\d\d)?\s*(?:to|until|till|-|–|—)\s*{_DAY}\s+{_MON},?\s+20\d\d\b"
    rf"|\b(?:now\s+)?(?:until|till)\s+{_DAY}\s+{_MON},?\s+20\d\d\b",
    re.I)


def find_period(text: str) -> str:
    """A date range printed in a page's text ("18 August to 31 December 2026",
    "until 30 June 2027"), or "". A page often prints several (one per offer);
    the one that ends LAST wins, because the promo is live until its last offer
    ends and an early date would hide it too soon. Best effort: end_date_from_period
    still decides whether it is clear enough to hide the promo by."""
    from merge_campaign import end_date_from_period  # lazy: merge_campaign imports this module
    found = [" ".join(m.group(0).split()) for m in _PERIOD_RE.finditer(text or "")]
    if not found:
        return ""
    return max(found, key=lambda p: end_date_from_period(p) or "")
