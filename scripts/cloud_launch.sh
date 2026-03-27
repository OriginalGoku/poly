#!/usr/bin/env bash
# Launch collectors on Oracle Cloud VM — each in its own tmux session
# for safe independent shutdown.
#
# Usage (from local Mac):
#   bash scripts/cloud_launch.sh configs/match_nba-xxx-2026-03-27.json configs/match_nhl-yyy-2026-03-27.json ...
#
# Or run directly on the VM:
#   bash scripts/cloud_launch.sh configs/match_*.json

set -euo pipefail

VENV_PYTHON="$HOME/poly/.venv/bin/python"
POLY_DIR="$HOME/poly"
STAGGER=3  # seconds between launches

if [ $# -eq 0 ]; then
    echo "Usage: $0 <config1.json> [config2.json ...]"
    exit 1
fi

echo "Launching $# collector(s)..."

for config in "$@"; do
    if [ ! -f "$config" ] && [ -f "$POLY_DIR/$config" ]; then
        config="$POLY_DIR/$config"
    fi

    if [ ! -f "$config" ]; then
        echo "  SKIP: $config (not found)"
        continue
    fi

    # Extract match_id for tmux session name
    match_id=$(python3 -c "import json; print(json.load(open('$config'))['match_id'])" 2>/dev/null || basename "$config" .json | sed 's/match_//')
    session_name="col-${match_id}"

    # Kill existing session with same name (if any)
    tmux kill-session -t "$session_name" 2>/dev/null || true

    # Launch in its own tmux session
    tmux new-session -d -s "$session_name" \
        "cd $POLY_DIR && $VENV_PYTHON -m collector --config $config; echo 'Collector exited. Press enter to close.'; read"

    echo "  Started: $session_name (config: $(basename $config))"
    sleep "$STAGGER"
done

echo ""
echo "All launched. Commands:"
echo "  tmux ls                          # list all sessions"
echo "  tmux attach -t col-<match_id>    # attach to a collector"
echo "  tmux kill-session -t col-<match_id>  # kill one collector"
echo "  bash scripts/cloud_kill.sh <match_id>  # kill by match_id"
