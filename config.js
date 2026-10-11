/* Single source of truth for where each tab gets its data.
 * Every tab renders from a JSON feed: Tab 1 (FX) from the live Worker API;
 * Tabs 2–4 from local data/*.json. Override the FX API at runtime via
 *   localStorage.setItem('fx_api', 'https://your-worker.workers.dev')
 *
 * The KLSE Monitor tab was removed when the Macro tab became the full
 * forecasting engine; AlphaSpike keeps its own Streamlit dashboard.
 */
window.DASH_CONFIG = {
  fxApi: (localStorage.getItem('fx_api') || 'https://fx-dashboard.jgjy926.workers.dev'),
  // Write endpoint for the Card Promos Console's "Publish" button — the
  // worker/promo-sync Worker, which commits data/promotions.json on your behalf
  // (it holds the GitHub token; the browser only ever holds the sync key).
  // Deployed and live; override per-browser the same way as fxApi:
  //   localStorage.setItem('promo_sync_api', 'https://promo-sync.<you>.workers.dev')
  // Leaving this blank makes Publish fall back to the download-and-commit flow.
  promoSyncApi: (localStorage.getItem('promo_sync_api') || 'https://promo-sync.jgjy926.workers.dev'),
  // Live air-quality feed — the worker/apims-proxy Worker, which serves APIMS
  // in the data/apims.json shape with CORS (refreshed within ~10 min of DOE
  // publishing). The tab falls back to feeds.apims when this is unreachable.
  // Override per-browser: localStorage.setItem('apims_api', 'https://…/apims')
  apimsApi: (localStorage.getItem('apims_api') || 'https://apims-proxy.jgjy926.workers.dev/apims'),
  feeds: {
    // The forecasting engine's full snapshot: regime, recession probabilities,
    // factors, scenarios, market implications, model performance, data health.
    // Written by `python main.py daily` in the sibling "macro dashboard" repo.
    macroEngine: 'data/macro_engine.json',
    // The original series monitor (yields / gold / equities overlay), kept as
    // the engine's "Series Monitor" sub-page. Written by tools/fetch_macro.py.
    macro: 'data/macro.json',
    treasury: 'data/treasury.json',
    promotions: 'data/promotions.json',
    // Malaysia Air Pollutant Index — the FALLBACK for apimsApi above. Written by
    // tools/fetch_apims.py on each deploy (refresh.yml), not committed.
    apims: 'data/apims.json'
  }
};
