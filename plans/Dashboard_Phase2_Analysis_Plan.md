# Dashboard Phase 2: Analysis Intelligence + Hypothesis Charts

> Add smart event-to-token linking and build the 3 hypothesis-critical visualizations (heatmap, spike table, game timeline) on top of the Phase 0.5 React dashboard.

---

## Problem Statement

Phase 0.5 delivered an event-aligned curve viewer, but it picks the 5 most active tokens blindly — a score_change by OKC shows random player props instead of the OKC moneyline token. The Phase 3 Visualization Plan identified analysis-layer logic (team name resolution, market classification, event-to-token linking) that would make the dashboard actually test the overreaction hypothesis. This plan merges that analysis intelligence into the FastAPI layer and adds the 3 highest-value charts.

## Design Decisions

### D1: Analysis intelligence lives in `api/analysis.py`

**Decision:** Create a new module `api/analysis.py` with team mapping, market classification, and event-to-token linking. The FastAPI endpoints call these helpers.

**Rationale:** Pure Python functions that are easily testable. Keeps `api/queries.py` focused on SQL, `api/analysis.py` focused on domain logic.

### D2: Smart token linking upgrades the existing event-windows endpoint

**Decision:** Add an optional `smart_link=true` query param to `GET /db/{name}/event-windows`. When enabled, the endpoint uses the event-to-token linker to select the most relevant tokens per event instead of the top-5-by-volume default.

**Rationale:** Backward compatible. Phase 0.5 UI continues to work. New param unlocks the hypothesis-testing view.

### D3: Three hypothesis-critical charts first

**Decision:** Build heatmap, spike candidate table, and game timeline. Defer spread dynamics, multi-token cascade, and full depth ladder.

**Rationale:** These 3 charts directly answer: "Do prices overreact? How much? Which events?" The deferred charts add context but don't change the go/no-go decision.

### D4: NHL event deduplication

**Decision:** Deduplicate NHL `period_end` events by requiring >60s between consecutive events of the same type and period. Apply in the event-windows query.

**Rationale:** NHL API produces 4-7 duplicate `period_end` events per intermission. Without dedup, the heatmap and spike table are polluted.

## Implementation Plan

### Step 1: Team name mapping + market classifier (`api/analysis.py`)

Create `api/analysis.py` with:

- `NBA_TRICODE_TO_NAME`: dict mapping 30 NBA tricodes → full team names (e.g., `"OKC": "Thunder"`)
- `NHL_TEAM_ID_TO_NAME`: dict mapping 32 NHL team IDs → full team names (e.g., `"10": "Maple Leafs"`)
- `resolve_event_team(event_team: str, sport: str) -> str`: Returns full team name
- `classify_market_type(question: str) -> str`: Returns `"moneyline"`, `"spread"`, `"over_under"`, or `"player_prop"` based on question text patterns:
  - Moneyline: exactly `"TeamA vs. TeamB"` (no colon, no O/U)
  - Spread: starts with `"Spread:"`
  - O/U: contains `": O/U"`
  - Player prop: everything else
- `build_market_lookup(conn) -> dict[str, MarketInfo]`: Parses markets table, returns `{token_id -> MarketInfo(market_id, question, outcome_label, market_type)}`

### Step 2: Event-to-token linker (`api/analysis.py`)

- `link_event_to_tokens(conn, event, lookup) -> list[str]`
- For `score_change`: resolve `event_team` → full name → find moneyline market where outcome matches → return both moneyline token_ids (scoring team + opponent)
- For other event types (foul, turnover, timeout): return moneyline tokens for BOTH teams
- Filter to `market_type == "moneyline"` by default, optional `include_spread=True` param

### Step 3: Upgrade event-windows endpoint

- Add `smart_link` query param to `GET /db/{name}/event-windows`
- When `smart_link=true`, use `link_event_to_tokens()` instead of top-5-by-volume
- Add `linked_market_type` field to each token_curve in response
- Add NHL event deduplication (>60s gap between same event_type + period)

### Step 4: New API endpoints

Add to `api/main.py` and `api/queries.py`:

- `GET /db/{name}/heatmap?metric=displacement|reversion`: Precomputed heatmap data
  - Rows: event types (score_change, foul, turnover, timeout, etc.)
  - Columns: time offsets (T+5s, T+15s, T+30s, T+60s, T+90s, T+120s)
  - Cells: median absolute bps displacement OR median reversion ratio
  - Uses smart token linking (moneyline only)
  - Liquidity filter: only tokens with >10 trades in event window
  - Separates server vs local timestamp quality

- `GET /db/{name}/spike-candidates?sort=reversion_pct&min_displacement=50`: Spike table data
  - Columns: event_id, event_type, event_time, token (human label), market_type, peak_displacement_bps, time_to_peak_s, reversion_pct, spread_at_peak_bps, trade_count, timestamp_quality
  - Paginated (50 rows default)
  - Uses smart token linking

- `GET /db/{name}/game-timeline?token=`: Full-game view data
  - Price signals downsampled to 1s bins (LTTB)
  - Match events with type + score + team
  - Period/quarter boundaries
  - For the game narrative timeline chart

### Step 5: Overreaction Heatmap component

React component `OverreactionHeatmap`:
- Grid: event types (rows) x time offsets (columns)
- Color-encoded cells: displacement magnitude (blue-to-red diverging scale)
- Second toggle for reversion ratio (green = full revert, red = price held)
- Faceted by timestamp quality (server vs local tabs)
- Click cell → filters event-aligned curves to that event_type + time window

### Step 6: Spike Candidate Table component

React component `SpikeCandidateTable`:
- TanStack Table with sortable columns
- Inline sparkline per row (mini bps curve, 60px wide)
- Color-coded reversion % (green >50%, yellow 20-50%, red <20%)
- Click row → opens event-aligned curve for that specific event
- Filter pills: event type, market type, min displacement, min reversion

### Step 7: Game Timeline component

React component `GameTimeline`:
- TradingView Lightweight Charts for price line (moneyline token)
- Event annotation rail below (reusing existing component)
- Period/quarter background bands (colored strips)
- Score overlay at each score_change event
- Click event dot → scrolls to event-aligned curve detail

### Step 8: Wire into page layout

- Add tab navigation: "Curves" (existing) | "Heatmap" | "Spikes" | "Timeline"
- Heatmap and Spikes tabs load data on tab switch (lazy)
- Timeline tab shows full-game view with event rail
- Add `smart_link` toggle in top bar (default on)

## Verification

### After Step 3:
- [ ] `GET /db/nba-okc-bos-2026-03-25/event-windows?event_type=score_change&smart_link=true` returns curves for moneyline tokens, not random props
- [ ] NHL events are deduplicated (no duplicate period_end within 60s)
- [ ] `linked_market_type` field present in response

### After Step 4:
- [ ] Heatmap endpoint returns grid with score_change row showing higher displacement at T+5s than T+120s (if hypothesis holds)
- [ ] Spike candidates sorted by reversion_pct, top entries have >50% reversion
- [ ] Game timeline returns downsampled price + events + period boundaries

### After Step 8:
- [ ] All 4 tabs render with real data
- [ ] Smart link toggle switches between top-5 and moneyline-linked views
- [ ] Heatmap cell click filters curves
- [ ] Spike table row click opens curve detail
- [ ] Dark theme consistent across all new components

## Deferred to Phase 3

- Spread dynamics dual-panel chart (bid-ask spread + trade volume)
- Multi-token cascade view (moneyline + spread + O/U overlaid)
- Full order book depth ladder (10 bid/ask levels)
- Monitoring section (collector status, gap timeline, capture rate)
- Light/dark theme toggle (currently dark-only)
- Mean + confidence band overlay on event-aligned curves
