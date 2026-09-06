/* Single source of truth for where each tab gets its data.
 * Every tab renders from a JSON feed: Tab 1 (FX) from the live Worker API;
 * Tabs 2–3 from local data/*.json. Override the FX API at runtime via
 *   localStorage.setItem('fx_api', 'https://your-worker.workers.dev')
 *
 * The KLSE Monitor tab was removed when the Macro tab became the full
 * forecasting engine; AlphaSpike keeps its own Streamlit dashboard.
 */
window.DASH_CONFIG = {
  fxApi: (localStorage.getItem('fx_api') || 'https://fx-dashboard.jgjy926.workers.dev'),
  feeds: {
    // The forecasting engine's full snapshot: regime, recession probabilities,
    // factors, scenarios, market implications, model performance, data health.
    // Written by `python main.py daily` in the sibling "macro dashboard" repo.
    macroEngine: 'data/macro_engine.json',
    // The original series monitor (yields / gold / equities overlay), kept as
    // the engine's "Series Monitor" sub-page. Written by tools/fetch_macro.py.
    macro: 'data/macro.json',
    treasury: 'data/treasury.json',
    promotions: 'data/promotions.json'
  }
};
