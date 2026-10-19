#!/usr/bin/env bash
# Commit refreshed public feeds and push, used by both jobs in refresh.yml.
#   tools/commit_feeds.sh "<commit message>" <file>...
#
# The push can race a concurrent run/commit on main (rejected: fetch first).
# Retry with a rebase onto whatever landed, a few times, instead of silently
# dropping this run's refreshed data.
set -u
msg="$1"; shift
git config user.name  "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
# status.json changes on every run, so an overlapping run always conflicts on it.
# Merge it per source (.gitattributes) instead of aborting the rebase below,
# which used to drop this run's whole feed commit.
git config merge.status-json.name "per-source merge of data/status.json"
git config merge.status-json.driver "python3 tools/write_status.py --merge-driver %A %B"
git add "$@" 2>/dev/null || true
if git diff --cached --quiet; then
  echo "no changes"
  exit 0
fi
git commit -m "$msg"
for i in 1 2 3 4 5; do
  if git push; then exit 0; fi
  echo "push rejected, retrying with rebase ($i/5)…"
  git fetch origin main
  git rebase origin/main || { git rebase --abort; break; }
  sleep $((i * 3))
done
# Warn, don't fail: the site still deploys from this run's working tree, and
# the next run re-commits the feeds.
echo "::warning::feed commit could not be pushed after retries"
exit 0
