/**
 * apims-proxy — Malaysia's Air Pollutant Index, live, with CORS.
 *
 * Why this exists: the Air Quality tab used to rely on an hourly GitHub Actions
 * schedule to refresh data/apims.json, but GitHub treats cron as best-effort
 * and on this repo it runs hours late or not at all (the daily 06:20 UTC job
 * starts anywhere from 11:47 to 14:52). So the page now asks this Worker instead,
 * and falls back to the committed-with-the-site snapshot only if it's down.
 *
 * The browser can't call APIMS itself: its API answers CORS only for DOE's own
 * origin. This Worker fetches the same keyless endpoints the APIMS map uses,
 * reshapes them into EXACTLY the data/apims.json contract (tools/fetch_apims.py
 * is the reference implementation — keep the two in step), and serves that with
 * CORS.
 *
 * Caching: one assembled snapshot is kept in the edge cache. Up to FRESH_S old
 * it's served as-is; after that it's still served immediately while a refresh
 * runs in the background (stale-while-revalidate), so visitors never wait on
 * the 17 upstream calls except on a cold cache. APIMS rate-limits bursts (429),
 * hence the low concurrency and the backoff.
 *
 * Overnight (00:00-06:00 MYT) it rests: no upstream calls at all, the last
 * evening snapshot is served whatever its age, and a cold cache answers 503 so
 * the page falls back to the snapshot deployed with the site.
 *
 *   GET /apims     -> the feed
 *   GET /          -> a one-line health note
 *
 * Bindings (wrangler.jsonc):
 *   var ALLOWED_ORIGINS  comma-separated; "" = any origin (it's public data)
 */

const BASE = "https://eqms.doe.gov.my/api3/publicportalapims";
const PORTAL = "https://eqms.doe.gov.my/APIMS/main";
const FRESH_S = 10 * 60;          // serve without refreshing
// Long enough to bridge the overnight rest: the last refresh is ~23:59 and the
// next one 06:00, so the 23:00 readings must survive ~7h in the cache.
const KEEP_S = 12 * 60 * 60;
// Overnight rest, Malaysia time: no APIMS calls from 00:00 to 05:59. Updates
// run 06:00-23:59 (the 6 AM through 11 PM hours); overnight the last evening
// snapshot is served as-is.
const REST_FROM_H = 0, REST_UNTIL_H = 6;

export function isRestHour(now = new Date()) {
  const h = new Date(now.getTime() + 8 * 3600e3).getUTCHours();
  return h >= REST_FROM_H && h < REST_UNTIL_H;
}
const CACHE_KEY = "https://apims-proxy.internal/feed/v1";

// Pollutant that set the index, per APIMS's own legend: "*" is PM10, "**" PM2.5.
const POLLUTANTS = { "*": "PM10", "**": "PM2.5", a: "SO2", b: "NO2", c: "O3", d: "CO" };
const BANDS = [[50, "Good"], [100, "Moderate"], [200, "Unhealthy"], [300, "Very Unhealthy"]];

// ── parsing (mirrors tools/fetch_apims.py) ─────────────────────────────────
export function apiValue(v) {
  if (v == null || typeof v === "boolean") return null;
  if (typeof v === "string") {
    v = v.trim();
    if (!v || ["NA", "N/A", "NULL", "-"].includes(v.toUpperCase())) return null;
  }
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return null;   // negative = analyser offline
  return Math.round(n);
}

export function category(api) {
  if (api == null) return "N/A";
  for (const [top, name] of BANDS) if (api <= top) return name;
  return "Hazardous";
}

/** Naive Malaysia time "2026-10-11T12:00:00" -> "2026-10-11T12:00:00+08:00". */
export function mytIso(s) {
  if (typeof s !== "string") return null;
  const m = s.trim().match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(:\d{2})?/);
  return m ? `${m[1]}${m[2] || ":00"}+08:00` : null;
}

function location(loc) {
  const name = String(loc || "").split(",")[0].trim();
  return name && name === name.toUpperCase() ? name.toLowerCase().replace(/\b\w/g, c => c.toUpperCase()) : name;
}

const pollutant = (sym, api) => api == null ? null : (POLLUTANTS[String(sym || "").trim()] || null);

export function parseMap(payload) {
  const out = {};
  for (const r of (payload && payload.data) || []) {
    const id = String(r.STATION_ID || "").trim();
    if (!id) continue;
    const api = apiValue(r.API);
    out[id] = {
      id,
      location: location(r.STATION_LOCATION),
      state: String(r.STATE_NAME || "").trim(),
      state_id: r.STATE_ID,
      lat: r.LATITUDE,
      lon: r.LONGITUDE,
      active: (r.STATION_STATUS ?? 1) === 1,
      api,
      pollutant: pollutant(r.PARAM_SYMBOL, api),
      category: category(api),
      at: mytIso(r.RECORD_DT),
      history: [],
    };
  }
  return out;
}

export function parseTable(payload) {
  const hist = {};
  for (const r of (payload && payload.api_table_hourly) || []) {
    const id = String(r.STATION_ID || "").trim();
    const t = mytIso(r.DATETIME);
    if (!id || !t) continue;
    const api = apiValue(r.API);
    (hist[id] = hist[id] || {})[t] = { t, api, pollutant: pollutant(r.PARAM_SYMBOL, api) };
  }
  const out = {};
  for (const [id, h] of Object.entries(hist)) out[id] = Object.keys(h).sort().map(t => h[t]);
  return out;
}

/** Attach history; the newest reading (map or table) is the headline. */
export function merge(stations, histories) {
  for (const [id, hist] of Object.entries(histories)) {
    const st = stations[id];
    if (!st) continue;
    st.history = hist.map(h => ({ t: h.t, api: h.api }));
    const newest = [...hist].reverse().find(h => h.api != null);
    if (newest && (st.at == null || st.api == null || newest.t > st.at)) {
      st.api = newest.api;
      st.pollutant = newest.pollutant;
      st.category = category(newest.api);
      st.at = newest.t;
    }
  }
  return Object.values(stations).sort((a, b) =>
    a.state.localeCompare(b.state) || a.location.localeCompare(b.location));
}

export function buildPayload(stations, failedStates, now = new Date()) {
  const times = stations.filter(s => s.at && s.api != null).map(s => s.at);
  const counts = {};
  for (const s of stations) counts[s.category] = (counts[s.category] || 0) + 1;
  return {
    meta: {
      generated_at: now.toISOString().replace(/\.\d{3}Z$/, "+00:00"),
      readings_as_of: times.length ? times.sort().at(-1) : null,
      source: "Department of Environment Malaysia — APIMS",
      source_url: PORTAL,
      served_by: "apims-proxy",
      station_count: stations.length,
      reporting: times.length,
      categories: counts,
      history_missing_states: failedStates,
      bands: [...BANDS.map(([max, c]) => ({ max, category: c })), { max: null, category: "Hazardous" }],
      pollutants: POLLUTANTS,
    },
    stations,
  };
}

// ── upstream ───────────────────────────────────────────────────────────────
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function getJson(url, tries = 4) {
  for (let i = 0; ; i++) {
    const r = await fetch(url, { headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 (personal-dashboard apims-proxy)" } });
    if (r.ok) return r.json();
    if (r.status !== 429 || i === tries - 1) throw new Error(`HTTP ${r.status} from ${new URL(url).pathname}`);
    const ra = Number(r.headers.get("Retry-After"));
    await sleep(Number.isFinite(ra) && ra > 0 ? Math.min(ra, 10) * 1000 : 1500 * (i + 1));
  }
}

function mytHour(now = new Date()) {
  const t = new Date(now.getTime() + 8 * 3600e3).toISOString();
  return `${t.slice(0, 13)}:00:00`;
}

export async function buildFeed() {
  const stations = parseMap(await getJson(`${BASE}/mapcaqm`));
  if (!Object.keys(stations).length) throw new Error("APIMS returned no stations");
  const hour = mytHour();
  const states = [...new Set(Object.values(stations).map(s => s.state_id).filter(v => v != null))].sort((a, b) => a - b);
  const histories = {}, failed = [];
  // Two at a time: six parallel requests got rate-limited (429) by APIMS.
  const queue = [...states];
  await Promise.all([0, 1].map(async () => {
    while (queue.length) {
      const st = queue.shift();
      try { Object.assign(histories, parseTable(await getJson(`${BASE}/apitablehourly?stateid=${st}&datetime=${hour}`))); }
      catch { failed.push(st); }
    }
  }));
  return buildPayload(merge(stations, histories), failed.sort((a, b) => a - b));
}

// ── HTTP ───────────────────────────────────────────────────────────────────
function corsHeaders(req, env) {
  const allowed = String(env.ALLOWED_ORIGINS || "").split(",").map(s => s.trim()).filter(Boolean);
  const origin = req.headers.get("Origin") || "";
  const allow = !allowed.length ? "*" : allowed.includes(origin) ? origin : allowed[0];
  return { "Access-Control-Allow-Origin": allow, "Access-Control-Allow-Methods": "GET, OPTIONS", Vary: "Origin" };
}

async function refresh(cache) {
  const feed = await buildFeed();
  const body = JSON.stringify(feed);
  await cache.put(CACHE_KEY, new Response(body, {
    headers: { "Content-Type": "application/json", "Cache-Control": `public, max-age=${KEEP_S}`, "X-Built-At": String(Date.now()) },
  }));
  return body;
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const cors = corsHeaders(req, env);
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (req.method !== "GET") return new Response("GET only", { status: 405, headers: cors });
    if (url.pathname === "/") return new Response("apims-proxy: GET /apims", { headers: cors });
    if (url.pathname !== "/apims") return new Response("not found", { status: 404, headers: cors });

    const cache = caches.default;
    const hit = await cache.match(CACHE_KEY);
    let body, state;
    if (hit) {
      const age = (Date.now() - Number(hit.headers.get("X-Built-At") || 0)) / 1000;
      body = await hit.text();
      state = isRestHour() ? "resting" : age < FRESH_S ? "fresh" : "stale";
      if (state === "stale") ctx.waitUntil(refresh(cache).catch(() => {}));
    } else if (isRestHour()) {
      return new Response(JSON.stringify({ error: "resting overnight (00:00-06:00 MYT); no cached copy" }), {
        status: 503, headers: { ...cors, "Content-Type": "application/json", "Retry-After": "3600" },
      });
    } else {
      try { body = await refresh(cache); state = "miss"; }
      catch (e) {
        return new Response(JSON.stringify({ error: String(e.message || e) }), {
          status: 502, headers: { ...cors, "Content-Type": "application/json" },
        });
      }
    }
    return new Response(body, {
      headers: { ...cors, "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store", "X-Cache": state },
    });
  },
};
