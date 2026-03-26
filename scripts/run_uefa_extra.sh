#!/usr/bin/env bash
# Launch additional FIFA qualifier collectors
set -e

cd "$(dirname "$0")/.."
source .venv/bin/activate

echo "Starting additional FIFA collectors..."

python -m collector --config configs/match_fif-geo-isr-2026-03-26.json &
PID1=$!
echo "Georgia vs Israel: PID $PID1"

python -m collector --config configs/match_fif-cyp-bel1-2026-03-26.json &
PID2=$!
echo "Cyprus vs Belarus: PID $PID2"

python -m collector --config configs/match_fif-bra-fra-2026-03-26.json &
PID3=$!
echo "Brazil vs France: PID $PID3"

echo ""
echo "All 3 collectors running. PIDs: $PID1, $PID2, $PID3"
echo "Press Ctrl+C to stop all."

wait
