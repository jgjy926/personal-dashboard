#!/usr/bin/env python3
"""
export_klse.py — read-only exporter: AlphaSpike SQLite -> data/klse.json

The KLSE_Monitor engine writes all of its state to a local SQLite file
(alphaspike_state.db). This exporter READS that DB (never writes to it, never
touches the engine) and produces a compact JSON snapshot the dashboard's KLSE
tab can render. Run it after an engine run, or any time.

    python tools/export_klse.py
    python tools/export_klse.py --db "../KLSE_Monitor/alphaspike_state.db" --out data/klse.json --top 25

Cloud publish (Phase B): once this writes data/klse.json into the dashboard repo,
`git commit && git push` (or a Cloudflare Pages deploy) makes it same-origin and
browser-fetchable with no keys and no CORS. See README "KLSE -> cloud".
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DB = os.path.normpath(os.path.join(ROOT, "..", "KLSE_Monitor", "alphaspike_state.db"))
DEFAULT_OUT = os.path.join(ROOT, "data", "klse.json")


def _connect_ro(path: str) -> sqlite3.Connection:
    """Open the DB strictly read-only so we can never mutate engine state."""
    uri = f"file:{os.path.abspath(path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _json_list(raw) -> list[str]:
    """reasons/risks are stored as a JSON array string; fail soft to []."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]
    except Exception:
        return [str(raw)]


def _clean_sector(s: str | None) -> str | None:
    """Source sometimes concatenates two sector labels with no separator, e.g.
    'Consumer ServicesConsumer Products & Services'. Split on a lower->Upper
    boundary and keep the first (most specific) label for display."""
    if not s:
        return s
    out = [s[0]]
    for i in range(1, len(s)):
        if s[i].isupper() and s[i - 1].islower():
            break
        out.append(s[i])
    return "".join(out).strip()


def _latest_run(conn: sqlite3.Connection, table: str, col: str = "run_date") -> str | None:
    if not _table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT MAX({col}) m FROM {table}").fetchone()
    return row["m"] if row and row["m"] else None


def build_funnel(conn: sqlite3.Connection) -> dict:
    if not _table_exists(conn, "run_log"):
        return {}
    r = conn.execute(
        "SELECT * FROM run_log WHERE status='COMPLETED' ORDER BY run_date DESC, run_id DESC LIMIT 1"
    ).fetchone()
    if not r:
        return {}
    k = r.keys()

    def g(name):
        return r[name] if name in k else None

    stages = [
        ("Universe", g("universe_total")),
        ("Bull filter", g("after_bull_filter")),
        ("Momentum", g("after_momentum")),
        ("Breakout", g("after_breakout")),
        ("Volume", g("after_volume")),
        ("Liquidity", g("after_liquidity")),
        ("Fundamental", g("after_fundamental")),
        ("Spread", g("after_spread")),
        ("Alerts sent", g("alerts_sent")),
    ]
    return {
        "run_date": g("run_date"),
        "stages": [{"label": lbl, "count": val} for lbl, val in stages if val is not None],
    }


def build_conviction(conn: sqlite3.Connection, top: int) -> dict:
    if not _table_exists(conn, "conviction_scores"):
        return {"run_date": None, "rows": []}
    run_date = _latest_run(conn, "conviction_scores")
    if not run_date:
        return {"run_date": None, "rows": []}
    cols = _cols(conn, "conviction_scores")

    def has(c):
        return c in cols

    rows = conn.execute(
        "SELECT * FROM conviction_scores WHERE run_date=? ORDER BY rank ASC LIMIT ?",
        (run_date, top),
    ).fetchall()
    out = []
    for r in rows:
        k = r.keys()
        out.append(
            {
                "rank": r["rank"] if "rank" in k else None,
                "ticker": r["ticker"] if "ticker" in k else None,
                "name": r["name"] if "name" in k else None,
                "conviction": round(r["conviction"], 1) if "conviction" in k and r["conviction"] is not None else None,
                "last_close": r["last_close"] if "last_close" in k else None,
                "rel_volume": round(r["rel_volume"], 2) if "rel_volume" in k and r["rel_volume"] is not None else None,
                "liquidity_myr": round(r["liquidity_myr"]) if "liquidity_myr" in k and r["liquidity_myr"] is not None else None,
                "gate6": bool(r["gate6"]) if "gate6" in k else False,
                "cold_eye": bool(r["cold_eye"]) if "cold_eye" in k else False,
                "sector": _clean_sector(r["sector"]) if "sector" in k else None,
                "reasons": _json_list(r["reasons"]) if "reasons" in k else [],
                "risks": _json_list(r["risks"]) if "risks" in k else [],
            }
        )
    return {"run_date": run_date, "rows": out}


def build_signals(conn: sqlite3.Connection) -> dict:
    """Signal-ledger performance: hit rate + avg forward return on resolved
    signals, plus a few most-recent rows for context."""
    if not _table_exists(conn, "signal_ledger"):
        return {}
    total = conn.execute("SELECT COUNT(*) n FROM signal_ledger").fetchone()["n"]
    finals = conn.execute(
        "SELECT hit, fwd_ret_20 FROM signal_ledger WHERE outcome_status='final'"
    ).fetchall()
    tp = sum(1 for r in finals if (r["hit"] or "").lower() == "tp")
    sl = sum(1 for r in finals if (r["hit"] or "").lower() == "sl")
    resolved = tp + sl
    rets = [r["fwd_ret_20"] for r in finals if r["fwd_ret_20"] is not None]
    recent = conn.execute(
        "SELECT run_date,ticker,name,hit,fwd_ret_20,outcome_status,disposition "
        "FROM signal_ledger ORDER BY run_date DESC LIMIT 12"
    ).fetchall()
    return {
        "total": total,
        "resolved": resolved,
        "tp": tp,
        "sl": sl,
        "hit_rate": round(100 * tp / resolved, 1) if resolved else None,
        "avg_fwd_ret_20": round(sum(rets) / len(rets), 2) if rets else None,
        "recent": [
            {
                "run_date": r["run_date"],
                "ticker": r["ticker"],
                "name": r["name"],
                "hit": r["hit"],
                "fwd_ret_20": round(r["fwd_ret_20"], 2) if r["fwd_ret_20"] is not None else None,
                "status": r["outcome_status"],
                "disposition": r["disposition"],
            }
            for r in recent
        ],
    }


def build_trades(conn: sqlite3.Connection) -> dict:
    """Closed-trade P&L vs FBMKLCI benchmark (alpha)."""
    if not _table_exists(conn, "trade_log"):
        return {}
    rows = conn.execute(
        "SELECT ticker,entry_date,exit_date,exit_reason,trade_return_pct,benchmark_return_pct "
        "FROM trade_log WHERE exit_date IS NOT NULL ORDER BY exit_date DESC"
    ).fetchall()
    closed = []
    for r in rows:
        tr = r["trade_return_pct"]
        br = r["benchmark_return_pct"]
        alpha = (tr - br) if (tr is not None and br is not None) else None
        closed.append(
            {
                "ticker": r["ticker"],
                "entry_date": r["entry_date"],
                "exit_date": r["exit_date"],
                "exit_reason": r["exit_reason"],
                "return_pct": round(tr, 2) if tr is not None else None,
                "benchmark_pct": round(br, 2) if br is not None else None,
                "alpha_pct": round(alpha, 2) if alpha is not None else None,
            }
        )
    wins = sum(1 for c in closed if (c["return_pct"] or 0) > 0)
    alphas = [c["alpha_pct"] for c in closed if c["alpha_pct"] is not None]
    rets = [c["return_pct"] for c in closed if c["return_pct"] is not None]
    return {
        "count": len(closed),
        "win_rate": round(100 * wins / len(closed), 1) if closed else None,
        "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "avg_alpha_pct": round(sum(alphas) / len(alphas), 2) if alphas else None,
        "recent": closed[:15],
    }


def build_positions(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "user_positions"):
        return []
    rows = conn.execute(
        "SELECT ticker,entry_price,entry_date,status,note,sold_date,sold_price "
        "FROM user_positions ORDER BY status ASC, entry_date DESC"
    ).fetchall()
    out = []
    for r in rows:
        k = r.keys()
        realised = None
        if r["status"] == "sold" and r["sold_price"] and r["entry_price"]:
            realised = round(100 * (r["sold_price"] - r["entry_price"]) / r["entry_price"], 2)
        out.append(
            {
                "ticker": r["ticker"],
                "entry_price": r["entry_price"],
                "entry_date": r["entry_date"],
                "status": r["status"],
                "note": r["note"] if "note" in k else None,
                "sold_date": r["sold_date"] if "sold_date" in k else None,
                "sold_price": r["sold_price"] if "sold_price" in k else None,
                "realised_pct": realised,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Export AlphaSpike SQLite -> dashboard JSON")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite path (default: {DEFAULT_DB})")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"Output JSON (default: {DEFAULT_OUT})")
    ap.add_argument("--top", type=int, default=25, help="How many conviction rows to include")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[export_klse] DB not found: {args.db}")
        return 2

    conn = _connect_ro(args.db)
    try:
        payload = {
            "meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": os.path.basename(args.db),
                "engine": "AlphaSpike v4",
                "disclaimer": "Screener output, not trade advice. Local snapshot as of the export time.",
            },
            "funnel": build_funnel(conn),
            "conviction": build_conviction(conn, args.top),
            "signals": build_signals(conn),
            "trades": build_trades(conn),
            "positions": build_positions(conn),
        }
    finally:
        conn.close()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    conv = payload["conviction"]
    print(f"[export_klse] wrote {args.out}")
    print(f"  run_date={conv.get('run_date')}  conviction_rows={len(conv.get('rows', []))}"
          f"  trades={payload['trades'].get('count')}  positions={len(payload['positions'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
