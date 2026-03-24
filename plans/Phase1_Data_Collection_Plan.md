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

### Phase 1a: Validate APIs (Day 1)

**Goal:** Confirm all APIs work as expected before writing the collector.

#### Step 1: Polymarket validation script

Create `scripts/validate_polymarket.py`:

1. `GET /book?token_id=<known_id>` — confirm response shape, parse all fields, note `timestamp` format, extract `tick_size` and `min_order_size`
2. `POST /books` with 2-3 token IDs — confirm array response with separate books per token
3. Sustained poll: `/books` every 3s for 10 minutes — record response latency p50/p95, check for throttling
4. Batch size test: `/books` with 10-20 token IDs — find practical limit before latency degrades
5. `GET /trades` for a known token — confirm response shape, pagination mechanism, fields available
6. Test trade pagination: confirm cursor/since parameter works for incremental fetching

**Output:** Print summary of response shapes, latency stats, pagination behavior, and any issues.

#### Step 2: Game-state API validation scripts

Create `scripts/validate_game_apis.py`:

1. **PandaScore (CS2):** Fetch upcoming matches, fetch round data for a recent completed match, confirm field availability
2. **OpenDota (Dota 2):** Hit `/live` endpoint for current matches, fetch a completed match with parsed data, confirm kill/objective timeline fields
3. **Riot API (LoL):** Fetch a recent pro match timeline, confirm frame-by-frame data, test LoL Esports API for live pro match state
4. **NBA CDN:** Fetch today's scoreboard, fetch play-by-play for a recent game, confirm possession-level detail

For any API with a live match available: poll every 5s for 10 minutes, measure update frequency.

**Output:** Per-API summary of coverage, field availability, update frequency stats.

#### Step 3: Market discovery script

Create `scripts/discover_markets.py`:

1. Hit Polymarket Gamma API `/events` to search for esports + sports events
2. For each event, enumerate all markets and their token IDs
3. Cross-reference with game-state APIs by team names + date where possible
4. Output match config JSON files ready for the collector
5. Report: how many matches across which sports have Polymarket markets in the next few days

**Output:** JSON config file(s) + summary of available events across all sports.

#### Gate

Proceed to Phase 1b if: at least one game-state API works AND there are upcoming events with Polymarket markets. Document what works and what doesn't for each API.

### Phase 1b: Build Collector (Day 2-3)

#### Project structure

```
poly_market_v2/
├── collector/
│   ├── __init__.py
│   ├── __main__.py              # CLI entry point, asyncio event loop
│   ├── polymarket_client.py     # CLOB API (/books) + Data API (/trades)
│   ├── game_state/
│   │   ├── __init__.py
│   │   ├── base.py              # Abstract base class for game-state clients
│   │   ├── cs2_client.py        # PandaScore
│   │   ├── dota2_client.py      # OpenDota
│   │   ├── lol_client.py        # Riot Games API
│   │   └── nba_client.py        # NBA CDN
│   ├── db.py                    # SQLite schema creation + write operations
│   ├── models.py                # Dataclasses for parsed API responses
│   └── config.py                # Config file loading + validation
├── configs/
│   └── match_example.json       # Template match config
├── scripts/
│   ├── validate_polymarket.py
│   ├── validate_game_apis.py
│   └── discover_markets.py
├── tests/
│   ├── fixtures/                # Saved API response samples per sport
│   ├── test_polymarket_client.py
│   ├── test_game_state_clients.py
│   └── test_db.py
├── plans/
├── README.md
└── requirements.txt
```

#### Game-state client interface

All sport-specific clients implement the same interface:

```python
class GameStateClient(ABC):
    sport: str                          # "cs2", "dota2", "lol", "nba"
    poll_interval_seconds: float        # 5s for esports, 10s for NBA

    async def poll(self) -> list[MatchEvent]:
        """Poll API, return new events since last poll."""

    async def close(self):
        """Cleanup."""
```

Each client tracks its own internal state (last known scores, cursor) and emits `MatchEvent` dataclasses with sport-specific fields stored in `raw_event_json`.

**Event types by sport:**

| Sport | Event Types |
|---|---|
| CS2 | round_end, map_end, match_end, side_switch, pause |
| Dota 2 | kill, tower_destroy, roshan_kill, barracks_destroy, game_end, match_end |
| LoL | kill, dragon_kill, baron_kill, tower_destroy, inhibitor_destroy, game_end, match_end |
| NBA | score_change, quarter_end, half_end, game_end, timeout |

#### Database schema

SQLite with WAL mode, `synchronous=NORMAL`.

```sql
CREATE TABLE markets (
    market_id TEXT PRIMARY KEY,
    condition_id TEXT,
    question TEXT,
    outcomes_json TEXT,       -- JSON array: ["FaZe", "NaVi"]
    token_ids_json TEXT,      -- JSON array: ["0x111...", "0x222..."]
    market_slug TEXT,
    tick_size REAL,           -- from /book response (e.g., 0.01)
    min_order_size REAL,      -- from /book response (e.g., 5.0)
    active BOOLEAN DEFAULT 1,
    created_at TEXT
);

CREATE TABLE market_match_mapping (
    market_id TEXT REFERENCES markets(market_id),
    match_id TEXT REFERENCES matches(match_id),
    relationship TEXT,        -- match_winner / map_1_winner / game_2_winner / total_rounds / over_under / etc
    PRIMARY KEY (market_id, match_id)
);

CREATE TABLE matches (
    match_id TEXT PRIMARY KEY,
    external_id TEXT,         -- PandaScore ID, OpenDota match ID, NBA game ID, etc.
    sport TEXT,               -- cs2 / dota2 / lol / nba / valorant / soccer / tennis / etc
    team1 TEXT,
    team2 TEXT,
    tournament TEXT,
    best_of INTEGER,          -- NULL for traditional sports
    scheduled_start TEXT,
    actual_start TEXT,
    end_time TEXT,
    status TEXT,              -- upcoming / live / completed
    data_source TEXT,         -- pandascore / opendota / riot / nba_cdn / none
    has_game_state BOOLEAN DEFAULT 0  -- whether game-state events are being collected
);

CREATE TABLE order_book_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT REFERENCES markets(market_id),
    token_id TEXT,
    local_ts TEXT,            -- ISO 8601 UTC wall clock
    local_mono_ns INTEGER,   -- time.monotonic_ns()
    server_ts TEXT,           -- from API response (may be NULL)
    fetch_latency_ms REAL,
    -- Top of book
    best_bid REAL,
    best_bid_size REAL,
    best_ask REAL,
    best_ask_size REAL,
    mid_price REAL,
    spread REAL,
    -- Full depth
    bid_depth_json TEXT,      -- [[price, size], ...] top 10 levels
    ask_depth_json TEXT,
    -- Quality metrics (computed at ingest)
    book_depth_usd REAL,      -- total $ depth within 5% of mid (both sides)
    is_empty BOOLEAN,         -- TRUE when bids or asks array is empty
    last_trade_price REAL,
    seconds_since_last_trade REAL  -- NULL on first snapshot, computed from last_trade_price changes
);

CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT,
    token_id TEXT,
    local_ts TEXT,
    trade_id TEXT UNIQUE,     -- deduplicate on this
    price REAL,
    size REAL,
    side TEXT,                -- BUY / SELL
    trade_ts TEXT             -- timestamp from API
);

CREATE TABLE match_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT REFERENCES matches(match_id),
    local_ts TEXT,
    server_ts TEXT,
    sport TEXT,               -- redundant with matches.sport but avoids join for common queries
    event_type TEXT,          -- sport-specific (see event types table above)
    -- Structured fields (populated where applicable, NULL otherwise)
    map_number INTEGER,       -- CS2/Valorant: map number
    map_name TEXT,            -- CS2/Valorant: map name
    round_number INTEGER,     -- CS2/Valorant: round number
    game_number INTEGER,      -- Dota2/LoL: game in series
    quarter INTEGER,          -- NBA: quarter number
    team1_score INTEGER,
    team2_score INTEGER,
    event_team TEXT,          -- team that triggered the event (round winner, scoring team, etc)
    ct_team TEXT,             -- CS2: which team is CT side
    -- Raw data
    raw_event_json TEXT       -- full API response for this event
);

CREATE TABLE data_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collector TEXT,           -- polymarket / trades / cs2 / dota2 / lol / nba
    gap_start TEXT,
    gap_end TEXT,
    reason TEXT
);

CREATE TABLE collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT,
    sport TEXT,
    start_time TEXT,
    end_time TEXT,
    config_json TEXT,         -- full config snapshot
    polymarket_snapshot_count INTEGER DEFAULT 0,
    trade_count INTEGER DEFAULT 0,
    match_event_count INTEGER DEFAULT 0,
    gap_count INTEGER DEFAULT 0,
    notes TEXT
);

-- Indexes
CREATE INDEX idx_obs_market_ts ON order_book_snapshots(market_id, local_ts);
CREATE INDEX idx_obs_token_ts ON order_book_snapshots(token_id, local_ts);
CREATE INDEX idx_events_match_ts ON match_events(match_id, local_ts);
CREATE INDEX idx_events_sport ON match_events(sport, event_type);
CREATE INDEX idx_trades_market_ts ON trades(market_id, local_ts);
CREATE INDEX idx_trades_dedupe ON trades(trade_id);
CREATE INDEX idx_matches_sport ON matches(sport, status);
```

#### Match config format

```json
{
  "match_id": "faze-vs-navi-2026-03-25",
  "external_id": "123456",
  "sport": "cs2",
  "team1": "FaZe Clan",
  "team2": "Natus Vincere",
  "tournament": "PGL Major 2026",
  "best_of": 3,
  "scheduled_start": "2026-03-25T18:00:00Z",
  "data_source": "pandascore",
  "markets": [
    {
      "market_id": "0xabc...",
      "question": "FaZe vs NaVi - Match Winner",
      "relationship": "match_winner",
      "outcomes": ["FaZe Clan", "Natus Vincere"],
      "token_ids": ["0x111...", "0x222..."]
    },
    {
      "market_id": "0xdef...",
      "question": "FaZe vs NaVi - Map 1 Winner",
      "relationship": "map_1_winner",
      "outcomes": ["FaZe Clan", "Natus Vincere"],
      "token_ids": ["0x333...", "0x444..."]
    }
  ]
}
```

For matches without game-state data (soccer, tennis, etc.), set `"data_source": "none"` — the collector will only run Polymarket tasks.

#### Collector behavior

**Entry point:** `python -m collector --config configs/match_faze_vs_navi.json`

**Concurrent async tasks:**

1. **Order book task (every 3s):**
   - `POST /books` with all token IDs from config
   - Parse each book → compute mid_price, spread, best bid/ask, book_depth_usd, is_empty
   - Compute seconds_since_last_trade by comparing last_trade_price to cached previous value
   - Buffer rows, flush every 10 rows or 30 seconds
   - On HTTP error: retry after 5s with fixed delay
   - If down >30s continuously: log data gap

2. **Trades task (every 15s):**
   - `GET /trades` for each token ID with cursor (last seen trade_id or timestamp)
   - Handle pagination: follow next page if available
   - Deduplicate by trade_id (skip if already in DB)
   - Insert new trades
   - Same error/gap handling as order book task

3. **Game-state task (sport-specific interval):**
   - Only runs if `data_source` is not `"none"`
   - Instantiate the appropriate `GameStateClient` based on `sport`
   - Poll at sport-specific interval (5s for esports, 10s for NBA)
   - Compare current state with last known state
   - On state change: insert match_event(s) with structured fields + raw JSON
   - Store raw_event_json for every detected event

**Lifecycle:**
- On start: create/open SQLite DB, fetch market metadata (tick_size, min_order_size) via `/book` for each market, insert collection_run + markets + match + mappings from config
- On SIGINT/SIGTERM: flush all buffers, update collection_run summary counts, close DB
- Structured JSON logging to `logs/` directory + stderr

#### Tests

Fixture-based tests using saved API response samples from Phase 1a:

- `test_polymarket_client.py` — parse order book response, quality metric computation, handle empty books, trade pagination/dedup
- `test_game_state_clients.py` — per-sport: parse API response, detect state changes, emit correct event types
- `test_db.py` — schema creation, insert/query round-trip, trade deduplication, gap logging, quality metric storage

### Phase 1c: Deploy & Collect (Day 3+)

1. SSH to Pi, clone repo, `pip install -r requirements.txt`
2. Run `scripts/discover_markets.py` to find upcoming events across all sports with Polymarket markets
3. Create config JSON for each match (human reviews mapping)
4. Run collector manually: `python -m collector --config configs/<match>.json`
5. Monitor logs in a second terminal
6. After match: run quick sanity queries on SQLite DB (snapshot count, event count, spread distribution, trade count)
7. Repeat for 2-3 matches across different sports
8. After confidence: add cron job or systemd timer for upcoming matches
9. Prioritize matches with game-state coverage (CS2, Dota 2, LoL, NBA) but also collect order-book-only for other sports

## Verification

### Phase 1a Success Criteria
- [ ] Polymarket `/books` returns valid order books for multiple token IDs in one request
- [ ] `tick_size` and `min_order_size` present in `/book` response
- [ ] Sustained 3s polling for 10 minutes shows no throttling and p95 latency < 500ms
- [ ] Trade pagination mechanism confirmed (cursor or since parameter works)
- [ ] At least one game-state API (PandaScore, OpenDota, Riot, or NBA CDN) returns usable live data
- [ ] Upcoming events with Polymarket markets exist across at least 2 sports

### Phase 1b Success Criteria
- [ ] All fixture-based tests pass (Polymarket client, game-state clients, DB operations)
- [ ] Collector runs against a live match and captures data to SQLite without crashing
- [ ] Order book snapshots appear every ~3 seconds (p95 interval < 5s)
- [ ] Quality metrics (spread, book_depth_usd, is_empty) populated on every snapshot
- [ ] No data gaps longer than 30 seconds during normal operation
- [ ] Trade deduplication works (no duplicate trade_ids in DB)
- [ ] Game-state events correlate with observable price movements when queried with timestamp proximity join

### Phase 1c Success Criteria (the real test)
- [ ] 5+ matches collected across at least 2 sports with complete data
- [ ] SQLite DB queryable: order book snapshots around game events show price movement
- [ ] Game-state API update frequency measured: p50/p95 reported per sport
- [ ] Scheduling jitter measured: `local_mono_ns` deltas show actual polling interval distribution
- [ ] collection_runs table has accurate summary counts for each match
- [ ] Spread and depth distributions visible per market type — can distinguish liquid vs thin markets
- [ ] Markets with `is_empty = TRUE` for >50% of snapshots identifiable and excludable
