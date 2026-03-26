#!/usr/bin/env bash
# Sync data and logs from Oracle Cloud VM via SSH
set -euo pipefail

CLOUD_HOST="140.238.137.121"
CLOUD_USER="ubuntu"
SSH_KEY="$HOME/.ssh/oracle_poly.key"
LOCAL_DIR="/Users/god/vs_code/poly_market_v2"

echo "$(date '+%Y-%m-%d %H:%M:%S') — Syncing from Oracle Cloud VM..."

# Sync SQLite databases (--update skips files already transferred unless cloud version is newer)
rsync -avz --update \
  -e "ssh -i ${SSH_KEY}" \
  "${CLOUD_USER}@${CLOUD_HOST}:/home/ubuntu/poly/data/" \
  "${LOCAL_DIR}/data/"

# Sync log files
rsync -avz --update \
  -e "ssh -i ${SSH_KEY}" \
  "${CLOUD_USER}@${CLOUD_HOST}:/home/ubuntu/poly/logs/" \
  "${LOCAL_DIR}/logs/"

echo "$(date '+%Y-%m-%d %H:%M:%S') — Sync complete."
