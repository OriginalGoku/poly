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

# Phase 1b collector
python -m collector --config configs/match_example.json
python -m collector --config configs/match_example.json --db data/custom.db

# Tests
python -m pytest tests/ -v
```

## Project structure

- `scripts/` — Phase 1a validation and discovery scripts
- `collector/` — Phase 1b async data collector
  - `models.py` — Dataclasses for order books, trades, match events, configs
  - `db.py` — SQLite schema + async write operations
  - `config.py` — Match config JSON loading and validation
  - `polymarket_client.py` — CLOB API order book polling + Data API trade polling
  - `game_state/base.py` — Abstract base class for sport-specific clients
  - `game_state/nba_client.py` — NBA CDN play-by-play event detection
  - `game_state/dota2_client.py` — OpenDota /live diff-based event detection
  - `__main__.py` — CLI entry point with asyncio event loop + graceful shutdown
- `configs/` — Auto-generated match configs from discovery + summary
- `tests/` — Fixture-based tests (40 tests)
- `tests/fixtures/` — Saved API response samples for fixture-based tests
- `plans/` — Implementation plans
- `data/` — SQLite databases (created at runtime, gitignored)

## Key API details

- **Polymarket batch books**: `POST /books` with JSON body `[{"token_id": "..."}]` (not GET)
- **Polymarket trades**: CLOB `/trades` requires API key auth; Data API (`data-api.polymarket.com/trades`) is keyless
- **Gamma API events**: Use `tag_slug` param on `/events` endpoint; markets are embedded in event response
- **Gamma API markets**: The `tag` and `event_slug` params on `/markets` endpoint don't filter properly — always use events endpoint instead

## Rate limits

| Endpoint | Limit |
|---|---|
| `/book` | 1,500 req/10s |
| `/books` | 500 req/10s |
| `/trades` | 200 req/10s |
| OpenDota | 60/min (no key), 1,200/min (with key) |
| PandaScore | 1,000 req/hr |
| Riot Games | 20 req/s (dev key) |
| NBA CDN | ~1 req/s (undocumented) |
