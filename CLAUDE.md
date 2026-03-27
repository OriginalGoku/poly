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
python -m collector --config configs/<match>.json --log-level DEBUG  # full third-party logs

# Streamlit data inspector dashboard (legacy)
streamlit run dashboard.py

# React dashboard (Phase 0.5+)
cd dashboard-next && npm run dev        # Next.js frontend on :3000
uvicorn api.main:app --reload --port 8000  # FastAPI data layer on :8000

# Tests
python -m pytest tests/ -v

# Post-match data quality verification
python scripts/verify_collection.py              # check all DBs in data/
python scripts/verify_collection.py data/nba-*.db # check specific DBs

# Data fitness analysis (coverage, liquidity, gaps)
python scripts/analyze_data_fitness.py                      # all DBs
python scripts/analyze_data_fitness.py data/nba-*.db        # specific DBs
python scripts/analyze_data_fitness.py --json               # JSON output

# Sync data + logs from Oracle VM
bash scripts/sync_from_cloud.sh
```

## Project structure

- `collector/` — Async data collector
  - `models.py` — Dataclasses for order books, trades, match events, price signals, configs; `from_ws()` factory methods
  - `db.py` — SQLite schema + async write operations (includes `price_signals` table)
  - `config.py` — Match config JSON loading/validation + `categorize_market()`/`build_token_shards()` for WS connection sharding (core vs prop markets, max 25 tokens per shard)
  - `ws_client.py` — WebSocket Market client: subscribe, dispatch book/trade/signal events, reconnect with backoff; supports shared queue + shard naming for multi-connection orchestration; library ping frames (30s/10s) for dead-connection detection
  - `polymarket_client.py` — CLOB API client for market metadata only (REST trade/book polling removed after WS validation)
  - `sports_ws_client.py` — WebSocket Sports API client (`wss://sports-api.polymarket.com/ws`): broadcast feed of live game state for all sports, league filtering + fuzzy team matching + gameId lock-on, event detection (game_start, score_change, period_change, game_end), dedicated MatchEvent queue; periodic diagnostic logging of observed leagues and target-league teams for debugging match failures
  - `game_state/registry.py` — Central registry of implemented data sources (single source of truth for config.py, __main__.py, discover_markets.py); includes `SPORTS_WS_SPORTS` set for Sports WS-covered sports
  - `game_state/base.py` — Abstract base class for sport-specific clients + `GameNotStarted` exception
  - `game_state/nba_client.py` — NBA CDN play-by-play event detection (score_change, foul, turnover, challenge, substitution, violation, timeout, quarter_end, game_end); `lookup_game_id()` auto-resolves game ID from scoreboard
  - `game_state/nhl_client.py` — NHL API play-by-play event detection; `lookup_game_id()` auto-resolves game ID from scoreboard
  - `game_state/dota2_client.py` — OpenDota /live diff-based event detection
  - `settings.py` — Project settings from `settings.json` (game state poll lead time)
  - `__main__.py` — CLI entry point with asyncio event loop + graceful shutdown (sharded WS clients + shared queue DB writer); three-state game poller (WAITING → BACKOFF → LIVE); `--log-level` flag (default INFO silences third-party loggers, DEBUG for full output)
- `api/` — FastAPI data layer for React dashboard
  - `main.py` — FastAPI app with CORS, 3 endpoints: `/databases`, `/db/{name}/signals`, `/db/{name}/event-windows`
  - `queries.py` — SQL queries, event-window alignment (asymmetric T-5s/T+120s, ±5s widening for local-quality timestamps), bps-from-baseline computation, LTTB-ready
- `dashboard.py` — Streamlit data inspector (legacy, still functional)
- `dashboard-next/` — React analytics dashboard (Next.js 16 + shadcn/ui + visx + TanStack Query)
  - Phase 0.5: event-aligned curve charts, annotation rail, confidence badges, dark theme
- `scripts/` — Validation, discovery, and utility scripts
  - `ws_research_spike.py` — WebSocket channel research spike (completed 2026-03-24)
  - `verify_collection.py` — Post-match data quality verification
  - `analyze_data_fitness.py` — Data fitness analysis: coverage, liquidity, spread distribution, gap detection
  - `sync_from_cloud.sh` — Rsync databases + logs from Oracle VM via SSH
  - `cloud_launch.sh` — Launch collectors on Oracle VM, each in its own tmux session (`col-<match_id>`)
  - `cloud_kill.sh` — Kill collectors by match_id, `--finished` (games with game_end), or `--all`
  - `run_tonight.sh` — Launch collectors for tonight's games (local/Pi use; for Oracle VM use `cloud_launch.sh`)
- `configs/` — Auto-generated match configs from discovery + summary
- `settings.json` — Self-documenting project settings (game state poll lead time)
- `tests/` — Fixture-based tests (259 tests, including 36 WS tests, 32 Sports WS tests, 25 API query tests, 21 delayed polling tests, 6 truncate_id tests, 13 discover tests)
- `tests/fixtures/` — Saved API response samples + WS message samples
- `plans/` — Active implementation plans
- `old_plans/` — Completed/superseded plans (kept for reference)
- `collection_logs/` — Structured collection session records
  - `README.md` — Collection Index table + Game State Coverage table
  - `_template_nightly.md` — Template for nightly collection logs
  - `_template_adhoc.md` — Template for ad-hoc collection logs
  - `YYYY-MM-DD.md` — Nightly collection logs (created by `/collection-tonight`)
  - `YYYY-MM-DD/` — Artifact directories (verify.txt, fitness.txt, commands.txt)
- `data/` — SQLite databases (created at runtime, gitignored; synced from Oracle VM via `scripts/sync_from_cloud.sh`)
- `logs/` — Collector log files (synced from Oracle VM via `scripts/sync_from_cloud.sh`)

## Key API details

- **Polymarket batch books**: `POST /books` with JSON body `[{"token_id": "..."}]` (not GET)
- **Polymarket trades**: CLOB `/trades` requires API key auth; Data API (`data-api.polymarket.com/trades`) is keyless
- **Polymarket WebSocket**: `wss://ws-subscriptions-clob.polymarket.com/ws/market` — no auth, subscribe with `{"assets_ids": [...], "type": "market", "custom_feature_enabled": true}`, library ping frames every 30s (no text PINGs)
- **WS `book` events**: Full snapshots (not deltas). Initial subscribe returns JSON array of all books. Fields: `market`, `asset_id`, `timestamp`, `hash`, `bids[]`, `asks[]` (string price/size), `tick_size`, `event_type`, `last_trade_price`
- **WS `last_trade_price`**: Full trade metadata — `price`, `size`, `side`, `fee_rate_bps`, `transaction_hash`. Can populate `trades` table directly.
- **Gamma API events**: Use `tag_slug` param on `/events` endpoint; markets are embedded in event response
- **Gamma API markets**: The `tag` and `event_slug` params on `/markets` endpoint don't filter properly — always use events endpoint instead
- **Polymarket Sports WebSocket**: `wss://sports-api.polymarket.com/ws` — no auth, no subscription, broadcasts all sports. Text `"ping"` → respond `"pong"`. Messages keyed by `gameId` (integer), `leagueAbbreviation`, `homeTeam`/`awayTeam`, `status`, `score`, `period`, `ended`, `eventState.updatedAt`. Used for tennis (ATP + Challenger), MLB, soccer, cricket, CBB (and potentially CS2, Valorant, LoL). Config `data_source: "polymarket_sports_ws"`.
- **Polymarket Sports WS observed leagues** (sniffed 2026-03-26): `atp`, `cbb`, `challenger`, `cs2`, `cwbb`, `fif`, `lol`, `mlb`, `nba`, `nhl`, `ufc`, `val`. New since 2026-03-25: `cwbb` (women's CBB), `ufc`, `fif` (FIFA/soccer), `lol` (League of Legends). **WTA is NOT broadcast** — only ATP and Challenger tennis leagues appear. Sports WS coverage expands over time — re-sniff if a new sport is needed.
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
| Riot Games | 20 req/1s, 100 req/2min (dev key, expires every 24h) |
| NBA CDN | ~1 req/s (undocumented) |

## Current phase

**Phase 2 → Phase 3 transition** — see `plans/Phase2_WS_Architecture.md`

Phase 2 WS validation passed (2026-03-25): WS captures 98.5-100% of config-token trades across 4 NBA + 15 NHL games. 114 databases collected, 5 sports, 127 tests passing. Hypothesis readiness: 5/5 checks passed. Phase 2 cleanup complete: REST trade poller and `--validate` flag removed, `polymarket_client.py` retained for metadata only, WS connection sharding deployed, `source` column kept for backward compat with 114 DBs.

### Data collection notes:
- **CS2**: Only 1 evening collected (2026-03-24, BC Game Masters BO1s). Markets were odd/even props — inherently illiquid. Need to collect during a major tournament (IEM, BLAST, tier-1 events) with match winner markets before drawing conclusions about CS2 viability.
- **CBB**: Polymarket Sports WS now broadcasts college basketball (observed 2026-03-25, `league=cbb`). CBB uses `data_source: "polymarket_sports_ws"` with game state via Sports WS. The `discover_markets.py` slug-based classification (`cbb-` prefix) correctly separates CBB from NBA.

### Pre-collection smoke test (2026-03-25):
5-minute parallel smoke test passed all checks before first real collection night. NBA OKC-BOS (84 tokens, 4 shards) + NHL NYR-TOR (14 tokens, 1 shard). Results: 192+46 snapshots, 44+16 trades, 208+578 price_signals, 0 gaps, 0 data quality issues. Log files 11 KB + 3.6 KB (vs 150 MB before log reduction). Third-party logger suppression confirmed (0 aiosqlite/websockets/httpcore lines). WS sharding correct (all shards ≤25 tokens). Game state pollers working: NBA backed off (pre-game), NHL resolved and polled normally. Low-activity prop shards (10 tokens) trigger 60s idle reconnect — handled correctly by reconnect logic.

### Immediate next steps:
1. **Collect one clean night** with WS sharding fixes, verify gap reduction
2. **Begin Phase 3: Analysis** — overshoot detection using price_signals + match_events
   - Use `server_ts_ms` for all event-price correlations
   - Asymmetric windows (T-5s to T+120s) to absorb cross-source clock drift
   - Focus on liquid tokens (~18-22 per NBA game)
   - 2,401+ spike candidates in DEN-PHX alone

### Cricket PSL collection (2026-03-26)
First cricket collection with verified pipeline: **Lahore Qalandars vs Hyderabad Kingsmen** (PSL, ~$104K volume). DB: `data/cricpsl-lah-hyd-2026-03-26.db`. Post-collection checks:
1. **Sports WS league match**: Verify `match_events > 0` — confirm PSL broadcasts as `psl` (or another abbreviation) on the Sports WS. If 0 events, check logs for league abbreviation mismatch and update `LEAGUE_MAP` in `sports_ws_client.py`.
2. **Score parsing**: Cricket scores are runs/wickets (e.g., "185/6") not simple "X-Y" — verify `team1_score`/`team2_score` parsed correctly or if they're `None`. May need cricket-specific score parsing.
3. **Trade volume**: Confirm trades captured during live play. Toss market token returned 404 on metadata (may be resolved already) — check if it affected data capture.
4. **Price signal density**: Compare signal interval to NBA/NHL baselines. Cricket matches are 3-4 hours — expect different cadence.
5. **Event types**: Check what event types the Sports WS produces for cricket (score_change, period_change for innings, game_end). Cricket has innings/overs — verify period detection works.
6. **Liquidity profile**: Run `analyze_data_fitness.py` on the DB to assess spread distribution and whether cricket markets are liquid enough for overshoot analysis.
