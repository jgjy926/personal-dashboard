/**
 * Offline tests for the promo-sync Worker — no deploy, no GitHub, no token.
 * `global.fetch` is stubbed with a tiny in-memory repo, so these exercise the
 * parts that are actually easy to get wrong: auth, CORS, the merge rule, the
 * UTF-8 base64 round-trip, and the stale-tab guarantee.
 *
 *   node worker/promo-sync/test.mjs
 */
import worker from "./src/index.js";

const ENV = {
  GITHUB_TOKEN: "ghp_fake",
  SYNC_KEY: "correct-horse-battery-staple",
  GITHUB_REPO: "owner/repo",
  GITHUB_BRANCH: "main",
  FILE_PATH: "data/promotions.json",
  ALLOWED_ORIGINS: "https://example.github.io",
};

// The "repo": what GitHub would return, and what it received on write.
let repoFile;
let lastWrite;

function resetRepo() {
  lastWrite = null;
  repoFile = {
    meta: { today: "2026-09-06", note: "Live scrape." },
    promotions: [
      { id: "aaa", title: "Already summarised — em dash — and “curly quotes”", period: "1 Jan 2026", tnc_summary: "existing summary" },
      { id: "bbb", title: "Needs a summary", period: "", tnc_summary: "" },
    ],
  };
}

function b64(text) {
  return Buffer.from(text, "utf-8").toString("base64");
}

global.fetch = async (url, init = {}) => {
  const method = init.method || "GET";
  if (method === "GET") {
    return new Response(JSON.stringify({ sha: "sha-123", content: b64(JSON.stringify(repoFile, null, 2)) }), { status: 200 });
  }
  if (method === "PUT") {
    const body = JSON.parse(init.body);
    lastWrite = { ...body, decoded: JSON.parse(Buffer.from(body.content, "base64").toString("utf-8")) };
    return new Response(JSON.stringify({ commit: { html_url: "https://github.com/owner/repo/commit/abc" } }), { status: 200 });
  }
  throw new Error("unexpected " + method);
};

function req(body, { key = ENV.SYNC_KEY, origin = "https://example.github.io", method = "POST", path = "/publish" } = {}) {
  return new Request("https://promo-sync.workers.dev" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-Sync-Key": key, Origin: origin },
    body: method === "POST" ? JSON.stringify(body) : undefined,
  });
}

let failures = 0;
function check(name, cond, detail) {
  if (cond) { console.log(`  ok   ${name}`); }
  else { failures++; console.log(`  FAIL ${name}${detail ? " — " + JSON.stringify(detail) : ""}`); }
}

console.log("promo-sync worker");

// 1 — a wrong key writes nothing.
resetRepo();
let r = await worker.fetch(req({ summaries: { bbb: { tnc_summary: "x" } } }, { key: "guess" }), ENV);
check("rejects a bad sync key with 401", r.status === 401);
check("...and does not touch the repo", lastWrite === null);

// 2 — the happy path.
resetRepo();
r = await worker.fetch(req({ summaries: { bbb: { period: "8 Sep - 27 Dec 2026", tnc_summary: "RM50 off, min spend RM500." } } }), ENV);
let out = await r.json();
check("commits a new summary", r.status === 200 && out.committed === true, out);
check("reports which ids it applied", JSON.stringify(out.applied) === '["bbb"]', out.applied);
check("writes the summary into the file", lastWrite?.decoded.promotions[1].tnc_summary === "RM50 off, min spend RM500.");
check("writes the period too", lastWrite?.decoded.promotions[1].period === "8 Sep - 27 Dec 2026");
check("commits to the configured branch", lastWrite?.branch === "main");
check("passes the sha back (no blind overwrite)", lastWrite?.sha === "sha-123");
check("returns the commit url", out.commit_url?.includes("/commit/"));

// 3 — non-ASCII survives the base64 round-trip. Getting this wrong mangles
//     every em dash and curly quote in the feed, silently.
check("preserves em dashes and curly quotes", lastWrite?.decoded.promotions[0].title === repoFile.promotions[0].title,
  lastWrite?.decoded.promotions[0].title);

// 4 — an existing summary is never clobbered by accident.
resetRepo();
r = await worker.fetch(req({ summaries: { aaa: { tnc_summary: "overwrite attempt" } } }), ENV);
out = await r.json();
check("skips promos that already have a summary", out.committed === false && out.skipped.includes("aaa"), out);
check("...writing nothing at all", lastWrite === null);

// 5 — ...unless you ask for it.
resetRepo();
r = await worker.fetch(req({ summaries: { aaa: { tnc_summary: "deliberate rewrite" } }, force: true }), ENV);
out = await r.json();
check("force:true does overwrite", out.committed === true && lastWrite?.decoded.promotions[0].tnc_summary === "deliberate rewrite");

// 6 — the stale-tab guarantee: a promo the page never saw must survive.
resetRepo();
repoFile.promotions.push({ id: "ccc", title: "Added by the daily scrape after the tab loaded", period: "", tnc_summary: "" });
r = await worker.fetch(req({ summaries: { bbb: { tnc_summary: "from an old tab" } } }), ENV);
check("keeps promos added since the page loaded", lastWrite?.decoded.promotions.some(p => p.id === "ccc"),
  lastWrite?.decoded.promotions.map(p => p.id));

// 7 — unknown ids are reported, not fatal.
resetRepo();
r = await worker.fetch(req({ summaries: { nope: { tnc_summary: "orphan" }, bbb: { tnc_summary: "real" } } }), ENV);
out = await r.json();
check("reports unknown ids but still commits the good ones", out.committed === true && out.unknown.includes("nope") && out.applied.includes("bbb"), out);

// 8 — garbage in.
resetRepo();
r = await worker.fetch(req({ summaries: [] }), ENV);
check("rejects a non-object summaries payload", r.status === 400);
r = await worker.fetch(req({ summaries: {} }), ENV);
check("rejects an empty payload", r.status === 400);
r = await worker.fetch(req({ summaries: { bbb: { tnc_summary: "   " } } }), ENV);
out = await r.json();
check("treats a blank summary as nothing to do", out.committed === false && lastWrite === null);

// 9 — CORS.
resetRepo();
r = await worker.fetch(req(null, { method: "OPTIONS" }), ENV);
check("answers the preflight", r.status === 204 && r.headers.get("Access-Control-Allow-Origin") === "https://example.github.io");
check("preflight allows the sync-key header", (r.headers.get("Access-Control-Allow-Headers") || "").includes("X-Sync-Key"));
r = await worker.fetch(req({ summaries: { bbb: { tnc_summary: "x" } } }, { origin: "https://evil.example" }), ENV);
check("refuses CORS to an unlisted origin", r.headers.get("Access-Control-Allow-Origin") === "null");

// 10 — misconfiguration is reported, not silently swallowed.
r = await worker.fetch(req({ summaries: { bbb: { tnc_summary: "x" } } }), { ...ENV, GITHUB_TOKEN: "" });
check("says so when the Worker is unconfigured", r.status === 500);

// 11 — end_date: only YYYY-MM-DD is accepted, and it never outlives its period.
resetRepo();
await worker.fetch(req({ summaries: { bbb: { tnc_summary: "s", period: "8 Sep - 27 Dec 2026", end_date: "2026-12-27" } } }), ENV);
check("writes a valid end_date", lastWrite?.decoded.promotions[1].end_date === "2026-12-27");
resetRepo();
await worker.fetch(req({ summaries: { bbb: { tnc_summary: "s", end_date: "27 Dec 2026" } } }), ENV);
check("ignores an end_date that isn't YYYY-MM-DD", lastWrite?.decoded.promotions[1].end_date === undefined,
  lastWrite?.decoded.promotions[1]);
resetRepo();
repoFile.promotions[0].end_date = "2026-01-31";
await worker.fetch(req({ summaries: { aaa: { tnc_summary: "rewrite", period: "1 - 28 Feb 2026" } }, force: true }), ENV);
check("a changed period without an end_date clears the stale one", lastWrite?.decoded.promotions[0].end_date === "");
resetRepo();
repoFile.promotions[0].end_date = "2026-01-31";
await worker.fetch(req({ summaries: { aaa: { tnc_summary: "rewrite", period: "1 Jan 2026" } }, force: true }), ENV);
check("the same period keeps its end_date", lastWrite?.decoded.promotions[0].end_date === "2026-01-31");

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
