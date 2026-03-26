# Plan: Add CBB (College Basketball) Support to Sports WS Client

## Context
During live sniffing on 2026-03-25, `league=cbb` was observed on the Polymarket Sports WebSocket for the Auburn vs Nevada game (gid=73445). This contradicts our earlier finding that CBB was absent. Polymarket appears to have added CBB coverage. We need to update the codebase to treat CBB as a Sports WS-covered sport instead of a control group.

## Steps

### Step 0: Run baseline tests
```bash
python -m pytest tests/ -v
```
Record pass/fail counts.

### Step 1: Verify CBB on the WS feed
Run `scripts/sniff_sports_ws.py` to confirm `league=cbb` messages and note the exact `leagueAbbreviation` value.

**If CBB is NOT on the feed** (no live game right now), proceed using `"cbb"` based on the user's sniff observation (`league=cbb`).

### Step 2: Update `LEAGUE_MAP` in `collector/sports_ws_client.py` (~line 29)
Add CBB entry:
```python
"cbb": ["cbb", "ncaab"],
```

### Step 3: Update `SPORT_CLASSIFY` in `scripts/discover_markets.py` (~line 37)
Change:
```python
(["ncaa", "march madness", "college basketball", "ncaab"], "cbb", "none"),
```
To:
```python
(["ncaa", "march madness", "college basketball", "ncaab"], "cbb", "polymarket_sports_ws"),
```

Also update the slug-based classification (~line 52-53):
```python
if slug.startswith("cbb-"):
    return "cbb", "none"
```
To:
```python
if slug.startswith("cbb-"):
    return "cbb", "polymarket_sports_ws"
```

### Step 4: Update `registry.py` (`collector/game_state/registry.py`)
- Add `"cbb"` to `SPORTS_WS_SPORTS` set (~line 18)
- Remove `"cbb"` from `CONTROL_GROUP_SPORTS` set (~line 21)

### Step 5: Add tests
**`tests/test_sports_ws_client.py`**: Add a CBB league filter test following existing pattern (e.g., the `test_league_filter_cbb_dayton` test at line 128).

**`tests/test_discover.py`**: Update existing CBB tests to assert `source == "polymarket_sports_ws"` instead of `source == "none"`. Tests affected:
- `test_slug_cbb_prefix` (line 15)
- `test_slug_cbb_ignores_basketball_keyword` (line 21)
- `test_keyword_ncaa` (line 32)
- `test_keyword_march_madness` (line 37)
- `test_keyword_college_basketball` (line 41)
- `test_keyword_ncaab` (line 45)

### Step 6: Regenerate configs
```bash
python scripts/discover_markets.py
```
Verify CBB configs now have `data_source: "polymarket_sports_ws"`.

### Step 7: Update documentation
**`CLAUDE.md`**:
- Line 84: Remove "Not available for CBB" clause. Add `cbb` to observed leagues list.
- Line 85: Add `cbb` to the observed leagues list, remove the "No CBB/NCAAB" note.
- Line 110: Update CBB note — now covered by Sports WS, no longer control group.

**`LESSONS_LEARNED.md`**:
- Line 37: Update the note about CBB not being on Sports WS — now it is. Keep the lesson about sniffing first, but note coverage was added later.

### Step 8: Run tests
```bash
python -m pytest tests/ -v
```
All tests should pass, including updated CBB tests.

## Key Files
- `collector/sports_ws_client.py` — LEAGUE_MAP
- `scripts/discover_markets.py` — SPORT_CLASSIFY + slug classification
- `collector/game_state/registry.py` — SPORTS_WS_SPORTS, CONTROL_GROUP_SPORTS
- `tests/test_sports_ws_client.py` — league filter tests
- `tests/test_discover.py` — classification tests
- `CLAUDE.md`, `LESSONS_LEARNED.md` — documentation

## Verification
1. `python -m pytest tests/ -v` — all tests pass
2. `python scripts/discover_markets.py` — CBB configs have `data_source: "polymarket_sports_ws"`
3. Grep for `"none"` in CBB-related code — should be gone
