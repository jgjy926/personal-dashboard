# Personal Dynamic Dashboard

One static site, four tabs, one design system. Every tab renders from a JSON feed,
so the frontend is decoupled from four very different backends and each feed can go
live independently without touching the UI.

| Tab | Feed | Status |
|---|---|---|
| 💱 **FX Rates** | live Worker API (`config.js → fxApi`) | ✅ live |
| 📈 **Macro** | `data/macro.json` | 🌱 seeded sample → Phase B backend |
| 💳 **Card Promos** | `data/promotions.json` | ✅ live — daily scrape + AI summary |
| 📊 **KLSE Monitor** | `data/klse.json` | ✅ real export from your SQLite |

## Run it locally

```bash
python -m http.server 8099
```

Then open <http://localhost:8099>. A static server is required (the tabs `fetch()`
their JSON — `file://` won't work). The FX tab calls a cross-origin Worker, which is
why this is a **static site, not a Claude Artifact** (Artifacts block cross-origin fetch).

## Files

```
index.html      shell + tab bar + one panel per tab
app.js          tab router + FX / Macro / Campaign / KLSE modules (vanilla, no deps)
styles.css      shared, theme-aware design tokens
config.js       where each tab gets its data
data/*.json     the four feeds
tools/          data producers (below)
worker/         promo-sync — the Console's publish endpoint (below)
```

## Data producers

### KLSE — real, now (`tools/export_klse.py`)
Reads the AlphaSpike SQLite **read-only** (never writes, never touches the engine) and
emits `data/klse.json` — conviction watchlist, signal hit-rate, closed-trade P&L vs
FBMKLCI, screening funnel, tracked positions.

```bash
python tools/export_klse.py                       # defaults to ../KLSE_Monitor/alphaspike_state.db
python tools/export_klse.py --db path\to.db --top 25
```

Re-run it after each engine run to refresh the tab.

### Campaign — parser + free enrichment (`tools/scrape_campaign.py`)
No paid API, $0. Each promo's `image` is the bank's real full campaign poster (not a
small crop) — the offer, minimum spend, and campaign period are printed on it directly.
Three ways to turn that into a written `tnc_summary`:

- **Console + Publish (recommended)** — Card Promos → 🛠️ Console in the dashboard itself:
  downloads a bundle of poster images + links, you paste it into a vision-capable AI
  (Claude, ChatGPT), paste or upload the JSON reply back, then hit **Publish** and the
  summaries are committed for you. See *Publishing from the Console* below.
- **Fully automatic** — `tools/summarize_campaign.py` reads each new promo's poster and
  official T&C with the Anthropic API and writes the summary itself. It runs in the daily
  workflow, so a promo that appears overnight is already summarised by morning. Needs an
  `ANTHROPIC_API_KEY` repo secret; skips itself cleanly without one.
- **CLI + OCR** — the scraper also runs local OCR (Tesseract, free, offline) on every
  poster as a fallback text layer, so `campaign_prompt.txt` works with *any* AI, not just
  a vision-capable one. OCR reads plain text well but often misses large stylized numbers
  (the RM amount is usually a big decorative graphic) — the prompt tells the AI to flag
  rather than guess those, and you should spot-check amounts against the image.

```bash
pip install requests beautifulsoup4 pytesseract Pillow
# plus the Tesseract OCR engine itself (optional — skipped gracefully if absent):
#   Windows:        winget install --id UB-Mannheim.TesseractOCR
#   Debian/Ubuntu:  sudo apt-get install tesseract-ocr   (already in the Actions workflow)
python tools/scrape_campaign.py --max 15
```

Produces `data/campaign_raw/*.txt`, `data/promotions.draft.json`, and
`data/campaign_prompt.txt`. Then: paste `campaign_prompt.txt` into Claude → get back
`{id: {period, tnc_summary}}` JSON → fill those into `promotions.draft.json` (or run
`tools/merge_campaign.py`, which does this against `data/promotions.json` and preserves
any summary already written for an id) → save as `data/promotions.json`. Done, $0.

To skip the paste entirely, let the summariser read the posters itself:

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python tools/summarize_campaign.py                    # summarise every promo missing one
python tools/summarize_campaign.py --dry-run          # show what it would write
python tools/summarize_campaign.py --apply reply.json # or apply an AI's JSON reply, no key needed
```

**New promo tomorrow?** It publishes automatically with its real poster (tap/hover shows
the full image). `merge_campaign.py` only carries forward summaries for ids it already
knows, so the summary comes from one of the three paths above — `summarize_campaign.py`
in the workflow if you've set `ANTHROPIC_API_KEY`, otherwise the Console. Amounts are
easy to get wrong from OCR or PDF text alone, so the automatic path is deliberately
allowed to decline: told to flag rather than guess, it leaves the summary blank and
names the promo in the job log, and the card falls back to the 📄 Official T&C link.

#### Publishing from the Console (`worker/promo-sync`)

The Console used to end in a download you had to drop into `data/` and commit yourself.
That wasn't a design choice — a page served from GitHub Pages is a static file with no
write endpoint, so it had no way to persist anything. `worker/promo-sync` is that
endpoint: a Cloudflare Worker that holds a GitHub token as a **Worker secret** and
commits `data/promotions.json` on the Console's behalf. The browser only ever holds the
sync passphrase, which is good for nothing but this one file.

```bash
cd worker/promo-sync
# edit wrangler.jsonc: GITHUB_REPO, ALLOWED_ORIGINS
npx wrangler secret put GITHUB_TOKEN   # fine-grained PAT · contents:write · this repo only
npx wrangler secret put SYNC_KEY       # any long passphrase; the Console asks for it once
npx wrangler deploy
```

Then point the dashboard at it — `promoSyncApi` in `config.js`, or at runtime
`localStorage.setItem('promo_sync_api','https://promo-sync.<you>.workers.dev')`.

The Console sends **only the summaries**, never the whole file: the Worker re-reads the
live `promotions.json` from GitHub and applies them to that. So publishing from a tab you
left open yesterday can't wipe promos the daily scrape has added since. It also refuses to
overwrite a summary that already exists (unless asked), reports unknown ids instead of
failing, and treats a repeated publish as a no-op.

`node worker/promo-sync/test.mjs` exercises all of that offline against a stubbed GitHub —
no deploy, no token, no network.

### Macro — seed now (`tools/seed_macro.py`), backend later
`python tools/seed_macro.py` regenerates the sample `data/macro.json`. The Phase-B
backend must emit this same shape (see below).

### US Treasury supply (`tools/fetch_treasury.py`)
Fully automated, free, keyless — runs in the same daily workflow:

```bash
python tools/fetch_treasury.py     # -> data/treasury.json
```

- **Upcoming auctions** (TreasuryDirect) — genuinely forward-looking: auctions
  announced but not yet held, with auction date, settlement date, security type/term,
  and the coupon when it's a reopening. This is the "when's the next new bond" answer.
- **Recent buybacks** (Treasury Fiscal Data) — operation date, purpose
  (Liquidity Support / Cash Management), maturity bucket, par accepted.

**Deliberate limitation:** the buyback feed contains **completed operations only**.
Treasury publishes forward buyback calendars solely inside quarterly-refunding PDFs,
not as a structured feed, so the panel never claims a "next buyback" date it can't know.

**No Japan equivalent:** MoF's JGB auction calendar exists only as per-month HTML
sub-pages with no CSV/Excel/PDF data files (checked), so there's no clean feed to
mirror this. Not scraped rather than half-built on fragile markup.

## JSON contracts (so Phase-B producers are drop-in)

- **`macro.json`** — `meta`, `snapshot[]` (`{id,label,value,unit,change,as_of,freq}`),
  `overlay{dates[], series{real_yield[],gold[],sp500[]}}`, `lag{lead_months,dates[],unemployment[],real_yield_lead[]}`,
  `regime{label,detail,caveat}`.
- **`promotions.json`** — `meta` (incl. `today`), `promotions[]` (`{id,title,image,link,category,period,first_seen,tnc_summary}`). `first_seen` = the date a promo first appeared on the page; a promo whose `first_seen` equals `meta.today` is flagged **NEW** and surfaced in the "new today" banner. The Card Promos tab filters by `category`.
- **`treasury.json`** — `meta{sources,note}`, `upcoming_auctions[]`
  (`{auction_date,issue_date,maturity_date,security_type,term,rate,cusip}`),
  `recent_buybacks[]` (`{operation_date,settlement_date,operation_type,maturity_bucket,par_accepted}`).
- **`klse.json`** — `meta`, `funnel{run_date,stages[]}`, `conviction{run_date,rows[]}`,
  `signals{hit_rate,resolved,total,avg_fwd_ret_20,recent[]}`, `trades{count,win_rate,avg_alpha_pct,recent[]}`, `positions[]`.

## Phase B — going live

### Macro
Build the Cloudflare Worker + D1 + KV per `macro-dashboard-build-plan.md`: daily cron
fetches FRED (free key) + Stooq, upserts to D1, runs the one-time backfill, and either
serves `/api/*` or writes a `macro.json` in this shape. **You** register the FRED key
and deploy (`wrangler`).

### Campaign
Done — `.github/workflows/refresh.yml` runs `scrape_campaign.py` → `merge_campaign.py` →
`summarize_campaign.py` daily and commits `promotions.json`. The scrape and merge stay
$0; only the optional auto-summary step costs anything (cents per *new* promo, and only
when `ANTHROPIC_API_KEY` is set). Without that secret the pipeline is still fully
automatic apart from the summary itself, which you write via the Console's Publish button.

### KLSE → online (chosen: publish the full Streamlit app)

The full 3-tab AlphaSpike dashboard (Conviction · Exit Advisor · Signal ledger) is
published **as-is** — keeping its interactivity (position writes, ad-hoc lookups) —
by exposing the local Streamlit app through a **free Cloudflare Quick Tunnel**, gated
by a server-side access key. Entirely $0 (a custom domain is optional). Full steps:
[`KLSE_Monitor/deploy/ONLINE_SETUP.md`](../KLSE_Monitor/deploy/ONLINE_SETUP.md).

Once you have the tunnel URL, expose an "Open full interactive dashboard ↗" button on
this dashboard's KLSE tab by setting it in `config.js` (`klseDashboardUrl`) or at
runtime: `localStorage.setItem('klse_url','https://…trycloudflare.com')`. The KLSE tab
here remains the fast **read-only snapshot** (from `data/klse.json`); the button hands
off to the live interactive app for anything that writes.

### KLSE JSON snapshot → cloud (the read-only tab, if you host this dashboard)

The dashboard fetches `data/klse.json` **same-origin**, so the only job is getting the
exporter's JSON to wherever the site is hosted. Ranked simplest-first:

1. **Commit to the repo the site is served from (recommended).** After the engine run:
   `python tools/export_klse.py && git add data/klse.json && git commit -m "klse" && git push`.
   Cloudflare Pages / GitHub Pages redeploys automatically. Same-origin, **no keys, no
   CORS, no proxy**. A 2-line scheduled task next to your existing 17:30 EOD job.
2. **Cloudflare KV + a tiny Worker route** (if you want it fully on Cloudflare). Exporter
   `PUT`s the JSON to KV via the REST API (token in a local secret); a Worker `/api/klse`
   returns it with a CORS header. This mirrors the "topology B" writer your `personal rate`
   project already uses — reuse that pattern. Needs a Cloudflare API token.
3. **Koofr (WebDAV)** — workable but the extra-wrinkle option: Koofr's WebDAV isn't
   browser-CORS-friendly, so the page can't `fetch()` it directly — you'd need a small
   CORS-proxy Worker in front. Since option 1 already gives you free same-origin hosting,
   Koofr only makes sense if you specifically want your files living in Koofr; otherwise skip it.

**Recommendation:** option 1 now (zero moving parts); option 2 if/when you consolidate
everything onto Cloudflare. The exporter output is identical either way.

## Notes / guardrails
- Every tab shows data freshness and degrades gracefully (last-good value, never a blank pane).
- Macro regime flag always carries *"heuristic, not a signal."*
- Campaign cards carry *"AI summary — verify official T&C"* + a source link.
- KLSE carries *"screener output, not trade advice"* + the export timestamp (it's a local snapshot).
- Override the FX Worker URL at runtime: `localStorage.setItem('fx_api','https://your-worker.workers.dev')`.
