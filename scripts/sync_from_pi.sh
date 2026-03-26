#!/usr/bin/env bash
# Sync data and logs from Raspberry Pi via Tailscale
set -euo pipefail

PI_HOST="100.123.238.76"
PI_USER="lordgoku"
LOCAL_DIR="/Users/god/vs_code/poly_market_v2"

echo "$(date '+%Y-%m-%d %H:%M:%S') — Syncing from Pi..."

# Sync SQLite databases (--update skips files already transferred unless Pi version is newer)
rsync -avz --update \
  "${PI_USER}@${PI_HOST}:/home/lordgoku/poly/data/" \
  "${LOCAL_DIR}/data/"

# Sync log files
rsync -avz --update \
  "${PI_USER}@${PI_HOST}:/home/lordgoku/poly/logs/" \
  "${LOCAL_DIR}/logs/"

echo "$(date '+%Y-%m-%d %H:%M:%S') — Sync complete."
