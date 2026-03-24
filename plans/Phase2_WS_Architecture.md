# Phase 2: WebSocket Architecture

> Replace REST polling with a WS-only data pipeline for order books, trades, and sub-second price signals. Validated by research spike (2026-03-24).

---

## Status: IMPLEMENTATION COMPLETE — NEED FRESH VALIDATION

All code is built and tested (71 tests passing). **Previous validation data was wiped (2026-03-24)** because a data fitness analysis revealed critical issues:

1. **match_events = 0** across all databases — game state clients never ran (configs had empty `external_id`)
2. **REST trade-market mismatch** — Data API returned event-wide trades, not filtered to config markets
3. **Illiquid majority** — 66% of snapshots had >10c spread (player props)

**Fixes applied:**
- Added `lookup_game_id()` to `nba_client.py` — auto-resolves NBA game ID from scoreboard at collector startup (no more empty `external_id`)
- Added `scripts/analyze_data_fitness.py` — comprehensive data quality analyzer that checks liquidity, price dynamics, event coverage, trade quality, and temporal resolution

**Next:** Re-run dual-write validation on fresh NBA games. Game events will now be captured alongside price data.

---

## Background

Phase 1b built a working REST-based collector (40 tests, 6 matches collected). Phase 1c live validation revealed three limitations:

1. **Data API rate limits** (~1 req/s) cause 429 errors polling 88 tokens for trades
2. **3s order book polling** misses sub-second price overshoots
3. **100-trade API cap** with no pagination silently drops trades during high activity

A WebSocket research spike (2026-03-24) connected to both Polymarket channels and answered all blocking questions. See `old_plans/WebSocket_Migration_Plan.md` for full spike results.

### Key Spike Findings

| Question | Answer |
|---|---|
| `book` events | **Full snapshots** (not deltas) — Path A confirmed |
| `last_trade_price` fields | `price`, `size`, `side`, `fee_rate_bps`, `transaction_hash` — **full trade metadata** |
| 88-token subscription | Success, no errors, no sharding needed |
| Message rate (88 tokens) | 7.17 msg/s total: 1,802 price_change, 272 best_bid_ask, 110 book, 11 trades |
| `book` cadence per token | ~2.7s average (similar to REST 3s polling) |
| `best_bid_ask` cadence | 0.91/s — sub-second BBO signals |
| Sports channel | Works but no NBA data observed; no `slug` field (uses `gameId` integer) |
| Auth | None required for either channel |
| Heartbeat | PING/PONG works, no disconnects in 5 min |

---

## Design Decisions

### D1: WS-only for books AND trades

**Decision:** Use WebSocket Market channel for both order book snapshots and trade capture. No REST polling in the default data path.

**Rationale:** The spike proved `last_trade_price` includes `transaction_hash`, `size`, `side`, and `fee_rate_bps` — all fields needed for the existing `trades` table. WS-only eliminates all three REST limitations (rate limits, temporal resolution, trade saturation) in one move.

**Validation gate:** Before deprecating REST trade polling, run dual-write for 2+ matches. If WS captures <98% of REST trades, keep REST as supplement.

### D2: Derive outcome/outcome_index from config

**Decision:** WS `last_trade_price` lacks `outcome` and `outcome_index`. Derive them at ingest time from the config's `token_ids → outcomes` mapping.

**Rationale:** Each market config has `outcomes: ["X", "Y"]` paired with `token_ids: [tok_X, tok_Y]`. The mapping `token_id → (outcome, outcome_index)` is deterministic and available at startup. No schema changes needed.

**Guardrail:** If a WS trade arrives for a token_id not in the config mapping, log an error and insert with `outcome=""` rather than dropping the trade.

### D3: New `price_signals` table for sub-second BBO tracking

**Decision:** Store `best_bid_ask` events in a lightweight `price_signals` table. This provides the sub-second resolution that `book` snapshots (~2.7s) don't deliver.

**Rationale:** The overreaction hypothesis needs to detect price overshoots at sub-second granularity. `best_bid_ask` events arrive at 0.91/s and include `best_bid`, `best_ask`, `spread` — sufficient for BBO-based overshoot detection. Storing these separately from the heavy `order_book_snapshots` table (20 columns) keeps writes cheap (~3,300 rows/hour).

**Future option:** If Phase 3 analysis requires depth context (e.g., liquidity thinning before price spikes), add synthetic 1s snapshots from in-memory books built from `price_change` events. Deferred until needed.

### D4: Keep sport-specific game state clients

**Decision:** NBA CDN and OpenDota remain primary game state sources. The Sports WS channel is not used in Phase 2.

**Rationale:** The spike showed no NBA data on the Sports channel (no live NBA game during test). The channel also lacks a `slug` field for matching to our configs (uses integer `gameId`). NBA CDN provides per-play granularity (every made shot, timeout, quarter end) which the Sports channel likely cannot match.

**Revisit trigger:** If a future spike during a live NBA game shows rich Sports channel data with a clear `gameId → match_id` mapping path, reconsider.

### D5: No REST fallback mode

**Decision:** Drop the `--mode ws|rest|hybrid` complexity. WS is the only data path. REST code stays in the repo but is not wired into the main loop.

**Rationale:** Early stage, single developer. Maintaining two parallel data paths with mode switching and fallback logic adds significant complexity for a scenario (prolonged WS outage) that hasn't been observed. WS reconnection with exponential backoff handles brief disconnects. Data gaps are logged via the existing `data_gaps` table.

**Mitigation:** If WS proves unreliable over 2-3 hour matches, restore REST fallback. The existing `polymarket_client.py` is untouched and can be re-integrated.

### D6: Discard `price_change` events (for now)

**Decision:** Do not persist `price_change` events. They are the highest-volume event type (6/s) but their value overlaps with `book` snapshots (full depth) and `best_bid_ask` (BBO signals).

**Rationale:** `price_change` events describe individual order-level updates (a specific price/size changed on one side). To extract meaningful book state from these requires maintaining an in-memory order book per token — significant complexity. The combination of periodic `book` snapshots (full depth every ~2.7s) + `best_bid_ask` signals (sub-second BBO) covers the analysis needs without this complexity.

**Revisit trigger:** If overshoot analysis requires understanding depth dynamics between book snapshots, implement in-memory books from `price_change` deltas with 1s synthetic snapshot emission.

---

## Schema Changes

### New table: `price_signals` ✅ BUILT

```sql
CREATE TABLE IF NOT EXISTS price_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT,
    server_ts_ms INTEGER,
    local_ts TEXT,
    best_bid REAL,
    best_ask REAL,
    mid_price REAL,
    spread REAL,
    event_type TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_token_ms ON price_signals(token_id, server_ts_ms);
```

### Modified table: `trades` ✅ BUILT

Added `source TEXT DEFAULT 'rest'` column for dual-write validation. Unique constraint changed to `UNIQUE(transaction_hash, token_id, source)` during validation phase to allow both sources to insert independently for comparison.

### Existing tables: no other changes

- `order_book_snapshots` — populated from WS `book` events via `OrderBookSnapshot.from_ws()`
- `trades` — populated from WS `last_trade_price` events via `Trade.from_ws()` + REST during validation
- `match_events` — populated from sport-specific clients (unchanged)
- All other tables unchanged

---

## Data Flow

```
WS Market Channel
  │
  ├── book (full snapshot, ~0.37/s total)
  │     └──→ OrderBookSnapshot.from_ws() ──→ order_book_snapshots table
  │
  ├── last_trade_price (~0.04/s total)
  │     └──→ Trade.from_ws() ──→ trades table (source='ws', outcome from config mapping)
  │
  ├── best_bid_ask (~0.91/s total)
  │     └──→ PriceSignal.from_ws() ──→ price_signals table
  │
  ├── price_change (~6/s total)
  │     └──→ discarded (logged at DEBUG level)
  │
  ├── new_market (~0.14/s total)
  │     └──→ discarded (not relevant to our match)
  │
  └── PING/PONG heartbeat (every 10s)

NBA CDN / OpenDota (polling, unchanged)
  └──→ match_events table

REST trades (--validate mode only, source='rest')
  └──→ trades table (deduped by UNIQUE(transaction_hash, token_id, source))
```

---

## What Was Built

### Step 1: WS Market Client ✅ COMPLETE

**File:** `collector/ws_client.py`

`WebSocketMarketClient` class that:

1. Connects to `wss://ws-subscriptions-clob.polymarket.com/ws/market`
2. Sends subscription message with all token_ids
3. Parses the initial book snapshot array (JSON array of all subscribed books)
4. Dispatches subsequent messages by `event_type`:
   - `book` → `OrderBookSnapshot.from_ws()` → buffer
   - `last_trade_price` → `Trade.from_ws()` → buffer
   - `best_bid_ask` → `PriceSignal.from_ws()` → buffer
   - others → log and discard
5. Sends `PING` every 10s, expects `PONG`
6. On disconnect: exponential backoff (1s, 2s, 4s, 8s, 16s, max 30s), re-subscribe on reconnect
7. Logs data gaps to `data_gaps` table when disconnected >5s

**Key implementation details:**
- `token_to_outcome: dict[str, tuple[str, int]]` built from config at startup
- Write buffering: `WriteBatch` dataclass, flush to `asyncio.Queue` every 50 rows or 5s
- `asyncio.Queue` between message handler and DB writer decouples parse speed from write speed
- `get_batch()` (blocking) and `get_batch_nowait()` (non-blocking) for consumer flexibility
- Counters: `snapshot_count`, `trade_count`, `signal_count`, `message_count`

### Step 2: Model Extensions ✅ COMPLETE

**File:** `collector/models.py`

- `OrderBookSnapshot.from_ws(raw)` — same BBO/depth logic as `from_api()` but `fetch_latency_ms=0`, no `seconds_since_last_trade` tracking
- `Trade.from_ws(raw, token_to_outcome)` — derives `outcome`/`outcome_index` from mapping, sets `source='ws'`, logs error for unknown token_ids
- `Trade.source` field added (default `'rest'`)
- New `PriceSignal` dataclass with `from_ws(raw)` — computes `mid_price` from bid/ask

### Step 3: Dual-Write Validation ⏳ NEEDS RE-RUN

**What's built:**
- `--validate` CLI flag enables REST `poll_trades()` alongside WS
- `source` column on `trades` table (`'ws'` or `'rest'`)
- `UNIQUE(transaction_hash, token_id, source)` allows both sources to insert independently
- `scripts/validate_dual_write.py` — analysis script comparing WS vs REST capture rates
- Streamlit dashboard (`dashboard.py`) with Dual-Write Validation tab
- `scripts/analyze_data_fitness.py` — comprehensive data quality analyzer

**Previous run (2026-03-24) — DATA WIPED:**

14 collectors ran across NBA, NHL, tennis, Valorant. Data fitness analysis revealed all DBs scored 17-31/100 due to: zero game events, trade-market mismatch, illiquid majority. All data deleted for clean restart.

**TO DO — FRESH VALIDATION:**

1. Pick 2-4 NBA games on a game night
2. Run with `--validate` flag: `python -m collector --config configs/match_nba-*.json --db data/<match>-VALIDATE.db --validate`
3. Game events should now populate (lookup_game_id fix applied)
4. After games complete, evaluate:
   - `python scripts/validate_dual_write.py data/*-VALIDATE.db` — WS ≥98% of REST trades?
   - `python scripts/analyze_data_fitness.py data/*-VALIDATE.db` — fitness score should be much higher now
5. **Pass criteria:** WS ≥98% trades AND match_events > 0 AND fitness score ≥50
6. If pass: clean up dual-write scaffolding (see "What To Do After Validation Completes")
7. If fail: investigate gaps, fix, re-validate

### Step 4: CLI Integration ✅ COMPLETE

**File:** `collector/__main__.py`

- Replaced `run_book_poller`/`run_trade_poller` with `run_ws_client` + `run_ws_db_writer`
- Builds `token_to_outcome` mapping from config at startup
- `--validate` flag adds `run_rest_trade_poller` task alongside WS
- Status reporter logs: snapshots, trades, signals, events, WS message count
- Graceful shutdown: `ws_client.stop()` flushes remaining buffer

Task layout:
```
asyncio.gather(
    run_ws_client(ws_client),           # WS connection + message parsing
    run_ws_db_writer(ws_client, db),    # DB writes from WS queue
    run_rest_trade_poller(pm_client),   # only with --validate
    run_game_state_poller(gs, db),      # sport-specific polling (unchanged)
    status_reporter(),                  # periodic status log
)
```

### Step 5: DB Extensions ✅ COMPLETE

**File:** `collector/db.py`

- `price_signals` table in schema
- `insert_price_signals(signals)` method
- `count_price_signals()` method
- `source` column on `trades` table
- `insert_trades()` writes `source` field

### Step 6: Tests ✅ COMPLETE

**File:** `tests/test_ws.py` — 31 new tests (71 total, up from 40)

1. **Parsing tests** (fixture-based):
   - `OrderBookSnapshot.from_ws()` — BBO, sort order, depth, timestamps, empty books, truncation
   - `Trade.from_ws()` — fields, outcome derivation, unknown token graceful handling, timestamps
   - `PriceSignal.from_ws()` — fields, mid_price computation, timestamps

2. **WS client dispatch tests**:
   - `_dispatch()` routes book/trade/signal correctly
   - Unknown event types discarded
   - Market ID override from config mapping
   - `WriteBatch` length counting
   - `_flush()` puts batch on queue
   - Empty flush is no-op

3. **DB round-trip tests**:
   - `price_signals` table exists
   - `insert_price_signals()` round-trip with field verification
   - WS trade dedup in DB
   - Full fixture → parse → insert → query round-trip

### Step 7: Streamlit Dashboard ✅ COMPLETE (bonus)

**File:** `dashboard.py`

Not in original plan but built for visual data inspection:

- DB selector sidebar (✓ marks validation DBs)
- Overview: row counts for all tables, market metadata
- **Price Signals tab**: BBO time series per token, spread chart, summary stats
- **Trades tab**: price over time by outcome, size distribution, recent trades table
- **Order Books tab**: mid price, book depth, inside liquidity, spread over time
- **Dual-Write Validation tab**: WS vs REST capture rate, overlap analysis, timeline comparison
- **Data Gaps tab**: any recorded disconnects
- Handles old DBs gracefully (missing columns/tables)

---

## Verification Checklist

- [x] `WebSocketMarketClient` connects and subscribes to tokens (tested with 12, 88, 10-token configs)
- [x] Initial book snapshot array parsed into `OrderBookSnapshot` objects
- [x] Subsequent `book` events parsed and stored in `order_book_snapshots`
- [x] `last_trade_price` events parsed into `Trade` with correct `outcome`/`outcome_index`
- [x] `best_bid_ask` events stored in `price_signals` table
- [x] Heartbeat PING/PONG works (confirmed in 45s live test, no disconnects)
- [x] Reconnection with backoff implemented (exponential 1s→30s)
- [x] Data gaps logged when WS disconnected >5s
- [ ] **⏳ Dual-write validation: WS captures ≥98% of REST trades over 2+ matches** — previous data wiped, needs re-run
- [x] All 71 parsing tests pass with spike fixtures
- [x] Live 45s test: 18 snapshots, 3 trades, 6 signals from NHL game (0 gaps)
- [x] NBA game ID auto-lookup from scoreboard (`lookup_game_id()`) — fixes 0 game events issue
- [ ] Fresh validation with game events captured (re-run on next NBA game night)

---

## ⏳ What To Do After Validation Completes

### If validation PASSES (WS ≥98%):

1. **Clean up dual-write scaffolding:**
   - Remove `source` column from trades schema (revert to `UNIQUE(transaction_hash, token_id)`)
   - Remove `--validate` flag and `run_rest_trade_poller` from `__main__.py`
   - Remove `Trade.source` field (or keep as always `'ws'`)
2. **Move `polymarket_client.py` REST trade code to `old_plans/` or mark as deprecated**
3. **Update `verify_collection.py` to report `price_signals` count**
4. **Move this plan to `old_plans/Phase2_WS_Architecture.md`**
5. **Begin Phase 3: Analysis** — overshoot detection using price_signals + match_events

### If validation FAILS (WS <98%):

1. Investigate: check `data_gaps` table, look for specific tokens or time windows where WS missed trades
2. If WS disconnects are the cause: tune reconnect logic, add redundant connection
3. If WS simply doesn't emit some trades: keep REST as supplement (`--hybrid` mode)
4. Re-run validation with fixes

---

## What's Deferred

| Item | Trigger to revisit |
|---|---|
| In-memory books from `price_change` deltas | Overshoot analysis needs depth context |
| Sports WS channel integration | Live NBA game shows useful Sports channel data |
| REST fallback mode | WS proves unreliable over 2-3 hour matches |
| `price_change` event persistence | Depth-based microstructure analysis needed |
| `new_market` / `market_resolved` handling | Dynamic market tracking needed |
| Remove `source` column from trades | After validation passes |
