# Collection Log System Plan

> Introduce a structured, repeatable file format for documenting nightly and ad-hoc data collection sessions — capturing intent, hypotheses, and outcomes in a searchable historical record.

---

## Problem Statement

Collection planning is currently mixed into operator runbooks (`plans/Tonight_Collection_*_Plan.md`) with no structured way to capture **why** a game was selected or **what was learned** from the results. Ad-hoc collections (pipeline tests, bug fix validations) happen and are forgotten entirely. Game state coverage knowledge — which sport+league combos actually produce match events — is scattered across CLAUDE.md notes and LESSONS_LEARNED.md bullets, not in a queryable format.

The FIFA qualifier collection exposed a specific gap: soccer is a "supported" sport, but FIFA qualifier matches produced zero game state events because the Sports WS doesn't broadcast that league. There's no systematic way to track or predict this per-league variability.

---

## Design Decisions

### D1: Separate directory from plans

**Decision:** Create `collection_logs/` as a new top-level directory, not inside `plans/`.

**Rationale:** `plans/` contains operator runbooks (how to deploy and run). Collection logs are research records (what we collected, why, and what we learned). Each log includes a Runbook reference linking back to its associated plan.

**Trade-off:** Considered extending `plans/` with a collection log section, but this fragments research intent across runbook files and makes the historical record harder to scan.

### D2: Two templates — nightly and ad-hoc

**Decision:** Separate templates for nightly batches (`_template_nightly.md`) and ad-hoc runs (`_template_adhoc.md`).

**Rationale:** Nightly collections involve 1-15 games, a full roster table, and artifact subdirectories. Ad-hoc collections are 1-2 games for pipeline testing — they need a ~2 minute single-page format with inline commands and results.

**Trade-off:** A single unified template was considered but would either be too heavy for ad-hoc or too sparse for nightly.

### D3: Game state coverage as prediction + verification

**Decision:** The Game Roster includes a **Data** column (`price+events` / `price-only` / `unknown`) set BEFORE collection as a prediction. The Review's **Diagnostics** block captures actual `match_events` counts. When prediction != reality, that's flagged as a finding. The coverage table in README.md is updated ONLY from actual results.

**Rationale:** The Sports WS broadcasts vary by league within the same sport (e.g., NBA yes, FIFA qualifiers unknown). Setting expectations before collection forces documenting assumptions; comparing against actuals turns every collection into a validation opportunity.

### D4: Diagnostics block in the human log

**Decision:** Include a minimal per-game diagnostics block (`observed_leagues`, `locked_game_id`, `match_events`) directly in the human-authored log, grepped from collector logs.

**Rationale:** Without this, reviewers must dig into artifact files or grep raw collector logs to answer the most basic question: "did we get game state?" Three fields per game makes reviews self-contained. Full script outputs remain in artifact files.

**Trade-off:** Adds ~30 seconds of manual grep per game. Worth it for review clarity.

### D5: Artifact storage — nightly gets subdirs, ad-hoc goes inline

**Decision:** Nightly collections store script outputs in `collection_logs/YYYY-MM-DD/` subdirectories with a `commands.txt` for reproducibility. Ad-hoc collections include commands and key results inline in the log file (no subdirectory).

**Rationale:** Ad-hoc runs are 1-2 games with 1-2 verification commands — a subdirectory is overkill. Nightly runs produce multi-page script outputs that would bloat the human log.

### D6: League column in game roster

**Decision:** Every game roster entry includes a **League** column (e.g., `nba`, `psl`, `uef`, `atp`, `challenger`) in addition to Sport.

**Rationale:** Game state coverage varies by league within the same sport. "Soccer" tells you nothing — `uef` (FIFA qualifiers) may not broadcast while `epl` does. The league is the key dimension for coverage tracking.

---

## Implementation Plan

### Step 1: Create directory and templates

Create `collection_logs/` with three files:

**`collection_logs/_template_nightly.md`:**
```markdown
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
- `summary.json` — collection_summary.py --json output
- `commands.txt` — exact commands used

### Diagnostics

<!-- Per-game, grepped from collector logs -->

#### {match_id}
- observed_leagues: ...
- locked_game_id: ...
- match_events: ...

### Game State Check

<!-- For each game where Data was price+events or unknown: did we get events? Update coverage table in README.md. -->

### Validation Results

<!-- One paragraph per validation game answering the hypothesis -->

### Issues & Anomalies

<!-- Unexpected findings with root cause if known -->

### Lessons Learned

<!-- New bullets to propagate to LESSONS_LEARNED.md -->

### Tomorrow's Actions

- [ ] ...
```

**`collection_logs/_template_adhoc.md`:**
```markdown
# Ad-hoc Collection: YYYY-MM-DDThhmm — {label}

> Started: YYYY-MM-DD hh:mm ET | Commit: {sha}

## What

| match_id | Sport | League | Matchup | Data |
|----------|-------|--------|---------|------|
| | | | | |

## Why

<!-- What are you testing? Pipeline fix, new feature, interesting game, etc. -->

## Monitor

<!-- Specific things to check when done -->
- [ ] ...

## Result

<!-- Filled after collection -->
- **Outcome**: PASS / FAIL
- **Diagnostics**:
  - observed_leagues: ...
  - locked_game_id: ...
  - match_events: ...
- **Commands run**:
  ```
  python scripts/verify_collection.py data/{db}.db
  ```
- **What happened**: ...
- **Lessons**: ...
```

**`collection_logs/README.md`:**

Two sections — Collection Index (one line per session with Date/Type/Games/Sports/Outcome/Notes) and Game State Coverage table (Sport/League/Game State?/Source/Confirmed date/Notes). Coverage table pre-populated from known results in CLAUDE.md and LESSONS_LEARNED.md. Coverage entries move from `Unknown` to `Yes`/`No` only based on actual `match_events` counts from a Review.

### Step 2: Create tonight's entry (2026-03-26)

Copy nightly template to `collection_logs/2026-03-26.md`. Fill Plan section using tonight's games (cricket PSL, UEFA qualifiers, NBA/NHL). Set Data column based on known coverage. Review section left as placeholders.

### Step 3: Update project docs

- Add `collection_logs/` to CLAUDE.md project structure section with description of both templates, artifact storage, and the nightly/ad-hoc workflow
- Fix CBB coverage note in README.md (currently contradicts registry.py — CBB IS on Sports WS, confirmed 2026-03-25)

---

## Verification

- Nightly template fillable in <5 min (Plan section only)
- Ad-hoc template fillable in <2 min
- Tonight's entry captures cricket PSL validation hypothesis with Data=`unknown`
- Game roster includes match_id, League, and Data columns for every game
- Diagnostics block has observed_leagues, locked_game_id, match_events per game
- Coverage table seeded with known results, Unknown entries for untested leagues (psl, uef, cs2)
- CLAUDE.md project structure updated to include `collection_logs/`
