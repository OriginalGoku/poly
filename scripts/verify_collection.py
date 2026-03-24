#!/usr/bin/env python3
"""Post-match verification script for Phase 1c.

Usage: python scripts/verify_collection.py [data/*.db ...]
       python scripts/verify_collection.py  # checks all DBs in data/
"""

import glob
import sqlite3
import sys
from pathlib import Path


def verify_db(db_path: str) -> dict:
    """Run all Phase 1c verification queries on a single database."""
    db = sqlite3.connect(db_path)
    c = db.cursor()
    name = Path(db_path).stem
    results = {"name": name, "issues": []}

    # --- Snapshot count and interval ---
    c.execute("SELECT COUNT(*), MIN(local_ts), MAX(local_ts) FROM order_book_snapshots")
    snap_count, snap_min, snap_max = c.fetchone()
    results["snapshots"] = snap_count
    results["snap_range"] = f"{snap_min} → {snap_max}"

    # --- NULL server_ts_ms ---
    for tbl in ["order_book_snapshots", "trades", "match_events"]:
        c.execute(f"SELECT COUNT(*) FROM {tbl} WHERE server_ts_ms IS NULL")
        null_ct = c.fetchone()[0]
        if null_ct > 0:
            results["issues"].append(f"{tbl}: {null_ct} rows with NULL server_ts_ms")

    # --- Trade count and dedup ---
    c.execute("SELECT COUNT(*) FROM trades")
    results["trades"] = c.fetchone()[0]
    c.execute(
        "SELECT COUNT(*) FROM ("
        "SELECT transaction_hash, token_id FROM trades "
        "GROUP BY transaction_hash, token_id HAVING COUNT(*) > 1)"
    )
    dupes = c.fetchone()[0]
    if dupes > 0:
        results["issues"].append(f"trades: {dupes} duplicate (tx_hash, token_id) pairs")
    results["trade_dupes"] = dupes

    # --- Match events ---
    c.execute("SELECT event_type, COUNT(*) FROM match_events GROUP BY event_type ORDER BY COUNT(*) DESC")
    results["events"] = dict(c.fetchall())
    results["total_events"] = sum(results["events"].values())

    # --- Data gaps ---
    c.execute("SELECT COUNT(*) FROM data_gaps")
    results["gaps"] = c.fetchone()[0]

    # --- Spread distribution ---
    c.execute(
        "SELECT "
        "  COUNT(CASE WHEN spread <= 0.02 THEN 1 END) as tight, "
        "  COUNT(CASE WHEN spread > 0.02 AND spread <= 0.10 THEN 1 END) as medium, "
        "  COUNT(CASE WHEN spread > 0.10 AND spread < 1.0 THEN 1 END) as wide, "
        "  COUNT(CASE WHEN spread IS NULL OR spread >= 1.0 THEN 1 END) as empty_or_extreme "
        "FROM order_book_snapshots"
    )
    tight, medium, wide, empty = c.fetchone()
    results["spread_dist"] = {"tight(<=2c)": tight, "medium(2-10c)": medium, "wide(>10c)": wide, "empty/extreme": empty}

    # --- Empty book percentage ---
    c.execute(
        "SELECT SUM(CASE WHEN is_empty THEN 1 ELSE 0 END) * 100.0 / COUNT(*) "
        "FROM order_book_snapshots"
    )
    results["empty_pct"] = round(c.fetchone()[0] or 0, 1)

    # --- Polling interval ---
    c.execute(
        "SELECT AVG(delta_ms), MIN(delta_ms), MAX(delta_ms), "
        "       COUNT(CASE WHEN delta_ms > 5000 THEN 1 END), COUNT(*) "
        "FROM ("
        "  SELECT (local_mono_ns - LAG(local_mono_ns) OVER "
        "    (PARTITION BY token_id ORDER BY local_mono_ns)) / 1000000.0 as delta_ms "
        "  FROM order_book_snapshots"
        ") WHERE delta_ms IS NOT NULL"
    )
    avg_ms, min_ms, max_ms, over_5s, total_intervals = c.fetchone()
    results["poll_interval"] = {
        "avg_ms": round(avg_ms or 0),
        "min_ms": round(min_ms or 0),
        "max_ms": round(max_ms or 0),
        "over_5s_count": over_5s or 0,
        "total_intervals": total_intervals or 0,
    }
    if avg_ms and avg_ms > 5000:
        results["issues"].append(f"avg polling interval {avg_ms:.0f}ms > 5000ms target")

    # --- Collection run ---
    c.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1")
    run = c.fetchone()
    if run:
        results["collection_run"] = {
            "sport": run[2],
            "started": run[3],
            "ended": run[4],
            "snapshots": run[7],
            "trades": run[8],
            "events": run[9],
            "gaps": run[10],
        }

    # --- Market metadata ---
    c.execute(
        "SELECT COUNT(*) FROM markets WHERE tick_size IS NOT NULL AND min_order_size IS NOT NULL"
    )
    meta_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM markets")
    total_markets = c.fetchone()[0]
    results["market_metadata"] = f"{meta_count}/{total_markets} markets have tick_size + min_order_size"

    # --- Book depth distribution ---
    c.execute(
        "SELECT "
        "  COUNT(CASE WHEN book_depth_usd < 100 THEN 1 END) as thin, "
        "  COUNT(CASE WHEN book_depth_usd >= 100 AND book_depth_usd < 1000 THEN 1 END) as moderate, "
        "  COUNT(CASE WHEN book_depth_usd >= 1000 THEN 1 END) as deep "
        "FROM order_book_snapshots WHERE book_depth_usd > 0"
    )
    thin, mod, deep = c.fetchone()
    results["depth_dist"] = {"thin(<$100)": thin, "moderate($100-1K)": mod, "deep(>$1K)": deep}

    db.close()
    return results


def print_report(r: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  {r['name']}")
    print(f"{'='*60}")
    print(f"  Snapshots:    {r['snapshots']:,}")
    print(f"  Trades:       {r['trades']:,} (dupes: {r['trade_dupes']})")
    print(f"  Events:       {r['total_events']} {r['events'] if r['events'] else ''}")
    print(f"  Gaps:         {r['gaps']}")
    print(f"  Empty books:  {r['empty_pct']}%")
    print(f"  Time range:   {r['snap_range']}")
    print(f"  Metadata:     {r['market_metadata']}")
    print(f"  Poll interval: avg={r['poll_interval']['avg_ms']}ms "
          f"min={r['poll_interval']['min_ms']}ms "
          f"max={r['poll_interval']['max_ms']}ms "
          f"(>{'>'}5s: {r['poll_interval']['over_5s_count']}/{r['poll_interval']['total_intervals']})")
    print(f"  Spread dist:  {r['spread_dist']}")
    print(f"  Depth dist:   {r['depth_dist']}")
    if r.get("collection_run"):
        cr = r["collection_run"]
        print(f"  Run:          {cr['sport']} | {cr['started']} → {cr['ended'] or 'running'}")
    if r["issues"]:
        print(f"  ISSUES:")
        for issue in r["issues"]:
            print(f"    ⚠ {issue}")
    else:
        print(f"  Status:       ALL CHECKS PASS")


def main():
    if len(sys.argv) > 1:
        db_files = sys.argv[1:]
    else:
        db_files = sorted(glob.glob("data/*.db"))

    if not db_files:
        print("No databases found in data/")
        return

    all_results = []
    for dbf in db_files:
        try:
            r = verify_db(dbf)
            all_results.append(r)
            print_report(r)
        except Exception as e:
            print(f"\nERROR processing {dbf}: {e}")

    # Summary
    total_snaps = sum(r["snapshots"] for r in all_results)
    total_trades = sum(r["trades"] for r in all_results)
    total_events = sum(r["total_events"] for r in all_results)
    sports = set()
    for r in all_results:
        cr = r.get("collection_run", {})
        if cr.get("sport"):
            sports.add(cr["sport"])

    print(f"\n{'='*60}")
    print(f"  SUMMARY: {len(all_results)} matches, {len(sports)} sports ({', '.join(sorted(sports))})")
    print(f"  Total: {total_snaps:,} snapshots, {total_trades:,} trades, {total_events} events")
    total_issues = sum(len(r["issues"]) for r in all_results)
    if total_issues == 0:
        print(f"  ALL CHECKS PASS")
    else:
        print(f"  {total_issues} issue(s) found")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
