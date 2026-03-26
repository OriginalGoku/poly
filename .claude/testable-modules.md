# Testable Modules
<!-- Auto-maintained by /test-epilogue — do not edit manually -->

- api/analysis.py — Team mapping, market classification, event-to-token linking, NHL event deduplication
- api/queries.py — SQL queries, sport guessing, token label building, event-window alignment with bps-from-baseline
- collector/__main__.py — truncate_id helper for shortening long token IDs and tx hashes in logs
- collector/config.py — Config loading, validation, market categorization (core/prop), and token shard building
- collector/game_state/registry.py — Central registry constants for implemented data sources
- collector/game_state/nba_client.py — NBA CDN play-by-play polling with score, foul, turnover, challenge, substitution, violation events
- collector/game_state/nhl_client.py — NHL play-by-play polling with per-event timestamp differentiation
- collector/models.py — Dataclasses with from_api/from_ws factory methods for parsing API/WS data
- collector/sports_ws_client.py — Sports WS client with score parsing, timestamp parsing, league filtering, fuzzy team matching, gameId lock-on, event detection
- collector/ws_client.py — WS message dispatch, imbalance tracking, batch flushing, shared queue support, shard naming
- scripts/discover_markets.py — Sport classification from title/tags/slug, team extraction, market relationship guessing
