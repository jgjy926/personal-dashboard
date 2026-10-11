#!/usr/bin/env python3
"""
merge_campaign.py — fold each fresh scrape into its live feed, keeping the T&C
summaries already written (the one step that needs a human/LLM and shouldn't be
redone every run).

Flow (all $0, no paid API):
    python tools/scrape_campaign.py   # Public Bank -> data/promos/pbb.draft.json
    python tools/scrape_hlb.py        # HLB         -> data/promos/hlb.draft.json
    python tools/scrape_uob.py        # UOB         -> data/promos/uob(.deals).draft.json
    python tools/merge_campaign.py    # every draft -> its data/promos/<feed>.json

Each draft is merged into its own feed and then removed, so a scraper that failed
today leaves that bank's feed exactly as it was. For each scraped promo: carry
forward tnc_summary + period from the live feed when the id is already known;
brand-new promos arrive with today's first_seen and (for campaigns) an empty
summary, which summarize_campaign.py or the Console fills.
"""
from __future__ import annotations

import os
import re
from datetime import date

from promo_feed import BANKS, PROMOS_DIR, load, save

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DATE_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?"
    r"|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?"
    r"(?:,?\s*(\d{4})\b)?", re.I)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
# A lone date only counts as the END when something before it says so.
_ENDS_AT_RE = re.compile(r"(?:until|till|through|to|by|ends?|expir\w*|[-–—])\s*$|\bnow\b", re.I)


def _resolve(match, period):
    """(date, had_own_year) for one _DATE_RE match. A year printed only on a later
    date ("15 August to 25 September 2026") carries back."""
    day, mon, year = match.groups()
    own = bool(year)
    year = year or (_YEAR_RE.search(period, match.end()) or [None, None])[1]
    if not year:
        return None, own
    try:
        return date(int(year), _MONTHS.index(mon[:3].lower()) + 1, int(day)), own
    except ValueError:
        return None, own


def end_date_from_period(period: str) -> str:
    """Best-effort ISO end date from a free-text campaign period, or "" when it can't
    be read with confidence. A missing date only means no expiry badge; a wrong one
    would hide a live promo, so anything ambiguous returns "".

    Reads: "15 August to 25 September 2026", "1 September 2026 - 28 February 2027",
    "Now until 30 June 2027", ordinals, and lists of recurring windows (the last one
    wins). Gives "" for month-only periods ("monthly, from September 2026 to April
    2027"), a lone start date ("From 1 Sep 2026"), and an end before its start."""
    period = period or ""
    matches = list(_DATE_RE.finditer(period))
    if not matches:
        return ""
    end, _ = _resolve(matches[-1], period)
    if not end:
        return ""
    if len(matches) == 1:
        return end.isoformat() if _ENDS_AT_RE.search(period[:matches[0].start()]) else ""
    start, start_had_year = _resolve(matches[-2], period)
    if start and start > end and not start_had_year:
        # "1 Dec - 5 Jan 2027": the carried-back year belongs to the end only.
        try:
            start = start.replace(year=start.year - 1)
        except ValueError:
            start = None
    if not start or start > end:
        return ""
    return end.isoformat()


# Fields a scrape never produces, so a known promo keeps what it had.
_CARRY = ("tnc_summary", "tnc_link", "summary_source")


def merge_file(draft_path: str, live_path: str) -> dict:
    """Merge one draft into one live feed and write it. Returns the new feed."""
    draft = load(draft_path) or {}
    live = load(live_path) or {"promotions": []}
    prior = {p.get("id"): p for p in live.get("promotions", [])}
    dmeta = draft.get("meta", {})

    today = date.today().isoformat()
    merged = []
    for p in draft.get("promotions", []):
        old = prior.get(p.get("id"))
        if old:
            scraped_period = p.get("period", "")
            for k in _CARRY:
                p[k] = p.get(k) or old.get(k, "")
            p["period"] = scraped_period or old.get("period", "")
            p["first_seen"] = old.get("first_seen") or p.get("first_seen") or today
            # A period the bank changed makes the old end_date describe another
            # campaign: drop it and re-derive below. Otherwise keep it (it may be
            # hand-set or written with the summary).
            if not p.get("end_date") and not (scraped_period and scraped_period != old.get("period", "")):
                p["end_date"] = old.get("end_date", "")
        p.setdefault("first_seen", today)
        if not p.get("end_date"):
            p["end_date"] = end_date_from_period(p.get("period", ""))
        merged.append(p)

    bank = dmeta.get("bank") or live.get("meta", {}).get("bank", "")
    out = {
        "meta": {
            "bank": bank,
            "bank_name": dmeta.get("bank_name") or BANKS.get(bank, bank),
            "kind": dmeta.get("kind", "campaign"),
            "source": dmeta.get("source", ""),
            "generated_at": today + "T00:00:00+00:00",
            "today": today,
            "new_today": sum(1 for p in merged if p.get("first_seen") == today),
            "count": len(merged),
            "note": dmeta.get("note", ""),
        },
        "promotions": merged,
    }
    save(live_path, out)
    return out


def main() -> int:
    drafts = sorted(f for f in os.listdir(PROMOS_DIR) if f.endswith(".draft.json"))         if os.path.isdir(PROMOS_DIR) else []
    if not drafts:
        print(f"[merge] no drafts in {PROMOS_DIR} — run a scraper first.")
        return 1
    today = date.today().isoformat()
    for name in drafts:
        draft = os.path.join(PROMOS_DIR, name)
        live = os.path.join(PROMOS_DIR, name.replace(".draft.json", ".json"))
        out = merge_file(draft, live)
        os.remove(draft)
        promos = out["promotions"]
        pending = sum(1 for p in promos if not p.get("tnc_summary"))
        expired = sum(1 for p in promos if p.get("end_date") and p["end_date"] < today)
        print(f"[merge] {os.path.basename(live)}: {len(promos)} promos, "
              f"{out['meta']['new_today']} new today"
              + (f", {pending} without a summary" if pending and out["meta"]["kind"] == "campaign" else "")
              + (f", {expired} past end_date (hidden behind 'Show expired')" if expired else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
