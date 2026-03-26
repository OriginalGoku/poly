# UEFA Qualifiers Collection Plan

> Enable data collection from UEFA World Cup qualifier soccer matches on Polymarket — first soccer collection for the project.

---

## Problem Statement

Three UEFA qualifier matches on 2026-03-26 have strong liquidity ($423K–$1.43M volume) on Polymarket. Soccer is already a registered sport in the collector (`SPORTS_WS_SPORTS`), and discovery has generated configs, but two gaps prevent reliable collection: the Sports WS league filter doesn't include UEFA abbreviations, and the fuzzy team matcher doesn't handle diacritics (e.g., "Türkiye" vs "Turkey").

## Matches

| Match | Kickoff | Volume | Tokens |
|-------|---------|--------|--------|
| Türkiye vs Romania | 1:00 PM | $1.43M | 6 (base) + 10 (more-markets) |
| Czechia vs Ireland | 3:45 PM | $628K | 6 |
| Ukraine vs Sweden | 3:45 PM | $423K | 6 |

## Design Decisions

### D1: Add UEFA league codes to LEAGUE_MAP

**Decision:** Add `"uef"` and `"uel"` to `LEAGUE_MAP["soccer"]`.

**Rationale:** The Sports WS client hard-filters by `leagueAbbreviation` before fuzzy team matching. Without the correct code, all game-state messages are silently dropped. We don't yet know the exact abbreviation Polymarket uses for UEFA qualifiers — `"uef"` is our best guess from the config slug pattern. Diagnostic logging (every 60s) will reveal the actual abbreviation if it differs.

**Trade-off:** Could bypass league filtering entirely for soccer, but that risks matching wrong games in a broadcast feed.

### D2: ASCII-fold diacritics in fuzzy team matching

**Decision:** Add `unicodedata.normalize('NFKD')` + strip combining characters to `_name_match()` in `sports_ws_client.py`.

**Rationale:** Config has `Türkiye` (from Gamma API) but the Sports WS feed likely sends `Turkey` or `Turkiye`. The current matcher does case-insensitive substring/token overlap but doesn't normalize diacritics. This is a general fix that benefits all future non-ASCII team names (common in soccer, cricket).

## Implementation Plan

### Step 1: LEAGUE_MAP update (already done)

- **File:** `collector/sports_ws_client.py:35`
- Added `"uef"` and `"uel"` to the soccer league list

### Step 2: Add ASCII folding to `_name_match()`

- **File:** `collector/sports_ws_client.py` — `_fuzzy_team_match()` / `_name_match()` inner function (~line 348)
- Add `import unicodedata` at top of file
- In `_name_match()`, normalize both strings with NFKD and strip combining chars before comparing
- This ensures `türkiye` matches `turkiye` or `turkey`

### Step 3: Run collectors

```bash
# Terminal 1 — Türkiye vs Romania (1:00 PM, highest volume)
python -m collector --config configs/match_uef-tur-rom-2026-03-26.json

# Terminal 2 — Czechia vs Ireland (3:45 PM)
python -m collector --config configs/match_uef-cze-ire-2026-03-26.json

# Terminal 3 — Ukraine vs Sweden (3:45 PM)
python -m collector --config configs/match_uef-ukr-swe-2026-03-26.json
```

Start Türkiye vs Romania early to verify Sports WS broadcasts UEFA games. Watch logs for `observed_leagues` diagnostic (every 60s) — if `uef` doesn't appear but a different abbreviation does, hot-fix LEAGUE_MAP.

### Step 4: Post-match verification

```bash
python scripts/verify_collection.py data/uef-*.db
python scripts/analyze_data_fitness.py data/uef-*.db
```

Check specifically:
- `match_events > 0` — confirms Sports WS game-state capture worked
- Trade count and price signal density during live play
- Spread distribution and liquidity profile

## Known Issues (Low Priority)

- **Stale `scheduled_start`:** Configs show `2026-02-27` instead of `2026-03-26`. Cosmetic — comes from Gamma API metadata, doesn't affect collection or game-state matching. Fix by re-running discovery or manual patch if it causes confusion in post-match analysis.

## Verification

1. `python -m pytest tests/ -v -k sports` — no regressions from LEAGUE_MAP + ASCII folding changes
2. Start Türkiye vs Romania collector before 1:00 PM, confirm `uef` in `observed_leagues` log output
3. After matches, `verify_collection.py` and `analyze_data_fitness.py` on output DBs
4. Confirm `match_events` table has rows (score_change, period_change, game_end)
