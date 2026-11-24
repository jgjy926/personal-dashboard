#!/usr/bin/env python3
"""
fetch_dalio.py — the Macro tab's "Dalio Lens" -> data/dalio_lens.json

The macro engine (data/macro_engine.json) is a business-cycle recession model.
Ray Dalio's framework reads the same economy along axes the engine does not
model at all, so this adds them as a separate, transparent layer:

  1. GROWTH x INFLATION QUADRANT. Which of the four environments the economy is
     in (each favours different assets: the basis of All Weather), and a 6-month
     forward probability for each, from leading indicators. The forward call is
     walk-forward tested against simple persistence, and both hit rates are
     published next to it.
  2. LONG-TERM DEBT CYCLE. Debt relative to income (private + federal), the
     interest burden on government revenue, effective rate vs nominal growth
     (r - g), the deficit against Dalio's 3%-of-GDP marker, and foreign demand
     for US debt, rolled into a stage label and a 0-1 pressure score.
  3. SHORT-TERM DEBT CYCLE. Credit growth and the direction of policy.
  4. POLICY ROOM. Dalio's MP1/MP2/MP3: whether rate cuts alone can still do the
     job, measured against what past recessions actually needed.
  5. PRODUCTIVITY. The long-run driver that debt should grow in line with.
  6. EXTERNAL / MONEY. Dollar, gold, and the foreign share of federal debt.

It ends with an All-Weather-style asset tilt and a side-by-side comparison with
the engine's own read.

These are RULES, written down and shown on the page. Only the quadrant forward
probabilities are fitted (a small logistic regression), and they come with
their out-of-sample record. All inputs are REVISED data, not real-time
vintages, so real-time accuracy will be lower than the published hit rates.
This is a lens for thinking with, not a forecast to trade on.

Keyless and stdlib-only, like fetch_macro.py, whose FRED/Yahoo fetch and CSV
parse helpers it reuses:
    python tools/fetch_dalio.py
    python tools/fetch_dalio.py --out data/dalio_lens.json

The network layer is fetch_all(); everything else is pure and unit-tested in
tools/test_fetch_dalio.py.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import fetch_macro as fm  # noqa: E402  (reuse the keyless FRED/Yahoo plumbing)

OUT = os.path.join(ROOT, "data", "dalio_lens.json")
ENGINE = os.path.join(ROOT, "data", "macro_engine.json")
VERSION = "dalio-lens-v1"

# Every FRED id this layer reads. `how` collapses higher-frequency series to a
# monthly value: "last" (month-end) or "mean" (weekly claims, whose single
# weeks are noisy). Quarterly and annual series pass through on their own dates.
SERIES = {
    # growth: the coincident pillars NBER itself leans on
    "INDPRO": {"label": "Industrial Production", "how": "last"},
    "PAYEMS": {"label": "Nonfarm Payrolls", "how": "last"},
    "W875RX1": {"label": "Real Personal Income ex Transfers", "how": "last"},
    # inflation
    "PCEPILFE": {"label": "Core PCE Price Index", "how": "last"},
    "CPIAUCSL": {"label": "CPI (headline)", "how": "last"},
    # growth leads
    "T10Y3M": {"label": "10Y - 3M Treasury Spread", "how": "mean"},
    "NEWORDER": {"label": "Core Capital Goods Orders", "how": "last"},
    "ICSA": {"label": "Initial Jobless Claims", "how": "mean"},
    "PERMIT": {"label": "Building Permits", "how": "last"},
    "BAA10Y": {"label": "Baa Corporate Spread", "how": "mean"},
    # inflation leads
    "DCOILWTICO": {"label": "WTI Crude Oil", "how": "mean"},
    "T10YIE": {"label": "10Y Breakeven Inflation", "how": "mean"},
    "M2SL": {"label": "M2 Money Supply", "how": "last"},
    # debt (quarterly, % of GDP unless noted)
    "QUSPAM770A": {"label": "Private Non-financial Credit, % GDP (BIS)", "how": "last"},
    "QUSHAM770A": {"label": "Household Credit, % GDP (BIS)", "how": "last"},
    "QUSNAM770A": {"label": "Corporate Credit, % GDP (BIS)", "how": "last"},
    "CRDQUSAPABIS": {"label": "Private Non-financial Credit, $bn (BIS)", "how": "last"},
    "GFDEGDQ188S": {"label": "Federal Debt, % GDP", "how": "last"},
    "GFDEBTN": {"label": "Federal Debt, $mn", "how": "last"},
    "A091RC1Q027SBEA": {"label": "Federal Interest Payments, $bn SAAR", "how": "last"},
    # TOTAL current receipts, payroll (social insurance) contributions included.
    # Tax receipts alone (W006RC1Q027SBEA) leave out ~30% of federal revenue and
    # overstate the interest burden by about a third.
    "W018RC1Q027SBEA": {"label": "Federal Current Receipts, $bn SAAR", "how": "last"},
    "FYFSGDA188S": {"label": "Federal Surplus/Deficit, % GDP (annual)", "how": "last"},
    "FYOIGDA188S": {"label": "Federal Interest Outlays, % GDP (annual)", "how": "last"},
    "FDHBFIN": {"label": "Federal Debt Held by Foreigners, $bn", "how": "last"},
    "TDSP": {"label": "Household Debt Service Ratio", "how": "last"},
    "TOTBKCR": {"label": "Bank Credit, all commercial banks", "how": "last"},
    "GDP": {"label": "Nominal GDP, $bn SAAR", "how": "last"},
    "GDPC1": {"label": "Real GDP", "how": "last"},
    # policy
    "FEDFUNDS": {"label": "Fed Funds Rate", "how": "last"},
    "DGS10": {"label": "10Y Treasury Yield", "how": "mean"},
    "WALCL": {"label": "Fed Balance Sheet, $mn", "how": "last"},
    "USREC": {"label": "NBER Recession Indicator", "how": "last"},
    # productivity
    "OPHNFB": {"label": "Output per Hour, Nonfarm Business", "how": "last"},
    "ULCNFB": {"label": "Unit Labor Costs, Nonfarm Business", "how": "last"},
    # external
    "DTWEXBGS": {"label": "Broad Dollar Index", "how": "mean"},
}

# Series the page cannot do without. If any of these is missing the run refuses
# to overwrite the previous feed (the same guard fetch_macro.py uses).
CRITICAL = ["INDPRO", "PAYEMS", "PCEPILFE", "CPIAUCSL", "FEDFUNDS", "GDP"]

QUADRANTS = {
    "goldilocks": {"label": "Goldilocks", "detail": "Rising growth · falling inflation",
                   "growth": "up", "inflation": "down"},
    "reflation": {"label": "Inflationary boom", "detail": "Rising growth · rising inflation",
                  "growth": "up", "inflation": "up"},
    "stagflation": {"label": "Stagflation", "detail": "Falling growth · rising inflation",
                    "growth": "down", "inflation": "up"},
    "deflation": {"label": "Deflationary slowdown", "detail": "Falling growth · falling inflation",
                  "growth": "down", "inflation": "down"},
}

# How each asset class tends to behave in each environment, from the All
# Weather logic: growth surprises favour equities, credit and commodities;
# inflation surprises favour inflation-linked bonds, commodities and gold;
# falling growth and inflation favour nominal bonds. Scaled -1..+1. This is a
# qualitative map, not a fitted one, and is printed on the page as such.
ASSET_MAP = {
    "US Equities":          {"goldilocks": 1.0, "reflation": 0.5, "stagflation": -1.0, "deflation": -0.8},
    "Nominal Treasuries":   {"goldilocks": 0.5, "reflation": -1.0, "stagflation": -0.5, "deflation": 1.0},
    "Inflation-linked (TIPS)": {"goldilocks": 0.0, "reflation": 0.6, "stagflation": 1.0, "deflation": -0.2},
    "Corporate Credit":     {"goldilocks": 0.8, "reflation": 0.4, "stagflation": -0.8, "deflation": -0.5},
    "Commodities":          {"goldilocks": -0.2, "reflation": 1.0, "stagflation": 0.6, "deflation": -1.0},
    "Gold":                 {"goldilocks": -0.5, "reflation": 0.4, "stagflation": 1.0, "deflation": 0.2},
    "Cash / T-bills":       {"goldilocks": -0.5, "reflation": -0.3, "stagflation": 0.4, "deflation": 0.3},
}
# How the long-term debt pressure leans on each asset: when debts are large
# relative to income and the interest burden rises, Dalio expects them to be
# inflated away rather than repaid, which is bad for long nominal bonds and good
# for hard money. Applied as weight * (pressure - 0.5), so neutral pressure is a
# no-op.
DEBT_OVERLAY = {"Gold": 0.6, "Nominal Treasuries": -0.6, "Inflation-linked (TIPS)": 0.3,
                "Commodities": 0.2}

CLEAR_SIGNAL = 0.35          # a quadrant needs this much forward probability to be "most likely"
DALIO_DEFICIT_MARKER = 3.0   # % of GDP, the deficit target in Dalio's 2025 "3% solution"
HORIZON = 6                  # months ahead for the forward quadrant


# ── date helpers ────────────────────────────────────────────────────────────
def ym_shift(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + (m - 1) + k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def ym_range(start: str, end: str) -> list[str]:
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = ym_shift(cur, 1)
    return out


def to_monthly(series: list[tuple[str, float]], how: str = "last") -> dict[str, float]:
    """[(YYYY-MM-DD, v)] ascending -> {YYYY-MM: v}. Quarterly/annual series keep
    their own (first-month) keys, so a quarterly map has one key per quarter."""
    if how == "mean":
        acc: dict[str, list[float]] = {}
        for d, v in series:
            acc.setdefault(d[:7], []).append(v)
        return {k: sum(v) / len(v) for k, v in acc.items()}
    return fm.monthly(series)


def fill_gaps(m: dict[str, float], max_gap: int = 2) -> dict[str, float]:
    """Linearly fill short holes in a MONTHLY series. A missing month breaks
    every year-on-year and 3-month average that touches it for a year: BLS never
    published October 2025 CPI (the government shutdown), which left headline
    CPI with no YoY for October 2026. Quarterly and annual series (keys more
    than a month apart as a rule) are returned untouched."""
    keys = sorted(m)
    if len(keys) < 3:
        return dict(m)
    steps = [(int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7]))
             for a, b in zip(keys, keys[1:])]
    if statistics.median(steps) != 1:
        return dict(m)
    out = dict(m)
    for a, b, s in zip(keys, keys[1:], steps):
        if 1 < s <= max_gap + 1:
            for j in range(1, s):
                out[ym_shift(a, j)] = m[a] + (m[b] - m[a]) * j / s
    return out


def latest_yoy(m: dict[str, float]) -> tuple[str | None, float | None]:
    """The newest month with a computable year-on-year change."""
    for ym in sorted(m, reverse=True):
        v = pct_change(m, ym, 12)
        if v is not None:
            return ym, v
    return None, None


def pct_change(m: dict[str, float], ym: str, k: int) -> float | None:
    a, b = m.get(ym), m.get(ym_shift(ym, -k))
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def diff(m: dict[str, float], ym: str, k: int) -> float | None:
    a, b = m.get(ym), m.get(ym_shift(ym, -k))
    return None if a is None or b is None else a - b


def latest(m: dict[str, float]) -> str | None:
    return max(m) if m else None


def latest_common(*maps: dict[str, float]) -> str | None:
    keys = set(maps[0]) if maps else set()
    for mp in maps[1:]:
        keys &= set(mp)
    return max(keys) if keys else None


def zscore_map(m: dict[str, float]) -> dict[str, float]:
    vals = list(m.values())
    if len(vals) < 24:
        return {}
    mu, sd = statistics.fmean(vals), statistics.pstdev(vals)
    if sd == 0:
        return {}
    return {k: (v - mu) / sd for k, v in m.items()}


def percentile_of(value: float, history: list[float]) -> float | None:
    if not history:
        return None
    return sum(1 for h in history if h <= value) / len(history)


def ordinal(frac: float) -> str:
    """0.91 -> '91st'."""
    n = round(frac * 100)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def r2(x, nd=2):
    return None if x is None else round(x, nd)


def avg3(m: dict[str, float], ym: str) -> float | None:
    vals = [m.get(ym_shift(ym, -i)) for i in range(3)]
    return None if any(v is None for v in vals) else sum(vals) / 3


# ── 1. growth x inflation quadrant ──────────────────────────────────────────
def growth_composite(M: dict[str, dict[str, float]]) -> dict[str, float]:
    """Mean z-score of the year-on-year growth of the three coincident pillars,
    defined only for months where all three exist (a partial average would
    compare different baskets month to month)."""
    parts = []
    for sid in ("INDPRO", "PAYEMS", "W875RX1"):
        m = M.get(sid, {})
        yoy = {ym: v for ym in m if (v := pct_change(m, ym, 12)) is not None}
        parts.append(zscore_map(yoy))
    if not all(parts):
        return {}
    common = set(parts[0]).intersection(*parts[1:])
    return {ym: sum(p[ym] for p in parts) / len(parts) for ym in sorted(common)}


def inflation_composite(M: dict[str, dict[str, float]]) -> dict[str, float]:
    """Mean of core PCE and headline CPI year-on-year, in percent."""
    parts = []
    for sid in ("PCEPILFE", "CPIAUCSL"):
        m = M.get(sid, {})
        parts.append({ym: v for ym in m if (v := pct_change(m, ym, 12)) is not None})
    if not all(parts):
        return {}
    common = set(parts[0]).intersection(*parts[1:])
    return {ym: sum(p[ym] for p in parts) / len(parts) for ym in sorted(common)}


def momentum(level: dict[str, float], k: int = 6) -> dict[str, float]:
    """3-month average now minus the 3-month average k months earlier: the
    'rising or falling' that Dalio's quadrants are defined by."""
    out = {}
    for ym in level:
        a, b = avg3(level, ym), avg3(level, ym_shift(ym, -k))
        if a is not None and b is not None:
            out[ym] = a - b
    return out


# Below these, "rising" vs "falling" is noise: the quadrant is still assigned
# by sign (it has to be one of four), but flagged as borderline on the page.
FLAT_GROWTH = 0.10      # z units
FLAT_INFLATION = 0.15   # percentage points


def direction_word(v: float, flat: float) -> str:
    return "flat" if abs(v) < flat else ("rising" if v > 0 else "falling")


def quadrant_of(g_mom: float, i_mom: float) -> str:
    if g_mom >= 0:
        return "reflation" if i_mom >= 0 else "goldilocks"
    return "stagflation" if i_mom >= 0 else "deflation"


GROWTH_LEADS = [
    # (key, label, sign, transform) — sign makes + mean "growth rising ahead"
    ("T10Y3M", "Yield curve (10Y-3M) level", +1, "level"),
    ("NEWORDER", "Core capital goods orders, YoY", +1, "yoy"),
    ("ICSA", "Initial jobless claims, YoY (inverted)", -1, "yoy"),
    ("PERMIT", "Building permits, YoY", +1, "yoy"),
    ("BAA10Y", "Baa credit spread, 6m change (inverted)", -1, "chg6"),
]
INFLATION_LEADS = [
    ("DCOILWTICO", "WTI oil, 6m % change", +1, "pct6"),
    ("T10YIE", "10Y breakeven, 6m change", +1, "chg6"),
    ("PCEPILFE", "Core PCE 3m annualised minus YoY", +1, "accel"),
    ("M2SL", "M2 money supply, YoY", +1, "yoy"),
]


def transform(m: dict[str, float], how: str) -> dict[str, float]:
    out = {}
    for ym in m:
        if how == "level":
            v = m[ym]
        elif how == "yoy":
            v = pct_change(m, ym, 12)
        elif how == "pct6":
            v = pct_change(m, ym, 6)
        elif how == "chg6":
            v = diff(m, ym, 6)
        elif how == "accel":
            three = pct_change(m, ym, 3)
            yoy = pct_change(m, ym, 12)
            v = None if three is None or yoy is None else ((1 + three / 100) ** 4 - 1) * 100 - yoy
        else:
            raise ValueError(how)
        if v is not None:
            out[ym] = v
    return out


def lead_score(M: dict[str, dict[str, float]], spec: list) -> tuple[dict[str, float], dict[str, dict]]:
    """Average signed z-score of the available leading components, per month,
    needing at least two. Returns (score_by_month, per-component detail)."""
    comps = {}
    for sid, label, sign, how in spec:
        raw = transform(M.get(sid, {}), how)
        z = zscore_map(raw)
        if z:
            comps[sid] = {"label": label, "sign": sign, "raw": raw, "z": z}
    months = sorted(set().union(*[set(c["z"]) for c in comps.values()])) if comps else []
    score = {}
    for ym in months:
        zs = [c["sign"] * c["z"][ym] for c in comps.values() if ym in c["z"]]
        if len(zs) >= 2:
            score[ym] = sum(zs) / len(zs)
    return score, comps


def fit_logistic(X: list[list[float]], y: list[int], ridge: float = 1e-3,
                 iters: int = 25) -> list[float]:
    """Plain Newton-Raphson logistic regression with an intercept and a small
    ridge. Small k (2-3 parameters), so the Hessian is solved directly."""
    k = len(X[0]) + 1
    w = [0.0] * k
    rows = [[1.0] + list(x) for x in X]
    for _ in range(iters):
        g = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        for r, t in zip(rows, y):
            z = sum(wi * ri for wi, ri in zip(w, r))
            p = 1 / (1 + math.exp(-max(-35, min(35, z))))
            s = p * (1 - p)
            for a in range(k):
                g[a] += (p - t) * r[a]
                for b in range(k):
                    H[a][b] += s * r[a] * r[b]
        for a in range(1, k):
            g[a] += ridge * w[a]
            H[a][a] += ridge
        step = _solve(H, g)
        if step is None:
            break
        w = [wi - si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    return w


def _solve(A: list[list[float]], b: list[float]) -> list[float] | None:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * bb for a, bb in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def predict(w: list[float], x: list[float]) -> float:
    z = w[0] + sum(wi * xi for wi, xi in zip(w[1:], x))
    return 1 / (1 + math.exp(-max(-35, min(35, z))))


def direction_model(level: dict[str, float], lead: dict[str, float],
                    horizon: int = HORIZON, test_from: str = "2000-01",
                    flat: float = 0.0) -> dict:
    """P(level is higher `horizon` months from now), from (lead score, current
    momentum). Walk-forward: refit each January on outcomes already known by
    then, predict that year's months out of sample. Benchmarked against
    persistence (the last 6 months' direction simply continues), which is the
    bar a forward call has to clear to be worth showing."""
    mom = momentum(level, horizon)
    feats = {ym: [lead[ym], mom[ym]] for ym in lead if ym in mom}
    target = {}
    for ym in feats:
        a, b = avg3(level, ym), avg3(level, ym_shift(ym, horizon))
        if a is not None and b is not None:
            target[ym] = 1 if b > a else 0
    labelled = sorted(target)
    hits = naive_hits = n = 0
    history = []
    w = None
    for ym in labelled:
        if ym < test_from:
            continue
        if w is None or ym.endswith("-01"):
            cutoff = ym_shift(ym, -horizon)          # outcomes known by `ym`
            train = [t for t in labelled if t <= cutoff]
            if len(train) < 120:
                continue
            w = fit_logistic([feats[t] for t in train], [target[t] for t in train])
        p = predict(w, feats[ym])
        n += 1
        hits += int((p >= 0.5) == bool(target[ym]))
        naive_hits += int((feats[ym][1] >= 0) == bool(target[ym]))
        history.append({"date": ym, "p": round(p, 3), "actual": target[ym]})
    w_all = fit_logistic([feats[t] for t in labelled], [target[t] for t in labelled]) if len(labelled) >= 120 else None
    now = max(feats) if feats else None
    p_model = predict(w_all, feats[now]) if (w_all and now) else None
    hit = hits / n if n else None
    naive = naive_hits / n if n else None
    # The same rule the macro engine applies to its own models: a fitted model
    # is published only if it beat the simple benchmark out of sample. When
    # persistence did better, its own hit rate becomes the probability.
    method, p_now = "model", p_model
    if hit is not None and naive is not None and naive > hit and now:
        method = "persistence"
        m_now = feats[now][1]
        p_now = 0.5 if abs(m_now) < flat else (naive if m_now > 0 else 1 - naive)
    return {
        "p_up": r2(p_now, 3), "as_of": now, "method": method,
        "p_model": r2(p_model, 3),
        "lead": r2(feats[now][0]) if now else None,
        "momentum": r2(feats[now][1], 3) if now else None,
        "skill": {
            "n": n, "hit_rate": r2(hit, 3), "persistence_hit_rate": r2(naive, 3),
            "base_rate_up": r2(sum(target[t] for t in labelled) / len(labelled), 3) if labelled else None,
            "test_from": test_from,
        },
        "coefficients": [r2(c, 3) for c in w_all] if w_all else None,
        "history": history[-120:],
    }


def component_rows(comps: dict[str, dict]) -> list[dict]:
    rows = []
    for sid, c in comps.items():
        ym = latest(c["z"])
        rows.append({"sid": sid, "label": c["label"], "date": ym,
                     "value": r2(c["raw"].get(ym)), "z": r2(c["sign"] * c["z"][ym]),
                     "pushing": "up" if c["sign"] * c["z"][ym] > 0.25
                     else "down" if c["sign"] * c["z"][ym] < -0.25 else "flat"})
    rows.sort(key=lambda r: -abs(r["z"] or 0))
    return rows


def build_quadrant(M: dict[str, dict[str, float]]) -> dict:
    g_level, i_level = growth_composite(M), inflation_composite(M)
    g_mom, i_mom = momentum(g_level), momentum(i_level)
    now = latest_common(g_mom, i_mom)
    if not now:
        return {"available": False}
    q = quadrant_of(g_mom[now], i_mom[now])
    g_lead, g_comps = lead_score(M, GROWTH_LEADS)
    i_lead, i_comps = lead_score(M, INFLATION_LEADS)
    gm = direction_model(g_level, g_lead, flat=FLAT_GROWTH)
    im = direction_model(i_level, i_lead, flat=FLAT_INFLATION)
    pg, pi = gm["p_up"], im["p_up"]
    probs = None
    if pg is not None and pi is not None:
        probs = {"goldilocks": pg * (1 - pi), "reflation": pg * pi,
                 "stagflation": (1 - pg) * pi, "deflation": (1 - pg) * (1 - pi)}
        probs = {k: round(v, 3) for k, v in probs.items()}
    trail_months = [ym for ym in sorted(g_mom) if ym in i_mom][-24:]
    hist_months = [ym for ym in sorted(g_mom) if ym in i_mom and ym >= "2000-01"]
    core_ym, core_yoy = latest_yoy(M.get("PCEPILFE", {}))
    cpi_ym, cpi_yoy = latest_yoy(M.get("CPIAUCSL", {}))
    g_word = direction_word(g_mom[now], FLAT_GROWTH)
    i_word = direction_word(i_mom[now], FLAT_INFLATION)
    return {
        "available": True,
        "current": {
            "key": q, **QUADRANTS[q], "as_of": now,
            "growth_momentum": r2(g_mom[now], 3), "inflation_momentum": r2(i_mom[now], 3),
            "growth_word": g_word, "inflation_word": i_word,
            "borderline": g_word == "flat" or i_word == "flat",
            "growth_level_z": r2(g_level.get(now), 2),
            "inflation_level": r2(i_level.get(now), 2),
            "core_pce_yoy": r2(core_yoy), "core_pce_as_of": core_ym,
            "cpi_yoy": r2(cpi_yoy), "cpi_as_of": cpi_ym,
            "months_in_quadrant": _run_length(g_mom, i_mom, q, now),
        },
        "trail": [{"date": ym, "g": r2(g_mom[ym], 3), "i": r2(i_mom[ym], 3),
                   "q": quadrant_of(g_mom[ym], i_mom[ym])} for ym in trail_months],
        "history": [{"date": ym, "g": r2(g_mom[ym], 3), "i": r2(i_mom[ym], 3)} for ym in hist_months],
        "forward": {
            "horizon_m": HORIZON, "p_growth_up": pg, "p_inflation_up": pi,
            "probs": probs,
            "most_likely": max(probs, key=probs.get) if probs else None,
            "growth": {k: v for k, v in gm.items()}, "inflation": {k: v for k, v in im.items()},
            "growth_components": component_rows(g_comps),
            "inflation_components": component_rows(i_comps),
            "note": ("Each direction is a logistic regression on a leading-indicator score and "
                     "the current 6-month momentum, refitted every January and tested only on "
                     "months it had not seen. Where that model did worse out of sample than "
                     "simple persistence (the last 6 months' direction continuing), persistence "
                     "is used instead. Quadrant probabilities multiply the two, which "
                     "assumes growth and inflation surprises are independent. Inputs are revised "
                     "data, so real-time accuracy will be lower than the hit rates shown."),
        },
    }


def _run_length(g_mom, i_mom, q, now) -> int:
    n, ym = 0, now
    while ym in g_mom and ym in i_mom and quadrant_of(g_mom[ym], i_mom[ym]) == q:
        n += 1
        ym = ym_shift(ym, -1)
    return n


# ── 2-4. debt cycles and policy room ────────────────────────────────────────
def quarterly_avg(m: dict[str, float]) -> dict[str, float]:
    """Monthly map -> quarter-start-keyed average (YYYY-01/04/07/10), matching
    how FRED dates quarterly series."""
    acc: dict[str, list[float]] = {}
    for ym, v in m.items():
        mo = int(ym[5:7])
        acc.setdefault(f"{ym[:4]}-{(mo - 1) // 3 * 3 + 1:02d}", []).append(v)
    return {k: sum(v) / len(v) for k, v in acc.items() if len(v) == 3}


def easing_cycles(ff: dict[str, float], rec: dict[str, float]) -> list[dict]:
    """How many points of cuts each recession since 1969 actually took: peak
    fed funds in the 18 months before the recession began, minus the trough in
    the 36 months after. This is the yardstick for 'room to ease'."""
    starts = [ym for ym in sorted(rec) if rec[ym] >= 1 and rec.get(ym_shift(ym, -1), 0) < 1
              and ym >= "1969-01"]
    out = []
    for s in starts:
        before = [ff[ym] for ym in ym_range(ym_shift(s, -18), s) if ym in ff]
        after = [ff[ym] for ym in ym_range(s, ym_shift(s, 36)) if ym in ff]
        if before and after:
            peak, trough = max(before), min(after)
            out.append({"recession_start": s, "peak": r2(peak), "trough": r2(trough),
                        "cut": r2(peak - trough)})
    return out


def build_policy(M: dict[str, dict[str, float]], core_pce_yoy: float | None) -> dict:
    ff, rec = M.get("FEDFUNDS", {}), M.get("USREC", {})
    now = latest(ff)
    if not now:
        return {"available": False}
    cycles = easing_cycles(ff, rec)
    median_cut = statistics.median([c["cut"] for c in cycles]) if cycles else None
    rate = ff[now]
    room = rate / median_cut if median_cut else None
    if rate < 1.0:
        lever, detail = "MP2/MP3", ("Rates are near the floor. Easing would have to come from "
                                    "balance-sheet expansion (MP2) or coordinated fiscal and "
                                    "monetary policy (MP3).")
    elif room is not None and room >= 1.0:
        lever, detail = "MP1", ("Rate cuts alone could deliver a typical recession's worth of "
                                "easing. Monetary policy is not yet constrained.")
    else:
        lever, detail = "MP1 → MP2", (f"Rates could fall {rate:.1f}pp, short of the "
                                      f"{median_cut:.1f}pp a typical recession has needed. A deep "
                                      "downturn would likely push the Fed back to balance-sheet "
                                      "tools.")
    gdp = M.get("GDP", {})
    walcl = M.get("WALCL", {})
    bs = None
    gq = latest(gdp)
    if gq and walcl:
        bs = walcl[latest(walcl)] / 1000 / gdp[gq] * 100
    dgs10 = M.get("DGS10", {})
    return {
        "available": True, "as_of": now, "fed_funds": r2(rate),
        "fed_funds_12m_change": r2(diff(ff, now, 12)),
        "real_rate": r2(rate - core_pce_yoy) if core_pce_yoy is not None else None,
        "ten_year": r2(dgs10.get(latest(dgs10))) if dgs10 else None,
        "fed_balance_sheet_pct_gdp": r2(bs, 1),
        "median_cut": r2(median_cut), "room_ratio": r2(room),
        "cycles": cycles, "lever": lever, "lever_detail": detail,
    }


def build_debt(M: dict[str, dict[str, float]], policy: dict) -> dict:
    priv, hh, corp = M.get("QUSPAM770A", {}), M.get("QUSHAM770A", {}), M.get("QUSNAM770A", {})
    fed = M.get("GFDEGDQ188S", {})
    total = {q: priv[q] + fed[q] for q in priv if q in fed}
    if not total:
        return {"available": False}
    tq = latest(total)
    tot_hist = [total[q] for q in sorted(total)]
    pctile = percentile_of(total[tq], tot_hist)
    chg5 = diff(total, tq, 60)
    chg3 = diff(total, tq, 36)

    gdp, credit = M.get("GDP", {}), M.get("CRDQUSAPABIS", {})
    gq = latest(gdp)
    g = pct_change(gdp, gq, 12) if gq else None
    cq = latest_common(credit, gdp)
    credit_yoy = pct_change(credit, cq, 12) if cq else None
    gdp_yoy_at_cq = pct_change(gdp, cq, 12) if cq else None

    intr, rcpt, debt_n = M.get("A091RC1Q027SBEA", {}), M.get("W018RC1Q027SBEA", {}), M.get("GFDEBTN", {})
    ir_q = latest_common(intr, rcpt)
    int_to_rcpt = intr[ir_q] / rcpt[ir_q] * 100 if ir_q else None
    ir_series = {q: intr[q] / rcpt[q] * 100 for q in intr if q in rcpt}
    ir_chg2y = diff(ir_series, ir_q, 24) if ir_q else None
    ir_pctile = percentile_of(int_to_rcpt, list(ir_series.values())) if ir_q else None
    eq = latest_common(intr, debt_n)
    eff = {q: intr[q] / (debt_n[q] / 1000) * 100 for q in intr if q in debt_n and debt_n[q]}
    eff_rate = eff.get(eq) if eq else None
    g_series = {q: v for q in gdp if (v := pct_change(gdp, q, 12)) is not None}
    rg_q = latest_common(eff, g_series)
    r_minus_g = eff[rg_q] - g_series[rg_q] if rg_q else None

    dq10 = quarterly_avg(M.get("DGS10", {}))
    defi, intan = M.get("FYFSGDA188S", {}), M.get("FYOIGDA188S", {})
    aq = latest_common(defi, intan)
    deficit = -defi[aq] if aq else None
    primary = defi[aq] + intan[aq] if aq else None     # overall = primary - interest
    stabilising = None
    if rg_q and fed.get(rg_q) is not None:
        rr, gg = eff[rg_q] / 100, g_series[rg_q] / 100
        stabilising = (rr - gg) / (1 + gg) * fed[rg_q]    # primary balance (% GDP) that holds debt/GDP flat

    fhold = M.get("FDHBFIN", {})
    fq = latest_common(fhold, debt_n)
    share = {q: fhold[q] / (debt_n[q] / 1000) * 100 for q in fhold if q in debt_n and debt_n[q]}
    foreign_share = share.get(fq) if fq else None
    foreign_chg5 = diff(share, fq, 60) if fq else None

    tdsp = M.get("TDSP", {})
    tdsp_now = tdsp.get(latest(tdsp)) if tdsp else None

    # pressure components, each 0 (benign) .. 1 (stretched), with the rule shown
    comps = []

    def comp(key, label, value, display, score, note):
        comps.append({"key": key, "label": label, "value": r2(value, 2), "display": display,
                      "score": None if score is None else round(clamp01(score), 3), "note": note})

    comp("level", "Debt / GDP vs history", pctile,
         f"{total[tq]:.0f}% of GDP · {ordinal(pctile)} pct" if pctile is not None else "—",
         pctile, "Private + federal debt against every quarter since 1966. Higher = more leveraged.")
    comp("trend", "Debt / GDP, 5-year change", chg5,
         f"{chg5:+.1f}pp" if chg5 is not None else "—",
         None if chg5 is None else 0.5 + chg5 / 40,
         "+20pp in five years scores 1; flat scores 0.5; deleveraging scores below.")
    comp("interest", "Federal interest / receipts vs history", int_to_rcpt,
         f"{int_to_rcpt:.1f}% · {ordinal(ir_pctile)} pct" if ir_pctile is not None else "—",
         ir_pctile,
         "The share of federal revenue that goes to interest before anything else is paid for, "
         "ranked against every quarter since 1947.")
    comp("r_minus_g", "Effective rate minus nominal growth", r_minus_g,
         f"{r_minus_g:+.2f}pp" if r_minus_g is not None else "—",
         None if r_minus_g is None else 0.5 + r_minus_g / 4,
         "When the rate paid on debt exceeds income growth, debt/income rises on its own. +2pp scores 1.")
    comp("deficit", f"Federal deficit vs Dalio's {DALIO_DEFICIT_MARKER:.0f}% marker", deficit,
         f"{deficit:.1f}% of GDP" if deficit is not None else "—",
         None if deficit is None else 0.3 + (deficit - DALIO_DEFICIT_MARKER) / 5,
         "3% of GDP scores 0.3; every 5pp above it adds 1.")
    comp("foreign", "Foreign share of federal debt, 5y change", foreign_chg5,
         f"{foreign_share:.1f}% ({foreign_chg5:+.1f}pp)" if foreign_share is not None and foreign_chg5 is not None else "—",
         None if foreign_chg5 is None else 0.5 - foreign_chg5 / 10,
         "Falling foreign demand means more debt must be absorbed at home, or by the central bank.")
    room = policy.get("room_ratio")
    comp("policy_room", "Room to cut vs a typical recession", room,
         f"{room:.2f}x" if room is not None else "—",
         None if room is None else 1 - room,
         "Fed funds divided by the median cut past recessions needed. 1x or more scores 0.")
    scored = [c["score"] for c in comps if c["score"] is not None]
    pressure = round(statistics.fmean(scored), 3) if scored else None

    # long-term stage: written-down rules, first match wins
    fired = []
    if policy.get("fed_funds") is not None and policy["fed_funds"] < 0.5:
        stage, detail = "Monetising", "Rates at the floor and the central bank is the marginal buyer of debt."
        fired.append("fed funds < 0.5%")
    elif chg5 is not None and chg5 <= -10 and r_minus_g is not None and r_minus_g < 0:
        # Debt/GDP falling because nominal income outruns the interest rate:
        # Dalio's "beautiful deleveraging" mechanism, here largely via inflation.
        stage, detail = ("Deleveraging via nominal growth",
                         "Debt is shrinking relative to income because nominal growth (much of it "
                         "inflation) is running above the interest rate on the debt, not because "
                         "debt is being repaid.")
        fired.append(f"debt/GDP 5y change {chg5:+.1f}pp (≤ -10pp)")
        fired.append(f"effective rate {abs(r_minus_g):.2f}pp below nominal growth")
    elif chg5 is not None and chg5 <= -10:
        stage, detail = "Deleveraging", "Debt is falling relative to income."
        fired.append(f"debt/GDP 5y change {chg5:+.1f}pp (≤ -10pp)")
    elif pctile is not None and pctile >= 0.8 and (
            (ir_chg2y is not None and ir_chg2y > 0) or (r_minus_g is not None and r_minus_g > 0)):
        stage, detail = ("Late — debt-service squeeze",
                         "Debt is near record levels relative to income and the cost of carrying it is rising.")
        fired.append(f"debt/GDP at {ordinal(pctile)} percentile (≥ 80th)")
        if ir_chg2y is not None and ir_chg2y > 0:
            fired.append(f"interest/receipts up {ir_chg2y:.1f}pp over 2 years")
        if r_minus_g is not None and r_minus_g > 0:
            fired.append(f"effective rate exceeds nominal growth by {r_minus_g:.2f}pp")
    elif credit_yoy is not None and gdp_yoy_at_cq is not None and credit_yoy > gdp_yoy_at_cq:
        stage, detail = "Leveraging up", "Credit is growing faster than nominal income."
        fired.append(f"private credit {credit_yoy:.1f}% YoY > nominal GDP {gdp_yoy_at_cq:.1f}%")
    else:
        stage, detail = ("Late — high but contained" if pctile is not None and pctile >= 0.8
                         else "Early / sustainable",
                         "Debt is not outgrowing income and carrying costs are not rising.")
        fired.append("no stress rule fired")

    # Stage says which way debt is moving; these say how exposed the starting
    # point is, which a falling ratio does not fix on its own.
    warnings = []
    if pctile is not None and pctile >= 0.8:
        warnings.append(f"Debt is still at the {ordinal(pctile)} percentile of its history "
                        f"({total[tq]:.0f}% of GDP).")
    if ir_pctile is not None and ir_pctile >= 0.8:
        warnings.append(f"Interest takes {int_to_rcpt:.1f}% of federal revenue, higher than in "
                        f"{ir_pctile * 100:.0f}% of quarters since 1947.")
    if deficit is not None and deficit > DALIO_DEFICIT_MARKER:
        warnings.append(f"The {aq[:4]} deficit was {deficit:.1f}% of GDP, against Dalio's "
                        f"{DALIO_DEFICIT_MARKER:.0f}% marker.")

    # short-term debt cycle
    ff = M.get("FEDFUNDS", {})
    fnow = latest(ff)
    ff12 = diff(ff, fnow, 12) if fnow else None
    bank = M.get("TOTBKCR", {})
    bnow = latest(bank)
    bank_yoy = pct_change(bank, bnow, 12) if bnow else None
    bank_yoy_6 = pct_change(bank, ym_shift(bnow, -6), 12) if bnow else None
    rec = M.get("USREC", {})
    in_rec = rec.get(latest(rec)) == 1 if rec else False
    real = policy.get("real_rate")
    if in_rec or (bank_yoy is not None and bank_yoy < 0):
        st_stage, st_detail = "Contraction", "Credit is shrinking or the economy is in recession."
    elif ff12 is not None and ff12 <= -0.25:
        st_stage, st_detail = "Early — easing", "Policy is easing, which is how a new credit expansion starts."
    elif ff12 is not None and ff12 >= 0.25:
        st_stage, st_detail = "Late — tightening", "Policy is tightening to restrain credit and inflation."
    elif real is not None and real > 1 and bank_yoy is not None and bank_yoy_6 is not None and bank_yoy < bank_yoy_6:
        st_stage, st_detail = "Late — restrictive pause", "Policy is on hold but restrictive, and credit growth is slowing."
    else:
        st_stage, st_detail = "Mid — steady", "Credit is growing and policy is steady."

    # chart histories (quarterly from 1980)
    qs = [q for q in sorted(total) if q >= "1980-01"]
    rg_qs = [q for q in sorted(eff) if q >= "1980-01" and q in g_series]
    ir_qs = [q for q in sorted(ir_series) if q >= "1970-01"]
    return {
        "available": True, "as_of": tq,
        "metrics": {
            "total_pct_gdp": r2(total[tq], 1), "private_pct_gdp": r2(priv.get(tq), 1),
            "household_pct_gdp": r2(hh.get(tq), 1), "corporate_pct_gdp": r2(corp.get(tq), 1),
            "federal_pct_gdp": r2(fed.get(tq), 1), "percentile": r2(pctile, 3),
            "change_3y": r2(chg3, 1), "change_5y": r2(chg5, 1),
            "private_credit_yoy": r2(credit_yoy), "nominal_gdp_yoy": r2(g),
            "interest_to_receipts": r2(int_to_rcpt, 1), "interest_to_receipts_2y_change": r2(ir_chg2y, 1),
            "interest_to_receipts_percentile": r2(ir_pctile, 3),
            "interest_pct_gdp": r2(intr[latest_common(intr, gdp)] / gdp[latest_common(intr, gdp)] * 100, 2)
            if latest_common(intr, gdp) else None,
            "effective_rate": r2(eff_rate), "ten_year_q": r2(dq10.get(latest(dq10))) if dq10 else None,
            "r_minus_g": r2(r_minus_g), "deficit_pct_gdp": r2(deficit, 2),
            "deficit_year": aq[:4] if aq else None,
            "primary_balance_pct_gdp": r2(primary, 2),
            "debt_stabilising_primary": r2(stabilising, 2),
            "foreign_share": r2(foreign_share, 1), "foreign_share_5y_change": r2(foreign_chg5, 1),
            "household_debt_service": r2(tdsp_now, 1),
        },
        "long_term": {"stage": stage, "detail": detail, "rules_fired": fired,
                      "warnings": warnings, "pressure": pressure, "components": comps},
        "short_term": {"stage": st_stage, "detail": st_detail,
                       "fed_funds_12m_change": r2(ff12), "bank_credit_yoy": r2(bank_yoy),
                       "bank_credit_yoy_6m_ago": r2(bank_yoy_6), "in_recession": in_rec},
        "history": {"dates": qs, "private": [r2(priv.get(q), 1) for q in qs],
                    "federal": [r2(fed.get(q), 1) for q in qs],
                    "household": [r2(hh.get(q), 1) for q in qs],
                    "corporate": [r2(corp.get(q), 1) for q in qs]},
        "rg_history": {"dates": rg_qs, "effective_rate": [r2(eff[q]) for q in rg_qs],
                       "nominal_growth": [r2(g_series[q]) for q in rg_qs],
                       "ten_year": [r2(dq10.get(q)) for q in rg_qs]},
        "interest_history": {"dates": ir_qs, "interest_to_receipts": [r2(ir_series[q], 1) for q in ir_qs]},
    }


# ── 5-6. productivity and external ─────────────────────────────────────────
def build_productivity(M: dict[str, dict[str, float]]) -> dict:
    op, ulc, rgdp = M.get("OPHNFB", {}), M.get("ULCNFB", {}), M.get("GDPC1", {})
    q = latest(op)
    if not q:
        return {"available": False}

    def ann(m, k_years):
        qq = latest(m)
        a, b = m.get(qq), m.get(ym_shift(qq, -12 * k_years))
        return None if a is None or b is None else ((a / b) ** (1 / k_years) - 1) * 100

    yoy = {k: v for k in op if (v := pct_change(op, k, 12)) is not None}
    hq = [k for k in sorted(yoy) if k >= "2000-01"]
    t5, t10 = ann(op, 5), ann(op, 10)
    long_run = ann(op, 40)
    return {
        "available": True, "as_of": q, "yoy": r2(yoy.get(q)), "trend_5y": r2(t5), "trend_10y": r2(t10),
        "trend_40y": r2(long_run),
        "unit_labor_cost_yoy": r2(pct_change(ulc, latest(ulc), 12)) if ulc else None,
        "real_gdp_trend_5y": r2(ann(rgdp, 5)) if rgdp else None,
        "read": ("Productivity is running above its long-run pace — real income can grow without "
                 "extra debt." if t5 is not None and long_run is not None and t5 > long_run + 0.2 else
                 "Productivity is running below its long-run pace — income growth is leaning more on "
                 "debt and inflation." if t5 is not None and long_run is not None and t5 < long_run - 0.2 else
                 "Productivity is running near its long-run pace."),
        "history": {"dates": hq, "yoy": [r2(yoy[k]) for k in hq]},
    }


def build_external(M: dict[str, dict[str, float]], gold: dict[str, float], debt: dict) -> dict:
    usd = M.get("DTWEXBGS", {})
    un = latest(usd)
    gn = latest(gold)
    m = debt.get("metrics", {}) if debt.get("available") else {}
    usd12 = pct_change(usd, un, 12) if un else None
    gold12 = pct_change(gold, gn, 12) if gn else None
    read = []
    if gold12 is not None and usd12 is not None:
        if gold12 > 10 and usd12 > -2:
            read.append("Gold is rising even without a weaker dollar — a sign of demand for money "
                        "outside the dollar system.")
        elif gold12 > 10:
            read.append("Gold is rising alongside a weaker dollar.")
    if m.get("foreign_share_5y_change") is not None and m["foreign_share_5y_change"] < -2:
        read.append("Foreigners hold a shrinking share of US federal debt.")
    return {"available": bool(un or gn), "usd_as_of": un, "usd_12m": r2(usd12), "gold_as_of": gn,
            "gold_12m": r2(gold12), "gold": r2(gold.get(gn), 0) if gn else None,
            "foreign_share": m.get("foreign_share"),
            "foreign_share_5y_change": m.get("foreign_share_5y_change"),
            "read": " ".join(read) or "No external stress signal from the dollar, gold or foreign holdings."}


# ── synthesis ───────────────────────────────────────────────────────────────
def stance(score: float) -> str:
    if score >= 0.35:
        return "FAVOURED"
    if score >= 0.1:
        return "TILT UP"
    if score > -0.1:
        return "NEUTRAL"
    if score > -0.35:
        return "TILT DOWN"
    return "AVOID"


def _spread_text(probs: dict[str, float]) -> str:
    lo, hi = f"{min(probs.values()):.0%}", f"{max(probs.values()):.0%}"
    return f"every quadrant sits near {lo}" if lo == hi else f"every quadrant sits between {lo} and {hi}"


def build_outlook(quad: dict, debt: dict, policy: dict, prod: dict, ext: dict) -> dict:
    fwd = (quad.get("forward") or {}) if quad.get("available") else {}
    probs = fwd.get("probs")
    pressure = debt.get("long_term", {}).get("pressure") if debt.get("available") else None
    assets = []
    if probs:
        for asset, row in ASSET_MAP.items():
            base = sum(probs[q] * row[q] for q in probs)
            overlay = DEBT_OVERLAY.get(asset, 0.0) * ((pressure or 0.5) - 0.5)
            sc = base + overlay
            top = max(probs, key=lambda q: probs[q] * abs(row[q]))
            why = f"{QUADRANTS[top]['label']} ({probs[top]:.0%}) {'helps' if row[top] > 0 else 'hurts'}"
            if abs(overlay) >= 0.02:
                why += f"; debt pressure {'adds' if overlay > 0 else 'subtracts'} {abs(overlay):.2f}"
            assets.append({"asset": asset, "score": round(sc, 3), "base": round(base, 3),
                           "overlay": round(overlay, 3), "stance": stance(sc), "why": why})
        assets.sort(key=lambda a: -a["score"])

    cur = quad.get("current", {}) if quad.get("available") else {}
    ml = fwd.get("most_likely")
    parts = []
    if cur:
        parts.append(f"The economy reads as {cur['label'].lower()} (growth {cur['growth_word']}, "
                     f"inflation {cur['inflation_word']}) for {cur['months_in_quadrant']} month(s)"
                     + (" — borderline, since one axis is barely moving." if cur.get("borderline") else "."))
    clear = bool(probs) and probs[ml] >= CLEAR_SIGNAL
    if ml and probs:
        parts.append(f"Over the next {fwd['horizon_m']} months the leading indicators point to "
                     f"{QUADRANTS[ml]['label'].lower()} as most likely ({probs[ml]:.0%})." if clear else
                     f"Over the next {fwd['horizon_m']} months the leading indicators give no clear "
                     f"lead: {_spread_text(probs)}.")
    if debt.get("available"):
        lt = debt["long_term"]
        parts.append(f"Long-term debt cycle: {lt['stage'].lower()}, pressure {lt['pressure']:.2f} of 1.")
        parts.append(f"Short-term credit cycle: {debt['short_term']['stage'].lower()}.")
    if policy.get("available"):
        parts.append(f"Policy lever: {policy['lever']} — {policy['lever_detail']}")

    risks = []
    if probs and probs.get("stagflation", 0) >= 0.30:
        risks.append(f"Stagflation at {probs['stagflation']:.0%}: the bind where weak growth calls for "
                     "easing but inflation stands in the way. Equities and nominal bonds can fall together.")
    if pressure is not None and pressure >= 0.55:
        risks.append("Debt pressure is high: Dalio's playbook expects such debts to be inflated away "
                     "over time rather than repaid, which erodes the real value of long bonds and cash.")
    m = debt.get("metrics", {}) if debt.get("available") else {}
    if m.get("r_minus_g") is not None and m["r_minus_g"] > 0:
        risks.append("Interest costs are compounding faster than the economy is growing, so debt/GDP "
                     "rises even without new borrowing.")
    if m.get("deficit_pct_gdp") is not None and m["deficit_pct_gdp"] > DALIO_DEFICIT_MARKER + 1:
        risks.append(f"The deficit ({m['deficit_pct_gdp']:.1f}% of GDP) is well above Dalio's "
                     f"{DALIO_DEFICIT_MARKER:.0f}% marker.")
    if policy.get("lever") and policy["lever"] != "MP1":
        risks.append("Rate cuts alone may not be enough in a real downturn.")
    if prod.get("available") and prod.get("trend_5y") is not None and prod.get("trend_40y") is not None \
            and prod["trend_5y"] < prod["trend_40y"] - 0.2:
        risks.append("Productivity is below trend, so income growth leans on credit and inflation.")

    watch = []
    for side in ("growth_components", "inflation_components"):
        for c in (fwd.get(side) or [])[:2]:
            watch.append(f"{c['label']} ({'+' if (c['z'] or 0) >= 0 else ''}{c['z']}σ, pushing "
                         f"{'growth' if side.startswith('growth') else 'inflation'} {c['pushing']})")

    nxt = (f"{QUADRANTS[ml]['label']} most likely in {fwd.get('horizon_m', HORIZON)}m" if clear
           else f"no clear lead for the next {fwd.get('horizon_m', HORIZON)}m")
    return {
        "headline": f"{cur.get('label', '—')} now{' (borderline)' if cur.get('borderline') else ''} · {nxt}",
        "clear_signal": clear,
        "narrative": " ".join(parts), "assets": assets, "risks": risks, "watch": watch,
        "disclaimer": ("Asset stances come from a fixed All-Weather-style map weighted by the quadrant "
                       "probabilities, plus a debt-pressure overlay. They are illustrative, not "
                       "investment advice and not a return forecast."),
    }


ENGINE_BUCKET = {"EXPANSION": "mid", "EARLY_RECOVERY": "early", "RECOVERY": "early",
                 "LATE_CYCLE": "late", "SLOWDOWN": "late", "PRE_RECESSION": "late",
                 "RECESSION": "contraction"}


def _dalio_bucket(stage: str) -> str:
    s = stage.lower()
    return ("contraction" if s.startswith("contraction") else "early" if s.startswith("early")
            else "late" if s.startswith("late") else "mid")


def compare_engine(engine: dict | None, quad: dict, debt: dict, policy: dict) -> dict:
    """Where the macro engine and the Dalio lens agree and where they part
    company. `agree` is True/False, or None where only one side has a view."""
    if not engine:
        return {"available": False}
    h = engine.get("headline", {}) or {}
    dims = (engine.get("regime", {}) or {}).get("dimensions", {}) or {}
    rows = []
    cur = quad.get("current", {}) if quad.get("available") else {}
    fwd = quad.get("forward", {}) if quad.get("available") else {}

    if debt.get("available") and h.get("regime"):
        eb, db = ENGINE_BUCKET.get(h["regime"]), _dalio_bucket(debt["short_term"]["stage"])
        rows.append({"topic": "Cycle position", "engine": h.get("regime_display", h["regime"]),
                     "dalio": f"Short-term credit cycle: {debt['short_term']['stage']}", "agree": eb == db})
    if cur and h.get("composite_momentum") is not None:
        cm = h["composite_momentum"]
        e_word = "flat" if abs(cm) < 0.03 else ("rising" if cm > 0 else "falling")
        d_word = cur["growth_word"]
        p = fwd.get("p_growth_up")
        rows.append({"topic": "Growth direction",
                     "engine": f"{dims.get('growth', '—').title()} · composite momentum {cm:+.2f}",
                     "dalio": f"{d_word.title()} now ({cur['growth_momentum']:+.2f}σ over 6m)"
                              + (f" · {p:.0%} odds of rising in 6m" if p is not None else ""),
                     # flat agrees with anything that is not a clear move the other way
                     "agree": e_word == d_word or "flat" in (e_word, d_word)})
    if cur and dims.get("inflation"):
        e = dims["inflation"].upper()
        e_word = ("flat" if e.startswith("STAB") else "rising" if e.startswith(("RIS", "REFL", "ACCEL", "HIGH"))
                  else "falling" if e.startswith(("FALL", "DIS", "DEFL")) else "flat")
        im = cur["inflation_momentum"]
        d_word = cur["inflation_word"]
        p = fwd.get("p_inflation_up")
        agree = e_word == d_word
        rows.append({"topic": "Inflation direction", "engine": dims["inflation"].title(),
                     "dalio": f"{d_word.title()} ({im:+.2f}pp over 6m)"
                              + (f" · {p:.0%} odds of rising in 6m" if p is not None else ""),
                     "agree": agree})
    if h.get("recession_12m") is not None and fwd.get("probs"):
        down = fwd["probs"]["stagflation"] + fwd["probs"]["deflation"]
        rows.append({"topic": "Downside risk",
                     "engine": f"{h['recession_12m']:.0%} recession odds (12m)",
                     "dalio": f"{down:.0%} odds growth is falling in 6m (stagflation + deflationary slowdown)",
                     "agree": None})
    pol = next((f for f in engine.get("factors", []) if f.get("factor") == "policy"), None)
    if pol and policy.get("available"):
        st = (pol.get("detail") or {}).get("stance", "—")
        rows.append({"topic": "Policy", "engine": f"{st.title()} (real rate vs an assumed neutral)",
                     "dalio": f"{policy['lever']}: {policy['room_ratio']:.2f}x a typical recession's cuts available",
                     "agree": None})
    if debt.get("available"):
        lt = debt["long_term"]
        rows.append({"topic": "Long-term debt cycle", "engine": "Not modelled",
                     "dalio": f"{lt['stage']} · pressure {lt['pressure']:.2f}", "agree": None})
    agreed = [r for r in rows if r["agree"] is True]
    split = [r for r in rows if r["agree"] is False]
    return {"available": True, "engine_as_of": (engine.get("meta") or {}).get("as_of"),
            "rows": rows, "agree_count": len(agreed), "disagree_count": len(split),
            "note": ("The engine answers 'how likely is a recession in the next year'. The Dalio lens "
                     "answers 'which growth-and-inflation environment is coming, and how much room is "
                     "left in the debt cycle'. Disagreement is information, not an error.")}


def build_payload(M: dict[str, dict[str, float]], gold: dict[str, float],
                  engine: dict | None, missing: list[str], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    quad = build_quadrant(M)
    core = (quad.get("current") or {}).get("core_pce_yoy") if quad.get("available") else None
    policy = build_policy(M, core)
    debt = build_debt(M, policy)
    prod = build_productivity(M)
    ext = build_external(M, gold, debt)
    outlook = build_outlook(quad, debt, policy, prod, ext)
    return {
        "meta": {
            "generated_at": now.isoformat(timespec="seconds"),
            "as_of": (quad.get("current") or {}).get("as_of") if quad.get("available") else None,
            "version": VERSION, "missing_series": missing,
            "source": "FRED keyless CSV (BEA, BLS, Fed, BIS, Treasury); gold via Yahoo Finance",
            "note": ("A rules-based reading of Ray Dalio's framework (the economic machine, the "
                     "growth/inflation quadrants, the short- and long-term debt cycles) applied to "
                     "public data. It is an interpretation, not his own call."),
        },
        "quadrant": quad, "debt": debt, "policy": policy, "productivity": prod,
        "external": ext, "outlook": outlook,
        "engine_compare": compare_engine(engine, quad, debt, policy),
    }


# ── network ────────────────────────────────────────────────────────────────
def fetch_one(sid: str) -> tuple[str, list[tuple[str, float]], str | None]:
    try:
        return sid, fm.parse_fred_csv(fm.fetch_fred_csv(sid)), None
    except Exception as e:  # noqa: BLE001 — one bad series must not sink the run
        return sid, [], f"{type(e).__name__}: {e}"


def fetch_gold() -> dict[str, float]:
    try:
        # Daily bars, collapsed here: Yahoo's "1mo" bars carry irregular dates
        # and skip months, which broke the 12-month change.
        bars, _ = fm.parse_yahoo_chart(fm.fetch_yahoo_chart("GC=F", rng="2y", interval="1d"))
        return fill_gaps(to_monthly(bars, "last"))
    except Exception as e:  # noqa: BLE001
        print(f"[dalio] gold unavailable: {e}", file=sys.stderr)
        return {}


def fetch_all() -> tuple[dict[str, dict[str, float]], dict[str, float], list[str]]:
    M: dict[str, dict[str, float]] = {}
    missing = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(fetch_one, sid) for sid in SERIES]
        gold_f = pool.submit(fetch_gold)
    for f in futs:
        sid, rows, err = f.result()
        if err or not rows:
            missing.append(sid)
            print(f"[dalio] {sid} failed: {err or 'empty'}", file=sys.stderr)
            continue
        M[sid] = fill_gaps(to_monthly(rows, SERIES[sid]["how"]))
    return M, gold_f.result(), missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--engine", default=ENGINE, help="macro engine feed to compare against")
    args = ap.parse_args()

    M, gold, missing = fetch_all()
    critical_missing = [s for s in CRITICAL if s in missing]
    if critical_missing or len(missing) > len(SERIES) // 2:
        print(f"[dalio] refusing to overwrite: missing {', '.join(missing)}", file=sys.stderr)
        return 1
    engine = None
    try:
        with open(args.engine, encoding="utf-8") as f:
            engine = json.load(f)
    except (OSError, ValueError):
        pass
    payload = build_payload(M, gold, engine, sorted(missing))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
    q = payload["quadrant"].get("current", {})
    lt = payload["debt"].get("long_term", {})
    # ASCII only: a Windows console (cp1252) cannot print an arrow or em dash.
    print(f"[dalio] wrote {args.out} - {len(SERIES) - len(missing)}/{len(SERIES)} series; "
          f"quadrant={q.get('key')} -> {payload['quadrant'].get('forward', {}).get('most_likely')}; "
          f"debt stage={lt.get('stage')} pressure={lt.get('pressure')}"
          + (f"; MISSING: {', '.join(missing)}" if missing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
