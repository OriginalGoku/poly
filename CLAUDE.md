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

# Collector (WS mode — default)
python -m collector --config configs/<match>.json
python -m collector --config configs/<match>.json --db data/custom.db

# Collector with dual-write validation (WS + REST trades)
python -m collector --config configs/<match>.json --db data/<match>-VALIDATE.db --validate

# Check dual-write validation results
python scripts/validate_dual_write.py data/<match>-VALIDATE.db

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
  - `config.py` — Match config JSON loading and validation
  - `ws_client.py` — WebSocket Market client: subscribe, dispatch book/trade/signal events, heartbeat, reconnect with backoff
  - `polymarket_client.py` — REST: CLOB API order book polling + Data API trade polling (retained for dual-write validation)
  - `game_state/base.py` — Abstract base class for sport-specific clients
  - `game_state/nba_client.py` — NBA CDN play-by-play event detection; `lookup_game_id()` auto-resolves game ID from scoreboard
  - `game_state/dota2_client.py` — OpenDota /live diff-based event detection
  - `__main__.py` — CLI entry point with asyncio event loop + graceful shutdown (WS client + DB writer tasks)
- `dashboard.py` — Streamlit data inspector (price signals, trades, books, dual-write validation)
- `scripts/` — Validation, discovery, and utility scripts
  - `validate_dual_write.py` — Compare WS vs REST trade capture rates from `--validate` runs
  - `ws_research_spike.py` — WebSocket channel research spike (completed 2026-03-24)
  - `verify_collection.py` — Post-match data quality verification
  - `analyze_data_fitness.py` — Data fitness analysis: coverage, liquidity, spread distribution, gap detection
  - `run_tonight.sh` — Launch collectors for tonight's games
- `configs/` — Auto-generated match configs from discovery + summary
- `tests/` — Fixture-based tests (71 tests, including 31 WS tests)
- `tests/fixtures/` — Saved API response samples + WS message samples
- `plans/` — Active implementation plans
- `old_plans/` — Completed/superseded plans (kept for reference)
- `data/` — SQLite databases (created at runtime, gitignored)

## Key API details

- **Polymarket batch books**: `POST /books` with JSON body `[{"token_id": "..."}]` (not GET)
- **Polymarket trades**: CLOB `/trades` requires API key auth; Data API (`data-api.polymarket.com/trades`) is keyless
- **Polymarket WebSocket**: `wss://ws-subscriptions-clob.polymarket.com/ws/market` — no auth, subscribe with `{"assets_ids": [...], "type": "market", "custom_feature_enabled": true}`, PING every 10s
- **WS `book` events**: Full snapshots (not deltas). Initial subscribe returns JSON array of all books. Fields: `market`, `asset_id`, `timestamp`, `hash`, `bids[]`, `asks[]` (string price/size), `tick_size`, `event_type`, `last_trade_price`
- **WS `last_trade_price`**: Full trade metadata — `price`, `size`, `side`, `fee_rate_bps`, `transaction_hash`. Can populate `trades` table directly.
- **Gamma API events**: Use `tag_slug` param on `/events` endpoint; markets are embedded in event response
- **Gamma API markets**: The `tag` and `event_slug` params on `/markets` endpoint don't filter properly — always use events endpoint instead

## Rate limits

| Endpoint | Limit |
|---|---|
| `/book` (CLOB) | 1,500 req/10s |
| `/books` (CLOB) | 500 req/10s |
| `/trades` (CLOB) | 200 req/10s |
| Data API (`data-api.polymarket.com`) | ~1 req/s (undocumented, 429s above this) |
| WS Market channel | 88 tokens on one connection works; no known limit |
| OpenDota | 60/min (no key), 1,200/min (with key) |
| PandaScore | 1,000 req/hr |
| Riot Games | 20 req/s (dev key) |
| NBA CDN | ~1 req/s (undocumented) |

## Current phase

**Phase 2: WebSocket Architecture** — see `plans/Phase2_WS_Architecture.md`

All code built and tested (71 tests). **Previous data wiped (2026-03-24)** due to data quality issues. Need fresh collection with fixes applied.

### What was wrong with old data (wiped 2026-03-24):
- **match_events = 0 across ALL databases** — game state clients never ran because configs had `external_id: ""`. Fixed: `lookup_game_id()` now auto-resolves NBA game IDs from scoreboard at startup.
- **REST trade-market mismatch** — Data API returned event-wide trades (931 markets), not filtered to config (40 markets). Only 3/3900 trades matched. WS trades don't have this issue.
- **~66% of snapshots had >10c spread** — player props are illiquid. Focus analysis on ~22 liquid tokens per NBA game (moneyline, spread, O/U).

### Immediate next steps:
1. **Commit current changes** (lookup_game_id fix, analyze_data_fitness.py, run_tonight.sh, doc updates)
2. **Launch ALL collectors for tonight (2026-03-24)** — start ASAP, pre-game data is useful:
   ```bash
   bash scripts/run_tonight.sh           # all sports (~60+ collectors)
   bash scripts/run_tonight.sh --nba     # NBA only (4 games, has game state)
   bash scripts/run_tonight.sh --dry-run # list what would launch
   ```
   Sports tonight:
   - **NBA** (4 games, 7-11pm ET) — has game state via `lookup_game_id()` auto-resolution
   - **NHL** (4 games, 7-8pm ET) — price data only, no game state client
   - **Valorant** (7 matches) — price data only, some currently live
   - **Tennis** (~45 matches, ATP/WTA) — price data only, many currently live
3. **After games finish**, evaluate:
   - `python scripts/validate_dual_write.py data/*-VALIDATE.db` — WS ≥98%?
   - `python scripts/analyze_data_fitness.py data/*-VALIDATE.db` — fitness score ≥50?
   - NBA DBs should now have match_events > 0 (the critical fix)
4. If WS ≥98% AND fitness score ≥50: drop REST, clean up `source` column, move to Phase 3
5. If WS <98%: investigate gaps, fix, re-validate
