# WebSocket Migration Plan

> Replace REST polling with Polymarket WebSocket feeds for sub-second order book data, eliminate rate limit errors, and unify game-state collection across all sports via the Sports channel.

---

## Problem Statement

The current REST-based collector has three limitations discovered during Phase 1c live validation:

1. **Data API rate limits**: `data-api.polymarket.com` enforces an undocumented ~1 req/s rate limit, causing 429 errors on 5-12 tokens per trade polling cycle across 88 tokens.
2. **Temporal resolution**: 3s order book polling may miss sub-second price overshoots — the exact signal needed for the overreaction thesis.
3. **Trade saturation**: The Data API returns max 100 trades per request with no working pagination. High-activity game moments silently lose trade data.

Polymarket offers unauthenticated WebSocket channels that solve problems 1 and 2, while a separate Sports channel provides unified game-state data across all sports.

## Design Decisions

### D1: Hybrid architecture — WS for books, REST for trades

**Decision:** Use WebSocket Market channel for order book data and price signals. Keep REST polling for trade capture only.

**Rationale:** The `trades` table requires `transaction_hash` (UNIQUE dedup key), `size`, `side`, `outcome`. The WS `last_trade_price` event almost certainly lacks these fields. Splitting by data type keeps the UNIQUE constraint working and avoids schema changes. With book polling moved to WS, the full Data API rate limit budget (~1 req/s) is available for trade polling alone — eliminating 429 errors.

**Trade-off:** Could go WS-only if `last_trade_price` includes full trade metadata, but this is unlikely and would require schema changes. Hybrid is safer and proven.

### D2: New `price_ticks` table for WS price signals

**Decision:** Store WS `last_trade_price` events in a new `price_ticks` table `(token_id, server_ts_ms, price)`, not in the `trades` or `order_book_snapshots` tables.

**Rationale:** Price ticks from WS have different semantics than trades (no tx_hash, size, side) and different cadence than snapshots (event-driven, not periodic). Mixing them into existing tables would pollute Phase 2 analytics and break UNIQUE constraints.

### D3: Sports channel supplements (doesn't replace) sport-specific clients

**Decision:** Use the Sports channel as a unified game-state source for all sports. Keep NBA CDN and OpenDota clients as enrichment for sports where per-play granularity matters.

**Rationale:** The Sports channel provides `score`, `period`, `live`, `ended` for NBA, CS2, Soccer, Tennis, NHL, and more — but likely at coarser granularity than our NBA CDN client (which gives every made shot, timeout, quarter end). Decision on full replacement deferred until the research spike reveals actual Sports channel granularity.

### D4: Research spike before implementation

**Decision:** Must connect and inspect actual WS payloads before writing any parser code. The docs describe event types but not field-level payload structures.

**Rationale:** Key unknowns that block architecture: (a) Does `book` send full depth or deltas? (b) Does `last_trade_price` include size/side/tx_hash? (c) Are there subscription limits for 88 tokens? (d) How frequent are book events? Wrong assumptions here mean wrong code.

### D5: In-memory book with fixed-cadence snapshot emission

**Decision:** If WS `book` events are deltas (not full snapshots), maintain an in-memory L2 order book per token updated from WS deltas, and emit snapshots to SQLite on a fixed cadence (e.g., 1s).

**Rationale:** Writing every WS message to SQLite could mean 100+ writes/second. Fixed-cadence emission bounds write rate, preserves the existing `order_book_snapshots` schema, and lets us choose resolution (250ms, 500ms, 1s) independently of WS message rate.

**Trade-off:** If `book` events are already full snapshots at reasonable frequency (~1/s), skip the in-memory book entirely and use `OrderBookSnapshot.from_ws()` directly. Decision deferred to research spike.

### D6: Keep REST as fallback

**Decision:** REST polling remains available as fallback. If WS disconnects for >30s, fall back to REST polling until WS reconnects. CLI flag: `--mode ws|rest|hybrid`.

**Rationale:** REST polling is proven (40/40 tests, 6 matches collected). WS connections drop. Games last 2-3 hours. Cannot afford data loss during a live match.

## WebSocket Channels (from docs)

### Market Channel (no auth)
- **URL:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- **Subscribe:** `{"assets_ids": ["token_id_1", ...], "type": "market", "custom_feature_enabled": true}`
- **Events:** `book`, `price_change`, `last_trade_price`, `best_bid_ask`, `tick_size_change`, `new_market`, `market_resolved`
- **Heartbeat:** Send `PING` every 10s, expect `PONG`
- **Dynamic:** Subscribe/unsubscribe without reconnecting

### Sports Channel (no auth)
- **URL:** `wss://sports-api.polymarket.com/ws`
- **Subscribe:** None needed — auto-streams all active sports events
- **Event:** `sport_result` with fields: `gameId`, `leagueAbbreviation`, `slug`, `homeTeam`, `awayTeam`, `status`, `score`, `period`, `live`, `ended`, `finished_timestamp`
- **Sports:** NFL, NHL, MLB, NBA, CBB, CFB, Soccer, Esports (CS2), Tennis
- **Heartbeat:** Server sends `ping` every 5s, client responds `pong` within 10s

## Implementation Plan

### Phase 0: Research Spike (30 min) — BLOCKING

See `plans/WebSocket_Research_Spike_Plan.md` for detailed steps.

**Goal:** Connect to both WS channels, dump raw messages, answer all blocking questions about payload structures, message frequency, and subscription limits.

**Outputs:** Sample fixtures in `tests/fixtures/ws_*.json`, analysis report, go/no-go decision on architecture.

### Phase 1: Market Channel Client (~2-3 hours)

Depends on research spike results. Two paths:

**Path A: `book` events are full snapshots**
1. Create `collector/ws_client.py` with `WebSocketMarketClient` class
2. Connect to Market channel, subscribe with all token_ids
3. Parse `book` events → `OrderBookSnapshot.from_ws()` → same DB table
4. Parse `last_trade_price` → new `price_ticks` table
5. Heartbeat task (PING every 10s)
6. Reconnection with exponential backoff + re-subscribe

**Path B: `book` events are deltas**
1. Same as Path A, plus:
2. In-memory `OrderBook` class per token (dict of price → size for bids/asks)
3. Apply `price_change` deltas to in-memory book
4. Emit `OrderBookSnapshot` on fixed cadence (configurable, default 1s)
5. Handle sequence gaps: request full book refresh on gap detection

### Phase 2: Sports Channel Client (~1-2 hours)

1. Create `collector/ws_sports_client.py`
2. Connect to Sports channel
3. Filter `sport_result` events by `slug` matching current match config
4. Parse → `MatchEvent` with `timestamp_quality="server"` when `finished_timestamp` present, else `"local"`
5. Map `score` string → `team1_score`/`team2_score` integers
6. Map `period` string → `quarter`/`round_number`/`map_number` as appropriate
7. Heartbeat: respond `pong` to server `ping` within 10s

### Phase 3: CLI Integration (~1 hour)

1. Add `--mode` flag to `__main__.py`: `ws` (default), `rest`, `hybrid`
2. `ws` mode: WS for books + Sports channel for game state + REST for trades only
3. `rest` mode: Current behavior (backward compatible)
4. `hybrid` mode: WS primary, REST fallback on WS failure
5. Gap detection: track `last_message_ts` per WS connection, log gap if no book/price message in 30s during live game

### Phase 4: Schema Addition (~30 min)

1. Add `price_ticks` table to `db.py`:
   ```sql
   CREATE TABLE IF NOT EXISTS price_ticks (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       token_id TEXT,
       server_ts_ms INTEGER,
       local_ts TEXT,
       price REAL,
       source TEXT DEFAULT 'ws'
   );
   CREATE INDEX IF NOT EXISTS idx_ticks_token_ms ON price_ticks(token_id, server_ts_ms);
   ```
2. Add `source` column to `order_book_snapshots` (`'rest'` or `'ws'`) for provenance tracking
3. Update `verify_collection.py` to report WS vs REST snapshot counts

### Phase 5: Testing (~1-2 hours)

1. Save WS message samples from research spike as fixtures
2. Test `OrderBookSnapshot.from_ws()` parsing (or in-memory book + snapshot emission)
3. Test `MatchEvent` parsing from Sports channel `sport_result`
4. Test reconnection logic (mock WS disconnect)
5. Test gap detection (mock stalled connection)
6. Test hybrid mode fallback (WS disconnect → REST resume → WS reconnect)

## Research Spike Results (2026-03-24)

Spike ran for 5 minutes against `match_nba-orl-cle-2026-03-24` (44 markets, 88 tokens). Raw data in `data/ws_spike_market.jsonl` and `data/ws_spike_sports.jsonl`. Fixtures saved to `tests/fixtures/ws_*.json`.

### Market Channel — Answers to Blocking Questions

**1. `book` event: Full snapshot or delta?**
→ **FULL SNAPSHOT**. On subscribe, server sends a single JSON array with all 88 books. Thereafter, individual `book` events are also full snapshots (all bids/asks for one token). No delta mode observed.

**2. `book` depth levels?**
→ Variable. Observed 4-14 levels per side. Fields: `market`, `asset_id`, `timestamp` (ms epoch string), `hash` (checksum), `bids[]`/`asks[]` (each `{price: string, size: string}`), `tick_size` (string), `event_type: "book"`, `last_trade_price` (string, may be empty).

**3. `last_trade_price` includes tx_hash?**
→ **YES!** Full payload: `{market, asset_id, price, size, fee_rate_bps, side, timestamp, event_type, transaction_hash}`. All fields needed for trades table are present.

**4. `last_trade_price` includes size/side?**
→ **YES.** `size: "31.7"`, `side: "BUY"`, `fee_rate_bps: "0"`.

**5. `best_bid_ask` event fields?**
→ `{market, asset_id, best_bid, best_ask, spread, timestamp, event_type}`. All strings.

**6. `tick_size_change` event?**
→ Not observed in 5 minutes.

**7. 88-token subscription?**
→ **SUCCESS.** All 88 tokens subscribed on one connection, no errors, book snapshots received for all.

**8. Message frequency (88 tokens, 5 min)?**
→ **7.17 msgs/sec** total. Breakdown:
| Event type | Count | Rate |
|---|---|---|
| `book` (snapshot) | 110 | 0.37/s |
| `price_change` | 1,802 | 6.01/s |
| `best_bid_ask` | 272 | 0.91/s |
| `last_trade_price` | 11 | 0.04/s |
| `new_market` | 43 | 0.14/s |
| Initial book array | 1 msg (88 books) | on-subscribe |

**9. Heartbeat?**
→ PING/PONG works as documented. No disconnects in 5 minutes.

**10. `new_market` event (bonus)?**
→ Broadcasts new market creation globally to all subscribers. Contains full event metadata (question, slug, outcomes, token IDs). Can be filtered or ignored.

### Sports Channel — Answers to Blocking Questions

**1. `sport_result` event payload?**
→ No `event_type` field. Flat objects: `{gameId (int), leagueAbbreviation, homeTeam, awayTeam, status, eventState: {type, createdAt, updatedAt, score, period, live, ended, ...sport-specific fields}, score, period, live, ended}`. Tennis includes `tournamentName`, `tennisRound` in `eventState`.

**2. Update frequency?**
→ 0.30 msgs/sec across all sports (92 messages in 308s). Bursty — long quiet periods then batches.

**3. Coverage (live during spike)?**
→ ATP, WTA, Challenger (tennis), Dota2, MLB. **No NBA observed** — likely no NBA game was live at time of spike.

**4. `slug` format?**
→ **No `slug` field present.** Uses `gameId` (integer). Matching to our config `match_id` will require either (a) a lookup table from Polymarket event metadata, or (b) fuzzy matching on `homeTeam`/`awayTeam`.

**5. `period` values?**
→ Tennis: "S1", "S2" (sets). NBA period values not observed (no live NBA games during spike).

### Critical Findings & Architecture Impact

**D1 REVISION — `last_trade_price` HAS full trade metadata:**
The original D1 assumed `last_trade_price` would lack `transaction_hash`, `size`, `side`. It has ALL of them. This means:
- WS-only for trades is viable (no REST needed for trade capture)
- `transaction_hash` enables UNIQUE dedup in `trades` table
- Rate limit problem (429s on Data API) is **fully solved** by going WS-only
- **Recommendation: Revise D1 to WS-only for both books AND trades**

**D2 REVISION — `price_ticks` table may be unnecessary:**
Since `last_trade_price` includes `size`, `side`, `fee_rate_bps`, and `transaction_hash`, these events can go directly into the `trades` table using the existing schema. A separate `price_ticks` table adds complexity without clear benefit.

**D5 RESOLVED — Path A confirmed:**
`book` events are full snapshots. No in-memory delta tracking needed. `OrderBookSnapshot.from_ws()` can parse directly.

**Sports channel limitations:**
- No NBA data observed (needs verification during live NBA game)
- No `slug` field — `gameId` matching is an open question
- D3 stands: keep sport-specific clients as primary, Sports channel as supplement/fallback

### Go/No-Go Decision

**GO for full WebSocket implementation (Path A).** Key reasons:
1. `book` events are full snapshots — simplest implementation path
2. `last_trade_price` has full trade metadata — WS-only architecture is viable
3. 88-token subscription works — no connection sharding needed
4. 7+ msgs/sec provides sub-second resolution for overreaction detection
5. No auth required, heartbeat works reliably

### Remaining Unknowns (non-blocking)

1. Sports channel during live NBA — need to test during a game
2. `tick_size_change` event format — rarely occurs, non-critical
3. Long-duration stability — 5 min success, need to verify over 2-3 hour game

## Verification

- [x] Research spike completed — all payload questions answered, fixtures saved
- [ ] WS Market client connects, subscribes to 88 tokens, receives book events without errors
- [ ] Order book snapshots from WS match schema of REST snapshots (same columns, same quality metrics)
- [ ] Price ticks table populated with sub-second price data
- [ ] Sports channel receives game events for live NBA/CS2 matches
- [ ] No rate limit errors (429s) during full game collection
- [ ] Gap detection works: simulated WS drop logs gap and triggers reconnect
- [ ] Hybrid mode: WS failure falls back to REST, WS reconnect resumes WS
- [ ] `verify_collection.py` reports WS vs REST data sources
- [ ] Full game collected via WS with comparable or better data quality than REST
