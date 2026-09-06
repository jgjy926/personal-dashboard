/* Macro Forecasting Engine — the Macro tab's ten sub-pages.
 *
 * Kept in its own file rather than appended to app.js: app.js was already ~47KB
 * covering three unrelated tabs, and this adds a ten-page application of its own.
 * It follows every app.js convention (same utils, same CSS tokens, same feed
 * pattern, zero dependencies, hand-rolled inline SVG) and is loaded before it,
 * exposing exactly one global — window.MACRO_ENGINE — which app.js calls.
 *
 * Feed: data/macro_engine.json, written by the engine's `python main.py daily`.
 * Every number shown here is computed upstream; nothing in this file does
 * analysis. That is deliberate — the terminal report, the database and this
 * dashboard all render one snapshot, so they cannot disagree.
 */
'use strict';
(function () {
  const C = () => getComputedStyle(document.documentElement);
  const tok = n => C().getPropertyValue(n).trim();
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pct = (v, d = 0) => v == null ? '—' : (v * 100).toFixed(d) + '%';
  const sgn = (v, d = 2) => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(d);
  const n2 = (v, d = 2) => v == null ? '—' : Number(v).toFixed(d);

  /* Score → colour. One ramp used by every score display on every page, so a
   * given colour always means the same thing regardless of which panel it is in. */
  function scoreColor(s) {
    if (s == null) return tok('--muted');
    if (s <= -0.45) return tok('--red');
    if (s <= -0.15) return tok('--amber');
    if (s < 0.15) return tok('--muted');
    return tok('--green');
  }
  /* Probability → colour, using the engine's own alert thresholds so the
   * dashboard cannot disagree with the alert channel about what "high" means. */
  function probColor(p) {
    if (p == null) return tok('--muted');
    if (p >= 0.70) return tok('--red');
    if (p >= 0.50) return tok('--amber');
    if (p >= 0.30) return tok('--s2');
    return tok('--green');
  }
  const ARROW = { '^^^': '▲▲▲', '^^': '▲▲', '^': '▲', '->': '▶', 'v': '▼', 'vv': '▼▼', 'vvv': '▼▼▼', '?': '·' };

  // ── small SVG primitives ────────────────────────────────────────────────
  function sparkline(points, key, color, w = 120, h = 30) {
    const vals = (points || []).map(p => p[key]).filter(v => v != null && Number.isFinite(v));
    if (vals.length < 2) return '<span class="muted" style="font-size:11px">no history</span>';
    const lo = Math.min(...vals), hi = Math.max(...vals), span = (hi - lo) || 1;
    const d = vals.map((v, i) =>
      `${i ? 'L' : 'M'}${(i / (vals.length - 1) * w).toFixed(1)},${(h - (v - lo) / span * h).toFixed(1)}`
    ).join(' ');
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <path d="${d}" fill="none" stroke="${color}" stroke-width="1.6"/></svg>`;
  }

  /* Half-circle gauge. Drawn as two arcs so the track stays visible at low
   * values. `value` is 0..1 and is read as RISK: green at the left, red at the
   * right. Feed it a risk quantity only -- an earlier draft passed regime
   * STRENGTH here, which painted "we are confident this label is right" bright
   * red, exactly inverting the meaning a reader takes from the colour. */
  function gauge(value, label, sub) {
    const v = Math.max(0, Math.min(1, value == null ? 0 : value));
    const R = 70, CX = 90, CY = 90;
    const arc = (frac) => {
      const a = Math.PI * (1 - frac);
      return `${(CX + R * Math.cos(a)).toFixed(1)},${(CY - R * Math.sin(a)).toFixed(1)}`;
    };
    const col = v >= 0.66 ? tok('--red') : v >= 0.33 ? tok('--amber') : tok('--green');
    return `<svg class="gauge" viewBox="0 0 180 108" role="img" aria-label="${esc(label)}">
      <path d="M ${arc(0)} A ${R} ${R} 0 0 1 ${arc(1)}" fill="none" stroke="${tok('--line')}" stroke-width="14" stroke-linecap="round"/>
      ${v > 0.01 ? `<path d="M ${arc(0)} A ${R} ${R} 0 ${v > 0.5 ? 1 : 0} 1 ${arc(v)}" fill="none" stroke="${col}" stroke-width="14" stroke-linecap="round"/>` : ''}
      <text x="90" y="86" text-anchor="middle" font-size="30" font-weight="700" fill="${tok('--ink')}">${Math.round(v * 100)}</text>
      <text x="90" y="102" text-anchor="middle" font-size="10" fill="${tok('--muted')}">${esc(sub || '')}</text>
    </svg>`;
  }

  /* Donut for the leading-indicator breadth split. */
  function donut(parts, centerTop, centerBottom) {
    const total = parts.reduce((a, p) => a + p.value, 0) || 1;
    const R = 54, r = 36, CX = 70, CY = 70;
    let angle = -Math.PI / 2, segs = '';
    parts.forEach(p => {
      if (!p.value) return;
      const sweep = (p.value / total) * Math.PI * 2;
      const end = angle + sweep;
      const large = sweep > Math.PI ? 1 : 0;
      const pt = (rad, ang) => `${(CX + rad * Math.cos(ang)).toFixed(2)},${(CY + rad * Math.sin(ang)).toFixed(2)}`;
      segs += `<path d="M ${pt(R, angle)} A ${R} ${R} 0 ${large} 1 ${pt(R, end)} L ${pt(r, end)} A ${r} ${r} 0 ${large} 0 ${pt(r, angle)} Z" fill="${p.color}"/>`;
      angle = end;
    });
    return `<div class="donut-wrap">
      <svg viewBox="0 0 140 140" class="donut" role="img" aria-label="breadth">
        ${segs}
        <text x="70" y="68" text-anchor="middle" font-size="26" font-weight="700" fill="${tok('--ink')}">${esc(centerTop)}</text>
        <text x="70" y="84" text-anchor="middle" font-size="9" fill="${tok('--muted')}">${esc(centerBottom)}</text>
      </svg>
      <ul class="donut-key">${parts.map(p =>
        `<li><span class="swatch" style="background:${p.color}"></span>${esc(p.label)}
          <b>${p.value}</b> <span class="muted">(${Math.round(p.value / total * 100)}%)</span></li>`).join('')}</ul>
    </div>`;
  }

  /* Horizontal bar, used for probabilities and scenario weights. */
  function bar(frac, color, height = 10) {
    const w = Math.max(0, Math.min(1, frac || 0)) * 100;
    return `<div class="mbar" style="height:${height}px"><i style="width:${w}%;background:${color}"></i></div>`;
  }

  /* Diverging bar for scores in [-1,+1], zero-centred so sign reads at a glance. */
  function divBar(score) {
    if (score == null) return '<div class="mbar div"><i style="width:0"></i></div>';
    const w = Math.min(Math.abs(score), 1) * 50;
    const left = score < 0 ? 50 - w : 50;
    return `<div class="mbar div"><span class="mid"></span>
      <i style="left:${left}%;width:${w}%;background:${scoreColor(score)}"></i></div>`;
  }

  function lineChartLocal(series, dates, opts = {}) {
    // app.js owns the shared chart; reuse it when present so both tabs draw
    // identically, and degrade to a sparkline-style fallback if load order ever
    // changes.
    if (typeof window.lineChart === 'function') return window.lineChart(series, dates, opts);
    const first = series.find(s => s.values && s.values.length);
    return first ? sparkline(first.values.map(v => ({ v })), 'v', first.color, 800, 160)
                 : '<p class="muted">No data.</p>';
  }

  /* The historical recession-probability line (spec 26), with the months a
   * recession actually followed shaded behind it. Drawn here rather than through
   * the shared lineChart because that helper has no concept of event shading,
   * and the shading is the part that makes the line interpretable. */
  function probabilityHistory(points) {
    const pts = (points || []).filter(p => p.probability != null);
    if (pts.length < 8) return '<p class="muted">No backtest history yet — run <code>python main.py monthly</code>.</p>';
    const W = 860, H = 240, padL = 40, padR = 12, padT = 12, padB = 26;
    const hi = Math.max(0.35, ...pts.map(p => Math.max(p.probability, p.score || 0)));
    const x = i => padL + (i / Math.max(1, pts.length - 1)) * (W - padL - padR);
    const y = v => padT + (1 - v / hi) * (H - padT - padB);

    // Shade the runs where a recession did follow within 12 months.
    let bands = '', start = null;
    pts.forEach((p, i) => {
      if (p.actual === 1 && start === null) start = i;
      if ((p.actual !== 1 || i === pts.length - 1) && start !== null) {
        const x0 = x(start), x1 = x(p.actual === 1 ? i : i - 1);
        bands += `<rect x="${x0.toFixed(1)}" y="${padT}" width="${Math.max(2, x1 - x0).toFixed(1)}" height="${H - padT - padB}" fill="${tok('--red')}" opacity="0.10"/>`;
        start = null;
      }
    });

    const path = (key, color, width, dash) => {
      let pen = false;
      const d = pts.map((p, i) => {
        const v = p[key];
        if (v == null) { pen = false; return ''; }
        const cmd = pen ? 'L' : 'M'; pen = true;
        return `${cmd}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      }).filter(Boolean).join(' ');
      return d ? `<path d="${d}" fill="none" stroke="${color}" stroke-width="${width}"${dash ? ` stroke-dasharray="${dash}"` : ''}/>` : '';
    };

    const grid = [0, hi / 2, hi].map(v =>
      `<line x1="${padL}" y1="${y(v).toFixed(1)}" x2="${W - padR}" y2="${y(v).toFixed(1)}" stroke="${tok('--line')}"/>
       <text x="4" y="${(y(v) + 3).toFixed(1)}" fill="${tok('--muted')}" font-size="10">${(v * 100).toFixed(0)}%</text>`).join('');

    let xl = '';
    const step = Math.ceil(pts.length / 8);
    for (let i = 0; i < pts.length; i += step) {
      xl += `<text x="${x(i).toFixed(1)}" y="${H - 8}" fill="${tok('--muted')}" font-size="9" text-anchor="middle">${esc(pts[i].date.slice(0, 7))}</text>`;
    }

    return `<div class="macro-chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="historical recession probability">
        ${bands}${grid}
        ${path('climatology', tok('--muted'), 1, '4 3')}
        ${path('score', tok('--s2'), 1.4)}
        ${path('probability', tok('--accent'), 2.2)}
        ${xl}
      </svg></div>
      <div class="legend">
        <span class="k"><span class="swatch" style="background:${tok('--accent')}"></span>Published probability</span>
        <span class="k"><span class="swatch" style="background:${tok('--s2')}"></span>Raw score</span>
        <span class="k"><span class="swatch" style="background:${tok('--muted')}"></span>Climatology</span>
        <span class="k"><span class="swatch" style="background:${tok('--red')};opacity:.3"></span>Recession followed within 12m</span>
      </div>`;
  }

  function table(headers, rows, cls = '') {
    return `<div class="table-scroll"><table class="data-table ${cls}">
      <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>
    </table></div>`;
  }

  function noteBox(text, kind = 'muted') {
    return `<p class="engine-note ${kind}">${esc(text)}</p>`;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // PAGE 1 — EXECUTIVE
  // ═══════════════════════════════════════════════════════════════════════
  function pageExecutive(d) {
    const h = d.headline || {};
    const rec = (d.recession && d.recession.horizons) || [];
    const b = (d.leading && d.leading.breadth) || {};
    const r = d.recommendation || {};

    const timeline = (h.regime_order || []).map((g, i) => {
      const on = i === h.regime_position;
      return `<li class="${on ? 'on' : ''}"><span class="node"></span>
        <span class="lbl">${esc(g.replace(/_/g, ' '))}</span></li>`;
    }).join('');

    const probRow = rec.map(x => `
      <div class="prob-cell">
        <span class="k">${x.horizon_m} Months</span>
        <b style="color:${probColor(x.probability)}">${pct(x.probability, 1)}</b>
        ${bar(x.probability, probColor(x.probability), 6)}
      </div>`).join('');

    const factorCards = (d.factors || []).map(f => `
      <div class="fcard">
        <div class="fc-head"><span>${esc(f.label)}</span>
          <span class="chip">${esc((f.detail && f.detail.state) || f.n_inputs + ' obs')}</span></div>
        <div class="fc-score" style="color:${scoreColor(f.score)}">${sgn(f.score)}</div>
        <div class="fc-sub" style="color:${scoreColor(f.score)}">${esc(scoreWord(f.score))}
          <span class="muted">${ARROW[f.arrow] || ''}</span></div>
        ${sparkline(seriesForFactor(d, f), 'signal', scoreColor(f.score), 130, 26)}
      </div>`).join('');

    return `
      <div class="exec-top">
        <div class="card-block regime-card">
          <h3>Macro Regime</h3>
          <div class="regime-name" style="color:${scoreColor(h.composite)}">${esc(h.regime_display || '—')}</div>
          ${gauge(cycleRisk(h), 'cycle position', 'Cycle Position')}
          <p class="muted small">
            Composite <b style="color:${scoreColor(h.composite)}">${sgn(h.composite)}</b>
            · label confidence <b>${pct(h.regime_strength, 0)}</b>
          </p>
          <p class="muted small">${esc(h.summary || '')}</p>
        </div>

        <div class="card-block prob-card">
          <div class="block-head"><h3>Recession Probability</h3>
            <span class="chip ${(d.recession && d.recession.calibrated_all) ? 'ok' : 'warn'}">
              ${(d.recession && d.recession.calibrated_all) ? 'calibrated' : 'partly uncalibrated'}</span></div>
          <div class="prob-row">${probRow}</div>
          <div class="prob-foot">
            <span>Trend: <b style="color:${h.trend === 'DETERIORATING' ? tok('--red') : h.trend === 'IMPROVING' ? tok('--green') : tok('--muted')}">${esc(h.trend || '—')}</b></span>
            <span>Confidence: <b>${esc(h.confidence_pct || '—')}</b> <span class="chip">${esc(h.confidence_band || '')}</span></span>
          </div>
        </div>

        <div class="card-block takeaway">
          <h3>Key Takeaway</h3>
          <p>${esc(r.narrative || '')}</p>
        </div>
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Macro Factors <span class="muted small">(+1 = economically positive)</span></h3></div>
        <div class="fcard-grid">${factorCards}</div>
      </div>

      <div class="exec-mid">
        <div class="card-block">
          <h3>Leading Indicator Breadth</h3>
          ${donut([
            { label: 'Weakening', value: b.weakening || 0, color: tok('--red') },
            { label: 'Neutral', value: b.neutral || 0, color: tok('--amber') },
            { label: 'Improving', value: b.improving || 0, color: tok('--green') }
          ], String(b.total || 0), 'Indicators')}
        </div>
        <div class="card-block">
          <h3>Cycle Position</h3>
          <ul class="cycle">${timeline}</ul>
          <p class="muted small">Strength ${pct(h.regime_strength, 0)} — how cleanly today's readings
            match this label rather than an adjacent one.</p>
        </div>
        <div class="card-block">
          <h3>Scenarios</h3>
          ${((d.scenarios && d.scenarios.scenarios) || []).map(s => `
            <div class="scen-row"><span class="k">${esc(s.scenario)}</span>
              ${bar(s.probability, s.scenario === 'CRISIS' ? tok('--red')
                    : s.scenario === 'BEAR' ? tok('--amber')
                    : s.scenario === 'BULL' ? tok('--green') : tok('--accent'))}
              <b>${pct(s.probability, 0)}</b></div>`).join('')}
        </div>
      </div>

      <div class="exec-mid">
        <div class="card-block">
          <h3>Top Negative Drivers</h3>
          <ol class="drivers">${(r.drivers || []).map(x =>
            `<li><span>${esc(x.label)}</span><b style="color:${tok('--red')}">${sgn(x.signal)}</b></li>`).join('')}</ol>
        </div>
        <div class="card-block">
          <h3>Offsetting Factors</h3>
          <ol class="drivers">${(r.offsetting || []).map(x =>
            `<li><span>${esc(x.label)}</span><b style="color:${tok('--green')}">${sgn(x.signal)}</b></li>`).join('')}</ol>
        </div>
        <div class="card-block">
          <h3>What Changes the Forecast?</h3>
          <ul class="invalidation">
            ${(r.improves_if || []).map(x => `<li class="up">✓ ${esc(x)}</li>`).join('')}
            ${(r.worsens_if || []).map(x => `<li class="dn">⚠ ${esc(x)}</li>`).join('')}
          </ul>
        </div>
      </div>

      ${(r.caveats || []).length ? `<div class="disclaimer"><b>Caveats:</b><ul style="margin:6px 0 0 18px">
        ${r.caveats.map(c => `<li>${esc(c)}</li>`).join('')}</ul></div>` : ''}
    `;
  }

  /* How far through the cycle today's regime sits, 0 (expansion) to 1
   * (recession). This is the quantity the gauge should show: it is monotone in
   * risk, so green-to-red reads correctly. Regime STRENGTH is a different thing
   * -- how cleanly the readings match this label rather than an adjacent one --
   * and is reported as a number beside it instead of being coloured. */
  function cycleRisk(h) {
    const order = h.regime_order || [];
    const i = h.regime_position;
    if (!order.length || i == null || i < 0) return 0;
    // REGIME_ORDER runs expansion -> ... -> recession -> recovery, so the last
    // two entries are the way BACK DOWN in risk, not further up.
    const peak = order.indexOf('RECESSION');
    if (peak < 0) return i / Math.max(1, order.length - 1);
    return i <= peak ? i / peak : Math.max(0, 1 - (i - peak) / Math.max(1, order.length - 1 - peak));
  }

  function scoreWord(s) {
    if (s == null) return 'Unavailable';
    if (s <= -0.45) return 'Strong Weakness';
    if (s <= -0.15) return 'Weakness';
    if (s < 0.15) return 'Neutral';
    if (s < 0.45) return 'Strength';
    return 'Strong Strength';
  }

  /* The factor's OWN score history, computed upstream by
   * modules/factors.factor_history(). An earlier draft substituted a member
   * series' history when the factor's was unavailable — a different quantity
   * under the factor's label, which is worse than showing nothing. Now the
   * fallback is an explicit "no history" note. */
  function seriesForFactor(d, f) {
    const h = (d.factor_history || {})[f.factor];
    return (h || []).map(p => ({ signal: p.value }));
  }

  // ═══════════════════════════════════════════════════════════════════════
  // PAGE 2 — RECESSION FORECAST
  // ═══════════════════════════════════════════════════════════════════════
  function pageRecession(d) {
    const rec = d.recession || {};
    const rows = (rec.horizons || []).map(h => [
      `<b>${h.horizon_m}M</b>`,
      `<span style="color:${probColor(h.probability)};font-weight:700">${pct(h.probability, 1)}</span>`,
      pct(h.raw_score, 1),
      pct(h.base_rate, 0),
      pct(h.signal_weight, 0),
      esc(h.model),
      h.auc == null ? '—' : h.auc.toFixed(3),
      `<span class="chip ${h.calibrated ? 'ok' : 'warn'}">${h.calibrated ? 'yes' : 'no'}</span>`,
      `<span class="chip ${h.band === 'NORMAL' ? 'ok' : h.band === 'ELEVATED' ? 'warn' : 'bad'}">${esc(h.band)}</span>`
    ]);

    const drivers = (rec.drivers || []).map(x => [
      esc(x.label),
      `<span style="color:${scoreColor(x.signal)}">${sgn(x.signal)}</span>`,
      x.percentile == null ? '—' : pct(x.percentile, 0),
      pct(x.importance, 1),
      `<span class="chip ${x.pushing.includes('toward') ? 'bad' : x.pushing.includes('away') ? 'ok' : ''}">${esc(x.pushing)}</span>`
    ]);

    return `
      <div class="card-block">
        <div class="block-head"><h3>Recession Probability by Horizon</h3></div>
        ${table(['Horizon', 'Calibrated', 'Raw score', 'Base rate', 'Signal wt', 'Model', 'AUC', 'Calibrated?', 'Band'], rows)}
        ${noteBox(rec.score_vs_probability_note || '')}
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Probability Curve</h3></div>
        <div class="hbars">${(rec.horizons || []).map(h => `
          <div class="hbar-row">
            <span class="k">${h.horizon_m}M</span>
            ${bar(h.probability, probColor(h.probability), 14)}
            <b style="color:${probColor(h.probability)}">${pct(h.probability, 1)}</b>
            <span class="muted small">raw ${pct(h.raw_score, 1)} · base ${pct(h.base_rate, 0)}</span>
          </div>`).join('')}</div>
        ${noteBox('The calibrated probability sits between the raw model score and the horizon\'s unconditional base rate. At longer horizons it leans harder on the base rate, because no indicator has demonstrated reliable 18-month precision.')}
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Historical Probability — 12-Month Horizon</h3>
          <span class="muted small">vintage-true backtest, not today\'s model replayed</span></div>
        ${probabilityHistory((d.recession_history && d.recession_history.length)
            ? d.recession_history
            : (d.backtest && d.backtest.probability_history) || [])}
        ${noteBox('This is what the engine WOULD have said at each date using only data '
          + 'available then. Redrawing it by running today\'s model over revised history '
          + 'would show a far better forecaster than ever existed.')}
      </div>

      <div class="card-block">
        <div class="block-head"><h3>What Is Driving the 12-Month Forecast</h3></div>
        ${table(['Indicator', 'Signal', 'Percentile', 'Model importance', 'Pushing'], drivers)}
        ${noteBox('Importance is how much the model relies on a feature; the signal is which way that feature currently points. Importance alone would list the same features every month regardless of the economy.')}
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Model Notes</h3></div>
        <ul class="notes">${(rec.horizons || []).map(h =>
          `<li><b>${h.horizon_m}M</b> — ${esc(h.note || '')}</li>`).join('')}</ul>
      </div>`;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // PAGE 3 — FACTOR ANALYSIS
  // ═══════════════════════════════════════════════════════════════════════
  function pageFactors(d) {
    const heat = (d.factors || []).map(f => [
      `<b>${esc(f.label)}</b>`,
      f.score == null ? '<span class="muted">unavailable</span>'
        : `<span style="color:${scoreColor(f.score)};font-weight:700">${sgn(f.score)}</span>`,
      divBar(f.score),
      `${ARROW[f.arrow] || '·'} <span class="muted">${sgn(f.momentum)}</span>`,
      pct(f.coverage, 0),
      String(f.n_inputs),
      `<span class="muted small">${esc(f.interpretation || '')}</span>`
    ]);

    const detail = (d.factors || []).map(f => {
      const rows = (f.contributions || [])
        .slice().sort((a, b) => (a.signal ?? 0) - (b.signal ?? 0))
        .map(c => [
          esc(c.label),
          `<span class="chip">${esc(c.role)}</span>`,
          c.raw == null ? '—' : Number(c.raw).toLocaleString(undefined, { maximumFractionDigits: 2 }),
          c.percentile == null ? '—' : pct(c.percentile, 0),
          `<span style="color:${scoreColor(c.signal)};font-weight:600">${sgn(c.signal)}</span>`,
          divBar(c.signal),
          sgn(c.momentum),
          `<span class="muted small">${esc(c.date || '')}</span>`
        ]);
      return `<details class="fdetail"><summary>
          <b>${esc(f.label)}</b>
          <span style="color:${scoreColor(f.score)};font-weight:700">${sgn(f.score)}</span>
          <span class="muted small">${f.n_inputs} indicators · coverage ${pct(f.coverage, 0)}</span>
        </summary>
        ${f.interpretation ? noteBox(f.interpretation) : ''}
        ${rows.length ? table(['Indicator', 'Role', 'Latest', 'Percentile', 'Signal', '', 'Momentum', 'As of'], rows)
                      : '<p class="muted">No signed contributors — this factor is derived structurally rather than as a weighted average.</p>'}
      </details>`;
    }).join('');

    return `
      <div class="card-block">
        <div class="block-head"><h3>Factor Heatmap</h3>
          <span class="muted small">+1 economically positive · −1 economically negative</span></div>
        ${table(['Factor', 'Score', '', 'Trend (3m)', 'Coverage', 'Inputs', 'Reading'], heat)}
      </div>
      <div class="card-block">
        <div class="block-head"><h3>Composite</h3></div>
        <div class="composite">
          <div><span class="k">Composite score</span>
            <b style="color:${scoreColor(d.headline && d.headline.composite)}">${sgn(d.headline && d.headline.composite)}</b></div>
          <div><span class="k">3-month momentum</span>
            <b style="color:${scoreColor(d.headline && d.headline.composite_momentum)}">${sgn(d.headline && d.headline.composite_momentum)}</b></div>
          <div><span class="k">Factor coverage</span>
            <b>${pct(d.composite_meta && d.composite_meta.coverage, 0)}</b></div>
        </div>
        ${(d.composite_meta && (d.composite_meta.missing || []).length)
          ? noteBox('Unavailable factors: ' + d.composite_meta.missing.join(', ') +
                    ' — their weight was redistributed across the remaining factors rather than counted as neutral.', 'warn')
          : noteBox('All nine factors available; weights are as configured.')}
      </div>
      <div class="card-block">
        <div class="block-head"><h3>Indicator Detail</h3>
          <span class="muted small">click a factor to expand</span></div>
        ${detail}
      </div>`;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // PAGE 4 — LEADING INDICATORS
  // ═══════════════════════════════════════════════════════════════════════
  function pageLeading(d) {
    const L = d.leading || {};
    const b = L.breadth || {};
    const ih = d.indices_history || {};
    const dates = (ih.leading || []).map(p => p.date.slice(0, 7));

    const rows = (L.indicators || []).map(i => [
      esc(i.label),
      `<span class="chip ${i.state === 'IMPROVING' ? 'ok' : i.state === 'WEAKENING' ? 'bad' : ''}">${esc(i.state)}</span>`,
      `<span style="color:${scoreColor(i.signal)};font-weight:600">${sgn(i.signal)}</span>`,
      divBar(i.signal),
      `<span style="color:${scoreColor(i.momentum)}">${sgn(i.momentum)}</span>`,
      i.percentile == null ? '—' : pct(i.percentile, 0),
      `<span class="muted small">${esc(i.date || '')}</span>`
    ]);

    return `
      <div class="exec-mid">
        <div class="card-block">
          <h3>Breadth</h3>
          ${donut([
            { label: 'Weakening', value: b.weakening || 0, color: tok('--red') },
            { label: 'Neutral', value: b.neutral || 0, color: tok('--amber') },
            { label: 'Improving', value: b.improving || 0, color: tok('--green') }
          ], String(b.total || 0), 'Indicators')}
          <p class="muted small">Net diffusion ${sgn(b.net_diffusion)} · ${pct(b.weakening_pct, 0)} weakening</p>
        </div>
        <div class="card-block">
          <h3>Persistence</h3>
          <div class="big" style="color:${(L.persistence || {}).direction === 'WEAKENING' ? tok('--red') : tok('--green')}">
            ${esc((L.persistence || {}).months || 0)}<span class="unit">months</span></div>
          <p class="muted small">${esc((L.persistence || {}).interpretation || '')}</p>
        </div>
        <div class="card-block">
          <h3>Turning Point</h3>
          <div class="big" style="color:${(L.turning_point || {}).turning ? tok('--red') : tok('--muted')}">
            ${(L.turning_point || {}).turning ? esc((L.turning_point || {}).direction) : 'None'}</div>
          <p class="muted small">${esc((L.turning_point || {}).interpretation || '')}</p>
        </div>
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Leading vs Coincident vs Lagging</h3></div>
        ${lineChartLocal([
          { name: 'Leading', values: (ih.leading || []).map(p => p.value), color: tok('--s1') },
          { name: 'Coincident', values: (ih.coincident || []).map(p => p.value), color: tok('--s2') },
          { name: 'Lagging', values: (ih.lagging || []).map(p => p.value), color: tok('--s3') }
        ], dates, { w: 860, h: 220 })}
        <div class="divbox ${(L.divergence || {}).diverging ? 'on' : ''}">
          <b>${(L.divergence || {}).diverging ? 'Divergence: ' + esc((L.divergence || {}).kind) : 'No divergence'}</b>
          <span class="muted"> ${esc((L.divergence || {}).interpretation || '')}</span>
        </div>
      </div>

      <div class="card-block">
        <div class="block-head"><h3>All Leading Indicators</h3>
          <span class="muted small">sorted by 3-month momentum, weakest first</span></div>
        ${table(['Indicator', 'State', 'Signal', '', 'Momentum', 'Percentile', 'As of'], rows)}
      </div>`;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // PAGE 5 — GROWTH / INFLATION / LABOR
  // ═══════════════════════════════════════════════════════════════════════
  function pageGrowth(d) {
    return variablePage(d, ['gdp', 'growth', 'inflation', 'core_inflation', 'unemployment', 'consumer', 'housing'],
      ['labor', 'manufacturing', 'consumer', 'housing', 'inflation'],
      'Growth · Inflation · Labour');
  }

  // ═══════════════════════════════════════════════════════════════════════
  // PAGE 6 — CREDIT / LIQUIDITY / RATES
  // ═══════════════════════════════════════════════════════════════════════
  function pageCredit(d) {
    return variablePage(d, ['credit', 'policy_rate', 'long_rates'],
      ['credit', 'curve', 'policy', 'financial'],
      'Credit · Liquidity · Rates');
  }

  function variablePage(d, varKeys, factorKeys, title) {
    const V = d.variables || {};
    const cards = varKeys.filter(k => (V[k] || []).length).map(k => {
      const fs = V[k];
      return `<div class="card-block">
        <div class="block-head"><h3>${esc(fs[0].label)}</h3></div>
        ${table(['Horizon', 'Direction', 'Estimate', 'Range (±1 RMSE)', 'Confidence', 'Drivers'],
          fs.map(f => [
            `<b>${f.horizon_m}M</b>`,
            `<span class="chip ${f.direction === 'UP' ? 'ok' : f.direction === 'DOWN' ? 'bad' : ''}">${esc(f.direction)}</span>`,
            f.point == null ? '—' : `<b>${n2(f.point)}${esc(f.unit)}</b>`,
            (f.lo == null || f.hi == null) ? '—' : `${n2(f.lo)} … ${n2(f.hi)}`,
            `${pct(f.confidence, 0)} ${bar(f.confidence, f.confidence > 0.5 ? tok('--green') : tok('--amber'), 5)}`,
            `<span class="muted small">${esc((f.drivers || []).join(', '))}</span>`
          ]))}
        ${noteBox(fs[0].note || '')}
      </div>`;
    }).join('');

    const facs = (d.factors || []).filter(f => factorKeys.includes(f.factor));
    const facBlocks = facs.map(f => `
      <div class="card-block">
        <div class="block-head"><h3>${esc(f.label)}
          <span style="color:${scoreColor(f.score)}">${sgn(f.score)}</span></h3>
          <span class="chip">${ARROW[f.arrow] || ''} ${sgn(f.momentum)}</span></div>
        ${f.interpretation ? `<p class="muted small">${esc(f.interpretation)}</p>` : ''}
        ${table(['Indicator', 'Latest', 'Percentile', 'Signal', ''],
          (f.contributions || []).slice().sort((a, b) => (a.signal ?? 0) - (b.signal ?? 0)).map(c => [
            esc(c.label),
            c.raw == null ? '—' : Number(c.raw).toLocaleString(undefined, { maximumFractionDigits: 2 }),
            c.percentile == null ? '—' : pct(c.percentile, 0),
            `<span style="color:${scoreColor(c.signal)}">${sgn(c.signal)}</span>`,
            divBar(c.signal)
          ]))}
      </div>`).join('');

    return `<h3 class="page-title">${esc(title)}</h3>
      ${cards || '<div class="emptybox">Variable forecasts are produced by the weekly run. Run <code>python main.py weekly</code> to populate them.</div>'}
      ${facBlocks}`;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // PAGE 7 — SCENARIOS
  // ═══════════════════════════════════════════════════════════════════════
  function pageScenarios(d) {
    const S = d.scenarios || {};
    const cards = (S.scenarios || []).map(s => {
      const col = s.scenario === 'CRISIS' ? tok('--red') : s.scenario === 'BEAR' ? tok('--amber')
        : s.scenario === 'BULL' ? tok('--green') : tok('--accent');
      const o = s.outlook || {};
      return `<div class="card-block scen-card">
        <div class="block-head"><h3 style="color:${col}">${esc(s.scenario)}</h3>
          <b style="font-size:20px;color:${col}">${pct(s.probability, 0)}</b></div>
        ${bar(s.probability, col, 8)}
        <p class="scen-head">${esc(s.headline)}</p>
        ${table(['', ''], Object.entries(o).map(([k, v]) =>
          [`<span class="k">${esc(k.replace(/_/g, ' '))}</span>`, esc(v)]), 'kv')}
        <div class="scen-lists">
          <div><h4>Assumptions</h4><ul>${(s.assumptions || []).map(a => `<li>${esc(a)}</li>`).join('')}</ul></div>
          <div><h4>Risks</h4><ul>${(s.risks || []).map(a => `<li>${esc(a)}</li>`).join('')}</ul></div>
        </div>
      </div>`;
    }).join('');
    return `<div class="scen-grid">${cards}</div>
      ${noteBox(S.consistency_note || '')}`;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // PAGE 8 — MARKET IMPLICATIONS
  // ═══════════════════════════════════════════════════════════════════════
  function pageMarket(d) {
    const M = d.market || {};
    const rg = M.regime || {};
    const stanceCol = s => s === 'POSITIVE' ? tok('--green') : s === 'CONSTRUCTIVE' ? tok('--s3')
      : s === 'NEUTRAL' ? tok('--muted') : s === 'CAUTIOUS' ? tok('--amber') : tok('--red');

    const assets = (M.assets || []).map(a => `
      <div class="asset-row">
        <span class="k">${esc(a.asset)}</span>
        ${divBar(a.score)}
        <b style="color:${stanceCol(a.stance)}">${esc(a.stance)}</b>
        <span class="muted small">${esc(a.rationale)}</span>
      </div>`).join('');

    const comps = Object.entries(rg.components || {}).map(([k, v]) => [
      `<b>${esc(k.replace(/_/g, ' '))}</b>`,
      `<span style="color:${scoreColor(v)}">${sgn(v)}</span>`,
      divBar(v),
      `<span class="muted small">${esc((rg.explanations || {})[k] || '')}</span>`
    ]);

    return `
      <div class="card-block">
        <div class="block-head"><h3>Market Regime</h3>
          <b style="font-size:20px;color:${scoreColor(rg.score)}">${esc(rg.label || '—')} ${sgn(rg.score)}</b></div>
        ${table(['Component', 'Score', '', 'Reading'], comps)}
        ${noteBox(rg.valuation_note || '')}
      </div>
      <div class="card-block">
        <div class="block-head"><h3>Asset-Class Implications</h3></div>
        <div class="assets">${assets}</div>
        ${noteBox(M.disclaimer || '', 'warn')}
      </div>
      <div class="card-block">
        <div class="block-head"><h3>Separation from the Macro Engine</h3></div>
        ${noteBox(M.separation_note || '')}
      </div>`;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // PAGE 9 — MODEL PERFORMANCE
  // ═══════════════════════════════════════════════════════════════════════
  function pageModel(d) {
    const P = d.model_performance || {};
    const cal = P.calibration || {};
    const conf = d.confidence || {};

    const models = (P.models || []).map(m => [
      `<b>${esc(m.model)}</b>${m.model === P.selected ? ' <span class="chip ok">selected</span>' : ''}`,
      m.auc == null ? '—' : m.auc.toFixed(4),
      m.brier == null ? '—' : m.brier.toFixed(5),
      String(m.folds || 0),
      `<span class="muted small">${esc(m.note || '')}</span>`
    ]);

    const curve = (cal.curve || []).filter(b => b.n > 0).map(b => [
      `${(b.bin_lo * 100).toFixed(0)}–${(b.bin_hi * 100).toFixed(0)}%`,
      String(b.n),
      pct(b.mean_predicted, 1),
      pct(b.observed_frequency, 1),
      bar(b.observed_frequency, Math.abs((b.observed_frequency || 0) - (b.mean_predicted || 0)) < 0.1
        ? tok('--green') : tok('--amber'), 8)
    ]);

    const confRows = Object.entries(conf.components || {}).map(([k, v]) => [
      `<b>${esc(k.replace(/_/g, ' '))}</b>`,
      pct(v, 0),
      bar(v, v > 0.66 ? tok('--green') : v > 0.4 ? tok('--amber') : tok('--red'), 8),
      `<span class="muted small">${esc((conf.explanations || {})[k] || '')}</span>`
    ]);

    const bt = d.backtest && d.backtest.metrics;
    const btRows = bt ? Object.entries(bt.by_horizon || {}).map(([h, m]) => [
      `<b>${esc(h)}M</b>`, String(m.n || 0), pct(m.base_rate, 1),
      m.auc == null ? '—' : `<b>${m.auc.toFixed(3)}</b>`,
      m.brier == null ? '—' : m.brier.toFixed(4),
      m.brier_skill == null ? '—'
        : `<span style="color:${m.brier_skill >= -0.02 ? tok('--green') : tok('--red')}">${m.brier_skill.toFixed(3)}</span>`
          + (m.brier_skill_vs_full_sample == null ? ''
             : `<br><span class="muted small">${m.brier_skill_vs_full_sample.toFixed(3)} vs full-sample</span>`),
      m.precision == null ? '—' : m.precision.toFixed(2),
      m.recall == null ? '—' : m.recall.toFixed(2),
      pct(m.vintage_true_share, 0)
    ]) : [];

    // Real-time (vintage) performance versus revised-data performance. The gap
    // is the whole reason the vintage backtest exists, so it is shown as a
    // number rather than left for the reader to notice by comparing two tables.
    const wfAuc = (P.models || []).find(m => m.model === P.selected);
    const btAuc = bt && bt.by_horizon && bt.by_horizon['12'] && bt.by_horizon['12'].auc;
    const gapNote = (wfAuc && wfAuc.auc != null && btAuc != null)
      ? `Walk-forward on REVISED data gives AUC ${wfAuc.auc.toFixed(3)} at 12 months; the `
        + `vintage-true backtest, using only what was knowable at the time, gives `
        + `${btAuc.toFixed(3)}. That gap of ${(wfAuc.auc - btAuc).toFixed(3)} is the cost `
        + `of real time — revisions and publication lags — and it is the honest number. `
        + `A system reporting only the first would be overstating what it could actually `
        + `have done.`
      : '';

    const lagged = (bt && bt.vintage_coverage && bt.vintage_coverage.lag_adjusted_model_features) || [];
    const tiers = (bt && bt.vintage_coverage && bt.vintage_coverage.model_feature_tiers) || {};
    const tierRows = Object.entries(tiers).map(([sid, tier]) => [
      esc(sid),
      `<span class="chip ${tier === 'alfred' ? 'ok' : tier === 'final' ? '' : 'warn'}">${esc(tier)}</span>`,
      tier === 'alfred' ? 'real ALFRED vintages — revisions reproduced'
        : tier === 'final' ? 'never revised — real-time value IS the final value'
        : 'lag-adjusted — publication timing respected, revisions NOT reproduced'
    ]);

    return `
      <div class="card-block">
        <div class="block-head"><h3>Model Comparison — walk-forward, 12-month horizon</h3>
          <span class="chip ok">selected: ${esc(P.selected || '—')}</span></div>
        ${table(['Model', 'AUC', 'Brier', 'Folds', 'Note'], models)}
        ${noteBox(P.selection_reason || '')}
        ${noteBox(P.note || '')}
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Calibration</h3>
          <span class="chip ${cal.fitted ? 'ok' : 'warn'}">${cal.fitted ? esc(cal.method) : 'not fitted'}</span></div>
        <div class="composite">
          <div><span class="k">Brier (raw → calibrated)</span><b>${n2(cal.brier_raw, 4)} → ${n2(cal.brier_calibrated, 4)}</b></div>
          <div><span class="k">Calibration error (ECE)</span><b>${n2(cal.ece_raw, 3)} → ${n2(cal.ece_calibrated, 3)}</b></div>
          <div><span class="k">Brier skill vs base rate</span><b>${n2(cal.brier_skill_calibrated, 3)}</b></div>
        </div>
        ${curve.length ? table(['Predicted band', 'n', 'Mean predicted', 'Observed frequency', ''], curve)
                       : '<p class="muted">No populated calibration bins.</p>'}
        ${noteBox(cal.note || '')}
        ${noteBox('A well-calibrated model has "mean predicted" close to "observed frequency" in every populated band. Empty bands are shown as absent rather than dropped — a band the model never predicts is itself information.')}
      </div>

      ${btRows.length ? `<div class="card-block">
        <div class="block-head"><h3>Vintage-True Historical Backtest</h3>
          <span class="muted small">${esc(d.backtest.start_date || '')} – ${esc(d.backtest.end_date || '')}
            · ${d.backtest.points} points · model ${esc(d.backtest.model || '')}</span></div>
        ${table(['Horizon', 'n', 'Base rate', 'AUC', 'Brier', 'Brier skill', 'Precision', 'Recall', 'Inputs vintage-true'], btRows)}
        ${gapNote ? noteBox(gapNote) : ''}
        ${bt.by_horizon && bt.by_horizon['12'] && bt.by_horizon['12'].skill_basis_note
          ? noteBox(bt.by_horizon['12'].skill_basis_note) : ''}
        ${bt.by_horizon && bt.by_horizon['12'] && bt.by_horizon['12'].ceiling_note
          ? noteBox(bt.by_horizon['12'].ceiling_note) : ''}
        ${bt.lead_time ? `<h4 class="sub-head">Lead time per recession</h4>
          ${table(['Recession start', 'First signal', 'Lead', 'Probability'],
            (bt.lead_time.episodes || []).map(e => [
              `<b>${esc(e.recession_start)}</b>`,
              e.first_signal ? esc(e.first_signal) : '<span class="chip bad">no signal</span>',
              e.lead_months == null ? '—' : `${e.lead_months} months`,
              e.probability_at_signal != null ? pct(e.probability_at_signal, 1)
                : e.peak_probability != null ? `peak ${pct(e.peak_probability, 1)}` : '—'
            ]))}
          ${noteBox('Median lead time ' + (bt.lead_time.median_months ?? '—') + ' months across '
            + bt.lead_time.detected + ' detected and ' + bt.lead_time.missed + ' missed. '
            + bt.lead_time.note)}` : ''}
        ${(bt.sanity || []).map(f => noteBox(f, f.startsWith('No sanity') ? 'muted' : 'warn')).join('')}
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Vintage Provenance of the Model's Inputs</h3>
          <span class="chip ${lagged.length ? 'warn' : 'ok'}">
            ${lagged.length ? lagged.length + ' lag-adjusted' : 'all vintage-true'}</span></div>
        ${tierRows.length ? table(['Feature', 'Tier', 'What that means'], tierRows) : ''}
        ${noteBox((bt.vintage_coverage && bt.vintage_coverage.note) || '',
                  lagged.length ? 'warn' : 'muted')}
      </div>` : `<div class="card-block"><div class="block-head"><h3>Historical Backtest</h3></div>
        <div class="emptybox">Run <code>python main.py monthly</code> (or <code>python main.py backtest</code>) to produce vintage-true backtest metrics.</div></div>`}

      <div class="card-block">
        <div class="block-head"><h3>Forecast Confidence</h3>
          <b style="color:${conf.band === 'HIGH' ? tok('--green') : conf.band === 'MEDIUM' ? tok('--amber') : tok('--red')}">
            ${pct(conf.score, 0)} ${esc(conf.band || '')}</b></div>
        ${table(['Component', 'Score', '', 'Reading'], confRows)}
        ${noteBox(conf.note || '')}
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Features</h3></div>
        <p class="muted small">${(P.features || []).map(f => `<span class="chip">${esc(f)}</span>`).join(' ')}</p>
        ${noteBox('The feature set is capped at ~14 on purpose: there are about eight usable recessions since 1970, so a 50-feature model would fit noise and backtest well for the wrong reason.')}
      </div>`;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // PAGE 10 — DATA QUALITY / SYSTEM HEALTH
  // ═══════════════════════════════════════════════════════════════════════
  function pageHealth(d) {
    const h = d.health || {};
    const vc = d.vintage_coverage || {};
    const gradeChip = g => `<span class="chip ${g === 'HIGH' ? 'ok' : g === 'MEDIUM' ? 'warn' : 'bad'}">${esc(g)}</span>`;

    const rows = (d.quality || []).slice()
      .sort((a, b) => a.score - b.score)
      .map(s => [
        `<b>${esc(s.label)}</b><br><span class="muted small">${esc(s.sid)}</span>`,
        `<span class="chip">${esc(s.category)}</span>`,
        `<span class="chip">${esc(s.frequency)}</span>`,
        gradeChip(s.grade),
        n2(s.score, 2),
        esc(s.last_observation || '—'),
        s.observation_age == null ? '—' : s.observation_age + 'd',
        `<span class="chip ${s.vintage_policy === 'alfred' ? 'ok' : s.vintage_policy === 'final' ? '' : 'warn'}">${esc(s.vintage_policy)}</span>`,
        `<span class="muted small">${esc((s.issues || []).join('; ') || 'none')}</span>`
      ]);

    const alerts = (d.alerts || []).map(a => [
      `<span class="chip ${a.severity === 'CRITICAL' ? 'bad' : a.severity === 'HIGH' ? 'bad' : a.severity === 'WARNING' ? 'warn' : ''}">${esc(a.severity)}</span>`,
      esc(a.as_of_date),
      `<b>${esc(a.title)}</b><br><span class="muted small">${esc(a.detail || '')}</span>`
    ]);

    const revs = (d.revisions || []).slice(0, 20).map(r => [
      esc(r.series_id), esc(r.observation_date),
      n2(r.old_value, 2), n2(r.new_value, 2),
      r.pct_change == null ? '—' : `<span style="color:${r.pct_change < 0 ? tok('--red') : tok('--green')}">${pct(r.pct_change, 2)}</span>`,
      esc((r.detected_at || '').slice(0, 10))
    ]);

    const runs = (d.runs || []).map(r => [
      String(r.run_id), esc(r.run_type),
      `<span class="chip ${r.status === 'COMPLETED' ? 'ok' : r.status === 'FAILED' ? 'bad' : 'warn'}">${esc(r.status)}</span>`,
      esc((r.run_timestamp || '').slice(0, 19)),
      esc(r.regime || '—'),
      r.prob_12m == null ? '—' : pct(r.prob_12m, 1),
      r.data_health == null ? '—' : pct(r.data_health, 0),
      `<code class="hash">${esc((r.input_hash || '').slice(0, 10))}</code>`,
      `<code class="hash">${esc((r.output_hash || '').slice(0, 10))}</code>`,
      r.duration_sec == null ? '—' : r.duration_sec + 's'
    ]);

    return `
      <div class="exec-mid">
        <div class="card-block">
          <h3>Data Health</h3>
          <div class="big" style="color:${h.health > 0.95 ? tok('--green') : h.health > 0.85 ? tok('--amber') : tok('--red')}">
            ${pct(h.health, 0)}</div>
          <p class="muted small">${h.high || 0} HIGH · ${h.medium || 0} MEDIUM · ${h.low || 0} LOW
            across ${h.n || 0} series</p>
        </div>
        <div class="card-block">
          <h3>Freshness</h3>
          <div class="big" style="color:${(h.stale || 0) === 0 ? tok('--green') : tok('--amber')}">${h.stale || 0}<span class="unit">stale</span></div>
          <p class="muted small">${(h.stale_series || []).join(', ') || 'Every series is inside its expected publication window.'}</p>
        </div>
        <div class="card-block">
          <h3>Vintage Store</h3>
          <div class="big">${Number(vc.total_vintage_rows || 0).toLocaleString()}<span class="unit">rows</span></div>
          <p class="muted small">${vc.tier_a_covered || 0} of ${vc.tier_a_series || 0} revision-heavy series have real ALFRED vintages stored for look-ahead-free backtesting.</p>
        </div>
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Alerts <span class="muted small">(last 60 days)</span></h3></div>
        ${alerts.length ? table(['Severity', 'Date', 'Alert'], alerts)
                        : '<div class="emptybox">No alerts raised. Thresholds are configurable in <code>config/strategy.py</code>.</div>'}
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Series Quality — all ${(d.quality || []).length} series</h3>
          <span class="muted small">worst first</span></div>
        ${table(['Series', 'Category', 'Freq', 'Grade', 'Score', 'Last obs', 'Age', 'Vintage tier', 'Issues'], rows)}
        ${noteBox('Vintage tier: "alfred" = real historical vintages pulled for look-ahead-free backtesting; "final" = never revised, so today\'s value IS the historical one; "lag" = revised but without vintage coverage, so backtests using it are flagged lag-adjusted rather than vintage-true.')}
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Recent Data Revisions</h3></div>
        ${revs.length ? table(['Series', 'Observation', 'Was', 'Now', 'Change', 'Detected'], revs)
                      : '<div class="emptybox">No material revisions detected in the last 45 days.</div>'}
      </div>

      <div class="card-block">
        <div class="block-head"><h3>Model Runs <span class="muted small">(reproducibility log)</span></h3></div>
        ${runs.length ? table(['Run', 'Type', 'Status', 'When', 'Regime', '12m prob', 'Health', 'Input hash', 'Output hash', 'Duration'], runs)
                      : '<div class="emptybox">No runs recorded yet.</div>'}
        ${noteBox('Two runs with the same input hash saw identical data, so any difference in their output hash is a code or configuration change, not a data change. That is what makes a stored forecast reproducible.')}
      </div>`;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // shell
  // ═══════════════════════════════════════════════════════════════════════
  const PAGES = [
    { id: 'exec', label: '📊 Executive', fn: pageExecutive },
    { id: 'recession', label: '⚠️ Recession', fn: pageRecession },
    { id: 'factors', label: '🧭 Factors', fn: pageFactors },
    { id: 'leading', label: '📡 Leading', fn: pageLeading },
    { id: 'growth', label: '📈 Growth·Inflation·Labour', fn: pageGrowth },
    { id: 'credit', label: '💳 Credit·Rates', fn: pageCredit },
    { id: 'scenarios', label: '🎲 Scenarios', fn: pageScenarios },
    { id: 'market', label: '💼 Market', fn: pageMarket },
    { id: 'model', label: '🔬 Model Performance', fn: pageModel },
    { id: 'health', label: '🩺 Data Health', fn: pageHealth }
  ];

  async function render(root, feedUrl, monitorFn) {
    let d;
    try {
      const r = await fetch(feedUrl, { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      d = await r.json();
    } catch (e) {
      root.innerHTML = `<div class="errbox">
        Couldn't load the macro engine feed (${esc(e.message)}). Expected at <code>${esc(feedUrl)}</code>.
        <br><br>Generate it with:<br><code>cd "macro dashboard" &amp;&amp; python main.py daily</code>
      </div>`;
      return;
    }

    const meta = d.meta || {};
    const h = d.health || {};
    const bar = `
      <div class="engine-bar">
        <div class="eb-left">
          <span class="eb-regime" style="color:${scoreColor(d.headline && d.headline.composite)}">
            ${esc((d.headline || {}).regime_display || '—')}</span>
          <span class="eb-sep">·</span>
          <span>12M recession <b style="color:${probColor((d.headline || {}).recession_12m)}">${esc((d.headline || {}).recession_12m_pct || '—')}</b></span>
          <span class="eb-sep">·</span>
          <span>confidence <b>${esc((d.headline || {}).confidence_pct || '—')}</b></span>
          <span class="eb-sep">·</span>
          <span>trend <b>${esc((d.headline || {}).trend || '—')}</b></span>
        </div>
        <div class="eb-right">
          <span class="chip ${h.health > 0.95 ? 'ok' : 'warn'}">data ${pct(h.health, 0)}</span>
          <span class="chip">${esc(meta.model_version || '')}</span>
          <span class="muted small">as of ${esc(meta.as_of || '')}</span>
        </div>
      </div>`;

    const nav = `<div class="subtabs engine-nav" role="tablist">
      ${PAGES.map((p, i) => `<button class="subtab ${i === 0 ? 'active' : ''}" data-page="${p.id}" role="tab">${p.label}</button>`).join('')}
      <button class="subtab" data-page="monitor" role="tab">🔎 Series Monitor</button>
    </div>`;

    root.innerHTML = `${bar}${nav}<div id="engine-page"></div>
      <p class="engine-foot muted small">${esc(meta.data_note || '')} ${esc(meta.disclaimer || '')}</p>`;

    const host = root.querySelector('#engine-page');
    const show = (id) => {
      root.querySelectorAll('.engine-nav .subtab').forEach(b =>
        b.classList.toggle('active', b.dataset.page === id));
      if (id === 'monitor') { monitorFn(host); return; }
      const page = PAGES.find(p => p.id === id) || PAGES[0];
      try { host.innerHTML = page.fn(d); }
      catch (e) { host.innerHTML = `<div class="errbox">Page failed to render: ${esc(e.message)}</div>`; console.error(e); }
      host.scrollIntoView({ block: 'nearest' });
    };
    root.querySelectorAll('.engine-nav .subtab').forEach(b =>
      b.addEventListener('click', () => show(b.dataset.page)));
    show('exec');
  }

  window.MACRO_ENGINE = { render };
})();
