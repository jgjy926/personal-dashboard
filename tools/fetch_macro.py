#!/usr/bin/env python3
"""
fetch_macro.py — REAL macro data from FRED's keyless CSV endpoint -> data/macro.json

No API key, no cost. FRED publishes every series as a public CSV at
    https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>
so we pull nominal & real yields, breakeven, unemployment, home prices, the dollar
index, the S&P 500 and gold — all from one keyless source (no Stooq, which
bot-challenges datacenter IPs). Output matches the exact contract the dashboard's
Macro tab reads, so nothing on the frontend changes.

Run locally or (recommended) in GitHub Actions, whose runners have clean egress:
    python tools/fetch_macro.py
    python tools/fetch_macro.py --years 6

Note: some environments (incl. this project's build sandbox) block FRED; run it
where egress is open. The network layer is isolated in fetch_fred_csv(); the parse
/ resample / assemble functions are pure and unit-tested in tools/test_fetch_macro.py.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "macro.json")

# FRED series ids (all keyless CSV). label/unit/freq drive the snapshot cards.
# Keyed by a stable dashboard id; `ids` lists FRED series to try in order (FRED
# has discontinued/renamed a few series over the years — e.g. the LBMA gold
# fixings were pulled from FRED's public catalogue, so GOLDPMGBD228NLBM 404s as
# of this writing; GOLDAMGBD228NLBM is tried as a fallback, kept even though it
# may also be gone, so a future re-add on FRED's side is picked up for free).
SERIES = {
    "DGS10":     {"ids": ["DGS10"],     "label": "10Y Nominal Yield",     "unit": "%", "freq": "daily"},
    "DFII10":    {"ids": ["DFII10"],    "label": "Real 10Y Yield (TIPS)", "unit": "%", "freq": "daily"},
    "T10YIE":    {"ids": ["T10YIE"],    "label": "Breakeven Inflation",   "unit": "%", "freq": "daily"},
    "SP500":     {"ids": ["SP500"],     "label": "S&P 500",               "unit": "",  "freq": "daily"},
    "GOLD":      {"ids": ["GOLDPMGBD228NLBM", "GOLDAMGBD228NLBM"],
                  "label": "Gold (LBMA)", "unit": "$", "freq": "daily"},
    "UNRATE":    {"ids": ["UNRATE"],    "label": "Unemployment Rate",     "unit": "%", "freq": "monthly"},
    "DTWEXBGS":  {"ids": ["DTWEXBGS"],  "label": "Dollar Index (broad)",  "unit": "",  "freq": "daily"},
    "CSUSHPISA": {"ids": ["CSUSHPISA"], "label": "Home Price Index",      "unit": "",  "freq": "monthly"},
}
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"


# ── network (isolated so the rest is pure + testable) ───────────────────────
def fetch_fred_csv(sid: str, timeout: int = 60) -> str:
    req = urllib.request.Request(
        FRED_CSV.format(sid=sid),
        headers={"User-Agent": "Mozilla/5.0 (macro-dashboard fetcher)",
                 "Accept": "text/csv,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1mo"


def fetch_yahoo_chart(symbol: str, rng: str = "5y", timeout: int = 30) -> str:
    """FRED discontinued its LBMA gold fixings (GOLDPMGBD228NLBM/GOLDAMGBD228NLBM
    both 404 as of this writing), so gold falls back to Yahoo Finance's public,
    keyless chart endpoint — no key, no signup, used by many open-source tools.
    It's unofficial (Yahoo could change/rate-limit it), which is exactly why it's
    a fallback and not the primary source, and why every call here is wrapped in
    the same try/except-per-series pattern as the FRED fetchers."""
    req = urllib.request.Request(
        YAHOO_CHART.format(symbol=symbol, rng=rng),
        headers={"User-Agent": "Mozilla/5.0 (macro-dashboard fetcher)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_yahoo_chart(text: str) -> list[tuple[str, float]]:
    """Yahoo's chart JSON: parallel `timestamp` (unix seconds, UTC) and
    `indicators.quote[0].close` arrays. Returns [(YYYY-MM-DD, value)] ascending,
    skipping null closes (non-trading days some ranges include)."""
    data = json.loads(text)
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        return []
    r0 = result[0]
    ts = r0.get("timestamp") or []
    closes = ((r0.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    out = []
    for t, v in zip(ts, closes):
        if v is None:
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append((d, float(v)))
    return out


# ── pure helpers ────────────────────────────────────────────────────────────
def parse_fred_csv(text: str) -> list[tuple[str, float]]:
    """FRED CSV: header row then DATE,VALUE. Missing values are '.'. Returns
    [(YYYY-MM-DD, value)] ascending, skipping gaps."""
    out = []
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return out
    for row in rows[1:]:
        if len(row) < 2:
            continue
        d, v = row[0].strip(), row[1].strip()
        if not d or v in ("", "."):
            continue
        try:
            out.append((d, float(v)))
        except ValueError:
            continue
    return out


def monthly(series: list[tuple[str, float]]) -> dict[str, float]:
    """Collapse to month-end (last observation in each YYYY-MM)."""
    m: dict[str, float] = {}
    for d, v in series:  # ascending -> last write wins = month-end
        m[d[:7]] = v
    return m


def month_axis(n: int, end_ym: str) -> list[str]:
    y, mo = int(end_ym[:4]), int(end_ym[5:7])
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{mo:02d}")
        mo -= 1
        if mo == 0:
            mo, y = 12, y - 1
    return list(reversed(out))


def align(monthly_map: dict[str, float], axis: list[str], nd: int = 2) -> list[float | None]:
    return [round(monthly_map[ym], nd) if ym in monthly_map else None for ym in axis]


def snapshot_card(sid: str, series: list[tuple[str, float]]) -> dict:
    meta = SERIES[sid]
    last_d, last_v = series[-1]
    prev_v = series[-2][1] if len(series) > 1 else None
    change = round(last_v - prev_v, 2) if prev_v is not None else None
    return {"id": sid, "label": meta["label"], "value": round(last_v, 2),
            "unit": meta["unit"], "change": change, "as_of": last_d, "freq": meta["freq"]}


def build_payload(raw: dict[str, list[tuple[str, float]]], years: int) -> dict:
    have = {k: v for k, v in raw.items() if v}
    missing = [sid for sid in SERIES if sid not in have]
    if not have:
        raise SystemExit("[fetch_macro] no series returned — refusing to overwrite.")
    n = years * 12
    # axis anchored on the latest month any daily series has data for
    latest_ym = max(monthly(s) and max(monthly(s)) for s in have.values())
    axis = month_axis(n, latest_ym)

    snapshot = [snapshot_card(sid, have[sid]) for sid in SERIES if sid in have]

    real_yield = align(monthly(have["DFII10"]), axis, 2) if "DFII10" in have else []
    gold = align(monthly(have["GOLD"]), axis, 1) if "GOLD" in have else []
    sp500 = align(monthly(have["SP500"]), axis, 1) if "SP500" in have else []
    unemp = align(monthly(have["UNRATE"]), axis, 2) if "UNRATE" in have else []

    LEAD = 15
    lag_axis = axis[LEAD:]
    lag_unemp = unemp[LEAD:] if unemp else []
    lag_real_lead = real_yield[: len(lag_axis)] if real_yield else []

    # regime heuristic over the last ~4 valid monthly points
    def trend(vals):
        v = [x for x in vals if x is not None]
        return (v[-1] - v[-4]) if len(v) >= 4 else 0.0
    ry_t, gold_t = trend(real_yield), trend(gold)
    if ry_t > 0 and gold_t < 0:
        label, detail = "Tightening", "Real yields rising while gold softens."
    elif ry_t < 0 and gold_t > 0:
        label, detail = "Easing", "Real yields falling while gold firms."
    else:
        label, detail = "Mixed", "Real yields and gold not clearly diverging."

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sample": False,
            "source": "FRED (keyless CSV)",
            "data_note": "Live FRED data. Freshness shows the DATA date, not the fetch date.",
            "disclaimer": "For monitoring context only. The regime flag is a heuristic, not a signal.",
            "missing_series": missing,  # honest, visible gap — e.g. if FRED drops/renames an id
        },
        "snapshot": snapshot,
        "overlay": {
            "dates": axis,
            "series": {"real_yield": real_yield, "gold": gold, "sp500": sp500},
            "note": "Real yield / gold / S&P 500 each scaled to its own 0–100 range so co-movement is comparable (min→max of the window, not indexed to the first point — robust to a series like real yield opening the window near/below zero).",
        },
        "lag": {
            "lead_months": LEAD, "dates": lag_axis,
            "unemployment": lag_unemp, "real_yield_lead": lag_real_lead,
            "note": f"Unemployment plotted against the real yield shifted forward {LEAD} months (lagged relationship, not causal).",
        },
        "regime": {"label": label, "detail": detail, "caveat": "Heuristic, not a signal."},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    raw: dict[str, list[tuple[str, float]]] = {}
    for sid, meta in SERIES.items():
        raw[sid] = []
        errors = []
        for candidate in meta["ids"]:
            try:
                raw[sid] = parse_fred_csv(fetch_fred_csv(candidate))
                if raw[sid]:
                    tag = f" (via {candidate})" if candidate != sid else ""
                    print(f"[fetch_macro] {sid}: {len(raw[sid])} obs"
                          f" (latest {raw[sid][-1][0]}){tag}")
                    break
            except Exception as e:
                errors.append(f"{candidate}: {e}")
        else:
            # Every FRED candidate failed. Gold specifically has a free, keyless
            # non-FRED fallback (Yahoo Finance chart API) — try it before giving up.
            if sid == "GOLD":
                try:
                    raw[sid] = parse_yahoo_chart(fetch_yahoo_chart("GC=F", rng=f"{args.years}y"))
                    if raw[sid]:
                        print(f"[fetch_macro] {sid}: {len(raw[sid])} obs "
                              f"(latest {raw[sid][-1][0]}) (via Yahoo Finance GC=F, FRED unavailable)")
                except Exception as e:
                    errors.append(f"yahoo GC=F: {e}")
            if not raw[sid]:
                print(f"[fetch_macro] {sid}: FAILED all candidates — {'; '.join(errors)}", file=sys.stderr)

    payload = build_payload(raw, args.years)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    missing = payload["meta"]["missing_series"]
    print(f"[fetch_macro] wrote {args.out} — {len(payload['snapshot'])} cards, "
          f"regime={payload['regime']['label']}"
          + (f", MISSING: {', '.join(missing)}" if missing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
