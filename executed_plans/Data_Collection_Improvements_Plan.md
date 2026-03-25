# Data Collection Improvements Plan

> Fix NHL game state configs, add order book imbalance signal, and optionally add sub-second BBO from price_change events — based on critical review of the original improvements analysis.

---

## Problem Statement

The 2026-03-24 collection run revealed several data quality gaps: 14/15 NHL games had zero game events, WS trade capture is 23-42% (not 98%+), and price signal resolution is ~4s. The original `plans/data_collection_improvements.md` proposed 5 improvements. A critical review (brainstorm with Codex) found flaws in 3 of the 5 proposals that need correction before implementation.

## Design Decisions

### D1: No Trade records from price_change events

**Decision:** Handle `price_change` WS events for BBO signals only. Do NOT emit Trade records.

**Rationale:** The `price_change` payload is an order book delta, not a fill notification. The `hash` field is not a `transaction_hash`, and `size` refers to resting order quantity at a price level, not fill size. Sample fixture (`tests/fixtures/ws_price_change_sample.json`) confirms `size: "0"` for both entries.

**Trade-off:** This means WS trade capture stays at 23-42% (sourced only from `last_trade_price` events). The plan's claim of ">90% trade capture" from price_change is incorrect. Trades remain secondary context per the analysis — price signals and game events drive the overreaction hypothesis.

### D2: NHL fix is config-only, not code

**Decision:** Regenerate NHL configs with `data_source: "nhl_api"`. No changes to `__main__.py`.

**Rationale:** The NHL dispatch in `__main__.py:81-94` is already correct and includes `lookup_game_id()` auto-resolution. The root cause is that all NHL configs have `data_source: "none"` (verified directly), so the client is never instantiated. The `discover_markets.py` script already has the correct sport-to-data_source mapping — existing configs predate this fix.

### D3: Imbalance on snapshots only, not price_signals

**Decision:** Compute order book imbalance only on `order_book_snapshots`. Do not add it to `price_signals`.

**Rationale:** `PriceSignal` has no size fields, and `best_bid_ask` WS events don't include sizes. Computing imbalance on price_signals would require an in-memory order book (unnecessary complexity). Snapshots already have `best_bid_size` and `best_ask_size`.

### D4: Keep ~4s BBO resolution, skip price_change handling

**Decision:** Do not implement `price_change` event handling. Keep existing `best_bid_ask` events at ~4s resolution.

**Rationale:** The overreaction hypothesis measures spikes that revert over 5-minute windows. At ~4s resolution, that's ~75 data points per window — more than sufficient to capture spike shape, peak, and reversion. Sub-second BBO would only matter for measuring exact event-to-price reaction latency (which also requires reliable server timestamps on game events, which we don't have for NHL) or high-frequency microstructure analysis (not our hypothesis). The cost — new parser, BBO-change dedup logic, 10-100x more rows in price_signals, larger DBs — is not justified.

**Trade-off:** If Phase 3 analysis reveals that 4s resolution is masking important spike dynamics, `price_change` handling can be added later. The `price_change` WS event is documented in `plans/data_collection_improvements.md` and a sample fixture exists at `tests/fixtures/ws_price_change_sample.json`.

### D5: Defer Sports API WS and NBA event expansion

**Decision:** Defer improvements #3 (Sports API WS) and #5 (additional NBA events) until after validating #1-#3.

**Rationale:** Sports API WS has unresolved protocol risks (no subscription filter, different heartbeat, team name mapping). NBA event expansion (fouls, turnovers) would 5-10x the event table without a clear filtering strategy. Both need more design work.

## Implementation Plan

### Step 1: Regenerate NHL configs (LOW EFFORT)

- Patch all `configs/match_nhl-*.json` files: set `"data_source": "nhl_api"`
- Or regenerate via `scripts/discover_markets.py` (already maps NHL correctly)
- Verify at least one NHL config has `data_source: "nhl_api"` after fix

### Step 2: Add config validation warning (LOW EFFORT)

- File: `collector/config.py`
- In `load_config()`, after building the `MatchConfig`, add:
  - Warning log when `sport in {"nba", "nhl"}` but `data_source == "none"`
  - This prevents silent config misconfiguration in future

### Step 3: Add imbalance to order_book_snapshots (LOW EFFORT)

- File: `collector/models.py` — add `imbalance: float | None` field to `OrderBookSnapshot`
- Compute: `best_bid_size / (best_bid_size + best_ask_size)` with guard for zero/None sizes
- File: `collector/db.py` — add `imbalance REAL` column to `order_book_snapshots` CREATE TABLE
- File: `collector/db.py` — include `imbalance` in INSERT statement for snapshots
- No migration needed for new DBs (schema is CREATE IF NOT EXISTS per collection)

## Verification

1. **NHL configs:** `python3 -c "import json; [print(json.load(open(f))['data_source']) for f in __import__('glob').glob('configs/match_nhl-*.json')]"` — all should print `nhl_api`
2. **Config warning:** Run collector with a config where `sport: "nhl"` and `data_source: "none"` — should log a warning
3. **Imbalance:** Run collector on a live game, then `SELECT imbalance FROM order_book_snapshots LIMIT 5` — values between 0 and 1
4. **Existing tests:** `python -m pytest tests/ -v` — all 71 tests pass
