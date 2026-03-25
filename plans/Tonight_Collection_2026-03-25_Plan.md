# Tonight's Data Collection Plan (2026-03-25)

> First real collection night with WS-only code, sharded connections, log reduction, and delayed game-state polling. 14 collectors (12 NBA + 2 NHL) on Raspberry Pi.

---

## Problem Statement

Tonight is the first production data collection after completing Phase 2 (WS architecture) and passing a 5-minute local smoke test. The system needs to run 14 parallel collectors on a Raspberry Pi for 5-7 hours, capturing order books, trades, price signals, and game-state events across 12 NBA and 2 NHL games. This validates the full pipeline at scale before committing to nightly collection runs.

## Design Decisions

### D1: GAP_THRESHOLD raised from 5s to 65s

**Decision:** Change `GAP_THRESHOLD` in `collector/ws_client.py` from `5.0` to `65.0` seconds before deploying.

**Rationale:** Low-activity prop shards (≤10 tokens) hit the 60s idle timeout and force reconnect. With a 5s threshold, every reconnect that takes >5s logs a data_gap — inflating gap counts with noise. 65s = idle timeout (60s) + 5s buffer, filtering out expected idle-reconnect pattern while catching real outages.

**Trade-off:** Keep 65s as the permanent default going forward — true WS outages take much longer than 65s to recover. No revert needed.

### D2: Staggered collector startup (5s intervals)

**Decision:** Add `sleep 5` between each collector launch in the for loop.

**Rationale:** All 14 collectors fetch market metadata sequentially at startup (7-42 HTTP requests each to the CLOB API). Launching simultaneously creates a thundering herd of ~504 concurrent requests. 5s stagger means all 14 are running within 70 seconds — trivial vs. game duration.

### D3: NBA + NHL only (no tennis/cricket/esports)

**Decision:** Collect only the 14 games with game-state clients tonight.

**Rationale:** NBA (nba_cdn) and NHL (nhl_api) are the priority — they produce match_events needed for the overreaction hypothesis. Tennis/cricket/esports are control data (order book only), not needed for first validation night.

## Implementation Plan

### Step 1: Pre-deployment code change

Change `GAP_THRESHOLD` in `collector/ws_client.py` line 24:
```python
# Before:
GAP_THRESHOLD = 5.0
# After:
GAP_THRESHOLD = 65.0
```

Commit and push to main before deploying to Pi.

### Step 2: Deploy to Raspberry Pi

```bash
cd ~/vs_code/poly_market_v2    # adjust to actual repo path
git pull origin main
source .venv/bin/activate
# If venv doesn't exist: uv venv && source .venv/bin/activate && uv pip install -r requirements.txt
```

### Step 3: Launch collectors (staggered)

```bash
mkdir -p logs data

for f in configs/match_nba-*2026-03-25*.json configs/match_nhl-*2026-03-25*.json; do
  match_id=$(basename "$f" .json | sed 's/match_//')
  echo "Starting $match_id..."
  python -m collector --config "$f" --db "data/${match_id}.db" > "logs/${match_id}_stdout.log" 2>&1 &
  echo "  PID=$! → data/${match_id}.db"
  sleep 5
done

echo ""
echo "$(jobs -r | wc -l) collectors running"
```

**Do NOT use `--log-level DEBUG`** — default INFO is correct (logs stay <50 KB).

Expected resource usage:
- 12 NBA × ~4 shards = ~48 WS connections
- 2 NHL × 1 shard = ~2 WS connections
- Total: ~50 WS connections + 14 game-state pollers

### Step 4: Monitor

```bash
# All collectors should show PIDs
jobs -l

# Spot-check a log for recent status lines
tail -3 logs/nba-okc-bos-2026-03-25_stdout.log
```

Expected status line pattern:
```
Status: NNN snapshots, NN trades, NNN signals, N events, NNNN WS msgs (N shards)
```

If a collector dies, check its log and restart:
```bash
match_id="<match-id>"
python -m collector --config "configs/match_${match_id}.json" --db "data/${match_id}.db" > "logs/${match_id}_stdout.log" 2>&1 &
```

### Step 5: Let collectors run through the games

- NBA games: ~7 PM ET to ~midnight ET
- NHL games: ~7 PM ET to ~10 PM ET
- Collectors do **not** auto-stop — leave running until the last game ends
- To stop all: `kill $(jobs -p)`
- Use `kill` (SIGTERM), **never** `kill -9`

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
grep "game state\|Game state\|backing off\|normal polling\|match_event" logs/nba-okc-bos-2026-03-25_stdout.log | head -10
grep "game state\|Game state\|backing off\|normal polling\|match_event" logs/nhl-nyr-tor-2026-03-25_stdout.log | head -10
```

**6e. Data gaps and match events summary:**
```python
python3 -c "
import sqlite3, os
for db in sorted([f'data/{f}' for f in os.listdir('data') if '2026-03-25' in f and f.endswith('.db')]):
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
```python
python3 -c "
import sqlite3, os
for db in sorted([f'data/{f}' for f in os.listdir('data') if '2026-03-25' in f and f.endswith('.db')]):
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
```python
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
```python
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
| WS data | snapshots, trades, signals > 0 in all 14 DBs |
| Sharding | ≤ 25 tokens per shard (NBA) |
| Game state (NHL) | match_events > 0 in both NHL DBs |
| Game state (NBA) | match_events > 0 in at least 10 of 12 NBA DBs |
| Data gaps | See interpretation guide below |

### Data gaps interpretation guide

**GAP_THRESHOLD is 65s for this run.** Only disconnects longer than 65 seconds are recorded. Despite this, some gaps may still appear. Classify them by shard type:

| Shard type | During live game | Pre/post-game | Action |
|---|---|---|---|
| **core** (moneyline, spread, O/U) | **BUG — investigate** | Acceptable if brief | Check log for errors |
| **prop_N** (player props) | Acceptable if < 20 per game | Expected noise | Ignore |

**How to distinguish:** The `reason` field in `data_gaps` includes the shard name (e.g., `"WS [prop_3] disconnected 72.1s"`). Use the 6f verification command to classify.

**Summary rule:** 0 core-shard gaps during live play = PASS. Any core-shard gap during live play = investigate. Prop-shard gaps = ignore unless excessive (>20 per game).

### Known non-issues (do NOT treat as failures)

- **`verify_collection.py` reports `avg polling interval > 5000ms`** — expected for WS data. This metric measures order book snapshot interval, not WS message rate.
- **In-memory counters slightly exceed DB row counts at shutdown** — SIGTERM cancels tasks before the queue fully drains. Small discrepancy (< 5%) is normal.
- **Game state shows "backing off" at startup** — correct pre-game behavior. The poller waits for the game to actually start.
- **`scheduled_start` is a stale discovery timestamp** — the poller may skip WAITING and go directly to BACKOFF. This is expected.
- **`match_events = 0` for a game that hasn't started yet** — only a bug if the game has ended and events are still 0.

### Failure conditions (report immediately)

- Any collector crashes and does not recover
- `match_events = 0` for an NBA or NHL game **after the game has ended**
- Log files > 5 MB (logger suppression broken)
- Any shard with > 25 tokens
- Core-shard data gaps during active live play

## Notes

- **Shard headroom:** NBA prop shards sit at exactly 25 tokens. If Polymarket added markets between discovery and collection, token counts may exceed 25. If any collector fails at startup with a config error, re-run `python scripts/discover_markets.py` to regenerate configs.
- **Queue drain at shutdown:** Deferred improvement — add a drain step after task cancellation to close counter/DB discrepancy. Not blocking for tonight.
- **No tennis/cricket/esports tonight** — can add in future collection nights as control data.
