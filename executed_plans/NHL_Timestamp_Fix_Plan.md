# NHL Timestamp Fix Plan

> Fix NHL game-state timestamps to prevent data leakage from poll-time bias, ensuring correct event-to-price causality ordering.

---

## Problem Statement

The NHL game-state client (`collector/game_state/nhl_client.py`) uses `datetime.now()` as `server_ts_ms` for all events, introducing two critical issues:

1. **Batch timestamp collapse**: `now_ms` is computed once per `poll()` call and reused for ALL events in that batch. Multiple events (e.g., goal + penalty in same poll window) get identical timestamps, destroying intra-poll ordering.
2. **Up to 10s poll-time bias**: Events are timestamped when we *discover* them, not when they *happened*. A goal at T+0 polled at T+8s looks like it happened at T+8s, while Polymarket trades (with server timestamps) may show price movement at T+1s — making it appear the **market moved before the event**. This is exactly the data leakage that would produce a backtest-only system.

The NBA client does not have this problem because the NBA API provides `timeActual` (ISO 8601 wall-clock) on each action. The NHL API provides only game-clock fields (`timeInPeriod`, `timeRemaining`, `sortOrder`) with no absolute wall-clock timestamps.

## Design Decisions

### D1: Simple fix first, defer live anchoring

**Decision:** Implement the simple fix (per-event timestamps, reduced poll interval, sortOrder differentiation) immediately. The full live-anchoring approach is designed but deferred until we have real collected data proving 5s jitter matters.

**Rationale:** The simple fix gets us from "10s error, batch-collapsed, incorrectly ordered" to "5s error, correctly ordered, per-event timestamps" with minimal complexity. Live anchoring adds subtle edge cases (backfill detection, future-dated timestamps) that aren't justified until we know the simpler approach is insufficient.

**Trade-off:** Live anchoring could achieve ~1-2s accuracy within a period, but risks introducing future-dated timestamps and backfill bugs that are worse than the current problem.

### D2: Keep `server_ts_ms` as honest poll time, don't fake derived precision

**Decision:** `server_ts_ms` stays as local poll time (with per-event differentiation via sortOrder offsets). Do not store derived timestamps in `server_ts_ms` until we have a proven, safe anchoring mechanism.

**Rationale:** Codex review identified that storing derived timestamps in `server_ts_ms` could violate downstream assumptions (`server_ts_ms <= local_ts`), produce future-dated events on backfill, and mislead consumers into treating approximate timestamps as authoritative.

### D3: No schema changes in simple fix

**Decision:** Use existing fields only. No `timestamp_error_bound_ms` column or schema migration.

**Rationale:** The existing `timestamp_quality` field (already supports "server"/"local") and `server_ts_raw` (already stores "P{n} {timeInPeriod}") are sufficient. Adding a new column requires migration logic that doesn't exist for `match_events`.

### D4: Reduce poll interval from 10s to 5s

**Decision:** Reduce `poll_interval_seconds` from 10.0 to 5.0.

**Rationale:** Halves the maximum timestamp error. NHL API appears CDN-like with no documented rate limit. Conservative reduction (not 2-3s) to avoid potential throttling on first deployment.

---

## Part 1: Simple Fix (Implement Now)

### Step 1: Per-event timestamp differentiation

**File:** `collector/game_state/nhl_client.py`

- Remove the single `now_ms = int(datetime.now(...))` at the top of the event loop
- Compute a fresh `local_ts` and `now_ms` for each event inside the loop
- Add sub-millisecond differentiation using `sortOrder`: `now_ms + (sort_order - base_sort_order)` to guarantee unique, ordered timestamps even within a single poll batch

### Step 2: Reduce poll interval

**File:** `collector/game_state/nhl_client.py`

- Change `poll_interval_seconds = 10.0` to `poll_interval_seconds = 5.0`

### Step 3: Parse `timeInPeriod` into `server_ts_raw` consistently

**File:** `collector/game_state/nhl_client.py`

- Already storing `f"P{period} {play.get('timeInPeriod', '')}"` — keep this, it's the best ordering data we have
- Ensure game-end and period-end events also store the period clock

### Step 4: Update tests

**Files:** `tests/` (new or existing NHL test files)

- Test that multiple events from a single `poll()` call get distinct `server_ts_ms` values
- Test that `server_ts_ms` values are monotonically increasing with `sortOrder`
- Test that `timestamp_quality` is set to `"local"` for all NHL events

### Step 5: Update CLAUDE.md

**File:** `CLAUDE.md`

- Document the NHL timestamp limitation and the mitigation approach

---

## Part 2: Live Anchoring (Deferred — Design Only)

This section documents the full live-anchoring approach for future implementation if the simple fix proves insufficient.

### Approach: Period-start anchoring with backfill detection

1. **Track period anchors**: `_period_anchors: dict[int, float]` maps period number to the wall-clock time we first detected that period's start during live polling
2. **Backfill detection**: On the first poll, record `_first_poll_max_sort_order`. Any `period-start` event with `sortOrder <= _first_poll_max_sort_order` is historical — do NOT anchor it
3. **Live anchoring**: When a `period-start` event appears with `sortOrder > _first_poll_max_sort_order`, set `_period_anchors[period] = datetime.now()`
4. **Derived timestamps**: For events in an anchored period: `derived_ts = anchor + parse_seconds(timeInPeriod)`. Set `timestamp_quality = "derived"`
5. **Unanchored periods**: Events in periods where we missed the start stay `timestamp_quality = "local"` with poll time

### Edge cases to handle
- **Collector started mid-period**: No anchor for current period, only subsequent periods get anchored
- **Same-poll period-start + events**: The anchor is poll time; events with `timeInPeriod > 0` would get timestamps slightly after poll time, which is acceptable (they're bounded by poll_interval)
- **OT / shootout**: Period numbers 4+ with different durations (5min OT, shootout has no clock). Handle by always using `timeInPeriod` delta from anchor, not fixed period math

### Required changes for live anchoring
- Add `period-start` to parsed event types (currently only handles goal, period-end, game-end, penalty)
- Add `_period_anchors` and `_first_poll_max_sort_order` state to `NhlClient`
- Consider adding `timestamp_error_bound_ms` to `MatchEvent` and DB schema (requires migration)
- Extend `timestamp_quality` enum to include `"derived"`

### Validation criteria (when to implement)
- Collect data from 5+ NHL games with the simple fix
- Analyze: does 5s jitter materially affect event-to-price correlation analysis?
- If >20% of goal events have ambiguous causality ordering with nearby trades, implement live anchoring

---

## Verification

### Simple fix verification
1. Run `python -m pytest tests/ -v` — all existing tests pass
2. Run collector against a live NHL game (or replay fixture)
3. Query the resulting DB: `SELECT server_ts_ms, local_ts, server_ts_raw, timestamp_quality FROM match_events WHERE sport='nhl' ORDER BY server_ts_ms`
4. Verify: no duplicate `server_ts_ms` values, timestamps are monotonically increasing, `timestamp_quality` is `"local"` for all NHL events
5. Cross-check: `server_ts_ms` values should differ by at least 1ms between events in the same poll batch
