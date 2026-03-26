"""SQL queries and data-processing helpers for the dashboard API."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DATA_DIR = Path("data")

# Event-window alignment version — increment when window logic changes.
ALIGNMENT_VERSION = 1

# Default asymmetric window (server-quality timestamps).
WINDOW_BEFORE_MS = 5_000   # T - 5s
WINDOW_AFTER_MS = 120_000  # T + 120s

# Extra padding for local-quality timestamps (NHL ±5s uncertainty).
LOCAL_QUALITY_PAD_MS = 5_000


def list_databases() -> list[dict[str, Any]]:
    """Return lightweight metadata for every .db file in DATA_DIR."""
    results = []
    for p in sorted(DATA_DIR.glob("*.db")):
        info: dict[str, Any] = {
            "name": p.stem,
            "file": p.name,
            "size_bytes": p.stat().st_size,
        }
        try:
            conn = _connect(p.name)
            info["sport"] = _scalar(conn, "SELECT sport FROM matches LIMIT 1") or _guess_sport(p.stem)
            info["match_events"] = _scalar(conn, "SELECT COUNT(*) FROM match_events") or 0
            info["price_signals"] = _scalar(conn, "SELECT COUNT(*) FROM price_signals") or 0
            info["trades"] = _scalar(conn, "SELECT COUNT(*) FROM trades") or 0
            conn.close()
        except Exception:
            info["sport"] = _guess_sport(p.stem)
            info["match_events"] = 0
            info["price_signals"] = 0
            info["trades"] = 0
        results.append(info)
    return results


def get_signals(
    db_name: str,
    token_id: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 10_000,
) -> dict[str, Any]:
    """Return price signals, optionally filtered by token and time range."""
    conn = _connect(db_name)

    # Token list for selector
    tokens = [
        r[0] for r in conn.execute("SELECT DISTINCT token_id FROM price_signals").fetchall()
    ]

    # Token label map from markets table
    token_labels = _build_token_labels(conn)

    # Build query
    conditions = []
    params: list[Any] = []
    if token_id:
        conditions.append("token_id = ?")
        params.append(token_id)
    if start_ms is not None:
        conditions.append("server_ts_ms >= ?")
        params.append(start_ms)
    if end_ms is not None:
        conditions.append("server_ts_ms <= ?")
        params.append(end_ms)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT token_id, server_ts_ms, best_bid, best_ask, mid_price, spread FROM price_signals{where} ORDER BY server_ts_ms LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    return {
        "tokens": tokens,
        "token_labels": token_labels,
        "count": len(rows),
        "signals": [
            {
                "token_id": r[0],
                "server_ts_ms": r[1],
                "best_bid": r[2],
                "best_ask": r[3],
                "mid_price": r[4],
                "spread": r[5],
            }
            for r in rows
        ],
    }


def get_event_windows(
    db_name: str,
    event_type: str | None = None,
    token_id: str | None = None,
    ts_quality: str | None = None,
) -> dict[str, Any]:
    """Compute event-aligned price windows.

    For each match_event, extract price_signals in an asymmetric window
    around the event timestamp.  Returns bps-from-baseline curves.

    If ts_quality="local", windows are widened by ±5s to account for
    NHL timestamp uncertainty.
    """
    conn = _connect(db_name)
    token_labels = _build_token_labels(conn)

    # --- Fetch events ---
    ev_conditions = []
    ev_params: list[Any] = []
    if event_type:
        ev_conditions.append("event_type = ?")
        ev_params.append(event_type)
    if ts_quality:
        ev_conditions.append("timestamp_quality = ?")
        ev_params.append(ts_quality)

    ev_where = (" WHERE " + " AND ".join(ev_conditions)) if ev_conditions else ""
    events = conn.execute(
        f"SELECT id, event_type, server_ts_ms, sport, timestamp_quality, "
        f"team1_score, team2_score, event_team "
        f"FROM match_events{ev_where} ORDER BY server_ts_ms",
        ev_params,
    ).fetchall()

    if not events:
        conn.close()
        return {
            "alignment_version": ALIGNMENT_VERSION,
            "event_count": 0,
            "windows": [],
            "token_labels": token_labels,
        }

    # --- Determine tokens to query ---
    if token_id:
        target_tokens = [token_id]
    else:
        # Use the 5 most active tokens by signal count
        target_tokens = [
            r[0]
            for r in conn.execute(
                "SELECT token_id, COUNT(*) as c FROM price_signals GROUP BY token_id ORDER BY c DESC LIMIT 5"
            ).fetchall()
        ]

    # --- Build windows ---
    windows = []
    for ev in events:
        ev_id, ev_type, ev_ts, ev_sport, ev_tsq, t1_score, t2_score, ev_team = ev

        # Widen window for local-quality timestamps
        before = WINDOW_BEFORE_MS
        after = WINDOW_AFTER_MS
        if ev_tsq == "local":
            before += LOCAL_QUALITY_PAD_MS
            after += LOCAL_QUALITY_PAD_MS

        window_start = ev_ts - before
        window_end = ev_ts + after

        # Fetch signals for each target token in this window
        token_curves = []
        for tid in target_tokens:
            rows = conn.execute(
                "SELECT server_ts_ms, mid_price FROM price_signals "
                "WHERE token_id = ? AND server_ts_ms BETWEEN ? AND ? "
                "ORDER BY server_ts_ms",
                (tid, window_start, window_end),
            ).fetchall()

            if not rows:
                continue

            # Compute bps from baseline (first price in window)
            baseline = rows[0][1]
            if baseline is None or baseline == 0:
                continue

            points = []
            for ts, mid in rows:
                if mid is not None:
                    offset_s = (ts - ev_ts) / 1000.0  # seconds relative to event
                    bps = (mid - baseline) / baseline * 10_000
                    points.append({
                        "offset_s": round(offset_s, 2),
                        "bps": round(bps, 2),
                        "mid_price": mid,
                        "server_ts_ms": ts,
                    })

            token_curves.append({
                "token_id": tid,
                "label": token_labels.get(tid, tid[:20]),
                "baseline_price": baseline,
                "point_count": len(points),
                "points": points,
            })

        windows.append({
            "event_id": ev_id,
            "event_type": ev_type,
            "server_ts_ms": ev_ts,
            "sport": ev_sport,
            "timestamp_quality": ev_tsq,
            "team1_score": t1_score,
            "team2_score": t2_score,
            "event_team": ev_team,
            "window_before_ms": before,
            "window_after_ms": after,
            "token_curves": token_curves,
        })

    conn.close()
    return {
        "alignment_version": ALIGNMENT_VERSION,
        "event_count": len(windows),
        "token_labels": token_labels,
        "windows": windows,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _connect(db_name: str) -> sqlite3.Connection:
    """Open a read-only connection to a database in DATA_DIR."""
    # Accept both "foo" and "foo.db"
    if not db_name.endswith(".db"):
        db_name = db_name + ".db"
    path = DATA_DIR / db_name
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {db_name}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _scalar(conn: sqlite3.Connection, sql: str) -> Any:
    try:
        row = conn.execute(sql).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _guess_sport(stem: str) -> str:
    for s in ("nba", "nhl", "mlb", "atp", "wta", "cs2", "val", "lol", "dota2", "cbb", "crint", "criclcl"):
        if stem.startswith(s):
            return s
    return "unknown"


def _build_token_labels(conn: sqlite3.Connection) -> dict[str, str]:
    """Map token_id -> 'Outcome (Question...)' from markets table."""
    labels: dict[str, str] = {}
    try:
        rows = conn.execute("SELECT question, outcomes_json, token_ids_json FROM markets").fetchall()
        for question, outcomes_json, token_ids_json in rows:
            outcomes = json.loads(outcomes_json)
            token_ids = json.loads(token_ids_json)
            for tid, outcome in zip(token_ids, outcomes):
                labels[tid] = f"{outcome} ({question[:40]})"
    except Exception:
        pass
    return labels
