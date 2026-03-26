# Collection Skills Plan

> Convert the collection log system into 4 Claude Code repo-level skills with a one-time manual directory setup. Collections execute on an Oracle VM via SSH + tmux.

---

## Problem Statement

Collection planning is currently ad-hoc markdown in `plans/`. There's no structured way to capture why games were selected, what was learned, or which sport/league combos actually produce game state events. The FIFA qualifier collection exposed this gap — soccer is "supported" but FIFA qualifiers produced zero events because the Sports WS doesn't broadcast that league.

Additionally, the migration from Raspberry Pi to Oracle VM requires a repeatable launch workflow that handles config syncing (gitignored files), VM memory limits (1GB RAM, max 5 concurrent collectors), and fire-and-forget execution.

---

## Design Decisions

### D1: Four separate skills instead of one or two

**Decision:** Split into `/collection-tonight`, `/collection-adhoc`, `/collection-run`, `/collection-review`.

**Rationale:** Each has a distinct trigger, different autonomy level, and separate template context. Nightly is discovery-driven and systematic; ad-hoc is reactive and may involve the LLM searching for markets. Execution and review are orthogonal to planning.

**Trade-off:** Considered a single skill with `nightly|adhoc` argument, but the LLM behavior differs significantly between modes — separate SKILL.md files keep instructions focused and shorter.

### D2: Manual one-time setup, not skill-bootstrapped

**Decision:** Create `collection_logs/` directory, templates, and README manually. Skills assume the structure exists.

**Rationale:** Bootstrap logic runs once but clutters every subsequent invocation. Static templates are repo files that should just exist.

### D3: DB-first diagnostics over log greps

**Decision:** Post-collection diagnostics query `collection_runs` and `match_events` SQLite tables as primary source. Log greps are supplementary only.

**Rationale:** Three issues with log-based diagnostics discovered in brainstorm review:
- `match_events` count is already in `collection_runs.event_count` — deterministic from DB
- `observed_leagues` diagnostic only logs pre-lock (stops once gameId locks), so it's incomplete
- `leagueAbbreviation` can be extracted from `match_events` table rows

### D4: Automated batching with `wait` for VM memory constraint

**Decision:** `/collection-run` generates a script that batches collectors (max 10 concurrent), using `wait` between batches. Fully automated — no manual monitoring.

**Rationale:** Oracle VM has 956MB RAM. Measured usage shows ~27MB per collector (regardless of token count). User does not want to manually monitor the VM. Sorting by `scheduled_start` and using `wait` ensures batches complete before the next starts.

**Trade-off:** Separate tmux windows with manual batch start was considered but rejected — requires operator attention.

### D5: Config rsync before launch

**Decision:** `/collection-run` must rsync configs from local Mac to VM before launching collectors.

**Rationale:** Configs are gitignored. `git pull` on the VM won't bring new configs. The exact rsync command is documented in `ORACLE_DATA_COLLECTOR.md`.

### D6: League = Polymarket slug heuristic

**Decision:** Derive league from match_id prefix (e.g., `cricpsl-` → `psl`). Label as heuristic in skill instructions.

**Rationale:** `MatchConfig` has no league field. `discovery_summary.json` only groups by sport. The match_id prefix comes from Polymarket's slug convention — works empirically across all ~2,825 configs but isn't guaranteed by our code. For post-collection coverage updates, DB-queried `match_events` data is authoritative.

### D7: Coverage table is league-level

**Decision:** Game State Coverage table in `collection_logs/README.md` tracks coverage per league, not per sport.

**Rationale:** Game state varies by league within the same sport (NBA yes, FIFA qualifiers unknown). Seeded from `registry.py` SPORTS_WS_SPORTS (sport-level ground truth) + dated confirmations in LESSONS_LEARNED.md (league-level).

### D8: Environment variables for VM connection

**Decision:** Store Oracle VM connection details in env vars (`ORACLE_VM_HOST`, `ORACLE_VM_USER`, `ORACLE_VM_REPO_PATH`), not in repo files.

**Rationale:** SSH credentials shouldn't be in committed files. Env vars are set in shell profile.

---

## Implementation Plan

### Step 0: One-time manual setup

Create `collection_logs/` directory with static files, committed to repo.

**Files to create:**
- `collection_logs/_template_nightly.md` — from `plans/Collection_Log_System_Plan.md` lines 69-143
- `collection_logs/_template_adhoc.md` — from `plans/Collection_Log_System_Plan.md` lines 147-181
- `collection_logs/README.md` — Collection Index table + Game State Coverage table

**Coverage table** seeded from `collector/game_state/registry.py` + `LESSONS_LEARNED.md`:

| Sport | League | Game State? | Source | Confirmed | Notes |
|-------|--------|-------------|--------|-----------|-------|
| nba | nba | Yes | nba_cdn | 2026-03-25 | CDN play-by-play |
| nhl | nhl | Yes | nhl_api | 2026-03-25 | Local timestamps only |
| cbb | cbb | Yes | polymarket_sports_ws | 2026-03-25 | Confirmed Sweet 16 |
| mlb | mlb | Yes | polymarket_sports_ws | 2026-03-25 | Mid-game start |
| tennis | atp | Yes | polymarket_sports_ws | 2026-03-25 | |
| tennis | challenger | Yes | polymarket_sports_ws | 2026-03-25 | Needed LEAGUE_MAP fix |
| dota2 | - | Yes | opendota | 2026-03-26 | OpenDota live diff |
| cricket | psl | Unknown | polymarket_sports_ws | - | First test tonight |
| soccer | uef | Unknown | polymarket_sports_ws | - | UEFA qualifiers |
| soccer | fif | Unknown | polymarket_sports_ws | - | FIFA qualifiers |
| cs2 | - | No | pandascore | - | Needs API token |
| valorant | - | No | riot | - | Needs API key |

**Also update:**
- `CLAUDE.md` — add `collection_logs/` to project structure
- `README.md` — fix CBB coverage (currently wrong — says not on Sports WS, but it is)
- `.claude/settings.local.json` — add `Skill(collection-tonight)`, `Skill(collection-adhoc)`, `Skill(collection-run)`, `Skill(collection-review)`

### Step 1: Skill `/collection-tonight`

**File:** `.claude/skills/collection-tonight/SKILL.md`

**Frontmatter:**
```yaml
---
name: collection-tonight
description: Build tonight's collection roster. Reads discovery summary, presents available games by sport, helps select which to collect, populates nightly template with game roster and coverage predictions.
disable-model-invocation: true
---
```

**Skill instructions cover:**
1. Read `configs/discovery_summary.json` — warn if `discovered_at` is not today
2. Present sport summary table (matches, volume, has_game_state)
3. Help user select sports/games
4. Read individual match configs for team names, volume, token counts
5. Derive League from match_id prefix (heuristic mapping table included in skill)
6. Look up Data prediction from coverage table in `collection_logs/README.md`
7. Copy nightly template → `collection_logs/YYYY-MM-DD.md`, fill Plan section
8. Create artifact dir `collection_logs/YYYY-MM-DD/`
9. Add entry to Collection Index (outcome=pending)

**League prefix mapping** (include in skill):
```
nba- → nba    nhl- → nhl    cbb- → cbb    mlb- → mlb
cricpsl- → psl    criclcl- → lcl    crint- → int
atp- → atp    challenger- → challenger
uef- → uef    fif- → fif    cs2- → cs2    dota2- → dota2
soccer- → soccer (generic)    val- → valorant
```

### Step 2: Skill `/collection-run`

**File:** `.claude/skills/collection-run/SKILL.md`

**Frontmatter:**
```yaml
---
name: collection-run
description: Launch data collectors on the Oracle VM. Rsyncs configs, generates a batched launch script (max 10 concurrent), SSHs to the VM, and runs collectors in tmux. Fire-and-forget.
disable-model-invocation: true
argument-hint: <collection-log-path or date>
---
```

**Skill instructions cover:**
1. Read `ORACLE_DATA_COLLECTOR.md` for VM details (IP, SSH key, repo path, RAM constraints)
2. Resolve collection log from argument, extract match_ids from Game Roster
3. Verify all configs exist locally
4. Rsync configs to VM: `rsync -avz -e "ssh -i ~/.ssh/oracle_poly.key" configs/ ubuntu@$ORACLE_VM_HOST:$ORACLE_VM_REPO_PATH/configs/`
5. Generate batched launch script:
   - Sort games by `scheduled_start`
   - Max 10 concurrent per batch (~27MB each, 1GB VM)
   - Background processes + `wait` between batches
   - `free -h` check between batches
   - Trap SIGINT/SIGTERM for graceful shutdown
6. SSH to VM, `git pull`, activate venv, start tmux session `collection-{date}`
7. Execute script in tmux, disconnect
8. Report: tmux session name, batch layout, attach command, sync command for later

### Step 3: Skill `/collection-adhoc`

**File:** `.claude/skills/collection-adhoc/SKILL.md`

**Frontmatter:**
```yaml
---
name: collection-adhoc
description: Plan an ad-hoc data collection. Use after code changes, for pipeline validation, or to find and collect specific games. Can search for markets by sport/volume/criteria.
disable-model-invocation: true
argument-hint: [label]
---
```

**Skill instructions cover:**
1. Parse `$ARGUMENTS` for optional label
2. Ask purpose (code validation, specific game, pipeline test)
3. Search for markets or accept user-specified match_ids
4. Copy adhoc template → `collection_logs/YYYY-MM-DD-{label}.md`, fill sections
5. Update Collection Index (outcome=pending)
6. No artifact subdirectory

**Key difference:** More autonomous than `/collection-tonight` — may search markets, read recent code changes, suggest monitoring checks.

### Step 4: Skill `/collection-review`

**File:** `.claude/skills/collection-review/SKILL.md`

**Frontmatter:**
```yaml
---
name: collection-review
description: Post-collection review. Runs verification scripts on databases, queries DBs for diagnostics, fills the Review section of a collection log, updates coverage table.
disable-model-invocation: true
argument-hint: <YYYY-MM-DD or log-file-path>
---
```

**Skill instructions cover:**
1. Resolve collection log from argument
2. Check data/logs exist locally — remind to run `bash scripts/sync_from_cloud.sh` if missing
3. Find databases by date pattern
4. Run `verify_collection.py` + `analyze_data_fitness.py`, save outputs
5. DB-first diagnostics (primary):
   - `SELECT event_count, snapshot_count, trade_count, gap_count FROM collection_runs`
   - `SELECT DISTINCT event_type, COUNT(*) FROM match_events GROUP BY event_type`
   - Extract league from match_events if available
6. Log greps (supplementary): `"locked to gameId"`, `"leagues seen"`, final `"Status:"` line
7. Fill Review section: Outcome, Diagnostics, Game State Check, Issues, Lessons
8. Update coverage table: `Unknown` → `Yes`/`No` from DB event_count
9. Append lessons to `LESSONS_LEARNED.md`
10. Save `commands.txt` (nightly), update Collection Index outcome

### Step 5: Archive both plans

Once all 4 skills are implemented and verified, move both plans to `executed_plans/`:
- `plans/Collection_Log_System_Plan.md` → `executed_plans/` (original design, templates sourced from here)
- `plans/Collection_Skills_Plan.md` → `executed_plans/` (this plan)

---

## Critical files

| File | Role |
|------|------|
| `ORACLE_DATA_COLLECTOR.md` | VM details, SSH key path, RAM limits, rsync commands |
| `collector/game_state/registry.py` | `SPORTS_WS_SPORTS` — sport-level coverage ground truth |
| `configs/discovery_summary.json` | Available markets by sport |
| `plans/Collection_Log_System_Plan.md` | Templates to copy (lines 69-143, 147-181) |
| `scripts/verify_collection.py` | Post-collection DB verification |
| `scripts/analyze_data_fitness.py` | Fitness scoring (0-100) |
| `scripts/sync_from_cloud.sh` | Rsync data/logs from Oracle VM |
| `collector/__main__.py` | Status line format, collection_runs table |
| `collector/sports_ws_client.py` | "leagues seen" and "locked to gameId" log messages |

## Implementation order

1. Step 0: Manual setup (collection_logs/ dir, templates, README, config updates)
2. Step 1: `/collection-tonight` (most immediate need)
3. Step 2: `/collection-run` (need to launch collectors)
4. Step 3: `/collection-adhoc` (can wait — ad-hoc runs work manually)
5. Step 4: `/collection-review` (needed after first run completes)
6. Step 5: Archive both plans (`Collection_Log_System_Plan.md` + `Collection_Skills_Plan.md` → `executed_plans/`)

---

## Verification

1. `/collection-tonight` creates `collection_logs/YYYY-MM-DD.md` with filled roster from discovery summary
2. `/collection-run YYYY-MM-DD` rsyncs configs, generates batched script (<=10 per batch with `wait`), SSHs to VM, starts tmux, reports attach command
3. For >10 games: verify script has multiple batches with `wait` between them
4. `/collection-adhoc label` creates adhoc log, can search for configs by criteria
5. `/collection-review YYYY-MM-DD` syncs data, queries DBs, fills Review section, updates coverage
6. Coverage table transitions `Unknown` → `Yes`/`No` based on actual DB event_count
7. Collection Index tracks all sessions with outcomes
