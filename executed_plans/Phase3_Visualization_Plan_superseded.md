# Phase 3: Overreaction Visualization & Analysis

> Build a UI-agnostic data transformation layer and 6 visualization charts to validate/reject the emotional overreaction hypothesis using collected Polymarket + game-state data.

---

## Problem Statement

Phase 2 collection is complete. We have 538K price signals, 45K trades, and 1,109 match events across 8 completed games (6 NBA + 2 NHL) from the first collection night (2026-03-25), with more nights coming over 1-2 weeks. The hypothesis is that prediction market prices overreact to live game events (score changes, fouls, turnovers) and then partially revert. We need visualizations that make this spike-then-revert pattern (or its absence) immediately visible, characterize it by event type and market type, and quantify its magnitude and duration.

## Data Quality Summary (2026-03-25)

| Metric | Value |
|---|---|
| Databases | 21 (12 NBA, 2 NHL, 7 cricket) |
| Total size | ~280 MB |
| Price signals | 538K |
| Trades | 45K |
| Match events | 1,109 (6 NBA games + 2 NHL) |
| Data gaps | **0** across all DBs |
| Coverage | ~2h16m continuous per game |
| Cricket | Not viable (0 trades, 0 events) |

Top games by volume: LAL-IND (98K signals, 4K trades, 239 events), ATL-DET (79K signals, 6.6K trades, 220 events), OKC-BOS (74K signals, 5.2K trades, 149 events).

## Design Decisions

### D1: UI-agnostic data transformation layer

**Decision:** Build a Python module that produces normalized time series from SQLite DBs. Any frontend (Streamlit, Next.js) consumes these outputs.

**Rationale:** The data shapes are identical regardless of frontend. The real bottleneck is the transformation logic (event-window extraction, normalization, token mapping), not the rendering.

### D2: Analysis-only market classifier (4-way)

**Decision:** Create a separate `classify_market_type(question)` function in analysis code that returns `"moneyline"`, `"spread"`, `"over_under"`, or `"player_prop"`. Default unrecognized to `"other"`.

**Rationale:** The existing `categorize_market()` in `collector/config.py` does core/prop and drives WS sharding. Changing it would affect live collection. The 4-way split is an analysis concern only. Question formats are highly regular:
- Moneyline: `"Thunder vs. Celtics"` (exactly "TeamA vs. TeamB")
- Spread: `"Spread: Thunder (-2.5)"` (starts with "Spread:")
- O/U: `"Thunder vs. Celtics: O/U 218.5"` (contains ": O/U")
- Player prop: matches existing `_PROP_PATTERN` regex

### D3: Sport-specific team name mapping for event linking

**Decision:** Build static mappings (NBA tricode → full name, NHL team ID → full name) applied at analysis time.

**Rationale:** `match_events.event_team` uses NBA tricodes ("OKC", "BOS") and NHL numeric team IDs ("10"), but `matches.team1/team2` and `markets.outcomes_json` use full names ("Thunder", "Celtics"). Without mapping, event-to-token linking fails silently.

**Trade-off:** Could store normalized names at collection time, but that's a schema change. Static dicts at analysis time are simpler and require no migration.

### D4: Timestamp quality gating

**Decision:** Filter/facet by `timestamp_quality`. Use `"server"` events for primary analysis. Show NHL (`"local"`, ±5s error) on separate facets with annotation. Don't mix server and local quality in aggregate statistics.

**Rationale:** NBA events are all `"server"` quality in current data (though code can fall back to `"local"` when `timeActual` is missing). NHL is always `"local"` due to NHL API limitations. The T-30s to T+120s window absorbs the ±5s NHL error for visual inspection, but aggregate stats would be contaminated.

### D5: Build order — Chart 1 first, then evaluate

**Decision:** Build Chart 1 (Event-Aligned Price Response Curves) first. If overreaction signal is visible in ~20 `score_change` events, build Charts 2+6, then 3+4+5. If no signal, revisit methodology before investing in more charts.

**Rationale:** Avoids premature visualization infrastructure. Chart 1 directly tests the hypothesis. If the pattern doesn't exist, the other charts are wasted work.

### D6: Moneyline tokens first, expand later

**Decision:** Start Chart 1 with moneyline tokens only. Add spread as a second series in iteration 2.

**Rationale:** Moneyline is the most liquid, most directly impacted by score changes, cleanest signal. Spread and O/U can be added once the pipeline works.

### D7: Analysis code location

**Decision:** Start in `scripts/`, move to `analysis/` package if it grows past 2-3 files.

**Rationale:** Follows existing project patterns (`scripts/verify_collection.py`, `scripts/analyze_data_fitness.py`). Low overhead for 1-2 weeks of exploratory analysis.

## Implementation Plan

### Step 1: Team name mapping module

Create `scripts/analysis_helpers.py` with:

- `NBA_TRICODE_TO_NAME`: Static dict mapping 30 NBA tricodes → full team names (e.g., `"OKC": "Thunder"`)
- `NHL_TEAM_ID_TO_NAME`: Static dict mapping 32 NHL team IDs → full team names (e.g., `"10": "Maple Leafs"`)
- `resolve_event_team(event_team: str, sport: str) -> str`: Returns full team name

### Step 2: Market lookup builder

In the same module, build a per-DB in-memory lookup:

- `build_market_lookup(db_path) -> dict[str, MarketInfo]`: Parses `markets` table, returns `{token_id -> MarketInfo(market_id, question, outcome_label, market_type)}`
- Joins `token_ids_json` with `outcomes_json` by index
- **Validates** `len(token_ids) == len(outcomes)` — raises error on mismatch
- Calls `classify_market_type(question)` for the 4-way classification

### Step 3: Event-window extractor

Core function for all visualizations:

- `get_price_window(db_path, event_server_ts_ms, token_id, before_s=30, after_s=120) -> DataFrame`
- Queries `price_signals` for the token in the time window
- Returns columns: `offset_s` (seconds relative to event, negative = before), `mid_price`, `spread`, `best_bid`, `best_ask`, `imbalance`
- Normalizes price to **basis points change from baseline** (median mid_price in T-30s to T-5s window)
- Uses existing index: `idx_signals_token_ms ON price_signals(token_id, server_ts_ms)`

### Step 4: Event-to-token linker

- `link_event_to_tokens(db_path, event, lookup) -> list[token_id]`
- For `score_change`: resolve `event_team` → full name → find moneyline market where outcome matches → return that token_id
- For other event types (foul, turnover, timeout): return moneyline tokens for BOTH teams (both may react)
- Filter to `market_type == "moneyline"` only (for v1)

### Step 5: Chart 1 — Event-Aligned Price Response Curves

The hypothesis test visualization:

- For each `match_event` with `timestamp_quality="server"`, get the linked moneyline token, extract price window
- Plot individual event curves (thin lines) + mean response curve (bold)
- Facet by `event_type`: separate panels for score_change, foul, turnover, timeout, substitution
- X-axis: seconds relative to event (T-30 to T+120)
- Y-axis: basis points change from baseline
- Vertical line at T=0

**The overreaction signature**: Sharp spike at T=0, partial reversion within 30-90s. Gap between peak and T+120s resting price = overreaction magnitude.

### Step 6: Chart 2 — Overreaction Heatmap

- Rows: event types (score_change, foul, turnover, timeout, substitution)
- Columns: time offsets (T+5s, T+15s, T+30s, T+60s, T+90s, T+120s)
- Cell color: median absolute price displacement (bps) from baseline
- Second heatmap: **reversion ratio** = `(peak_displacement - final_displacement) / peak_displacement`
  - Near 1.0 = full reversion (pure overreaction)
  - Near 0.0 = price held (efficient)
- Only include tokens with >10 trades in the event window (liquidity filter)

### Step 7: Chart 6 — Spike Candidate Table

Sortable table of all event-token pairs:

| Column | Source |
|---|---|
| Game | matches.match_id |
| Event type | match_events.event_type |
| Event time | match_events.server_ts_ms |
| Token | markets.question (human-readable) |
| Peak displacement (bps) | max abs(bps_change) in window |
| Time to peak (s) | offset of peak |
| Reversion % | (peak - T+120s) / peak |
| Spread at peak (bps) | price_signals.spread at peak moment |
| Trade count | count of trades in window |

Note: `analyze_data_fitness.py` already has spike candidate detection (">5c spike + 30% reversion in 5min") — can be refined and reused.

### Step 8: Chart 3 — Spread + Volume Around Events

Dual-panel per event:
- Top: bid-ask spread (bps) from `order_book_snapshots` around event window
- Bottom: trade count in 5-second bins from `trades` table
- Shows liquidity withdrawal mechanism (spread widens + volume spikes = emotional trading)

### Step 9: Chart 4 — Multi-Token Cascade

For a single event, plot 4-6 related tokens simultaneously:
- Moneyline (both teams), spread, O/U — all normalized to bps-from-baseline
- Same T-30s to T+120s window
- Shows which market reacts first and whether there's a propagation lag
- Should be interactive: pick a game, pick an event, see the cascade

### Step 10: Chart 5 — Game Narrative Timeline

Full-game horizontal view:
- X-axis: game time
- Upper area: moneyline price for one team (line chart)
- Lower area: colored markers for each event (green=score_change, red=foul, yellow=timeout, etc.)
- Background bands for quarters (NBA) / periods (NHL)
- The "zoom out" view for identifying interesting moments to deep-dive

## Known Issues to Handle

1. **NHL duplicate `period_end` events** (4-7 per intermission): Deduplicate by requiring >60s between consecutive `period_end` events for the same period
2. **NHL `event_team` is numeric team ID**: Map via `NHL_TEAM_ID_TO_NAME` dict
3. **NBA `timestamp_quality` can be `"local"`**: Filter to `"server"` for primary analysis, surface `"local"` events separately
4. **`price_signals` has no `market_id`**: All market context comes through the in-memory token→market lookup
5. **Cricket data**: Zero trades, zero events — exclude from all analysis

## Verification

### After Step 3 (event-window extractor):
```python
# Smoke test: extract a window for a known OKC-BOS score_change event
from scripts.analysis_helpers import build_market_lookup, get_price_window
lookup = build_market_lookup("data/nba-okc-bos-2026-03-25.db")
# Pick a score_change event's server_ts_ms and moneyline token_id
df = get_price_window("data/nba-okc-bos-2026-03-25.db", event_ts_ms, token_id)
assert len(df) > 0
assert "offset_s" in df.columns
assert "bps_change" in df.columns
```

### After Step 5 (Chart 1):
- Plot 20 `score_change` events from OKC-BOS or LAL-IND
- Visual inspection: do any curves show spike-then-revert?
- If >30% show the pattern, proceed to Charts 2-6
- If <10% show it, revisit: try different tokens, windows, or event types

### After all charts:
- Cross-check Chart 2 heatmap: is T+5s column hotter than T+120s?
- Cross-check Chart 6 table: sort by reversion % — are there >100 candidates with >50% reversion?
- Spot-check Chart 4 cascade: does moneyline lead spread/O-U?
