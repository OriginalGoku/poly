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
    smart_link: bool = False,
) -> dict[str, Any]:
    """Compute event-aligned price windows.

    For each match_event, extract price_signals in an asymmetric window
    around the event timestamp.  Returns bps-from-baseline curves.

    If ts_quality="local", windows are widened by ±5s to account for
    NHL timestamp uncertainty.

    If smart_link=True, uses analysis intelligence to select the most
    relevant moneyline tokens per event instead of top-5-by-volume.
    """
    from .analysis import build_market_lookup, dedup_events, link_event_to_tokens

    conn = _connect(db_name)
    token_labels = _build_token_labels(conn)

    # --- Fetch ALL events (for dedup + overlapping event computation) ---
    all_raw = conn.execute(
        "SELECT id, event_type, server_ts_ms, sport, timestamp_quality, "
        "team1_score, team2_score, event_team, quarter "
        "FROM match_events ORDER BY server_ts_ms",
    ).fetchall()

    if not all_raw:
        conn.close()
        return {
            "alignment_version": ALIGNMENT_VERSION,
            "event_count": 0,
            "windows": [],
            "token_labels": token_labels,
        }

    # Parse into dicts
    all_events_raw = [
        {
            "id": r[0], "event_type": r[1], "server_ts_ms": r[2],
            "sport": r[3], "timestamp_quality": r[4],
            "team1_score": r[5], "team2_score": r[6],
            "event_team": r[7], "quarter": r[8],
        }
        for r in all_raw
    ]

    # Apply NHL dedup to the full list first (D4)
    all_events = dedup_events(all_events_raw)

    # Split: primary events (filtered) get their own windows
    primary_events = all_events
    if event_type:
        primary_events = [e for e in primary_events if e["event_type"] == event_type]
    if ts_quality:
        primary_events = [e for e in primary_events if e["timestamp_quality"] == ts_quality]

    if not primary_events:
        conn.close()
        return {
            "alignment_version": ALIGNMENT_VERSION,
            "event_count": 0,
            "windows": [],
            "token_labels": token_labels,
        }

    # --- Smart linking setup ---
    market_lookup = None
    if smart_link:
        market_lookup = build_market_lookup(conn)

    # --- Top-5 fallback tokens ---
    top5_tokens: list[str] | None = None

    def _get_top5() -> list[str]:
        nonlocal top5_tokens
        if top5_tokens is None:
            top5_tokens = [
                r[0]
                for r in conn.execute(
                    "SELECT token_id, COUNT(*) as c FROM price_signals "
                    "GROUP BY token_id ORDER BY c DESC LIMIT 5"
                ).fetchall()
            ]
        return top5_tokens

    # --- Build windows ---
    windows = []
    for ev in primary_events:
        ev_ts = ev["server_ts_ms"]
        ev_tsq = ev["timestamp_quality"]

        # Widen window for local-quality timestamps
        before = WINDOW_BEFORE_MS
        after = WINDOW_AFTER_MS
        if ev_tsq == "local":
            before += LOCAL_QUALITY_PAD_MS
            after += LOCAL_QUALITY_PAD_MS

        window_start = ev_ts - before
        window_end = ev_ts + after

        # Determine tokens for this event
        if token_id:
            target_tokens = [token_id]
        elif smart_link and market_lookup:
            target_tokens = link_event_to_tokens(
                ev["event_type"], ev["event_team"], ev["sport"], market_lookup,
            )
            if not target_tokens:
                target_tokens = _get_top5()
        else:
            target_tokens = _get_top5()

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

            linked_info = market_lookup.get(tid) if market_lookup else None
            token_curves.append({
                "token_id": tid,
                "label": token_labels.get(tid, tid[:20]),
                "baseline_price": baseline,
                "point_count": len(points),
                "points": points,
                **({"linked_market_type": linked_info.market_type} if linked_info else {}),
            })

        # Smart-link fallback: if linked tokens produced 0 curves, use top-5
        if smart_link and market_lookup and not token_id and not token_curves:
            for tid in _get_top5():
                rows = conn.execute(
                    "SELECT server_ts_ms, mid_price FROM price_signals "
                    "WHERE token_id = ? AND server_ts_ms BETWEEN ? AND ? "
                    "ORDER BY server_ts_ms",
                    (tid, window_start, window_end),
                ).fetchall()
                if not rows:
                    continue
                baseline = rows[0][1]
                if baseline is None or baseline == 0:
                    continue
                points = []
                for ts, mid in rows:
                    if mid is not None:
                        offset_s = (ts - ev_ts) / 1000.0
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

        # Overlapping events (D7): other events within this window
        overlapping = []
        for other in all_events:
            if other["id"] == ev["id"]:
                continue
            other_ts = other["server_ts_ms"]
            if window_start <= other_ts <= window_end:
                overlapping.append({
                    "event_type": other["event_type"],
                    "offset_s": round((other_ts - ev_ts) / 1000.0, 2),
                    "team1_score": other["team1_score"],
                    "team2_score": other["team2_score"],
                    "event_team": other["event_team"],
                })

        windows.append({
            "event_id": ev["id"],
            "event_type": ev["event_type"],
            "server_ts_ms": ev_ts,
            "sport": ev["sport"],
            "timestamp_quality": ev_tsq,
            "team1_score": ev["team1_score"],
            "team2_score": ev["team2_score"],
            "event_team": ev["event_team"],
            "quarter": ev["quarter"],
            "window_before_ms": before,
            "window_after_ms": after,
            "token_curves": token_curves,
            "overlapping_events": overlapping,
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


# ---------------------------------------------------------------------------
# Heatmap endpoint
# ---------------------------------------------------------------------------

# Time offsets (seconds) for heatmap columns
HEATMAP_OFFSETS = [5, 15, 30, 60, 90, 120]


def get_heatmap(
    db_name: str,
    metric: str = "displacement",
    min_signals: int = 20,
) -> dict[str, Any]:
    """Precomputed heatmap: event types (rows) x time offsets (columns).

    Each cell contains the median absolute bps displacement or median
    reversion ratio for smart-linked moneyline tokens.
    """
    from statistics import median

    from .analysis import build_market_lookup, dedup_events, link_event_to_tokens

    conn = _connect(db_name)
    token_labels = _build_token_labels(conn)

    # Fetch all events + dedup
    all_raw = conn.execute(
        "SELECT id, event_type, server_ts_ms, sport, timestamp_quality, "
        "team1_score, team2_score, event_team, quarter "
        "FROM match_events ORDER BY server_ts_ms"
    ).fetchall()
    all_events = dedup_events([
        {
            "id": r[0], "event_type": r[1], "server_ts_ms": r[2],
            "sport": r[3], "timestamp_quality": r[4],
            "team1_score": r[5], "team2_score": r[6],
            "event_team": r[7], "quarter": r[8],
        }
        for r in all_raw
    ])

    market_lookup = build_market_lookup(conn)

    # Collect bps values per (event_type, ts_quality, offset)
    # Key: (event_type, ts_quality, offset_s) -> list[float]
    cells: dict[tuple[str, str, int], list[float]] = {}
    # Track peak displacement for reversion computation
    peaks: dict[tuple[str, str, int], list[tuple[float, float]]] = {}  # (peak_bps, bps_at_offset)

    for ev in all_events:
        ev_ts = ev["server_ts_ms"]
        ev_tsq = ev["timestamp_quality"] or "server"
        ev_type = ev["event_type"]

        tokens = link_event_to_tokens(
            ev_type, ev["event_team"], ev["sport"], market_lookup,
        )
        if not tokens:
            continue

        before = WINDOW_BEFORE_MS
        after = WINDOW_AFTER_MS
        if ev_tsq == "local":
            before += LOCAL_QUALITY_PAD_MS
            after += LOCAL_QUALITY_PAD_MS

        for tid in tokens:
            rows = conn.execute(
                "SELECT server_ts_ms, mid_price FROM price_signals "
                "WHERE token_id = ? AND server_ts_ms BETWEEN ? AND ? "
                "ORDER BY server_ts_ms",
                (tid, ev_ts - before, ev_ts + after),
            ).fetchall()

            if len(rows) < min_signals:
                continue

            baseline = rows[0][1]
            if baseline is None or baseline == 0:
                continue

            # Build offset -> bps mapping
            bps_by_offset: dict[int, float] = {}
            peak_abs_bps = 0.0
            for ts, mid in rows:
                if mid is None:
                    continue
                offset_s = (ts - ev_ts) / 1000.0
                bps = (mid - baseline) / baseline * 10_000
                peak_abs_bps = max(peak_abs_bps, abs(bps))

                # Snap to nearest heatmap offset
                for target in HEATMAP_OFFSETS:
                    if abs(offset_s - target) < 2.5:  # ±2.5s tolerance
                        bps_by_offset[target] = bps
                        break

            for offset, bps_val in bps_by_offset.items():
                key = (ev_type, ev_tsq, offset)
                cells.setdefault(key, []).append(abs(bps_val))
                peaks.setdefault(key, []).append((peak_abs_bps, abs(bps_val)))

    conn.close()

    # Build response grid
    event_types = sorted({k[0] for k in cells})
    ts_qualities = sorted({k[1] for k in cells})

    grid: dict[str, Any] = {}
    for tsq in ts_qualities:
        rows_out = []
        for ev_type in event_types:
            row_data: dict[str, Any] = {"event_type": ev_type, "offsets": {}}
            for offset in HEATMAP_OFFSETS:
                key = (ev_type, tsq, offset)
                values = cells.get(key, [])
                if not values:
                    row_data["offsets"][str(offset)] = None
                    continue

                if metric == "reversion":
                    # Reversion ratio: how much of peak displacement has reverted
                    peak_vals = peaks.get(key, [])
                    reversions = []
                    for peak_bps, bps_at_t in peak_vals:
                        if peak_bps > 0:
                            reversions.append(1.0 - (bps_at_t / peak_bps))
                    row_data["offsets"][str(offset)] = (
                        round(median(reversions) * 100, 1) if reversions else None
                    )
                else:
                    row_data["offsets"][str(offset)] = round(median(values), 1)

            row_data["sample_count"] = len(cells.get((ev_type, tsq, HEATMAP_OFFSETS[0]), []))
            rows_out.append(row_data)
        grid[tsq] = rows_out

    return {
        "metric": metric,
        "min_signals": min_signals,
        "offsets": HEATMAP_OFFSETS,
        "event_types": event_types,
        "timestamp_qualities": ts_qualities,
        "grid": grid,
        "token_labels": token_labels,
    }


# ---------------------------------------------------------------------------
# Spike candidates endpoint
# ---------------------------------------------------------------------------

def get_spike_candidates(
    db_name: str,
    sort: str = "reversion_pct",
    min_displacement: float = 0,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Find spike candidates: events where moneyline tokens show large displacement + reversion."""
    from .analysis import build_market_lookup, dedup_events, link_event_to_tokens

    conn = _connect(db_name)
    token_labels = _build_token_labels(conn)

    all_raw = conn.execute(
        "SELECT id, event_type, server_ts_ms, sport, timestamp_quality, "
        "team1_score, team2_score, event_team, quarter "
        "FROM match_events ORDER BY server_ts_ms"
    ).fetchall()
    all_events = dedup_events([
        {
            "id": r[0], "event_type": r[1], "server_ts_ms": r[2],
            "sport": r[3], "timestamp_quality": r[4],
            "team1_score": r[5], "team2_score": r[6],
            "event_team": r[7], "quarter": r[8],
        }
        for r in all_raw
    ])

    market_lookup = build_market_lookup(conn)

    candidates: list[dict[str, Any]] = []

    for ev in all_events:
        ev_ts = ev["server_ts_ms"]
        ev_tsq = ev["timestamp_quality"] or "server"

        tokens = link_event_to_tokens(
            ev["event_type"], ev["event_team"], ev["sport"], market_lookup,
        )
        if not tokens:
            continue

        before = WINDOW_BEFORE_MS
        after = WINDOW_AFTER_MS
        if ev_tsq == "local":
            before += LOCAL_QUALITY_PAD_MS
            after += LOCAL_QUALITY_PAD_MS

        for tid in tokens:
            rows = conn.execute(
                "SELECT server_ts_ms, mid_price FROM price_signals "
                "WHERE token_id = ? AND server_ts_ms BETWEEN ? AND ? "
                "ORDER BY server_ts_ms",
                (tid, ev_ts - before, ev_ts + after),
            ).fetchall()

            if not rows:
                continue

            baseline = rows[0][1]
            if baseline is None or baseline == 0:
                continue

            # Find peak displacement and final value
            peak_bps = 0.0
            peak_ts = ev_ts
            final_bps = 0.0
            signal_count = 0

            for ts, mid in rows:
                if mid is None:
                    continue
                signal_count += 1
                bps = (mid - baseline) / baseline * 10_000
                if abs(bps) > abs(peak_bps):
                    peak_bps = bps
                    peak_ts = ts
                final_bps = bps

            peak_displacement = abs(peak_bps)
            if peak_displacement < min_displacement:
                continue

            time_to_peak = (peak_ts - ev_ts) / 1000.0
            reversion_pct = (
                (1.0 - abs(final_bps) / peak_displacement) * 100
                if peak_displacement > 0 else 0.0
            )

            # Get spread at peak
            spread_row = conn.execute(
                "SELECT spread FROM price_signals "
                "WHERE token_id = ? AND server_ts_ms = ?",
                (tid, peak_ts),
            ).fetchone()
            spread_at_peak = spread_row[0] if spread_row else None

            info = market_lookup.get(tid)
            candidates.append({
                "event_id": ev["id"],
                "event_type": ev["event_type"],
                "event_time": ev_ts,
                "token_id": tid,
                "token_label": token_labels.get(tid, tid[:20]),
                "market_type": info.market_type if info else "unknown",
                "peak_displacement_bps": round(peak_bps, 1),
                "time_to_peak_s": round(time_to_peak, 1),
                "reversion_pct": round(reversion_pct, 1),
                "spread_at_peak_bps": round(spread_at_peak * 10_000, 1) if spread_at_peak else None,
                "signal_count": signal_count,
                "timestamp_quality": ev_tsq,
                "team1_score": ev["team1_score"],
                "team2_score": ev["team2_score"],
                "event_team": ev["event_team"],
            })

    conn.close()

    # Sort
    reverse = True
    if sort == "reversion_pct":
        candidates.sort(key=lambda c: c["reversion_pct"], reverse=reverse)
    elif sort == "peak_displacement_bps":
        candidates.sort(key=lambda c: abs(c["peak_displacement_bps"]), reverse=reverse)
    elif sort == "time_to_peak_s":
        candidates.sort(key=lambda c: c["time_to_peak_s"])
    else:
        candidates.sort(key=lambda c: c["reversion_pct"], reverse=reverse)

    total = len(candidates)
    page = candidates[offset : offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "sort": sort,
        "min_displacement": min_displacement,
        "candidates": page,
    }


# ---------------------------------------------------------------------------
# Game timeline endpoint
# ---------------------------------------------------------------------------

def get_game_timeline(
    db_name: str,
    token_id: str | None = None,
) -> dict[str, Any]:
    """Full-game price line + events + period boundaries for timeline chart."""
    from .analysis import build_market_lookup, dedup_events

    conn = _connect(db_name)
    token_labels = _build_token_labels(conn)

    # If no token specified, use the most active moneyline token
    market_lookup = build_market_lookup(conn)

    if token_id:
        target_token = token_id
    else:
        # Find most active match_winner token
        ml_tokens = [
            info.token_id for info in market_lookup.values()
            if info.market_type == "match_winner"
        ]
        if ml_tokens:
            # Pick the one with most signals
            best = None
            best_count = 0
            for tid in set(ml_tokens):
                count = _scalar(conn, f"SELECT COUNT(*) FROM price_signals WHERE token_id = '{tid}'") or 0
                if count > best_count:
                    best_count = count
                    best = tid
            target_token = best
        else:
            # Fallback to most active token overall
            row = conn.execute(
                "SELECT token_id FROM price_signals GROUP BY token_id ORDER BY COUNT(*) DESC LIMIT 1"
            ).fetchone()
            target_token = row[0] if row else None

    if not target_token:
        conn.close()
        return {"token_id": None, "signals": [], "events": [], "periods": [], "token_labels": token_labels}

    # Fetch all signals for this token, downsampled to 1s bins
    raw_signals = conn.execute(
        "SELECT server_ts_ms, mid_price, best_bid, best_ask, spread "
        "FROM price_signals WHERE token_id = ? ORDER BY server_ts_ms",
        (target_token,),
    ).fetchall()

    # Downsample: pick last value per 1-second bin
    binned: list[dict[str, Any]] = []
    if raw_signals:
        current_bin = raw_signals[0][0] // 1000
        last_in_bin = raw_signals[0]
        for row in raw_signals[1:]:
            row_bin = row[0] // 1000
            if row_bin != current_bin:
                ts, mid, bid, ask, spread = last_in_bin
                binned.append({
                    "server_ts_ms": ts,
                    "mid_price": mid,
                    "best_bid": bid,
                    "best_ask": ask,
                    "spread": spread,
                })
                current_bin = row_bin
            last_in_bin = row
        # Last bin
        ts, mid, bid, ask, spread = last_in_bin
        binned.append({
            "server_ts_ms": ts,
            "mid_price": mid,
            "best_bid": bid,
            "best_ask": ask,
            "spread": spread,
        })

    # Fetch events
    all_raw = conn.execute(
        "SELECT id, event_type, server_ts_ms, sport, timestamp_quality, "
        "team1_score, team2_score, event_team, quarter "
        "FROM match_events ORDER BY server_ts_ms"
    ).fetchall()
    all_events = dedup_events([
        {
            "id": r[0], "event_type": r[1], "server_ts_ms": r[2],
            "sport": r[3], "timestamp_quality": r[4],
            "team1_score": r[5], "team2_score": r[6],
            "event_team": r[7], "quarter": r[8],
        }
        for r in all_raw
    ])

    # Extract period boundaries
    periods: list[dict[str, Any]] = []
    period_events = [
        e for e in all_events
        if e["event_type"] in ("quarter_end", "period_end", "period_change", "half_end")
    ]
    for pe in period_events:
        periods.append({
            "event_type": pe["event_type"],
            "server_ts_ms": pe["server_ts_ms"],
            "quarter": pe["quarter"],
        })

    info = market_lookup.get(target_token)
    conn.close()

    return {
        "token_id": target_token,
        "token_label": token_labels.get(target_token, target_token[:20]),
        "market_type": info.market_type if info else "unknown",
        "signal_count": len(binned),
        "signals": binned,
        "events": [
            {
                "id": e["id"],
                "event_type": e["event_type"],
                "server_ts_ms": e["server_ts_ms"],
                "team1_score": e["team1_score"],
                "team2_score": e["team2_score"],
                "event_team": e["event_team"],
                "quarter": e["quarter"],
                "timestamp_quality": e["timestamp_quality"],
            }
            for e in all_events
        ],
        "periods": periods,
        "token_labels": token_labels,
    }
