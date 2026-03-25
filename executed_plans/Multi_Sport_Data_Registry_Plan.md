# Multi-Sport Data Source Registry & Collection Fixes Plan

> Centralize data source truth, fix phantom coverage bugs, and establish the architecture for collecting clean data across all Polymarket sports.

---

## Problem Statement

The system is in a **data collection phase** — the goal is to collect as much clean, usable data across all sports as possible. There is no specific hypothesis yet; analysis and data mining come in a later phase.

Several issues block reliable multi-sport collection:
1. `discovery_summary.json` falsely marks LoL/Valorant/CS2 as `has_game_state: true` — no clients exist for `riot` or `pandascore` data sources
2. No single source of truth for which data sources are actually implemented
3. `config.py` only warns for NBA/NHL missing game state — misses dota2 and future sports
4. Plans and docs reference hypothesis-driven priorities instead of "collect everything clean"
5. Order-book-only sports have no explicit designation

**Key discovery:** Polymarket's own Sports API WebSocket (`wss://sports-api.polymarket.com/ws`) pushes live game state for ALL sports (tennis, LoL, CS2, valorant, NBA, NHL, etc.). A research spike already captured samples (`tests/fixtures/ws_sport_result_sample.json`). This is far more practical than building per-sport external API clients.

---

## Design Decisions

### D1: Central Registry Instead of Scattered Allowlists

**Decision:** Create `collector/game_state/registry.py` as the single source of truth for implemented data sources. All other files (`config.py`, `discover_markets.py`, `__main__.py`) import from it.

**Rationale:** Currently `build_game_state_client()` in `__main__.py` is the implicit registry, `config.py` has a hardcoded `{"nba", "nhl"}` set, and `discover_markets.py` infers `has_game_state` from `data_source != "none"`. These three will inevitably drift when clients are added/removed.

**Trade-off:** Adds a new file for only 3 implemented clients. Accepted because the cost is minimal and prevents all the phantom coverage / validation drift issues.

### D2: Polymarket Sports WS Over External APIs

**Decision:** For new sports (Tennis, LoL, CS2, Valorant, Soccer), use the Polymarket Sports API WebSocket as the primary game state source. Defer external API clients (Riot, PandaScore, SportRadar) unless the Sports WS lacks sufficient granularity.

**Rationale:**
- Already proven working (research spike captured ATP tennis data)
- No API keys, no rate limits, no per-sport auth
- One client replaces 5+ external integrations
- Covers all sports Polymarket lists

**Trade-off:** Sports WS provides coarser data (score + period) vs external APIs (point-by-point for tennis, kills/gold for LoL). Accepted because the current phase is data collection — granularity can be improved later if needed.

### D3: Data Collection Phase, Not Hypothesis Phase

**Decision:** All priorities and plans should reflect "collect clean, usable data across all sports" — not "detect emotional overreaction." No emotion taxonomy, no event mapping to analytical categories.

**Rationale:** User explicitly stated they don't have a clear hypothesis yet and don't want to do microsecond trading. The current phase is about maximizing data coverage; data mining comes later.

### D4: Order-Book-Only Sports as Explicit Control Group

**Decision:** Sports without game state (Cricket, MLB, UFC, NFL) are explicitly tagged as a control group in the registry. They're still collected — useful as a baseline for future analysis comparing price movements with vs without game events.

**Rationale:** Rather than excluding them or pretending they'll get game state, make their role explicit in the architecture.

### D5: Fix `has_game_state` Bug (Check All Items, Not First)

**Decision:** Replace `items[0]["data_source"] != "none"` with a check across ALL items in a sport, validated against the registry's `IMPLEMENTED_SOURCES`.

**Rationale:** A sport with mixed data sources across matches (e.g., some configs have `data_source: "riot"`, others `"none"`) would be misclassified by only checking the first item.

---

## Implementation Plan

### Step 1: Create `collector/game_state/registry.py`

New file — central registry consumed by `__main__.py`, `config.py`, and `discover_markets.py`.

```python
IMPLEMENTED_SOURCES: dict[str, dict] = {
    "nba_cdn": {"sport": "nba", "module": "nba_client", "has_lookup": True},
    "nhl_api": {"sport": "nhl", "module": "nhl_client", "has_lookup": True},
    "opendota": {"sport": "dota2", "module": "dota2_client", "has_lookup": False},
}

SPORTS_WITH_GAME_STATE: set[str] = {v["sport"] for v in IMPLEMENTED_SOURCES.values()}

ASPIRATIONAL_SOURCES: set[str] = {"pandascore", "riot"}

CONTROL_GROUP_SPORTS: set[str] = {"cricket", "mlb", "ufc", "nfl"}
```

### Step 2: Update `collector/config.py` (lines 61-69)

Replace hardcoded `{"nba", "nhl"}` with registry imports. Two warnings:
1. Sport has a client but `data_source="none"` → warn about missing game state
2. `data_source` is set to something not in `IMPLEMENTED_SOURCES` and not `"none"` → warn it's unimplemented

### Step 3: Fix `scripts/discover_markets.py` (line 250)

Replace `items[0]["data_source"] != "none"` with registry-aware logic:
- Check ALL items' data sources (not just first)
- Set `has_game_state` based on intersection with `IMPLEMENTED_SOURCES`
- Add `"data_sources"` and `"implemented_sources"` fields to summary

### Step 4: Update `collector/__main__.py` (line 107)

Import from registry. Improve fallthrough log message to list known implemented sources.

### Step 5: Update `collector/game_state/__init__.py`

Re-export registry constants for convenient imports.

### Step 6: Add Tests

**`tests/test_config.py`** — 3 new tests:
- `test_warns_unimplemented_data_source` — sport="lol", data_source="riot" → warning
- `test_warns_dota2_without_game_state` — sport="dota2", data_source="none" → warning
- `test_no_warning_implemented_source` — sport="dota2", data_source="opendota" → no warning

**`tests/test_registry.py`** (new file) — registry sanity:
- Constants are non-empty
- `SPORTS_WITH_GAME_STATE` derived correctly
- No overlap between `IMPLEMENTED_SOURCES` and `ASPIRATIONAL_SOURCES`

### Step 7: Update Documentation

**`plans/data_collection_improvements.md`:**
- Reframe priorities as "data collection" not "hypothesis testing"
- Promote Sports API WS (currently item #5) as the key multi-sport unlock

**`CLAUDE.md`:**
- Update "Current phase" to clarify data collection focus
- Add `collector/game_state/registry.py` to project structure
- Update test count

**`LESSONS_LEARNED.md`:**
- Add: `has_game_state` in `discovery_summary.json` was computed from first item only — could misclassify mixed-source sports

---

## API Research Summary (for reference)

### Polymarket Sports WS (RECOMMENDED for all new sports)
- URL: `wss://sports-api.polymarket.com/ws`
- Auth: None
- Data: `gameId`, `leagueAbbreviation`, `score`, `period`, `status`, `live`, `ended`, `eventState`
- Covers: ATP, WTA, LoL, CS2, Valorant, Soccer, NBA, NHL, etc.
- Sample captured: `tests/fixtures/ws_sport_result_sample.json`

### Tennis External APIs (DEFERRED — Sports WS covers basic scores)
- SofaScore: $20-100/month, partial point-level
- SportRadar: $50-150/month, full point-level
- Free options: Very limited or scraping-based

### LoL External APIs (DEFERRED — Sports WS likely covers basic scores)
- Riot Esports API: Public key, `/getLive` endpoint, game state for pro matches
- Riot Developer API: 20 req/s dev key, frame-by-frame timeline (completed matches only)
- PandaScore: 1,000 req/hr, less granular

### Strategy
Collect via Sports WS first for all sports. Evaluate data granularity. Only build external API clients if Sports WS lacks sufficient detail for specific sports.

---

## Verification

1. `python -m pytest tests/ -v` — all existing + new tests pass
2. `python scripts/discover_markets.py` — regenerate summary
3. Check `configs/discovery_summary.json` — LoL/Valorant/CS2 should show `has_game_state: false`
4. `python -m collector --config configs/<any-lol-config>.json` — should log warning about unimplemented data_source
5. `grep -r "nba.*nhl" collector/config.py` — hardcoded set should be gone
6. Verify registry is the only place that defines implemented sources
