# Collection Log: YYYY-MM-DD

> Discovered: YYYY-MM-DDThh:mm UTC | Games: NN | Sports: ...
> Runbook: plans/Tonight_Collection_YYYY-MM-DD_Plan.md
> Commit: {sha}

## Plan

### Tonight's Focus

<!-- 2-3 sentences: what makes tonight different? -->

### Game Roster

| match_id | Sport | League | Matchup | Volume | Data | Type | Hypothesis |
|----------|-------|--------|---------|--------|------|------|------------|
| | | | | | | | |

**Data**: `price+events` / `price-only` / `unknown`
**Type**: `routine` / `validation`

### Skipped / Deferred

<!-- Games not collected and why -->

### Overrides

<!-- Diff-from-default only. See Runbook for full operational steps. -->

---

## Review

### Outcome

- **Started**: hh:mm ET | **Ended**: hh:mm ET
- **Games collected**: NN / NN planned
- **Overall**: PASS / PARTIAL / FAIL

### Artifacts

See `collection_logs/YYYY-MM-DD/`:
- `verify.txt` — verify_collection.py output
- `fitness.txt` — analyze_data_fitness.py output
- `commands.txt` — exact commands used

### Diagnostics

<!-- Per-game, from DB queries + log greps -->

#### {match_id}
- event_count: ...
- locked_game_id: ...
- observed_leagues: ...

### Game State Check

<!-- For each game where Data was price+events or unknown: did we get events? Update coverage table in collection_logs/README.md. -->

### Validation Results

<!-- One paragraph per validation game answering the hypothesis -->

### Issues & Anomalies

<!-- Unexpected findings with root cause if known -->

### Lessons Learned

<!-- New bullets to propagate to LESSONS_LEARNED.md -->

### Tomorrow's Actions

- [ ] ...
