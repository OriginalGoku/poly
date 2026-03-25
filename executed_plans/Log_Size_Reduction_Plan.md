# Log Size Reduction Plan

> Reduce collector log files from ~150 MB to ~2-3 MB per game by silencing noisy third-party loggers, with opt-in full DEBUG mode.

---

## Problem Statement

The data collector generates extremely large log files (up to 1.2 GB per game with old REST code, ~150 MB with new WS code) because the root logger is set to DEBUG and captures every internal operation from third-party libraries. Analysis of the largest log file (1.19 GB, NBA DEN-PHX) shows:

| Logger | % of file | Size | Useful? |
|---|---|---|---|
| `aiosqlite` | 88% | ~1,097 MB | No |
| `httpcore.http11` | 9% | ~111 MB | No |
| `httpx` | 1% | ~14 MB | No |
| `websockets.client` | (new code) ~23% | - | No |
| `collector.*` | <1% | ~2 MB | Yes |

Only ~0.2% of log content is useful application-level information. Tonight (2026-03-25) is the first real collection night with WS-only code, and logs need to be manageable.

## Design Decisions

### D1: Conditional third-party logger suppression

**Decision:** Silence `aiosqlite`, `httpcore`, `httpx`, `websockets`, and `urllib3` loggers to WARNING level in normal mode. Leave them at NOTSET (inheriting root DEBUG) when `--log-level DEBUG` is explicitly requested.

**Rationale:** Unconditional suppression would prevent debugging WS/HTTP issues when needed. Gating on the CLI flag preserves the full firehose as an opt-in.

**Trade-off:** Considered always suppressing third-party loggers even in DEBUG mode ("collector-only DEBUG"). Rejected because WS connection issues and HTTP errors from these libraries are sometimes needed for troubleshooting.

### D2: Call-site truncation over logging.Filter

**Decision:** Use a simple `truncate_id()` helper applied at ~3 specific call sites rather than a custom `logging.Filter` or `Formatter`.

**Rationale:** Only ~3 places log long token IDs (78-digit integers) or tx hashes (66-char hex). A Filter would be more robust but adds complexity for marginal benefit. Full IDs are always stored in the SQLite database for incident triage.

**Trade-off:** A `logging.Filter` with regex replacement would catch IDs from any future log call automatically. Deferred — worth revisiting if more logging call sites emerge.

### D3: Log rotation deferred

**Decision:** Do not add log rotation now.

**Rationale:** With INFO-level logs at ~2-3 MB per game, even a 20-game night produces ~50 MB total. Rotation would only matter in DEBUG mode, where a 50 MB cap with 3 backups could silently discard early-run context (often the most valuable for diagnosis).

## Implementation Plan

### Step 1: Add `--log-level` CLI flag and update `setup_logging()`

**File:** `collector/__main__.py`

1. Add `--log-level` argument to `cli()` argparse (lines 415-425):
   - Choices: `DEBUG`, `INFO`, `WARNING`
   - Default: `INFO`
2. Pass the level into `setup_logging(match_id, file_level)`
3. In `setup_logging()` (lines 35-61):
   - Set `file_handler.setLevel()` to the passed level
   - After root logger setup, conditionally silence third-party loggers:
     ```python
     if file_level > logging.DEBUG:
         for noisy in ("aiosqlite", "httpcore", "httpx", "websockets", "urllib3"):
             logging.getLogger(noisy).setLevel(logging.WARNING)
     ```
4. Update `main()` signature to accept and pass through the log level

### Step 2: Add `truncate_id()` helper and apply at call sites

**File:** `collector/__main__.py` (define the helper)

```python
def truncate_id(val: str, length: int = 12) -> str:
    if len(val) > length + 4:
        return f"{val[:length]}...{val[-4:]}"
    return val
```

Apply at these call sites:
- `collector/models.py:272-275` — unknown token error log
- `collector/ws_client.py` — any DEBUG-level token ID logging
- `collector/__main__.py:284` — metadata fetch failure (`m.market_id[:16]` already partially truncated)

### Step 3: Run tests and verify

No new tests needed — this is a logging configuration change. Verify manually.

## Verification

1. Run collector briefly with default settings:
   ```bash
   python -m collector --config configs/nba-atl-det-2026-03-25.json
   ```
   - Log file should grow at ~KB/minute, not MB/minute
   - Stderr should show INFO-level app messages (WS connected, snapshots, trades)
   - Log file should contain collector.* INFO messages but NOT aiosqlite/httpcore DEBUG spam

2. Run collector with DEBUG to verify opt-in works:
   ```bash
   python -m collector --config configs/nba-atl-det-2026-03-25.json --log-level DEBUG
   ```
   - Log file should contain full aiosqlite/httpcore/websockets DEBUG output

3. Verify errors still captured: library WARNING/ERROR messages should appear in both modes

4. Run test suite:
   ```bash
   python -m pytest tests/ -v
   ```

## Expected Impact

| Scenario | Before | After |
|---|---|---|
| 3-hour NBA game (default INFO) | ~150 MB | ~2-3 MB |
| 3-hour NBA game (opt-in DEBUG) | ~150 MB | ~150 MB |
| Full night, 20 games (default) | ~2+ GB | ~40-60 MB |
