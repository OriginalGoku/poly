#!/usr/bin/env bash
# Launch UEFA qualifier collectors as background processes
set -e

cd "$(dirname "$0")/.."
source .venv/bin/activate

echo "Starting UEFA collectors..."

python -m collector --config configs/match_uef-tur-rom-2026-03-26.json &
PID1=$!
echo "Türkiye vs Romania: PID $PID1"

python -m collector --config configs/match_uef-cze-ire-2026-03-26.json &
PID2=$!
echo "Czechia vs Ireland: PID $PID2"

python -m collector --config configs/match_uef-ukr-swe-2026-03-26.json &
PID3=$!
echo "Ukraine vs Sweden: PID $PID3"

echo ""
echo "All 3 collectors running. PIDs: $PID1, $PID2, $PID3"
echo "Logs: logs/collector_uef-*.log"
echo "Press Ctrl+C to stop all."

wait
