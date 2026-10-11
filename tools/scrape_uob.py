#!/usr/bin/env python3
"""
scrape_uob.py — UOB Malaysia card promotions, as two feeds:

  data/promos/uob.draft.json        the featured CAMPAIGNS on
                                    uob.com.my/personal/promotions/index.page
                                    (bank-run, e.g. "Travel further with daily
                                    cashback"); summarised like the other banks'.
  data/promos/uob-deals.draft.json  the card MERCHANT DEALS behind its Dining /
                                    Shopping / Travel / Lifestyle / Others pages,
                                    a few hundred of them.

The deal pages show six cards and page in the rest from

    GET https://www.uob.com.my/revamp/personal/ajax-category-page-filter.page
        ?categoryName=Dining Deals&dctInfo=page/category-page-filter
        &MaximumNumberOfBlocks=<n>&start=0

which returns every card of a category in one HTML fragment when n is large.
Each card carries the offer text itself, including "Offer period: …" and a link
to that deal's terms page, so deals need no AI step: the bank's own words become
tnc_summary (summary_source "bank") and the period gives end_date.

Usage:
  pip install requests beautifulsoup4
  python tools/scrape_uob.py && python tools/merge_campaign.py
"""
from __future__ import annotations

import html as htmllib
import re
import sys
from datetime import date
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing deps. Run:  pip install requests beautifulsoup4")

from promo_feed import BANKS, HEADERS, draft_path, find_period, guess_category, prior_promos, promo_id, save

BASE = "https://www.uob.com.my"
LANDING = f"{BASE}/personal/promotions/index.page"
DEALS_API = f"{BASE}/revamp/personal/ajax-category-page-filter.page"
# (categoryName the endpoint wants, the label the tab shows, the public page)
DEAL_CATEGORIES = [
    ("Dining Deals", "Dining", f"{BASE}/personal/promotions/dining-deals.page"),
    ("Shopping Deals", "Shopping", f"{BASE}/personal/promotions/shopping-deals.page"),
    ("Travel Deals", "Travel", f"{BASE}/personal/promotions/travel-deals.page"),
    ("Lifestyle Deals", "Lifestyle", f"{BASE}/personal/promotions/lifestyle-deals.page"),
    ("Other Deals", "Others", f"{BASE}/personal/promotions/other-deals.page"),
]
# Site-wide PDFs every UOB page links in its chrome; never a campaign's T&C.
_CHROME_PDF = re.compile(r"cctips|stc-governing|service-guide|privacy|pn-web-cookies", re.I)


def _get(session, url, **kw) -> str:
    r = session.get(url, headers=HEADERS, timeout=60, **kw)
    r.raise_for_status()
    r.encoding = "utf-8"   # the server omits the charset; requests would guess Latin-1
    return r.text


# ── featured campaigns ──────────────────────────────────────────────────────
def featured_links(landing_html: str) -> list[dict]:
    """The landing page's 'Find out more' tiles: link, heading, blurb."""
    soup = BeautifulSoup(landing_html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True, attrs={"button_name": "find out more"}):
        link = urljoin(BASE, a["href"]).split("?")[0]
        if link in seen:
            continue
        seen.add(link)
        box = a
        for _ in range(6):
            box = box.parent
            if box is None or box.find("img"):
                break
        heading = box.find(["h1", "h2", "h3", "h4", "h5"]) if box else None
        para = box.find("p") if box else None
        out.append({"link": link,
                    "title": heading.get_text(" ", strip=True) if heading else "",
                    "desc": para.get_text(" ", strip=True) if para else ""})
    return out


def parse_campaign(page_html: str) -> dict:
    soup = BeautifulSoup(page_html, "html.parser")
    meta = lambda prop: (soup.find("meta", property=prop) or {}).get("content", "")  # noqa: E731
    title = re.sub(r"\s*\|.*$", "", meta("og:title") or (soup.title.get_text() if soup.title else ""))
    tnc = ""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().split("?")[0].endswith(".pdf") and not _CHROME_PDF.search(href):
            tnc = urljoin(BASE, href)
            if re.search(r"tnc|terms|t-c", href + a.get_text(" "), re.I):
                break
    for tag in soup(["script", "style"]):
        tag.decompose()
    return {"title": title.strip(), "image": urljoin(BASE, meta("og:image")) if meta("og:image") else "",
            "tnc_link": tnc, "period": find_period(soup.get_text(" "))}


def scrape_campaigns(session, today: str) -> list[dict]:
    prior = prior_promos("uob")
    promos = []
    for f in featured_links(_get(session, LANDING)):
        try:
            c = parse_campaign(_get(session, f["link"]))
        except Exception as e:  # noqa: BLE001
            print(f"   ! {f['link']}: {type(e).__name__}")
            c = {"title": "", "image": "", "tnc_link": "", "period": ""}
        pid = promo_id(f["link"])
        title = f["title"] or c["title"]
        promos.append({
            "id": pid, "bank": "uob", "title": title, "desc": f["desc"], "link": f["link"],
            "image": c["image"], "category": guess_category(f"{title} {f['desc']}", "Credit Card"),
            "period": c["period"], "first_seen": (prior.get(pid) or {}).get("first_seen") or today,
            "tnc_link": c["tnc_link"], "tnc_summary": "",
        })
        print(f"   • campaign {pid}  {title[:60]}")
    return promos


# ── merchant deals ──────────────────────────────────────────────────────────
def parse_offer(desc_html: str) -> tuple[str, str, str]:
    """A deal card's hidden description -> (offer text, period, terms link)."""
    soup = BeautifulSoup(htmllib.unescape(desc_html or ""), "html.parser")
    a = soup.find("a", href=True)
    link = urljoin(BASE, a["href"]) if a else ""
    # The CMS wraps the whole text in an outer <p>; the lines are the innermost
    # paragraphs and any bullet points (some offers are a <ul>/<ol> of options).
    lines = [" ".join(el.get_text(" ").split()) for el in soup.find_all(["p", "li"])
             if el.name == "li" or not el.find(["p", "li"])]
    lines = [ln for ln in lines if ln]
    offer, period, take_period = [], "", False
    for ln in lines:
        low = ln.lower()
        if low.startswith("for full terms"):
            continue
        m = re.match(r"offer period\s*:?\s*(.*)", ln, re.I)
        if m:
            period = m.group(1).strip()
            take_period = not period
            continue
        if take_period:
            period, take_period = ln, False
            continue
        offer.append(ln)
    return " · ".join(offer), period, link


def parse_deals(fragment: str, label: str, page: str) -> list[dict]:
    soup = BeautifulSoup(fragment, "html.parser")
    out = []
    for it in soup.select(".category-item"):
        title_el = it.select_one(".card-title")
        if not title_el:
            continue
        key_el = it.select_one(".card-id")
        sub_el = it.select_one(".card-subtitle")
        img = it.find("img")
        desc = it.select_one("input.fullDesc")
        offer, period, link = parse_offer(desc["value"] if desc else "")
        title = " ".join(title_el.get_text(" ").split())
        out.append({
            "key": (key_el.get_text(strip=True) if key_el else "") or title.lower(),
            "title": title,
            "subcategory": (sub_el.get_text(" ", strip=True).title() if sub_el else ""),
            "category": label,
            "image": urljoin(BASE, img.get("data-src") or img.get("src") or "") if img else "",
            "offer": offer, "period": period,
            "link": link or page, "tnc_link": link,
        })
    return out


def scrape_deals(session, today: str) -> list[dict]:
    prior = prior_promos("uob-deals")
    promos, seen = [], set()
    for name, label, page in DEAL_CATEGORIES:
        frag = _get(session, DEALS_API, params={"categoryName": name, "dctInfo": "page/category-page-filter",
                                                "MaximumNumberOfBlocks": "1000", "start": "0"})
        deals = parse_deals(frag, label, page)
        print(f"   • {label}: {len(deals)} deals")
        for d in deals:
            if d["key"] in seen:   # listed under two categories: keep the first
                continue
            seen.add(d["key"])
            pid = promo_id(f"uob-deal|{d['key']}")
            promos.append({
                "id": pid, "bank": "uob", "title": d["title"], "link": d["link"], "image": d["image"],
                "category": label, "subcategory": d["subcategory"], "period": d["period"],
                "first_seen": (prior.get(pid) or {}).get("first_seen") or today,
                "tnc_link": d["tnc_link"], "tnc_summary": d["offer"], "summary_source": "bank",
            })
    return promos


def main() -> int:
    session = requests.Session()
    today = date.today().isoformat()
    status = 0
    try:
        campaigns = scrape_campaigns(session, today)
        if campaigns:
            save(draft_path("uob"), {
                "meta": {"bank": "uob", "bank_name": BANKS["uob"], "kind": "campaign", "source": LANDING,
                         "count": len(campaigns), "today": today,
                         "note": "UOB's featured card campaigns. tnc_summary is written from the "
                                 "banner + T&C by summarize_campaign.py or the Console."},
                "promotions": campaigns,
            })
        else:
            print("[uob] no featured campaigns found on the landing page — its layout may have changed.")
            status = 1
    except Exception as e:  # noqa: BLE001 - the deals below are independent
        print(f"[uob] campaigns failed: {type(e).__name__}: {e}")
        status = 1
    try:
        deals = scrape_deals(session, today)
        if deals:
            save(draft_path("uob-deals"), {
                "meta": {"bank": "uob", "bank_name": BANKS["uob"], "kind": "deal",
                         "source": f"{BASE}/personal/promotions/index.page",
                         "count": len(deals), "today": today,
                         "note": "UOB card merchant deals. tnc_summary is the bank's own offer text "
                                 "from the deal card (summary_source 'bank'), not an AI summary."},
                "promotions": deals,
            })
        else:
            print("[uob] no deals parsed — the deal pages' layout may have changed.")
            status = 1
    except Exception as e:  # noqa: BLE001
        print(f"[uob] deals failed: {type(e).__name__}: {e}")
        status = 1
    print("[uob] wrote data/promos/uob*.draft.json. Next: python tools/merge_campaign.py")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
