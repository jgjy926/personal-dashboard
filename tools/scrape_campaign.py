#!/usr/bin/env python3
"""
scrape_campaign.py — Public Bank card-promo PARSER (no LLM, no API key, $0).

Why this shape: calling an LLM API to summarise every promo costs money. So this
tool does ONLY the free part — it scrapes each promo's title, image, link, and
downloads the raw Terms & Conditions text. You then paste the generated prompt
into Claude (or any chat LLM) for FREE, get the summaries back as JSON, and drop
the finished promotions.json into data/. No paid API anywhere.

Outputs (into data/):
  campaign_raw/<id>.txt     raw T&C text per promo (what you feed the LLM)
  promotions.draft.json     title/image/link/category, tnc_summary left ""
  campaign_prompt.txt       paste-this-into-Claude block (all raw T&C + instruction)

Usage:
  pip install requests beautifulsoup4
  python tools/scrape_campaign.py --max 15

Then: open campaign_prompt.txt -> paste into Claude -> paste the JSON reply of
{id: tnc_summary} back, merge into promotions.draft.json, save as promotions.json.
(A helper to merge is printed at the end.)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing deps. Run:  pip install requests beautifulsoup4")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA, "campaign_raw")

BASE = "https://www.pbebank.com"
LISTING = f"{BASE}/en/promotions/credit-debit-cards-promotions/?type=cards"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}

# Cheap category guess from the title/text — the LLM can refine it later.
CATEGORY_HINTS = [
    ("Dining", ["dining", "restaurant", "food", "cafe"]),
    ("Travel", ["travel", "flight", "airline", "lounge", "hotel"]),
    ("Fuel", ["fuel", "petrol", "petron", "shell", "petronas"]),
    ("Cashback", ["cashback", "cash back", "rebate"]),
    ("Instalment", ["instalment", "installment", "0%", "ezy"]),
    ("Online", ["online", "e-commerce", "contactless", "qr"]),
]


def _id(link: str) -> str:
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]


def _abs(url: str) -> str:
    if not url:
        return ""
    return url if url.startswith("http") else f"{BASE}{url}"


def _guess_category(text: str) -> str:
    low = text.lower()
    for cat, keys in CATEGORY_HINTS:
        if any(k in low for k in keys):
            return cat
    return "Promotion"


def scrape_listing(session) -> list[dict]:
    r = session.get(LISTING, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    promos, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/promotions/" not in href:
            continue
        title = a.get_text(strip=True)
        if len(title) < 6:
            continue
        link = _abs(href)
        if link in seen or link.rstrip("/").endswith("credit-debit-cards-promotions"):
            continue
        seen.add(link)
        img = a.find("img")
        promos.append({
            "id": _id(link),
            "title": title,
            "link": link,
            "image": _abs(img["src"]) if img and img.get("src") else "",
        })
    return promos


def fetch_detail(session, promo: dict) -> str:
    """Return raw page text; also backfill the image if the card had none."""
    try:
        r = session.get(promo["link"], headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return f"(could not fetch detail page: {e})"
    soup = BeautifulSoup(r.text, "html.parser")
    if not promo["image"]:
        img = soup.find("img", {"class": "img-responsive"}) or soup.find("img")
        if img and img.get("src"):
            promo["image"] = _abs(img["src"])
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = re.sub(r"\s+\n", "\n", soup.get_text(separator=" ", strip=True))
    return text[:6000]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=15, help="Max promos to process")
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    session = requests.Session()
    today = date.today().isoformat()

    # Carry forward first_seen dates from the last published feed so "new today"
    # is honest — a promo keeps the date it FIRST appeared; only genuinely new
    # ids get stamped with today.
    prior_first_seen = {}
    prev_path = os.path.join(DATA, "promotions.json")
    if os.path.exists(prev_path):
        try:
            prev = json.load(open(prev_path, encoding="utf-8"))
            for p in prev.get("promotions", []):
                if p.get("id") and p.get("first_seen"):
                    prior_first_seen[p["id"]] = p["first_seen"]
        except Exception:
            pass

    print(f"[scrape] listing: {LISTING}")
    promos = scrape_listing(session)[: args.max]
    if not promos:
        print("[scrape] no promo links found — the page structure may have changed. "
              "Inspect the listing HTML and adjust the selector in scrape_listing().")
        return 1
    print(f"[scrape] found {len(promos)} promos, downloading T&C…")

    prompt_blocks = []
    for p in promos:
        raw = fetch_detail(session, p)
        p["category"] = _guess_category(p["title"] + " " + raw)
        p["period"] = ""          # LLM fills this from the raw text
        p["first_seen"] = prior_first_seen.get(p["id"], today)  # new ids => today
        p["tnc_summary"] = ""     # you fill this via the free LLM step
        with open(os.path.join(RAW_DIR, f"{p['id']}.txt"), "w", encoding="utf-8") as f:
            f.write(f"TITLE: {p['title']}\nLINK: {p['link']}\n\n{raw}")
        prompt_blocks.append(f"### id: {p['id']}\nTITLE: {p['title']}\n{raw[:2500]}\n")
        print(f"   • {p['id']}  {p['title'][:60]}")

    new_today = sum(1 for p in promos if p["first_seen"] == today)
    draft = {
        "meta": {"source": LISTING, "sample": False, "count": len(promos),
                 "today": today, "new_today": new_today,
                 "note": "Draft — tnc_summary/period pending the manual LLM step."},
        "promotions": promos,
    }
    with open(os.path.join(DATA, "promotions.draft.json"), "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=2, ensure_ascii=False)

    instruction = (
        "You are summarising Malaysian credit-card promotions. For EACH id below, "
        "read the raw text and return STRICT JSON: an object mapping id -> "
        '{"period": "<campaign period or \'\'>", "tnc_summary": "<=60 words covering the '
        "main offer, minimum spend/criteria, cap, and expiry>\"}. Do not invent terms "
        "not present in the text.\n\n"
    )
    with open(os.path.join(DATA, "campaign_prompt.txt"), "w", encoding="utf-8") as f:
        f.write(instruction + "\n".join(prompt_blocks))

    print("\n[scrape] wrote data/promotions.draft.json, data/campaign_prompt.txt, data/campaign_raw/*.txt")
    print("Next (free): paste data/campaign_prompt.txt into Claude → get {id:{period,tnc_summary}} JSON →")
    print("merge into promotions.draft.json (fill each promo's period + tnc_summary) → save as data/promotions.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
