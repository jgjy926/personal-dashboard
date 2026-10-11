#!/usr/bin/env bash
# Stage the dashboard for GitHub Pages and stamp its code version. Used by
# every deploy in refresh.yml.
#   tools/stage_site.sh <dest_dir> [live_site_base_url]
#
# Publishes the dashboard, not the workspace: the engine checkout (and its 66MB
# database + model pickle) used to ride along to Pages on every run.
#
# data/apims.json is the Air Quality tab's FALLBACK (the tab reads APIMS live
# from worker/apims-proxy) and is deliberately not committed. When this run's
# fetch failed, carry the copy that's already live forward rather than
# deploying without one. Its readings_as_of still says how old it is.
set -eu
dest="$1"; base="${2:-}"
rsync -a --exclude .git --exclude macro-engine ./ "$dest/"
if [ ! -s "$dest/data/apims.json" ] && [ -n "$base" ]; then
  curl -fsS -m 20 "${base%/}/data/apims.json" -o "$dest/data/apims.json" \
    && echo "apims.json: carried forward the live copy" \
    || { rm -f "$dest/data/apims.json"; echo "::warning::no apims.json to publish"; }
fi
python3 tools/stamp_version.py "$dest"
