# Personal Dynamic Dashboard — BA Review & Build Plan

*Prepared as: BA (requirements) + Senior Dev (architecture) + Product + QA. For your review before any build.*

**Target folder:** `C:\Users\jgjy8\Downloads\Claude\Personal Dynamic Dashboard\` (currently empty — the home for the new unified dashboard).

---

## 1. BA Review — what each source actually is

I read all four references in full. They are at **very different stages**, which is the single most important fact driving the plan.

| Tab | Source | State today | Backend | Data locality |
|---|---|---|---|---|
| **1 — Personal Rate (FX)** | `personal rate/` (spec + full code) | ✅ **Built & deployed** | Cloudflare Worker (live at `fx-dashboard.jgjy926.workers.dev`) + D1 | Live web API |
| **2 — Macro** | `macro-dashboard-build-plan.md` (spec only) | 📄 Spec, **not built** | Cloudflare Worker + D1 + KV (to build) | Needs FRED API key + Stooq |
| **3 — Public Campaign** | `Public_Campaign_Dashboard.txt` (spec + sample code) | 📄 Spec, **not built** | Python scraper + Gemini LLM (to build) | Needs Gemini key + GitHub Actions |
| **4 — KLSE Monitor** | `KLSE_Monitor/` (full Python engine) | ✅ **Built**, but **no web UI/API** | Local Python engine, local SQLite | Local file — needs an exporter |

### Tab 1 — Personal Rate (FX) — *the design north-star*
Fully working. `pages/` frontend calls a deployed Worker: `/api/pairs`, `/api/rates?base=MYR&quote=USD`, `/api/history?...&days=30`. Shows **Market / Visa / Mastercard / Wise** side-by-side, MYR-based, with staleness badges (green ≤1d / amber 2–3d / red >3d), a 30-day inline-SVG chart, a converter, and a travel quick-reference table. Zero dependencies, theme-aware (light/dark). **This is the visual language I'll unify the whole dashboard on.**

### Tab 2 — Macro
Spec defines: snapshot cards (10Y nominal yield, real yield, gold, S&P 500, unemployment, dollar index, home-price index), an **overlay chart** (real yield / gold / S&P indexed to 100), a **lag panel** (unemployment vs real yield shifted 12–18mo), a **regime-flag banner** that must always read *"heuristic, not a signal"*, and a freshness footer that shows **data date, not fetch date**. Data from FRED (free key) + Stooq (no key); history needs a one-time backfill script.

### Tab 3 — Public Campaign
Spec + sample code: a scheduled **Python scraper** of Public Bank card promotions + **Gemini** to summarise each promo's T&C → `promotions.json` → a grid of cards (image, title, T&C summary, "View full promotion" link). The `.txt` is a bit garbled (concatenated), but intent is clear.

### Tab 4 — KLSE Monitor
A complete algorithmic screener (AlphaSpike v4) with a rich **local SQLite**: `conviction_scores` (daily ranked watchlist with reasons/risks), `signal_ledger` (signals + forward outcomes → hit-rate/expectancy), `active_trades` / `trade_log` (P&L vs FBMKLCI), `run_log` (screening funnel), `user_positions` (Exit Advisor). **There is no web frontend or API today** — the data sits in a local `.db` file. To show it on a dashboard, we need a small **exporter** (SQLite → JSON).

---

## 2. The unifying architecture (Senior Dev)

The four tabs have four different backends and two different data localities (live web API, and a local file). Trying to make the frontend talk to each backend natively would couple it to four moving parts.

**The clean contract: every tab renders from a JSON feed.**

```
 Tab 1 (FX)       → live Worker API      (exists, cross-origin)
 Tab 2 (Macro)    → data/macro.json      (seed now → Worker later)
 Tab 3 (Campaign) → data/promotions.json (seed now → scraper later)
 Tab 4 (KLSE)     → data/klse.json       (real exporter from your SQLite)
```

One frontend, four JSON contracts. This lets us **ship the entire UI now** and swap each seed feed for a live producer later, with **no frontend changes**.

### Important constraint: this is a static site, **not** a Claude Artifact
The dashboard makes **cross-origin** calls (Tab 1 → `workers.dev`, and later Tab 3 → GitHub raw). Claude Artifacts run under a Content-Security-Policy that **blocks arbitrary cross-origin fetch** — Tab 1's live rates would silently fail there. So the build target is a **local static site** in the project folder, runnable locally and deployable to Cloudflare Pages / GitHub Pages (same free-tier target every source assumes). I'll run it on a local preview server to show it working.

### Tech choice
Plain **HTML + vanilla JS + CSS custom properties**, extending the FX dashboard's existing design tokens — zero build step, zero dependencies, theme-aware, matches what's already polished. (The campaign `.txt` used Tailwind's dark theme; I'll port its grid into the shared design system rather than bolt on a second styling approach.)

---

## 3. Proposed build — phased

### Phase A — Unified 4-tab dashboard (this build, the reviewable milestone)
1. **Scaffold** `Personal Dynamic Dashboard/`: `index.html`, `app.js` (tab router + one module per tab), `styles.css` (shared tokens), `data/` (seed feeds), `tools/export_klse.py`, `README.md`.
2. **Shared shell:** top tab bar (💱 FX · 📈 Macro · 💳 Campaign · 📊 KLSE), responsive, light/dark, per-tab freshness line, graceful *"awaiting live feed"* empty states (never blank, never a raw error).
3. **Tab 1 — FX:** port the existing view, **live** against the deployed Worker. Working end-to-end on day one.
4. **Tab 2 — Macro:** snapshot cards + indexed overlay chart + lag note + regime banner (with the mandatory *"heuristic, not a signal"* caveat), from `data/macro.json` seeded to the spec's schema.
5. **Tab 3 — Campaign:** promo-card grid from `data/promotions.json` (seeded sample), each card linking to source with an *"AI summary — verify official T&C"* caveat.
6. **Tab 4 — KLSE:** today's conviction watchlist (top N with conviction, reasons/risks), signal performance (hit-rate / expectancy), open positions, last-run funnel + freshness — from `data/klse.json`. **Plus a real `tools/export_klse.py`** that reads your local SQLite and writes that JSON, so this tab can show *your actual* screener data, not just a sample.
7. **Document each JSON contract**, so Phase-B producers only have to emit the agreed shape.

### Phase B — wire the live backends (follow-up; I scaffold, you own keys + deploy)
- **Macro:** Cloudflare Worker + D1 + KV, FRED fetchers + Stooq, one-time backfill → emits the `macro.json` shape (or `/api/*`).
- **Campaign:** Python scraper + Gemini + GitHub Actions on a cron → commits `promotions.json`.
- **KLSE:** schedule `export_klse.py` next to the engine run (or a tiny read-only API).

I can write the Phase-B scaffolds, but **registering FRED/Gemini keys and deploying to your Cloudflare/GitHub accounts are yours to do** (those need your credentials — I'll give exact commands).

---

## 4. QA / Product guardrails (baked into the UI copy, not an afterthought)
- Every tab shows **data freshness** and **degrades gracefully** — last-good value greyed + badged, never a blank pane.
- Macro regime flag **always** carries *"heuristic, not a signal."*
- Macro freshness shows **data date, not fetch date** (monthly series lag weeks).
- Campaign cards carry *"AI-generated summary — verify official T&C"* + a link to the real page.
- KLSE carries *"screener output, not trade advice"* + the export timestamp (it's a local snapshot).
- One consistent design system across all four tabs.

---

## 5. Cost & risk
- **Cost:** $0 — static hosting (Pages/GitHub), the existing free Worker, FRED/Stooq free, Gemini free tier, GitHub Actions free minutes. No paid dependency.
- **Risks to flag:** Campaign scraping depends on Public Bank's page structure (fragile selectors, ToS) and an LLM paraphrase of legal T&C (hence the caveat + source link); Tab 4 data is a **local** export, so the dashboard is only as fresh as the last exporter run.

---

## 6. Decisions I need from you
1. **How much to build now** — Phase A only, or Phase A + Phase B scaffolds?
2. **Tab 4 data** — build the real SQLite→JSON exporter against your live DB, or just seed a sample for now?

Everything else (target folder, static-site-not-Artifact, unified FX design system) I'll proceed with as recommended above unless you say otherwise.
