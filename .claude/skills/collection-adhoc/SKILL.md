---
name: collection-adhoc
description: Plan an ad-hoc data collection. Use after code changes, for pipeline validation, or to find and collect specific games. Can search for markets by sport/volume/criteria.
disable-model-invocation: true
argument-hint: [label]
---

# /collection-adhoc

Plan an ad-hoc data collection session for pipeline validation, specific games, or testing code changes.

## Arguments

`$ARGUMENTS` is an optional label for the session (e.g., `cricket-test`, `ws-fix-validation`). If not provided, ask the user for a brief label.

## Steps

### 1. Determine purpose

Ask the user what this ad-hoc collection is for:
- **Code validation** — testing a code change (read recent git diff to understand what changed)
- **Specific game** — user wants to collect a particular match
- **Pipeline test** — smoke test of the full pipeline
- **New sport/league** — first collection for an untested sport/league

### 2. Find games

Based on purpose:
- If user specifies match_ids, use those directly
- If searching, read `configs/discovery_summary.json` and present matches filtered by the user's criteria (sport, volume, timing)
- For pipeline tests, suggest a small game (few tokens) starting soon

### 3. Derive league and data prediction

Use the same league prefix mapping as `/collection-tonight`:
```
nba-       → nba          nhl-       → nhl
cbb-       → cbb          mlb-       → mlb
cricpsl-   → psl          criclcl-   → lcl
crint-     → int          atp-       → atp
challenger-→ challenger   uef-       → uef
fif-       → fif          cs2-       → cs2
dota2-     → dota2        soccer-    → soccer (generic)
val-       → valorant     wta-       → wta
```

Look up Data prediction from `collection_logs/README.md` Game State Coverage table.

### 4. Create collection log

1. Copy `collection_logs/_template_adhoc.md` → `collection_logs/YYYY-MM-DD-{label}.md`
2. Fill the header: start time, current git commit SHA
3. Fill the **What** table with selected games
4. Fill the **Why** section based on the stated purpose
5. Fill the **Monitor** section with specific things to check:
   - For code validation: the specific behavior being tested
   - For new sport/league: `match_events > 0`, league abbreviation in logs
   - For pipeline tests: basic data quality (snapshots, trades, no gaps)

### 5. Update Collection Index

Add a row to the Collection Index table in `collection_logs/README.md`:

```
| YYYY-MM-DD | adhoc | N | sport | pending | {label} | [log](YYYY-MM-DD-{label}.md) |
```

### 6. Report

Show the user:
- The filled game table
- Monitor checklist
- Path to the collection log file
- Suggest next step: `/collection-run` or manual launch command if just 1-2 games
