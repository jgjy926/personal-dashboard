#!/usr/bin/env python3
"""
fetch_aq_forecast.py — MET Malaysia's MyCMAQ 5-day air-quality forecast,
read at every APIMS station -> data/aq_forecast.json

Source: https://mycmaq.met.gov.my — MET Malaysia's operational WRF-CMAQ model
(9 km over Malaysia, hourly, 120 h ahead, a new run every 12 h). It publishes
NO numbers: each run is a folder of PNG maps plus a manifest listing them,

    /                                    index; its first display_<run>.html link is the latest run
    /<YYYYMMDD_HH>/manifest.json         {"files": ["PM25_TOT_d02_20261024_15LST.png", ...]}
    /<YYYYMMDD_HH>/<VAR>_d02_<YYYYMMDD>_<HH>LST.png

so the numbers are read back off the maps. That works because the maps are
regular: the same NCL layout every frame (an equirectangular lat/lon frame, a
24-box colour bar with one shared palette for every pollutant), only the bar's
labels differ per pollutant. For each station we take the pixels around its
lat/lon, match them to a colour-bar box, and take that box's concentration
range. A box is coarse (e.g. PM2.5 35-55 ug/m3 in three steps), so the result
is a BAND, rounded to the box mid-point; that's the honest precision here.

Concentrations become an API with the map legend's own breakpoints, which are
the Malaysian API bands (the legend reads e.g. PM2.5 "Unhealthy (36-150)" =
API 101-200). The day's API is, as APIMS does it, the worst pollutant: PM2.5
and PM10 on their mean over the day, O3 on its hourly peak.

Layout guard: if a frame's axes or colour bar aren't where this expects, the
site changed its plots and every reading would be wrong, so it refuses rather
than publishing guesses. Not on the push fast path: it downloads ~180 maps
(~45 MB) and needs Pillow.

Usage:
    python tools/fetch_aq_forecast.py            # -> data/aq_forecast.json
    python tools/fetch_aq_forecast.py --step 1   # every hour (2x the downloads)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import statistics
import sys
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "aq_forecast.json")
APIMS = os.path.join(ROOT, "data", "apims.json")

SITE = "https://mycmaq.met.gov.my"
UA = {"User-Agent": "Mozilla/5.0 (personal-dashboard fetcher)"}
MYT = timezone(timedelta(hours=8))
DOMAIN = "d02"                     # 9 km, Malaysia; d01 is 27 km Southeast Asia

# ── the plot layout (1024x1024, measured off the frames) ───────────────────
FRAME = (112, 219, 983, 763)       # x0, y0, x1, y1 of the black map border
# Axis ticks: 105/110/115 E at x = 365/574/783, 4 N and 2 S at y = 452/703.
PX_PER_DEG_X = (783 - 365) / 10
PX_PER_DEG_Y = (703 - 452) / 6
X_105E, Y_EQ = 365, 452 + 4 * PX_PER_DEG_Y

# The colour bar, box 0 (left, below the first level) to box 23 (right, above
# the last). Same palette on every pollutant's map.
PALETTE = [
    (255, 255, 255), (250, 255, 250), (220, 255, 220), (60, 200, 60), (0, 255, 0),
    (255, 255, 180), (255, 235, 120), (255, 255, 0), (255, 190, 120), (255, 160, 80),
    (255, 126, 0), (255, 60, 60), (255, 25, 25), (255, 0, 0), (180, 60, 180),
    (150, 25, 150), (128, 0, 128), (160, 30, 50), (140, 15, 45), (126, 0, 35),
    (100, 0, 25), (70, 0, 15), (40, 0, 5), (0, 0, 0),
]
BOX_OF = {c: i for i, c in enumerate(PALETTE)}
BAR_Y, BAR_X0, BAR_W = 845, 117, 36        # box i is centred at BAR_X0 + 17 + 36*i
BLACK = len(PALETTE) - 1                   # also the colour of coastlines + wind barbs

# Per pollutant: the bar's labelled levels (they sit on box edges 0, 4, 7, ...
# 22; the unlabelled edges between are interpolated), and the legend's
# concentration -> API breakpoints. Units: ug/m3, O3 in ppb.
LABEL_EDGES = [0, 4, 7, 10, 13, 16, 19, 22]
POLLUTANTS = {
    "PM2.5": {"var": "PM25_TOT", "labels": [0, 35, 55, 150, 200, 250, 375, 500],
              "api": [(0, 0), (35, 100), (150, 200), (250, 300), (500, 500)], "agg": "mean"},
    "PM10": {"var": "PM10", "labels": [0, 150, 250, 350, 385, 420, 510, 600],
             "api": [(0, 0), (150, 100), (350, 200), (420, 300), (600, 500)], "agg": "mean"},
    "O3": {"var": "O3", "labels": [0, 76, 95, 115, 274, 374, 500, 700],
           "api": [(0, 0), (76, 100), (115, 200), (374, 300), (500, 500)], "agg": "max"},
}
BANDS = [(50, "Good"), (100, "Moderate"), (200, "Unhealthy"), (300, "Very Unhealthy")]
MIN_DAY_HOURS = 12                 # a day with less forecast than this is dropped


# ── pure: colour bar / geometry / API ──────────────────────────────────────
def box_edges(labels: list[float]) -> list[float]:
    """The 23 box edges (edge k is the top of box k), labelled ones exact."""
    edges = []
    for (e0, v0), (e1, v1) in zip(zip(LABEL_EDGES, labels), zip(LABEL_EDGES[1:], labels[1:])):
        edges += [v0 + (v1 - v0) * (k - e0) / (e1 - e0) for k in range(e0, e1)]
    return edges + [labels[-1]]


def box_value(box: int, edges: list[float]) -> float:
    """A box's representative concentration: its mid-point; the open-ended
    top box reads as its floor."""
    if box <= 0:
        return 0.0
    if box >= len(edges):
        return float(edges[-1])
    return (edges[box - 1] + edges[box]) / 2


def to_api(conc: float, knots: list[tuple[float, float]]) -> int:
    for (c0, a0), (c1, a1) in zip(knots, knots[1:]):
        if conc <= c1:
            return round(a0 + (a1 - a0) * (conc - c0) / (c1 - c0))
    return 500


def category(api: int | None) -> str:
    if api is None:
        return "N/A"
    for top, name in BANDS:
        if api <= top:
            return name
    return "Hazardous"


def lonlat_to_px(lon: float, lat: float) -> tuple[int, int] | None:
    x = round(X_105E + (lon - 105) * PX_PER_DEG_X)
    y = round(Y_EQ - lat * PX_PER_DEG_Y)
    x0, y0, x1, y1 = FRAME
    return (x, y) if x0 < x < x1 and y0 < y < y1 else None


def sample_box(px, x: int, y: int) -> int | None:
    """The colour-bar box at (x, y), read through the overlays. Coastlines,
    wind barbs and station circles are drawn in black, which is also the top
    box's colour, so black only counts when it dominates the neighbourhood
    (a real 'Extreme' area is solid). Otherwise: the median non-black box."""
    for r in (2, 4):
        boxes = [BOX_OF.get(px[x + dx, y + dy]) for dx in range(-r, r + 1) for dy in range(-r, r + 1)]
        boxes = [b for b in boxes if b is not None]
        if not boxes:
            continue
        if boxes.count(BLACK) >= 0.6 * (2 * r + 1) ** 2:
            return BLACK
        rest = sorted(b for b in boxes if b != BLACK)
        if rest:
            return rest[len(rest) // 2]
    return None


def check_layout(px, size) -> str | None:
    """Why this frame isn't the expected plot, or None if it is."""
    if size != (1024, 1024):
        return f"frame is {size[0]}x{size[1]}, expected 1024x1024"
    for i, c in enumerate(PALETTE):
        got = px[BAR_X0 + 17 + BAR_W * i, BAR_Y]
        if got != c:
            return f"colour bar box {i} is {got}, expected {c}"
    x0, y0, x1, y1 = FRAME
    for x, y in ((x0, (y0 + y1) // 2), (x1, (y0 + y1) // 2), ((x0 + x1) // 2, y0), ((x0 + x1) // 2, y1)):
        if sum(px[x, y]) > 150:
            return f"map border not at ({x},{y})"
    return None


# ── pure: run discovery + aggregation ──────────────────────────────────────
def latest_run(index_html: str) -> str | None:
    """'20261023_12' from the index's first display_<run>.html link (the
    'Latest Forecast Cycle' button comes first)."""
    m = re.search(r'href="\./display_(\d{8}_\d{2})\.html"', index_html)
    return m.group(1) if m else None


FRAME_RE = re.compile(r"^(?P<var>.+)_(?P<dom>d0\d)_(?P<d>\d{8})_(?P<h>\d{2})LST\.png$")


def frames(manifest: dict, var: str, step: int) -> list[tuple[str, datetime]]:
    """[(filename, valid time MYT)] for one variable on DOMAIN, every `step` h."""
    out = []
    for f in manifest.get("files") or []:
        m = FRAME_RE.match(f)
        if m and m["var"] == var and m["dom"] == DOMAIN:
            t = datetime.strptime(m["d"] + m["h"], "%Y%m%d%H").replace(tzinfo=MYT)
            out.append((f, t))
    out.sort(key=lambda x: x[1])
    return [x for x in out if x[1].hour % step == 0]


def daily(samples: dict[str, list[tuple[datetime, float]]], step: int) -> list[dict]:
    """{pollutant: [(t, conc)]} for one station -> per-MYT-day forecast."""
    by_day: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for pol, rows in samples.items():
        for t, c in rows:
            by_day[t.date().isoformat()][pol].append(c)
    days = []
    for d in sorted(by_day):
        concs = by_day[d]
        hours = max(len(v) for v in concs.values()) * step
        if hours < MIN_DAY_HOURS:
            continue
        day = {"date": d, "hours": hours}
        sub = {}
        for pol, spec in POLLUTANTS.items():
            v = concs.get(pol)
            if not v:
                continue
            c = statistics.fmean(v) if spec["agg"] == "mean" else max(v)
            day[pol.lower().replace(".", "")] = round(c, 1)
            sub[pol] = to_api(c, spec["api"])
        if not sub:
            continue
        worst = max(sub, key=sub.get)
        day.update(api=sub[worst], pollutant=worst, category=category(sub[worst]))
        days.append(day)
    return days


# ── network ─────────────────────────────────────────────────────────────────
def _get(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def load_stations() -> list[dict]:
    """APIMS stations (id, location, state, lat, lon): from the fresh
    data/apims.json when the run has one, else straight from APIMS."""
    try:
        with open(APIMS, encoding="utf-8") as f:
            st = json.load(f).get("stations") or []
    except (OSError, ValueError):
        st = []
    if not st:
        sys.path.insert(0, HERE)
        import fetch_apims  # noqa: E402 — only needed without a local snapshot
        st = list(fetch_apims.parse_map(fetch_apims._get_json(fetch_apims.MAP_URL)).values())
    return [s for s in st if s.get("lat") is not None and s.get("lon") is not None]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=2, help="hours between sampled frames (1-6)")
    args = ap.parse_args()
    step = max(1, min(6, args.step))
    try:
        from PIL import Image
    except ImportError:
        print("[aq_forecast] needs Pillow (pip install Pillow) — refusing to overwrite.", file=sys.stderr)
        return 1

    try:
        run = latest_run(_get(SITE + "/").decode("utf-8", "replace"))
        if not run:
            raise ValueError("no forecast run linked from the index — the site may have changed")
        manifest = json.loads(_get(f"{SITE}/{run}/manifest.json"))
        stations = load_stations()
    except Exception as e:  # noqa: BLE001 — any failure means "keep the old feed"
        print(f"[aq_forecast] failed: {e}", file=sys.stderr)
        return 1
    if not stations:
        print("[aq_forecast] no APIMS stations to place — refusing to overwrite.", file=sys.stderr)
        return 1

    where = {s["id"]: lonlat_to_px(float(s["lon"]), float(s["lat"])) for s in stations}
    jobs = [(pol, f, t) for pol, spec in POLLUTANTS.items() for f, t in frames(manifest, spec["var"], step)]
    if not jobs:
        print(f"[aq_forecast] run {run} lists no {DOMAIN} PM/O3 maps — refusing to overwrite.", file=sys.stderr)
        return 1

    def one(job):
        pol, fname, t = job
        try:
            im = Image.open(io.BytesIO(_get(f"{SITE}/{run}/{fname}"))).convert("RGB")
        except Exception as e:  # noqa: BLE001
            return job, None, f"{fname}: {e}"
        px = im.load()
        bad = check_layout(px, im.size)
        if bad:
            return job, None, f"{fname}: {bad}"
        edges = box_edges(POLLUTANTS[pol]["labels"])
        out = {}
        for sid, xy in where.items():
            b = sample_box(px, *xy) if xy else None
            if b is not None:
                out[sid] = box_value(b, edges)
        return job, out, None

    samples: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    errors, layout_errors = [], 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (pol, _f, t), got, err in pool.map(one, jobs):
            if err:
                errors.append(err)
                layout_errors += "colour bar" in err or "border" in err or "frame is" in err
                continue
            for sid, c in got.items():
                samples[sid][pol].append((t, c))
    for e in errors[:5]:
        print(f"[aq_forecast] {e}", file=sys.stderr)
    if layout_errors:
        print(f"[aq_forecast] {layout_errors} map(s) not in the expected layout — the plots may have changed; refusing to overwrite.", file=sys.stderr)
        return 1
    if len(errors) > len(jobs) // 4:
        print(f"[aq_forecast] {len(errors)}/{len(jobs)} maps failed to download — refusing to overwrite.", file=sys.stderr)
        return 1

    out_st = {}
    for s in stations:
        days = daily(samples.get(s["id"], {}), step)
        if days:
            out_st[s["id"]] = {"location": s.get("location"), "state": s.get("state"), "days": days}
    if not out_st:
        print("[aq_forecast] no station could be read off the maps — refusing to overwrite.", file=sys.stderr)
        return 1

    init = datetime.strptime(run, "%Y%m%d_%H").replace(tzinfo=timezone.utc)
    valid = sorted(t for _p, _f, t in jobs)
    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run": run,
            "run_init": init.isoformat(timespec="seconds"),
            "valid_from": valid[0].isoformat(timespec="seconds"),
            "valid_to": valid[-1].isoformat(timespec="seconds"),
            "source": "MET Malaysia — MyCMAQ (WRF-CMAQ) forecast",
            "source_url": SITE,
            "domain": f"{DOMAIN} (9 km, Malaysia)",
            "step_hours": step,
            "maps_read": len(jobs) - len(errors),
            "station_count": len(out_st),
            "method": "Read off the published maps' colour bands at each APIMS station; "
                      "PM2.5/PM10 on their daily mean, O3 on its daily peak, converted with "
                      "the map legend's API breakpoints. Values are band mid-points.",
        },
        "stations": out_st,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    n_days = max(len(v["days"]) for v in out_st.values())
    print(f"[aq_forecast] run {run}: {len(out_st)}/{len(stations)} stations, {n_days} days, "
          f"{len(jobs) - len(errors)} maps -> data/aq_forecast.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
