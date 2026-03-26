# Esports API Probe Plan

> Evaluate PandaScore (CS2) and Riot (LoL/Valorant) APIs during live matches on 2026-03-26 to determine game state data viability before building full clients.

---

## Problem Statement

The collector has working game state clients for NBA, NHL, Dota2, and Sports WS-covered sports, but CS2 and Valorant matches run market-data-only (no game state). We have `PANDASCORE_TOKEN` and `RIOT_API_KEY` configured but no clients to use them. Tomorrow (2026-03-26) has 2 CS2 and 9 Valorant matches on Polymarket — an opportunity to probe these APIs during live games and determine what data they actually provide.

## Design Decisions

### D1: Standalone probes, not full GameStateClient implementations

**Decision:** Create lightweight probe scripts that log raw API responses to JSONL, running alongside existing collectors.

**Rationale:** Building full `GameStateClient` subclasses requires understanding the API's live data shape, timestamp quality, and update frequency — exactly what we don't know yet. Probes answer these questions with ~30% of the effort.

**Trade-off:** Probes don't feed into the collector's MatchEvent pipeline, so we can't do real-time event-price correlation on this data. That's acceptable for a first collection.

### D2: PandaScore is the primary probe target, not Riot

**Decision:** Focus probe effort on PandaScore. Defer the Riot probe.

**Rationale:** Critical review found that the LoL Esports API (`esports-api.lolesports.com`) is LoL-specific and does NOT cover Valorant. There is no confirmed Valorant esports API endpoint. PandaScore is a multi-esport provider and may cover both CS2 AND Valorant under a single API — a `GET /videogames` call will confirm.

**Trade-off:** The Riot API dev key (24h expiry) adds operational risk. LoL matches don't start until 2026-03-28 anyway, so the Riot probe can wait.

### D3: Verify Sports WS esports coverage as a cheaper alternative

**Decision:** Re-sniff the Polymarket Sports WS during live esports matches before investing in external API clients.

**Rationale:** The Sports WS registry (`SPORTS_WS_SPORTS`) already lists cs2, valorant, lol, but the 2026-03-25 sniff only found traditional sports leagues. If Polymarket adds esports broadcasts, Sports WS becomes the simplest game state path — no new client needed, just a config change.

### D4: Valorant may become a control group

**Decision:** If PandaScore doesn't cover Valorant and Sports WS doesn't broadcast it, treat Valorant as market-data-only (control group).

**Rationale:** VCL-tier Valorant matches have low volume ($5-8K). Building a scraping solution for game state has poor ROI compared to focusing on sports/esports with working APIs.

## Implementation Plan

### Step 1: `scripts/probe_pandascore.py` (~180 lines, create new)

Standalone async script with three modes:

**Discovery mode** (`--discover`):
- `GET /videogames` to list all supported games
- `GET /valorant/matches/upcoming` to test if Valorant endpoints exist (may 404)
- `GET /csgo/matches/upcoming` to confirm CS2 (known working)
- Print results and exit — run this FIRST to resolve the Valorant question

**CS2 probe mode** (default):
- Load `PANDASCORE_TOKEN` from `.env` via `dotenv`
- Resolve PandaScore match IDs from `/csgo/matches/upcoming` for Alliance vs ReThink and EYEBALLERS vs Young Ninjas (fuzzy team name matching)
- Pre-match: `GET /csgo/matches/running` every 30s, watching for target matches
- Live: `GET /csgo/matches/{id}` every 10s once a target appears (fits 1000 req/hr limit)
- Also try `GET /csgo/games/{game_id}` per-game endpoint for richer round data
- Post-match: Final `GET /csgo/matches/{id}` for complete round data
- Output: JSONL to `data/pandascore_probe_2026-03-26.jsonl`, complete JSON to `data/pandascore_match_{id}_complete.json`
- Print summary: polls, state changes, timestamp fields, round-level data availability

**Valorant probe mode** (`--valorant`, only if discovery confirms support):
- Same pattern hitting `/valorant/matches/*` endpoints

Key questions the probe answers:
- Does PandaScore provide `rounds[]` during live "running" matches or only post-game?
- What timestamp fields exist? (`begin_at`, `end_at`, `modified_at` on match/games/rounds)
- What is poll-to-update latency?
- Does PandaScore cover Valorant?

Reference: `scripts/validate_game_apis.py` lines 25-108 (existing PandaScore API patterns)

### Step 2: Sports WS re-sniff (zero new code)

Reuse existing `scripts/sniff_sports_ws.py` during live esports match time. Check for `cs2`, `valorant`, or similar league abbreviations in the broadcast.

### Step 3: `scripts/run_esports_2026_03_26.sh` (~80 lines, create new)

Launch script following `scripts/run_tonight.sh` pattern:

- Layer 1: Standard collectors for top 4 matches (market data, no game state):
  - `configs/match_cs2-all-rt1-2026-03-26.json` (Alliance vs ReThink, $4K vol, 11 mkts)
  - `configs/match_cs2-eye-yn-2026-03-26.json` (EYEBALLERS vs Young Ninjas, $1K vol, 11 mkts)
  - `configs/match_val-art-osg-2026-03-26.json` (ARETE vs ONSIDE GAMING, $8K vol, 5 mkts)
  - `configs/match_val-t1a-drx-2026-03-26.json` (T1A vs DRX Academy, $5K vol, 5 mkts)
- Layer 2: PandaScore probe (CS2 guaranteed, Valorant if supported)
- Layer 3: Sports WS sniff

Includes `--dry-run`, PID tracking, monitor/stop instructions.

### Step 4 (deferred): Riot LoL probe

Only needed when LoL matches appear on Polymarket (first: 2026-03-28). Uses public LoL Esports API key (no expiry). Not part of tomorrow's collection.

## Verification

1. **Pre-flight:** `python scripts/probe_pandascore.py --discover` — verify API connectivity and Valorant coverage
2. **Pre-flight:** `python scripts/probe_pandascore.py --dry-run` — verify match ID resolution
3. **During matches:** `tail -f data/pandascore_probe_*.jsonl` — confirm polls are flowing
4. **Post-match analysis:**
   - Were `rounds[]` populated during live "running" status?
   - What timestamp fields exist on rounds/games?
   - Compare PandaScore event times to Polymarket `price_signals` timestamps in the SQLite DBs
5. **Market data:** `python scripts/verify_collection.py data/*2026-03-26*.db`

## Decision Tree After Collection

```
PandaScore --discover:
+-- Valorant supported --> build unified PandaScore client for CS2 + Valorant
+-- Valorant NOT supported -->
    +-- Sports WS sniff finds esports leagues --> use Sports WS for Valorant
    +-- No Valorant API available --> Valorant = control group (market-only)

PandaScore live round data:
+-- rounds[] populated during "running" --> build full GameStateClient
|   Events: round_start, round_end, score_change, map_end, game_end
|   Pattern: collector/game_state/dota2_client.py diff-based detection
+-- rounds[] only post-game --> coarser events only (map_end, game_end)
    Still useful but lower granularity than NBA/NHL
```

## Notes

- Config `scheduled_start` values are stale (Polymarket event creation time). Probes resolve real match times from PandaScore API.
- PandaScore rate limit: 1000 req/hr. Two matches at 10s intervals = ~12 req/min, well within budget.
- Existing collectors handle `data_source: "pandascore"` gracefully — logs warning, skips game state, collects market data normally.
