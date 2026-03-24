# Live Event Volatility Trading — Polymarket

> Build a data capture and analysis system to exploit emotional overreactions in Polymarket prediction markets during live CS2 esports events, starting with a Raspberry Pi-based data collector and progressing through backtesting to live trading.

---

## Problem Statement

On Polymarket, during live sports and esports events, odds swing dramatically in response to momentum shifts (goals scored, rounds won). These swings often appear to overshoot "fair value" — driven by emotional/fan-driven trading rather than rational probability updates. The hypothesis is that a system can enter trades at or near event start, capture profit from these emotional swings (mean reversion, momentum fade, underdog ladder), and exit during or after the event.

This is NOT about beating the market on information. It's about profiting from behavioral mispricing in thin, emotionally-driven markets during live play.

## Design Decisions

### D1: CS2 as the initial sport

**Decision:** Focus exclusively on Counter-Strike 2 for the pilot and initial data collection.

**Rationale:** High-frequency scoring events (rounds) produce more data points per match. Active Polymarket presence for major tournaments. HLTV and PandaScore provide detailed round-by-round data. Round-win probability models are well-studied in CS2 analytics.

**Trade-off:** Could have started with basketball (also high-frequency scoring), but CS2 has better community data tools and simpler game-state modeling (discrete rounds vs continuous play).

### D2: Raspberry Pi as collection infrastructure

**Decision:** Run the data collector on an always-on Raspberry Pi.

**Rationale:** Zero cost, always on, low power, user already has one running. More reliable than free cloud tiers with sleep/timeout behavior.

**Constraints:** SD card write wear (mitigated by USB SSD for long-term), WiFi network drops (mitigated by auto-reconnect logic), limited RAM (mitigated by lightweight Python + SQLite).

### D3: Fixed 3-second polling interval (no adaptive rate)

**Decision:** Poll Polymarket order book every 3 seconds for the entire match duration, no adaptive rate changes.

**Rationale:** Eliminates complexity of detecting match phases (active play vs pause). Adaptive polling requires real-time phase detection from the same endpoints being rate-limited — creates a circular dependency. 3-second interval is sufficient for minute-scale trading strategies.

**Trade-off:** Wastes some requests during halftime/pauses. Acceptable for simplicity and reliability.

### D4: Single data source per match (no blending)

**Decision:** Use one CS2 data source per match (PandaScore preferred, HLTV scraping as fallback). Never blend sources for the same match.

**Rationale:** Mixing sources creates silent timestamp alignment errors that are worse than data gaps. Each source has different event semantics and timing. Record which source was used in match metadata.

### D5: Triple timestamp strategy

**Decision:** Record three timestamps per data point: local monotonic clock, local wall clock, server timestamp from API response.

**Rationale:** Local clocks drift. Server timestamps may have their own issues. Recording all three allows post-hoc alignment during analysis and drift detection. chronyd on Pi provides sub-millisecond NTP sync.

### D6: No raw response storage in main schema

**Decision:** Do not store raw API responses in the main order_book_snapshots table. Optional raw storage in a separate file/table, disabled by default.

**Rationale:** Reduces SD card write amplification. For pilot, parsed fields are sufficient. Raw storage can be enabled via config flag when running on USB SSD for long-term collection.

### D7: Replace RL agent with threshold-based policy

**Decision:** Phase 3 AI layer uses a classification model + rules-based risk management instead of reinforcement learning.

**Rationale:** RL is over-engineering for a solo developer at this stage. Threshold-based policy (divergence > N% triggers entry, configurable position limits, cooldown after losses) is simpler, more interpretable, and easier to debug. RL remains an optional upgrade path after basic strategy proves profitable.

### D8: Explicit data gap tracking

**Decision:** Maintain a dedicated data_gaps table that logs every collection interruption with timestamps and reason.

**Rationale:** Critical for honest backtesting. Without knowing where data is missing, analysis could mistake gaps for market behavior. Gaps are inevitable on WiFi/Pi — better to track them than pretend they don't exist.

## Project Phases

### Phase 1: Data Capture (current focus)

Build and deploy a Python data collector on the Raspberry Pi.

#### Architecture

Single Python asyncio application with three components:

1. **Polymarket Collector** — polls CLOB API order book every 3 seconds during matches
2. **CS2 Match State Collector** — polls PandaScore (or scrapes HLTV) for round-by-round results
3. **Orchestrator** — monitors schedules, activates/deactivates collectors, manages health

#### Data Schema (SQLite, WAL mode)

```sql
-- Market metadata
markets (
    market_id TEXT PRIMARY KEY,
    event_slug TEXT,
    match_id TEXT,
    question TEXT,
    outcome TEXT,
    token_id TEXT
)

-- Order book snapshots (every 3 seconds during match)
order_book_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT,
    local_ts TIMESTAMP,          -- local wall clock
    local_mono_ts REAL,          -- monotonic clock (for drift detection)
    server_ts TIMESTAMP,         -- from API response
    fetch_latency_ms INTEGER,
    mid_price REAL,
    spread REAL,
    best_bid REAL,
    best_ask REAL,
    bid_depth_json TEXT,         -- JSON array of [price, size], top 10 levels
    ask_depth_json TEXT,         -- JSON array of [price, size], top 10 levels
    last_trade_price REAL,
    last_trade_size REAL
)

-- Match metadata
matches (
    match_id TEXT PRIMARY KEY,
    team1 TEXT,
    team2 TEXT,
    tournament TEXT,
    best_of INTEGER,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    status TEXT,                  -- upcoming/live/completed
    data_source TEXT              -- pandascore/hltv (one source per match)
)

-- Round-by-round events
match_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT,
    local_ts TIMESTAMP,
    server_ts TIMESTAMP,
    event_type TEXT,              -- round_end/map_end/match_end/timeout/pause
    round_number INTEGER,
    map_number INTEGER,
    map_name TEXT,
    team1_score INTEGER,
    team2_score INTEGER,
    round_winner TEXT,
    team1_ct_side BOOLEAN
)

-- Collection gap tracking
data_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collector TEXT,
    gap_start TIMESTAMP,
    gap_end TIMESTAMP,
    reason TEXT
)

-- NTP drift monitoring
ntp_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP,
    offset_ms REAL,
    source TEXT
)

-- Component health log
collector_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP,
    component TEXT,
    status TEXT,
    message TEXT
)
```

#### Key Implementation Details

- Python 3.11+, asyncio, aiohttp/httpx, SQLite
- Auto-reconnect on network drops with exponential backoff
- Rate limit handling (429 → backoff, log gap)
- Orchestrator activates collectors 10-15 min before scheduled match start
- Generous timeout after expected end (2 hours past scheduled start for BO3)
- systemd service for auto-start on Pi boot
- Log rotation to prevent disk fill
- Config file for: polling intervals, market IDs, API keys, data directory

#### Pilot Plan

1. Verify PandaScore free-tier covers upcoming CS2 events with Polymarket markets
2. Build Polymarket CLOB client, test order book fetch for one known market
3. Build CS2 data client, test round data for a recent match
4. Wire orchestrator + SQLite, deploy to Pi, run on one live match
5. Analyze: data completeness, timestamp alignment, storage estimates

### Phase 2: Analysis & Backtesting

**Prerequisite:** Sufficient data from Phase 1 (target: 20+ matches with complete data).

- Compute fair value model (CS2 round-win probability based on score, map, side)
- Measure delta between market price and fair value per round across all collected matches
- Statistical validation: after >10% divergence, what's the price distribution 5/10/15 min later?
- Decision gate: if effect size CI doesn't cross profitability threshold after 20 matches, re-evaluate thesis
- Backtest three strategies against historical data:
  - **Mean Reversion:** buy when market diverges >N% from fair value, sell on convergence
  - **Momentum Fade:** after sharp move, bet against it with time delay
  - **Underdog Ladder:** small long on underdog pre-event, scale out during favorable swings
- Fill simulation using recorded order book depth (fill at worst quoted price for position size)
- Sensitivity analysis across 1x-3x slippage multipliers
- P&L accounting including Polymarket fees

### Phase 3: AI Supervisor Layer

**Prerequisite:** At least one strategy shows positive expected value in backtesting.

- Classification model: given game state features, predict whether current price will revert toward fair value within N minutes
- Rules-based risk management: max position size, max loss per event, cooldown after consecutive losses
- Anomaly detection: flag when market behavior deviates from historical patterns (informed trading, unusual liquidity)
- RL agent deferred as optional future upgrade

### Phase 4: Paper Trading → Live

**Prerequisite:** Positive results from Phase 3 paper trading.

- Paper trade against live Polymarket data using the full pipeline
- Measure: Sharpe ratio, max drawdown, win rate, profit factor
- Graduate to small real positions if metrics hold over 20+ events

## Key Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Polymarket CLOB API doesn't support sustained 3s polling | High | Pilot test validates this before any model work |
| PandaScore doesn't cover CS2 events with Polymarket markets | High | Verify before writing code; HLTV scraping as fallback |
| Edge is real but too thin after fees/slippage | Medium | Sensitivity analysis in backtesting; kill switch if unprofitable |
| SD card wear from continuous writes | Low | USB SSD for long-term collection; SD fine for pilot |
| WiFi drops on Pi | Low | Auto-reconnect + data_gaps tracking |
| Effect size too small / needs more data than expected | Medium | Decision gate at 20 matches; extend collection if needed |

## Verification

### Phase 1 Pilot Success Criteria
- [ ] Polymarket order book snapshots captured every ~3 seconds for entire match duration
- [ ] No gaps longer than 30 seconds (excluding deliberate pauses)
- [ ] CS2 round results captured with timestamps within 5 seconds of actual round end
- [ ] Server timestamps and local timestamps drift < 100ms over match duration
- [ ] SQLite database queryable: can join snapshots with match events on timestamp proximity
- [ ] Storage estimate extrapolated: projected DB size for 20+ matches fits on Pi storage

### Phase 2 Go/No-Go Gate
- [ ] After 20 matches: overshoot-reversion pattern has statistically significant effect size
- [ ] At least one strategy shows positive expected value after 2x slippage assumption
