#!/bin/sh
# Refresh the data files the API reads (companies, deals, movers, US news/earnings/...)
# into /app/backend/data, which docker-compose mounts as a named volume so screener.db
# survives rebuilds. A named volume is only filled from the image the first time, so
# without this the running container never sees newer data files.
#
# Source: /app/backend/data-live when docker-compose bind-mounts the repo's data folder
# there (then a plain `git pull` on the host + this script is a complete, no-downtime
# data deploy -- what the frequent jobs like the US news feed use), otherwise the copy
# baked into the image at data-dist/. screener.db* is never touched. Files that have
# not changed are left alone so the API's mtime-keyed caches stay warm.
set -e
cd /app/backend
SRC=data-dist
[ -d data-live ] && SRC=data-live
[ -d "$SRC" ] || exit 0
# news_channel/ is written by the server itself every five minutes (as well as by the GitHub job), so it
# is merged below rather than copied: copying the repo's older latest.json over the server's threw away
# every story fetched since the job last ran (see app/news_sync.py).
(cd "$SRC" && find . -type f ! -name 'screener.db*' ! -name '.refresh.lock' ! -name '.us_alerts.lock' ! -path './news_channel/*') | while read -r f; do
  cmp -s "$SRC/$f" "data/$f" 2>/dev/null && continue
  mkdir -p "data/$(dirname "$f")"
  cp -f "$SRC/$f" "data/$f"
done
[ -d "$SRC/news_channel" ] && python -m app.news_sync "$SRC/news_channel" || true
