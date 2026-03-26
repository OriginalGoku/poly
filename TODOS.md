# TODOs

## Phase 1a follow-ups

- [ ] Validate PandaScore API (CS2) — `PANDASCORE_TOKEN` obtained 2026-03-25 (free tier: 1,000 req/hr)
- [ ] Validate Riot Games API (LoL/Valorant) — `RIOT_API_KEY` obtained 2026-03-25 (dev key, expires every 24h; rate limits: 20 req/1s, 100 req/2min)
- [x] ~~Investigate Data API trade pagination~~ — superseded: WS `last_trade_price` provides full trade data without pagination issues
- [ ] Test batch sizes beyond 5 tokens (need more active markets to test 10-20 token batches for latency degradation)
- [ ] Run full 10-minute sustained polling test (`--full` flag) before Pi deployment
- [x] ~~Fix `discover_markets.py` filename generation~~ — fixed: sanitize match_id before building path

## Phase 1b

- [x] Build async collector (polymarket_client, game_state clients, db, models, config)
- [x] Fixture-based tests using saved API response samples from Phase 1a — 40 tests passing
- [x] SQLite schema implementation with WAL mode

## Phase 1c

- [x] Run collector against a live match and verify data capture — 6 matches across 3 sports, all checks pass
- [x] Measure order book polling interval distribution — avg 3.2s, zero >5s (p95 target met)
- [x] ~~Validate trade watermark persistence across collector restart~~ — moot: REST trade polling removed, WS captures trades directly
- [ ] Measure game-state API update frequency (p50/p95 per sport)
- [ ] Collect 5+ matches across at least 2 sports with full game-state events
- [ ] Add CS2 (PandaScore) and LoL (Riot) game-state clients once API keys obtained

## Phase 2 — WebSocket Architecture

See `plans/Phase2_WS_Architecture.md` for full plan and status.

- [x] ~~Build `collector/ws_client.py`~~ — WebSocket Market client with connect, subscribe, dispatch, heartbeat, reconnect, buffered writes
- [x] ~~Add `OrderBookSnapshot.from_ws()` and `Trade.from_ws()` factory methods to `models.py`~~
- [x] ~~Add `PriceSignal` dataclass and `from_ws()` for `best_bid_ask` events~~
- [x] ~~Add `price_signals` table to `db.py` schema~~
- [x] ~~Build `token_to_outcome` config mapping for deriving outcome/outcome_index from token_id~~
- [x] ~~Wire WS client into `__main__.py` (replace book/trade poller tasks)~~
- [x] ~~Fixture-based tests for WS parsing (book, trade, price signal)~~ — 31 WS tests, 71 total
- [x] ~~WS client dispatch and flush tests~~
- [x] ~~DB round-trip tests for price_signals~~
- [x] ~~Build dual-write validation infrastructure~~ — `--validate` flag, `source` column, `validate_dual_write.py`, Streamlit dashboard
- [x] ~~Evaluate Polymarket WebSocket feed~~ — completed 2026-03-24: spike confirmed full snapshots, full trade metadata, 88-token subscription works
- [x] ~~Obtain CLOB API key for trade pagination~~ — superseded: WS `last_trade_price` eliminates need for REST trade polling entirely
- [x] ~~**Evaluate dual-write validation results**~~ — WS captures 98.5-99.5% of configured-token trades across 4 NBA + 15 NHL games. Validation passed.
- [x] ~~Clean up after validation passes~~ — removed `--validate` flag, REST trade polling, and `run_rest_trade_poller()`. Kept `source` column for backward compat with 114 DBs.
- [ ] Update `verify_collection.py` to report price_signals count
- [x] ~~Test Sports WS channel during live NBA game~~ — superseded: Sports WS client built with league filtering + gameId lock-on, covers tennis/MLB/soccer/cricket. NBA uses dedicated CDN client.
- [x] ~~**Debug game state clients**~~ — fixed: added `lookup_game_id()` to auto-resolve NBA game ID from scoreboard at startup (configs had `external_id` empty)
- [x] ~~**Data fitness analysis**~~ — built `scripts/analyze_data_fitness.py`. Run on any DB to check coverage, liquidity, price dynamics, event readiness, and overall fitness score (0-100)
- [x] ~~**Wipe old data**~~ — deleted all 22 DBs (1.2 GB) on 2026-03-24. Previous data had 0 game events and trade-market mismatch, unusable for hypothesis testing
- [x] ~~**Drop REST trade polling**~~ — removed `poll_trades()`, `poll_books()`, `_fetch_trades()`, `_fetch_books()` from `polymarket_client.py`. Kept `fetch_market_metadata()` only.
- [x] ~~**WS stability fix**~~ — connection sharding (core/prop by question text, ≤25 tokens/shard), library ping frames (30s/10s), backoff reset only after data receipt
- [ ] Phase 3 analysis: focus on liquid tokens only (~22 per NBA game: moneyline, spread, O/U) — skip player props with >10c spreads
- [ ] Dashboard Phase 1: expand FastAPI with remaining endpoints (summary, markets, trades, gaps, depth, spike-candidates, heatmap); build monitoring + explore sections
- [ ] Dashboard Phase 2: overreaction heatmap, game timeline, multi-token cascade, spread dynamics, spike table, full depth ladder

## CBB (College Basketball) support

- [x] ~~**Sniff live Sports WS** during a CBB game~~ — confirmed 2026-03-25: Sports WS does NOT broadcast CBB. Observed leagues during live Nevada vs Auburn: `atp`, `challenger`, `mlb`, `nba`, `nhl`. No `ncaab`/`cbb` variant exists. CBB moved to control group (`data_source: "none"`)
- [ ] **Run `discover_markets.py`** and verify CBB markets classified as `sport: "cbb"`, `data_source: "none"` (no cross-contamination with NBA)
- [ ] **Collect a CBB game** for order book + trade data (no game state events expected)
- [ ] **Delete `scripts/sniff_sports_ws.py`** — temporary script, no longer needed
- [ ] **Investigate alternative CBB game state sources** if game state data becomes important for CBB analysis (ESPN, CBS, NCAA APIs)

## Manual verification — WS sharding (post-deploy)

Pre-collection smoke test (2026-03-25, local, 5 min) validated items marked ✅. Full production verification on Pi still needed for remaining items.

- [ ] **Deploy to Raspberry Pi** and collect one full evening with WS sharding fixes
- [x] ~~**Check logs**: verify shard names appear~~ — ✅ smoke test confirmed: `"WS [core] connected"`, `"WS [prop_1] connected"`, etc. (4 shards NBA, 1 shard NHL)
- [ ] **Check `data_gaps` table**: gaps should be dramatically fewer for NBA (hours between disconnects vs ~80s before); `collector` field should show shard names (e.g., `"ws_market"`) — smoke test showed 0 gaps in 5 min, need full-game validation
- [ ] **Check `match_events > 0`** for NHL games (verify game state config fix is working in production) — smoke test showed NHL game state poller active and polling, but 0 events pre-game (expected)
- [x] ~~**Compare snapshot/trade/signal counts**~~ — ✅ smoke test: 192 snapshots, 44 trades, 208 signals (NBA) + 46 snapshots, 16 trades, 578 signals (NHL) in 5 min
- [x] ~~**Verify no REST trades**~~ — ✅ all trades from WS (REST polling removed in Phase 2 cleanup)
- [ ] If collection is clean: proceed to Phase 3 event-price correlation analysis
