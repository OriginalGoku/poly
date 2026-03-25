# WS Stability Fix + REST Removal + Connection Sharding

> Fix WebSocket connection stability (23% downtime for NBA), drop broken REST trade polling, shard connections by market type, and prepare for another collection night before Phase 3 analysis.

---

## Problem Statement

Analysis of 112 databases from the 2026-03-24/25 collection revealed three blocking issues:

1. **WS disconnects every ~80s with 82 tokens** — Polymarket's server disconnects high-bandwidth connections. Token count directly correlates with disconnect frequency (82 tokens = ~80s uptime, 20 tokens = hours). Compounded by a dual-ping conflict: the `websockets` library sends protocol PING frames every 20s while our code sends text `"PING"` every 10s. Result: **23% downtime** for NBA games.

2. **REST trade data is useless** — The Data API ignores the `asset_id` query parameter, returning event-wide trades (~2,600 tokens) instead of the requested token. WS captures 98.5-99.5% of configured-token trades; REST captures <2% that overlap. REST is noise, not a safety net.

3. **Backoff counter never resets** — After `ConnectionClosed`, the attempt counter climbs and stays capped at 30s delay forever. Only clean disconnects (which never happen with server-side kicks) reset it.

Additional context:
- NHL game state configs are already fixed (committed 2026-03-25). Just need stale comment fix in `run_tonight.sh`.
- CS2 data (1 evening, odd/even props only) is inconclusive — need collection during a major tournament before writing it off.
- Goal is to collect one clean night with these fixes before starting Phase 3 event-price correlation analysis.

---

## Design Decisions

### D1: Shard WS connections by market type, not evenly

**Decision:** Group tokens into "core" (moneyline + spread + O/U + 1H lines) and "prop" (player stat props) shards. All shards capped at 25 tokens; oversized groups split into numbered sub-shards.

**Rationale:** Core markets are the ones that matter for the overreaction hypothesis (liquid, tight spreads, price reactions to game events). They get a stable connection (~22 tokens). Props are noise (avg spread $0.69, avg depth $23) — if their connection is less stable, minimal data loss. Market-type sharding also enables prioritized reconnect for the most valuable data.

**Trade-off:** Even sharding (split 82 tokens into 4x20) was considered but doesn't prioritize the liquid markets that actually matter for analysis.

### D2: Shared queue with single DB writer (not per-shard writers)

**Decision:** All WS client shards feed into one shared `asyncio.Queue`. A single `run_ws_db_writer` task consumes from it.

**Rationale:** Simpler architecture, no DB write contention, serialized inserts. The DB writer is not a bottleneck (SQLite writes are fast relative to WS message rate). Per-shard writers would add complexity for no benefit.

### D3: Drop REST entirely (no fallback)

**Decision:** Remove REST trade polling completely. Do not implement REST-as-fallback during WS gaps.

**Rationale:** REST Data API returns event-wide trades (asset_id param is ignored), has ~1 req/s rate limit, and takes ~97s to cycle through 82 tokens. By the time REST completes one cycle, WS has already reconnected (~31s). The 0.5-1.5% of trades WS misses correlate with disconnection gaps — fixing WS stability directly addresses this more effectively than a broken REST fallback.

### D4: Reset backoff only after receiving data, not just on connection

**Decision:** Reset `attempt = 0` only when the client has received meaningful data (initial book snapshot dispatched). Keep escalating if the connection was rejected immediately or kicked before any data arrived.

**Rationale:** `_connected` is set True before subscription completes, so a server that accepts TCP but immediately kicks after subscribe would appear as a "real session" and keep `attempt` near 0, causing rapid reconnect loops. Requiring actual data receipt ensures we only reset after a genuinely healthy session.

### D5: Categorize markets by question text, not relationship field

**Decision:** Use question text parsing for market categorization. The `relationship` field from configs is unreliable — moneylines get `"unknown"`, spreads get `"unknown"`, and both player props and game O/U share `"over_under"`.

**Rationale:** Question text has clear, reliable patterns: player props always have `"PlayerName: Stat O/U"` format. Everything else (moneyline, spread, game O/U, 1H lines) is "core" — all liquid, all relevant for analysis. Unrecognized patterns default to "core" as a fail-safe.

### D6: Use library ping frames, remove manual text pings

**Decision:** Keep `websockets` library protocol PING (30s interval, 10s timeout) and remove all manual text `"PING"` sending.

**Rationale:** Disabling library pings entirely (the original approach) would remove dead-connection detection — the only liveness check after a `recv` timeout was sending a text `"PING"` and continuing, with no PONG timeout enforcement. A stalled TCP connection could persist indefinitely and silently drop data. Library ping frames handle this automatically: if no PONG arrives within the timeout, the connection is closed and reconnect triggers.

---

## Implementation Plan

### Step 1: Fix WS ping strategy + dead-connection detection

**File:** `collector/ws_client.py`

**Problem:** Dual pings — library sends protocol PING frames every 20s, our code sends text `"PING"` every 10s. Combined traffic may trigger server bandwidth limits.

**Fix:** Use library ping frames only (reliable dead-connection detection), remove manual text pings.

1. **Line 120** — Set explicit library ping interval (longer than default to reduce overhead):
   ```python
   async with websockets.connect(WS_MARKET_URL, ping_interval=30, ping_timeout=10) as ws:
   ```
   This means: library sends a protocol PING every 30s, closes connection if no PONG within 10s. This handles dead-connection detection automatically.

2. **Remove `_heartbeat()` method entirely** (lines 182-188) — no more manual text PINGs.

3. **Remove heartbeat task creation** (lines 149, 152-157) — just call `await self._receive_loop(ws)` directly.

4. **Update `_receive_loop()`** (lines 190-207):
   - Keep a generous timeout (60s) as a safety net, but instead of sending PING on timeout, return to trigger reconnect:
   ```python
   try:
       raw = await asyncio.wait_for(ws.recv(), timeout=60)
   except asyncio.TimeoutError:
       logger.warning("No WS message in 60s, forcing reconnect")
       return  # exit receive loop, triggers reconnect
   ```

5. **Remove PONG handling** (lines 200-201) — library handles PONG at protocol level; text "PONG" messages won't arrive anymore.

### Step 2: Fix backoff reset logic

**File:** `collector/ws_client.py`

**Problem:** Backoff counter never resets after server-side disconnects during live sessions. Resetting on `_connected` alone is too aggressive — server could accept TCP then immediately kick before any data arrives.

**Fix:** Track whether we received meaningful data before resetting.

1. **Add `_received_data` flag** (init area, ~line 69):
   ```python
   self._received_data = False
   ```

2. **Set flag True after initial books dispatched** — in `_connect_and_receive()` after `self._dispatch(initial_books)` (line 180):
   ```python
   self._dispatch(initial_books)
   self._received_data = True
   ```

3. **In the `except` block** (lines 81-90), reset attempt only if we had a real session with data:
   ```python
   except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
       if not self._running:
           break
       if self._received_data:
           attempt = 0  # had a real session with data
       self._received_data = False
       delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
       logger.warning("WS disconnected (%s), reconnecting in %ds...", e, delay)
       self._connected = False
       if self._disconnect_ts is None:
           self._disconnect_ts = time.time()
       await asyncio.sleep(delay)
       attempt += 1
   ```

### Step 3: Drop REST trade polling

**File:** `collector/__main__.py`
- Remove `run_rest_trade_poller()` function (~lines 116-118)
- Remove `validate` parameter from `main()` signature
- Remove conditional REST task creation (~lines 283-285)
- Remove `--validate` CLI arg (~line 354)
- Keep `PolymarketClient` import + instantiation (still used for `fetch_market_metadata()`)

**File:** `collector/polymarket_client.py`
- Remove `poll_trades()`, `_fetch_trades()`, `_fetch_trades_for_token()`
- Remove `poll_books()`, `_fetch_books()`, `_flush_snapshots()`
- Remove unused init state: `_trade_error_start`, `_book_error_start`, `_prev_last_trade`, `_prev_snapshot_ts`, `_snapshot_buffer`, `_last_flush_time`, `snapshot_count`, `trade_count`, `book_interval`, `trade_interval`
- Remove `TradeWatermark` import
- Keep: simplified `__init__`, `start()`, `close()`, `fetch_market_metadata()`

**File:** `scripts/run_tonight.sh`
- Remove `--validate` from launch command
- Remove `-VALIDATE` from db_path construction
- Remove `validate_dual_write.py` from post-run instructions

### Step 4: WS connection sharding by market type

**File:** `collector/config.py` — Add two functions:

1. `categorize_market(question: str) -> str` — Returns `"core"` or `"prop"`:
   - Player props match patterns like `"PlayerName: Points O/U"`, `"PlayerName: Rebounds O/U"`, etc.
   - Everything else (moneyline, spread, game O/U, 1H lines) is core
   - **Fallback:** Unrecognized question patterns default to `"core"` (safe — ensures important data gets the stable connection)

2. `build_token_shards(markets: list[MarketConfig], max_per_shard: int = 25) -> dict[str, list[str]]`:
   - Groups tokens into core and prop categories
   - **Core tokens:** If <= `max_per_shard`, single shard `"core"`. If > `max_per_shard`, split into `"core_1"`, `"core_2"`, etc.
   - **Prop tokens:** Split into chunks of `max_per_shard`: `"prop_1"`, `"prop_2"`, etc.
   - Limit is 25 (not 20) — data shows 20 tokens = hours of stability, and core markets typically have ~22 tokens. 25 gives headroom without approaching the instability zone (~40+ tokens).
   - Returns e.g. `{"core": ["0x..."], "prop_1": ["0x..."], "prop_2": ["0x..."]}`

**File:** `collector/ws_client.py` — Minor constructor changes:
- Add optional `queue: asyncio.Queue | None = None` — if provided, use shared queue; otherwise create internal queue (backward-compatible)
- Add optional `name: str = "default"` — for log messages: `"WS [core] connected"`, `"WS [prop_1] disconnected"`
- Update all log messages to include shard name

**File:** `collector/__main__.py` — Multi-client orchestration:
- Call `build_token_shards(config.markets)` to get shard groupings
- Create one shared `asyncio.Queue`
- Instantiate one `WebSocketMarketClient` per shard with shared queue + shard name
- Create one `run_ws_client` task per shard
- Single `run_ws_db_writer` consuming from shared queue (accepts queue param directly)
- Update status reporter to sum counts across all clients
- Update shutdown to stop all clients
- Update collection run finalization to sum counts across all clients
- **Per-shard gap attribution:** Include shard name in `data_gaps.collector` field (e.g., `"ws_market:core"`, `"ws_market:prop_1"`)

### Step 5: Documentation updates

**File:** `scripts/run_tonight.sh` line 64
- Change `"price data only, no game state"` to `"has game state via nhl_api"`

**File:** `CLAUDE.md`
- Remove `--validate` from command examples
- Note WS connection sharding in ws_client.py description
- Update test count

**File:** `LESSONS_LEARNED.md`
- Add bullet about dual-ping conflict causing disconnects
- Add bullet about REST Data API asset_id being ignored

### Step 6: Tests

**New tests in `tests/test_config.py`:**
- `test_categorize_market_moneyline()` — "Team A vs Team B" -> "core"
- `test_categorize_market_spread()` — "Team A +3.5" -> "core"
- `test_categorize_market_game_ou()` — "Team A vs Team B: Total O/U 215.5" -> "core"
- `test_categorize_market_player_prop()` — "LeBron James: Points O/U 27.5" -> "prop"
- `test_categorize_market_unknown_defaults_core()` — fallback behavior
- `test_build_token_shards_basic()` — verify core/prop split
- `test_build_token_shards_respects_max()` — verify chunking at max_per_shard boundary
- `test_build_token_shards_small_count()` — all tokens fit in one shard

**New tests in `tests/test_ws.py`:**
- `test_shared_queue_two_clients()` — two clients with shared queue, single consumer gets both
- `test_backoff_resets_after_data()` — verify attempt resets only after receiving data
- `test_backoff_no_reset_without_data()` — verify attempt doesn't reset on immediate disconnect

**Existing tests (110+):** Should all pass — constructor changes are backward-compatible (optional params with defaults).

---

## Verification

1. **`python -m pytest tests/ -v`** — All tests pass (existing + new)
2. **Dry run:** `python -m collector --config configs/match_nba-*.json` — verify shard log output shows shard names and correct token counts
3. **Deploy to Raspberry Pi, collect one full evening** (next available game night)
4. **Post-collection checks:**
   - `data_gaps` table: gaps should be dramatically fewer for NBA (hours between disconnects vs ~80s before)
   - `data_gaps.collector` should show shard names
   - `match_events > 0` for NHL games (verify game state fix)
   - Snapshot/trade/signal counts should match or exceed March 24 collection
   - No REST trades in new databases (source column should be 100% `ws`)
5. If collection is clean: proceed to Phase 3 event-price correlation analysis

---

## Review Notes (from Codex critique, 2026-03-25)

**Accepted and incorporated:**
- **D6 (new):** Use library ping frames (30s interval, 10s timeout) instead of disabling pings entirely — preserves dead-connection detection. Original plan would have left half-open connections undetected.
- **D4 (revised):** Reset backoff only after receiving data, not just on `_connected` — prevents rapid reconnect loops when server accepts TCP but kicks before data arrives.
- **D1 (revised):** Raised `max_per_shard` from 20 to 25 and added core-splitting logic — no shard exceeds its own limit. Original plan had core shard at ~22 tokens with a limit of 20.
- **D5 (strengthened):** Default unrecognized question patterns to "core" — fail-safe for hypothesis-critical data.

**Deferred:**
- Whether Polymarket's WS server officially supports text PING/PONG is undocumented — moot since we're removing text pings entirely.
- Dynamic sharding based on runtime disconnect frequency — nice-to-have but unnecessary given 25 tokens = hours of stability.
- Per-shard gap attribution in `data_gaps` table — included in plan but can be simplified to just `"ws_market"` if shard-level analysis isn't needed.
