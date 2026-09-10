/* Personal Dynamic Dashboard — tab router + three tab modules.
 * Each tab renders from a JSON feed and lazy-initialises on first view.
 * Zero dependencies; charts are hand-rolled inline SVG.
 *
 * The Macro tab is the front end for the sibling macro forecasting engine and
 * lives in macro-engine.js (ten sub-pages); this file keeps the original series
 * monitor, which the engine shows as its "Series Monitor" sub-page. */
'use strict';
const CFG = window.DASH_CONFIG;
const COLOR = getComputedStyle(document.documentElement);
const cv = n => COLOR.getPropertyValue(n).trim();

// ── shared utils ──────────────────────────────────────────────────────────
async function fetchJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function fmt(n) {
  if (n == null || !Number.isFinite(+n)) return '—';
  const a = Math.abs(n);
  const max = a !== 0 && a < 1 ? 6 : 4;
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: max, minimumFractionDigits: 2 });
}
function fmtAmt(n) {
  if (n == null || !Number.isFinite(+n)) return '—';
  const a = Math.abs(n);
  const max = a >= 100 ? 2 : a >= 1 ? 3 : 6;
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: max, minimumFractionDigits: 2 });
}
function num(n, d = 0) {
  return n == null || !Number.isFinite(+n) ? '—'
    : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
}
function downloadJSON(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
function agoLabel(iso) {
  if (!iso) return 'unknown';
  const then = new Date(iso), now = new Date();
  const h = Math.round((now - then) / 36e5);
  if (h < 1) return 'just now';
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

/* Reusable multi-series line chart (inline SVG). series: [{name, values, color}].
 * opts.normalize scales EACH series independently to its own 0–100 range (min→0,
 * max→100), so shape/co-movement is comparable regardless of native units. This
 * is deliberately NOT "index to 100 at the first point ÷ first value" — that
 * ratio approach breaks (huge, sign-flipped swings) whenever a series' first
 * value is near zero or negative, e.g. a real yield that opens the window
 * negative. Min–max is robust to that and still shows the same co-movement. */
function lineChart(series, dates, opts = {}) {
  const W = opts.w || 860, H = opts.h || 220, padL = 46, padR = 12, padT = 10, padB = 22;
  const scaler = vals => {
    if (!opts.normalize) return x => x;
    const valid = vals.filter(v => v != null && Number.isFinite(v));
    if (!valid.length) return x => x;
    const lo = Math.min(...valid), hi = Math.max(...valid), rng = (hi - lo) || 1;
    return x => (x == null ? null : ((x - lo) / rng) * 100);
  };
  const prepped = series.filter(s => s.values && s.values.length).map(s => {
    const f = scaler(s.values);
    return { ...s, plot: s.values.map(v => (v == null ? null : f(v))) };
  });
  const all = prepped.flatMap(s => s.plot).filter(Number.isFinite);
  if (!all.length) return '<p class="muted">No data.</p>';
  const n = Math.max(...prepped.map(s => s.plot.length));
  const min = Math.min(...all), max = Math.max(...all), span = (max - min) || 1;
  const xFor = i => padL + (i / Math.max(1, n - 1)) * (W - padL - padR);
  const yFor = v => padT + (1 - (v - min) / span) * (H - padT - padB);
  const ticks = [min, min + span / 2, max].map(v =>
    `<line x1="${padL}" y1="${yFor(v).toFixed(1)}" x2="${W - padR}" y2="${yFor(v).toFixed(1)}" stroke="${cv('--line')}"/>
     <text x="4" y="${(yFor(v) + 3).toFixed(1)}" fill="${cv('--muted')}" font-size="10">${opts.normalize ? v.toFixed(0) : fmt(v)}</text>`).join('');
  const paths = prepped.map(s => {
    // Begin each subpath with M; a null breaks the line and the next point re-Ms
    // (so leading/interior gaps never produce an invalid "L…" path start).
    let pen = false;
    const d = s.plot.map((v, i) => {
      if (v == null) { pen = false; return ''; }
      const cmd = pen ? 'L' : 'M'; pen = true;
      return `${cmd}${xFor(i).toFixed(1)},${yFor(v).toFixed(1)}`;
    }).filter(Boolean).join(' ');
    return d ? `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2"/>` : '';
  }).join('');
  // sparse x labels
  let xlabels = '';
  if (dates && dates.length) {
    const step = Math.ceil(dates.length / 6);
    for (let i = 0; i < dates.length; i += step) {
      xlabels += `<text x="${xFor(i).toFixed(1)}" y="${H - 6}" fill="${cv('--muted')}" font-size="9" text-anchor="middle">${esc(dates[i])}</text>`;
    }
  }
  const legend = prepped.map(s =>
    `<span class="k"><span class="swatch" style="background:${s.color}"></span>${esc(s.name)}</span>`).join('');
  return `<div class="macro-chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="chart">${ticks}${paths}${xlabels}</svg></div>
          <div class="legend">${legend}</div>`;
}

// macro-engine.js reuses this chart so both the engine pages and the series
// monitor draw identically rather than each rolling their own axes.
window.lineChart = lineChart;

// ── tab controller ────────────────────────────────────────────────────────
const TABS = ['fx', 'macro', 'campaign'];
const started = {};
const MODS = {}; // id -> init fn, registered below
function showTab(id) {
  if (!TABS.includes(id)) id = 'fx';
  TABS.forEach(t => {
    document.getElementById('panel-' + t).hidden = t !== id;
    document.getElementById('tab-' + t).classList.toggle('active', t === id);
  });
  // A hidden thumb never fires mouseleave, so the shared image-zoom overlay
  // (Card Promos) could otherwise be left showing over an unrelated tab.
  const zoom = document.getElementById('img-zoom-overlay');
  if (zoom) zoom.classList.remove('show');
  if (!started[id]) { started[id] = true; try { MODS[id](); } catch (e) { console.error(e); } }
  if (location.hash !== '#' + id) history.replaceState(null, '', '#' + id);
}
document.querySelectorAll('.tab').forEach(b =>
  b.addEventListener('click', () => showTab(b.dataset.tab)));

// ════════════════════════════════ TAB 1 · FX ══════════════════════════════
MODS.fx = function initFX() {
  const API = CFG.fxApi;
  const HOME = 'MYR';
  let CCYS = ['MYR', 'USD'];
  const SOURCES = ['market', 'visa', 'mastercard', 'wise'];
  const LABEL = { market: 'Market', visa: 'Visa', mastercard: 'Mastercard', wise: 'Wise' };
  let WISE_SEND = 0;
  let REFRESH = { utcHour: 1, utcMinute: 15, label: '9:15 AM', tz: 'MYT' };
  const TRAVEL_AMOUNTS = [1, 5, 20, 100, 200, 500, 1000, 5000];
  let lastBySrc = {}, lastCtx = null;
  const els = {};
  ['from', 'to', 'swap', 'refresh', 'cards', 'chart', 'legend', 'chartpair',
    'convAmount', 'convFrom', 'convResults', 'travelCcy', 'travelToggle', 'travelBody']
    .forEach(k => els[k] = document.getElementById(k));
  els.status = document.getElementById('fx-status');
  els.refreshbar = document.getElementById('fx-refreshbar');
  els.generated = document.getElementById('fx-generated');

  const fillSelect = (sel, list) => sel.innerHTML = list.map(c => `<option value="${c}">${c}</option>`).join('');
  const show = (stored, ctx) => (ctx.inverted ? 1 / stored : stored);
  const bySource = rows => { const m = {}; (rows || []).forEach(r => m[r.source] = r); return m; };

  (async function boot() {
    try {
      const r = await fetchJSON(`${API}/api/pairs`);
      if (r && Array.isArray(r.pairs) && r.pairs.length) {
        const spends = r.pairs.map(p => p.spend).filter(c => c !== HOME);
        CCYS = [HOME, ...spends];
        WISE_SEND = Number(r.wise_send_myr) || 0;
        if (r.refresh) REFRESH = r.refresh;
      }
    } catch { /* keep fallback list */ }

    fillSelect(els.from, CCYS); fillSelect(els.to, CCYS);
    els.from.value = HOME;
    els.to.value = CCYS.includes('USD') ? 'USD' : (CCYS[1] || HOME);
    els.from.addEventListener('change', () => { enforceOneHome('from'); load(); });
    els.to.addEventListener('change', () => { enforceOneHome('to'); load(); });
    els.swap.addEventListener('click', () => { const f = els.from.value; els.from.value = els.to.value; els.to.value = f; load(); });
    els.refresh.addEventListener('click', load);
    els.convAmount.addEventListener('input', renderConverter);

    const foreign = CCYS.filter(c => c !== HOME);
    fillSelect(els.travelCcy, foreign);
    els.travelCcy.value = foreign.includes(els.to.value) ? els.to.value : (foreign[0] || 'USD');
    els.travelToggle.addEventListener('click', () => {
      const openIt = els.travelBody.hidden;
      els.travelBody.hidden = !openIt;
      els.travelToggle.textContent = openIt ? 'Hide' : 'Show';
      els.travelToggle.setAttribute('aria-expanded', String(openIt));
      if (openIt) loadTravel();
    });
    els.travelCcy.addEventListener('change', () => { if (!els.travelBody.hidden) loadTravel(); });

    renderRefreshBar(); setInterval(renderRefreshBar, 60000);
    load();
  })();

  function renderRefreshBar() {
    if (!els.refreshbar) return;
    const now = new Date();
    const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), REFRESH.utcHour, REFRESH.utcMinute, 0));
    if (next <= now) next.setUTCDate(next.getUTCDate() + 1);
    const mins = Math.max(0, Math.round((next - now) / 60000));
    const rel = mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
    const myt = `${REFRESH.label} ${REFRESH.tz}`;
    const viewerIsMYT = now.getTimezoneOffset() === -480;
    const localStr = next.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });
    const when = viewerIsMYT ? `<b>${myt}</b> (your local time)` : `<b>${myt}</b> — that's <b>${localStr}</b> your time`;
    els.refreshbar.innerHTML = `🕘 Market, Visa &amp; Mastercard refresh daily at ${when} · next in <span class="next">${rel}</span> · Wise updates live`;
  }
  function enforceOneHome(changed) {
    const f = els.from.value, t = els.to.value;
    if (f !== HOME && t !== HOME) { if (changed === 'from') els.to.value = HOME; else els.from.value = HOME; }
    else if (f === HOME && t === HOME) { const def = CCYS.find(c => c !== HOME) || 'USD'; if (changed === 'from') els.to.value = def; else els.from.value = def; }
  }
  async function load() {
    const from = els.from.value, to = els.to.value;
    const foreign = from === HOME ? to : from;
    const ctx = { from, to, foreign, inverted: from === HOME };
    setStatus(''); renderCards({}, ctx);
    try {
      const [rates, history] = await Promise.all([
        fetchJSON(`${API}/api/rates?base=${HOME}&quote=${foreign}`),
        fetchJSON(`${API}/api/history?base=${HOME}&quote=${foreign}&days=30`).catch(() => null)
      ]);
      const bySrc = bySource(rates.rates);
      renderCards(bySrc, ctx);
      lastBySrc = bySrc; lastCtx = ctx; renderConverter();
      els.generated.textContent = rates.generated_at ? `Checked ${new Date(rates.generated_at).toLocaleTimeString()}` : '';
      if (history) renderChart(history, ctx);
    } catch (err) {
      setStatus(`Could not reach the FX API (${err.message}). Showing last view. Set a different Worker URL via localStorage 'fx_api' if needed.`);
    }
  }
  function renderCards(bySrc, ctx) { els.cards.innerHTML = SOURCES.map(src => card(src, bySrc[src], bySrc.market, ctx)).join(''); }
  function card(src, r, market, ctx) {
    const has = r && r.rate != null;
    const val = has ? fmt(show(r.rate, ctx)) : '—';
    const badge = staleBadge(r ? r.stale_days : null, has);
    const asOf = r && r.as_of ? `as of ${r.as_of}` : 'no data yet';
    const stale = !has || (r.stale_days != null && r.stale_days > 3);
    let spread = '';
    if (has && src !== 'market' && market && market.rate) {
      const pct = ((r.rate - market.rate) / market.rate) * 100;
      spread = `<div class="spread ${pct >= 0 ? 'up' : 'down'}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}% vs market</div>`;
    }
    const bench = has && r.benchmark != null ? `<div class="sub">benchmark ${fmt(show(r.benchmark, ctx))}</div>` : '';
    const note = src === 'wise' ? `<div class="sub">mid-market · Wise card</div>` : '';
    return `<div class="rate-card ${stale ? 'stale' : ''}">${badge}
      <div class="src ${src}"><span class="dot"></span>${LABEL[src]}</div>
      <div class="val ${has ? '' : 'missing'}">${val}</div>
      <div class="sub">${ctx.to} per 1 ${ctx.from}</div>${note}
      <div class="sub">${asOf}</div>${bench}${spread}</div>`;
  }
  function staleBadge(days, has) {
    if (!has) return `<span class="badge red">missing</span>`;
    if (days == null) return '';
    if (days <= 1) return `<span class="badge green">fresh</span>`;
    if (days <= 3) return `<span class="badge amber">${days}d old</span>`;
    return `<span class="badge red">${days}d stale</span>`;
  }
  function renderChart(history, ctx) {
    els.chartpair.textContent = `${ctx.from} → ${ctx.to}`;
    const g = { market: [], visa: [], mastercard: [], wise: [] };
    (history.series || []).forEach(r => { if (g[r.source]) g[r.source].push(r); });
    const dates = [...new Set((history.series || []).map(d => d.as_of))].sort();
    const tv = v => show(v, ctx);
    const colors = { market: cv('--market'), visa: cv('--visa'), mastercard: cv('--mc'), wise: cv('--wise') };
    const series = SOURCES.map(src => {
      const pts = (g[src] || []).slice().sort((a, b) => a.as_of.localeCompare(b.as_of));
      const values = dates.map(d => { const p = pts.find(x => x.as_of === d); return p ? tv(p.rate) : null; });
      return { name: LABEL[src], values, color: colors[src] };
    }).filter(s => s.values.some(v => v != null));
    els.chart.innerHTML = series.length ? lineChart(series, dates, { w: 820 }).replace(/<div class="legend">[\s\S]*<\/div>\s*$/, '') : '<p class="muted">No history yet.</p>';
    els.legend.innerHTML = series.map(s => `<span class="k"><span class="swatch" style="background:${s.color}"></span>${s.name}</span>`).join('');
  }
  function renderConverter() {
    if (!lastCtx) return;
    els.convFrom.textContent = lastCtx.from;
    const amt = Number(els.convAmount.value);
    if (!Number.isFinite(amt) || amt < 0) { els.convResults.innerHTML = ''; return; }
    els.convResults.innerHTML = SOURCES.map(src => {
      const r = lastBySrc[src], has = r && r.rate != null;
      const out = has ? amt * show(r.rate, lastCtx) : null;
      return `<div class="conv-row ${has ? '' : 'missing'}"><span class="src ${src}"><span class="dot"></span>${LABEL[src]}</span>
        <span class="camt">${has ? `${fmtAmt(out)} ${lastCtx.to}` : '—'}</span></div>`;
    }).join('');
  }
  async function loadTravel() {
    const ccy = els.travelCcy.value;
    els.travelBody.innerHTML = '<p class="muted">Loading…</p>';
    try { const data = await fetchJSON(`${API}/api/rates?base=${HOME}&quote=${ccy}`); renderTravel(bySource(data.rates), ccy); }
    catch { els.travelBody.innerHTML = `<p class="muted">Couldn't load ${ccy} rates.</p>`; }
  }
  function renderTravel(bySrc, ccy) {
    const cols = SOURCES.filter(s => bySrc[s] && bySrc[s].rate != null);
    if (!cols.length) { els.travelBody.innerHTML = `<p class="muted">No rates for ${ccy} yet.</p>`; return; }
    const head = `<tr><th class="l">${ccy}</th>${cols.map(s => `<th><span class="src ${s}"><span class="dot"></span>${LABEL[s]}</span></th>`).join('')}</tr>`;
    const body = TRAVEL_AMOUNTS.map(a => `<tr><td class="l">${a.toLocaleString()}</td>${cols.map(s => `<td>${fmtAmt(a * bySrc[s].rate)}</td>`).join('')}</tr>`).join('');
    els.travelBody.innerHTML = `<div class="table-scroll"><table class="data-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>
      <p class="muted" style="margin-top:8px">Amounts in <b>${ccy}</b>, values in <b>MYR</b> — what each source charges.</p>`;
  }
  function setStatus(msg) { els.status.hidden = !msg; els.status.textContent = msg; }
};

// ════════════════════════════════ TAB 2 · MACRO ═══════════════════════════
/* The Macro tab is now the forecasting engine's shell. macro-engine.js renders
 * the ten spec pages from data/macro_engine.json; the original series monitor
 * below is handed to it as one more sub-page, so nothing that already worked
 * was thrown away — the overlay chart, the lagged-unemployment panel and the
 * Treasury supply panel all still render, one click away. */
MODS.macro = async function initMacro() {
  const root = document.getElementById('macro-body');
  if (!window.MACRO_ENGINE) {
    root.innerHTML = `<div class="errbox">macro-engine.js did not load — check the script tag order in index.html (it must come before app.js).</div>`;
    return;
  }
  return window.MACRO_ENGINE.render(root, CFG.feeds.macroEngine, renderSeriesMonitor);
};

/* The original Macro Monitor: raw series cards, the real-yield/gold/S&P overlay,
 * the lagged-unemployment panel and Treasury supply. Spec 27 says the main
 * dashboard should not be buried in raw economic series and that they belong in
 * a drill-down — this is that drill-down. */
async function renderSeriesMonitor(root) {
  root.innerHTML = '<p class="muted loading">Loading series monitor…</p>';
  let d;
  try { d = await fetchJSON(CFG.feeds.macro); }
  catch (e) { root.innerHTML = `<div class="errbox">Couldn't load the series feed (${esc(e.message)}). Expected at <code>${esc(CFG.feeds.macro)}</code>. Generate it with <code>python tools/fetch_macro.py</code>.</div>`; return; }

  const sample = d.meta && d.meta.sample;
  const rg = d.regime || {};

  // Age is computed HERE, against the reader's clock, never baked into the feed.
  // A baked "3 days old" would itself go stale the moment the refresh job stops
  // — which is precisely the failure this is meant to expose.
  const DAY = 86400000;
  const ageDays = iso => {
    const t = Date.parse(iso);
    return Number.isNaN(t) ? null : Math.floor((Date.now() - t) / DAY);
  };
  // What counts as "old" depends on the release cadence, and the cadence is a
  // property of the PUBLISHER, not of the word "daily". The Fed's broad dollar
  // index is a daily series shipped weekly in the H.10; Case-Shiller reports the
  // month before last. Both were being painted amber for running exactly on
  // schedule, which is how a staleness warning stops being believed. So the feed
  // now carries `stale_after` for any series whose cadence isn't the generic
  // case, and only falls back to the blanket rule when it doesn't: a daily
  // series more than ~4 days behind is worth a second look (a long weekend plus
  // a public holiday is 4), a monthly one only past ~45.
  const staleAfter = s => (s.stale_after != null ? s.stale_after : s.freq === 'monthly' ? 45 : 4);

  const cards = (d.snapshot || []).map(s => {
    const dir = s.change == null ? '' : (s.change >= 0 ? 'up' : 'down');
    const arrow = s.change == null ? '' : (s.change >= 0 ? '▲' : '▼');
    const unit = s.unit === '%' ? '%' : '';
    const pre = (s.unit === '$' || s.unit === '¥') ? s.unit : '';
    const age = ageDays(s.as_of);
    const stale = age != null && age > staleAfter(s);
    const agoTxt = age == null ? '' : age <= 0 ? 'today' : age === 1 ? '1d ago' : `${age}d ago`;
    // The source is on the card, not just in the JSON: "why is this number from
    // last Friday" is only answerable if you can see which release produced it.
    const src = s.source ? ` · ${esc(s.source)}` : '';
    // …and for the series with no faster publisher, the schedule it runs on,
    // which is the actual answer to that question rather than a hint towards it.
    const rel = s.release ? ` · ${esc(s.release)}` : '';
    return `<div class="stat" title="${esc(s.label)} — latest observation ${esc(s.as_of)}${
        s.source ? `, from ${esc(s.source)}` : ''}${
        s.release ? `; published ${esc(s.release)}` : ''}">
      <span class="k">${esc(s.label)} <span class="chip freq">${esc(s.freq || '')}</span>${
        s.provisional ? ' <span class="chip ok">live</span>' : ''}</span>
      <div class="v">${pre}${fmt(s.value)}${unit}</div>
      <div class="d ${dir}">${arrow} ${s.change == null ? '' : fmt(Math.abs(s.change))}</div>
      <div class="meta${stale ? ' stale' : ''}">as of ${esc(s.as_of)}${
        agoTxt ? ` <span class="ago">(${agoTxt})</span>` : ''}${src}${rel}</div>
    </div>`;
  }).join('');

  // Daily series can legitimately show DIFFERENT "as of" dates — each is an
  // independent release (Treasury yields, breakeven, equities…) with its own
  // publish schedule, not all stamped by one clock. Surface that explicitly
  // whenever it's actually happening, so a 1-day gap between two "daily" cards
  // reads as expected, not as a stale-fetch bug.
  const dailyDates = [...new Set((d.snapshot || []).filter(s => s.freq === 'daily').map(s => s.as_of))];
  const dateSkewNote = dailyDates.length > 1
    ? `<p class="muted" style="margin:-4px 0 12px">ℹ️ Daily series don't all show the same date (${dailyDates.slice().sort().join(' vs ')}) — each is published independently by its own source on its own schedule (the Treasury curve posts the same afternoon, US equities settle overnight, and the Fed's H.10 broad dollar index is a daily series released <em>weekly</em>, on Mondays); not a stale fetch. Each card names its own cadence.</p>`
    : '';

  // The stale-card question the individual "as of" dates cannot answer: is the
  // FETCHER still running at all? An old observation on a feed rebuilt an hour
  // ago is an upstream publication lag; an old observation on a feed that itself
  // hasn't been rebuilt in days is a broken job. Separating those two is the
  // whole point, so the age of the feed is stated outright rather than inferred.
  const feedAgeH = d.meta && d.meta.generated_at
    ? Math.floor((Date.now() - Date.parse(d.meta.generated_at)) / 3600000) : null;
  const feedNote = feedAgeH == null ? ''
    : feedAgeH > 36
      ? `<div class="disclaimer">⚠ This feed was last rebuilt <b>${Math.floor(feedAgeH / 24)} days ago</b> (${esc(d.meta.generated_at)}). The daily refresh job looks like it has stopped — check the "Refresh feeds &amp; deploy" workflow in GitHub Actions. Every figure below is frozen at that date.</div>`
      : `<p class="muted" style="margin:-4px 0 12px">🔄 Feed rebuilt ${feedAgeH < 1 ? 'less than an hour' : feedAgeH === 1 ? '1 hour' : `${feedAgeH} hours`} ago${
          d.meta.latest_observation ? `; newest market observation ${esc(d.meta.latest_observation)}` : ''}. Cards dated further back are waiting on their publisher, not on this job.</p>`;

  const ov = d.overlay || {};
  const overlayChart = lineChart([
    { name: 'Real yield', values: ov.series && ov.series.real_yield, color: cv('--s1') },
    { name: 'Gold', values: ov.series && ov.series.gold, color: cv('--s2') },
    { name: 'S&P 500', values: ov.series && ov.series.sp500, color: cv('--s3') }
  ], ov.dates, { normalize: true, w: 860 });

  // Oil and FX get their own blocks rather than extra lines on the overlay above:
  // each block is single-unit, so it plots on a REAL axis (normalize:false) and the
  // actual $/bbl and yen levels stay readable — the overlay's 0-100 rescale exists
  // only because real yield / gold / S&P have no common scale.
  const has = o => o && o.series && Object.values(o.series).some(v => v && v.length);

  const oil = d.oil || {};
  const oilBlock = !has(oil) ? '' : `<div class="card-block">
    <h3>Crude oil <span class="muted">— Brent vs WTI, $/bbl</span></h3>
    ${lineChart([
      { name: 'Brent', values: oil.series.brent, color: cv('--s1') },
      { name: 'WTI', values: oil.series.wti, color: cv('--s2') }
    ], oil.dates, { w: 860, h: 190 })}
    <p class="muted" style="margin-top:6px">${esc(oil.note || '')}</p></div>`;

  const fx = d.fx || {};
  const fxBlock = !has(fx) ? '' : `<div class="card-block">
    <h3>USD/JPY <span class="muted">— yen per US dollar</span></h3>
    ${lineChart([
      { name: 'USD/JPY', values: fx.series.usdjpy, color: cv('--s3') }
    ], fx.dates, { w: 860, h: 190 })}
    <p class="muted" style="margin-top:6px">${esc(fx.note || '')}</p></div>`;

  const lag = d.lag || {};
  const lagChart = lineChart([
    { name: 'Unemployment', values: lag.unemployment, color: cv('--s1') },
    { name: `Real yield (led ${lag.lead_months || 15}m)`, values: lag.real_yield_lead, color: cv('--s2') }
  ], lag.dates, { normalize: true, w: 860, h: 190 });

  const missing = (d.meta && d.meta.missing_series) || [];
  root.innerHTML = `
    ${sample ? `<div class="disclaimer">⚠ Showing <b>sample</b> data for UI review — wire the FRED/Stooq feed (Phase B) to go live. ${esc(d.meta.data_note || '')}</div>` : ''}
    ${missing.length ? `<div class="disclaimer">⚠ Data source gap: <b>${esc(missing.join(', '))}</b> could not be fetched this run (e.g. the upstream series was renamed/discontinued) — shown as missing rather than guessed.</div>` : ''}
    <div class="banner regime-${esc(rg.label || 'Mixed')}">
      <span class="b-ico">🧭</span>
      <div><div class="b-title">Regime: ${esc(rg.label || '—')}</div>
        <div class="b-detail">${esc(rg.detail || '')}</div>
        <div class="b-caveat">${esc(rg.caveat || 'Heuristic, not a signal.')}</div></div>
    </div>
    ${feedNote}
    ${dateSkewNote}
    <div class="stat-grid">${cards}</div>
    <div class="card-block"><h3>Real yield · Gold · S&amp;P 500 <span class="muted">— each scaled to its own 0–100 range</span></h3>${overlayChart}
      <p class="muted" style="margin-top:6px">${esc(ov.note || '')}</p></div>
    ${oilBlock}
    ${fxBlock}
    <div class="card-block"><h3>Unemployment vs lagged real yield</h3>${lagChart}
      <p class="muted" style="margin-top:6px">${esc(lag.note || '')}</p></div>
    <div id="treasury-panel"></div>
    <div class="freshline"><span>${esc((d.meta && d.meta.disclaimer) || '')}</span></div>`;

  renderTreasuryPanel(root.querySelector('#treasury-panel'));
}

/* US Treasury supply panel — upcoming auctions (new issuance / reopenings) and
 * recent buybacks. Loaded separately from the macro feed so a Treasury outage
 * can never blank the macro tab: on failure the panel just omits itself. */
async function renderTreasuryPanel(root) {
  if (!root) return;
  let t;
  try { t = await fetchJSON(CFG.feeds.treasury); }
  catch { return; }   // silently skip — macro cards/charts above still stand

  const fmtDay = s => {
    const dt = new Date(s + 'T00:00:00');
    return Number.isNaN(+dt) ? esc(s)
      : dt.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  };
  const daysAway = s => {
    const dt = new Date(s + 'T00:00:00');
    if (Number.isNaN(+dt)) return null;
    const now = new Date(); now.setHours(0, 0, 0, 0);
    return Math.round((dt - now) / 86400000);
  };
  const bn = v => v == null ? '—' : `$${(v / 1e9).toFixed(2)}B`;

  const auctions = t.upcoming_auctions || [];
  const buybacks = t.recent_buybacks || [];
  if (!auctions.length && !buybacks.length) return;

  const auctionRows = auctions.map((a, i) => {
    const d0 = daysAway(a.auction_date);
    const when = d0 === 0 ? 'today' : d0 === 1 ? 'tomorrow' : d0 > 1 ? `in ${d0}d` : '';
    return `<tr${i === 0 ? ' class="next-up"' : ''}>
      <td class="l"><b>${fmtDay(a.auction_date)}</b>${when ? ` <span class="muted">${when}</span>` : ''}</td>
      <td class="l">${esc(a.security_type)} · ${esc(a.term)}</td>
      <td class="l">${fmtDay(a.issue_date)}</td>
      <td>${a.rate ? Number(a.rate).toFixed(3) + '%' : '<span class="muted">at auction</span>'}</td>
    </tr>`;
  }).join('');

  // Auction RESULTS — what the rows in the table above became once they were
  // actually held. Two percentages, kept visually distinct because they answer
  // different questions and are easy to conflate (see the footnote).
  const results = t.recent_results || [];
  const pc = v => v == null ? '—' : `${v.toFixed(1)}%`;
  const rate = v => v == null ? '—' : `${v.toFixed(3)}%`;
  const share = (part, whole) => (part == null || !whole) ? '—'
    : `${Math.round(part / whole * 100)}%`;

  const resultRows = results.map(r => {
    // Bills clear on a discount rate; their comparable annualised figure is the
    // separate investment rate. Showing the headline alone would understate a
    // bill against a note sitting in the same column.
    const stop = rate(r.stop_rate) + (r.investment_rate != null
      ? ` <span class="muted">/ ${rate(r.investment_rate)}</span>` : '');
    return `<tr>
      <td class="l"><b>${fmtDay(r.auction_date)}</b></td>
      <td class="l">${esc(r.security_type)} · ${esc(r.term)}</td>
      <td class="num">${stop}</td>
      <td class="num">${r.bid_to_cover == null ? '—' : r.bid_to_cover.toFixed(2)}</td>
      <td class="num"><b>${pc(r.allotted_at_high)}</b></td>
      <td class="num">${bn(r.accepted)}<span class="muted"> / ${bn(r.tendered)}</span></td>
      <td class="num">${share(r.indirect_accepted, r.accepted)}<span class="muted"> · ${
        share(r.dealer_accepted, r.accepted)}</span></td>
    </tr>`;
  }).join('');

  const buybackRows = buybacks.map(b => {
    // A low hit rate against a filled cap is a CAPPED operation, not refused
    // offers. Marking the capped ones stops the two reading the same.
    const capped = b.par_cap != null && b.par_accepted != null
      && b.par_accepted >= b.par_cap - 1;
    return `<tr>
      <td class="l"><b>${fmtDay(b.operation_date)}</b></td>
      <td class="l">${esc(b.operation_type)}</td>
      <td class="l">${esc(b.maturity_bucket)}</td>
      <td class="num">${bn(b.par_offered)}</td>
      <td class="num">${bn(b.par_accepted)}${
        capped ? ' <span class="chip freq">at cap</span>' : ''}</td>
      <td class="num">${share(b.par_accepted, b.par_offered)}</td>
    </tr>`;
  }).join('');

  root.innerHTML = `
    ${auctions.length ? `<div class="card-block"><h3>🇺🇸 Upcoming Treasury auctions <span class="muted">— new issuance &amp; reopenings</span></h3>
      <div class="table-scroll"><table class="data-table">
        <thead><tr><th class="l">Auction</th><th class="l">Security</th><th class="l">Settles</th><th>Coupon</th></tr></thead>
        <tbody>${auctionRows}</tbody></table></div>
      <p class="muted small" style="margin-top:8px">Announced but not yet held. “At auction” = price/yield set on the day (bills and new issues); a coupon shown means it's a reopening of an existing bond.</p>
    </div>` : ''}
    ${results.length ? `<div class="card-block"><h3>📊 Recent auction results <span class="muted">— what they cleared at</span></h3>
      <div class="table-scroll"><table class="data-table">
        <thead><tr><th class="l">Auction</th><th class="l">Security</th>
          <th class="num">Stop-out<span class="muted"> / inv.</span></th>
          <th class="num">Bid/cover</th><th class="num">Allot @ high</th>
          <th class="num">Accepted<span class="muted"> / tendered</span></th>
          <th class="num">Indirect<span class="muted"> · dealer</span></th></tr></thead>
        <tbody>${resultRows}</tbody></table></div>
      <p class="muted small" style="margin-top:8px">Every winner pays the same <b>stop-out</b> rate (bills show discount rate / investment rate — the second is the comparable annualised figure). <b>Allot @ high</b> is the share of bids <em>at</em> that rate which were filled: the number that actually moves, and the one to read. <b>Accepted / tendered</b> sits near 35% by construction — Treasury fixes the size in advance and bidders over-bid about 3×, so it is not a demand signal. A rising <b>dealer</b> share means the auction had to be absorbed rather than bought. The <em>tail</em> (stop-out vs when-issued) isn't published in this feed and is deliberately not estimated.</p>
    </div>` : ''}
    ${buybacks.length ? `<div class="card-block"><h3>🔁 Recent Treasury buybacks <span class="muted">— completed operations</span></h3>
      <div class="table-scroll"><table class="data-table">
        <thead><tr><th class="l">Operation</th><th class="l">Purpose</th><th class="l">Maturity bucket</th>
          <th class="num">Offered</th><th class="num">Accepted</th><th class="num">Hit rate</th></tr></thead>
        <tbody>${buybackRows}</tbody></table></div>
      <p class="muted small" style="margin-top:8px">⚠ These are operations already <b>completed</b>, not a forward schedule — Treasury only publishes upcoming buyback calendars inside quarterly-refunding PDFs, not as a structured feed, so no “next buyback” date is claimed here.</p>
    </div>` : ''}
    <div class="freshline"><span>Sources: <b>TreasuryDirect</b> (auctions &amp; results) · <b>Treasury Fiscal Data</b> (buybacks)</span>
      <span>· fetched ${esc((t.meta && t.meta.generated_at || '').slice(0, 10))}</span></div>`;
}

// ════════════════════════════════ TAB 3 · CAMPAIGN ════════════════════════
// Full-size image preview: one reusable overlay, shared by every card. The
// card thumbnail is deliberately cropped (object-fit: cover, fixed height, for
// a uniform grid) — this overlay is what shows the COMPLETE, uncropped image.
// Fixed-position so it always escapes the card's own rounded-corner clipping,
// regardless of scroll position or which card triggered it.
//
// Two trigger paths, because "point at it" means different things on
// different devices: mouseenter/mouseleave give an instant hover preview on
// desktop, but never fire at all on touch (there is no hover state on a
// phone/tablet) — a touch-only user would otherwise NEVER see the full image,
// only the cropped thumbnail. click/tap opens it on every device (a tap
// synthesizes a click) and stays open until dismissed by tapping the overlay
// itself or pressing Escape, so it works as a real "view full image" action
// on mobile, not just a hover-only desktop nicety.
function ensureZoomOverlay() {
  let ov = document.getElementById('img-zoom-overlay');
  if (ov) return ov;
  ov = document.createElement('div');
  ov.id = 'img-zoom-overlay';
  ov.className = 'zoom-overlay';
  ov.innerHTML = '<img alt=""><span class="zoom-hint">Tap anywhere to close</span>';
  document.body.appendChild(ov);
  ov.addEventListener('click', () => ov.classList.remove('show'));       // tap/click anywhere to dismiss
  document.addEventListener('keydown', e => { if (e.key === 'Escape') ov.classList.remove('show'); });
  return ov;
}
function wireImageZoom(container) {
  const overlay = ensureZoomOverlay();
  const img = overlay.querySelector('img');
  const open = src => { img.src = src; overlay.classList.add('show'); };
  container.querySelectorAll('.thumb img').forEach(el => {
    el.addEventListener('mouseenter', () => open(el.src));
    el.addEventListener('mouseleave', () => overlay.classList.remove('show'));
    el.addEventListener('click', e => { e.preventDefault(); open(el.src); });
  });
}

MODS.campaign = async function initCampaign() {
  const root = document.getElementById('campaign-body');
  const consoleRoot = document.getElementById('campaign-console');
  let d;
  try { d = await fetchJSON(CFG.feeds.promotions); }
  catch (e) { root.innerHTML = `<div class="errbox">Couldn't load promotions feed (${esc(e.message)}). Expected at <code>${esc(CFG.feeds.promotions)}</code>.</div>`; return; }

  const list = d.promotions || [];
  const sample = d.meta && d.meta.sample;
  if (!list.length) { root.innerHTML = `<div class="emptybox">No promotions yet. Run <code>tools/scrape_campaign.py</code>, then use the 🛠️ Console tab to enrich and publish.</div>`; return; }

  // "New today" = first detected on the page today. Use the feed's own `today`
  // (the date the producer ran) if present, else the viewer's local date.
  const today = (d.meta && d.meta.today) || new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD
  const fmtDate = s => { const t = new Date(s); return Number.isNaN(+t) ? esc(s) : t.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); };
  list.forEach(p => p._new = p.first_seen && p.first_seen === today);
  const newCount = list.filter(p => p._new).length;

  // Category filter values, newest-flagged first for visibility.
  const cats = ['All', ...[...new Set(list.map(p => p.category).filter(Boolean))].sort()];
  let active = 'All';

  // Most Public Bank promos carry NO inline description on their own page — the
  // real terms live only in a linked PDF (captured separately as tnc_link by
  // tools/scrape_campaign.py). The promo banner image is usually self-explanatory
  // (the offer is printed on it), so it's the star of the card — bigger, and
  // zoomable to full size on hover — rather than padding out an empty "no
  // summary" box. A written tnc_summary (via the Console's AI-enrich flow)
  // still shows when one exists.
  const cardHTML = p => `
    <article class="promo${p._new ? ' is-new' : ''}">
      ${p._new ? '<span class="new-badge">🆕 NEW</span>' : ''}
      <div class="thumb">${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy" onerror="this.parentNode.textContent='No image'">` : 'No image'}</div>
      <div class="body">
        ${p.category ? `<div class="cat">${esc(p.category)}</div>` : ''}
        <h3>${esc(p.title)}</h3>
        ${p.period ? `<div class="period">🗓 Valid: ${esc(p.period)}</div>` : ''}
        ${p.tnc_summary ? `<div class="tnc">${esc(p.tnc_summary)}</div>` : ''}
        ${p.first_seen ? `<div class="seen${p._new ? ' new' : ''}">${p._new ? '🆕 Added today' : '👁 Listed since'} ${fmtDate(p.first_seen)}</div>` : ''}
        <div class="cta-row">
          ${p.link ? `<a class="cta" href="${esc(p.link)}" target="_blank" rel="noopener">View promotion ➔</a>` : ''}
          ${p.tnc_link ? `<a class="cta ghost" href="${esc(p.tnc_link)}" target="_blank" rel="noopener">📄 Official T&amp;C</a>` : ''}
        </div>
      </div>
    </article>`;

  function render() {
    const filtered = active === 'All' ? list : list.filter(p => p.category === active);
    // new-today items first, then by first_seen desc
    filtered.sort((a, b) => (b._new - a._new) || String(b.first_seen || '').localeCompare(String(a.first_seen || '')));
    const chips = cats.map(c => {
      const n = c === 'All' ? list.length : list.filter(p => p.category === c).length;
      const newN = (c === 'All' ? list : list.filter(p => p.category === c)).filter(p => p._new).length;
      return `<button class="fchip${c === active ? ' on' : ''}" data-cat="${esc(c)}">${esc(c)} <span class="fn">${n}</span>${newN ? `<span class="fnew">${newN}</span>` : ''}</button>`;
    }).join('');
    root.innerHTML = `
      <div class="disclaimer">⚠ Public Bank publishes most terms only as a PDF, not on-page — tap/hover a banner for the full picture, or use 📄 Official T&amp;C for the real terms. Where a written summary is shown, it's AI-generated — <b>always verify the linked T&amp;C</b>.${sample ? ' Currently showing <b>sample</b> data.' : ''}</div>
      ${newCount ? `<div class="banner new-banner"><span class="b-ico">🆕</span><div><div class="b-title">${newCount} new promotion${newCount > 1 ? 's' : ''} today</div><div class="b-detail">First detected on the page on ${fmtDate(today)}. Marked 🆕 below.</div></div></div>` : ''}
      <div class="fchips" role="tablist" aria-label="Filter by category">${chips}</div>
      <div class="promo-grid">${filtered.map(cardHTML).join('') || '<div class="emptybox">No promotions in this category.</div>'}</div>
      <div class="freshline"><span>Source: <a href="${esc((d.meta && d.meta.source) || '#')}" target="_blank" rel="noopener">Public Bank promotions</a></span>
        <span>· Updated <b>${esc((d.meta && d.meta.generated_at || '').slice(0, 10))}</b></span>
        <span>· ${filtered.length}/${list.length} shown</span></div>`;
    root.querySelectorAll('.fchip').forEach(b => b.addEventListener('click', () => { active = b.dataset.cat; render(); }));
    wireImageZoom(root);
  }
  render();

  // ── 🛠️ Console: export a bundle for AI enrichment, then import the reply ──
  // Static site, no backend: this is a fully client-side, in-browser workflow.
  // "Enrich by AI" for THIS site means VISION, not link-following: every promo's
  // `image` is the bank's own full campaign poster (not the small listing
  // thumbnail) with the offer, minimum spend, and campaign period printed
  // directly on it as a designed graphic — reading that image is far more
  // reliable than trying to fetch/parse the linked PDF, and works with any
  // vision-capable chat AI (no browsing needed, just an attached/pasted image).
  // tnc_link is still included as the authoritative legal document to verify
  // against — the poster is marketing copy, not the binding terms. The import
  // step re-implements tools/merge_campaign.py's merge rule in JS (fill
  // tnc_summary/period only where the upload provides them) to preview what
  // will change; Publish then sends the summaries to the promo-sync Worker,
  // which re-reads the live feed and commits it. The download remains as the
  // no-Worker fallback.
  function renderConsole() {
    const missing = list.filter(p => !p.tnc_summary);
    const bundle = {
      instructions:
        "For each item below, view its image (the bank's full campaign poster — the offer, "
        + "minimum spend/criteria, and campaign period are printed on it) and summarise it in "
        + "<=60 words. Use tnc_link only to double-check anything unclear on the poster. "
        + "Reply with STRICT JSON only — an object mapping id -> "
        + '{"period":"<campaign period as printed, or \'\'>","tnc_summary":"<your summary>"}. '
        + "Do not invent details not visible on the poster or linked document.",
      promos: missing.map(p => ({ id: p.id, title: p.title, image: p.image, tnc_link: p.tnc_link || p.link })),
    };

    consoleRoot.innerHTML = `
      <div class="card-block">
        <h3>1 · Export for AI enrichment</h3>
        <p class="muted">${missing.length} of ${list.length} promos have no summary yet. Download a bundle of their titles + poster images + official T&amp;C links.</p>
        <div class="console-actions">
          <button id="btn-dl-bundle" class="cta"${missing.length ? '' : ' disabled'}>📥 Download AI bundle (.json)</button>
          <button id="btn-copy-prompt" class="cta ghost"${missing.length ? '' : ' disabled'}>📋 Copy prompt to clipboard</button>
        </div>
        <p class="muted small">${missing.length ? 'Paste the bundle (or the copied prompt) into a vision-capable AI (e.g. Claude, ChatGPT) — it can view each poster image directly, which reads far more reliably than the linked PDF. Ask for the JSON reply described in the instructions.' : 'Every promo already has a summary — nothing to export.'}</p>
        <p id="copy-status" class="muted small" hidden></p>
      </div>
      <div class="card-block">
        <h3>2 · Import AI summaries</h3>
        <p class="muted">Paste the JSON the AI replied with — a map of <code>id → {tnc_summary, period}</code> — or upload it as a file.</p>
        <textarea id="console-paste" rows="6" spellcheck="false"
          placeholder='{&quot;0f12f47b3d&quot;: {&quot;period&quot;: &quot;8 September - 27 December 2026&quot;, &quot;tnc_summary&quot;: &quot;RM50 off with minimum spend RM500…&quot;}}'></textarea>
        <input type="file" id="console-upload" accept=".json,application/json" />
        <div id="console-preview"></div>
      </div>
      <div class="card-block" id="console-publish" hidden>
        <h3>3 · Publish</h3>
        <div class="console-actions">
          <button id="btn-publish" class="cta">🚀 Publish to the live site</button>
          <button id="btn-dl-merged" class="cta ghost">⬇ Download merged promotions.json</button>
        </div>
        <p id="publish-status" class="muted small" hidden></p>
        <p class="muted small">Publish sends just the summaries to your <code>promo-sync</code> Worker, which commits
          <code>data/promotions.json</code> for you — the site redeploys on its own. It re-reads the live file first, so
          publishing from a tab you left open yesterday can't overwrite promos the daily scrape has added since.
          The download is the offline fallback: drop it into <code>data/</code> and commit by hand.</p>
      </div>`;

    const dlBundleBtn = document.getElementById('btn-dl-bundle');
    const copyBtn = document.getElementById('btn-copy-prompt');
    const copyStatus = document.getElementById('copy-status');
    if (missing.length) {
      dlBundleBtn.addEventListener('click', () => downloadJSON(bundle, 'card-promos-ai-bundle.json'));
      copyBtn.addEventListener('click', async () => {
        const text = bundle.instructions + '\n\n' + JSON.stringify(bundle.promos, null, 2);
        try {
          await navigator.clipboard.writeText(text);
          copyStatus.textContent = '✅ Copied to clipboard.';
        } catch {
          copyStatus.textContent = '⚠ Clipboard blocked by the browser — use Download instead.';
        }
        copyStatus.hidden = false;
      });
    }

    let mergedResult = null;      // the whole feed, for the download fallback
    let uploadedMap = null;       // just the summaries, for the Publish call
    const previewEl = document.getElementById('console-preview');
    const publishBlock = document.getElementById('console-publish');
    const dlMergedBtn = document.getElementById('btn-dl-merged');
    const publishBtn = document.getElementById('btn-publish');
    const publishStatus = document.getElementById('publish-status');

    // One ingest path for both the paste box and the file picker.
    function ingest(text, { quiet = false } = {}) {
      if (!text.trim()) {
        previewEl.innerHTML = '';
        publishBlock.hidden = true;
        mergedResult = uploadedMap = null;
        return;
      }
      let map;
      try {
        map = JSON.parse(text);
      } catch (err) {
        // While someone is still typing/pasting, half-written JSON isn't an
        // error worth shouting about — only the file picker reports it.
        if (!quiet) previewEl.innerHTML = `<div class="errbox">Couldn't read that as JSON: ${esc(err.message)}</div>`;
        publishBlock.hidden = true;
        mergedResult = uploadedMap = null;
        return;
      }
      if (!map || typeof map !== 'object' || Array.isArray(map)) {
        if (!quiet) previewEl.innerHTML = `<div class="errbox">Expected an object mapping id → {period, tnc_summary}.</div>`;
        publishBlock.hidden = true;
        return;
      }
      const ids = Object.keys(map);
      const merged = list.map(p => {
        const upd = map[p.id];
        return upd ? { ...p, tnc_summary: upd.tnc_summary || p.tnc_summary, period: upd.period || p.period } : p;
      });
      const matched = ids.filter(id => list.some(p => p.id === id)).length;
      previewEl.innerHTML =
        `<div class="disclaimer" style="background:var(--green-bg);color:var(--green)">✅ Matched ${matched} of ${ids.length} id(s) in this feed.</div>` +
        `<div class="table-scroll"><table class="data-table"><thead><tr><th class="l">ID</th><th class="l">Title</th><th class="l">New summary</th></tr></thead><tbody>` +
        ids.map(id => {
          const p = list.find(x => x.id === id);
          return `<tr><td class="l">${esc(id)}</td><td class="l">${esc(p ? p.title : '(unknown id)')}</td><td class="l">${esc((map[id] && map[id].tnc_summary || '').slice(0, 80))}</td></tr>`;
        }).join('') + `</tbody></table></div>`;
      mergedResult = merged;
      uploadedMap = map;
      publishBlock.hidden = false;
      publishStatus.hidden = true;
    }

    const pasteEl = document.getElementById('console-paste');
    pasteEl.addEventListener('input', () => ingest(pasteEl.value, { quiet: true }));
    // A finished paste deserves a real error if it's malformed.
    pasteEl.addEventListener('blur', () => ingest(pasteEl.value));
    document.getElementById('console-upload').addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const text = await file.text();
      pasteEl.value = text;
      ingest(text);
    });

    dlMergedBtn.addEventListener('click', () => {
      if (!mergedResult) return;
      const payload = {
        meta: { ...d.meta, generated_at: new Date().toISOString(), note: 'Merged locally via the Card Promos Console.' },
        promotions: mergedResult,
      };
      downloadJSON(payload, 'promotions.json');
    });

    // ── Publish: hand the summaries to the promo-sync Worker, which holds the
    // GitHub token and does the commit. The browser never sees that token; it
    // only holds the sync key, which is useless for anything but this one file.
    publishBtn.addEventListener('click', async () => {
      if (!uploadedMap) return;
      const base = ((window.DASH_CONFIG && window.DASH_CONFIG.promoSyncApi) || '').replace(/\/+$/, '');
      const say = (msg, kind) => {
        publishStatus.hidden = false;
        publishStatus.textContent = msg;
        publishStatus.style.color = kind === 'err' ? 'var(--red)' : kind === 'ok' ? 'var(--green)' : '';
      };
      if (!base) {
        say('No sync endpoint configured. Deploy worker/promo-sync, then run: '
          + "localStorage.setItem('promo_sync_api','https://promo-sync.<you>.workers.dev') and reload. "
          + 'Until then, use the download button.', 'err');
        return;
      }
      let key = localStorage.getItem('promo_sync_key');
      if (!key) {
        key = window.prompt('Sync key for promo-sync (stored in this browser for next time):');
        if (!key) return;
        localStorage.setItem('promo_sync_key', key);
      }
      publishBtn.disabled = true;
      say('Publishing…');
      try {
        const res = await fetch(base + '/publish', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Sync-Key': key },
          body: JSON.stringify({ summaries: uploadedMap }),
        });
        const out = await res.json().catch(() => ({}));
        if (res.status === 401) {
          // A wrong key shouldn't be sticky — clear it so the next click re-asks.
          localStorage.removeItem('promo_sync_key');
          say('Sync key rejected. Click Publish again to re-enter it.', 'err');
        } else if (!res.ok) {
          say('Publish failed: ' + (out.error || res.status), 'err');
        } else if (!out.committed) {
          say(out.message || 'Nothing to write — those promos already have summaries.', '');
        } else {
          say(`✅ Published ${out.applied.length} summary/ies — committed. The site rebuilds in a minute or two.`
            + (out.unknown && out.unknown.length ? ` (${out.unknown.length} unknown id(s) ignored.)` : ''), 'ok');
        }
      } catch (err) {
        say('Publish failed: ' + err.message + ' — check the Worker URL and that this origin is in ALLOWED_ORIGINS.', 'err');
      } finally {
        publishBtn.disabled = false;
      }
    });
  }

  // Sub-tab toggle between the promo grid and the Console (wired once; this
  // module only initialises the first time the Campaign tab is opened).
  function showSub(sub) {
    root.hidden = sub !== 'promos';
    consoleRoot.hidden = sub !== 'console';
    document.getElementById('csub-promos').classList.toggle('active', sub === 'promos');
    document.getElementById('csub-console').classList.toggle('active', sub === 'console');
    // A hidden thumb never fires mouseleave, so the zoom overlay can be left
    // showing a now-hidden image behind the Console panel — clear it explicitly.
    const overlay = document.getElementById('img-zoom-overlay');
    if (overlay) overlay.classList.remove('show');
    if (sub === 'console') renderConsole();
  }
  document.getElementById('csub-promos').addEventListener('click', () => showSub('promos'));
  document.getElementById('csub-console').addEventListener('click', () => showSub('console'));
};

/* The KLSE Monitor tab was removed when the Macro tab became the full
 * forecasting engine. AlphaSpike keeps its own Streamlit dashboard; its
 * exporter (tools/export_klse.py) and data/klse.json are left in place so the
 * tab can be restored by re-adding the panel to index.html and the module here. */

// ── boot ────────────────────────────────────────────────────────────────────
showTab((location.hash || '#fx').slice(1));
window.addEventListener('hashchange', () => showTab((location.hash || '#fx').slice(1)));
