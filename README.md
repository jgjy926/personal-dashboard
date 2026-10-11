# Personal Dynamic Dashboard

One static site, several tabs, one design system. Every tab renders from a JSON feed,
so the frontend is decoupled from four very different backends and each feed can go
live independently without touching the UI.

| Tab | Feed | Status |
|---|---|---|
| 💱 **FX Rates** | live Worker API (`config.js → fxApi`) | ✅ live |
| 📈 **Macro** | `data/macro.json` | 🌱 seeded sample → Phase B backend |
| 💳 **Card Promos** | `data/promos/*.json` (one per bank) | ✅ live — Public Bank, HLB, UOB · daily scrape + AI summary |
| 📊 **KLSE Monitor** | `data/klse.json` | ✅ real export from your SQLite |
| 🌫️ **Air Quality** | live Worker (`config.js → apimsApi`), `data/apims.json` fallback | ✅ live — DOE APIMS, ≤10 min behind |

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

### Card Promos — one feed per source (`data/promos/`)
The tab reads one JSON file per source, listed in `config.js` (`feeds.promos`), and
filters them by **bank** and category:

| Feed | Scraper | What it holds |
|---|---|---|
| `pbb.json` | `tools/scrape_campaign.py` | Public Bank credit + debit card campaigns |
| `hlb.json` | `tools/scrape_hlb.py` | HLB promos tagged **Cards** (read from the JSON servlet behind hlb.com.my's promotions page) |
| `uob.json` | `tools/scrape_uob.py` | UOB's featured card campaigns |
| `uob-deals.json` | `tools/scrape_uob.py` | UOB card merchant deals (Dining / Shopping / Travel / Lifestyle / Others) — shown under **Merchant deals** |

Each scraper writes only its own `data/promos/<feed>.draft.json` (git-ignored) and
`tools/merge_campaign.py` folds every draft into its live file, so one bank changing its
page or being down never touches another bank's promos or summaries, and the daily churn
of merchant deals doesn't bury campaign changes in the git history. Shared helpers live
in `tools/promo_feed.py`. Merchant deals need no AI: the bank prints the offer and
"Offer period" on each deal card, so that text is the summary (`summary_source: "bank"`).
To add a bank: write `tools/scrape_<bank>.py` producing the same shape, add its file to
`config.js`, `PROMO_FILES` in `worker/promo-sync/wrangler.jsonc` (campaign feeds only)
and the scrape step in `refresh.yml`.

```bash
python tools/scrape_campaign.py --max 15   # Public Bank (details below)
python tools/scrape_hlb.py
python tools/scrape_uob.py
python tools/merge_campaign.py
```

### Campaign summaries — parser + free enrichment
No paid API, $0. Each promo's `image` is the bank's real full campaign poster (not a
small crop) — the offer, minimum spend, and campaign period are printed on it directly.
Three ways to turn that into a written `tnc_summary`:

- **Console + Publish (recommended)** — Card Promos → 🛠️ Console in the dashboard itself:
  one click saves `card-promos-ai-bundle.json` **plus every unsummarised promo's poster as
  a .jpg**, so you can attach the images themselves to a vision-capable AI (Claude,
  ChatGPT) — that's what it needs to read an offer. Paste or upload its JSON reply back,
  then hit **Publish** and the summaries are committed for you. Each poster is named
  `NN-<title>-<id>.jpg` and cross-referenced from the JSON as `poster_file`. See
  *Publishing from the Console* below.
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

`--max` is the window for promos that have **no summary yet**, applied to the credit and
debit listings **each** (the credit one alone lists 130+, so a single window over both
never reached debit-only promos). A promo you've already summarised stays in the feed for
as long as the bank still lists it, however far down new promos push it.

Produces `data/campaign_raw/*.txt`, `data/promos/pbb.draft.json`, and
`data/campaign_prompt.txt`. `tools/merge_campaign.py` folds the draft into
`data/promos/pbb.json`, preserving any summary already written for an id. For the free
path, paste `campaign_prompt.txt` into Claude → get back `{id: {period, tnc_summary}}`
JSON → `python tools/summarize_campaign.py --apply reply.json`. Done, $0.

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
commits the campaign feeds in `data/promos/` on the Console's behalf. The browser only
ever holds the sync passphrase, which is good for nothing but those files.

```bash
cd worker/promo-sync
# edit wrangler.jsonc: GITHUB_REPO, ALLOWED_ORIGINS
npx wrangler secret put GITHUB_TOKEN   # fine-grained PAT · contents:write · this repo only
npx wrangler secret put SYNC_KEY       # any long passphrase; the Console asks for it once
npx wrangler deploy
```

Then point the dashboard at it — `promoSyncApi` in `config.js`, or at runtime
`localStorage.setItem('promo_sync_api','https://promo-sync.<you>.workers.dev')`.

The Console sends **only the summaries**, never a whole file, and doesn't say which bank
each id belongs to: the Worker re-reads every feed in `PROMO_FILES` from GitHub, applies
each summary to the file that holds its id, and commits only the files that changed. So publishing from a tab you
left open yesterday can't wipe promos the daily scrape has added since. It also refuses to
overwrite a summary that already exists (unless asked), reports unknown ids instead of
failing, and treats a repeated publish as a no-op.

The same Worker also serves `GET /poster?url=…`, which is what lets the download button
hand you the poster **images** and not just their URLs. The bank's image server sends no
`Access-Control-Allow-Origin`, so the page may display a poster in an `<img>` but may not
read its bytes; this route re-serves it with CORS. It needs no sync key (public marketing
images) and only fetches from the hosts in `POSTER_HOSTS` (default `www.pbebank.com`, `www.hlb.com.my`, `www.uob.com.my`), so
it can't be pointed at anything else. Without it the button still saves the JSON and says
so — the AI then has URLs it probably can't open.

`node worker/promo-sync/test.mjs` exercises all of that offline against a stubbed GitHub —
no deploy, no token, no network.

### Air Quality — live (`worker/apims-proxy`, fallback `tools/fetch_apims.py`)
Malaysia's Air Pollutant Index for every DOE continuous monitoring station (68),
latest reading + last 24 hours, from the keyless JSON endpoints behind
<https://eqms.doe.gov.my/APIMS/main>. stdlib-only, ~1s:

```bash
python tools/fetch_apims.py     # -> data/apims.json
```

The API only answers CORS for DOE's own site, so the browser can't call it directly.
The tab reads it **live** from `worker/apims-proxy`, a keyless Cloudflare Worker that
serves the same shape as `data/apims.json` with CORS, caching for 10 minutes
(stale-while-revalidate). Updates run 6 AM-11 PM MYT; overnight (00:00-06:00) it makes no APIMS calls and serves the evening copy. Deploy: `cd worker/apims-proxy && npx wrangler deploy`;
`node worker/apims-proxy/test.mjs` checks its parser offline.

The live path exists because **GitHub's cron can't do hourly here** (scheduled runs on
this repo start hours late or not at all), and because redeploying the site for every
new reading would mix data into the site's version. `refresh.yml` still writes a
fallback `data/apims.json` on each deploy (uncommitted, git-ignored;
`tools/stage_site.sh` carries the live one forward on a failed fetch). The tab uses
whichever of the Worker or that snapshot has the newer readings, and says which.

### Version badge (`tools/stamp_version.py`)
The header badge shows two lines, each `<MYT date>-<n>` where n counts that kind of
commit on that day:

- **Code** `v2026.10.11-4` — the 4th commit on 11 Oct that changed the page itself.
  Commits that only touch `data/`, `tools/`, `worker/`, `.github/` or docs don't count.
- **Data** `2026.10.11-2` — the 2nd commit on 11 Oct that changed anything under `data/`
  (the daily refresh, a summary published from the Console).

Click it for each one's commit hash, subject and time. Per-feed freshness still lives on
each tab ("Readings as of…", "updated…").

Each deploy writes `version.json` and stamps the page's `<meta name="build-id">`; an open
tab re-checks every 5 min and shows **↻ New version — reload** only when newer code is
live. The `?v=` cache-busters follow the same hash, so code changes always reach the
browser and data-only deploys never force a re-download. Served locally the badge reads
*local dev build*. (Needs full git history in CI: `fetch-depth: 0`.)

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
  `overlay{dates[], series{real_yield[],gold[],sp500[]}}`, `oil{dates[],series{brent[],wti[]},unit,note}`,
  `fx{dates[],series{usdjpy[]},unit,note}`, `lag{lead_months,dates[],unemployment[],real_yield_lead[]}`,
  `regime{label,detail,caveat}`. `oil` and `fx` are each single-unit, so the frontend plots them on a
  real axis rather than the 0-100 rescale `overlay` needs; both blocks are optional and the monitor
  omits the chart if absent.
- **`promos/<feed>.json`** — `meta{bank,bank_name,kind,source,today,count,…}` (`kind` is `campaign` or `deal`), `promotions[]` (`{id,bank,title,image,link,category,subcategory?,desc?,period,end_date,first_seen,tnc_link,tnc_summary,summary_source?}`). `end_date` (YYYY-MM-DD, or empty when unknown) is the last day the offer can be used — written with the summary, or derived from `period` by `merge_campaign.py`. Within 7 days the card shows ⏳ *Ends in N days*; once it has passed, the promo is hidden behind a **Show expired** toggle rather than deleted (only the bank delisting a promo removes it from the feed). `first_seen` = the date a promo first appeared on the page; a promo whose `first_seen` equals `meta.today` is flagged **NEW** and surfaced in the "new today" banner. The Card Promos tab filters by bank and `category`, with campaigns and merchant deals as two views.
- **`treasury.json`** — `meta{sources,note}`, `upcoming_auctions[]`
  (`{auction_date,issue_date,maturity_date,security_type,term,rate,cusip}`),
  `recent_buybacks[]` (`{operation_date,settlement_date,operation_type,maturity_bucket,par_accepted}`).
- **`apims.json`** — `meta{generated_at,readings_as_of,source_url,station_count,reporting,categories}`,
  `stations[]` (`{id,location,state,lat,lon,api,pollutant,category,at,history[{t,api}]}`); times are
  `+08:00`, `api: null` means no reading (never 0).
- **`klse.json`** — `meta`, `funnel{run_date,stages[]}`, `conviction{run_date,rows[]}`,
  `signals{hit_rate,resolved,total,avg_fwd_ret_20,recent[]}`, `trades{count,win_rate,avg_alpha_pct,recent[]}`, `positions[]`.

## Phase B — going live

### Macro
Build the Cloudflare Worker + D1 + KV per `macro-dashboard-build-plan.md`: daily cron
fetches FRED (free key) + Stooq, upserts to D1, runs the one-time backfill, and either
serves `/api/*` or writes a `macro.json` in this shape. **You** register the FRED key
and deploy (`wrangler`).

### Campaign
Done — `.github/workflows/refresh.yml` runs the three scrapers (each best-effort) →
`merge_campaign.py` → `summarize_campaign.py` daily and commits `data/promos/`. The scrape and merge stay
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
