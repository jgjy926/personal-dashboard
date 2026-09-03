# Personal Dynamic Dashboard

One static site, four tabs, one design system. Every tab renders from a JSON feed,
so the frontend is decoupled from four very different backends and each feed can go
live independently without touching the UI.

| Tab | Feed | Status |
|---|---|---|
| 💱 **FX Rates** | live Worker API (`config.js → fxApi`) | ✅ live |
| 📈 **Macro** | `data/macro.json` | 🌱 seeded sample → Phase B backend |
| 💳 **Card Promos** | `data/promotions.json` | 🌱 seeded sample → parser + manual LLM |
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

### Campaign — parser + free manual summary (`tools/scrape_campaign.py`)
No LLM API, no cost. The parser scrapes each Public Bank promo's title/image/link and
**downloads the raw T&C**, then writes a paste-ready prompt so you summarise for free:

```bash
pip install requests beautifulsoup4
python tools/scrape_campaign.py --max 15
```

Produces `data/campaign_raw/*.txt`, `data/promotions.draft.json`, and
`data/campaign_prompt.txt`. Then: paste `campaign_prompt.txt` into Claude → get back
`{id: {period, tnc_summary}}` JSON → fill those into `promotions.draft.json` → save as
`data/promotions.json`. Done, $0.

### Macro — seed now (`tools/seed_macro.py`), backend later
`python tools/seed_macro.py` regenerates the sample `data/macro.json`. The Phase-B
backend must emit this same shape (see below).

## JSON contracts (so Phase-B producers are drop-in)

- **`macro.json`** — `meta`, `snapshot[]` (`{id,label,value,unit,change,as_of,freq}`),
  `overlay{dates[], series{real_yield[],gold[],sp500[]}}`, `lag{lead_months,dates[],unemployment[],real_yield_lead[]}`,
  `regime{label,detail,caveat}`.
- **`promotions.json`** — `meta` (incl. `today`), `promotions[]` (`{id,title,image,link,category,period,first_seen,tnc_summary}`). `first_seen` = the date a promo first appeared on the page; a promo whose `first_seen` equals `meta.today` is flagged **NEW** and surfaced in the "new today" banner. The Card Promos tab filters by `category`.
- **`klse.json`** — `meta`, `funnel{run_date,stages[]}`, `conviction{run_date,rows[]}`,
  `signals{hit_rate,resolved,total,avg_fwd_ret_20,recent[]}`, `trades{count,win_rate,avg_alpha_pct,recent[]}`, `positions[]`.

## Phase B — going live

### Macro
Build the Cloudflare Worker + D1 + KV per `macro-dashboard-build-plan.md`: daily cron
fetches FRED (free key) + Stooq, upserts to D1, runs the one-time backfill, and either
serves `/api/*` or writes a `macro.json` in this shape. **You** register the FRED key
and deploy (`wrangler`).

### Campaign
Schedule `scrape_campaign.py` (GitHub Actions cron), do the free LLM summary step, commit
`promotions.json`. No paid API.

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
