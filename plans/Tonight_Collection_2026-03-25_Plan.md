# Tonight's Data Collection Plan (2026-03-25)

> First real collection night with WS-only code, sharded connections, log reduction, and delayed game-state polling. 21 collectors (12 NBA + 2 NHL + 7 cricket) on Raspberry Pi.

---

## Problem Statement

Tonight is the first production data collection after completing Phase 2 (WS architecture) and passing a 5-minute local smoke test. The system needs to run 21 parallel collectors on a Raspberry Pi for 5-7 hours, capturing order books, trades, price signals, and game-state events across 12 NBA, 2 NHL, and 7 cricket configs (2 cricket games with prop variants). This validates the full pipeline at scale before committing to nightly collection runs.

## Design Decisions

### D1: GAP_THRESHOLD raised from 5s to 65s — [DONE]

`GAP_THRESHOLD` in `collector/ws_client.py` line 24 is already set to `65.0` (commit `c99cd7b`). No code changes needed.

**Rationale:** Low-activity prop shards (<=10 tokens) hit the 60s idle timeout and force reconnect. With a 5s threshold, every reconnect that takes >5s logs a data_gap — inflating gap counts with noise. 65s = idle timeout (60s) + 5s buffer, filtering out expected idle-reconnect pattern while catching real outages.

### D2: Staggered collector startup (5s intervals)

**Decision:** Add `sleep 5` between each collector launch in the for loop.

**Rationale:** All 14 collectors fetch market metadata sequentially at startup (7-42 HTTP requests each to the CLOB API). Launching simultaneously creates a thundering herd of ~504 concurrent requests. 5s stagger means all 14 are running within 70 seconds — trivial vs. game duration.

### D3: NBA + NHL + Cricket (no tennis/esports tonight)

**Decision:** Collect 12 NBA, 2 NHL, and 7 cricket configs (2 cricket games with prop variants).

**Rationale:** NBA (nba_cdn) and NHL (nhl_api) are the priority — they produce match_events needed for the overreaction hypothesis. Cricket added as control data via `polymarket_sports_ws` game state. Tennis skipped — all 35 matches had already started by collection time (22:27 UTC). CS2 and Valorant skipped — API keys not yet configured (see D4).

### D4: CS2 and Valorant deferred — API keys needed

**Decision:** Skip CS2 (1 game, $56k vol) and Valorant (6 games, $62k vol) tonight.

**Rationale:** CS2 requires `PANDASCORE_TOKEN` for game state events; Valorant requires `RIOT_API_KEY`. Without these, collectors capture order books only (no match_events). Deferred to next collection night once API keys are obtained.

**Action for tomorrow:** Set `PANDASCORE_TOKEN` and `RIOT_API_KEY` env vars (or in a `.env` file) before launching collectors.

## Implementation Plan

### Step 1: Deploy to Raspberry Pi

```bash
cd ~/poly_market_v2
git pull origin main
```

If the venv doesn't exist yet, create it. Try `uv` first, fall back to `pip`:
```bash
# Option A (preferred):
uv venv && source .venv/bin/activate && uv pip install -r requirements.txt

# Option B (if uv not installed):
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

If the venv already exists:
```bash
source .venv/bin/activate
```

Dependencies: `httpx`, `aiohttp`, `aiosqlite`, `websockets`, `pytest`, `pytest-asyncio` (see `requirements.txt`).

Verify the deployment is correct:
```bash
python -c "from collector.ws_client import GAP_THRESHOLD; print(f'GAP_THRESHOLD={GAP_THRESHOLD}')"
```
Expected output: `GAP_THRESHOLD=65.0`. If it says `5.0`, `git pull` did not work — investigate.

### Step 2: Launch collectors (staggered, with PID tracking)

**IMPORTANT:** Run this entire block as a single script in one shell session. Do NOT run commands one at a time — background PIDs are only tracked within the same shell session.

```bash
mkdir -p logs data

PIDFILE="logs/pids_2026-03-25.txt"
> "$PIDFILE"

for f in configs/match_nba-*2026-03-25*.json configs/match_nhl-*2026-03-25*.json configs/match_crint-*2026-03-25*.json configs/match_criclcl-*2026-03-25*.json; do
  match_id=$(basename "$f" .json | sed 's/match_//')
  echo "Starting $match_id..."
  python -m collector --config "$f" --db "data/${match_id}.db" > "logs/${match_id}_stdout.log" 2>&1 &
  PID=$!
  echo "$PID" >> "$PIDFILE"
  echo "  PID=$PID -> data/${match_id}.db"
  sleep 5
done

echo ""
echo "$(wc -l < "$PIDFILE") collectors launched"
echo "PIDs: $(tr '\n' ' ' < "$PIDFILE")"
```

**Do NOT use `--log-level DEBUG`** — default INFO is correct (logs stay <50 KB).

**Do NOT use `scripts/run_tonight.sh`** — it contains stale game lists from a previous night.

Expected resource usage:
- 12 NBA x ~4 shards = ~48 WS connections
- 2 NHL x 1 shard = ~2 WS connections
- 7 cricket x 1 shard = ~7 WS connections
- Total: ~57 WS connections + 21 game-state pollers
- Memory: ~840 MB (21 Python processes x ~40 MB each). Requires Pi 4 (4 GB+).

### Step 3: Verify all collectors are running

Wait 30 seconds after the last collector launches, then check:

```bash
echo "=== Running collectors ==="
RUNNING=0
DEAD=0
while read pid; do
  if kill -0 "$pid" 2>/dev/null; then
    echo "  PID $pid: RUNNING"
    RUNNING=$((RUNNING + 1))
  else
    echo "  PID $pid: DEAD"
    DEAD=$((DEAD + 1))
  fi
done < logs/pids_2026-03-25.txt
echo ""
echo "$RUNNING running, $DEAD dead"
```

Spot-check a couple of logs for recent status lines:
```bash
echo "=== NBA spot check ==="
tail -3 logs/nba-okc-bos-2026-03-25_stdout.log 2>/dev/null || echo "Log not found"
echo ""
echo "=== NHL spot check ==="
tail -3 logs/nhl-nyr-tor-2026-03-25_stdout.log 2>/dev/null || echo "Log not found"
echo ""
echo "=== Cricket spot check ==="
tail -3 logs/criclcl-3rd-4th-2026-03-25_stdout.log 2>/dev/null || echo "Log not found"
```

Expected status line pattern in logs:
```
Status: NNN snapshots, NN trades, NNN signals, N events, NNNN WS msgs (N shards)
```

All 21 should be RUNNING. If any are DEAD, go to Step 4.

### Step 4: If a collector dies, restart it

Find which collector died:
```bash
while read pid; do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "DEAD: PID $pid"
  fi
done < logs/pids_2026-03-25.txt
```

To identify which match a dead PID corresponds to, check the logs:
```bash
grep -l "PID" logs/*_stdout.log
# Or check which DB files are smallest (dead collectors write less):
ls -lhS data/*2026-03-25*.db
```

Check the dead collector's log for the error:
```bash
tail -20 logs/<match-id>_stdout.log
```

Restart it and update the PID file:
```bash
match_id="<match-id>"
python -m collector --config "configs/match_${match_id}.json" --db "data/${match_id}.db" > "logs/${match_id}_stdout.log" 2>&1 &
echo $! >> logs/pids_2026-03-25.txt
echo "Restarted $match_id with PID $!"
```

### Step 5: Let collectors run through the games

- NBA games: ~7 PM ET to ~midnight ET
- NHL games: ~7 PM ET to ~10 PM ET
- Collectors do **not** auto-stop — leave running until the last game ends

To stop all collectors when games are done (use SIGTERM, **never** `kill -9`):
```bash
while read pid; do
  kill "$pid" 2>/dev/null && echo "Stopped PID $pid" || echo "PID $pid already stopped"
done < logs/pids_2026-03-25.txt
```

### Step 6: Post-game verification

Run all verification commands and **share the full output**.

**6a. Data quality:**
```bash
python scripts/verify_collection.py data/nba-*2026-03-25*.db data/nhl-*2026-03-25*.db
python scripts/analyze_data_fitness.py data/nba-*2026-03-25*.db data/nhl-*2026-03-25*.db
```

**6b. Log file sizes:**
```bash
ls -lh logs/*2026-03-25*
```

**6c. Shard verification (any NBA game):**
```bash
grep "WS shard" logs/nba-okc-bos-2026-03-25_stdout.log
```

**6d. Game state behavior:**
```bash
grep -E "game state|Game state|backing off|normal polling|match_event" logs/nba-okc-bos-2026-03-25_stdout.log | head -10
grep -E "game state|Game state|backing off|normal polling|match_event" logs/nhl-nyr-tor-2026-03-25_stdout.log | head -10
```

**6e. Data gaps and match events summary:**
```bash
python3 -c "
import sqlite3, os, glob
for db in sorted(glob.glob('data/*2026-03-25*.db')):
    conn = sqlite3.connect(db)
    gaps = conn.execute('SELECT COUNT(*) FROM data_gaps').fetchone()[0]
    events = conn.execute('SELECT COUNT(*) FROM match_events').fetchone()[0]
    signals = conn.execute('SELECT COUNT(*) FROM price_signals').fetchone()[0]
    trades = conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
    print(f'{os.path.basename(db):45s} gaps={gaps:<3} events={events:<5} trades={trades:<5} signals={signals}')
    conn.close()
"
```

**6f. Data gaps detail (with shard classification):**
```bash
python3 -c "
import sqlite3, os, glob
for db in sorted(glob.glob('data/*2026-03-25*.db')):
    conn = sqlite3.connect(db)
    rows = conn.execute('SELECT reason, gap_start, gap_end FROM data_gaps').fetchall()
    if rows:
        print(f'\n{os.path.basename(db)}:')
        for reason, start, end in rows:
            shard = 'CORE' if 'core' in reason else 'PROP'
            print(f'  [{shard}] {reason} ({start} to {end})')
    conn.close()
"
```

**6g. Match events breakdown — NHL:**
```bash
python3 -c "
import sqlite3
for db in ['data/nhl-nyr-tor-2026-03-25.db', 'data/nhl-bos-buf-2026-03-25.db']:
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute('SELECT event_type, COUNT(*) FROM match_events GROUP BY event_type').fetchall()
        print(f'{db}: {dict(rows) if rows else \"NO EVENTS\"}')
        conn.close()
    except Exception as e:
        print(f'{db}: {e}')
"
```

**6h. Match events breakdown — NBA (spot-check 2 games):**
```bash
python3 -c "
import sqlite3
for db in ['data/nba-okc-bos-2026-03-25.db', 'data/nba-lal-ind-2026-03-25.db']:
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute('SELECT event_type, COUNT(*) FROM match_events GROUP BY event_type').fetchall()
        print(f'{db}: {dict(rows) if rows else \"NO EVENTS\"}')
        conn.close()
    except Exception as e:
        print(f'{db}: {e}')
"
```

## Verification

### Pass criteria

| Check | Criterion |
|---|---|
| Log size | < 500 KB per file |
| Log content | 0 aiosqlite/httpcore/websockets DEBUG lines |
| WS data | snapshots, trades, signals > 0 in all 21 DBs |
| Sharding | <= 25 tokens per shard (NBA) |
| Game state (NHL) | match_events > 0 in both NHL DBs |
| Game state (NBA) | match_events > 0 in at least 10 of 12 NBA DBs |
| Game state (cricket) | match_events >= 0 (control data, no hard requirement) |
| Data gaps | See interpretation guide below |

### Data gaps interpretation guide

**GAP_THRESHOLD is 65s for this run.** Only disconnects longer than 65 seconds are recorded. Despite this, some gaps may still appear. Classify them by shard type:

| Shard type | During live game | Pre/post-game | Action |
|---|---|---|---|
| **core** (moneyline, spread, O/U) | **BUG -- investigate** | Acceptable if brief | Check log for errors |
| **prop_N** (player props) | Acceptable if < 20 per game | Expected noise | Ignore |

**How to distinguish:** The `reason` field in `data_gaps` includes the shard name (e.g., `"WS [prop_3] disconnected 72.1s"`). Use the 6f verification command to classify.

**Summary rule:** 0 core-shard gaps during live play = PASS. Any core-shard gap during live play = investigate. Prop-shard gaps = ignore unless excessive (>20 per game).

### Known non-issues (do NOT treat as failures)

- **`verify_collection.py` reports `avg polling interval > 5000ms`** — expected for WS data. This metric measures order book snapshot interval, not WS message rate.
- **In-memory counters slightly exceed DB row counts at shutdown** — SIGTERM cancels tasks before the queue fully drains. Small discrepancy (< 5%) is normal.
- **Game state shows "backing off" at startup** — correct pre-game behavior. The poller waits for the game to actually start.
- **`scheduled_start` is a stale discovery timestamp** — the poller may skip WAITING and go directly to BACKOFF. This is expected.
- **`match_events = 0` for a game that hasn't started yet** — only a bug if the game has ended and events are still 0.
- **Dual log files per collector** — each collector produces both `logs/collector_<match>_<ts>.log` (JSON format) and `logs/<match>_stdout.log` (human-readable). The verification commands use `_stdout.log`. This is expected behavior, not an error.

### Failure conditions (report immediately)

- Any collector crashes and does not recover
- `match_events = 0` for an NBA or NHL game **after the game has ended**
- Log files > 5 MB (logger suppression broken)
- Any shard with > 25 tokens
- Core-shard data gaps during active live play

## MLB Late Addition: Yankees vs. Giants

**Yankees vs. Giants collection started at 9:12 PM ET on 2026-03-25** (mid-game, game started 8:05 PM ET).

Config: `configs/match_mlb-nyy-sf-2026-03-25.json`
DB: `data/mlb-nyy-sf-2026-03-25.db`
Data source: `polymarket_sports_ws`
Markets: 4 (moneyline, NRFI, spread -1.5, O/U 7.5) — 8 tokens, 1 shard

**For tomorrow's analysis:** Collection started ~1h07m into the game. The first ~67 minutes of market data are missing. When correlating price movements against game events, use 9:12 PM ET (02:12:00 UTC on 2026-03-26) as the collection start boundary — do not assume coverage from game start. The NRFI market was already resolved by collection start (0.9995 Yes) so it will show minimal activity.

---

## Notes

- **Do NOT use `scripts/run_tonight.sh`** — it contains stale game lists from a previous night. Use the commands in Step 2 above.
- **Shard headroom:** NBA prop shards sit at exactly 25 tokens. If Polymarket added markets between discovery and collection, token counts may exceed 25. If any collector fails at startup with a config error, re-run `python scripts/discover_markets.py` to regenerate configs.
- **Queue drain at shutdown:** Deferred improvement — add a drain step after task cancellation to close counter/DB discrepancy. Not blocking for tonight.
- **No tennis tonight** — all 35 matches had already started by collection time (22:27 UTC). Will collect tennis on future nights if starting before matches begin.
- **No esports tonight** — CS2 and Valorant need API keys (see D4).
- **Euroleague skipped** — `euroleague-baskonia-zvezda-2026-03-25` was miscategorized with `nba_cdn` data source; NBA CDN will not resolve a Euroleague game. Skip until a correct data source is implemented.
- **Pi requirements:** Python 3.10+, ~840 MB free RAM, stable internet for ~57 concurrent WS connections. Pi 4 (4 GB+) recommended.
