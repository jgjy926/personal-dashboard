#!/usr/bin/env python3
"""
fetch_macro.py — REAL macro data from FRED's keyless CSV endpoint -> data/macro.json

No API key, no cost. FRED publishes every series as a public CSV at
    https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>
so we pull nominal & real yields, breakeven, unemployment, home prices, the dollar
index, the S&P 500, gold, both crude benchmarks (Brent and WTI) and the USD/JPY
rate. Japan's JGB curve comes from Japan's MoF, which FRED doesn't carry.

FRESHNESS. FRED is authoritative but *slow* for a daily dashboard: DEXJPUS and
DTWEXBGS come off the Fed's H.10 release, which lands ONCE A WEEK (Mondays), and
the EIA oil series are similarly batched — so a card sourced purely from FRED can
legitimately sit 7-11 days behind the market while the fetch job runs perfectly
every day. That looked exactly like a broken refresh. It isn't fixed by fetching
harder; it needs a faster source. So for the market-traded series (gold, Brent,
WTI, USD/JPY, S&P 500) we now keep FRED's long, authoritative history AND splice
Yahoo Finance's daily bars on top of it for the days FRED hasn't published yet
(`splice()`). Yahoo is the *extension*, never a replacement, and every card
carries the `source` that produced its latest point so the UI can show it.

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
import re
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
    "DGS30":     {"ids": ["DGS30"],     "label": "30Y Nominal Yield",     "unit": "%", "freq": "daily"},
    "DFII10":    {"ids": ["DFII10"],    "label": "Real 10Y Yield (TIPS)", "unit": "%", "freq": "daily"},
    "DFII30":    {"ids": ["DFII30"],    "label": "Real 30Y Yield (TIPS)", "unit": "%", "freq": "daily"},
    "T10YIE":    {"ids": ["T10YIE"],    "label": "Breakeven Inflation",   "unit": "%", "freq": "daily"},
    # ^GSPC is the same index FRED's SP500 series tracks (spot-checked equal to
    # the cent on overlapping closes), so it extends rather than contradicts it.
    "SP500":     {"ids": ["SP500"], "yahoo": "^GSPC",
                  "label": "S&P 500", "unit": "", "freq": "daily"},
    "GOLD":      {"ids": ["GOLDPMGBD228NLBM", "GOLDAMGBD228NLBM"], "yahoo": "GC=F",
                  "label": "Gold (LBMA)", "unit": "$", "freq": "daily"},
    # Both crude benchmarks, so the Brent–WTI spread (the transatlantic freight /
    # quality gap that widens when US supply is landlocked) is readable off one
    # shared $/bbl axis rather than inferred from a single price.
    "BRENT":     {"ids": ["DCOILBRENTEU"], "yahoo": "BZ=F",
                  "label": "Brent Crude", "unit": "$", "freq": "daily"},
    "WTI":       {"ids": ["DCOILWTICO"],   "yahoo": "CL=F",
                  "label": "WTI Crude",    "unit": "$", "freq": "daily"},
    "UNRATE":    {"ids": ["UNRATE"],    "label": "Unemployment Rate",     "unit": "%", "freq": "monthly"},
    "DTWEXBGS":  {"ids": ["DTWEXBGS"],  "label": "Dollar Index (broad)",  "unit": "",  "freq": "daily"},
    # Yen per one US dollar (FRED's DEXJPUS quotes it that way round, so a RISING
    # number is a WEAKER yen). Sits next to the broad dollar index deliberately:
    # the pair is the single most reactive leg of it to the JGB yields below.
    "USDJPY":    {"ids": ["DEXJPUS"], "yahoo": "JPY=X",
                  "label": "USD/JPY", "unit": "¥", "freq": "daily"},
    "CSUSHPISA": {"ids": ["CSUSHPISA"], "label": "Home Price Index",      "unit": "",  "freq": "monthly"},
    # Japan yields are NOMINAL — Japan's inflation-indexed (JGBi) market is thin
    # and not published on FRED, so there is no true "Japan TIPS" equivalent to
    # the US DFII series above; these are ordinary JGB yields, labelled as such.
    # Primary source is Japan's Ministry of Finance (`mof_tenor`): free, keyless,
    # daily, authoritative, and it carries every tenor. FRED is only a fallback
    # for 10Y (IRLTLT01JPM156N, an OECD *monthly* series that runs ~3 months
    # stale); FRED has no 30Y JGB series at all (verified: all candidates 404).
    "JP10Y":     {"ids": ["IRLTLT01JPM156N"], "mof_tenor": "10年",
                  "label": "Japan 10Y Nominal Yield", "unit": "%", "freq": "daily"},
    "JP30Y":     {"ids": [], "mof_tenor": "30年",
                  "label": "Japan 30Y Nominal Yield", "unit": "%", "freq": "daily"},
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


YAHOO_CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               "{symbol}?range={rng}&interval={interval}")


def fetch_yahoo_chart(symbol: str, rng: str = "5y", interval: str = "1d",
                      timeout: int = 30) -> str:
    """FRED discontinued its LBMA gold fixings (GOLDPMGBD228NLBM/GOLDAMGBD228NLBM
    both 404 as of this writing), so gold falls back to Yahoo Finance's public,
    keyless chart endpoint — no key, no signup, used by many open-source tools.
    It's unofficial (Yahoo could change/rate-limit it), which is exactly why it
    only ever EXTENDS a FRED series rather than replacing one, and why every call
    here is wrapped in the same try/except-per-series pattern as the FRED
    fetchers.

    interval defaults to "1d". It used to be hardcoded "1mo", which quietly made
    every Yahoo-sourced card a MONTHLY series wearing a "daily" label: the
    snapshot's `change` was then a month-over-month move presented as a daily
    one."""
    req = urllib.request.Request(
        YAHOO_CHART.format(symbol=symbol, rng=rng, interval=interval),
        headers={"User-Agent": "Mozilla/5.0 (macro-dashboard fetcher)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# MoF publishes the curve as TWO files and you need both: jgbcm.csv holds only
# the CURRENT MONTH (5 rows on the 8th of a month — which is all the Japan cards
# ever had), while jgbcm_all.csv carries the full history back to 1974 but stops
# at the end of last month. Fetched in this order and spliced.
MOF_JGB_ALL_CSV = "https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv"
MOF_JGB_CSV = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"


def fetch_mof_jgb(url: str = MOF_JGB_CSV, timeout: int = 30) -> str:
    """Japan's Ministry of Finance publishes the official JGB yield curve as a
    free, keyless CSV — every tenor from 1Y to 40Y, updated daily. This is the
    right source for Japan: FRED carries no 30Y JGB series at all (verified —
    IRLTLT30JP*/JGBS30Y all 404) and its 10Y proxy is an OECD *monthly* series
    that runs ~3 months stale, while MoF is daily and authoritative. The file
    is Shift-JIS encoded and dated in Japanese imperial era format."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (macro-dashboard fetcher)", "Accept": "text/csv,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("shift_jis", errors="replace")


# Japanese imperial era -> the Gregorian year that era's year 1 fell in, minus 1
# (so era year N = OFFSET + N). The history file opens in Showa 49 (1974), so a
# Reiwa-only pattern silently discarded every row before 2019.
_ERA_OFFSET = {"S": 1925, "H": 1988, "R": 2018}
_ERA_RE = re.compile(r"([SHR])(\d+)\.(\d+)\.(\d+)")


def parse_mof_jgb(text: str, tenor: str) -> list[tuple[str, float]]:
    """Pull one tenor column (e.g. "10年", "30年") out of a MoF JGB CSV.
    Layout: a title row, then a header row starting with 基準日 (base date),
    then daily rows dated like "R8.9.1" = Reiwa 8 (2026) Sep 1, or "S49.9.24"
    = Showa 49 (1974) Sep 24 in the history file. Rows whose date doesn't parse
    (footers/notes) are skipped rather than trusted."""
    rows = list(csv.reader(io.StringIO(text)))
    hdr_i = next((i for i, row in enumerate(rows) if row and "基準日" in row[0]), None)
    if hdr_i is None:
        return []
    header = rows[hdr_i]
    if tenor not in header:
        return []
    col = header.index(tenor)
    out: list[tuple[str, float]] = []
    for row in rows[hdr_i + 1:]:
        if not row or len(row) <= col:
            continue
        m = _ERA_RE.match(row[0].strip())
        if not m:
            continue
        era, era_y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        try:
            year = _ERA_OFFSET[era] + era_y
            out.append((f"{year:04d}-{mo:02d}-{d:02d}", float(row[col])))
        except ValueError:
            continue
    return out


def parse_yahoo_chart(text: str) -> tuple[list[tuple[str, float]], str | None]:
    """Yahoo's chart JSON: parallel `timestamp` (unix seconds) and
    `indicators.quote[0].close` arrays. Returns
    ([(YYYY-MM-DD, value)] ascending, provisional_date) — null closes (non-trading
    days some ranges include) skipped.

    Two things the naive read gets wrong:

    * Bars are stamped at the session's OPEN in the exchange's own timezone, so
      they must be dated with `meta.gmtoffset`, not in UTC. JPY=X trades on a
      London clock whose session opens at 23:00 UTC the day BEFORE — read as UTC,
      every yen observation lands one calendar day early.
    * The final bar is the CURRENT session, which may still be open — a live
      intraday quote, not a settled close. It is genuinely what a monitor wants
      to show, so it is kept, and its date is returned as `provisional_date` so
      the card can say so rather than implying the day has closed.
    """
    data = json.loads(text)
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        return [], None
    r0 = result[0]
    meta = r0.get("meta") or {}
    offset = int(meta.get("gmtoffset") or 0)
    ts = r0.get("timestamp") or []
    closes = ((r0.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []

    def local_date(t: int) -> str:
        return datetime.fromtimestamp(t + offset, tz=timezone.utc).strftime("%Y-%m-%d")

    out = []
    for t, v in zip(ts, closes):
        if v is None:
            continue
        out.append((local_date(t), float(v)))
    if not out:
        return [], None

    # The session described by currentTradingPeriod.regular is still running if
    # the last trade Yahoo has (regularMarketTime) falls before its end.
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = regular.get("start"), regular.get("end")
    rmt = meta.get("regularMarketTime")
    provisional = None
    if start and end and rmt and rmt < end and out[-1][0] >= local_date(int(start)):
        provisional = out[-1][0]
    return out, provisional


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


def splice(base: list[tuple[str, float]],
           extra: list[tuple[str, float]]) -> tuple[list[tuple[str, float]], int]:
    """Extend `base` with the points in `extra` dated strictly AFTER base's last
    observation, returning (merged, n_appended). Both ascending [(date, value)].

    This is how a slow-but-authoritative source and a fast-but-unofficial one are
    combined without either overwriting the other: FRED's settled history is never
    touched, and Yahoo only ever fills the days FRED has not published yet. With
    an empty base it degenerates to "use `extra`", which is the old fallback
    behaviour, so a total FRED outage still works exactly as before."""
    if not extra:
        return list(base), 0
    if not base:
        return list(extra), len(extra)
    cutoff = base[-1][0]
    tail = [(d, v) for d, v in extra if d > cutoff]
    return list(base) + tail, len(tail)


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


def snapshot_card(sid: str, series: list[tuple[str, float]],
                  origin: dict | None = None) -> dict:
    """One snapshot card. `origin` carries provenance from the fetch loop:
    which source produced the LATEST point, and whether that point is a still-open
    session rather than a settled close. Both are rendered on the card — a number
    the reader cannot trace the age of is what made this feed look broken."""
    meta = SERIES[sid]
    origin = origin or {}
    last_d, last_v = series[-1]
    prev_v = series[-2][1] if len(series) > 1 else None
    change = round(last_v - prev_v, 2) if prev_v is not None else None
    card = {"id": sid, "label": meta["label"], "value": round(last_v, 2),
            "unit": meta["unit"], "change": change, "as_of": last_d,
            "freq": meta["freq"]}
    if origin.get("source"):
        card["source"] = origin["source"]
    if origin.get("provisional") == last_d:
        card["provisional"] = True
    return card


def build_payload(raw: dict[str, list[tuple[str, float]]], years: int,
                  origins: dict[str, dict] | None = None) -> dict:
    origins = origins or {}
    have = {k: v for k, v in raw.items() if v}
    missing = [sid for sid in SERIES if sid not in have]
    if not have:
        raise SystemExit("[fetch_macro] no series returned — refusing to overwrite.")
    n = years * 12
    # axis anchored on the latest month any daily series has data for
    latest_ym = max(monthly(s) and max(monthly(s)) for s in have.values())
    axis = month_axis(n, latest_ym)

    snapshot = [snapshot_card(sid, have[sid], origins.get(sid))
                for sid in SERIES if sid in have]

    real_yield = align(monthly(have["DFII10"]), axis, 2) if "DFII10" in have else []
    gold = align(monthly(have["GOLD"]), axis, 1) if "GOLD" in have else []
    sp500 = align(monthly(have["SP500"]), axis, 1) if "SP500" in have else []
    unemp = align(monthly(have["UNRATE"]), axis, 2) if "UNRATE" in have else []
    brent = align(monthly(have["BRENT"]), axis, 2) if "BRENT" in have else []
    wti = align(monthly(have["WTI"]), axis, 2) if "WTI" in have else []
    usdjpy = align(monthly(have["USDJPY"]), axis, 2) if "USDJPY" in have else []

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
            "source": "FRED (keyless CSV) + Yahoo Finance + Japan MoF",
            "data_note": ("Live data. Freshness shows the DATA date, not the fetch date. "
                          "FRED supplies each series' settled history; Yahoo Finance extends "
                          "the market-traded ones over the days FRED has not published yet "
                          "(the Fed's H.10 dollar/yen release is weekly, the EIA crude series "
                          "are batched), so every card names the source behind its latest point."),
            "disclaimer": "For monitoring context only. The regime flag is a heuristic, not a signal.",
            "missing_series": missing,  # honest, visible gap — e.g. if FRED drops/renames an id
            # The newest observation across every daily series. The UI compares
            # this (and generated_at) with the wall clock to answer the only
            # question a stale-looking card really raises: is the job still running?
            "latest_observation": max(
                (v[-1][0] for sid, v in have.items()
                 if SERIES[sid]["freq"] == "daily"), default=None),
        },
        "snapshot": snapshot,
        "overlay": {
            "dates": axis,
            "series": {"real_yield": real_yield, "gold": gold, "sp500": sp500},
            "note": "Real yield / gold / S&P 500 each scaled to its own 0–100 range so co-movement is comparable (min→max of the window, not indexed to the first point — robust to a series like real yield opening the window near/below zero).",
        },
        # Both benchmarks are quoted in $/bbl, so these plot on a SHARED, un-normalised
        # axis — the gap between the two lines is the Brent-WTI spread itself, which a
        # per-series 0-100 rescale (as used by the overlay above) would destroy.
        "oil": {
            "dates": axis,
            "series": {"brent": brent, "wti": wti},
            "unit": "$/bbl",
            "note": "Brent (waterborne, the global marginal barrel) vs WTI (Cushing, Oklahoma, landlocked). Same $/bbl axis, so the vertical gap between the lines is the Brent-WTI spread.",
        },
        "fx": {
            "dates": axis,
            "series": {"usdjpy": usdjpy},
            "unit": "JPY per USD",
            "note": "Yen per one US dollar, so a RISING line is a WEAKER yen. Month-end observations of a daily series.",
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
    origins: dict[str, dict] = {}     # sid -> {"source", "provisional"}
    mof_cache: dict[str, str] = {}    # url -> text, so both JGB tenors share a fetch
    for sid, meta in SERIES.items():
        points: list[tuple[str, float]] = []
        source: str | None = None
        provisional: str | None = None
        errors: list[str] = []

        # Japan: Ministry of Finance (daily + authoritative + every tenor). Two
        # files — full history, then the current month on top — because jgbcm.csv
        # alone is current-month-only and gave the JP cards a 5-point history.
        # NOTE: keep the fetch/parse inside try/except but the logging OUTSIDE
        # it. The tenor label is Japanese ("10年"), and printing that to a
        # non-UTF-8 console (Windows cp1252) raises UnicodeEncodeError — if that
        # happened inside the try, a *logging* failure would silently demote us
        # to the stale monthly FRED series. Log an ASCII-safe label instead.
        if meta.get("mof_tenor"):
            for url in (MOF_JGB_ALL_CSV, MOF_JGB_CSV):
                try:
                    if url not in mof_cache:
                        mof_cache[url] = fetch_mof_jgb(url)
                    points, _ = splice(points, parse_mof_jgb(mof_cache[url], meta["mof_tenor"]))
                except Exception as e:
                    errors.append(f"MoF {url.rsplit('/', 1)[-1]}: {e}")
            if points:
                source = "MoF JGB curve"
                print(f"[fetch_macro] {sid}: {len(points)} obs "
                      f"(latest {points[-1][0]}) (via MoF JGB curve)")

        # FRED: the authoritative history for everything else.
        if not points:
            for candidate in meta["ids"]:
                try:
                    points = parse_fred_csv(fetch_fred_csv(candidate))
                except Exception as e:
                    errors.append(f"{candidate}: {e}")
                    continue
                if points:
                    source = f"FRED {candidate}"
                    tag = f" (via {candidate})" if candidate != sid else ""
                    print(f"[fetch_macro] {sid}: {len(points)} obs"
                          f" (latest {points[-1][0]}){tag}")
                    break

        # Yahoo Finance: attempted for every series that names a symbol, not just
        # on total FRED failure. Several FRED series are published in WEEKLY
        # batches (H.10 for DEXJPUS/DTWEXBGS, EIA for the crudes), so FRED can be
        # a fully working source and still be 7-11 days behind the market — which
        # is what made these cards look like a dead refresh. splice() appends only
        # the days FRED hasn't got yet, so the settled history stays FRED's.
        if meta.get("yahoo"):
            sym = meta["yahoo"]
            try:
                ypoints, yprov = parse_yahoo_chart(
                    fetch_yahoo_chart(sym, rng=f"{args.years}y"))
            except Exception as e:
                errors.append(f"yahoo {sym}: {e}")
            else:
                points, added = splice(points, ypoints)
                if added:
                    provisional = yprov
                    source = f"Yahoo {sym}" if source is None else f"{source} + Yahoo {sym}"
                    print(f"[fetch_macro] {sid}: +{added} obs from Yahoo {sym} "
                          f"-> latest {points[-1][0]}"
                          + (" (session still open)" if yprov == points[-1][0] else ""))

        raw[sid] = points
        origins[sid] = {"source": source, "provisional": provisional}
        if not points:
            print(f"[fetch_macro] {sid}: FAILED all candidates — {'; '.join(errors)}",
                  file=sys.stderr)

    payload = build_payload(raw, args.years, origins)
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
