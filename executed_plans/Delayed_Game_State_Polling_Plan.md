# Delayed Game State Polling Plan

> Eliminate pre-game 403/404 error spam by delaying game-state API polling until near scheduled start, using a three-state machine and a new project settings file.

---

## Problem Statement

When the collector starts before a game begins (the common case — configs are generated hours before tip-off), game-state APIs (NBA CDN, NHL API) return 403/404 because play-by-play data isn't available yet. This spams logs with full exception tracebacks every 5-10 seconds and wastes HTTP requests for hours.

The `scheduled_start` field is already present in every match config but is never used by the collector.

## Design Decisions

### D1: Three-state poller instead of base class change

**Decision:** Implement the delay logic entirely in `run_game_state_poller()` in `__main__.py`, not in the `GameStateClient` base class.

**Rationale:** The base class has no `__init__`, no client calls `super().__init__()`, and modifying every sport client constructor to accept `scheduled_start` is unnecessary coupling. The poller loop already owns the sleep interval and is the natural place for this.

**Trade-off:** Rejected putting `should_poll()` in base class — would require changes to every client `__init__` for no benefit.

### D2: Combined schedule-based delay + response-based backoff

**Decision:** Use `scheduled_start - lead_minutes` to sleep initially (no requests at all), then exponential backoff until first HTTP 200, then normal polling.

**Rationale:** Schedule-based delay alone depends on `scheduled_start` accuracy. Response-based backoff alone still sends some requests pre-game. Combining both eliminates all pre-game requests when schedule is available and self-heals when it isn't.

### D3: `GameNotStarted` exception for poller visibility

**Decision:** Add a `GameNotStarted` exception in `base.py`. Sport clients raise it on 403/404 instead of logging. The poller catches it and controls logging/backoff.

**Rationale:** Currently `poll()` swallows HTTP errors and returns `[]`, identical to "no new events." The poller has no signal to distinguish error from empty success. Without this, the state machine can't transition BACKOFF → LIVE, and clients still emit stack traces during backoff. This is a ~3-4 line change per client.

### D4: Self-documenting `settings.json`

**Decision:** Create a project-level `settings.json` where each setting has `value` and `description` fields.

**Rationale:** User wants a centralized settings file that is self-documenting. Each setting carries its own description so the file is readable without external docs.

### D5: HTTP 200 = game is live (even with empty play-by-play)

**Decision:** Transition from BACKOFF to LIVE on first HTTP 200, regardless of whether the response contains play-by-play data.

**Rationale:** Some APIs return 200 with empty actions before the first play. The important signal is that the endpoint is responding — individual clients already handle empty action lists gracefully (return `[]`).

### D6: Global lead time, not per-sport

**Decision:** Single `game_state_poll_lead_minutes` setting (30 min) applies to all sports.

**Rationale:** Not worth the complexity to split by sport. Can always add per-sport overrides later if needed.

## Implementation Plan

### Step 1: Create `settings.json`

**File:** `settings.json` (project root, NEW)

```json
{
  "game_state_poll_lead_minutes": {
    "value": 30,
    "description": "Minutes before scheduled_start to begin game-state API polling. Set to 0 to poll immediately on collector start."
  }
}
```

### Step 2: Create `collector/settings.py`

**File:** `collector/settings.py` (NEW)

- Read `settings.json` from project root once at import
- Expose typed accessor: `get_game_state_poll_lead_minutes() -> int`
- Fall back to default (30) if file missing, key absent, or malformed
- Keep it minimal — no classes, just a module-level dict and accessor functions

### Step 3: Add `GameNotStarted` exception

**File:** `collector/game_state/base.py` (MODIFIED, ~3 lines)

- Add `class GameNotStarted(Exception): pass` alongside the existing base class
- This is the signal that the API returned 403/404 because the game hasn't started

### Step 4: Update NBA client error handling

**File:** `collector/game_state/nba_client.py` (MODIFIED, ~4 lines)

In `poll()`, change the `except Exception` block:
- Catch `httpx.HTTPStatusError` specifically
- If status is 403 or 404, raise `GameNotStarted` (no logging)
- All other exceptions continue to `logger.exception()` and return `[]` as today

### Step 5: Update NHL client error handling

**File:** `collector/game_state/nhl_client.py` (MODIFIED, ~4 lines)

Same change as NBA client.

### Step 6: Implement three-state poller

**File:** `collector/__main__.py` (MODIFIED, ~25 lines in `run_game_state_poller()`)

State machine:

```
WAITING → (asyncio.sleep until scheduled_start - lead_minutes) → BACKOFF → (first HTTP 200) → LIVE
```

**WAITING state:**
- Parse `config.scheduled_start` using `.replace("Z", "+00:00")` + `datetime.fromisoformat()`
- If empty/unparseable, skip to BACKOFF immediately (current behavior)
- If `poll_start_time` is in the past, skip to BACKOFF immediately
- If in the future, log once: `"Game state polling delayed until {time} UTC ({lead} min before scheduled start)"`
- `await asyncio.sleep(seconds_until_poll_start)`

**BACKOFF state:**
- Poll, catch `GameNotStarted` → stay in BACKOFF
- Log once on first `GameNotStarted`: `"Game state API not ready, backing off until available"`
- Exponential backoff: 30s → 60s → 120s (capped)
- On first successful return (no `GameNotStarted` raised), transition to LIVE
- Log: `"Game state API responding, switching to normal polling"`

**LIVE state:**
- Normal `poll()` + `asyncio.sleep(gs_client.poll_interval_seconds)` loop
- Standard error handling (same as current behavior)
- `GameNotStarted` in LIVE state is unexpected — log as warning, continue

**Edge cases:**
| Case | Behavior |
|---|---|
| `scheduled_start` empty/missing | Skip WAITING, go to BACKOFF → LIVE |
| `scheduled_start` unparseable | Same — skip WAITING |
| `scheduled_start` in the past | Skip WAITING, go to BACKOFF → LIVE |
| Collector started mid-game | Past scheduled_start → immediate BACKOFF → LIVE |
| Game starts early | 30-min buffer covers this |
| API 200 then later 403 | Already in LIVE, log as warning |

### Step 7: Tests

- Unit test `collector/settings.py`: missing file, missing key, valid file, malformed value
- Unit test schedule parsing: empty string, Z suffix, +00:00 suffix, past time, future time, malformed
- Unit test state transitions in `run_game_state_poller` with mock game client that raises `GameNotStarted` N times then returns `[]`

## Verification

1. Start collector against a game that hasn't started (e.g., tonight's Hawks-Pistons before 7 PM ET)
2. Confirm log shows: "Game state polling delayed until HH:MM UTC"
3. Confirm zero HTTP requests / zero error logs during WAITING
4. When the window opens, confirm BACKOFF logs once then backs off silently
5. When game starts, confirm transition to LIVE with normal polling
6. Run `python -m pytest tests/ -v` to confirm no regressions
