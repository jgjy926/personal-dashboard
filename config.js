/* Single source of truth for where each tab gets its data.
 * Every tab renders from a JSON feed: Tab 1 (FX) from the live Worker API;
 * Tabs 2–4 from local data/*.json (swap these for live producers in Phase B
 * with NO frontend change). Override the FX API at runtime via
 *   localStorage.setItem('fx_api', 'https://your-worker.workers.dev')
 */
window.DASH_CONFIG = {
  fxApi: (localStorage.getItem('fx_api') || 'https://fx-dashboard.jgjy926.workers.dev'),
  feeds: {
    macro: 'data/macro.json',
    promotions: 'data/promotions.json',
    klse: 'data/klse.json'
  },
  // Full interactive AlphaSpike dashboard (Streamlit via Cloudflare Tunnel).
  // Paste the tunnel URL here (or set localStorage 'klse_url') to show an
  // "Open full dashboard ↗" button on the KLSE tab. Leave blank to hide it.
  klseDashboardUrl: (localStorage.getItem('klse_url') || '')
};
