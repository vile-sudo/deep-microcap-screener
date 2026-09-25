#!/bin/sh
# docker-compose.yml mounts a named volume over /app/backend/data to keep the SQLite
# database across rebuilds. A named volume is only filled from the image the FIRST time it
# is created, so every later deploy's refreshed data files (companies, deals, movers,
# US announcements/insiders, ...) stayed hidden behind the old copies in the volume.
# Refresh them from the copy baked into the image at data-dist/ on every start. The
# database itself (screener.db*) is the one thing never touched.
set -e
cd /app/backend
if [ -d data-dist ]; then
  (cd data-dist && find . -type f ! -name 'screener.db*' ! -name '.refresh.lock') | while read -r f; do
    mkdir -p "data/$(dirname "$f")"
    cp -f "data-dist/$f" "data/$f"
  done
fi
exec "$@"
