#!/usr/bin/env python3
"""
scrape_hlb.py — Hong Leong Bank card promotions -> data/promos/hlb.draft.json.

HLB's promotions page (hlb.com.my/en/personal-banking/promotions.html) renders
its grid from a JSON servlet, so this reads that instead of the HTML:

    GET https://www.hlb.com.my/bin/hlb/PromotionTeaserUtil
        ?locale=en&targetedSection=…/promotions&targetedCategory=all
        &targetedPillar=…/promotion-pillar-list/…/dropdownValue/1   (Personal)

Every listing item carries its category as a CMS path (in a field the site
spells `catogory`); the "Cards" tab is the one ending in dropdownValue/66. That
tag is what decides "card promo", never a word in the URL: the promo carousel
links e.g. `?icp=…cards-offer…` from promos that aren't card promos at all.

Each kept promo's detail page supplies the poster-style banner (`image`), the
first T&C PDF (`tnc_link`) and, when it is printed as text, the campaign period.
tnc_summary is left blank for summarize_campaign.py / the Console, exactly like
Public Bank's; the listing's own one-liner is kept as `desc` meanwhile.

Usage:
  pip install requests beautifulsoup4
  python tools/scrape_hlb.py && python tools/merge_campaign.py
"""
from __future__ import annotations

import re
import sys
from datetime import date
from urllib.parse import urljoin, urlsplit, urlunsplit

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing deps. Run:  pip install requests beautifulsoup4")

from promo_feed import BANKS, HEADERS, draft_path, find_period, guess_category, prior_promos, promo_id, save

FEED = "hlb"
BASE = "https://www.hlb.com.my"
PAGE = f"{BASE}/en/personal-banking/promotions.html"
SERVLET = f"{BASE}/bin/hlb/PromotionTeaserUtil"
_CMS = "/content/site-configuration/hlb-site/my/dialog-admin"
PARAMS = {
    "locale": "en",
    "targetedSection": "/content/hlb/my/en/personal-banking/promotions",
    "targetedCategory": "all",
    "targetedPillar": f"{_CMS}/promotion-pillar-list/jcr:content/full-content/datasourcewithsvg/dropdownValue/1",
}
CARDS_CATEGORY = f"{_CMS}/promotion-category-list/jcr:content/full-content/datasourcewithsvg/dropdownValue/66"

_SKIP_IMG = re.compile(r"icon|logo|\.svg|social-sharing|qr", re.I)


def _clean_link(href: str) -> str:
    """Absolute, without the ?icp= campaign-tracking query (it differs per entry
    point, and the id must not)."""
    u = urlsplit(urljoin(BASE, href))
    return urlunsplit((u.scheme, u.netloc, u.path, "", ""))


def card_items(doc: dict) -> list[dict]:
    """The listing entries tagged Cards, from every section of the servlet reply
    ("latest", and "ending_soon" when the bank has one), first occurrence wins."""
    out, seen = [], set()
    for section in doc.values():
        for it in (section or {}).get("listing", []) if isinstance(section, dict) else []:
            if (it.get("catogory") or it.get("category")) != CARDS_CATEGORY or not it.get("link"):
                continue
            link = _clean_link(it["link"])
            if link in seen:
                continue
            seen.add(link)
            out.append({
                "title": " ".join((it.get("title") or "").split()),
                "desc": " ".join((it.get("desc") or "").split()),
                "link": link,
                "image": urljoin(BASE, it["image"]) if it.get("image") else "",
            })
    return out


def parse_detail(html: str) -> dict:
    """banner image, first T&C PDF, printed period — each "" when absent."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    image = ""
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        if "/content/dam/" in src and "/Promotions/" in src and not _SKIP_IMG.search(src):
            image = urljoin(BASE, src)
            break
    pdfs = [a["href"] for a in soup.find_all("a", href=True) if a["href"].lower().split("?")[0].endswith(".pdf")]
    tnc = next((h for h in pdfs if re.search(r"tnc|t-c|terms", h, re.I)), "") \
        or next((h for h in pdfs if "/Promotions/" in h), "")
    return {
        "image": image,
        "tnc_link": urljoin(BASE, tnc) if tnc else "",
        "period": find_period(soup.get_text(" ")),
    }


def main() -> int:
    session = requests.Session()
    r = session.get(SERVLET, params=PARAMS, headers={**HEADERS, "Accept": "application/json",
                                                      "Referer": PAGE}, timeout=30)
    r.raise_for_status()
    items = card_items(r.json())
    if not items:
        print("[hlb] no Cards-tagged promos in the servlet reply — the category id "
              f"({CARDS_CATEGORY.rsplit('/', 1)[-1]}) or the reply shape may have changed.")
        return 1

    prior = prior_promos(FEED)
    today = date.today().isoformat()
    promos = []
    for it in items:
        pid = promo_id(it["link"])
        try:
            d = session.get(it["link"], headers=HEADERS, timeout=30)
            d.raise_for_status()
            detail = parse_detail(d.text)
        except Exception as e:  # noqa: BLE001 - the listing data alone is still useful
            print(f"   ! {it['title'][:50]}: detail page failed ({type(e).__name__})")
            detail = {"image": "", "tnc_link": "", "period": ""}
        promos.append({
            "id": pid,
            "bank": FEED,
            "title": it["title"],
            "desc": it["desc"],
            "link": it["link"],
            "image": detail["image"] or it["image"],
            "category": guess_category(f"{it['title']} {it['desc']}", "Credit Card"),
            "period": detail["period"],
            "first_seen": (prior.get(pid) or {}).get("first_seen") or today,
            "tnc_link": detail["tnc_link"],
            "tnc_summary": "",
        })
        print(f"   • {pid}  {it['title'][:50]:<50} [{detail['period'] or 'no period on page'}]")

    save(draft_path(FEED), {
        "meta": {"bank": FEED, "bank_name": BANKS[FEED], "kind": "campaign", "source": PAGE,
                 "count": len(promos), "today": today,
                 "note": "HLB promotions tagged 'Cards'. `desc` is the bank's listing line; "
                         "tnc_summary is written from the banner + T&C PDF by "
                         "summarize_campaign.py or the Console."},
        "promotions": promos,
    })
    print(f"[hlb] {len(promos)} card promos -> data/promos/hlb.draft.json. Next: python tools/merge_campaign.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
