// Offline checks for the parse layer: node worker/apims-proxy/test.mjs
// Mirrors tools/test_fetch_apims.py so the Worker and the Python fetcher agree.
import assert from "node:assert/strict";
import { apiValue, category, mytIso, parseMap, parseTable, merge, buildPayload } from "./src/index.js";

for (const v of [null, "", "  ", "NA", "N/A", "n/a", "null", "-", -1, "-99", true, "abc"]) assert.equal(apiValue(v), null, String(v));
assert.equal(apiValue(0), 0); assert.equal(apiValue("151"), 151); assert.equal(apiValue(99.6), 100);
for (const [a, c] of [[50, "Good"], [51, "Moderate"], [200, "Unhealthy"], [300, "Very Unhealthy"], [301, "Hazardous"], [null, "N/A"]]) assert.equal(category(a), c);
assert.equal(mytIso("2026-10-11T12:00:00"), "2026-10-11T12:00:00+08:00");
assert.equal(mytIso("junk"), null);

const MAP = { data: [
  { STATE_ID: 9, STATE_NAME: "Perlis", STATION_STATUS: 1, STATION_ID: "CA01R", STATION_LOCATION: "Kangar, PERLIS", API: 47, PARAM_SYMBOL: "**", RECORD_DT: "2026-10-11T11:00:00" },
  { STATE_ID: 10, STATE_NAME: "Selangor", STATION_STATUS: 1, STATION_ID: "M1", STATION_LOCATION: "JOHAN SETIA, Selangor", API: "NA", PARAM_SYMBOL: "", RECORD_DT: "2026-10-11T11:00:00" },
] };
const TABLE = { api_table_hourly: [
  { STATION_ID: "CA01R", DATETIME: "2026-10-11T12:00:00", API: 52, PARAM_SYMBOL: "*" },
  { STATION_ID: "CA01R", DATETIME: "2026-10-11T11:00:00", API: 47, PARAM_SYMBOL: "**" },
  { STATION_ID: "CA01R", DATETIME: "2026-10-11T10:00:00", API: -1, PARAM_SYMBOL: "**" },
] };
const st = parseMap(MAP);
assert.equal(st.CA01R.pollutant, "PM2.5");
assert.equal(st.M1.location, "Johan Setia");
assert.equal(st.M1.category, "N/A");
assert.deepEqual(parseTable(TABLE).CA01R.map(h => h.api), [null, 47, 52]);
const merged = merge(st, parseTable(TABLE));
const k = merged.find(s => s.id === "CA01R");
assert.deepEqual([k.api, k.pollutant, k.category, k.at], [52, "PM10", "Moderate", "2026-10-11T12:00:00+08:00"]);
const p = buildPayload(merged, []);
assert.equal(p.meta.reporting, 1);
assert.equal(p.meta.readings_as_of, "2026-10-11T12:00:00+08:00");
assert.deepEqual(p.meta.categories, { Moderate: 1, "N/A": 1 });
console.log("apims-proxy: all checks passed");
