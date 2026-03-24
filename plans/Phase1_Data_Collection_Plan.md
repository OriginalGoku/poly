# Phase 1: Data Collection — Implementation Plan

> Build and deploy a multi-sport data collector on a Raspberry Pi that captures Polymarket order books, trade history, and game-state events during live esports and sports events. Collect from every available market to statistically test the emotional overreaction hypothesis.

---

## Problem Statement

During live sports and esports events, Polymarket prediction market prices appear to overshoot fair value in response to momentum shifts (rounds won, goals scored, scoring runs). The hypothesis is that these overshoots are driven by emotional/fan-driven trading and revert predictably — creating a tradeable edge.

Before building any trading system, we need data. This plan covers collecting synchronized order book snapshots, executed trades, and game-state events during live matches across multiple sports. The dataset must be rich enough to validate (or invalidate) the overreaction thesis in Phase 2 backtesting — including determining whether microtrades on thin markets are viable given spread costs.

## Design Decisions

### D1: Collect all related markets per match, no pre-filtering

**Decision:** For each match, collect order books for every Polymarket market tied to it — match winner, map/game winners, over/under, props, etc. Do not filter out low-volume or wide-spread markets during collection.

**Rationale:** We don't yet know which markets exhibit the strongest overreactions. Thin markets may overshoot MORE — which is the core thesis. Pre-filtering would encode assumptions we haven't validated. Rate limits (500 req/10s for `/books`) and storage (100GB) make collecting everything trivially affordable. Filter during Phase 2 analysis using quality metrics stored at ingest time.

**Trade-off:** More data to store and query, but storage math checks out: 500 tokens × 1,200 snapshots/hr × 3hrs × 20 matches ≈ 7.2GB.

### D2: Store full order book depth (top 10 levels)

**Decision:** Store full bid/ask depth arrays, not just best bid/ask.

**Rationale:** Phase 2 fill simulation requires depth data to model realistic execution. 100GB on Pi — storage is not a constraint. Estimated ~7MB per match with full depth. Re-collecting is impossible; storing extra costs nothing.

### D3: Add trade history capture with cursor-based pagination

**Decision:** Poll Polymarket Data API `/trades` endpoint every 15 seconds. Track last seen trade_id/timestamp per token and request only newer trades.

**Rationale:** Order books show available liquidity; trades show what actually executed. Without cursor tracking, polling every 15s could miss trades in active markets, biasing the dataset. Data API limit is 200 req/10s — polling trades for 10 tokens every 15s uses ~0.67 req/s.

### D4: Compute quality metrics at ingest time

**Decision:** For each order book snapshot, compute and store quality/liquidity signals alongside the raw data: spread, depth near mid, empty-book flag, and staleness indicator.

**Rationale:** Phase 2 filtering on spread/depth/staleness is the core analysis workflow. Computing these at ingest makes analysis queries simple `WHERE` clauses instead of complex window functions over JSON blobs. The compute cost at ingest is trivial.

### D5: Capture market metadata (tick_size, min_order_size)

**Decision:** Store `tick_size` and `min_order_size` from the `/book` API response on the `markets` table. Populate once per market at collection start.

**Rationale:** These fields directly determine microtrade feasibility. A tight spread means nothing if `min_order_size` is $50. With 0% maker/taker fees on Polymarket's CLOB, spread IS the entire trading cost — so knowing tick_size and min_order_size alongside spread tells you the actual cost of entry/exit.

### D6: Multi-sport collection with sport-specific game-state clients

**Decision:** Collect from CS2, Dota 2, LoL, NBA, and any other sport with Polymarket markets. Each sport gets its own game-state client. For sports without a free game-state API (soccer, tennis, hockey), collect Polymarket order books only (no game state).

**Rationale:** Polymarket has thousands of sports/esports markets across many sports. The overreaction thesis isn't sport-specific. More data across more sports strengthens or weakens the hypothesis faster.

**Available free game-state APIs:**

| Sport | API | Granularity | Free Tier |
|---|---|---|---|
| CS2 | PandaScore | Match scores, round results | 1,000 req/hr |
| Dota 2 | OpenDota | Kill timelines, objectives, live matches | 60k calls/mo (60/min) |
| LoL | Riot Games API + LoL Esports API | Frame-by-frame timeline, live pro match state | 20 req/s (dev key) |
| NBA | NBA CDN (unofficial) | Full play-by-play (every possession) | ~1 req/s (undocumented) |
| Soccer | API-Football (RapidAPI) | Goals, cards, subs | 100 req/day — too low for live polling |
| Valorant | Riot Games API | Round-by-round results | 20 req/s (dev key) |

**Sports collected order-book-only (no game state):** Soccer, tennis, hockey, cricket, UFC, baseball.

### D7: Measure API update latency, not "actual event" latency

**Decision:** Drop absolute latency criteria. Replace with measuring each game-state API's update frequency distribution (p50/p95 between polls where state changes).

**Rationale:** No ground truth for "actual round/play end time" from our data sources. Alignment quality will be self-evident in Phase 2 when correlating price movements with events.

### D8: Manual market discovery, logged and auditable

**Decision:** Market-to-match mapping is a manual human step before each match. The mapping is stored in a JSON config file and logged in the collection_runs table.

**Rationale:** Automated discovery requires fuzzy matching of team names across Polymarket and various game-state APIs — error-prone and over-engineered for pilot. A human can verify in 5 minutes. What matters is logging: which markets were found, which were included, which were excluded and why.

### D9: No orchestrator for pilot — manual runs

**Decision:** Run the collector manually per match via CLI. No scheduling, no orchestrator, no systemd until after 3-5 successful manual runs.

**Rationale:** For the first few matches, you need to watch the collector anyway. An orchestrator adds complexity before we've proven the basic pipeline works. Add automation after trust is established.

## Validated Constraints

Confirmed during planning:

| Constraint | Value | Source |
|---|---|---|
| `/book` rate limit | 1,500 req/10s | [Polymarket docs](https://docs.polymarket.com/api-reference/rate-limits) |
| `/books` rate limit | 500 req/10s | [Polymarket docs](https://docs.polymarket.com/api-reference/rate-limits) |
| `/trades` rate limit | 200 req/10s | [Polymarket docs](https://docs.polymarket.com/api-reference/rate-limits) |
| `/books` batch format | POST with `[{token_id}, ...]`, returns per-token books | [Polymarket docs](https://docs.polymarket.com/api-reference/market-data/get-order-book) |
| Order book response fields | market, asset_id, timestamp, hash, bids, asks, last_trade_price, tick_size, min_order_size | Polymarket docs |
| Polymarket fee structure | 0% maker/taker fees on CLOB — spread is the entire cost | Polymarket docs |
| Builder tier needed | None (Unverified default is sufficient) | [Builder tiers](https://docs.polymarket.com/developers/builders/builder-tiers) |
| Throttling behavior | Requests are delayed/queued, not rejected with 429 | Polymarket docs |
| Pi storage | 100GB available | User confirmed |
| PandaScore free tier | 1,000 req/hr, fixtures + match scores, round results for CS2 | [PandaScore](https://pandascore.co/pricing) |
| OpenDota free tier | 60k calls/mo, 60/min without key, 1,200/min with key | [OpenDota docs](https://docs.opendota.com) |
| Riot Games API free tier | 20 req/s dev key, match timeline + live spectator | [Riot developer portal](https://developer.riotgames.com) |
| NBA CDN endpoints | Unofficial, no key needed, ~1 req/s safe | Public JSON endpoints |

## Implementation Plan

### Phase 1a: Validate APIs (Day 1) — COMPLETED

**Goal:** Confirm all APIs work as expected before writing the collector.

**Status:** Gate passed. Two game-state APIs validated (OpenDota, NBA CDN). 511 match events discovered across 10 sports.

#### Results

**Polymarket CLOB API (`scripts/validate_polymarket.py`):**

| Test | Result |
|------|--------|
| `GET /book` response shape | PASS — all fields present including `tick_size=0.001`, `min_order_size=5` |
| `POST /books` batch | PASS — JSON body `[{"token_id": "..."}]` returns array of books |
| Sustained 3s polling (2 min) | PASS — 40/40 success, p50=124ms, p95=144ms, 0 errors, 0 throttling |
| Batch size scaling | PASS (partial) — flat ~115ms for 1-5 tokens; need more tokens to test 10-20 |
| Trade data | PASS — Data API (`data-api.polymarket.com/trades`) works keyless |
| Trade pagination | INCOMPLETE — cursor params don't shift window on Data API (see corrections below) |

**Game-state APIs (`scripts/validate_game_apis.py`):**

| API | Result |
|-----|--------|
| OpenDota (Dota 2) | PASS — 100 live matches, 15 pro/league, kill/objective timelines with types: building_kill, roshan_kill, courier_lost, firstblood, aegis |
| NBA CDN | PASS — scoreboard + full play-by-play (571 actions/game), action keys include period, clock, actionType, scoreHome, scoreAway, teamTricode |
| PandaScore (CS2) | SKIPPED — needs `PANDASCORE_TOKEN` env var |
| Riot Games (LoL) | SKIPPED — needs `RIOT_API_KEY` env var |

**Market discovery (`scripts/discover_markets.py`):**

511 match events found across 10 sports. Top by volume:

| Sport | Matches | Markets | Tokens | Volume | Data Source |
|-------|---------|---------|--------|--------|-------------|
| NBA | 115 | 325 | 650 | $8.3M | nba_cdn |
| Tennis | 88 | 811 | 1,622 | $3.9M | none |
| NHL | 83 | 426 | 852 | $2.7M | none |
| LoL | 1 | 17 | 34 | $711K | riot |
| Soccer | 37 | 143 | 286 | $561K | none |
| CS2 | 44 | 292 | 584 | $513K | pandascore |
| Valorant | 33 | 169 | 338 | $251K | riot |
| Cricket | 99 | 289 | 578 | $96K | none |

NBA games have 39-44 markets each (match winner, spreads, totals, player props). Config files saved to `configs/`.

#### Corrections to original plan (discovered during validation)

1. **`POST /books` format:** Body is `[{"token_id": "..."}]` (array of objects), not `[{token_id}, ...]` as originally written. Optional `"side"` field can filter to BUY or SELL only.
2. **CLOB `/trades` requires auth:** The CLOB endpoint requires `POLY_API_KEY` + signature headers. For keyless trade collection, use the Data API at `data-api.polymarket.com/trades` which returns: `proxyWallet`, `side`, `asset`, `conditionId`, `size`, `price`, `timestamp`, `title`, `slug`, `transactionHash`.
3. **Data API trade pagination is broken:** All cursor params (`after`, `before`, `cursor`, `since`, `next_cursor`, `offset`) return the same results with full overlap. Phase 1b should implement timestamp-windowed polling as a workaround, or obtain a CLOB API key for proper `next_cursor` pagination.
4. **Gamma API market search is broken:** The `/markets` endpoint ignores `tag`, `event_slug`, and `_q` params — always returns the same default results. Use `/events` with `tag_slug` param instead; markets are embedded in the event response.
5. **Response field `timestamp` is millisecond Unix epoch** (e.g., `1774366287418`), not ISO 8601.
6. **Order book `price` and `size` fields are strings**, not numbers. Parser must cast.

#### Saved fixtures

API response samples saved to `tests/fixtures/` for use in Phase 1b fixture-based tests:
- `polymarket_book_sample.json` — single `/book` response
- `polymarket_books_batch_sample.json` — batch `/books` response (3 tokens)
- `data_api_trades_sample.json` — Data API `/trades` response (5 trades)
- `opendota_live_sample.json` — OpenDota `/live` response (3 matches)
- `opendota_match_sample.json` — OpenDota match detail with objectives/teamfights
- `nba_scoreboard_sample.json` — NBA CDN scoreboard (2 games)
- `nba_pbp_sample.json` — NBA CDN play-by-play (20 actions)
- `riot_esports_sample.json` — placeholder (API key not set during validation)

### Phase 1b: Build Collector (Day 2-3) — COMPLETED

**Goal:** Build the async collector with all core modules, game-state clients for validated APIs, and fixture-based tests.

**Status:** Code complete. 40/40 fixture-based tests passing. Ready for live validation in Phase 1c.

#### What was built

All 8 planned modules implemented in `collector/`:

| Module | Status | Key implementation details |
|--------|--------|---------------------------|
| `models.py` | DONE | `OrderBookSnapshot.from_api()` handles string→float casting, sorts bids desc/asks asc, computes quality metrics. `Trade.from_api()` normalizes seconds→ms epoch. `MatchEvent` has all sport-specific fields. `MatchConfig`/`MarketConfig` for config loading. `TradeWatermark` for dedup state. |
| `db.py` | DONE | 10 tables, 11 indexes, WAL mode, `synchronous=NORMAL`. All CRUD ops async via aiosqlite. Trade insert uses `INSERT ... UNIQUE` constraint for dedup (catches `IntegrityError`). Watermark get/set. Gap logging. Collection run lifecycle (start/finish with summary counts). Query helpers for verification. |
| `config.py` | DONE | Loads match config JSON, validates required fields (`match_id`, `sport`, `team1`, `team2`, `markets`), accepts optional `polymarket_event_slug` and `polymarket_volume` from discover script. |
| `polymarket_client.py` | DONE | Two async polling loops: books (3s) and trades (15s). Books: `POST /books` with all tokens, buffered writes (flush every 10 rows or 30s). Trades: timestamp-windowed polling with watermark dedup per token, warns on potential truncation. Both: 5s retry on error, gap logging after 30s continuous failure. |
| `game_state/base.py` | DONE | ABC with `sport`, `poll_interval_seconds`, `poll() -> list[MatchEvent]`, `close()`. |
| `game_state/nba_client.py` | DONE | Polls NBA CDN play-by-play. Tracks `_last_action_number` to avoid reprocessing. Detects: `score_change` (made shots/FTs), `quarter_end`, `half_end`, `timeout`, `game_end`. Parses ISO 8601 `timeActual` → `server_ts_ms`. |
| `game_state/dota2_client.py` | DONE | Polls OpenDota `/live`, finds target match by `external_match_id`. Diffs consecutive polls for: `score_change` (score delta), `building_destroy` (bitmask change), `gold_lead_swing` (configurable threshold, default 2000), `game_end` (match disappears). First poll initializes state without emitting events. |
| `__main__.py` | DONE | CLI: `python -m collector --config <path> [--db <path>]`. Creates asyncio tasks for books, trades, game state (if applicable), status reporter (60s). SIGINT/SIGTERM graceful shutdown: cancels tasks, flushes buffers, finalizes collection run counts, closes DB. Structured JSON logging to `logs/` + stderr. |

**Not built (deferred to when API keys are obtained):**
- `game_state/cs2_client.py` — needs `PANDASCORE_TOKEN`
- `game_state/lol_client.py` — needs `RIOT_API_KEY`

The `__main__.py` already handles these gracefully: if `data_source` is `"pandascore"` or `"riot"`, it logs "not yet implemented" and runs order book + trades only.

#### Project structure (as built)

```
collector/
├── __init__.py
├── __main__.py              # CLI entry point, asyncio event loop, graceful shutdown
├── polymarket_client.py     # CLOB API (/books) + Data API (/trades)
├── game_state/
│   ├── __init__.py
│   ├── base.py              # Abstract base class for game-state clients
│   ├── dota2_client.py      # OpenDota /live diff-based event detection
│   └── nba_client.py        # NBA CDN play-by-play event detection
├── db.py                    # SQLite schema creation + write operations
├── models.py                # Dataclasses for parsed API responses
└── config.py                # Config file loading + validation
```

#### Tests (40/40 passing)

```
tests/
├── test_polymarket_client.py   # 16 tests — string→float casting, bid/ask sorting, spread/mid/depth
│                                #            computation, empty book handling, timestamp parsing,
│                                #            depth limited to 10 levels, batch parsing, trade parsing,
│                                #            timestamp normalization, unique tx hashes
├── test_game_state_clients.py  # 13 tests — NBA: score_change detection, team attribution, server_ts_ms
│                                #            populated, no duplicates on repoll, quarter tracking.
│                                #            Dota2: first poll no events, score_change, building_destroy,
│                                #            gold_lead_swing, game_end on disappear, no events after end.
└── test_db.py                  # 11 tests — schema creation, match/market insert, snapshot insert,
                                 #            quality metrics stored, trade dedup (UNIQUE constraint),
                                 #            watermark round-trip + update, gap logging, match events,
                                 #            collection run lifecycle, server_ts_ms NOT NULL across all tables.
```

Run: `python -m pytest tests/ -v`

#### Database schema

Implemented exactly as planned. SQLite with WAL mode, `synchronous=NORMAL`. 10 tables, 11 indexes. Schema SQL is in `db.py` as `SCHEMA_SQL` constant, executed on `Database.open()`.

Tables: `markets`, `market_match_mapping`, `matches`, `order_book_snapshots`, `trades`, `trade_watermarks`, `match_events`, `match_events_enriched` (Phase 2 placeholder), `data_gaps`, `collection_runs`.

All timestamp normalization implemented as planned:
- Order book `timestamp` (ms epoch string) → `server_ts_ms` integer
- Trade `timestamp` (seconds epoch) → `server_ts_ms` = `timestamp * 1000`
- NBA `timeActual` (ISO 8601) → `server_ts_ms` via `datetime.fromisoformat()`
- Dota2 `last_update_time` (seconds epoch) → `server_ts_ms` = `last_update_time * 1000`

#### Match config format

Implemented as planned. `config.py` loads JSON with required fields: `match_id`, `sport`, `team1`, `team2`, `markets[]`. Each market needs `market_id` and `token_ids[]`. Optional fields accepted: `external_id`, `tournament`, `best_of`, `scheduled_start`, `data_source`, `polymarket_event_slug`, `polymarket_volume`.

#### Collector behavior

Implemented as planned with these specifics:

- **Book polling:** `POST /books` to `https://clob.polymarket.com/books`. 30s timeout. Snapshot buffer flushes at 10 rows or 30s. Error retry after 5s. Gap logged after 30s continuous failure.
- **Trade polling:** `GET https://data-api.polymarket.com/trades?asset_id=<token>&limit=100`. Iterates all tokens sequentially. Watermark: loads from DB, filters `timestamp >= last_timestamp - 1`, deduplicates by `(transaction_hash, token_id)` against `recent_hashes` + DB UNIQUE constraint. Warns if all returned trades share same timestamp (truncation risk).
- **Game state:** Instantiated based on `config.data_source`. NBA polls every 10s, Dota2 polls every 5s. Only runs if `data_source != "none"`.
- **Status reporter:** Logs snapshot/trade/event counts every 60s.
- **Shutdown:** SIGINT/SIGTERM → sets `asyncio.Event`, cancels all tasks, flushes snapshots, finalizes collection run with summary counts.
- **Market metadata:** On startup, fetches `tick_size` and `min_order_size` via `GET /book?token_id=<first_token>` for each market, updates `markets` table.

### Phase 1c: Deploy & Collect (Day 3+) — NOT STARTED

**Goal:** Run the collector against real live matches, validate data quality, fix any issues discovered during live operation.

**Prerequisites:**
- Phase 1b code is complete and all 40 tests pass
- `discover_markets.py` can generate match configs
- Collector CLI works: `python -m collector --config <path>`

#### Steps

1. SSH to Pi, clone repo, `pip install -r requirements.txt`
2. Run `scripts/discover_markets.py` to find upcoming events across all sports with Polymarket markets
3. Create config JSON for each match (human reviews mapping). For NBA: set `external_id` to NBA game ID (e.g., `"0022501038"`). For Dota2: set `external_id` to OpenDota match ID.
4. Run collector manually: `python -m collector --config configs/<match>.json`
5. Monitor logs in a second terminal (`tail -f logs/collector_<match>_*.log`)
6. After match: run quick sanity queries on SQLite DB (see verification queries below)
7. Repeat for 2-3 matches across different sports
8. After confidence: add cron job or systemd timer for upcoming matches
9. Prioritize matches with game-state coverage (NBA, Dota 2) but also collect order-book-only for other sports

#### Post-match verification queries

```sql
-- Snapshot count and interval distribution
SELECT COUNT(*), MIN(local_ts), MAX(local_ts) FROM order_book_snapshots;

-- Check for NULL server_ts_ms
SELECT COUNT(*) FROM order_book_snapshots WHERE server_ts_ms IS NULL;
SELECT COUNT(*) FROM trades WHERE server_ts_ms IS NULL;
SELECT COUNT(*) FROM match_events WHERE server_ts_ms IS NULL;

-- Spread distribution
SELECT ROUND(spread, 3) as spread_bucket, COUNT(*)
FROM order_book_snapshots GROUP BY spread_bucket ORDER BY spread_bucket;

-- Empty book percentage per token
SELECT token_id,
       SUM(CASE WHEN is_empty THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as empty_pct
FROM order_book_snapshots GROUP BY token_id;

-- Trade count and dedup check
SELECT COUNT(*) FROM trades;
SELECT COUNT(*) FROM (SELECT transaction_hash, token_id FROM trades GROUP BY transaction_hash, token_id HAVING COUNT(*) > 1);

-- Game events
SELECT event_type, COUNT(*) FROM match_events GROUP BY event_type;

-- Data gaps
SELECT * FROM data_gaps;

-- Collection run summary
SELECT * FROM collection_runs;

-- Polling interval distribution (monotonic clock)
SELECT token_id,
       AVG(delta_ms) as avg_interval_ms,
       MIN(delta_ms) as min_interval_ms,
       MAX(delta_ms) as max_interval_ms
FROM (
    SELECT token_id,
           (local_mono_ns - LAG(local_mono_ns) OVER (PARTITION BY token_id ORDER BY local_mono_ns)) / 1000000.0 as delta_ms
    FROM order_book_snapshots
) WHERE delta_ms IS NOT NULL
GROUP BY token_id;
```

#### Deferred items for Phase 1c

- [ ] Build `game_state/cs2_client.py` once `PANDASCORE_TOKEN` is obtained and validated
- [ ] Build `game_state/lol_client.py` once `RIOT_API_KEY` is obtained and validated
- [ ] Consider obtaining CLOB API key for authenticated `/trades` with proper `next_cursor` pagination (upgrade path from timestamp-windowed polling)

## Verification

### Phase 1a Success Criteria — PASSED
- [x] Polymarket `/books` returns valid order books for multiple token IDs in one request — `POST` with JSON body, 3 tokens in 124ms
- [x] `tick_size` and `min_order_size` present in `/book` response — tick_size=0.001, min_order_size=5
- [x] Sustained 3s polling for 10 minutes shows no throttling and p95 latency < 500ms — p95=144ms over 2 min, 0 errors (full 10-min test deferred to Pi deployment)
- [ ] Trade pagination mechanism confirmed — **PARTIALLY FAILED**: Data API cursor params don't work; workaround is timestamp-windowed polling + transactionHash dedup (implemented in Phase 1b)
- [x] At least one game-state API (PandaScore, OpenDota, Riot, or NBA CDN) returns usable live data — OpenDota and NBA CDN both validated
- [x] Upcoming events with Polymarket markets exist across at least 2 sports — 511 match events across 10 sports

### Phase 1b Success Criteria — PASSED (code complete, fixture-tested)
- [x] All fixture-based tests pass (Polymarket client, game-state clients, DB operations) — 40/40 tests pass
- [x] Quality metrics (spread, book_depth_usd, is_empty) computed at ingest — verified in `test_polymarket_client.py` and `test_db.py`
- [x] `server_ts_ms` populated on every row type — verified in `test_db.py::test_server_ts_ms_not_null`
- [x] Trade deduplication works via UNIQUE constraint — verified in `test_db.py::test_trade_deduplication`
- [x] Trade watermark persists and updates — verified in `test_db.py::test_watermark_persistence`
- [x] Dota 2 client detects score_change, building_destroy, gold_lead_swing, game_end — verified in `test_game_state_clients.py`
- [x] NBA client emits score_change events from play-by-play — verified in `test_game_state_clients.py`
- [x] No duplicate events on repoll — verified in `test_game_state_clients.py::test_no_duplicate_events_on_repoll`

**Remaining criteria require live validation (Phase 1c):**
- [ ] Collector runs against a live match and captures data to SQLite without crashing
- [ ] Order book snapshots appear every ~3 seconds (p95 interval < 5s)
- [ ] No data gaps longer than 30 seconds during normal operation
- [ ] Trade watermark persists across collector restart — restarting mid-match resumes without gaps or mass duplicates
- [ ] Game-state events correlate with observable price movements when queried with `server_ts_ms` proximity join

### Phase 1c Success Criteria (the real test)
- [ ] 5+ matches collected across at least 2 sports with complete data
- [ ] SQLite DB queryable: order book snapshots around game events show price movement
- [ ] Game-state API update frequency measured: p50/p95 reported per sport
- [ ] Scheduling jitter measured: `local_mono_ns` deltas show actual polling interval distribution
- [ ] collection_runs table has accurate summary counts for each match
- [ ] Spread and depth distributions visible per market type — can distinguish liquid vs thin markets
- [ ] Markets with `is_empty = TRUE` for >50% of snapshots identifiable and excludable
