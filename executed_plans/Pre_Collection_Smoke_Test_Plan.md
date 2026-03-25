# Pre-Collection Smoke Test Plan

> Run 2 collectors for 5 minutes on tonight's games to validate WS sharding, log size reduction, and data writes before the first real collection night.

---

## Problem Statement

Tonight (2026-03-25) is the first real collection night with WS-only code, log size reduction (`--log-level` flag, third-party logger suppression), delayed game-state polling (three-state WAITING → BACKOFF → LIVE), and `GameNotStarted` exception handling. A 5-minute smoke test with 2 parallel collectors validates the full pipeline before committing to multi-hour runs.

## Design Decisions

### D1: Two games, different profiles

**Decision:** NBA OKC-BOS (84 tokens, ~4 shards) + NHL NYR-TOR (14 tokens, single shard).

**Rationale:** Covers both extremes — high-token multi-shard path and minimal single-shard path. Different game-state clients (NBA CDN vs NHL API). OKC-BOS is a marquee game with likely pre-game trading activity.

### D2: 5-minute timeout via `timeout 300`

**Decision:** Use shell `timeout 300` to auto-stop collectors.

**Rationale:** Long enough for WS to connect, receive initial book snapshots, and accumulate meaningful trade/signal data. Short enough to not waste time.

**Trade-off:** `timeout` sends SIGTERM, which triggers graceful shutdown. The `run_ws_db_writer` task has no `CancelledError` handling, so a small number of queued-but-unwritten records may be lost. In-memory counters will slightly exceed DB row counts — this is expected, not a bug.

### D3: Adjusted game-state success criteria

**Decision:** Accept either "backing off" or "skipping game state" as valid pre-game behavior.

**Rationale:** Both configs have `external_id: ""`. `lookup_game_id()` hits the live scoreboard — if games aren't listed yet (hours before tip-off), it returns `None` and the game-state client is suppressed. Either outcome is correct. The key criterion is **no 403/404 traceback spam**.

## Implementation Plan

### Step 1: Run 2 collectors in parallel (5 min each)

Spin off 2 background agents, each running:

```bash
# Agent 1 — NBA OKC-BOS (multi-shard)
timeout 300 python -m collector --config configs/match_nba-okc-bos-2026-03-25.json --db data/test-nba-okc-bos.db

# Agent 2 — NHL NYR-TOR (single shard)
timeout 300 python -m collector --config configs/match_nhl-nyr-tor-2026-03-25.json --db data/test-nhl-nyr-tor.db
```

### Step 2: Post-run verification

After both finish (~5 min):

**2a. Log file sizes**
```bash
ls -lh logs/collector_*okc* logs/collector_*nyr*
```
- **Pass:** < 500 KB each
- **Fail:** > 5 MB → third-party logger suppression broken

**2b. Log content spot-check**
```bash
# Should be 0 (suppressed loggers):
grep -c '"logger":"aiosqlite"' logs/collector_*okc*
grep -c '"logger":"websockets"' logs/collector_*okc*
# Should have collector.* INFO lines:
grep '"logger":"collector"' logs/collector_*okc* | head -5
```

**2c. Data quality**
```bash
python scripts/verify_collection.py data/test-nba-okc-bos.db data/test-nhl-nyr-tor.db
```
- Snapshots, trades, price_signals > 0
- Gaps: 0 or minimal (startup only)
- Note: `avg polling interval > 5000ms` flag is expected for pre-game WS data — not a failure

**2d. Shard verification (NBA only)**

Check stderr/log for:
```
WS shard 'core': N tokens
WS shard 'prop_1': N tokens
...
```
Confirm ≤25 tokens per shard.

**2e. Game state poller**

Check stderr/log for one of:
- "Game state polling delayed" / "Game state API not ready, backing off" (game on scoreboard)
- "Could not resolve NBA/NHL game ID — skipping game state" (game not yet listed)
- **NOT:** repeated 403/404 exception tracebacks

### Step 3: Cleanup

```bash
rm data/test-nba-okc-bos.db data/test-nhl-nyr-tor.db
```

## Verification

### Pass criteria (all must hold)

| Check | Criterion |
|---|---|
| Log size | < 500 KB per file |
| Log content | 0 aiosqlite/httpcore/websockets DEBUG lines |
| WS data | snapshots, trades, signals > 0 in both DBs |
| Sharding | ≤25 tokens per shard (NBA) |
| Game state | No 403/404 traceback spam |
| Counter/DB mismatch | Small discrepancy at shutdown is expected |

### Known non-issues (do not treat as failures)

- `verify_collection.py` avg polling interval > 5000ms — expected for pre-game WS
- In-memory counters slightly exceed DB row counts — shutdown flush timing
- Game state client skipped entirely — normal if game not on scoreboard yet
- `scheduled_start` is a stale discovery timestamp — poller skips WAITING, goes to BACKOFF

## Notes

- **Shard headroom:** NBA prop shards sit at exactly 25 tokens. If Polymarket adds markets between discovery and collection, re-run `discover_markets.py` to regenerate configs.
- **Queue drain at shutdown:** Deferred improvement — add a drain step after task cancellation to close counter/DB discrepancy. Not blocking for tonight.
- **Test DB names** (`test-*`) are distinct from production names (`nba-okc-bos-2026-03-25.db`) — no collision risk.
