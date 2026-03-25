# Repo Audit Plan

> Conservative codebase audit to clean up REST-era drift without risking tonight's data collection.

---

## Problem Statement

The codebase evolved through three phases (REST polling -> WebSocket-only -> auto sport event detection) via incremental upgrades. Before the next data collection session, we need to verify the code is concise, coherent, and free of drift — without introducing any regressions on the hot WS collection path.

## Design Decisions

### D1: No hot-path refactoring before collection

**Decision:** Do not extract shared `_compute_book_metrics()` from `OrderBookSnapshot.from_ws()` / `from_api()` until after tonight's collection.

**Rationale:** `from_ws()` is called on every WS book event — the hottest path in the system. Even subtle changes to float rounding, truthy checks, or field ordering could alter persisted data (spread, depth, imbalance) and silently corrupt tonight's collection.

**Trade-off:** The ~70% code duplication between `from_ws()` and `from_api()` in `collector/models.py` remains. Post-collection, this can be addressed by first writing golden-output tests that freeze current `from_ws()` output for known fixtures, then extracting the shared helper and proving equivalence.

### D2: `from_api()` is legacy infrastructure, not dead code

**Decision:** Keep `OrderBookSnapshot.from_api()` and `Trade.from_api()`. Mark as "legacy/validation-only" via docstring.

**Rationale:** 16 tests in `test_polymarket_client.py` exercise these methods. README and CLAUDE.md document `scripts/validate_polymarket.py` as runnable. Removing `from_api()` would break tests and make docs inaccurate. The methods also remain useful for analyzing 114 existing SQLite databases collected during the REST era.

### D3: Keep validation scripts in place, mark deprecated

**Decision:** Leave `scripts/validate_dual_write.py` in `scripts/`. Add a deprecation note to its docstring.

**Rationale:** The `source` column is still in the trades schema (backward compat with 114 DBs). This script is the only tool for re-auditing WS vs REST overlap on historical data. Moving it to `old_plans/` would reduce audit capability without meaningful benefit.

### D4: Remove unused `db` parameter from PolymarketClient

**Decision:** Remove the `db: Database` parameter from `PolymarketClient.__init__()`.

**Rationale:** Constructor accepts and stores `self.db` but never uses it. `fetch_market_metadata()` is HTTP-only. Safe 3-line change with zero risk.

## Implementation Plan

### Step 1: Run full test suite

- Run `python -m pytest tests/ -v` to verify all tests pass with today's changes
- Reconcile actual test count with CLAUDE.md claim of 127 tests
- **Files:** None modified

### Step 2: Remove unused `db` parameter from PolymarketClient

- Remove `db: Database` parameter from `__init__()` signature
- Remove `self.db = db` assignment
- Remove `from .db import Database` import
- Update all call sites (check `collector/__main__.py`)
- **Files:** `collector/polymarket_client.py`, `collector/__main__.py`

### Step 3: Mark legacy code with docstrings

- Add "Legacy/validation-only" docstring to `OrderBookSnapshot.from_api()` in `collector/models.py`
- Add "Legacy/validation-only" docstring to `Trade.from_api()` in `collector/models.py`
- Add deprecation note to `scripts/validate_dual_write.py` module docstring
- **Files:** `collector/models.py`, `scripts/validate_dual_write.py`

### Step 4: Documentation alignment

- Update CLAUDE.md: collapse Phase 2 cleanup checklist into past-tense summary
- Check README.md for stale references to `--validate` flag or REST-only commands
- Update test count in CLAUDE.md if it differs from actual
- **Files:** `CLAUDE.md`, `README.md`

### Step 5 (deferred, post-collection): OrderBookSnapshot deduplication

- Write golden-output tests: freeze `from_ws()` output for `tests/fixtures/ws_book_sample.json`
- Extract shared `_compute_book_metrics()` static method
- Have both `from_ws()` and `from_api()` call the shared helper
- Prove equivalence via golden tests
- **Files:** `collector/models.py`, `tests/test_ws.py` or new test file

## Verification

1. `python -m pytest tests/ -v` — all tests pass after Steps 2-3
2. `grep -r "PolymarketClient(" collector/ scripts/` — no call sites pass `db` after Step 2
3. `grep -r "\-\-validate" README.md CLAUDE.md` — no stale references after Step 4
4. Manual review of modified files for consistency
