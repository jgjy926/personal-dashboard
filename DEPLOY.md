# Deploy — put the dashboard online ($0)

The dashboard is a static site; each tab reads a JSON feed. Getting it online means
hosting those files somewhere public — with **two rules**:

1. **The data fetchers must run where egress is clean** (GitHub Actions runners, or
   your own machine). A restricted sandbox can't reach FRED/Public Bank; GitHub
   Actions can. The included workflow does this for you.
2. **KLSE stays private.** `data/klse.json` is git-ignored, so it is never committed
   or published. Online, the KLSE tab shows a locked hand-off to your access-keyed
   AlphaSpike tunnel (see `KLSE_Monitor/deploy/ONLINE_SETUP.md`); the rich snapshot
   only appears when you run the dashboard locally (where the file exists).

FX is live from your Worker; Macro and Card Promos become **real** as soon as the
workflow runs (FRED works from Actions).

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
