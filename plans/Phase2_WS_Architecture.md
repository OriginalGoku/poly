# Phase 2: WebSocket Architecture

> Replace REST polling with a WS-only data pipeline for order books, trades, and sub-second price signals. Validated by research spike (2026-03-24).

---

## Status: VALIDATION PASSED — READY FOR PHASE 3

All code built and tested (71 tests). **WS validation passed (2026-03-25):** WS captures 98.5-100% of config-token trades across 4 NBA + 15 NHL games. 114 databases collected, 5 sports.

**Key milestones:**
- `lookup_game_id()` fix confirmed — all 4 NBA games have game events (117-167 per game)
- DEN-PHX scored 77/100 fitness, with 2,401 spike candidates
- Hypothesis readiness: 5/5 checks passed

**Next:** Phase 2 cleanup (see below), then begin Phase 3 analysis.

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

### Step 3: Dual-Write Validation ✅ PASSED

**What's built:**
- `--validate` CLI flag enables REST `poll_trades()` alongside WS
- `source` column on `trades` table (`'ws'` or `'rest'`)
- `UNIQUE(transaction_hash, token_id, source)` allows both sources to insert independently
- `scripts/validate_dual_write.py` — analysis script comparing WS vs REST capture rates
- Streamlit dashboard (`dashboard.py`) with Dual-Write Validation tab
- `scripts/analyze_data_fitness.py` — comprehensive data quality analyzer

**Validation run (2026-03-24/25):**

114 databases collected across NBA, NHL, ATP, WTA, Valorant, CS2. When filtered to config tokens only, WS captures 98.5-100% of trades (NBA: 98.5-99.5%, NHL: 99.3-100%). REST only captures 0.4-2.3%.

**Note:** The original unfiltered analysis showed misleadingly low WS rates (23-42%) because the REST Data API returns event-wide trades from 1,933+ markets. See corrected analysis in `plans/data_collection_improvements.md`.

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
- [x] **Dual-write validation: WS captures ≥98% of config-token trades** — PASSED 2026-03-25 (98.5-100% across 19 games)
- [x] All 71 parsing tests pass with spike fixtures
- [x] Live 45s test: 18 snapshots, 3 trades, 6 signals from NHL game (0 gaps)
- [x] NBA game ID auto-lookup from scoreboard (`lookup_game_id()`) — fixes 0 game events issue
- [x] Fresh validation with game events captured — 4 NBA games, 117-167 events each

---

## ✅ Validation PASSED (2026-03-25)

WS captures **98.5-100%** of trades for config tokens across all NBA and NHL games. REST captures only 0.4-2.3%. See corrected analysis in `plans/data_collection_improvements.md`.

### Phase 2 Cleanup Steps

1. **Keep `source` column** in trades schema for backward compatibility with 114 existing DBs. All new trades default to `source='ws'`.
2. **Remove `--validate` flag and `run_rest_trade_poller`** from `__main__.py` (REST polling no longer runs by default)
3. **Keep `polymarket_client.py`** in repo but unwired — fallback insurance if WS degrades
4. **Move `validate_dual_write.py`** to `old_plans/` (served its purpose)
5. **Update `verify_collection.py`** to report `price_signals` count
6. **Move this plan to `old_plans/`** after cleanup is done
7. **Begin Phase 3: Analysis** — overshoot detection using price_signals + match_events

---

## Notes & Open Items

### Timestamp alignment (in progress)

WS price signals use Polymarket `server_ts_ms` (exchange clock). NBA game events use NBA CDN `server_ts_ms` (NBA clock). These are different server clocks with estimated <1-2s drift. Current polling delay (local_ts vs server_ts_raw) ranges 10-75s but `server_ts_ms` bypasses this.

**Action:** Use `server_ts_ms` for all event-price correlations in Phase 3. Use asymmetric windows (e.g., T-5s to T+120s) wide enough to absorb clock drift. **Re-evaluate after Phase 3 implementation** — if initial results show suspicious timing patterns, investigate per-source clock offset calibration.

### Sub-second price resolution (`price_change` events) — deferred

`price_change` WS events could improve signal resolution from ~4s to sub-second. However, initial research suggests overreaction patterns in prediction markets operate on **seconds-to-minutes timescales**, not sub-second. The current ~4s resolution from `best_bid_ask` signals may be sufficient.

**Action:** Double-check this assumption during Phase 3 analysis. If spike detection or lead-lag analysis shows meaningful signal loss at 4s resolution, prioritize `price_change` handling.

### WS monitoring after REST removal

With REST disabled by default, there is no external sanity check for WS degradation.

**Action (Phase 4 candidate):** Build a lightweight periodic WS health audit — either a scheduled `--validate` run on one game per week, or a WS message rate monitor that flags anomalies (e.g., trade events dropping below expected baseline for active markets).

---

## What's Deferred

| Item | Trigger to revisit |
|---|---|
| In-memory books from `price_change` deltas | Overshoot analysis needs depth context |
| Sports WS channel integration | Live NBA game shows useful Sports channel data |
| REST fallback mode | WS proves unreliable over 2-3 hour matches |
| `price_change` event persistence | Phase 3 shows ~4s resolution is insufficient |
| `new_market` / `market_resolved` handling | Dynamic market tracking needed |
| WS health monitoring / periodic audit | Phase 4 — build after Phase 3 is running |
