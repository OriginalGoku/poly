# TODOs

## Phase 1a follow-ups

- [ ] Validate PandaScore API (CS2) — need `PANDASCORE_TOKEN` env var
- [ ] Validate Riot Games API (LoL/Valorant) — need `RIOT_API_KEY` env var
- [ ] Investigate Data API trade pagination — cursor params don't work; try timestamp windowing or obtain CLOB API key
- [ ] Test batch sizes beyond 5 tokens (need more active markets to test 10-20 token batches for latency degradation)
- [ ] Run full 10-minute sustained polling test (`--full` flag) before Pi deployment
- [x] ~~Fix `discover_markets.py` filename generation~~ — fixed: sanitize match_id before building path

## Phase 1b

- [x] Build async collector (polymarket_client, game_state clients, db, models, config)
- [x] Fixture-based tests using saved API response samples from Phase 1a — 40 tests passing
- [x] SQLite schema implementation with WAL mode

## Phase 1c

- [ ] Run collector against a live match and verify data capture
- [ ] Validate trade watermark persistence across collector restart
- [ ] Measure order book polling interval distribution (p95 < 5s target)
- [ ] Measure game-state API update frequency (p50/p95 per sport)
- [ ] Collect 5+ matches across at least 2 sports
- [ ] Add CS2 (PandaScore) and LoL (Riot) game-state clients once API keys obtained
