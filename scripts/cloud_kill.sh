#!/usr/bin/env bash
# Kill specific collectors on Oracle Cloud VM by match_id or kill all.
#
# Usage:
#   bash scripts/cloud_kill.sh nba-nop-det-2026-03-26   # kill one
#   bash scripts/cloud_kill.sh nba-nop-det nhl-min-fla   # kill multiple
#   bash scripts/cloud_kill.sh --finished                 # kill collectors whose games have ended
#   bash scripts/cloud_kill.sh --all                      # kill all collectors

set -euo pipefail

POLY_DIR="$HOME/poly"

if [ $# -eq 0 ]; then
    echo "Usage: $0 <match_id> [match_id ...] | --finished | --all"
    echo ""
    echo "Running collectors:"
    tmux ls 2>/dev/null | grep '^col-' | sed 's/^col-/  /' || echo "  (none)"
    exit 0
fi

if [ "$1" = "--all" ]; then
    echo "Killing all collectors..."
    tmux ls 2>/dev/null | grep '^col-' | cut -d: -f1 | while read session; do
        tmux kill-session -t "$session" 2>/dev/null && echo "  Killed: $session"
    done
    echo "Done."
    exit 0
fi

if [ "$1" = "--finished" ]; then
    echo "Checking for finished games..."
    tmux ls 2>/dev/null | grep '^col-' | cut -d: -f1 | while read session; do
        match_id="${session#col-}"
        # Check logs for game_end event
        log=$(ls -t "$POLY_DIR"/logs/collector_${match_id}*.log 2>/dev/null | head -1)
        if [ -n "$log" ] && grep -q 'game_end' "$log" 2>/dev/null; then
            tmux kill-session -t "$session" 2>/dev/null && echo "  Killed (game ended): $session"
        else
            echo "  Keeping (still active): $session"
        fi
    done
    echo "Done."
    exit 0
fi

# Kill specific match_ids
for match_id in "$@"; do
    session="col-${match_id}"
    if tmux kill-session -t "$session" 2>/dev/null; then
        echo "Killed: $session"
    else
        # Try partial match
        found=$(tmux ls 2>/dev/null | grep "^col-.*${match_id}" | cut -d: -f1 | head -1)
        if [ -n "$found" ] && tmux kill-session -t "$found" 2>/dev/null; then
            echo "Killed: $found"
        else
            echo "Not found: $match_id"
        fi
    fi
done
