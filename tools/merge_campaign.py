#!/usr/bin/env python3
"""
merge_campaign.py — fold a fresh scrape into the live promotions.json, keeping the
T&C summaries you already wrote (the one step that needs a human/LLM and shouldn't
be redone every run).

Flow (all $0, no paid API):
    python tools/scrape_campaign.py          # -> data/promotions.draft.json (+ raw T&C)
    python tools/merge_campaign.py           # merge draft into data/promotions.json

For each scraped promo: carry forward tnc_summary + period from the existing
promotions.json when the id is already known; brand-new promos arrive with an empty
summary (shown as "summary pending" on the tab) and today's first_seen. The script
prints which ids still need a summary so you can fill them from data/campaign_prompt.txt.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
DRAFT = os.path.join(DATA, "promotions.draft.json")
LIVE = os.path.join(DATA, "promotions.json")


def _load(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None


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


def main() -> int:
    draft = _load(DRAFT)
    if not draft:
        print(f"[merge] no {DRAFT} — run tools/scrape_campaign.py first.")
        return 1
    live = _load(LIVE) or {"promotions": []}
    prior = {p.get("id"): p for p in live.get("promotions", [])}

    today = date.today().isoformat()
    merged = []
    pending = []
    for p in draft.get("promotions", []):
        old = prior.get(p.get("id"))
        if old:
            p["tnc_summary"] = p.get("tnc_summary") or old.get("tnc_summary", "")
            p["period"] = p.get("period") or old.get("period", "")
            p["tnc_link"] = p.get("tnc_link") or old.get("tnc_link", "")
            p["first_seen"] = old.get("first_seen") or p.get("first_seen") or today
            p["end_date"] = p.get("end_date") or old.get("end_date", "")
        # An end_date written with the summary (or by hand) wins; otherwise derive it.
        if not p.get("end_date"):
            p["end_date"] = end_date_from_period(p.get("period", ""))
        if not p.get("tnc_summary"):
            pending.append(p.get("id"))
        merged.append(p)

    new_today = sum(1 for p in merged if p.get("first_seen") == today)
    out = {
        "meta": {
            "generated_at": date.today().isoformat() + "T00:00:00+00:00",
            "sample": False,
            "source": draft.get("meta", {}).get("source", ""),
            "today": today,
            "new_today": new_today,
            "count": len(merged),
            "note": "Live scrape. Each promo's `image` is the bank's full campaign poster (offer, "
                    "minimum spend, and campaign period are printed on it) — where tnc_summary/period "
                    "are set they were read from that poster by a human or vision-capable AI via the "
                    "Console. tnc_link is the authoritative PDF/terms page for verification.",
        },
        "promotions": merged,
    }
    with open(LIVE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"[merge] wrote {LIVE} — {len(merged)} promos, {new_today} new today.")
    expired = sum(1 for p in merged if p.get("end_date") and p["end_date"] < today)
    if expired:
        print(f"[merge] {expired} past their end_date but still listed by the bank — kept, and "
              "hidden on the dashboard behind 'Show expired'.")
    if pending:
        # Not a defect: most promos have no on-page text to summarise (terms are
        # PDF-only), so they show a plain "see official T&C" link on the dashboard
        # instead of a summary — that's expected, not a to-do list.
        prompt_path = os.path.join(DATA, "campaign_prompt.txt")
        if os.path.exists(prompt_path):
            print(f"[merge] {len(pending)} have no tnc_summary (fine — dashboard links to "
                  "tnc_link instead). Optional: paste data/campaign_prompt.txt into Claude "
                  "for the ones that DO have real page text, then re-run merge.")
        else:
            print(f"[merge] {len(pending)} have no tnc_summary — normal for this site "
                  "(terms are PDF-only). The dashboard shows the 📄 Official T&C link instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
