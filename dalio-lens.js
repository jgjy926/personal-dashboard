/* Dalio Lens — one more sub-page of the Macro tab.
 *
 * Reads data/dalio_lens.json, written by tools/fetch_dalio.py: the growth x
 * inflation quadrant (now, and 6-month odds), the long- and short-term debt
 * cycles, policy room, productivity and the external picture, plus a
 * side-by-side with the macro engine's own read. Like macro-engine.js, nothing
 * here does analysis: every number and label is computed upstream so the page,
 * the feed and the tests cannot disagree. Exposes one global, window.DALIO_LENS,
 * which macro-engine.js calls when its "Dalio Lens" sub-tab is opened.
 */
'use strict';
(function () {
  const tok = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pct = (v, d = 0) => v == null ? '—' : (v * 100).toFixed(d) + '%';
  const num = (v, d = 2, unit = '') => v == null ? '—' : Number(v).toFixed(d) + unit;
  const sgn = (v, d = 2, unit = '') => v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(d) + unit;

  const Q = {
    goldilocks: { label: 'Goldilocks', sub: 'growth ↑ · inflation ↓', col: '--green' },
    reflation: { label: 'Inflationary boom', sub: 'growth ↑ · inflation ↑', col: '--s2' },
    stagflation: { label: 'Stagflation', sub: 'growth ↓ · inflation ↑', col: '--red' },
    deflation: { label: 'Deflationary slowdown', sub: 'growth ↓ · inflation ↓', col: '--accent' }
  };
  // Screen layout of the 2x2: x = growth momentum, y = inflation momentum.
  const CELL = { stagflation: [0, 0], reflation: [1, 0], deflation: [0, 1], goldilocks: [1, 1] };

  let cache = null;

  function table(headers, rows) {
    return `<div class="table-scroll"><table class="data-table">
      <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>
    </table></div>`;
  }
  const note = (t, kind = 'muted') => t ? `<p class="engine-note ${kind}">${esc(t)}</p>` : '';
  function bar(frac, color, h = 8) {
    const w = Math.max(0, Math.min(1, frac || 0)) * 100;
    return `<div class="mbar" style="height:${h}px"><i style="width:${w}%;background:${color}"></i></div>`;
  }
  function divBar(score, color) {
    if (score == null) return '<div class="mbar div"><i style="width:0"></i></div>';
    const w = Math.min(Math.abs(score), 1) * 50;
    return `<div class="mbar div"><span class="mid"></span>
      <i style="left:${score < 0 ? 50 - w : 50}%;width:${w}%;background:${color}"></i></div>`;
  }
  const pressureColor = p => p == null ? tok('--muted') : p >= 0.66 ? tok('--red') : p >= 0.4 ? tok('--amber') : tok('--green');
  const stanceColor = s => ({ 'FAVOURED': tok('--green'), 'TILT UP': tok('--s3'), 'NEUTRAL': tok('--muted'),
    'TILT DOWN': tok('--amber'), 'AVOID': tok('--red') }[s] || tok('--muted'));

  // ── the quadrant map: where the economy is, and the path it took ──────────
  function quadrantMap(quad) {
    const trail = quad.trail || [];
    const cur = quad.current || {};
    const S = 300, P = 26, mid = S / 2;
    // Each axis scaled to its own range: growth (σ) and inflation (pp) are in
    // different units, and a shared scale squashed whichever moved less.
    const spanG = Math.max(0.3, ...trail.map(p => Math.abs(p.g || 0))) * 1.15;
    const spanI = Math.max(0.3, ...trail.map(p => Math.abs(p.i || 0))) * 1.15;
    const X = g => mid + (g / spanG) * (mid - P);
    const Y = i => mid - (i / spanI) * (mid - P);
    const cells = Object.entries(CELL).map(([k, [cx, cy]]) => {
      const x = cx ? mid : P, y = cy ? mid : P, w = mid - P;
      const on = k === cur.key;
      return `<rect x="${x}" y="${y}" width="${w}" height="${w}" fill="${tok(Q[k].col)}" opacity="${on ? 0.16 : 0.06}"/>
        <text x="${cx ? S - P - 6 : P + 6}" y="${cy ? S - P - 8 : P + 14}" text-anchor="${cx ? 'end' : 'start'}"
          font-size="11" font-weight="${on ? 700 : 600}" fill="${tok(Q[k].col)}">${esc(Q[k].label)}</text>`;
    }).join('');
    const pts = trail.map(p => [X(p.g), Y(p.i)]);
    const path = pts.length > 1 ? `<path d="${pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')}"
      fill="none" stroke="${tok('--muted')}" stroke-width="1.2" opacity=".7"/>` : '';
    const dots = pts.map((p, i) => i === pts.length - 1 ? '' :
      `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.4" fill="${tok('--muted')}" opacity="${(0.25 + 0.6 * i / pts.length).toFixed(2)}"/>`).join('');
    const last = pts[pts.length - 1];
    const first = trail[0];
    return `<svg viewBox="0 0 ${S} ${S}" class="dl-quad" role="img" aria-label="growth and inflation quadrant map">
      ${cells}
      <line x1="${P}" y1="${mid}" x2="${S - P}" y2="${mid}" stroke="${tok('--line')}" stroke-width="1.5"/>
      <line x1="${mid}" y1="${P}" x2="${mid}" y2="${S - P}" stroke="${tok('--line')}" stroke-width="1.5"/>
      <text x="${S - P}" y="${mid - 5}" text-anchor="end" font-size="9" fill="${tok('--muted')}">growth →</text>
      <text x="${mid + 5}" y="${P - 8}" font-size="9" fill="${tok('--muted')}">↑ inflation</text>
      ${path}${dots}
      ${last ? `<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="6.5" fill="${tok(Q[cur.key] ? Q[cur.key].col : '--ink')}" stroke="${tok('--card')}" stroke-width="2"/>` : ''}
      ${first ? `<text x="${X(first.g).toFixed(1)}" y="${(Y(first.i) - 6).toFixed(1)}" text-anchor="middle" font-size="9" fill="${tok('--muted')}">${esc(first.date)}</text>` : ''}
    </svg>`;
  }

  // ── forward odds in the same 2x2 layout, so the two read side by side ─────
  function oddsGrid(fwd) {
    const probs = fwd.probs || {};
    const top = Math.max(...Object.values(probs), 0);
    const cell = k => {
      const p = probs[k];
      return `<div class="dl-odds-cell ${p === top && top >= 0.35 ? 'top' : ''}" style="--c:${tok(Q[k].col)};--a:${(0.06 + (p || 0) * 0.5).toFixed(2)}">
        <span class="k">${esc(Q[k].label)}</span><b>${pct(p, 0)}</b><span class="muted small">${esc(Q[k].sub)}</span></div>`;
    };
    return `<div class="dl-odds">${cell('stagflation')}${cell('reflation')}${cell('deflation')}${cell('goldilocks')}</div>`;
  }

  function methodChip(side) {
    if (!side) return '';
    const sk = side.skill || {};
    const ok = side.method === 'model';
    return `<span class="chip ${ok ? 'ok' : 'warn'}" title="out-of-sample since ${esc(sk.test_from)}">
      ${ok ? 'model' : 'persistence'} · ${pct(ok ? sk.hit_rate : sk.persistence_hit_rate, 0)} hit</span>`;
  }

  // ── page ──────────────────────────────────────────────────────────────────
  function page(d, engine) {
    const q = d.quadrant || {}, cur = q.current || {}, fwd = q.forward || {};
    const debt = d.debt || {}, lt = debt.long_term || {}, st = debt.short_term || {}, m = debt.metrics || {};
    const pol = d.policy || {}, prod = d.productivity || {}, ext = d.external || {};
    const o = d.outlook || {}, cmp = d.engine_compare || {};
    const meta = d.meta || {};

    const hero = `<div class="card-block dl-hero">
      <div class="block-head"><h3>Dalio Lens <span class="muted small">— growth, inflation &amp; the debt cycle</span></h3>
        <span class="muted small">data to ${esc(meta.as_of || '—')} · built ${esc((meta.generated_at || '').slice(0, 10))}</span></div>
      <p class="dl-headline">${esc(o.headline || '')}</p>
      <p class="muted small">${esc(o.narrative || '')}</p>
      ${(meta.missing_series || []).length ? note('Missing this run: ' + meta.missing_series.join(', '), 'warn') : ''}
    </div>`;

    const quadCard = q.available ? `<div class="card-block">
      <div class="block-head"><h3>Where we are</h3>
        ${cur.borderline ? '<span class="chip warn">borderline</span>' : `<span class="chip">${esc(cur.months_in_quadrant)} mo</span>`}</div>
      <div class="dl-qname" style="color:${tok(Q[cur.key] ? Q[cur.key].col : '--ink')}">${esc(cur.label)}</div>
      <p class="muted small">Growth <b>${esc(cur.growth_word)}</b> (${sgn(cur.growth_momentum)}σ over 6m) ·
        inflation <b>${esc(cur.inflation_word)}</b> (${sgn(cur.inflation_momentum)}pp)</p>
      ${quadrantMap(q)}
      <p class="muted small">Core PCE ${num(cur.core_pce_yoy, 1, '%')} (${esc(cur.core_pce_as_of)}) · CPI ${num(cur.cpi_yoy, 1, '%')} (${esc(cur.cpi_as_of)}).
        Trail: the last ${(q.trail || []).length} months.</p>
    </div>` : '<div class="card-block"><div class="emptybox">Quadrant unavailable this run.</div></div>';

    const fwdCard = q.available ? `<div class="card-block">
      <div class="block-head"><h3>Next ${esc(fwd.horizon_m)} months</h3>
        <span class="chip ${o.clear_signal ? 'ok' : 'warn'}">${o.clear_signal ? 'clear lead' : 'no clear lead'}</span></div>
      ${oddsGrid(fwd)}
      <div class="dl-dirs">
        <div><span class="k">Growth rising</span><b>${pct(fwd.p_growth_up, 0)}</b> ${methodChip(fwd.growth)}</div>
        <div><span class="k">Inflation rising</span><b>${pct(fwd.p_inflation_up, 0)}</b> ${methodChip(fwd.inflation)}</div>
      </div>
      <p class="muted small">Odds near 25% each mean the leading indicators cancel out. That is a real reading, not a gap in the data.</p>
    </div>` : '';

    const debtCard = debt.available ? `<div class="card-block">
      <div class="block-head"><h3>Debt cycle</h3></div>
      <span class="k muted small">Long-term</span>
      <div class="dl-stage">${esc(lt.stage)}</div>
      <div class="dl-press"><span class="muted small">Pressure</span>${bar(lt.pressure, pressureColor(lt.pressure), 10)}
        <b style="color:${pressureColor(lt.pressure)}">${num(lt.pressure, 2)}</b></div>
      <p class="muted small">${esc(lt.detail)}</p>
      <span class="k muted small">Short-term (credit)</span>
      <div class="dl-stage sm">${esc(st.stage)}</div>
      <span class="k muted small">Policy lever</span>
      <div class="dl-stage sm">${esc(pol.lever || '—')} <span class="muted small">· ${num(pol.room_ratio, 2)}× a typical recession's cuts</span></div>
    </div>` : '';

    const cmpBlock = cmp.available ? `<div class="card-block">
      <div class="block-head"><h3>Dalio Lens vs Macro Engine</h3>
        <span class="muted small">engine as of ${esc(cmp.engine_as_of || '—')} ·
          ${cmp.agree_count} agree · ${cmp.disagree_count} differ</span></div>
      ${table(['Topic', 'Macro Engine', 'Dalio Lens', ''], (cmp.rows || []).map(r => [
        `<b>${esc(r.topic)}</b>`, esc(r.engine), esc(r.dalio),
        r.agree === true ? '<span class="chip ok">agree</span>'
          : r.agree === false ? '<span class="chip bad">differ</span>' : '<span class="chip">different question</span>'
      ]))}
      ${note(cmp.note)}
    </div>` : '';

    const assets = (o.assets || []).map(a => `<div class="asset-row">
        <span class="k">${esc(a.asset)}</span>${divBar(a.score, stanceColor(a.stance))}
        <b style="color:${stanceColor(a.stance)}">${esc(a.stance)}</b>
        <span class="muted small">${esc(a.why)}</span></div>`).join('');
    const tiltBlock = `<div class="card-block">
        <div class="block-head"><h3>All-Weather tilt</h3><span class="muted small">quadrant odds × asset map + debt overlay</span></div>
        <div class="assets">${assets || '<p class="muted">Needs the forward quadrant.</p>'}</div>
        ${note(o.disclaimer, 'warn')}
      </div>
      <div class="exec-mid dl-two">
        <div class="card-block"><h3>Risks through this lens</h3>
          <ul class="invalidation">${(o.risks || []).map(r => `<li class="dn">⚠ ${esc(r)}</li>`).join('') || '<li class="muted">No rule-based risk flags.</li>'}</ul></div>
        <div class="card-block"><h3>What to watch</h3>
          <ul class="invalidation">${(o.watch || []).map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>
      </div>`;

    // growth / inflation momentum history + the leading indicators behind the odds
    const hist = q.history || [];
    const leadRows = comps => (comps || []).map(c => [
      esc(c.label), c.value == null ? '—' : esc(c.value), `<b>${sgn(c.z)}σ</b>`,
      divBar(Math.max(-1, Math.min(1, (c.z || 0) / 2)), (c.z || 0) >= 0 ? tok('--s3') : tok('--amber')),
      `<span class="chip ${c.pushing === 'up' ? 'ok' : c.pushing === 'down' ? 'bad' : ''}">${esc(c.pushing)}</span>`,
      `<span class="muted small">${esc(c.date)}</span>`]);
    const skillRow = (name, s) => {
      const sk = (s || {}).skill || {};
      return [`<b>${name}</b>`, esc(s && s.method), pct(sk.hit_rate, 1), pct(sk.persistence_hit_rate, 1),
        pct(sk.base_rate_up, 0), String(sk.n || 0), pct(s && s.p_model, 0)];
    };
    const quadDetail = q.available ? `<div class="card-block">
      <div class="block-head"><h3>Growth &amp; inflation momentum since 2000</h3>
        <span class="muted small">6-month change · growth in σ of the coincident composite, inflation in pp</span></div>
      ${window.lineChart ? window.lineChart([
        { name: 'Growth momentum (σ)', values: hist.map(p => p.g), color: tok('--s1') },
        { name: 'Inflation momentum (pp)', values: hist.map(p => p.i), color: tok('--s2') },
        { name: 'zero', values: hist.map(() => 0), color: tok('--muted'), dash: '4 3' }
      ], hist.map(p => p.date), { w: 860, h: 200 }) : ''}
      <div class="exec-mid dl-two" style="margin-top:12px">
        <div><h4 class="sub-head">Growth leads</h4>${table(['Indicator', 'Latest', 'z', '', 'Pushing', 'As of'], leadRows(fwd.growth_components))}</div>
        <div><h4 class="sub-head">Inflation leads</h4>${table(['Indicator', 'Latest', 'z', '', 'Pushing', 'As of'], leadRows(fwd.inflation_components))}</div>
      </div>
      <h4 class="sub-head">Out-of-sample record of the 6-month direction call</h4>
      ${table(['Direction', 'Used', 'Model hit', 'Persistence hit', 'Base rate up', 'Months', 'Model says now'],
        [skillRow('Growth', fwd.growth), skillRow('Inflation', fwd.inflation)])}
      ${note(fwd.note)}
    </div>` : '';

    const debtHist = debt.history || {}, rg = debt.rg_history || {}, ih = debt.interest_history || {};
    const debtDetail = debt.available ? `<div class="card-block">
      <div class="block-head"><h3>Long-term debt cycle</h3><span class="muted small">as of ${esc(debt.as_of)}</span></div>
      <div class="composite">
        <div><span class="k">Private + federal debt</span><b>${num(m.total_pct_gdp, 0, '%')}</b><span class="muted small"> of GDP</span></div>
        <div><span class="k">5-year change</span><b>${sgn(m.change_5y, 1, 'pp')}</b></div>
        <div><span class="k">Interest / federal receipts</span><b>${num(m.interest_to_receipts, 1, '%')}</b></div>
        <div><span class="k">Effective rate − nominal growth</span><b style="color:${(m.r_minus_g || 0) > 0 ? tok('--red') : tok('--green')}">${sgn(m.r_minus_g, 2, 'pp')}</b></div>
        <div><span class="k">Deficit (${esc(m.deficit_year)})</span><b style="color:${(m.deficit_pct_gdp || 0) > 3 ? tok('--amber') : tok('--green')}">${num(m.deficit_pct_gdp, 1, '%')}</b><span class="muted small"> vs 3% marker</span></div>
        <div><span class="k">Primary balance / to stabilise</span><b>${sgn(m.primary_balance_pct_gdp, 1)} / ${sgn(m.debt_stabilising_primary, 1)}</b><span class="muted small"> % GDP</span></div>
      </div>
      ${(lt.warnings || []).map(w => note(w, 'warn')).join('')}
      <h4 class="sub-head">Pressure components (0 benign · 1 stretched)</h4>
      ${table(['Component', 'Reading', 'Score', '', 'Rule'], (lt.components || []).map(c => [
        `<b>${esc(c.label)}</b>`, esc(c.display), `<b style="color:${pressureColor(c.score)}">${num(c.score, 2)}</b>`,
        bar(c.score, pressureColor(c.score), 8), `<span class="muted small">${esc(c.note)}</span>`]))}
      <p class="muted small">Stage rule that fired: ${(lt.rules_fired || []).map(esc).join('; ')}.</p>
      <h4 class="sub-head">Debt by sector, % of GDP</h4>
      ${window.lineChart ? window.lineChart([
        { name: 'Federal', values: debtHist.federal, color: tok('--red') },
        { name: 'Private (BIS)', values: debtHist.private, color: tok('--s1') },
        { name: 'Corporate', values: debtHist.corporate, color: tok('--s3') },
        { name: 'Household', values: debtHist.household, color: tok('--s2') }
      ], (debtHist.dates || []).map(x => x.slice(0, 4)), { w: 860, h: 200 }) : ''}
      <div class="exec-mid dl-two">
        <div><h4 class="sub-head">r vs g — rate on federal debt vs nominal GDP growth (%)</h4>
          ${window.lineChart ? window.lineChart([
            { name: 'Nominal GDP growth', values: rg.nominal_growth, color: tok('--green') },
            { name: 'Effective rate on federal debt', values: rg.effective_rate, color: tok('--red') },
            { name: '10Y yield', values: rg.ten_year, color: tok('--muted'), dash: '4 3' }
          ], (rg.dates || []).map(x => x.slice(0, 4)), { w: 520, h: 190 }) : ''}</div>
        <div><h4 class="sub-head">Federal interest as % of receipts</h4>
          ${window.lineChart ? window.lineChart([
            { name: 'Interest / receipts', values: ih.interest_to_receipts, color: tok('--amber') }
          ], (ih.dates || []).map(x => x.slice(0, 4)), { w: 520, h: 190 }) : ''}</div>
      </div>
      ${note('Debt here is private non-financial credit (BIS) plus gross federal debt; state and local debt is left out. The effective rate is federal interest paid divided by gross federal debt, an approximation of the average coupon.')}
    </div>` : '';

    const policyCard = pol.available ? `<div class="card-block">
      <h3>Policy room (MP1 · MP2 · MP3)</h3>
      <div class="composite">
        <div><span class="k">Fed funds</span><b>${num(pol.fed_funds, 2, '%')}</b></div>
        <div><span class="k">Real policy rate</span><b>${sgn(pol.real_rate, 2, '%')}</b></div>
        <div><span class="k">Median recession cut</span><b>${num(pol.median_cut, 1, 'pp')}</b></div>
      </div>
      <p class="small"><b>${esc(pol.lever)}</b> — ${esc(pol.lever_detail)}</p>
      ${table(['Recession', 'Peak', 'Trough', 'Cut'], (pol.cycles || []).map(c =>
        [esc(c.recession_start), num(c.peak), num(c.trough), `<b>${num(c.cut, 2, 'pp')}</b>`]))}
      <p class="muted small">Fed balance sheet ${num(pol.fed_balance_sheet_pct_gdp, 1, '%')} of GDP · 10Y ${num(pol.ten_year, 2, '%')}.</p>
    </div>` : '';

    const prodHist = prod.history || {};
    const prodCard = prod.available ? `<div class="card-block">
      <h3>Productivity</h3>
      <div class="composite">
        <div><span class="k">YoY</span><b>${num(prod.yoy, 1, '%')}</b></div>
        <div><span class="k">5y trend</span><b>${num(prod.trend_5y, 1, '%')}</b></div>
        <div><span class="k">40y trend</span><b>${num(prod.trend_40y, 1, '%')}</b></div>
      </div>
      <p class="small">${esc(prod.read)}</p>
      ${window.lineChart ? window.lineChart([
        { name: 'Output per hour, YoY %', values: prodHist.yoy, color: tok('--s3') }
      ], (prodHist.dates || []).map(x => x.slice(0, 4)), { w: 420, h: 150 }) : ''}
      <p class="muted small">Unit labour costs ${sgn(prod.unit_labor_cost_yoy, 1, '%')} YoY · real GDP 5y trend ${num(prod.real_gdp_trend_5y, 1, '%')}. As of ${esc(prod.as_of)}.</p>
    </div>` : '';

    const extCard = ext.available ? `<div class="card-block">
      <h3>Money &amp; external</h3>
      <div class="composite">
        <div><span class="k">Broad dollar, 12m</span><b>${sgn(ext.usd_12m, 1, '%')}</b></div>
        <div><span class="k">Gold, 12m</span><b>${sgn(ext.gold_12m, 1, '%')}</b></div>
        <div><span class="k">Foreign share of federal debt</span><b>${num(ext.foreign_share, 1, '%')}</b>
          <span class="muted small">${sgn(ext.foreign_share_5y_change, 1, 'pp')} in 5y</span></div>
      </div>
      <p class="small">${esc(ext.read)}</p>
      <p class="muted small">Dalio watches gold and foreign demand for a reserve currency's debt as the first signs that holders want to own less of it.</p>
    </div>` : '';

    const method = `<details class="fdetail"><summary><b>How this page is built</b></summary>
      <ul class="notes">
        <li><b>Quadrant.</b> Growth is the average z-score of year-on-year industrial production, payrolls and real income excluding transfers. Inflation is the average of core PCE and CPI year-on-year. "Rising" or "falling" is the 3-month average against the 3-month average six months earlier.</li>
        <li><b>Forward odds.</b> Two logistic regressions (growth up? inflation up?) on a leading-indicator score and current momentum, walk-forward since 2000. Where persistence did better out of sample, persistence is used. Quadrant odds multiply the two directions.</li>
        <li><b>Debt stage.</b> Written-down rules, first match wins: monetising (fed funds &lt; 0.5%), deleveraging (debt/GDP down ≥10pp in 5 years, labelled "via nominal growth" when r &lt; g), late (top-fifth debt with rising interest burden or r &gt; g), leveraging up (credit outgrowing nominal GDP), otherwise early or sustainable.</li>
        <li><b>Policy room.</b> Fed funds against the median peak-to-trough cut of the recessions since 1969. MP1 means cuts suffice, MP2 means balance-sheet tools, MP3 means coordinated fiscal and monetary policy.</li>
        <li><b>Caveats.</b> Revised data, not real-time vintages. This is a reading of Dalio's framework, not his own call. Not investment advice.</li>
      </ul>
      ${note(meta.note)}
    </details>`;

    return `<div class="dl-page">${hero}
      <div class="exec-mid">${quadCard}${fwdCard}${debtCard}</div>
      ${cmpBlock}
      ${tiltBlock}
      ${quadDetail}
      ${debtDetail}
      <div class="exec-mid">${policyCard}${prodCard}${extCard}</div>
      <div class="card-block">${method}</div>
      <p class="engine-foot muted small">${esc(meta.source || '')}</p></div>`;
  }

  async function render(host, feedUrl, engine) {
    host.innerHTML = '<p class="muted loading">Loading the Dalio lens…</p>';
    try {
      if (!cache) {
        const r = await fetch(feedUrl, { cache: 'no-store' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        cache = await r.json();
      }
    } catch (e) {
      host.innerHTML = `<div class="errbox">Couldn't load the Dalio lens feed (${esc(e.message)}).
        Expected at <code>${esc(feedUrl)}</code>. Generate it with <code>python tools/fetch_dalio.py</code>.</div>`;
      return;
    }
    try { host.innerHTML = page(cache, engine); }
    catch (e) { host.innerHTML = `<div class="errbox">Dalio lens failed to render: ${esc(e.message)}</div>`; console.error(e); }
  }

  window.DALIO_LENS = { render };
})();
