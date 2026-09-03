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
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
DRAFT = os.path.join(DATA, "promotions.draft.json")
LIVE = os.path.join(DATA, "promotions.json")


def _load(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None


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
            p["first_seen"] = old.get("first_seen") or p.get("first_seen") or today
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
            "note": "Live scrape. Summaries are AI-generated — verify official T&C via the source link.",
        },
        "promotions": merged,
    }
    with open(LIVE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"[merge] wrote {LIVE} — {len(merged)} promos, {new_today} new today.")
    if pending:
        print(f"[merge] {len(pending)} need a T&C summary: {', '.join(pending)}")
        print("        Paste data/campaign_prompt.txt into Claude, then fill these in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
