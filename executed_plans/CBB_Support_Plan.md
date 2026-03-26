# CBB Support Plan

> Add College Basketball data collection via the existing Polymarket Sports WebSocket pipeline.

---

## Problem Statement

Polymarket has active CBB markets (March Madness — Illinois State vs Dayton, Nevada vs Auburn, etc.) but the collector pipeline cannot handle them. The `"basketball"` keyword in `discover_markets.py` routes all basketball to `sport: "nba"` with `data_source: "nba_cdn"`, so CBB markets are misclassified. Confirmed: 37 existing `cbb-*` prefixed configs are tagged `sport: "nba"`. The Sports WS pipeline already handles generic event detection (score_change, period_change, game_start, game_end), so CBB support requires only routing and filtering changes — no new client code.

**Critical unknown:** The exact `leagueAbbreviation` value Polymarket broadcasts for CBB in the Sports WS feed. Must be discovered by sniffing the live feed.

## Design Decisions

### D1: Slug-based classification (primary) + keyword fallback

**Decision:** Add a slug-prefix check (`cbb-`) to `classify_sport()` before the keyword loop, plus a CBB keyword entry (`"ncaa"`, `"march madness"`, etc.) before the NBA entry as a safety net.

**Rationale:** Polymarket already uses `cbb-` prefixed slugs for all known CBB events. Slug-based classification is more reliable than keyword matching on titles/tags. The keyword fallback catches any hypothetical CBB events that don't follow the `cbb-` slug convention.

**Trade-off:** An earlier approach proposed removing `"basketball"` from the NBA keyword entry to prevent CBB leakage. Rejected because NBA events may only be tagged "basketball" without explicitly saying "NBA" — removing the keyword risks breaking NBA classification. The slug check runs first, so `cbb-` events never reach the `"basketball"` match. NBA keywords stay untouched.

### D2: Sniff-first for league abbreviation

**Decision:** Write a temporary script to capture the actual `leagueAbbreviation` from the live Sports WS feed before making code changes.

**Rationale:** The WS client uses a strict league allow-list. If we guess wrong, the client silently drops all CBB messages with no error. Sniffing during a live game is the only reliable way to discover the exact value(s). Multiple variants may exist (men's/women's, tournament vs regular season).

### D3: Fuzzy match collision risk accepted (deferred)

**Decision:** Do not modify `_fuzzy_team_match()` for CBB. Accept the pre-existing collision risk.

**Rationale:** College team names share tokens like "State" and "University" more often than pro teams, increasing false-positive match risk during March Madness (many concurrent games). However, the `gameId` lock-on after 2 consecutive matches mitigates this. The fuzzy matching limitation is pre-existing and affects all sports — fixing it is a separate effort.

## Implementation Plan

### Step 0: Sniff the Sports WS feed

Write `scripts/sniff_sports_ws.py` (temporary — delete after use). Connect to `wss://sports-api.polymarket.com/ws`, listen ~30s, print all unique `leagueAbbreviation` values and any messages matching known CBB team names. Run during a live CBB game.

### Step 1: Update `scripts/discover_markets.py`

1. Add `slug` parameter to `classify_sport()`: `def classify_sport(title: str, tags: list, slug: str = "") -> tuple[str, str]:`
2. Add slug-prefix check before keyword loop: `if slug.startswith("cbb-"): return "cbb", "polymarket_sports_ws"`
3. Add CBB keyword entry to `SPORT_CLASSIFY` before the NBA entry: `(["ncaa", "march madness", "college basketball", "ncaab"], "cbb", "polymarket_sports_ws")`
4. Update call site (line 201) to pass slug: `classify_sport(event.get("title", ""), tags, slug)`
5. Optionally add `"college-basketball"` to `TAG_SLUGS` if Gamma API uses that tag

### Step 2: Update `collector/sports_ws_client.py`

Add `"cbb"` to `LEAGUE_MAP` (line ~29) with discovered abbreviation(s):
```python
"cbb": ["ncaab", "<other discovered values>"],
```

### Step 3: Update `collector/game_state/registry.py`

Add `"cbb"` to `SPORTS_WS_SPORTS` (line 18).

### Step 4: Add tests

**`tests/test_sports_ws_client.py`:**
- League filter: discovered abbreviation accepted for sport `"cbb"`
- Fuzzy team match: `"Dayton"` matches `"Dayton Flyers"`
- Event detection: score_change with CBB-shaped message

**`tests/test_discover.py`** (new):
- Slug path: `classify_sport("...", [], "cbb-dayton-illst-2026-03-25")` returns `("cbb", "polymarket_sports_ws")`
- Keyword fallback: `classify_sport("NCAA March Madness ...", [], "some-slug")` returns `("cbb", "polymarket_sports_ws")`
- No NBA regression: `classify_sport("Hawks vs Celtics", [], "nba-atl-bos-2026-03-27")` returns `("nba", "nba_cdn")`
- Legacy no-slug: `classify_sport("Hawks vs Celtics", [{"label": "Basketball"}], "")` returns `("nba", "nba_cdn")`

### Step 5: Run discovery and verify

```bash
python scripts/discover_markets.py
```
- CBB markets: `sport: "cbb"`, `data_source: "polymarket_sports_ws"`
- NBA markets: `sport: "nba"`, `data_source: "nba_cdn"`
- No cross-contamination

### Step 6: Run full test suite

```bash
python -m pytest tests/ -v
```

## Files Modified

| File | Change |
|------|--------|
| `scripts/discover_markets.py` | Add slug param + prefix check + CBB keyword entry |
| `collector/sports_ws_client.py` | Add `"cbb"` to `LEAGUE_MAP` |
| `collector/game_state/registry.py` | Add `"cbb"` to `SPORTS_WS_SPORTS` |
| `tests/test_sports_ws_client.py` | Add CBB league filter + team match tests |
| `tests/test_discover.py` | New: classify_sport tests (slug, keyword, no-regression) |
| `scripts/sniff_sports_ws.py` | Temporary sniff script (delete after) |

## Verification

1. Sniff script captures CBB messages with correct `leagueAbbreviation`
2. `discover_markets.py` classifies CBB via both slug and keyword paths
3. All existing tests pass (181+)
4. New tests pass (classify_sport + WS client CBB cases)
5. Generate tonight's CBB config and smoke-test collector ~60s
