/**
 * promo-sync — the write endpoint the Card Promos Console was missing.
 *
 * The dashboard is a static site, so the page itself has no way to persist
 * anything: it could merge your AI's summaries in memory but then had to hand
 * you a file to drop into data/ and commit by hand. This Worker closes that
 * loop. The Console POSTs the summaries here with a passphrase; the Worker
 * holds the GitHub token (as a Worker secret, never in the browser), commits
 * data/promotions.json, and GitHub Actions redeploys the site.
 *
 * Design note — the browser sends SUMMARIES, not a whole file. It would be
 * simpler to POST the merged promotions.json the page already built, but the
 * page's copy of the feed is as old as the tab: the daily scrape may have added
 * promos since it loaded, and committing that stale copy would delete them.
 * So the Worker re-reads the live file from GitHub and applies the summaries to
 * THAT. Publishing from a tab you opened yesterday is therefore safe.
 *
 * Bindings (see wrangler.jsonc):
 *   secret GITHUB_TOKEN     fine-grained PAT, contents:write, this repo only
 *   secret SYNC_KEY         the passphrase the Console must present
 *   var    GITHUB_REPO      "owner/name"
 *   var    GITHUB_BRANCH    default "main"
 *   var    FILE_PATH        default "data/promotions.json"
 *   var    ALLOWED_ORIGINS  comma-separated; empty = any origin (the SYNC_KEY
 *                           is still required either way)
 */

const GH = "https://api.github.com";
const MAX_SUMMARY = 2000;

// ── small helpers ──────────────────────────────────────────────────────────
// GitHub hands back base64 of UTF-8 bytes; JSON.parse needs a string. atob/btoa
// are byte-oriented, so both directions go through TextEncoder/TextDecoder or
// the em dashes and curly quotes in the feed come back mangled.
function b64ToText(b64) {
  const bin = atob(b64.replace(/\s/g, ""));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

function textToB64(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  // Chunked: String.fromCharCode(...bytes) overflows the argument limit once
  // the feed grows past a few tens of KB.
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

/** Length-independent comparison, so the key can't be recovered by timing. */
function safeEqual(a, b) {
  const ea = new TextEncoder().encode(a || "");
  const eb = new TextEncoder().encode(b || "");
  let diff = ea.length ^ eb.length;
  for (let i = 0; i < Math.max(ea.length, eb.length); i++) {
    diff |= (ea[i] || 0) ^ (eb[i] || 0);
  }
  return diff === 0;
}

function corsHeaders(request, env) {
  const origin = request.headers.get("Origin") || "";
  const allowed = (env.ALLOWED_ORIGINS || "")
    .split(",").map((s) => s.trim()).filter(Boolean);
  const ok = allowed.length === 0 || allowed.includes(origin);
  return {
    "Access-Control-Allow-Origin": ok ? (origin || "*") : "null",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Sync-Key",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

function json(body, status, extra) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...extra },
  });
}

// ── GitHub contents API ────────────────────────────────────────────────────
function ghHeaders(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    // GitHub rejects requests without one.
    "User-Agent": "promo-sync-worker",
  };
}

async function readFile(env, path, branch) {
  const url = `${GH}/repos/${env.GITHUB_REPO}/contents/${path}?ref=${encodeURIComponent(branch)}`;
  const r = await fetch(url, { headers: ghHeaders(env) });
  if (!r.ok) throw new Error(`GitHub read ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const meta = await r.json();
  return { sha: meta.sha, doc: JSON.parse(b64ToText(meta.content)) };
}

async function writeFile(env, path, branch, doc, sha, message) {
  const url = `${GH}/repos/${env.GITHUB_REPO}/contents/${path}`;
  const r = await fetch(url, {
    method: "PUT",
    headers: { ...ghHeaders(env), "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      // Trailing newline so the commit diff stays clean against the Python
      // writers, which end the file the same way.
      content: textToB64(JSON.stringify(doc, null, 2) + "\n"),
      sha,
      branch,
    }),
  });
  if (!r.ok) throw new Error(`GitHub write ${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

// ── the merge rule (same one as tools/merge_campaign.py) ───────────────────
function applySummaries(doc, summaries, force) {
  const applied = [];
  const skipped = [];
  const unknown = [];
  const byId = new Map((doc.promotions || []).map((p) => [p.id, p]));

  for (const [id, val] of Object.entries(summaries)) {
    const p = byId.get(id);
    if (!p) { unknown.push(id); continue; }
    const summary = String(val?.tnc_summary ?? "").trim();
    if (!summary) { skipped.push(id); continue; }
    if (p.tnc_summary && !force) { skipped.push(id); continue; }
    p.tnc_summary = summary.slice(0, MAX_SUMMARY);
    const period = String(val?.period ?? "").trim();
    if (period) p.period = period.slice(0, 200);
    applied.push(id);
  }
  return { applied, skipped, unknown };
}

// ── handler ────────────────────────────────────────────────────────────────
export default {
  async fetch(request, env) {
    const cors = corsHeaders(request, env);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });

    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, repo: env.GITHUB_REPO, path: env.FILE_PATH || "data/promotions.json" }, 200, cors);
    }
    if (request.method !== "POST" || url.pathname !== "/publish") {
      return json({ error: "POST /publish" }, 404, cors);
    }
    if (!env.GITHUB_TOKEN || !env.SYNC_KEY || !env.GITHUB_REPO) {
      return json({ error: "Worker is not configured (GITHUB_TOKEN / SYNC_KEY / GITHUB_REPO)." }, 500, cors);
    }
    if (!safeEqual(request.headers.get("X-Sync-Key"), env.SYNC_KEY)) {
      return json({ error: "Bad or missing sync key." }, 401, cors);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "Body must be JSON." }, 400, cors);
    }
    const summaries = body?.summaries;
    if (!summaries || typeof summaries !== "object" || Array.isArray(summaries)) {
      return json({ error: 'Expected {"summaries": {id: {period, tnc_summary}}}.' }, 400, cors);
    }
    const ids = Object.keys(summaries);
    if (ids.length === 0) return json({ error: "No summaries in the payload." }, 400, cors);
    if (ids.length > 100) return json({ error: "Too many summaries in one request (max 100)." }, 400, cors);

    const branch = env.GITHUB_BRANCH || "main";
    const path = env.FILE_PATH || "data/promotions.json";

    try {
      const { doc, sha } = await readFile(env, path, branch);
      const result = applySummaries(doc, summaries, body.force === true);

      if (result.applied.length === 0) {
        // Nothing to do is a success, not a failure — re-publishing the same
        // reply twice should be harmless.
        return json({ committed: false, ...result, message: "Nothing to write (already summarised, or unknown ids)." }, 200, cors);
      }

      doc.meta = {
        ...(doc.meta || {}),
        generated_at: new Date().toISOString(),
        note: (doc.meta?.note || "") + " Summaries published from the Card Promos Console.",
      };

      const commit = await writeFile(
        env, path, branch, doc, sha,
        `Card promos: publish ${result.applied.length} AI summary/ies from the Console`
      );
      return json({
        committed: true,
        ...result,
        commit_url: commit?.commit?.html_url || null,
      }, 200, cors);
    } catch (err) {
      return json({ error: String(err.message || err) }, 502, cors);
    }
  },
};
