/* Personal Dynamic Dashboard — tab router + four tab modules.
 * Each tab renders from a JSON feed and lazy-initialises on first view.
 * Zero dependencies; charts are hand-rolled inline SVG. */
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
function agoLabel(iso) {
  if (!iso) return 'unknown';
  const then = new Date(iso), now = new Date();
  const h = Math.round((now - then) / 36e5);
  if (h < 1) return 'just now';
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

/* Reusable multi-series line chart (inline SVG). series: [{name, values, color}].
 * opts.normalize indexes each series to 100 at its first point (shape comparison). */
function lineChart(series, dates, opts = {}) {
  const W = opts.w || 860, H = opts.h || 220, padL = 46, padR = 12, padT = 10, padB = 22;
  const norm = v0 => opts.normalize ? (x => v0 ? (x / v0) * 100 : x) : (x => x);
  const prepped = series.filter(s => s.values && s.values.length).map(s => ({
    ...s, plot: s.values.map(norm(s.values.find(v => v != null) ?? 1))
  }));
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

// ── tab controller ────────────────────────────────────────────────────────
const TABS = ['fx', 'macro', 'campaign', 'klse'];
const started = {};
const MODS = {}; // id -> init fn, registered below
function showTab(id) {
  if (!TABS.includes(id)) id = 'fx';
  TABS.forEach(t => {
    document.getElementById('panel-' + t).hidden = t !== id;
    document.getElementById('tab-' + t).classList.toggle('active', t === id);
  });
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
MODS.macro = async function initMacro() {
  const root = document.getElementById('macro-body');
  let d;
  try { d = await fetchJSON(CFG.feeds.macro); }
  catch (e) { root.innerHTML = `<div class="errbox">Couldn't load macro feed (${esc(e.message)}). Expected at <code>${esc(CFG.feeds.macro)}</code>.</div>`; return; }

  const sample = d.meta && d.meta.sample;
  const rg = d.regime || {};
  const cards = (d.snapshot || []).map(s => {
    const dir = s.change == null ? '' : (s.change >= 0 ? 'up' : 'down');
    const arrow = s.change == null ? '' : (s.change >= 0 ? '▲' : '▼');
    const unit = s.unit === '%' ? '%' : (s.unit === '$' ? '' : '');
    const pre = s.unit === '$' ? '$' : '';
    return `<div class="stat">
      <span class="k">${esc(s.label)} <span class="chip freq">${esc(s.freq || '')}</span></span>
      <div class="v">${pre}${fmt(s.value)}${unit}</div>
      <div class="d ${dir}">${arrow} ${s.change == null ? '' : fmt(Math.abs(s.change))}</div>
      <div class="meta">as of ${esc(s.as_of)}</div>
    </div>`;
  }).join('');

  const ov = d.overlay || {};
  const overlayChart = lineChart([
    { name: 'Real yield', values: ov.series && ov.series.real_yield, color: cv('--s1') },
    { name: 'Gold', values: ov.series && ov.series.gold, color: cv('--s2') },
    { name: 'S&P 500', values: ov.series && ov.series.sp500, color: cv('--s3') }
  ], ov.dates, { normalize: true, w: 860 });

  const lag = d.lag || {};
  const lagChart = lineChart([
    { name: 'Unemployment', values: lag.unemployment, color: cv('--s1') },
    { name: `Real yield (led ${lag.lead_months || 15}m)`, values: lag.real_yield_lead, color: cv('--s2') }
  ], lag.dates, { normalize: true, w: 860, h: 190 });

  root.innerHTML = `
    ${sample ? `<div class="disclaimer">⚠ Showing <b>sample</b> data for UI review — wire the FRED/Stooq feed (Phase B) to go live. ${esc(d.meta.data_note || '')}</div>` : ''}
    <div class="banner regime-${esc(rg.label || 'Mixed')}">
      <span class="b-ico">🧭</span>
      <div><div class="b-title">Regime: ${esc(rg.label || '—')}</div>
        <div class="b-detail">${esc(rg.detail || '')}</div>
        <div class="b-caveat">${esc(rg.caveat || 'Heuristic, not a signal.')}</div></div>
    </div>
    <div class="stat-grid">${cards}</div>
    <div class="card-block"><h3>Real yield · Gold · S&amp;P 500 <span class="muted">— indexed to 100 at window start</span></h3>${overlayChart}
      <p class="muted" style="margin-top:6px">${esc(ov.note || '')}</p></div>
    <div class="card-block"><h3>Unemployment vs lagged real yield</h3>${lagChart}
      <p class="muted" style="margin-top:6px">${esc(lag.note || '')}</p></div>
    <div class="freshline"><span>${esc((d.meta && d.meta.disclaimer) || '')}</span></div>`;
};

// ════════════════════════════════ TAB 3 · CAMPAIGN ════════════════════════
MODS.campaign = async function initCampaign() {
  const root = document.getElementById('campaign-body');
  let d;
  try { d = await fetchJSON(CFG.feeds.promotions); }
  catch (e) { root.innerHTML = `<div class="errbox">Couldn't load promotions feed (${esc(e.message)}). Expected at <code>${esc(CFG.feeds.promotions)}</code>.</div>`; return; }

  const list = d.promotions || [];
  const sample = d.meta && d.meta.sample;
  if (!list.length) { root.innerHTML = `<div class="emptybox">No promotions yet. Run <code>tools/scrape_campaign.py</code>, summarise the T&amp;C, then drop <code>promotions.json</code> here.</div>`; return; }

  // "New today" = first detected on the page today. Use the feed's own `today`
  // (the date the producer ran) if present, else the viewer's local date.
  const today = (d.meta && d.meta.today) || new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD
  const fmtDate = s => { const t = new Date(s); return Number.isNaN(+t) ? esc(s) : t.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); };
  list.forEach(p => p._new = p.first_seen && p.first_seen === today);
  const newCount = list.filter(p => p._new).length;

  // Category filter values, newest-flagged first for visibility.
  const cats = ['All', ...[...new Set(list.map(p => p.category).filter(Boolean))].sort()];
  let active = 'All';

  const cardHTML = p => `
    <article class="promo${p._new ? ' is-new' : ''}">
      ${p._new ? '<span class="new-badge">🆕 NEW</span>' : ''}
      <div class="thumb">${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy" onerror="this.parentNode.textContent='No image'">` : 'No image'}</div>
      <div class="body">
        ${p.category ? `<div class="cat">${esc(p.category)}</div>` : ''}
        <h3>${esc(p.title)}</h3>
        ${p.period ? `<div class="period">🗓 Valid: ${esc(p.period)}</div>` : ''}
        <div class="tnc">${esc(p.tnc_summary || 'No summary yet.')}</div>
        ${p.first_seen ? `<div class="seen${p._new ? ' new' : ''}">${p._new ? '🆕 Added today' : '👁 Listed since'} ${fmtDate(p.first_seen)}</div>` : ''}
        ${p.link ? `<a class="cta" href="${esc(p.link)}" target="_blank" rel="noopener">View full promotion ➔</a>` : ''}
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
      <div class="disclaimer">⚠ Summaries are AI-generated for convenience — <b>always verify the official Terms &amp; Conditions</b> on the bank's page before relying on them.${sample ? ' Currently showing <b>sample</b> data.' : ''}</div>
      ${newCount ? `<div class="banner new-banner"><span class="b-ico">🆕</span><div><div class="b-title">${newCount} new promotion${newCount > 1 ? 's' : ''} today</div><div class="b-detail">First detected on the page on ${fmtDate(today)}. Marked 🆕 below.</div></div></div>` : ''}
      <div class="fchips" role="tablist" aria-label="Filter by category">${chips}</div>
      <div class="promo-grid">${filtered.map(cardHTML).join('') || '<div class="emptybox">No promotions in this category.</div>'}</div>
      <div class="freshline"><span>Source: <a href="${esc((d.meta && d.meta.source) || '#')}" target="_blank" rel="noopener">Public Bank promotions</a></span>
        <span>· Updated <b>${esc((d.meta && d.meta.generated_at || '').slice(0, 10))}</b></span>
        <span>· ${filtered.length}/${list.length} shown</span></div>`;
    root.querySelectorAll('.fchip').forEach(b => b.addEventListener('click', () => { active = b.dataset.cat; render(); }));
  }
  render();
};

// ════════════════════════════════ TAB 4 · KLSE ════════════════════════════
MODS.klse = async function initKLSE() {
  const root = document.getElementById('klse-body');
  // Privacy by design: the snapshot feed (data/klse.json) is your private trading
  // data and is NOT published to a public host (it's git-ignored). When it's
  // absent — i.e. online — show a protected hand-off to the key-gated live app
  // instead of an error. Locally the file exists, so the full snapshot renders.
  const handoff = (why) => {
    const btn = CFG.klseDashboardUrl
      ? `<a class="live-link" href="${esc(CFG.klseDashboardUrl)}" target="_blank" rel="noopener" style="height:40px">Open the private AlphaSpike dashboard ↗</a>`
      : `<p class="muted">Set the tunnel URL in <code>config.js</code> (<code>klseDashboardUrl</code>) or run <code>localStorage.setItem('klse_url','https://…')</code> to show the launch button here. Setup: <code>KLSE_Monitor/deploy/ONLINE_SETUP.md</code>.</p>`;
    root.innerHTML = `
      <div class="emptybox" style="text-align:left">
        <div style="font-size:15px;font-weight:650;color:var(--ink)">🔒 KLSE data is private</div>
        <p class="muted" style="margin:6px 0 12px">${esc(why)} Your trading book isn't published to this public dashboard. The full 3-tab AlphaSpike app (Conviction · Exit Advisor · Signal ledger) lives behind an access key on your Cloudflare Tunnel.</p>
        ${btn}
      </div>`;
  };
  let d;
  try { d = await fetchJSON(CFG.feeds.klse); }
  catch (e) { handoff('The read-only snapshot feed is not available here.'); return; }

  const conv = d.conviction || {}, sig = d.signals || {}, tr = d.trades || {}, fn = d.funnel || {};
  const kpi = (k, v, sub, cls) => `<div class="stat"><span class="k">${k}</span><div class="v ${cls || ''}">${v}</div><div class="meta">${sub || ''}</div></div>`;

  // KPI row
  const kpis = [
    kpi('Signal hit rate', sig.hit_rate == null ? '—' : sig.hit_rate + '%', `${sig.resolved || 0} resolved of ${sig.total || 0}`),
    kpi('Avg fwd return (20d)', sig.avg_fwd_ret_20 == null ? '—' : (sig.avg_fwd_ret_20 > 0 ? '+' : '') + sig.avg_fwd_ret_20 + '%', 'on resolved signals', sig.avg_fwd_ret_20 >= 0 ? 'pos' : 'neg'),
    kpi('Closed-trade win rate', tr.win_rate == null ? '—' : tr.win_rate + '%', `${tr.count || 0} closed trades`),
    kpi('Avg alpha vs KLCI', tr.avg_alpha_pct == null ? '—' : (tr.avg_alpha_pct > 0 ? '+' : '') + tr.avg_alpha_pct + '%', 'per closed trade', tr.avg_alpha_pct >= 0 ? 'pos' : 'neg')
  ].join('');

  // Funnel
  const stages = fn.stages || [];
  const fmax = Math.max(1, ...stages.map(s => s.count || 0));
  const funnel = stages.map(s => `
    <div class="funnel-row"><span class="fl">${esc(s.label)}</span>
      <span class="fbar" style="width:${Math.max(2, (s.count / fmax) * 100).toFixed(1)}%"></span>
      <span class="fn">${num(s.count)}</span></div>`).join('');

  // Conviction watchlist
  const rows = (conv.rows || []).map(r => {
    const tags = `${r.gate6 ? '<span class="tag gate6">GATE6</span>' : ''}${r.cold_eye ? '<span class="tag cold">COLD-EYE</span>' : ''}`;
    const reasons = (r.reasons || []).slice(0, 2).map(esc).join(' · ');
    return `<tr>
      <td>${num(r.rank)}</td>
      <td class="l"><b>${esc(r.name || r.ticker)}</b> <span class="muted">${esc(r.ticker)}</span>${tags}<div class="reasons">${reasons}</div></td>
      <td>${fmt(r.conviction)}</td>
      <td>${r.last_close == null ? '—' : fmt(r.last_close)}</td>
      <td>${r.rel_volume == null ? '—' : r.rel_volume + '×'}</td>
      <td>${r.liquidity_myr == null ? '—' : 'RM' + num(r.liquidity_myr)}</td>
    </tr>`;
  }).join('');
  const watchlist = rows ? `<div class="table-scroll"><table class="data-table">
    <thead><tr><th>#</th><th class="l">Counter</th><th>Conviction</th><th>Close</th><th>Rel vol</th><th>Liquidity</th></tr></thead>
    <tbody>${rows}</tbody></table></div>` : '<div class="emptybox">No conviction rows in the latest run.</div>';

  // Positions
  const pos = (d.positions || []).map(p => `<tr>
    <td class="l"><b>${esc(p.ticker)}</b></td>
    <td>${fmt(p.entry_price)}</td>
    <td>${esc(p.entry_date)}</td>
    <td><span class="pill ${p.status === 'hold' ? 'hold' : 'sold'}">${esc(p.status)}</span></td>
    <td class="${(p.realised_pct || 0) >= 0 ? 'pos' : 'neg'}">${p.realised_pct == null ? '—' : (p.realised_pct > 0 ? '+' : '') + p.realised_pct + '%'}</td>
  </tr>`).join('');
  const positions = pos ? `<div class="table-scroll"><table class="data-table">
    <thead><tr><th class="l">Ticker</th><th>Entry</th><th>Entry date</th><th>Status</th><th>Realised</th></tr></thead>
    <tbody>${pos}</tbody></table></div>` : '<div class="emptybox">No tracked positions.</div>';

  // Recent closed trades
  const trades = (tr.recent || []).map(t => `<tr>
    <td class="l"><b>${esc(t.ticker)}</b></td>
    <td>${esc(t.exit_date)}</td>
    <td>${esc(t.exit_reason || '')}</td>
    <td class="${(t.return_pct || 0) >= 0 ? 'pos' : 'neg'}">${t.return_pct == null ? '—' : (t.return_pct > 0 ? '+' : '') + t.return_pct + '%'}</td>
    <td class="${(t.alpha_pct || 0) >= 0 ? 'pos' : 'neg'}">${t.alpha_pct == null ? '—' : (t.alpha_pct > 0 ? '+' : '') + t.alpha_pct + '%'}</td>
  </tr>`).join('');
  const tradeTable = trades ? `<div class="table-scroll"><table class="data-table">
    <thead><tr><th class="l">Ticker</th><th>Exit</th><th>Reason</th><th>Return</th><th>Alpha</th></tr></thead>
    <tbody>${trades}</tbody></table></div>` : '<div class="emptybox">No closed trades yet.</div>';

  const gen = d.meta && d.meta.generated_at;
  const liveBtn = CFG.klseDashboardUrl
    ? `<a class="live-link" href="${esc(CFG.klseDashboardUrl)}" target="_blank" rel="noopener">Open full interactive dashboard ↗</a>`
    : '';
  root.innerHTML = `
    <div class="klse-topbar"><div class="disclaimer" style="margin:0;flex:1">⚠ ${esc((d.meta && d.meta.disclaimer) || 'Screener output, not trade advice.')} This is a read-only snapshot — the live 3-tab app (Conviction · Exit Advisor · Ledger) is where you enter positions & run ad-hoc lookups.</div>${liveBtn}</div>
    <div class="kpi-row">${kpis}</div>
    <div class="card-block"><h3>Today's conviction watchlist <span class="muted">— run ${esc(conv.run_date || '?')}, top ${(conv.rows || []).length}</span></h3>${watchlist}</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px" class="klse-two">
      <div class="card-block" style="margin-top:0"><h3>Screening funnel <span class="muted">— ${esc(fn.run_date || '')}</span></h3><div class="funnel">${funnel || '<p class="muted">No funnel data.</p>'}</div></div>
      <div class="card-block" style="margin-top:0"><h3>Tracked positions</h3>${positions}</div>
    </div>
    <div class="card-block"><h3>Recent closed trades <span class="muted">— return &amp; alpha vs FBMKLCI</span></h3>${tradeTable}</div>
    <div class="freshline"><span>Exported <b>${esc((gen || '').slice(0, 16).replace('T', ' '))} UTC</b> (${agoLabel(gen)})</span>
      <span>· from ${esc((d.meta && d.meta.source) || 'local DB')}</span>
      <span>· local snapshot — refresh by re-running the exporter</span></div>`;
};

// ── boot ────────────────────────────────────────────────────────────────────
showTab((location.hash || '#fx').slice(1));
window.addEventListener('hashchange', () => showTab((location.hash || '#fx').slice(1)));
