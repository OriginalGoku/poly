# Polymarket Live Event Volatility Trading

A data capture and analysis system that identifies and exploits emotional overreactions in [Polymarket](https://polymarket.com) prediction markets during live esports events.

## Thesis

During live sports and esports events, Polymarket odds swing dramatically in response to momentum shifts — often overshooting fair value due to emotional trading. This system captures order book and game-state data during live matches, validates the overreaction hypothesis statistically, and (if validated) executes trades to profit from the mean reversion.

**Not trying to beat the market on information** — exploiting behavioral mispricing in thin, emotionally-driven markets during live play.

## Project Phases

| Phase | Description | Status |
|---|---|---|
| **1. Data Capture** | Raspberry Pi collector for Polymarket order books + CS2 match state | Not started |
| **2. Analysis & Backtesting** | Fair-value modeling, overshoot validation, strategy simulation | Planned |
| **3. AI Supervisor** | Classification model + rules-based risk management | Planned |
| **4. Paper → Live Trading** | Paper trading, then small real positions | Planned |

## Architecture (Phase 1)

Single Python asyncio application with three components running on a Raspberry Pi:

```
┌─────────────────────────────────────────────────┐
│                  Orchestrator                    │
│   (schedule monitoring, collector lifecycle)     │
├────────────────────┬────────────────────────────┤
│  Polymarket        │  CS2 Match State           │
│  Collector         │  Collector                 │
│                    │                            │
│  CLOB API polling  │  PandaScore API / HLTV     │
│  every 3 seconds   │  round-by-round results    │
├────────────────────┴────────────────────────────┤
│              SQLite (WAL mode)                   │
│  order_book_snapshots | match_events | markets   │
│  data_gaps | ntp_checks | collector_health       │
└─────────────────────────────────────────────────┘
```

## Strategy Archetypes (Phase 2)

- **Mean Reversion** — Buy when market price diverges >N% from fair value, sell on convergence
- **Momentum Fade** — After a sharp price move, bet against it with a time delay
- **Underdog Ladder** — Small long on underdog pre-event, scale out during favorable momentum swings

## Tech Stack

- **Python 3.11+** — asyncio, aiohttp/httpx
- **SQLite** — WAL mode, lightweight storage on Pi
- **Polymarket CLOB API** — order book data
- **PandaScore API** — CS2 round-by-round match data (HLTV scraping as fallback)
- **Raspberry Pi** — always-on data collection

## Initial Focus: CS2 (Counter-Strike 2)

- High-frequency scoring events (rounds) = more data points per match
- Active Polymarket presence for major tournaments
- Well-studied round-win probability models
- Reliable data sources (PandaScore, HLTV)

## Project Structure

```
poly_market_v2/
├── plans/
│   └── Live_Event_Volatility_Trading_Plan.md   # Detailed project plan
├── README.md
```

## Getting Started

> Phase 1 implementation has not started yet. The steps below outline the pilot plan.

### Prerequisites

- Raspberry Pi with Python 3.11+ and WiFi
- PandaScore API key (free tier)
- NTP sync enabled (`timedatectl status` — ensure chronyd is active)

### Pilot Plan

1. Verify PandaScore free-tier covers upcoming CS2 events with Polymarket markets
2. Build and test Polymarket CLOB client for one known market
3. Build and test CS2 data client for a recent match
4. Deploy to Pi, run full capture on one live CS2 match
5. Analyze pilot data — validate completeness, timestamp alignment, storage estimates

### Pilot Success Criteria

- Order book snapshots captured every ~3 seconds for entire match duration
- No data gaps longer than 30 seconds
- CS2 round results captured within 5 seconds of actual round end
- Timestamp drift < 100ms over match duration
- Projected storage for 20+ matches fits on Pi

## Key Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Initial sport | CS2 | High-frequency rounds, good data availability |
| Polling rate | Fixed 3s | Simple, no adaptive phase-detection complexity |
| Data sources | One source per match, never blended | Avoids silent timestamp alignment errors |
| Timestamps | Triple (local mono, local wall, server) | Robust drift detection and post-hoc alignment |
| Infrastructure | Raspberry Pi | Always-on, zero cost, user-controlled |
| AI layer | Threshold + rules (not RL) | Simpler, interpretable, RL deferred as upgrade path |

## License

Private project — not open source.
