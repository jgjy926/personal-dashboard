#!/usr/bin/env python3
"""
summarize_campaign.py — write the missing `tnc_summary` / `period` fields straight
into data/promotions.json, so a new promo goes live summarised with no human step.

This replaces the Card Promos 🛠️ Console round-trip (download bundle → paste into an
AI → upload the reply → download the merged file → drop it into data/). That loop is
manual for exactly one reason: the Console runs in the browser, and a browser page
cannot write back into the repo. Anything with filesystem access can — which is what
this is.

Two modes, both ending with promotions.json updated in place:

  auto (default)   Reads each promo's poster image and official T&C PDF with the
                   Anthropic API (vision) and writes the summary itself. Needs
                   ANTHROPIC_API_KEY. Runs unattended in CI.

                       python tools/summarize_campaign.py

  --apply FILE     Applies an {id: {period, tnc_summary}} map produced elsewhere —
                   the Console's AI reply, or a summary written by Claude Code in a
                   chat session. No API key, no network.

                       python tools/summarize_campaign.py --apply reply.json

Safety rails, because these are money amounts read off a decorative poster:
  * Only promos whose tnc_summary is EMPTY are touched. A summary already in the
    file — hand-written or verified — is never overwritten unless --force.
  * The model is told to return needs_review=true rather than guess an amount it
    cannot read. Those promos are left empty and listed at the end instead of being
    filled with a plausible-looking wrong number.
  * The card on the dashboard still carries "AI summary — verify official T&C" with
    a link to the authoritative PDF.

Cost: a poster plus a short T&C PDF is a few thousand input tokens — cents per promo
at Opus pricing, and only for promos that are actually new. Pass --model
claude-sonnet-5 to cut that further.
"""
from __future__ import annotations

import argparse
import base64
import json
import os

import re

import requests

from merge_campaign import end_date_from_period

HERE = os.path.dirname(os.path.abspath(__file__))
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ROOT = os.path.dirname(HERE)
LIVE = os.path.join(ROOT, "data", "promotions.json")

UA = {"User-Agent": "Mozilla/5.0 (compatible; personal-dashboard/1.0)"}

SYSTEM = """You read Malaysian bank credit-card campaign posters and their official \
Terms & Conditions, and write one short factual summary a cardholder can act on.

Rules:
- Lead with the concrete benefit and the condition to get it: discount/cashback amount, \
minimum spend, and the card or channel it is restricted to.
- Then add only the conditions that change whether someone qualifies: redemption windows, \
quotas, per-user caps, registration requirements, notable exclusions.
- 1-3 sentences. No marketing language, no "Terms and conditions apply", no invented detail.
- `period` is the overall campaign period, styled like "8 September - 27 December 2026".
- `end_date` is the last day the offer can be used, as YYYY-MM-DD. For recurring windows \
(e.g. a monthly sale) use the last day of the final window. Use "" if no end is stated.
- Amounts printed as large stylised graphics on the poster are the ones most often misread. \
If the poster and the T&C disagree, trust the T&C. If you cannot read an amount, a minimum \
spend, or the period with confidence, set needs_review=true and say what was unclear in \
`note` — do NOT guess. A missing summary is fine; a wrong RM figure is not."""

SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "period": {"type": "string"},
            "tnc_summary": {"type": "string"},
            "end_date": {"type": "string"},
            "needs_review": {"type": "boolean"},
            "note": {"type": "string"},
        },
        "required": ["period", "end_date", "tnc_summary", "needs_review", "note"],
        "additionalProperties": False,
    },
}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, doc):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def fetch_pdf_b64(url):
    """The official T&C as a base64 document block. Optional — the poster alone is
    usually enough, and a dead link must not sink the run."""
    if not url or not url.lower().endswith(".pdf"):
        return None
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        if len(r.content) > 20_000_000:   # well under the 32MB request cap
            return None
        return base64.standard_b64encode(r.content).decode("ascii")
    except Exception as e:                # noqa: BLE001 - best effort by design
        print(f"    (T&C PDF unavailable: {type(e).__name__}) — reading the poster only")
        return None


def summarize_one(client, promo, model):
    content = []
    if promo.get("image"):
        # URL source: Anthropic fetches the poster, so nothing is downloaded here.
        content.append({"type": "image", "source": {"type": "url", "url": promo["image"]}})
    pdf = fetch_pdf_b64(promo.get("tnc_link"))
    if pdf:
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf},
        })
    content.append({
        "type": "text",
        "text": f"Campaign title: {promo.get('title', '')}\n"
                f"Source page: {promo.get('link', '')}\n\n"
                "Summarise this promotion for a dashboard card.",
    })

    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_config={"format": SCHEMA},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text), resp.usage


def apply_map(doc, mapping, force=False):
    """Fold an {id: {period, tnc_summary}} map into the feed. Shared by both modes."""
    written = []
    for p in doc.get("promotions", []):
        got = mapping.get(p.get("id"))
        if not got:
            continue
        if p.get("tnc_summary") and not force:
            continue
        summary = (got.get("tnc_summary") or "").strip()
        if not summary:
            continue
        p["tnc_summary"] = summary
        old_period = p.get("period", "")
        if got.get("period"):
            p["period"] = got["period"].strip()
        # Same rule as the promo-sync Worker: only a real YYYY-MM-DD is taken. Without
        # one, derive it from the period text — unless the period is unchanged and a
        # date (possibly hand-set) is already there.
        end = (got.get("end_date") or "").strip()
        if ISO_DATE.match(end):
            p["end_date"] = end
        elif p.get("period", "") != old_period or not p.get("end_date"):
            p["end_date"] = end_date_from_period(p.get("period", ""))
        written.append(p["id"])
    return written


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=LIVE, help="promotions.json to update in place")
    ap.add_argument("--apply", metavar="JSON",
                    help="apply an {id: {period, tnc_summary}} map instead of calling the API")
    ap.add_argument("--id", action="append", help="restrict to these promo ids (repeatable)")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--force", action="store_true",
                    help="also rewrite promos that already have a summary")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be written, change nothing")
    args = ap.parse_args()

    doc = load(args.file)
    dry = " (dry run)" if args.dry_run else ""

    if args.apply:
        written = apply_map(doc, load(args.apply), force=args.force)
        if written and not args.dry_run:
            save(args.file, doc)
        print(f"[summarize] applied {len(written)} summary/ies{dry}: {', '.join(written) or '-'}")
        return 0

    todo = [p for p in doc.get("promotions", [])
            if (args.force or not p.get("tnc_summary"))
            and (not args.id or p.get("id") in args.id)]
    if not todo:
        print("[summarize] every promo already has a summary - nothing to do.")
        return 0

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        # Not an error: the site still deploys, those cards just show the T&C link.
        print(f"[summarize] {len(todo)} promo(s) need a summary but no ANTHROPIC_API_KEY is "
              "set - skipping. Use the Card Promos Console, or --apply a summary map.")
        return 0
    try:
        import anthropic
    except ImportError:
        print("[summarize] pip install anthropic - skipping for now.")
        return 0

    client = anthropic.Anthropic()
    written, review = [], []
    for p in todo:
        print(f"[summarize] {p['id']} - {p.get('title', '')[:70]}")
        try:
            got, usage = summarize_one(client, p, args.model)
        except Exception as e:            # noqa: BLE001 - one bad promo must not abort the run
            print(f"    failed: {type(e).__name__}: {e}")
            continue
        note = (got.get("note") or "")[:140]
        if got.get("needs_review") or not got.get("tnc_summary", "").strip():
            review.append((p["id"], note))
            print(f"    left empty for review: {note}")
            continue
        if apply_map(doc, {p["id"]: got}, force=args.force):
            written.append(p["id"])
        print(f"    {got.get('period', '')} | {got['tnc_summary'][:100]}"
              f"  [{usage.input_tokens} in / {usage.output_tokens} out]")

    if written and not args.dry_run:
        save(args.file, doc)
    print(f"\n[summarize] wrote {len(written)} summary/ies{dry}.")
    if review:
        print("[summarize] needs a human eye (left blank rather than guessed):")
        for pid, note in review:
            print(f"  - {pid}: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
