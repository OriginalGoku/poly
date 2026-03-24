# Phase 1c: Live Validation Plan

> Run the collector against real live matches, validate data quality, fix issues, then transition to WebSocket-based collection for sub-second resolution.

---

## Problem Statement

Phase 1b (async collector) is code-complete with 40/40 tests passing. Phase 1c validates the collector against real live events — confirming data quality, measuring polling intervals, and capturing game-state events that correlate with price movements. During initial live runs, we discovered rate limit issues and metric gaps that are now fixed, and identified WebSocket as a superior data transport.

## Current Status

### Completed
- 6 test collection runs across 3 sports (4 NBA, 1 CS2, 1 LoL), all quality checks pass
- 26,758 snapshots, 800 trades collected in ~5 min pre-game runs
- Polling interval: avg 3.2s, zero >5s (target met)
- Zero NULL timestamps, zero trade duplicates, zero data gaps
- Market metadata (tick_size=0.01, min_order_size=5.0) captured for all NBA markets

### Fixes Applied (this session)
1. **Trade overlap backfill**: Watermark looks back 60s instead of 1s, UNIQUE constraint deduplicates
2. **Trade saturation monitoring**: Warns when 100 trades returned (API max)
3. **429 retry**: Single retry with 2s backoff on rate limit errors
4. **Rate limiting**: 1s delay between sequential trade requests
5. **`inside_liquidity_usd`**: New metric — `best_bid * size + best_ask * size` — fixes zero-depth artifact for wide-spread markets (2,294 of 3,256 snapshots now correctly show available liquidity)
6. **NBA timestamp fallback**: Falls back to local clock when `timeActual` missing, with `timestamp_quality` field

### Remaining Phase 1c Criteria (from original plan)
- [ ] Collector runs through a full live match without crashing
- [ ] Game-state events captured and correlate with price movements
- [ ] Trade watermark persists across collector restart
- [ ] 5+ complete matches across at least 2 sports
- [ ] Spread and depth distributions visible per market type
- [ ] Game-state API update frequency measured (p50/p95)

## Implementation Plan

### Step 1: Run tonight's NBA games (REST collector)

4 NBA games tonight (2026-03-24):
- SAC @ CHA — 7:00 PM ET (gameId: 0022501047)
- NOP @ NYK — 7:30 PM ET (gameId: 0022501048)
- ORL @ CLE — 8:00 PM ET (gameId: 0022501049)
- DEN @ PHX — 11:00 PM ET (gameId: 0022501050)

**Run commands:**
```bash
# Each in a separate terminal
python -m collector --config configs/match_nba-sac-cha-2026-03-24.json
python -m collector --config configs/match_nba-nop-nyk-2026-03-24.json
python -m collector --config configs/match_nba-orl-cle-2026-03-24.json
python -m collector --config configs/match_nba-den-phx-2026-03-24.json
```

Or: `bash scripts/run_tonight.sh --all`

**Post-game:** `python scripts/verify_collection.py`

### Step 2: WebSocket research spike (can run in parallel with Step 1)

See `plans/WebSocket_Research_Spike_Plan.md`.

30-minute focused task: connect to Market + Sports channels, dump raw messages, answer all blocking payload questions, save fixtures.

### Step 3: Validate game-state events

After at least one NBA game completes:
```sql
-- Check game events were captured
SELECT event_type, COUNT(*) FROM match_events GROUP BY event_type;

-- Verify score progression
SELECT event_type, team1_score, team2_score, server_ts_ms, timestamp_quality
FROM match_events ORDER BY server_ts_ms;

-- Correlate events with price movements (the whole point)
SELECT me.event_type, me.team1_score, me.team2_score, me.server_ts_ms,
       obs.mid_price, obs.spread, obs.inside_liquidity_usd
FROM match_events me
JOIN order_book_snapshots obs ON obs.server_ts_ms BETWEEN me.server_ts_ms - 5000 AND me.server_ts_ms + 15000
WHERE obs.token_id = '<match_winner_token>'
ORDER BY obs.server_ts_ms;
```

### Step 4: WebSocket implementation

After research spike answers are in — see `plans/WebSocket_Migration_Plan.md` for full implementation plan.

### Step 5: Collect 5+ matches via WebSocket

Re-run collection for multiple matches using the new WS-based collector. Compare data quality (resolution, completeness, gaps) against REST-collected matches.

## Verification

All items from Phase 1c success criteria in the original plan, plus:
- [ ] Full NBA game collected with game-state events (score_change, quarter_end, game_end)
- [ ] Price movements visible around game events in proximity join query
- [ ] Trade saturation frequency measured during live games (how often does 100-trade limit hit?)
- [ ] WebSocket research spike completed with all questions answered
- [ ] WebSocket collector operational and collecting at sub-second resolution
- [ ] `verify_collection.py` passes on all collected match databases
