# TODOs

## Phase 1a follow-ups

- [ ] Validate PandaScore API (CS2) — need `PANDASCORE_TOKEN` env var
- [ ] Validate Riot Games API (LoL/Valorant) — need `RIOT_API_KEY` env var
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
- [ ] Validate trade watermark persistence across collector restart (REST mode only — may be dropped with WS migration)
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
- [ ] **⏳ Evaluate dual-write validation results** — 14 collectors running (2026-03-24). Run `python scripts/validate_dual_write.py` on each `*-VALIDATE.db`. Pass = WS ≥98%.
- [ ] Clean up after validation passes: remove `source` column, revert UNIQUE constraint, remove `--validate` flag
- [ ] Update `verify_collection.py` to report price_signals count
- [ ] Test Sports WS channel during live NBA game (deferred — no NBA data observed in spike)
- [x] ~~**Debug game state clients**~~ — fixed: added `lookup_game_id()` to auto-resolve NBA game ID from scoreboard at startup (configs had `external_id` empty)
- [x] ~~**Data fitness analysis**~~ — built `scripts/analyze_data_fitness.py`. Run on any DB to check coverage, liquidity, price dynamics, event readiness, and overall fitness score (0-100)
- [x] ~~**Wipe old data**~~ — deleted all 22 DBs (1.2 GB) on 2026-03-24. Previous data had 0 game events and trade-market mismatch, unusable for hypothesis testing
- [ ] **Commit current changes** — lookup_game_id fix, analyze_data_fitness.py, all doc updates (uncommitted)
- [ ] **Re-run dual-write validation** — fresh NBA games with `--validate` flag. Game events should now populate. Run `analyze_data_fitness.py` on results to verify fitness score ≥50
- [ ] After WS validation passes: drop REST trade polling entirely to fix trade-market mismatch (REST Data API returns event-wide trades, not per-token)
- [ ] Phase 3 analysis: focus on liquid tokens only (~22 per NBA game: moneyline, spread, O/U) — skip player props with >10c spreads
