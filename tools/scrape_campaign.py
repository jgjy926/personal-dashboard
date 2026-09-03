#!/usr/bin/env python3
"""
scrape_campaign.py — Public Bank card-promo PARSER (no LLM, no API key, $0).

Scrapes the two card-specific listing pages (credit + debit), each a grid of
`.grid-item` cards — real title, real detail link, and (from the detail page)
the bank's own full CAMPAIGN POSTER as `image` — not the small landscape crop
the listing thumbnail uses, but the actual flyer graphic (`..._wb.jpg`,
~860x1300-1800px) with the offer, minimum spend, and campaign period printed
on it. That poster is the real source of truth for what a promo actually
offers, since this site publishes almost no separate inline T&C text.

`tnc_link` is captured separately (usually a PDF, occasionally an external
campaign site) — that's the AUTHORITATIVE legal document; the poster is
marketing copy. Enrichment (writing tnc_summary/period) is therefore best done
by VIEWING the poster (a human, or a vision-capable AI via the dashboard's
Card Promos -> Console) rather than trying to parse the PDF. The handful of
promos that DO carry real on-page body text are still captured to
data/campaign_raw/ and folded into campaign_prompt.txt as a secondary path.

Usage:
  pip install requests beautifulsoup4
  python tools/scrape_campaign.py --max 20

Outputs (into data/):
  campaign_raw/<id>.txt       raw detail-page text per promo (where non-trivial)
  promotions.draft.json       title/image(poster)/link/tnc_link/category, tnc_summary ""
  campaign_prompt.txt         paste-ready block for the promos with real body text

Then: python tools/merge_campaign.py   (folds into data/promotions.json, keeping
any tnc_summary you already wrote for a given id).
"""
from __future__ import annotations

import argparse
import hashlib
import io
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

# OCR is OPTIONAL — the scraper works fine without it (falls back to whatever
# inline page text exists, usually nothing). Install for best-effort text
# extraction from each poster: `pip install pytesseract Pillow` + the
# Tesseract OCR engine itself (Windows: winget install UB-Mannheim.TesseractOCR;
# Debian/Ubuntu incl. GitHub Actions: apt-get install tesseract-ocr).
try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageOps
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA, "campaign_raw")

BASE = "https://www.pbebank.com"
# Card-specific listings (verified 2026-09: both render real `.grid-item` cards,
# unlike the generic /credit-debit-cards-promotions/ hub page, which is mostly
# nav/category links and was the original bug here).
LISTINGS = [
    (f"{BASE}/en/promotions/credit-cards-promotions/", "Credit Card"),
    (f"{BASE}/en/promotions/debit-cards-promotions/", "Debit Card"),
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}

CATEGORY_HINTS = [
    ("Dining", ["dining", "restaurant", "food", "cafe", "hotpot"]),
    ("Travel", ["travel", "flight", "airline", "lounge", "hotel", "destination"]),
    ("Fuel", ["fuel", "petrol", "petron", "shell", "petronas"]),
    ("Cashback", ["cashback", "cash back", "rebate"]),
    ("Instalment", ["instalment", "installment", "0%", "flexipay"]),
    ("Online", ["online", "e-commerce", "lazada", "shopee", "contactless", "qr"]),
]


def _id(link: str) -> str:
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]


def _abs(url: str) -> str:
    if not url:
        return ""
    return url if url.startswith("http") else f"{BASE}{url}"


def _guess_category(title: str, default: str) -> str:
    low = title.lower()
    for cat, keys in CATEGORY_HINTS:
        if any(k in low for k in keys):
            return cat
    return default


def scrape_listing(session, url: str) -> list[dict]:
    """Real cards live in `.grid-item` — a thumbnail <a> (image via data-src,
    lazy-loaded) + an <h3><a> title + a detail link. This is the fix for the
    original bug: scraping *every* `<a href*=/promotions/>` on the page picked
    up top-nav / category-filter links first, which have no image and no body
    text (that's why promos showed up with a blank picture and no T&C)."""
    r = session.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.find_all(class_="grid-item"):
        h3 = card.find("h3")
        a_title = h3.find("a", href=True) if h3 else None
        if not a_title:
            continue
        title = a_title.get_text(strip=True)
        link = _abs(a_title["href"])
        img = card.find("img")
        image = ""
        if img:
            image = _abs(img.get("data-src") or img.get("src") or "")
        out.append({"title": title, "link": link, "image": image})
    return out


_ICON_HINT_RE = re.compile(r"icon|logo|floater|favicon", re.I)


def _best_poster(soup) -> str:
    """The listing's `.grid-item` thumbnail (data-src, `..._s.jpg`) is a small,
    cropped landscape preview. Every detail page also lazy-loads a MUCH larger
    poster (`..._wb.jpg`, verified ~860x1300-1800px) as the first non-icon
    `<img class="lazy">` in the page — this is the bank's actual campaign
    flyer, with the offer, minimum spend, and campaign period printed on it as
    a designed graphic (confirmed across multiple promos). That's the real
    "full picture" a user expects, not the small crop, so it's preferred here."""
    for img in soup.find_all("img", class_="lazy"):
        src = img.get("data-src") or img.get("src") or ""
        if not src or _ICON_HINT_RE.search(src) or src.lower().endswith(".svg"):
            continue
        return _abs(src)
    return ""


def ocr_poster_text(session, image_url: str) -> str:
    """Best-effort text extraction from a promo's poster, via local Tesseract
    OCR — free, no API, runs entirely offline. Real-world tested against the
    live posters: it reads plain/small text well (body copy, footers, and
    often the campaign-period line) but FREQUENTLY MISSES large stylized
    numerals — the actual RM amount or "%" is often a huge decorative graphic,
    not real text, and OCR just drops it. So this is a supplementary text
    layer for the free copy-paste-to-any-AI path (campaign_prompt.txt), not a
    substitute for actually looking at the poster — the Console's primary
    flow still hands the AI the image itself so amounts are read correctly.
    Returns "" (never raises) if OCR isn't installed or anything goes wrong."""
    if not HAS_OCR or not image_url:
        return ""
    try:
        r = session.get(image_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        # Grayscale + contrast boost + 2x upscale measurably improved recall in
        # testing against these specific posters (low-contrast text over photo/
        # gradient backgrounds); PSM 11 (sparse text, no layout assumptions)
        # suits a graphic-design poster better than the default "assume a
        # uniform block of text" mode.
        gray = ImageOps.grayscale(img)
        boosted = ImageEnhance.Contrast(gray).enhance(2.5)
        w, h = boosted.size
        upscaled = boosted.resize((w * 2, h * 2))
        text = pytesseract.image_to_string(upscaled, config="--psm 11")
    except Exception:
        return ""
    # OCR noise cleanup: drop very short "lines" (single stray characters from
    # misread decorative graphics/icons are the dominant noise source here)
    # and collapse blank runs.
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if len(ln) >= 3]
    return "\n".join(lines).strip()


def fetch_detail(session, promo: dict) -> tuple[str, str]:
    """Return (raw_body_text, tnc_link). The bank's terms live behind a
    "Terms and Conditions — Click here" link inside `.content` (usually a PDF,
    sometimes an external campaign site) rather than as inline page text, so
    tnc_link is captured as its own field — that's what the card should point
    to for the AUTHORITATIVE legal terms (the poster is marketing copy, not
    the legal document). The poster image itself replaces the small listing
    thumbnail whenever the detail page has one (see _best_poster); og:image is
    a last-resort fallback only if neither is found."""
    try:
        r = session.get(promo["link"], headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return f"(could not fetch detail page: {e})", ""
    soup = BeautifulSoup(r.text, "html.parser")

    poster = _best_poster(soup)
    if poster:
        promo["image"] = poster
    elif not promo.get("image"):
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            promo["image"] = _abs(og["content"])

    tnc_link = ""
    content = soup.find(class_="content")
    if content:
        for a in content.find_all("a", href=True):
            label = a.get_text(strip=True).lower()
            if "click here" in label or "terms" in label or a["href"].lower().endswith(".pdf"):
                tnc_link = _abs(a["href"])
                break

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    main = soup.find(class_="content") or soup.find("main")
    text = re.sub(r"\s+\n", "\n", main.get_text(separator=" ", strip=True)) if main else ""
    return text[:4000], tnc_link


_BOILERPLATE_RE = re.compile(
    r"terms\s+and\s+conditions\s*(click here|apply)?", re.I)


def has_real_body(title: str, raw: str) -> bool:
    """Most promos on this site carry NO inline description — the detail page's
    `.content` block is just the title echoed back + a bare "Terms and
    Conditions — Click here" link (captured separately as tnc_link). Strip that
    known boilerplate and the title, and only call it "real body text" if a
    meaningful amount of *other* content remains — otherwise every promo would
    falsely qualify for the LLM-summary flow with nothing worth summarising."""
    if "could not fetch" in raw:
        return False
    stripped = _BOILERPLATE_RE.sub("", raw)
    stripped = stripped.replace(title, "", 1).strip()
    return len(stripped) > 60


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=20, help="Max promos to process")
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    session = requests.Session()
    today = date.today().isoformat()

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

    seen_links: set[str] = set()
    promos: list[dict] = []
    for url, default_cat in LISTINGS:
        print(f"[scrape] listing: {url}")
        for card in scrape_listing(session, url):
            if not card["link"] or card["link"] in seen_links:
                continue
            seen_links.add(card["link"])
            card["id"] = _id(card["link"])
            card["category"] = _guess_category(card["title"], default_cat)
            promos.append(card)
            if len(promos) >= args.max:
                break
        if len(promos) >= args.max:
            break

    if not promos:
        print("[scrape] no promo cards found — the page structure may have changed. "
              "Inspect a listing page's HTML and adjust scrape_listing()'s selector "
              "(currently `.grid-item`).")
        return 1
    print(f"[scrape] found {len(promos)} card promos, fetching detail pages…")

    if not HAS_OCR:
        print("[scrape] note: pytesseract/Pillow/Tesseract not installed — skipping OCR "
              "(campaign_prompt.txt will only include the rare promo with real inline text). "
              "pip install pytesseract Pillow, plus the Tesseract engine itself, to enable it.")

    prompt_blocks = []
    ocr_count = 0
    for p in promos:
        raw, tnc_link = fetch_detail(session, p)
        p["period"] = ""
        p["first_seen"] = prior_first_seen.get(p["id"], today)
        p["tnc_link"] = tnc_link
        p["tnc_summary"] = ""

        ocr_text = ocr_poster_text(session, p.get("image", ""))
        if ocr_text:
            ocr_count += 1
        combined = (raw + "\n\n" + ocr_text).strip() if ocr_text else raw
        has_body = has_real_body(p["title"], combined)
        if has_body:
            with open(os.path.join(RAW_DIR, f"{p['id']}.txt"), "w", encoding="utf-8") as f:
                f.write(f"TITLE: {p['title']}\nLINK: {p['link']}\nTNC: {tnc_link}\n\n"
                        f"PAGE TEXT:\n{raw}\n\n"
                        f"OCR OF POSTER (best-effort — often misses large stylized numbers/amounts "
                        f"drawn as graphics; verify amounts against the image itself):\n{ocr_text}\n")
            prompt_blocks.append(
                f"### id: {p['id']}\nTITLE: {p['title']}\nPAGE TEXT: {raw[:1000]}\n"
                f"OCR OF POSTER (noisy, may miss large numbers — verify against the image): {ocr_text[:1500]}\n")
        src = "page" if has_real_body(p["title"], raw) else ("ocr" if ocr_text else "none")
        tag = f"poster+{src}-text" if has_body else ("poster only" if p.get("image") else "no image/T&C found")
        print(f"   • {p['id']}  {p['title'][:50]:<50} [{tag}]")

    new_today = sum(1 for p in promos if p["first_seen"] == today)
    draft = {
        "meta": {"source": LISTINGS[0][0], "sample": False, "count": len(promos),
                 "today": today, "new_today": new_today,
                 "note": "Draft — tnc_summary stays blank until filled via the Console "
                         "(a vision-capable AI reading each poster image, the reliable path) "
                         "or from campaign_prompt.txt's OCR/page text for the ones that have it."},
        "promotions": promos,
    }
    with open(os.path.join(DATA, "promotions.draft.json"), "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=2, ensure_ascii=False)

    if prompt_blocks:
        instruction = (
            "You are summarising Malaysian credit-card promotions from a mix of page text and "
            "OCR'd poster text. The OCR text is NOISY and frequently MISSES large stylized numbers "
            "(the RM amount or %% is often a big decorative graphic OCR can't read) — if an amount "
            "is unclear or missing, say so rather than guessing. For EACH id below, return STRICT "
            "JSON: an object mapping id -> {\"period\": \"<campaign period or ''>\", \"tnc_summary\": "
            "\"<=60 words covering the main offer, minimum spend/criteria, cap, and expiry>\"}. "
            "Do not invent terms not present in the text.\n\n"
        )
        with open(os.path.join(DATA, "campaign_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(instruction + "\n".join(prompt_blocks))
        print(f"\n[scrape] {len(prompt_blocks)}/{len(promos)} promos have usable text (page and/or OCR) "
              "-> data/campaign_prompt.txt")
        print("Optional (free): paste it into any AI (vision not required for this path) for summaries, then merge.")
    if ocr_count:
        print(f"[scrape] OCR extracted text from {ocr_count}/{len(promos)} posters.")
    print(f"[scrape] Every promo's poster image is captured as `image` — enrich via the dashboard's "
          "Card Promos -> Console (view each poster with a vision-capable AI) for the main path.")

    print("[scrape] wrote data/promotions.draft.json" + (", data/campaign_raw/*.txt" if prompt_blocks else ""))
    print("Next: python tools/merge_campaign.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
