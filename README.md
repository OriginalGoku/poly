# Polymarket Live Event Volatility Trading

A data capture and analysis system that identifies and exploits emotional overreactions in [Polymarket](https://polymarket.com) prediction markets during live sports and esports events.

## Thesis

During live sports and esports events, Polymarket odds swing dramatically in response to momentum shifts — often overshooting fair value due to emotional trading. This system captures order book, trade history, and game-state data during live matches across multiple sports, validates the overreaction hypothesis statistically, and (if validated) executes trades to profit from the mean reversion.

**Not trying to beat the market on information** — exploiting behavioral mispricing in thin, emotionally-driven markets during live play.

## Project Phases

| Phase | Description | Status |
|---|---|---|
| **1. Data Capture** | Multi-sport Raspberry Pi collector for Polymarket order books, trades, and game state | In progress |
| **2. Analysis & Backtesting** | Fair-value modeling, overshoot validation, strategy simulation | Planned |
| **3. AI Supervisor** | Classification model + rules-based risk management | Planned |
| **4. Paper → Live Trading** | Paper trading, then small real positions | Planned |

## Architecture (Phase 1)

Single Python asyncio application with concurrent tasks running on a Raspberry Pi:

```
┌───────────────────────────────────────────────────────┐
│                   CLI Entry Point                      │
│         python -m collector --config <match>.json      │
├──────────────┬──────────────┬─────────────────────────┤
│  Order Book  │  Trades      │  Game State             │
│  Task        │  Task        │  Task                   │
│              │              │                         │
│  POST /books │  GET /trades │  Sport-specific client  │
│  every 3s    │  every 15s   │  (5-10s interval)       │
├──────────────┴──────────────┴─────────────────────────┤
│                  SQLite (WAL mode)                      │
│  order_book_snapshots | trades | match_events          │
│  markets | matches | market_match_mapping              │
│  collection_runs | data_gaps                           │
└───────────────────────────────────────────────────────┘
```

## Supported Sports

| Sport | Game-State Source | Event Granularity |
|---|---|---|
| CS2 | PandaScore | Round results, map scores |
| Dota 2 | OpenDota | Kills, objectives, tower/barracks |
| LoL | Riot Games API | Kills, dragons, barons, towers |
| NBA | NBA CDN (unofficial) | Play-by-play (every possession) |
| Valorant | Riot Games API | Round-by-round results |
| Soccer, Tennis, Hockey, etc. | Order book only | No game state — Polymarket data only |

All sports with Polymarket markets are collected (order books + trades). Game-state events are captured for sports with free APIs.

## Strategy Archetypes (Phase 2)

- **Mean Reversion** — Buy when market price diverges >N% from fair value, sell on convergence
- **Momentum Fade** — After a sharp price move, bet against it with a time delay
- **Underdog Ladder** — Small long on underdog pre-event, scale out during favorable momentum swings

## Tech Stack

- **Python 3.11+** — asyncio, aiohttp/httpx
- **SQLite** — WAL mode, lightweight storage on Pi
- **Polymarket CLOB API** — order book snapshots (top 10 depth levels)
- **Polymarket Data API** — executed trade history with cursor-based pagination
- **PandaScore / OpenDota / Riot Games / NBA CDN** — sport-specific game-state data
- **Raspberry Pi** — always-on data collection (100GB storage)

## Project Structure

```
poly_market_v2/
├── collector/                   # Phase 1b async data collector
│   ├── __main__.py              # CLI entry point, asyncio event loop, graceful shutdown
│   ├── polymarket_client.py     # CLOB API (/books) + Data API (/trades)
│   ├── db.py                    # SQLite schema + async write operations
│   ├── models.py                # Dataclasses for order books, trades, events, configs
│   ├── config.py                # Match config JSON loading + validation
│   └── game_state/
│       ├── base.py              # Abstract base class for sport-specific clients
│       ├── nba_client.py        # NBA CDN play-by-play event detection
│       └── dota2_client.py      # OpenDota /live diff-based event detection
├── configs/
│   └── discovery_summary.json   # Auto-generated market discovery results
├── scripts/
│   ├── validate_polymarket.py   # Phase 1a: CLOB/Data API validation
│   ├── validate_game_apis.py    # Phase 1a: game-state API validation
│   └── discover_markets.py      # Phase 1a: market discovery across sports
├── tests/
│   ├── fixtures/                # Saved API response samples from validation
│   ├── test_polymarket_client.py
│   ├── test_game_state_clients.py
│   └── test_db.py
├── plans/
│   └── Phase1_Data_Collection_Plan.md
├── requirements.txt
└── README.md
```

## Getting Started

### Prerequisites

- Raspberry Pi (or any machine) with Python 3.11+
- PandaScore API key (free tier — 1,000 req/hr)
- Riot Games API dev key (for LoL/Valorant — 20 req/s)
- No API key needed for: Polymarket, OpenDota, NBA CDN

### Phase 1 Implementation

Phase 1 is split into three sub-phases:

**1a — Validate APIs:** Run validation scripts to confirm all APIs return expected data and can sustain polling rates.

**1b — Build Collector:** Implement the asyncio collector with order book, trade, and game-state tasks. Fixture-based tests using saved API responses from 1a.

**1c — Deploy & Collect:** Manual CLI runs per match, starting with 2-3 matches across different sports. Automate scheduling after confidence is established.

### Setup

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
```

### Running Phase 1a Validation

```bash
# Validate Polymarket APIs (no keys needed)
python scripts/validate_polymarket.py          # 2-min sustained test
python scripts/validate_polymarket.py --full   # 10-min sustained test

# Validate game-state APIs (set env vars for optional APIs)
export PANDASCORE_TOKEN=...   # optional: CS2 data
export RIOT_API_KEY=...       # optional: LoL/Valorant data
python scripts/validate_game_apis.py

# Discover upcoming events with Polymarket markets
python scripts/discover_markets.py

# Run collector for a match
python -m collector --config configs/<match>.json

# Run tests
python -m pytest tests/ -v
```

## Key Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Market scope | All markets per match, no pre-filtering | Don't know which markets overshoot most; filter in Phase 2 |
| Order book depth | Full top 10 levels | Phase 2 fill simulation needs depth data; storage is cheap |
| Trade capture | Cursor-based polling every 15s | Order books show liquidity; trades show what executed |
| Quality metrics | Computed at ingest (spread, depth, staleness) | Makes Phase 2 analysis queries simple WHERE clauses |
| Market metadata | tick_size + min_order_size stored | Determines microtrade feasibility — tight spread means nothing if min_order_size is $50 |
| Multi-sport | Collect everything, sport-specific game-state clients | More data across more sports validates/invalidates thesis faster |
| Market discovery | Manual human step, logged in config | Automated fuzzy matching is error-prone; human verifies in 5 min |
| Orchestration | Manual CLI runs first, automate later | Prove the pipeline works before adding scheduling complexity |
| Timestamps | Triple (local mono, local wall, server) | Robust drift detection and post-hoc alignment |
| Infrastructure | Raspberry Pi | Always-on, zero cost, user-controlled |
| AI layer (Phase 3) | Threshold + rules (not RL) | Simpler, interpretable; RL deferred as upgrade path |

## Key Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Edge is real but too thin after spread costs | Medium | Polymarket has 0% maker/taker fees — spread IS the entire cost. Sensitivity analysis in Phase 2 |
| PandaScore/game APIs don't cover events with Polymarket markets | High | Validate in Phase 1a before writing collector code |
| Effect size too small / needs more data | Medium | Go/no-go gate at 20 matches; extend collection if needed |
| SD card wear from continuous writes | Low | USB SSD for long-term; SD fine for pilot |
| WiFi drops on Pi | Low | Auto-reconnect + data_gaps tracking |

## License

Private project — not open source.
