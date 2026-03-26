# Collection Log Analysis — 2026-03-25

> Investigate issues found during post-collection log review: MLB game matching failure, stale cricket configs, duplicate ATP runs, and WS disconnect patterns.

---

## Problem Statement

First full collection night with WS sharding (12 NBA, 2 NHL, 1 MLB, 7 cricket, 1 ATP tennis). Log sampling revealed:
- MLB Sports WS never matched the NYY-SF game (0 match events despite 1,906 WS messages received)
- Cricket collectors ran against games scheduled for March 18 (stale configs)
- ATP tennis had two identical collector runs (unintentional double-launch on Pi)
- NBA games had a WS disconnect cluster around 19:19-20:00 UTC

Data is still being collected on the Raspberry Pi — these are interim results from a mid-collection sync.

## Collection Results Summary

| Sport | Status | Games | Snapshots/game | Trades/game | Events/game |
|-------|--------|-------|----------------|-------------|-------------|
| NBA | Healthy | 12 | 10K-25K | 5K-12K | 354+ |
| NHL | Healthy | 2 | ~5K | ~2.5K | 12-13 |
| MLB | Degraded | 1 | 560 | 275 | **0** |
| Cricket | Stale configs | 7 | 282-846 | 0 | 0 |
| ATP Tennis | Healthy (dup) | 1 (×2) | 1,714 | ~720 | 0 |

## Design Decisions

### D1: Add Sports WS debug logging for league/team visibility

**Decision:** Log the set of observed `leagueAbbreviation` values and sample team names periodically (every 500 messages) in the Sports WS client.

**Rationale:** The MLB matching failure is undiagnosable — we can't tell if the game wasn't broadcast, if team names didn't match, or if the league wasn't present. Debug logging would have immediately answered this.

**Trade-off:** Could also add a `--debug-sports-ws` flag, but periodic logging at INFO level is simpler and always available without restarting collectors.

### D2: No code fix for MLB matching yet

**Decision:** Don't modify the fuzzy matching logic until we have debug data from a confirmed-live MLB game.

**Rationale:** The fuzzy matcher's token overlap logic *should* match "Yankees" against "New York Yankees". Two hypotheses remain: (A) the game wasn't live during collection, or (B) MLB isn't broadcast on Sports WS for this game. Need data to distinguish.

## Implementation Plan

### Step 1: Add observed-leagues logging to Sports WS client

**File:** `collector/sports_ws_client.py`

In the message processing loop, every 500 messages, log at INFO level:
- Set of `leagueAbbreviation` values seen so far
- Sample of `homeTeam`/`awayTeam` names for the target league (if any)

This runs alongside existing "N messages received, no match yet" logging.

### Step 2: Update LESSONS_LEARNED.md

**File:** `LESSONS_LEARNED.md`

Add bullet: Verify game dates in configs match the actual collection date before launching collectors (cricket configs were 7 days stale).

### Step 3: Clean up duplicate ATP data (manual, post-collection)

After collection ends on the Pi:
- Compare the two ATP databases for data overlap
- Delete the duplicate if identical
- No code change needed — operator error

### Step 4: Monitor WS disconnect pattern in future collections

No code change. The 19:19-20:00 UTC disconnect cluster across multiple NBA games suggests a Polymarket-side transient event. All connections auto-recovered within 1-2s. Watch for recurrence in next collection night.

## Verification

```bash
# After Step 1: run Sports WS tests
python -m pytest tests/test_sports_ws_client.py -v

# After Step 1: verify logging works with a quick manual test (optional)
# Start a collector for a known-live game and check logs for league summary lines

# Verify no regressions
python -m pytest tests/ -v
```
