#!/usr/bin/env python3
"""Check dual-write validation results: compare WS vs REST trade capture.

DEPRECATED: REST polling was removed after WS validation (2026-03-25).
Retained for re-auditing WS vs REST overlap on the 114 historical databases
collected during the REST era. The 'source' column remains in the trades schema.

Usage: python scripts/validate_dual_write.py [db_path]
       Default: data/val-ega-adg-VALIDATE.db
"""

import sqlite3
import sys


def validate(db_path: str) -> None:
    db = sqlite3.connect(db_path)

    # Overall counts
    print("=" * 60)
    print(f"DUAL-WRITE VALIDATION: {db_path}")
    print("=" * 60)

    for table in ["order_book_snapshots", "trades", "price_signals", "data_gaps"]:
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count:,}")

    print()

    # Trades by source
    print("--- Trades by source ---")
    rows = db.execute("SELECT source, COUNT(*) FROM trades GROUP BY source").fetchall()
    source_counts = dict(rows)
    ws_count = source_counts.get("ws", 0)
    rest_count = source_counts.get("rest", 0)
    for source, count in sorted(rows):
        print(f"  {source}: {count:,}")

    print()

    # Overlap analysis
    print("--- Overlap analysis ---")
    overlap = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT transaction_hash, token_id FROM trades WHERE source='ws'
            INTERSECT
            SELECT transaction_hash, token_id FROM trades WHERE source='rest'
        )
    """).fetchone()[0]
    print(f"  Captured by BOTH:   {overlap:,}")

    ws_only = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT transaction_hash, token_id FROM trades WHERE source='ws'
            EXCEPT
            SELECT transaction_hash, token_id FROM trades WHERE source='rest'
        )
    """).fetchone()[0]
    print(f"  WS only (REST missed): {ws_only:,}")

    rest_only = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT transaction_hash, token_id FROM trades WHERE source='rest'
            EXCEPT
            SELECT transaction_hash, token_id FROM trades WHERE source='ws'
        )
    """).fetchone()[0]
    print(f"  REST only (WS missed): {rest_only:,}")

    # Distinct trades (union of both)
    total_unique = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT transaction_hash, token_id FROM trades
        )
    """).fetchone()[0]
    print(f"  Total unique trades: {total_unique:,}")

    print()

    # Pass/fail
    print("--- Validation result ---")
    # Only count trades that occurred AFTER both sources were running
    # (REST historical trades don't count against WS)
    if overlap + ws_only == 0:
        print("  Not enough overlapping data yet. Keep running.")
        print(f"  REST has {rest_count} trades (likely historical batch).")
        print(f"  WS has {ws_count} trades (real-time only).")
        print("  Wait for new trades to occur while both are running.")
    else:
        # WS capture rate = (overlap + ws_only) / (overlap + ws_only + rest_only)
        # But rest_only includes historical trades. Filter to only trades after WS started.
        ws_min_ts = db.execute("SELECT MIN(server_ts_ms) FROM trades WHERE source='ws'").fetchone()[0]
        if ws_min_ts:
            rest_after_ws = db.execute(
                "SELECT COUNT(*) FROM trades WHERE source='rest' AND server_ts_ms >= ?",
                (ws_min_ts,)
            ).fetchone()[0]
            ws_after_ws = ws_count  # all WS trades are after WS started

            overlap_after = db.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT transaction_hash, token_id FROM trades WHERE source='ws' AND server_ts_ms >= {ws_min_ts}
                    INTERSECT
                    SELECT transaction_hash, token_id FROM trades WHERE source='rest' AND server_ts_ms >= {ws_min_ts}
                )
            """).fetchone()[0]

            rest_only_after = db.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT transaction_hash, token_id FROM trades WHERE source='rest' AND server_ts_ms >= {ws_min_ts}
                    EXCEPT
                    SELECT transaction_hash, token_id FROM trades WHERE source='ws' AND server_ts_ms >= {ws_min_ts}
                )
            """).fetchone()[0]

            total_after = overlap_after + (ws_after_ws - overlap_after) + rest_only_after
            ws_capture = ws_after_ws / total_after * 100 if total_after > 0 else 0
            rest_capture = rest_after_ws / total_after * 100 if total_after > 0 else 0

            print(f"  (Counting only trades after WS connected, ts >= {ws_min_ts})")
            print(f"  REST captured: {rest_after_ws}/{total_after} ({rest_capture:.1f}%)")
            print(f"  WS captured:   {ws_after_ws}/{total_after} ({ws_capture:.1f}%)")
            print()
            if ws_capture >= 98:
                print("  PASS: WS captures >= 98% of trades")
            elif total_after < 10:
                print(f"  INSUFFICIENT DATA: only {total_after} trades since WS connected. Need more time.")
            else:
                print(f"  FAIL: WS captures {ws_capture:.1f}% (need >= 98%)")
                # Show what WS missed
                missed = db.execute(f"""
                    SELECT t.transaction_hash, t.token_id, t.price, t.size, t.server_ts_ms
                    FROM trades t
                    WHERE t.source='rest' AND t.server_ts_ms >= {ws_min_ts}
                    AND NOT EXISTS (
                        SELECT 1 FROM trades t2
                        WHERE t2.source='ws'
                        AND t2.transaction_hash = t.transaction_hash
                        AND t2.token_id = t.token_id
                    )
                    LIMIT 10
                """).fetchall()
                if missed:
                    print("  WS missed these trades:")
                    for row in missed:
                        print(f"    hash={row[0][:20]}... token={row[1][:16]}... price={row[2]} size={row[3]} ts={row[4]}")

    db.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/val-ega-adg-VALIDATE.db"
    validate(db_path)
