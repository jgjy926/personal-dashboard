#!/usr/bin/env python3
"""
seed_macro.py — generate a realistic SAMPLE data/macro.json for the Macro tab.

This is PLACEHOLDER data so the UI is fully reviewable before the real backend
(Cloudflare Worker + D1 + FRED + Stooq, per macro-dashboard-build-plan.md) exists.
The Phase-B backend must emit this same JSON shape, so the frontend never changes.

Deterministic (seeded) so re-running gives the same file. Values are plausible for
late-2026 but are NOT real observations — the UI labels them "sample".
"""
from __future__ import annotations

import json
import os
import random
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "macro.json")

random.seed(20260903)

# 60 monthly points, 2021-09 .. 2026-09
def months(n=60, end=(2026, 9)):
    y, m = end
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))

DATES = months()

def walk(start, drift, vol, lo=None, hi=None):
    v = start
    out = []
    for _ in DATES:
        v += drift + random.gauss(0, vol)
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        out.append(round(v, 2))
    return out

# Series over the window (monthly). Rough shapes: yields up then plateau, gold up,
# S&P up with a drawdown, unemployment low then drifting up.
real_yield = walk(0.6, 0.02, 0.06, lo=-0.5, hi=2.4)      # DFII10-style, %
gold = walk(1800, 12, 45, lo=1600)                        # XAU/USD
sp500 = walk(4400, 22, 130, lo=3400)                      # ^SPX
unemployment = walk(3.6, 0.012, 0.08, lo=3.4, hi=5.0)     # UNRATE, %
nominal10 = [round(r + 2.3 + random.gauss(0, 0.05), 2) for r in real_yield]  # DGS10 ≈ real + breakeven
home_price = walk(300, 0.6, 2.2, lo=280)                  # CSUSHPISA index
dollar_idx = walk(115, 0.15, 1.3, lo=100)                 # DTWEXBGS

def last_change(series):
    return round(series[-1] - series[-2], 2)

snapshot = [
    {"id": "DGS10", "label": "10Y Nominal Yield", "value": nominal10[-1], "unit": "%",
     "change": last_change(nominal10), "as_of": "2026-09-02", "freq": "daily"},
    {"id": "DFII10", "label": "Real 10Y Yield (TIPS)", "value": real_yield[-1], "unit": "%",
     "change": last_change(real_yield), "as_of": "2026-09-02", "freq": "daily"},
    {"id": "T10YIE", "label": "Breakeven Inflation", "value": round(nominal10[-1] - real_yield[-1], 2),
     "unit": "%", "change": 0.01, "as_of": "2026-09-02", "freq": "daily"},
    {"id": "XAUUSD", "label": "Gold (XAU/USD)", "value": gold[-1], "unit": "$",
     "change": round(gold[-1] - gold[-2], 1), "as_of": "2026-09-02", "freq": "daily"},
    {"id": "SPX", "label": "S&P 500", "value": sp500[-1], "unit": "",
     "change": round(sp500[-1] - sp500[-2], 1), "as_of": "2026-09-02", "freq": "daily"},
    {"id": "UNRATE", "label": "Unemployment Rate", "value": unemployment[-1], "unit": "%",
     "change": last_change(unemployment), "as_of": "2026-07-01", "freq": "monthly"},
    {"id": "DTWEXBGS", "label": "Dollar Index (broad)", "value": dollar_idx[-1], "unit": "",
     "change": round(dollar_idx[-1] - dollar_idx[-2], 2), "as_of": "2026-09-02", "freq": "daily"},
    {"id": "CSUSHPISA", "label": "Home Price Index", "value": home_price[-1], "unit": "",
     "change": last_change(home_price), "as_of": "2026-06-01", "freq": "monthly"},
    {"id": "FARM_INCOME", "label": "Net Farm Income", "value": 141.3, "unit": "$B",
     "change": -8.2, "as_of": "2026-Q2", "freq": "quarterly"},
]

# Lag panel: unemployment vs real yield led ~15 months (spec: 12–18mo).
LEAD = 15
lag_dates = DATES[LEAD:]
lag_unemp = unemployment[LEAD:]
lag_realyield_lead = real_yield[: len(lag_dates)]

# Regime heuristic (mirror of what the client would compute). Rising real yields +
# falling gold => "tightening"; the CAVEAT is mandatory UI copy.
ry_trend = real_yield[-1] - real_yield[-4]
gold_trend = gold[-1] - gold[-4]
if ry_trend > 0 and gold_trend < 0:
    label, detail = "Tightening", "Real yields rising while gold softens."
elif ry_trend < 0 and gold_trend > 0:
    label, detail = "Easing", "Real yields falling while gold firms."
else:
    label, detail = "Mixed", "Real yields and gold not clearly diverging."

payload = {
    "meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample": True,
        "data_note": "SAMPLE data for UI review. Freshness shows the DATA date, not the fetch date.",
        "disclaimer": "For monitoring context only. The regime flag is a heuristic, not a signal.",
    },
    "snapshot": snapshot,
    "overlay": {
        "dates": DATES,
        "series": {"real_yield": real_yield, "gold": gold, "sp500": sp500},
        "note": "Real yield / gold / S&P 500 indexed to 100 at the window start so co-movement is comparable.",
    },
    "lag": {
        "lead_months": LEAD,
        "dates": lag_dates,
        "unemployment": lag_unemp,
        "real_yield_lead": lag_realyield_lead,
        "note": f"Unemployment plotted against the real yield shifted forward {LEAD} months (lagged relationship, not causal).",
    },
    "regime": {
        "label": label,
        "detail": detail,
        "caveat": "Heuristic, not a signal.",
    },
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2, ensure_ascii=False)
print(f"[seed_macro] wrote {OUT} — {len(DATES)} monthly points, {len(snapshot)} snapshot cards, regime={label}")
