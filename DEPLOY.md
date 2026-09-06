# Deploy — put the dashboard online ($0)

The dashboard is a static site; each tab reads a JSON feed. Getting it online means
hosting those files somewhere public — with **two rules**:

1. **The data fetchers must run where egress is clean** (GitHub Actions runners, or
   your own machine). A restricted sandbox can't reach FRED/Public Bank; GitHub
   Actions can. The included workflow does this for you.
2. **Nothing private is published.** The KLSE Monitor tab was removed when the Macro
   tab became the full forecasting engine, so there is no longer a private trading
   feed in this repo at all. The Macro Engine's `data/macro_engine.json` is public
   economic data plus this model's own output — safe to publish.

FX is live from your Worker; Macro and Card Promos become **real** as soon as the
workflow runs (FRED works from Actions).

---

## The Macro Engine in CI

The Macro tab is driven by the sibling **macro forecasting engine**, which the
workflow checks out and runs daily. Two settings make it work:

| Where | Name | Value |
|---|---|---|
| Settings → Secrets and variables → Actions → **Variables** | `MACRO_ENGINE_REPO` | `your-user/macro-dashboard` |
| Settings → Secrets and variables → Actions → **Secrets** | `MACRO_ENGINE_TOKEN` | a PAT with `repo` scope — only if the engine repo is private |

If the engine lives inside *this* repo instead, delete the "Check out the macro
engine" step and point the later steps at its directory.

### Why the database is not committed

The full engine database is ~214MB, and 160MB of that is the ALFRED vintage store
(1.2M rows) used to run look-ahead-free backtests. That store is needed to
**produce** a backtest, never to **display** one. So:

- CI keeps a **light** database (observations only, ~55MB) in the Actions cache.
  A cache miss just costs a ~2-minute full pull from FRED.
- The **backtest runs on your machine** when you want it refreshed:

  ```bash
  cd "macro dashboard" && python main.py monthly
  ```

  That writes a ~25KB `data/backtest_result.json` into this repo, which you
  commit. The engine reads it whenever the database has no backtest summary, so
  the published Model Performance page stays complete without the big database
  ever leaving your machine.

The daily CI job therefore runs in a few minutes and the repository carries no
binary blob.

---

## Option A — GitHub Pages + Actions (recommended: $0, auto-refresh, no secrets)

From the dashboard folder:

```bash
cd "Personal Dynamic Dashboard"
git init -b main
git add .
git commit -m "Personal Dynamic Dashboard"
gh repo create personal-dashboard --private --source=. --push
```

(or create the repo on github.com and `git remote add origin … && git push -u origin main`).

Then in the repo: **Settings → Pages → Build and deployment → Source = GitHub Actions.**

That's it. On every push and daily at 06:20 UTC the workflow (`.github/workflows/refresh.yml`)
pulls fresh FRED macro data, refreshes promos, commits the public feeds, and publishes to:

```
https://<your-user>.github.io/personal-dashboard/
```

- **Show the KLSE launch button:** edit `config.js` → set `klseDashboardUrl` to your
  Cloudflare Tunnel URL, commit, push. (Or per-browser: `localStorage.setItem('klse_url','https://…')`.)
- **A repo can be private and still publish a public Pages site** — that's fine; no
  private data is in the repo (klse.json is ignored). If you'd rather the Pages site
  itself be gated, put Cloudflare Access in front of a custom domain (needs a domain).

## Option B — Cloudflare Pages (one command; you already use Cloudflare)

```bash
cd "Personal Dynamic Dashboard"
npx wrangler pages deploy . --project-name personal-dashboard
```

Gives a `https://personal-dashboard.pages.dev` URL. Re-run to update, or connect the
GitHub repo in the Cloudflare dashboard for auto-deploys. Run the fetchers first
(`python tools/fetch_macro.py`) so Macro is real; don't upload `data/klse.json`
(the deploy respects `.gitignore` when deploying from a git checkout).

---

## FX cross-origin note
The FX tab calls your Worker from the Pages origin. It already answers cross-origin
(it worked from `localhost` in testing), so it will work from `github.io` / `pages.dev`
too. If you ever lock the Worker's `Access-Control-Allow-Origin` down, add your Pages
origin to its allow-list.

## Verify locally first
```bash
python tools/fetch_macro.py           # real macro (needs open egress)
python tools/export_klse.py           # real KLSE snapshot (local only)
python -m http.server 8099            # open http://localhost:8099
```
