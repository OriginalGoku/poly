# Polymarket Live Event Volatility Trading

A data capture and analysis system that identifies and exploits emotional overreactions in [Polymarket](https://polymarket.com) prediction markets during live sports and esports events.

## Thesis

During live sports and esports events, Polymarket odds swing dramatically in response to momentum shifts — often overshooting fair value due to emotional trading. This system captures order book, trade history, and game-state data during live matches across multiple sports, validates the overreaction hypothesis statistically, and (if validated) executes trades to profit from the mean reversion.

**Not trying to beat the market on information** — exploiting behavioral mispricing in thin, emotionally-driven markets during live play.

## Project Phases

| Phase | Description | Status |
|---|---|---|
| **1. Data Capture** | Multi-sport collector for Polymarket order books, trades, and game state | WS pipeline built (127 tests); 114 databases collected across 5 sports |
| **2. Analysis & Backtesting** | Fair-value modeling, overshoot validation, strategy simulation | Starting |
| **3. AI Supervisor** | Classification model + rules-based risk management | Planned |
| **4. Paper → Live Trading** | Paper trading, then small real positions | Planned |

## Architecture

Single Python asyncio application with sharded WebSocket connections:

```
┌────────────────────────────────────────────────────────────┐
│                    CLI Entry Point                          │
├───────────────────────────────────┬────────────────────────┤
│  WS Sharded Clients              │  Game State Poller     │
│                                   │                        │
│  ┌─────────┐  ┌─────────┐        │  Sport-specific client │
│  │  core   │  │ prop_1  │  ...   │  (NBA, NHL, Dota2)     │
│  │ ≤25 tok │  │ ≤25 tok │        │  5-10s poll interval   │
│  └────┬────┘  └────┬────┘        │                        │
│       └──────┬─────┘              │                        │
│         shared queue              │                        │
│              │                    │                        │
│     ┌────────▼────────┐          │                        │
│     │  DB Writer Task  │          │                        │
├─────┴─────────────────┴──────────┴────────────────────────┤
│                   SQLite (WAL mode)                        │
│  order_book_snapshots | trades | price_signals             │
│  match_events | data_gaps | markets                        │
└────────────────────────────────────────────────────────────┘
```

## Supported Sports

| Sport | Game-State Source | Status | Event Granularity |
|---|---|---|---|
| NBA | NBA CDN (unofficial) | **Implemented** | Play-by-play (score, foul, turnover, challenge, timeout, quarter/game end) |
| NHL | NHL API | **Implemented** | Play-by-play (goals, penalties, periods) |
| Dota 2 | OpenDota | **Implemented** | Kills, objectives, tower/barracks |
| CS2 | — | Order book only | PandaScore deferred; Polymarket Sports WS planned |
| LoL | — | Order book only | Riot API deferred; Polymarket Sports WS planned |
| Valorant | — | Order book only | Riot API deferred; Polymarket Sports WS planned |
| Tennis, Soccer | — | Order book only | Polymarket Sports WS planned |
| Cricket, MLB, UFC, NFL | — | Control group | Order book only — no game state planned |

All sports with Polymarket markets are collected (order books + trades). Game-state events are captured for sports with implemented clients (see `collector/game_state/registry.py`).

## Strategy Archetypes (Phase 2)

- **Mean Reversion** — Buy when market price diverges >N% from fair value, sell on convergence
- **Momentum Fade** — After a sharp price move, bet against it with a time delay
- **Underdog Ladder** — Small long on underdog pre-event, scale out during favorable momentum swings

## Tech Stack

- **Python 3.12** — asyncio, httpx, websockets
- **SQLite** — WAL mode, lightweight per-match databases
- **Polymarket CLOB API** — order book snapshots (REST, being replaced by WS)
- **Polymarket WebSocket** — real-time order books, trades, and price signals (no auth)
- **Polymarket Data API** — trade history (keyless, ~1 req/s rate limit; being replaced by WS)
- **NBA CDN / NHL API / OpenDota** — implemented game-state clients (PandaScore, Riot deferred)

## Project Structure

```
poly_market_v2/
├── collector/                   # Async data collector
│   ├── __main__.py              # CLI entry point, asyncio event loop, graceful shutdown
│   ├── ws_client.py              # WebSocket Market client (book, trade, signal dispatch)
│   ├── polymarket_client.py     # REST: CLOB API (/books) + Data API (/trades)
│   ├── db.py                    # SQLite schema + async write operations (incl. price_signals)
│   ├── models.py                # Dataclasses + from_ws() factories for order books, trades, signals
│   ├── config.py                # Match config JSON loading + validation
│   └── game_state/
│       ├── registry.py          # Central registry of implemented data sources (single source of truth)
│       ├── base.py              # Abstract base class for sport-specific clients
│       ├── nba_client.py        # NBA CDN play-by-play + auto game ID lookup
│       ├── nhl_client.py        # NHL API play-by-play + auto game ID lookup
│       └── dota2_client.py      # OpenDota /live diff-based event detection
├── dashboard.py                   # Streamlit data inspector (signals, trades, books, validation)
├── configs/                     # Auto-generated match configs from discovery
├── scripts/
│   ├── validate_polymarket.py   # Phase 1a: CLOB/Data API validation
│   ├── validate_game_apis.py    # Phase 1a: game-state API validation
│   ├── discover_markets.py      # Phase 1a: market discovery across sports
│   ├── validate_dual_write.py   # Compare WS vs REST trade capture rates
│   ├── ws_research_spike.py     # WebSocket channel research spike
│   ├── verify_collection.py     # Post-match data quality verification
│   ├── analyze_data_fitness.py  # Data fitness analysis (coverage, liquidity, gaps)
│   └── run_tonight.sh           # Launch collectors for tonight's games
├── tests/                          # 110 tests (REST + WS parsing, dispatch, DB round-trip, registry)
│   └── fixtures/                # API response samples + WS message samples
├── plans/                       # Active implementation plans
├── old_plans/                   # Completed/superseded plans
├── data/                        # SQLite databases (gitignored)
├── requirements.txt
└── README.md
```

## Getting Started

### Prerequisites

- Raspberry Pi (or any machine) with Python 3.11+
- No API keys needed — all implemented sources (Polymarket, OpenDota, NBA CDN, NHL API) are keyless

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
| Trade capture | WS `last_trade_price` (replacing REST polling) | WS provides full trade metadata with zero rate limit issues |
| Quality metrics | Computed at ingest (spread, depth, staleness) | Makes Phase 2 analysis queries simple WHERE clauses |
| Market metadata | tick_size + min_order_size stored | Determines microtrade feasibility — tight spread means nothing if min_order_size is $50 |
| Multi-sport | Collect everything, sport-specific game-state clients | More data across more sports validates/invalidates thesis faster |
| Market discovery | Manual human step, logged in config | Automated fuzzy matching is error-prone; human verifies in 5 min |
| Orchestration | Manual CLI runs first, automate later | Prove the pipeline works before adding scheduling complexity |
| Timestamps | Triple (local mono, local wall, server) | Robust drift detection and post-hoc alignment |
| Data transport | WebSocket (replacing REST polling) | Sub-second price signals, no rate limits, full trade metadata |
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
