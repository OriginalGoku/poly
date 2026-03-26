---
name: collection-tonight
description: Build tonight's collection roster. Reads discovery summary, presents available games by sport, helps select which to collect, populates nightly template with game roster and coverage predictions.
disable-model-invocation: true
---

# /collection-tonight

Build tonight's data collection roster from the discovery summary.

## Steps

### 1. Read discovery summary

Read `configs/discovery_summary.json`. Check `discovered_at` — if it is not today, warn the user and ask whether to proceed with stale data or re-run `python scripts/discover_markets.py` first.

### 2. Present sport summary

Build a summary table from the discovery data:

| Sport | Matches | Total Volume | Has Game State? |
|-------|---------|-------------|-----------------|

For "Has Game State?", check `collector/game_state/registry.py`:
- Sports in `SPORTS_WS_SPORTS` or with entries in `IMPLEMENTED_SOURCES` → Yes
- Sports in `CONTROL_GROUP_SPORTS` → No (control group)
- Otherwise → Unknown

Present this table and ask which sports/games to collect.

### 3. Read selected match configs

For each selected game, read its config file from `configs/` to extract:
- `match_id`
- Team names (from `event_slug` or market questions)
- Total volume across all markets
- Token count (sum of all `token_id` entries)

### 4. Derive league and data prediction

**League prefix mapping** (heuristic — derived from Polymarket slug conventions):
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

For the **Data** column, look up the league in `collection_logs/README.md` Game State Coverage table:
- `Game State? = Yes` → `price+events`
- `Game State? = No` → `price-only`
- `Game State? = Unknown` → `unknown`

### 5. Create collection log

1. Copy `collection_logs/_template_nightly.md` → `collection_logs/YYYY-MM-DD.md` (today's date)
2. Fill the header metadata:
   - `Discovered:` timestamp from `discovery_summary.json`
   - `Games:` count
   - `Sports:` comma-separated list
3. Write a 2-3 sentence "Tonight's Focus" based on what's notable (new sports, validation games, high volume, etc.)
4. Fill the Game Roster table with all selected games:
   - `match_id`, `Sport`, `League` (from prefix mapping), `Matchup`, `Volume`, `Data` (from coverage lookup), `Type` (routine or validation), `Hypothesis` (brief note if relevant)
5. Fill "Skipped / Deferred" with games not selected and why

### 6. Create artifact directory

```bash
mkdir -p collection_logs/YYYY-MM-DD/
```

### 7. Update Collection Index

Add a row to the Collection Index table in `collection_logs/README.md`:

```
| YYYY-MM-DD | nightly | N | sport1, sport2 | pending | ... | [log](YYYY-MM-DD.md) |
```

### 8. Report

Show the user:
- The filled Game Roster table
- Total token count (relevant for VM memory: ~27MB per collector, max 10 concurrent)
- Any `unknown` data predictions that tonight will validate
- Path to the collection log file
- Remind: run `/collection-run YYYY-MM-DD` to launch collectors on the VM
