# CLAUDE.md

## Project overview

Polymarket live event volatility trading system. Captures order book snapshots, trade history, and game-state events during live sports/esports matches to validate an emotional overreaction hypothesis.

## Commands

```bash
# Setup
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Phase 1a validation scripts
python scripts/validate_polymarket.py
python scripts/validate_game_apis.py    # optional: PANDASCORE_TOKEN, RIOT_API_KEY
python scripts/discover_markets.py

# Collector (WS mode with connection sharding)
python -m collector --config configs/<match>.json
python -m collector --config configs/<match>.json --db data/custom.db

# Streamlit data inspector dashboard
streamlit run dashboard.py

# Tests
python -m pytest tests/ -v

# Post-match data quality verification
python scripts/verify_collection.py              # check all DBs in data/
python scripts/verify_collection.py data/nba-*.db # check specific DBs

# Data fitness analysis (coverage, liquidity, gaps)
python scripts/analyze_data_fitness.py                      # all DBs
python scripts/analyze_data_fitness.py data/nba-*.db        # specific DBs
python scripts/analyze_data_fitness.py --json               # JSON output
```

## Project structure

- `collector/` — Async data collector
  - `models.py` — Dataclasses for order books, trades, match events, price signals, configs; `from_ws()` factory methods
  - `db.py` — SQLite schema + async write operations (includes `price_signals` table)
  - `config.py` — Match config JSON loading/validation + `categorize_market()`/`build_token_shards()` for WS connection sharding (core vs prop markets, max 25 tokens per shard)
  - `ws_client.py` — WebSocket Market client: subscribe, dispatch book/trade/signal events, reconnect with backoff; supports shared queue + shard naming for multi-connection orchestration; library ping frames (30s/10s) for dead-connection detection
  - `polymarket_client.py` — CLOB API client for market metadata only (REST trade/book polling removed after WS validation)
  - `game_state/registry.py` — Central registry of implemented data sources (single source of truth for config.py, __main__.py, discover_markets.py)
  - `game_state/base.py` — Abstract base class for sport-specific clients
  - `game_state/nba_client.py` — NBA CDN play-by-play event detection (score_change, foul, turnover, challenge, substitution, violation, timeout, quarter_end, game_end); `lookup_game_id()` auto-resolves game ID from scoreboard
  - `game_state/nhl_client.py` — NHL API play-by-play event detection; `lookup_game_id()` auto-resolves game ID from scoreboard
  - `game_state/dota2_client.py` — OpenDota /live diff-based event detection
  - `__main__.py` — CLI entry point with asyncio event loop + graceful shutdown (sharded WS clients + shared queue DB writer)
- `dashboard.py` — Streamlit data inspector (price signals, trades, books)
- `scripts/` — Validation, discovery, and utility scripts
  - `ws_research_spike.py` — WebSocket channel research spike (completed 2026-03-24)
  - `verify_collection.py` — Post-match data quality verification
  - `analyze_data_fitness.py` — Data fitness analysis: coverage, liquidity, spread distribution, gap detection
  - `run_tonight.sh` — Launch collectors for tonight's games
- `configs/` — Auto-generated match configs from discovery + summary
- `tests/` — Fixture-based tests (127 tests, including 36 WS tests)
- `tests/fixtures/` — Saved API response samples + WS message samples
- `plans/` — Active implementation plans
- `old_plans/` — Completed/superseded plans (kept for reference)
- `data/` — SQLite databases (created at runtime, gitignored)

## Key API details

- **Polymarket batch books**: `POST /books` with JSON body `[{"token_id": "..."}]` (not GET)
- **Polymarket trades**: CLOB `/trades` requires API key auth; Data API (`data-api.polymarket.com/trades`) is keyless
- **Polymarket WebSocket**: `wss://ws-subscriptions-clob.polymarket.com/ws/market` — no auth, subscribe with `{"assets_ids": [...], "type": "market", "custom_feature_enabled": true}`, library ping frames every 30s (no text PINGs)
- **WS `book` events**: Full snapshots (not deltas). Initial subscribe returns JSON array of all books. Fields: `market`, `asset_id`, `timestamp`, `hash`, `bids[]`, `asks[]` (string price/size), `tick_size`, `event_type`, `last_trade_price`
- **WS `last_trade_price`**: Full trade metadata — `price`, `size`, `side`, `fee_rate_bps`, `transaction_hash`. Can populate `trades` table directly.
- **Gamma API events**: Use `tag_slug` param on `/events` endpoint; markets are embedded in event response
- **Gamma API markets**: The `tag` and `event_slug` params on `/markets` endpoint don't filter properly — always use events endpoint instead
- **NHL timestamps**: NHL API provides no absolute wall-clock timestamps (only game clock `timeInPeriod`). NHL events use `timestamp_quality="local"` with poll-time `server_ts_ms`. Per-event sortOrder offsets guarantee unique, monotonically increasing timestamps within a batch. Poll interval is 5s, so max timestamp error is ~5s. See `plans/NHL_Timestamp_Fix_Plan.md` for deferred live-anchoring design.

## Rate limits

| Endpoint | Limit |
|---|---|
| `/book` (CLOB) | 1,500 req/10s |
| `/books` (CLOB) | 500 req/10s |
| `/trades` (CLOB) | 200 req/10s |
| Data API (`data-api.polymarket.com`) | ~1 req/s (undocumented, 429s above this) |
| WS Market channel | ~25 tokens/connection stable (82 tokens = ~80s disconnects); shard to ≤25 |
| OpenDota | 60/min (no key), 1,200/min (with key) |
| PandaScore | 1,000 req/hr |
| Riot Games | 20 req/s (dev key) |
| NBA CDN | ~1 req/s (undocumented) |

## Current phase

**Phase 2 → Phase 3 transition** — see `plans/Phase2_WS_Architecture.md`

Phase 2 WS validation passed (2026-03-25): WS captures 98.5-100% of config-token trades across 4 NBA + 15 NHL games. 114 databases collected, 5 sports, 71 tests passing. Hypothesis readiness: 5/5 checks passed.

### Phase 2 cleanup (done):
1. Keep `source` column in trades (backward compat with 114 DBs) ✅
2. Remove `--validate` flag and REST trade poller ✅
3. Keep `polymarket_client.py` for metadata only ✅
4. WS stability fix: connection sharding (core/prop), library ping frames, backoff reset after data ✅

### Data collection notes:
- **CS2**: Only 1 evening collected (2026-03-24, BC Game Masters BO1s). Markets were odd/even props — inherently illiquid. Need to collect during a major tournament (IEM, BLAST, tier-1 events) with match winner markets before drawing conclusions about CS2 viability.

### Immediate next steps:
1. **Collect one clean night** with WS sharding fixes, verify gap reduction
2. **Begin Phase 3: Analysis** — overshoot detection using price_signals + match_events
   - Use `server_ts_ms` for all event-price correlations
   - Asymmetric windows (T-5s to T+120s) to absorb cross-source clock drift
   - Focus on liquid tokens (~18-22 per NBA game)
   - 2,401+ spike candidates in DEN-PHX alone
