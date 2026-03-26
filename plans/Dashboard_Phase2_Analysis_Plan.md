# Dashboard Phase 2: Analysis Intelligence + Hypothesis Charts

> Add smart event-to-token linking and build the 3 hypothesis-critical visualizations (heatmap, spike table, game timeline) on top of the Phase 0.5 React dashboard.

---

## Problem Statement

Phase 0.5 delivered an event-aligned curve viewer, but it picks the 5 most active tokens blindly — a score_change by OKC shows random player props instead of the OKC moneyline token. The Phase 3 Visualization Plan identified analysis-layer logic (team name resolution, market classification, event-to-token linking) that would make the dashboard actually test the overreaction hypothesis. This plan merges that analysis intelligence into the FastAPI layer and adds the 3 highest-value charts.

## PR Strategy

Split into two PRs at the API/UI boundary to keep review surface manageable:

- **PR 1 (Steps 1–4):** Analysis intelligence + API endpoints. Testable independently via curl/httpie.
- **PR 2 (Steps 5–9):** Overlapping event markers, new React components + page wiring. Depends on PR 1.

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

### D4: NHL event deduplication (surgical)

**Decision:** Deduplicate ONLY `period_end` and `half_end` events by requiring >60s between consecutive events of the same `(event_type, quarter)`. Do NOT dedup other event types (score_change, penalty, etc.). Apply dedup to the full events list before both primary window selection and overlapping event computation.

**Rationale:** NHL API produces 4-7 duplicate `period_end` events per intermission. Without dedup, the heatmap and spike table are polluted. Restricting to period_end/half_end avoids false dedup of legitimate rapid events (back-to-back penalties, quick scores). Intermissions are 15-20 minutes, so the 60s threshold is safe for these specific event types.

### D5: Three `event_team` formats require sport-aware resolution

**Decision:** `resolve_event_team()` branches on sport to handle three distinct formats stored in `match_events.event_team`:
- **NBA:** Tricode string (e.g., `"OKC"`) → map via `NBA_TRICODE_TO_NAME`
- **NHL:** Numeric team ID string (e.g., `"10"`) → map via `NHL_TEAM_ID_TO_NAME`
- **Sports WS (CBB, MLB, tennis, etc.):** Field is empty/None — Sports WS `_make_event()` never sets `event_team`

**Rationale:** Without sport-aware routing, NHL numeric IDs would fail name lookup and Sports WS events would silently produce empty curves. Confirmed by reading `nhl_client.py` (`event_team=str(scoring_team_id)`), `nba_client.py` (tricodes), and `sports_ws_client.py` (no `event_team` in `_make_event`).

### D7: Overlapping events shown in event-aligned charts

**Decision:** Each event-aligned window includes all other game events that fall within its time range, rendered as vertical marker lines on the chart. Without this, price movements caused by subsequent events (e.g., a score_change at T+30s) are wrongly attributed to the original event at T=0.

**Rationale:** NBA events are typically 11–60s apart. 97% of events in OKC-BOS have another event within their 120s window. Any per-event chart without overlapping event markers is misleading for hypothesis evaluation.

### D6: Moneyline classification uses `market_match_mapping.relationship`

**Decision:** Primary classification: look up `market_match_mapping.relationship` for each market. `relationship == "match_winner"` → moneyline. This value is populated at discovery time by `guess_relationship()` in `discover_markets.py` and stored in all 114 DBs. Thin fallback: if mapping is missing or `relationship == "unknown"`, fall back to outcomes-based matching (outcomes entries fuzzy-match `team1`/`team2`).

**Rationale:** The mapping is already computed once at config time and covers all sports consistently. Avoids reinventing classification at query time. The fallback handles edge cases but is unlikely to trigger for any existing DB.

**3-way markets:** Soccer and cricket `match_winner` markets can have 3 outcomes (Team A, Draw, Team B). The linker should handle this by returning all match_winner token_ids (not assuming exactly 2). The hypothesis applies to draw odds too.

## Implementation Plan

### Step 1: Team name mapping + market classifier (`api/analysis.py`)

Create `api/analysis.py` with:

- `NBA_TRICODE_TO_NAME`: dict mapping 30 NBA tricodes → full team names (e.g., `"OKC": "Thunder"`)
- `NHL_TEAM_ID_TO_NAME`: dict mapping 32 NHL team IDs → full team names (e.g., `"10": "Maple Leafs"`)
- `resolve_event_team(event_team: str | None, sport: str, matches_row: tuple | None) -> str | None`:
  - NBA: tricode → team name via `NBA_TRICODE_TO_NAME`
  - NHL: numeric ID → team name via `NHL_TEAM_ID_TO_NAME`
  - Sports WS / empty: returns `None` (caller handles fallback)
- `normalize_team_name(name: str) -> str`: Lowercase, strip city prefixes, common suffixes for fuzzy matching (needed to match `event_team` resolved names against `outcomes_json` labels)
- `build_market_lookup(conn) -> dict[str, MarketInfo]`: Joins `markets` + `market_match_mapping` + `matches` once per request. Uses `market_match_mapping.relationship` as the primary market type (D6). Returns `{token_id -> MarketInfo(market_id, question, outcome_label, market_type, team_name, match_id)}`. Joins on `match_id` to avoid cross-match misclassification in multi-match DBs. Thin fallback: if mapping row is missing, classify via outcomes-based matching (outcomes entries fuzzy-match team1/team2 → `"match_winner"`).

### Step 2: Event-to-token linker (`api/analysis.py`)

- `link_event_to_tokens(event, lookup, matches_row) -> list[str]`
- **When `event_team` resolves to a team name** (NBA, NHL):
  - For `score_change`: find moneyline market where an outcome matches the resolved team → return both moneyline token_ids (scoring team + opponent)
  - For other event types: return moneyline tokens for BOTH teams
- **When `event_team` is empty/None** (Sports WS fallback):
  - Return both moneyline token_ids unconditionally (showing both sides of the market is still hypothesis-useful — you see the winner's price rise and loser's drop)
- **Fallback:** If smart-linked tokens produce 0 curves (no signals in window), fall back to top-5-by-volume for that event
- Filter to `market_type == "match_winner"` by default (consistent with `market_match_mapping.relationship` naming), optional `include_spread=True` param
- Handle 3-way markets (soccer/cricket): return all match_winner token_ids, not just 2

### Step 3: Upgrade event-windows endpoint

- Add `smart_link` query param to `GET /db/{name}/event-windows`
- When `smart_link=true`, use `link_event_to_tokens()` instead of top-5-by-volume
- Add `linked_market_type` field to each token_curve in response
- Add `quarter` to the match_events SELECT (needed for NHL dedup)
- Fetch `matches` row once for team1/team2 (used by linker and classifier)
- **Event fetching restructure (brainstorm fix):** Fetch ALL events once (no `event_type`/`ts_quality` filter in SQL). Apply NHL dedup to the full list first (D4: only `period_end`/`half_end`, >60s gap between same `(event_type, quarter)`). Then split into two views:
  - `primary_events`: filtered by `event_type`/`ts_quality` query params → these get their own windows
  - `all_events`: the full deduped list → used for overlapping event computation
  This ensures that a `score_change` window correctly shows overlapping fouls/turnovers/etc. without an extra DB round-trip.
- **Overlapping events (D7):** For each primary event window, find all other events from `all_events` whose `server_ts_ms` falls within `[ev_ts - window_before, ev_ts + window_after]`. Return as `overlapping_events` array in the response:
  ```json
  "overlapping_events": [
    {"event_type": "score_change", "offset_s": 30.2, "team1_score": 10, "team2_score": 16, "event_team": "OKC"},
    {"event_type": "foul", "offset_s": 55.8, "team1_score": 10, "team2_score": 16, "event_team": "BOS"}
  ]
  ```
  Excludes the primary event itself.

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

### Step 5: Overlapping event markers in existing Curves charts

Upgrade `EventAlignedChart` to render overlapping events:
- For each entry in `overlapping_events`, draw a thin dashed vertical line at its `offset_s` position
- Color-code by event type (reuse existing `EVENT_COLORS` mapping)
- Small label above the chart area: event type abbreviation + score (e.g., "Score 10-16" or "Foul")
- Labels should not overlap — stack or offset if events are close together
- Tooltip on hover shows full event details (type, score, team)
- This applies to the existing Curves tab immediately — no new component needed

### Step 6: Overreaction Heatmap component

React component `OverreactionHeatmap`:
- Grid: event types (rows) x time offsets (columns)
- Color-encoded cells: displacement magnitude (blue-to-red diverging scale)
- Second toggle for reversion ratio (green = full revert, red = price held)
- Faceted by timestamp quality (server vs local tabs)
- Click cell → filters event-aligned curves to that event_type + time window

### Step 7: Spike Candidate Table component

React component `SpikeCandidateTable`:
- TanStack Table with sortable columns
- Inline sparkline per row (mini bps curve, 60px wide)
- Color-coded reversion % (green >50%, yellow 20-50%, red <20%)
- Click row → opens event-aligned curve for that specific event
- Filter pills: event type, market type, min displacement, min reversion

### Step 8: Game Timeline component

React component `GameTimeline`:
- TradingView Lightweight Charts for price line (moneyline token)
- Event annotation rail below (reusing existing component)
- Period/quarter background bands (colored strips)
- Score overlay at each score_change event
- Click event dot → scrolls to event-aligned curve detail

### Step 9: Wire into page layout

- Add tab navigation: "Curves" (existing) | "Heatmap" | "Spikes" | "Timeline"
- Heatmap and Spikes tabs load data on tab switch (lazy)
- Timeline tab shows full-game view with event rail
- Add `smart_link` toggle in top bar (default on)

## Verification

### After Step 2 (unit tests):
- [ ] `resolve_event_team("OKC", "nba", ...)` → `"Thunder"`
- [ ] `resolve_event_team("10", "nhl", ...)` → `"Maple Leafs"`
- [ ] `resolve_event_team(None, "cbb", ...)` → `None`
- [ ] `build_market_lookup()` uses `market_match_mapping.relationship` as primary classifier
- [ ] `build_market_lookup()` falls back to outcomes-based matching when mapping row is missing
- [ ] 3-way match_winner markets (soccer) return all 3 token_ids from linker

### After Step 3:
- [ ] `GET /db/nba-okc-bos-2026-03-25/event-windows?event_type=score_change&smart_link=true` returns curves for moneyline tokens, not random props
- [ ] NHL period_end/half_end events are deduplicated (>60s gap); other event types NOT deduped
- [ ] `linked_market_type` field present in response
- [ ] Smart-linked events with no moneyline signals fall back to top-5
- [ ] Each window includes `overlapping_events` array with correct `offset_s` values
- [ ] Primary event is excluded from its own `overlapping_events`
- [ ] `event_type=score_change` windows still show overlapping fouls/turnovers in `overlapping_events` (cross-type overlaps work)

### After Step 4:
- [ ] Heatmap endpoint returns grid with score_change row showing higher displacement at T+5s than T+120s (if hypothesis holds)
- [ ] Spike candidates sorted by reversion_pct, top entries have >50% reversion
- [ ] Game timeline returns downsampled price + events + period boundaries
- [ ] Heatmap `min_signals` param filters low-liquidity windows

### After Step 5:
- [ ] Overlapping events render as dashed vertical lines on Curves charts
- [ ] Labels don't overlap when events are close together
- [ ] Hover on marker shows event details

### After Step 9:
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
