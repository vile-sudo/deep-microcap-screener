#!/bin/sh
# Bring the data volume up to date with the data files shipped in this build, then run
# the server. See sync-data.sh for why this is needed.
set -e
/app/sync-data.sh
exec "$@"
