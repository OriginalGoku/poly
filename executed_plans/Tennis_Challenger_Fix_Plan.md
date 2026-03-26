# Tennis Challenger Fix Plan

> Add Challenger league support to Sports WS client and regenerate all stale tennis configs.

---

## Problem Statement

The Polymarket Sports WebSocket broadcasts tennis events with two distinct `leagueAbbreviation` values: `"atp"` and `"challenger"` (confirmed via live sniff 2026-03-25). The current `LEAGUE_MAP` for tennis only includes `["atp", "wta"]`, so all Challenger tennis messages are silently dropped by the league filter in `sports_ws_client.py`.

Additionally, all 150 existing tennis config files (124 ATP + 26 WTA) have `data_source: "none"` because they were generated before the Sports WS integration was added to `discover_markets.py`. These need regeneration.

Discovered during CBB support work — the sniff script (`scripts/sniff_sports_ws.py`) captured both `atp` and `challenger` as active leagues alongside `mlb`, `nba`, `nhl`.

## Design Decisions

### D1: Add `"challenger"` to existing tennis LEAGUE_MAP entry

**Decision:** Append `"challenger"` to the tennis list in `LEAGUE_MAP` rather than creating a separate `"challenger"` sport.

**Rationale:** Challenger is a tier of ATP tennis, not a separate sport. The same event detection logic (score_change, period_change, game_start, game_end) applies. Polymarket also classifies Challenger matches under tennis tags.

### D2: Add `"challenger"` keyword to SPORT_CLASSIFY as safety net

**Decision:** Add `"challenger"` to the tennis keyword list in `discover_markets.py`.

**Rationale:** Currently all Gamma Challenger events include a `"Tennis"` tag, so they already classify correctly. However, if a future event title contains only "Challenger" without "tennis"/"atp"/"wta", it would fall through to `unknown`. Adding the keyword is a cheap safety net. Verified via Gamma API that no current events are affected.

**Risk note:** Substring `"challenger"` also matches `"challengers"` (e.g. "Valorant Challengers", "LoL Challenger Series"). Mitigated by SPORT_CLASSIFY ordering — `valorant` (line 36) and `lol` (line 35) are checked before `tennis` (line 44), so those events match their correct sport first. A negative test is added to lock in this ordering defense.

### D3: Full config regeneration (not targeted migration)

**Decision:** Re-run `discover_markets.py` to regenerate all configs rather than writing a targeted migration script.

**Rationale:** All 736 configs are auto-generated from live Gamma data — none are manually curated. Full regeneration is the normal workflow and ensures all sports get correct `data_source` values. A targeted migration would add complexity for no benefit.

**Trade-off:** Rejected targeted migration (update only tennis configs' `data_source` field) — unnecessary complexity given configs are disposable and regenerated regularly.

**Caveat:** `discover_markets.py` only writes configs for currently active events — it does not delete old files. The 150 stale tennis configs with `data_source: "none"` will remain on disk (their events have expired). This is harmless since expired events won't be collected. Optionally `rm configs/match_tennis-*.json` before regeneration for a clean slate.

## Implementation Plan

### Step 1: Add `"challenger"` to LEAGUE_MAP

**File:** `collector/sports_ws_client.py` (line 30)

Change `"tennis": ["atp", "wta"]` to `"tennis": ["atp", "wta", "challenger"]`.

### Step 2: Add `"challenger"` to SPORT_CLASSIFY keywords

**File:** `scripts/discover_markets.py` (line 44)

Add `"challenger"` to the tennis keyword list:
```python
(["tennis", "atp", "wta", "wimbledon", "open", "dubai", "lugano", "challenger"], "tennis", "polymarket_sports_ws"),
```

### Step 3: Add tests

**File:** `tests/test_sports_ws_client.py`

Add a behavioral test: create a client with `sport="tennis"`, verify `_matches_our_game()` returns `True` for a synthetic message with `leagueAbbreviation: "challenger"` and matching team names.

**File:** `tests/test_discover.py`

Add tests:
- Positive: `classify_sport("Challenger Braga: Rico vs Bertran", [], "")` returns `("tennis", "polymarket_sports_ws")`
- Negative (ordering defense): `classify_sport("Valorant Challengers: Team A vs Team B", ["Valorant"], "")` returns `("valorant", "riot")` — confirms esports events with "challenger" in the title are not misclassified as tennis

### Step 4: Regenerate configs

```bash
rm configs/match_tennis-*.json   # clean stale configs (optional)
python scripts/discover_markets.py
```

Verify:
- Newly generated tennis configs have `data_source: "polymarket_sports_ws"` (not `"none"`)
- CBB configs have `data_source: "none"` (control group)
- NBA configs still have `data_source: "nba_cdn"`

### Step 5: Run full test suite

```bash
python -m pytest tests/ -v
```

### Step 6: Update documentation

- **CLAUDE.md**: Note `challenger` as a confirmed Sports WS league
- **LESSONS_LEARNED.md**: Add lesson about stale configs from pre-Sports-WS discovery runs

## Files Modified

| File | Change |
|------|--------|
| `collector/sports_ws_client.py` | Add `"challenger"` to tennis LEAGUE_MAP entry |
| `scripts/discover_markets.py` | Add `"challenger"` to tennis SPORT_CLASSIFY keywords |
| `tests/test_sports_ws_client.py` | Add Challenger league filter behavioral test |
| `tests/test_discover.py` | Add Challenger positive + negative classification tests |
| `configs/*.json` | Regenerated by `discover_markets.py` |

## Verification

1. New tests pass: Challenger league accepted for tennis sport + keyword classification + esports negative test
2. All existing tests pass (193+)
3. Newly generated tennis configs have `data_source: "polymarket_sports_ws"`
4. CBB configs unaffected (`data_source: "none"`)
5. NBA configs unaffected (`data_source: "nba_cdn"`)
