---
name: collection-review
description: Post-collection review. Runs verification scripts on databases, queries DBs for diagnostics, fills the Review section of a collection log, updates coverage table.
disable-model-invocation: true
argument-hint: <YYYY-MM-DD or log-file-path>
---

# /collection-review

Post-collection review: run diagnostics, fill the Review section, update coverage.

## Arguments

`$ARGUMENTS` should be one of:
- A date like `2026-03-26` → resolves to `collection_logs/2026-03-26.md` (nightly) or searches for adhoc logs with that date prefix
- A file path like `collection_logs/2026-03-26-cricket-test.md`

If no argument, check for the most recent collection log with `outcome=pending`.

## Steps

### 1. Check data exists locally

Look for databases in `data/` matching the collection date pattern. If no databases found, remind the user:
```
No databases found for this date. Run: bash scripts/sync_from_cloud.sh
```

Also check `logs/` for collector log files.

### 2. Read the collection log

Read the collection log file. Extract the Game Roster (nightly) or What table (adhoc) to get the list of match_ids and their expected Data predictions.

### 3. Run verification scripts

For each database found:
```bash
python scripts/verify_collection.py data/{db}.db
python scripts/analyze_data_fitness.py data/{db}.db
```

Save outputs to the artifact directory (nightly only):
```bash
python scripts/verify_collection.py data/*YYYY-MM-DD*.db > collection_logs/YYYY-MM-DD/verify.txt 2>&1
python scripts/analyze_data_fitness.py data/*YYYY-MM-DD*.db > collection_logs/YYYY-MM-DD/fitness.txt 2>&1
```

### 4. DB-first diagnostics (primary)

For each database, query SQLite directly for authoritative data:

```sql
-- Collection summary
SELECT event_count, snapshot_count, trade_count, gap_count FROM collection_runs;

-- Event types and counts
SELECT event_type, COUNT(*) as cnt FROM match_events GROUP BY event_type ORDER BY cnt DESC;

-- League detection (from match_events data)
SELECT DISTINCT json_extract(raw_data, '$.leagueAbbreviation') as league FROM match_events WHERE raw_data IS NOT NULL LIMIT 5;

-- Price signal count
SELECT COUNT(*) FROM price_signals;

-- Time span
SELECT MIN(server_ts_ms), MAX(server_ts_ms) FROM price_signals;
```

These DB queries are the **primary** diagnostic source. They are deterministic and authoritative.

### 5. Log greps (supplementary)

If log files exist, grep for supplementary context:
- `"locked to gameId"` — confirms game state client connected
- `"leagues seen"` — shows what leagues the Sports WS was broadcasting (pre-lock only)
- Final `"Status:"` line — collector exit status

These are **supplementary only** — do not rely on them if DB queries provide the answer.

### 6. Fill Review section

#### For nightly logs (`_template_nightly.md`):

Fill each subsection:

**Outcome:**
- Started/Ended times (from DB timestamps)
- Games collected: X / Y planned
- Overall: PASS (all games have expected data), PARTIAL (some issues), FAIL (major problems)

**Diagnostics:**
For each game, fill the per-game diagnostic block:
```markdown
#### {match_id}
- event_count: {from collection_runs}
- snapshot_count: {from collection_runs}
- trade_count: {from collection_runs}
- gap_count: {from collection_runs}
- locked_game_id: {from logs if available}
- league_detected: {from match_events query}
```

**Game State Check:**
For each game where Data was `price+events` or `unknown`:
- Did we get `event_count > 0`?
- What event types were detected?
- If Data was `unknown`, this resolves the prediction.

**Issues & Anomalies:**
Note any unexpected findings (gaps, missing events, disconnects, errors).

**Lessons Learned:**
Extract actionable bullets for `LESSONS_LEARNED.md`.

#### For adhoc logs (`_template_adhoc.md`):

Fill the **Result** section:
- Outcome: PASS / FAIL
- Diagnostics (event_count, locked_game_id, observed_leagues)
- What happened: narrative summary
- Lessons: bullets

### 7. Update coverage table

If any game had Data prediction `unknown`, update the Game State Coverage table in `collection_logs/README.md`:
- `event_count > 0` → change `Unknown` to `Yes`, set `Confirmed` date
- `event_count = 0` → change `Unknown` to `No`, add notes about why

### 8. Append lessons to LESSONS_LEARNED.md

If any new lessons were identified, append concise bullets to `LESSONS_LEARNED.md`.

### 9. Save commands (nightly only)

Save the exact diagnostic commands used to `collection_logs/YYYY-MM-DD/commands.txt`.

### 10. Update Collection Index

Update the outcome in the Collection Index table in `collection_logs/README.md`:
- `pending` → `pass`, `partial`, or `fail`

### 11. Report

Show the user:
- Overall outcome
- Per-game summary table (match_id, events, trades, signals, fitness score)
- Any coverage table updates
- Any new lessons learned
- Path to the filled collection log
