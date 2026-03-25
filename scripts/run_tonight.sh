#!/usr/bin/env bash
# Launch collectors for tonight's games (2026-03-25)
# Each collector writes to data/<match_id>.db
#
# Usage:
#   bash scripts/run_tonight.sh              # Launch all (NBA + NHL)
#   bash scripts/run_tonight.sh --nba        # NBA only
#   bash scripts/run_tonight.sh --nhl        # NHL only
#   bash scripts/run_tonight.sh --dry-run    # Just list what would launch

set -e
cd "$(dirname "$0")/.."
mkdir -p logs data

DATE="2026-03-25"
PIDFILE="logs/pids_${DATE}.txt"
STAGGER=5  # seconds between launches to avoid thundering herd

# --- Tonight's games by sport ---

NBA_GAMES=(
    "configs/match_nba-atl-det-${DATE}.json"    # ATL @ DET
    "configs/match_nba-bkn-gsw-${DATE}.json"    # BKN @ GSW
    "configs/match_nba-chi-phi-${DATE}.json"    # CHI @ PHI
    "configs/match_nba-dal-den-${DATE}.json"    # DAL @ DEN
    "configs/match_nba-hou-min-${DATE}.json"    # HOU @ MIN
    "configs/match_nba-lal-ind-${DATE}.json"    # LAL @ IND
    "configs/match_nba-mia-cle-${DATE}.json"    # MIA @ CLE
    "configs/match_nba-mil-por-${DATE}.json"    # MIL @ POR
    "configs/match_nba-okc-bos-${DATE}.json"    # OKC @ BOS
    "configs/match_nba-sas-mem-${DATE}.json"    # SAS @ MEM
    "configs/match_nba-tor-lac-${DATE}.json"    # TOR @ LAC
    "configs/match_nba-was-uta-${DATE}.json"    # WAS @ UTA
)

NHL_GAMES=(
    "configs/match_nhl-bos-buf-${DATE}.json"    # BOS @ BUF
    "configs/match_nhl-nyr-tor-${DATE}.json"    # NYR @ TOR
)

# --- Build launch list based on args ---

GAMES=()
case "${1:-}" in
    --nba)     GAMES=("${NBA_GAMES[@]}") ;;
    --nhl)     GAMES=("${NHL_GAMES[@]}") ;;
    --dry-run)
        echo "=== DRY RUN ==="
        echo ""
        echo "NBA (${#NBA_GAMES[@]} games — game state via nba_cdn):"
        printf '  %s\n' "${NBA_GAMES[@]}"
        echo ""
        echo "NHL (${#NHL_GAMES[@]} games — game state via nhl_api):"
        printf '  %s\n' "${NHL_GAMES[@]}"
        echo ""
        TOTAL=$(( ${#NBA_GAMES[@]} + ${#NHL_GAMES[@]} ))
        echo "Total: $TOTAL collectors"
        echo "Stagger: ${STAGGER}s between launches (~$(( TOTAL * STAGGER ))s total startup)"
        exit 0
        ;;
    *)
        GAMES=("${NBA_GAMES[@]}" "${NHL_GAMES[@]}")
        ;;
esac

echo "Launching ${#GAMES[@]} collectors (${STAGGER}s stagger)..."
echo ""

> "$PIDFILE"
PIDS=()
for g in "${GAMES[@]}"; do
    if [ ! -f "$g" ]; then
        echo "  SKIP (not found): $g"
        continue
    fi
    match_id=$(basename "$g" .json | sed 's/match_//')
    db_path="data/${match_id}.db"
    log_path="logs/${match_id}_stdout.log"

    echo "  Starting $match_id..."
    python -m collector --config "$g" --db "$db_path" > "$log_path" 2>&1 &
    PID=$!
    PIDS+=($PID)
    echo "$PID" >> "$PIDFILE"
    echo "    PID=$PID -> $db_path"
    sleep "$STAGGER"
done

echo ""
echo "All ${#PIDS[@]} collectors running."
echo "PIDs saved to: $PIDFILE"
echo "PIDs: ${PIDS[*]}"
echo ""
echo "Monitor:"
echo "  tail -f logs/*${DATE}*_stdout.log"
echo "  # Or check status:"
echo "  while read pid; do kill -0 \"\$pid\" 2>/dev/null && echo \"PID \$pid: RUNNING\" || echo \"PID \$pid: DEAD\"; done < $PIDFILE"
echo ""
echo "Stop all:"
echo "  while read pid; do kill \"\$pid\" 2>/dev/null; done < $PIDFILE"
echo ""
echo "After games finish:"
echo "  python scripts/verify_collection.py data/*${DATE}*.db"
echo "  python scripts/analyze_data_fitness.py data/*${DATE}*.db"
