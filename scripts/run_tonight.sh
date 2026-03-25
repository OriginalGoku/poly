#!/usr/bin/env bash
# Launch collectors for tonight's games (2026-03-24)
# Each collector writes to data/<match_id>.db
#
# Usage:
#   bash scripts/run_tonight.sh              # Launch all games in background
#   bash scripts/run_tonight.sh --nba        # NBA only
#   bash scripts/run_tonight.sh --nhl        # NHL only
#   bash scripts/run_tonight.sh --esports    # Valorant only
#   bash scripts/run_tonight.sh --tennis     # Tennis only (many matches)
#   bash scripts/run_tonight.sh --dry-run    # Just list what would launch

set -e
cd "$(dirname "$0")/.."
mkdir -p logs data

# --- Tonight's games by sport ---

NBA_GAMES=(
    "configs/match_nba-sac-cha-2026-03-24.json"    # SAC @ CHA, 7:00 PM ET
    "configs/match_nba-nop-nyk-2026-03-24.json"    # NOP @ NYK, 7:30 PM ET
    "configs/match_nba-orl-cle-2026-03-24.json"    # ORL @ CLE, 8:00 PM ET
    "configs/match_nba-den-phx-2026-03-24.json"    # DEN @ PHX, 11:00 PM ET
)

NHL_GAMES=(
    "configs/match_nhl-cbj-phi-2026-03-24.json"    # CBJ @ PHI, 7:00 PM ET
    "configs/match_nhl-col-pit-2026-03-24.json"    # COL @ PIT, 7:00 PM ET
    "configs/match_nhl-ott-det-2026-03-24.json"    # OTT @ DET, 7:00 PM ET
    "configs/match_nhl-wsh-stl-2026-03-24.json"    # WSH @ STL, 8:00 PM ET
)

ESPORTS_GAMES=(
    "configs/match_val-ele-2game-2026-03-24.json"  # Elevate vs 2GAME
    "configs/match_val-ts-intz-2026-03-24.json"    # Team Solid vs INTZ
    "configs/match_val-100t1-eg2-2026-04-11.json"  # 100T vs EG
    "configs/match_val-fur-nrg-2026-04-12.json"    # FURIA vs NRG
    "configs/match_val-c9-lev1-2026-04-12.json"    # C9 vs Leviatan
    "configs/match_val-g21-mibr-2026-04-10.json"   # G2 vs MIBR
    "configs/match_val-nv2-loud-2026-04-11.json"   # Envy vs LOUD
)

# Tennis — many matches, collect all active ones
TENNIS_GAMES=()
for f in configs/match_atp-*-2026-03-24.json configs/match_atp-*-2026-03-25.json configs/match_wta-*-2026-03-24.json configs/match_wta-*-2026-03-25.json; do
    [ -f "$f" ] && TENNIS_GAMES+=("$f")
done

# --- Build launch list based on args ---

GAMES=()
case "${1:-}" in
    --nba)     GAMES=("${NBA_GAMES[@]}") ;;
    --nhl)     GAMES=("${NHL_GAMES[@]}") ;;
    --esports) GAMES=("${ESPORTS_GAMES[@]}") ;;
    --tennis)  GAMES=("${TENNIS_GAMES[@]}") ;;
    --dry-run)
        echo "=== DRY RUN ==="
        echo ""
        echo "NBA (${#NBA_GAMES[@]} games — has game state via nba_cdn):"
        printf '  %s\n' "${NBA_GAMES[@]}"
        echo ""
        echo "NHL (${#NHL_GAMES[@]} games — has game state via nhl_api):"
        printf '  %s\n' "${NHL_GAMES[@]}"
        echo ""
        echo "Esports (${#ESPORTS_GAMES[@]} games — price data only):"
        printf '  %s\n' "${ESPORTS_GAMES[@]}"
        echo ""
        echo "Tennis (${#TENNIS_GAMES[@]} matches — price data only):"
        printf '  %s\n' "${TENNIS_GAMES[@]}"
        echo ""
        TOTAL=$(( ${#NBA_GAMES[@]} + ${#NHL_GAMES[@]} + ${#ESPORTS_GAMES[@]} + ${#TENNIS_GAMES[@]} ))
        echo "Total: $TOTAL collectors"
        exit 0
        ;;
    *)
        GAMES=("${NBA_GAMES[@]}" "${NHL_GAMES[@]}" "${ESPORTS_GAMES[@]}" "${TENNIS_GAMES[@]}")
        ;;
esac

echo "Launching ${#GAMES[@]} collectors..."
echo ""

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
    PIDS+=($!)
    echo "    PID=$! → $db_path"
done

echo ""
echo "All ${#PIDS[@]} collectors running."
echo "PIDs: ${PIDS[*]}"
echo ""
echo "Monitor: tail -f logs/*_stdout.log"
echo "Stop all: kill ${PIDS[*]}"
echo ""
echo "After games finish:"
echo "  python scripts/verify_collection.py data/*.db"
echo "  python scripts/analyze_data_fitness.py data/*.db"
