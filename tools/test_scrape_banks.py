#!/usr/bin/env python3
"""Unit tests for the HLB / UOB scrapers and the shared promo helpers — no network.

    python -m unittest discover -s tools -p 'test_*.py'

The HTML parsers need BeautifulSoup; CI's test job installs no scraper deps, so
those tests skip there and run wherever bs4 is installed. The fixtures are trimmed
copies of what the banks' pages returned in October 2026.
"""
from __future__ import annotations

import os
import sys
import types
import unittest

try:
    import bs4  # noqa: F401
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
for mod in ("requests", "bs4"):
    if mod not in sys.modules:
        try:
            __import__(mod)
        except ImportError:
            stub = types.ModuleType(mod)
            stub.BeautifulSoup = object
            sys.modules[mod] = stub

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import promo_feed as pf  # noqa: E402
import scrape_hlb as hlb  # noqa: E402
import scrape_uob as uob  # noqa: E402

CARDS = hlb.CARDS_CATEGORY
DEPOSIT = CARDS.replace("/66", "/2")


class TestFindPeriod(unittest.TestCase):
    def test_reads_a_range(self):
        self.assertEqual(pf.find_period("… a swipe within 60 days *From 18 August to 31 December 2026."),
                         "18 August to 31 December 2026")

    def test_several_offers_take_the_one_that_ends_last(self):
        text = "Offer A. Campaign valid until 30 September 2026. Offer B. Campaign valid until 31 October 2026."
        self.assertEqual(pf.find_period(text), "until 31 October 2026")

    def test_nothing_printed(self):
        self.assertEqual(pf.find_period("Terms and conditions apply."), "")


class TestHlbListing(unittest.TestCase):
    def test_keeps_only_cards_and_strips_tracking(self):
        doc = {"latest": {"listing": [
            {"catogory": CARDS, "title": "Win  RM2,000", "desc": "Apply now",
             "link": "/en/personal-banking/promotions/cards-acquisition-promotion.html",
             "image": "/content/dam/x.jpg"},
            {"catogory": DEPOSIT, "title": "3.88% p.a.", "link": "/en/personal-banking/promotions/dp.html"},
            {"catogory": CARDS, "title": "iPhone",
             "link": "/en/personal-banking/promotions/iphone.html?icp=hlb-en-promo-landing-cards-iphone18"},
        ]}, "ending_soon": {"listing": [
            {"catogory": CARDS, "title": "iPhone", "link": "/en/personal-banking/promotions/iphone.html"},
        ]}}
        got = hlb.card_items(doc)
        self.assertEqual([g["title"] for g in got], ["Win RM2,000", "iPhone"])
        self.assertEqual(got[1]["link"], "https://www.hlb.com.my/en/personal-banking/promotions/iphone.html")
        self.assertEqual(got[0]["image"], "https://www.hlb.com.my/content/dam/x.jpg")

    @unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
    def test_detail_page(self):
        html = """<header><img src="/content/dam/hlb/logo.svg"></header>
        <img src="/content/dam/hlb/my/images/Promotions/2026/x/x-social-sharing.jpg">
        <img src="/content/dam/hlb/my/images/Promotions/2026/x/header-banner.jpg">
        <p>*From 18 August to 31 December 2026</p>
        <a href="/content/dam/hlb/my/images/Promotions/2026/x/guide.pdf">Guide</a>
        <a href="/content/dam/hlb/my/images/Promotions/2026/x/offer-tnc.pdf">T&amp;C</a>"""
        got = hlb.parse_detail(html)
        self.assertTrue(got["image"].endswith("/header-banner.jpg"))
        self.assertTrue(got["tnc_link"].endswith("/offer-tnc.pdf"))
        self.assertEqual(got["period"], "18 August to 31 December 2026")


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestUobDeals(unittest.TestCase):
    def test_offer_period_and_terms_link(self):
        desc = ('<p class="paragraph" ><p>RM100 off with minimum spend RM3,000</p><p>&nbsp;</p>'
                '<p><span>Promo Code:</span> <br>UOB20</p><p>&nbsp;</p>'
                '<p><span>Offer period:</span> <br>15 October to 31 October 2026</p>'
                '<p>For full terms and conditions, click <a href="/personal/promotions/senheng.page">here</a>.</p></p>')
        offer, period, link = uob.parse_offer(desc)
        self.assertEqual(offer, "RM100 off with minimum spend RM3,000 · Promo Code: UOB20")
        self.assertEqual(period, "15 October to 31 October 2026")
        self.assertEqual(link, "https://www.uob.com.my/personal/promotions/senheng.page")

    def test_bulleted_offers(self):
        desc = ('<p class="paragraph" ><ol><li>20% off total dining bill</li>'
                '<li>Complimentary a la carte dish</li></ol><p>&nbsp;</p>'
                '<p><span>Offer period:</span> <br>15 July 2026 to 31 December 2026</p></p>'
                '<ul class="content-list note mb-0"></ul>')
        offer, period, _ = uob.parse_offer(desc)
        self.assertEqual(offer, "20% off total dining bill · Complimentary a la carte dish")
        self.assertEqual(period, "15 July 2026 to 31 December 2026")

    def test_card_fragment(self):
        frag = """<div id="product-category-content"><div class="category-item"><div class="card">
          <img src="/assets/web-resources/personal/promotions/kissa/360x240.jpg">
          <div class="card-id d-none">kissa_koyotei_café</div>
          <h6 class="card-subtitle">ASIAN</h6><h4 class="card-title">KissA Koyotei Café</h4>
          <input class="fullDesc" type="hidden"
                 value="&lt;p&gt;10% off&lt;/p&gt;&lt;p&gt;Offer period: 1 October 2026 to 15 January 2027&lt;/p&gt;">
        </div></div></div>"""
        (d,) = uob.parse_deals(frag, "Dining", "https://www.uob.com.my/personal/promotions/dining-deals.page")
        self.assertEqual((d["title"], d["subcategory"], d["category"]), ("KissA Koyotei Café", "Asian", "Dining"))
        self.assertEqual((d["offer"], d["period"]), ("10% off", "1 October 2026 to 15 January 2027"))
        self.assertEqual(d["link"], "https://www.uob.com.my/personal/promotions/dining-deals.page",
                         "no terms link: falls back to the category page")
        self.assertTrue(d["image"].startswith("https://www.uob.com.my/assets/"))


if __name__ == "__main__":
    unittest.main()
