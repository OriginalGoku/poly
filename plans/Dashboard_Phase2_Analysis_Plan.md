# Dashboard Phase 2: Analysis Intelligence + Hypothesis Charts

> Add smart event-to-token linking and build the 3 hypothesis-critical visualizations (heatmap, spike table, game timeline) on top of the Phase 0.5 React dashboard.

---

## Problem Statement

Phase 0.5 delivered an event-aligned curve viewer, but it picks the 5 most active tokens blindly — a score_change by OKC shows random player props instead of the OKC moneyline token. The Phase 3 Visualization Plan identified analysis-layer logic (team name resolution, market classification, event-to-token linking) that would make the dashboard actually test the overreaction hypothesis. This plan merges that analysis intelligence into the FastAPI layer and adds the 3 highest-value charts.

## PR Strategy

Split into two PRs at the API/UI boundary to keep review surface manageable:

- **PR 1 (Steps 1–4):** Analysis intelligence + API endpoints. Testable independently via curl/httpie.
- **PR 2 (Steps 5–8):** React components + page wiring. Depends on PR 1.

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

### D5: Three `event_team` formats require sport-aware resolution

**Decision:** `resolve_event_team()` branches on sport to handle three distinct formats stored in `match_events.event_team`:
- **NBA:** Tricode string (e.g., `"OKC"`) → map via `NBA_TRICODE_TO_NAME`
- **NHL:** Numeric team ID string (e.g., `"10"`) → map via `NHL_TEAM_ID_TO_NAME`
- **Sports WS (CBB, MLB, tennis, etc.):** Field is empty/None — Sports WS `_make_event()` never sets `event_team`

**Rationale:** Without sport-aware routing, NHL numeric IDs would fail name lookup and Sports WS events would silently produce empty curves. Confirmed by reading `nhl_client.py` (`event_team=str(scoring_team_id)`), `nba_client.py` (tricodes), and `sports_ws_client.py` (no `event_team` in `_make_event`).

### D6: Moneyline classification uses outcomes + team names, not just question text

**Decision:** Primary classification: match `outcomes_json` entries against normalized `matches.team1/team2` values (lowercase, strip common suffixes). Secondary: fall back to question-text pattern matching. This avoids brittleness from question format variants ("incl. OT", "Moneyline:", swapped separators).

**Rationale:** `markets.question` format isn't guaranteed consistent across sports and time. `outcomes_json` combined with the known team names from `matches` is more reliable.

## Implementation Plan

### Step 1: Team name mapping + market classifier (`api/analysis.py`)

Create `api/analysis.py` with:

- `NBA_TRICODE_TO_NAME`: dict mapping 30 NBA tricodes → full team names (e.g., `"OKC": "Thunder"`)
- `NHL_TEAM_ID_TO_NAME`: dict mapping 32 NHL team IDs → full team names (e.g., `"10": "Maple Leafs"`)
- `resolve_event_team(event_team: str | None, sport: str, matches_row: tuple | None) -> str | None`:
  - NBA: tricode → team name via `NBA_TRICODE_TO_NAME`
  - NHL: numeric ID → team name via `NHL_TEAM_ID_TO_NAME`
  - Sports WS / empty: returns `None` (caller handles fallback)
- `normalize_team_name(name: str) -> str`: Lowercase, strip city prefixes, common suffixes for fuzzy matching
- `classify_market_type(question: str, outcomes: list[str], team1: str, team2: str) -> str`:
  - **Primary (D6):** If outcomes are exactly 2 entries and both fuzzy-match `team1`/`team2` → `"moneyline"`
  - **Secondary:** Question text patterns as fallback:
    - Spread: starts with `"Spread:"` or contains `"spread"`
    - O/U: contains `": O/U"` or `"over/under"`
    - Player prop: everything else
- `build_market_lookup(conn) -> dict[str, MarketInfo]`: Parses `markets` + `matches` tables once per request. Returns `{token_id -> MarketInfo(market_id, question, outcome_label, market_type, team_name)}`. Shared across all endpoints in the same request.

### Step 2: Event-to-token linker (`api/analysis.py`)

- `link_event_to_tokens(event, lookup, matches_row) -> list[str]`
- **When `event_team` resolves to a team name** (NBA, NHL):
  - For `score_change`: find moneyline market where an outcome matches the resolved team → return both moneyline token_ids (scoring team + opponent)
  - For other event types: return moneyline tokens for BOTH teams
- **When `event_team` is empty/None** (Sports WS fallback):
  - Return both moneyline token_ids unconditionally (showing both sides of the market is still hypothesis-useful — you see the winner's price rise and loser's drop)
- **Fallback:** If smart-linked tokens produce 0 curves (no signals in window), fall back to top-5-by-volume for that event
- Filter to `market_type == "moneyline"` by default, optional `include_spread=True` param

### Step 3: Upgrade event-windows endpoint

- Add `smart_link` query param to `GET /db/{name}/event-windows`
- When `smart_link=true`, use `link_event_to_tokens()` instead of top-5-by-volume
- Add `linked_market_type` field to each token_curve in response
- Add `quarter` to the match_events SELECT (needed for NHL dedup)
- Add NHL event deduplication: skip events where `(event_type, quarter)` matches previous event AND time gap < 60s
- Fetch `matches` row once for team1/team2 (used by linker and classifier)

### Step 4: New API endpoints

Add to `api/main.py` and `api/queries.py`:

- `GET /db/{name}/heatmap?metric=displacement|reversion`: Precomputed heatmap data
  - Rows: event types (score_change, foul, turnover, timeout, etc.)
  - Columns: time offsets (T+5s, T+15s, T+30s, T+60s, T+90s, T+120s)
  - Cells: median absolute bps displacement OR median reversion ratio
  - Uses smart token linking (moneyline only)
  - Liquidity filter: only tokens with ≥20 price_signals in event window (not trades — signals are denser from WS book snapshots). Exposed as `min_signals` query param, default 20.
  - Separates server vs local timestamp quality

- `GET /db/{name}/spike-candidates?sort=reversion_pct&min_displacement=50`: Spike table data
  - Columns: event_id, event_type, event_time, token (human label), market_type, peak_displacement_bps, time_to_peak_s, reversion_pct, spread_at_peak_bps, signal_count, timestamp_quality
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

### After Step 2 (unit tests):
- [ ] `resolve_event_team("OKC", "nba", ...)` → `"Thunder"`
- [ ] `resolve_event_team("10", "nhl", ...)` → `"Maple Leafs"`
- [ ] `resolve_event_team(None, "cbb", ...)` → `None`
- [ ] `classify_market_type(...)` correctly identifies moneyline via outcomes+team matching
- [ ] `classify_market_type(...)` falls back to question-text patterns when outcomes don't match teams

### After Step 3:
- [ ] `GET /db/nba-okc-bos-2026-03-25/event-windows?event_type=score_change&smart_link=true` returns curves for moneyline tokens, not random props
- [ ] NHL events are deduplicated (no duplicate period_end within 60s)
- [ ] `linked_market_type` field present in response
- [ ] Smart-linked events with no moneyline signals fall back to top-5

### After Step 4:
- [ ] Heatmap endpoint returns grid with score_change row showing higher displacement at T+5s than T+120s (if hypothesis holds)
- [ ] Spike candidates sorted by reversion_pct, top entries have >50% reversion
- [ ] Game timeline returns downsampled price + events + period boundaries
- [ ] Heatmap `min_signals` param filters low-liquidity windows

### After Step 8:
- [ ] All 4 tabs render with real data
- [ ] Smart link toggle switches between top-5 and moneyline-linked views
- [ ] Heatmap cell click filters curves
- [ ] Spike table row click opens curve detail
- [ ] Dark theme consistent across all new components

## Deferred to Phase 3

- Score-diff inference for Sports WS `event_team` (identify which team scored by diffing consecutive team1_score/team2_score)
- Spread dynamics dual-panel chart (bid-ask spread + trade volume)
- Multi-token cascade view (moneyline + spread + O/U overlaid)
- Full order book depth ladder (10 bid/ask levels)
- Monitoring section (collector status, gap timeline, capture rate)
- Light/dark theme toggle (currently dark-only)
- Mean + confidence band overlay on event-aligned curves
