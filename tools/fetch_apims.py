#!/usr/bin/env python3
"""
fetch_apims.py — Malaysia Air Pollutant Index (APIMS) -> data/apims.json

Source: the Department of Environment's own public portal,
    https://eqms.doe.gov.my/APIMS/main
which is a single-page app reading these keyless JSON endpoints (checked: no
auth, no cookies, plain GET):

  1. LATEST, ALL STATIONS — one row per continuous monitoring station (68 at
     the time of writing), with coordinates and the latest hourly API.
         /api3/publicportalapims/mapcaqm
  2. LAST 24 HOURS, PER STATE — every station in the state, one row per hour.
         /api3/publicportalapims/apitablehourly?stateid=N&datetime=<MYT hour>

Why it is fetched here and not by the browser: the API answers CORS only for
DOE's own origin (Access-Control-Allow-Origin: https://eqmp.doe.gov.my), so a
page on github.io cannot read it directly. Same reason the other tabs read a
JSON feed rather than the upstream service.

Two upstream quirks the parse below exists to absorb:

  * Timestamps are NAIVE Malaysia time ("2026-10-11T12:00:00"). They are
    stamped +08:00 here, so the page's "N min ago" is right in any timezone.
  * "No reading" is spelled several ways (null, "", "NA", "N/A", negative
    numbers for an offline analyser). All of them become None — never 0, which
    would publish a station as having perfectly clean air.

The PARAM_SYMBOL on each reading names the pollutant that set the index. The
mapping is taken from APIMS's own legend (in its JS bundle), which is the
opposite of what you might guess:  "*" = PM10,  "**" = PM2.5.

Usage:
    python tools/fetch_apims.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "apims.json")

BASE = "https://eqms.doe.gov.my/api3/publicportalapims"
MAP_URL = f"{BASE}/mapcaqm"
TABLE_URL = BASE + "/apitablehourly?stateid={state}&datetime={hour}"
PORTAL = "https://eqms.doe.gov.my/APIMS/main"
UA = {"User-Agent": "Mozilla/5.0 (personal-dashboard fetcher)", "Accept": "application/json"}

MYT = timezone(timedelta(hours=8))

# Pollutant that set the index, per APIMS's legend.
POLLUTANTS = {
    "*": "PM10",
    "**": "PM2.5",
    "a": "SO2",
    "b": "NO2",
    "c": "O3",
    "d": "CO",
}

# Malaysian API bands (DOE). Upper bound inclusive; anything above the last is
# Hazardous. Computed here rather than trusting each row's CLASS, because the
# 24h history rows don't carry one and both must be coloured alike.
BANDS = [(50, "Good"), (100, "Moderate"), (200, "Unhealthy"), (300, "Very Unhealthy")]


# ── network (isolated so parsing stays pure/testable) ───────────────────────
def _get_json(url: str, timeout: int = 30, tries: int = 4):
    """GET with backoff on 429. APIMS rate-limits bursts: 6 parallel requests
    from a GitHub runner got 3 of 16 states refused."""
    for attempt in range(tries):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == tries - 1:
                raise
            retry_after = e.headers.get("Retry-After", "")
            time.sleep(min(int(retry_after), 30) if retry_after.isdigit() else 2 * (attempt + 1))


# ── pure parsing ────────────────────────────────────────────────────────────
def api_value(v) -> int | None:
    """An API reading, or None for every spelling of 'no reading'."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip()
        if not v or v.upper() in ("NA", "N/A", "NULL", "-"):
            return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n < 0:          # NaN, or a negative "analyser offline" code
        return None
    return int(round(n))


def category(api: int | None) -> str:
    if api is None:
        return "N/A"
    for top, name in BANDS:
        if api <= top:
            return name
    return "Hazardous"


def myt_iso(s) -> str | None:
    """'2026-10-11T12:00:00' (naive MYT) -> '2026-10-11T12:00:00+08:00'."""
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", ""))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MYT)
    return dt.astimezone(MYT).isoformat(timespec="seconds")


def _title_location(loc: str) -> str:
    """'Kangar, PERLIS' -> keeps the station name, drops the shouted state
    (the state is its own column). A few newer stations shout the name too
    ('JOHAN SETIA, Selangor'), so an all-caps name is title-cased."""
    name = (loc or "").split(",")[0].strip()
    return name.title() if name.isupper() else name


def parse_map(payload) -> dict[str, dict]:
    """mapcaqm -> {station_id: station} with the latest reading."""
    rows = (payload or {}).get("data") if isinstance(payload, dict) else payload
    out = {}
    for r in rows or []:
        sid = (r.get("STATION_ID") or "").strip()
        if not sid:
            continue
        api = api_value(r.get("API"))
        out[sid] = {
            "id": sid,
            "location": _title_location(r.get("STATION_LOCATION")),
            "state": (r.get("STATE_NAME") or "").strip(),
            "state_id": r.get("STATE_ID"),
            "lat": r.get("LATITUDE"),
            "lon": r.get("LONGITUDE"),
            "active": r.get("STATION_STATUS", 1) == 1,
            "api": api,
            "pollutant": POLLUTANTS.get((r.get("PARAM_SYMBOL") or "").strip()) if api is not None else None,
            "category": category(api),
            "at": myt_iso(r.get("RECORD_DT")),
            "history": [],
        }
    return out


def parse_table(payload) -> dict[str, list[dict]]:
    """apitablehourly -> {station_id: [{t, api, pollutant}] oldest first}."""
    rows = (payload or {}).get("api_table_hourly") if isinstance(payload, dict) else None
    hist: dict[str, dict[str, dict]] = {}
    for r in rows or []:
        sid = (r.get("STATION_ID") or "").strip()
        t = myt_iso(r.get("DATETIME"))
        if not sid or not t:
            continue
        api = api_value(r.get("API"))
        hist.setdefault(sid, {})[t] = {
            "t": t,
            "api": api,
            "pollutant": POLLUTANTS.get((r.get("PARAM_SYMBOL") or "").strip()) if api is not None else None,
        }
    return {sid: [h[t] for t in sorted(h)] for sid, h in hist.items()}


def merge(stations: dict[str, dict], histories: dict[str, list[dict]]) -> list[dict]:
    """Attach 24h history, and let the newest reading win.

    The two endpoints don't advance together: the per-state table regularly
    carries an hour the map hasn't picked up yet (seen: table at 12:00, map
    still at 11:00). Whichever is newer is what the page headlines."""
    for sid, hist in histories.items():
        st = stations.get(sid)
        if st is None:
            continue
        st["history"] = [{"t": h["t"], "api": h["api"]} for h in hist]
        newest = next((h for h in reversed(hist) if h["api"] is not None), None)
        if newest and (st["at"] is None or st["api"] is None or newest["t"] > st["at"]):
            st["api"] = newest["api"]
            st["pollutant"] = newest["pollutant"]
            st["category"] = category(newest["api"])
            st["at"] = newest["t"]
    out = list(stations.values())
    out.sort(key=lambda s: (s["state"], s["location"]))
    return out


def build_payload(stations: list[dict], failed_states: list) -> dict:
    times = [s["at"] for s in stations if s["at"] and s["api"] is not None]
    counts: dict[str, int] = {}
    for s in stations:
        counts[s["category"]] = counts.get(s["category"], 0) + 1
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "readings_as_of": max(times) if times else None,
            "source": "Department of Environment Malaysia — APIMS",
            "source_url": PORTAL,
            "station_count": len(stations),
            "reporting": len(times),
            "categories": counts,
            # States whose 24h table couldn't be fetched this run: those
            # stations still show their latest reading, just no trend.
            "history_missing_states": failed_states,
            "bands": [{"max": top, "category": name} for top, name in BANDS]
                     + [{"max": None, "category": "Hazardous"}],
            "pollutants": POLLUTANTS,
        },
        "stations": stations,
    }


# ── orchestration ───────────────────────────────────────────────────────────
def main() -> int:
    try:
        stations = parse_map(_get_json(MAP_URL))
    except Exception as e:  # noqa: BLE001 — any failure means "keep the old feed"
        print(f"[fetch_apims] station map failed: {e}", file=sys.stderr)
        stations = {}
    if not stations:
        print("[fetch_apims] no stations returned — refusing to overwrite.", file=sys.stderr)
        return 1

    # Ask for the current MYT hour; the endpoint returns the 24 hours up to it.
    hour = datetime.now(MYT).strftime("%Y-%m-%dT%H:00:00")
    state_ids = sorted({s["state_id"] for s in stations.values() if s["state_id"] is not None})

    def one(state_id):
        try:
            return state_id, parse_table(_get_json(TABLE_URL.format(state=state_id, hour=hour)))
        except Exception as e:  # noqa: BLE001
            print(f"[fetch_apims] state {state_id} history failed: {e}", file=sys.stderr)
            return state_id, None

    histories, failed = {}, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for state_id, h in pool.map(one, state_ids):
            if h is None:
                failed.append(state_id)
            else:
                histories.update(h)

    payload = build_payload(merge(stations, histories), failed)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
    m = payload["meta"]
    print(f"[fetch_apims] {m['reporting']}/{m['station_count']} stations reporting, "
          f"as of {m['readings_as_of']} -> {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
