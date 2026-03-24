#!/usr/bin/env python3
"""Data fitness analyzer: determines whether collected Polymarket data
is useful for the emotional overreaction hypothesis.

The overreaction hypothesis needs:
1. Sub-second price data around game events
2. Detectable price overshoots (spike + reversion)
3. Enough liquidity that price moves reflect real sentiment (not just wide spreads)
4. Game events correlated in time with price data

This script audits each DB and produces a structured report with a fitness verdict.

Usage:
    python scripts/analyze_data_fitness.py                      # all DBs
    python scripts/analyze_data_fitness.py data/nba-*.db        # specific DBs
    python scripts/analyze_data_fitness.py --json               # JSON output
"""

import glob
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0] > 0


def column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(c[1] == col for c in cols)
    except Exception:
        return False


def safe_div(a, b, default=0.0):
    return a / b if b else default


def fmt_pct(val: float) -> str:
    return f"{val:.1f}%"


def fmt_usd(val: float) -> str:
    if val >= 1_000_000:
        return f"${val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"${val / 1_000:.1f}K"
    return f"${val:.0f}"


# ---------------------------------------------------------------------------
# Analysis modules
# ---------------------------------------------------------------------------

@dataclass
class FitnessReport:
    db_name: str
    sport: str = ""
    duration_hours: float = 0.0
    time_range: str = ""

    # Completeness
    snapshot_count: int = 0
    trade_count: int = 0
    signal_count: int = 0
    event_count: int = 0
    gap_count: int = 0
    market_count: int = 0
    token_count: int = 0

    # Trade quality
    trades_in_config_markets: int = 0
    trades_outside_config: int = 0
    trade_market_overlap: int = 0
    trade_market_total: int = 0
    avg_trades_per_market: float = 0.0

    # Liquidity
    empty_book_pct: float = 0.0
    wide_spread_pct: float = 0.0  # spread > 0.10
    avg_spread: float = 0.0
    median_spread: float = 0.0
    avg_depth_usd: float = 0.0
    liquid_token_count: int = 0  # tokens with avg spread < 0.05
    illiquid_token_count: int = 0

    # Price dynamics
    tokens_with_movement: int = 0  # mid_price range > 0.05
    max_price_range: float = 0.0
    avg_price_range: float = 0.0
    spike_candidates: int = 0  # 5-min windows with >5c move then partial reversion

    # Temporal resolution
    avg_snapshot_interval_ms: float = 0.0
    avg_signal_interval_ms: float = 0.0
    snapshot_coverage_pct: float = 0.0  # % of 5s windows with at least 1 snapshot

    # Event-price readiness
    has_game_events: bool = False
    event_types: dict = field(default_factory=dict)

    # Verdicts
    issues: list = field(default_factory=list)
    strengths: list = field(default_factory=list)
    fitness_score: int = 0  # 0-100
    verdict: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def analyze_completeness(conn: sqlite3.Connection, report: FitnessReport):
    """Check what data exists and basic counts."""
    for table, attr in [
        ("order_book_snapshots", "snapshot_count"),
        ("trades", "trade_count"),
        ("match_events", "event_count"),
        ("data_gaps", "gap_count"),
    ]:
        if table_exists(conn, table):
            setattr(report, attr, conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    if table_exists(conn, "price_signals"):
        report.signal_count = conn.execute("SELECT COUNT(*) FROM price_signals").fetchone()[0]

    if table_exists(conn, "markets"):
        report.market_count = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]

    report.token_count = conn.execute(
        "SELECT COUNT(DISTINCT token_id) FROM order_book_snapshots"
    ).fetchone()[0] if report.snapshot_count > 0 else 0

    # Time range
    if report.snapshot_count > 0:
        r = conn.execute(
            "SELECT MIN(server_ts_ms), MAX(server_ts_ms) FROM order_book_snapshots"
        ).fetchone()
        if r[0] and r[1]:
            report.duration_hours = (r[1] - r[0]) / 1000 / 3600
            t0 = datetime.utcfromtimestamp(r[0] / 1000).strftime("%Y-%m-%d %H:%M")
            t1 = datetime.utcfromtimestamp(r[1] / 1000).strftime("%H:%M UTC")
            report.time_range = f"{t0} → {t1}"

    # Sport
    if table_exists(conn, "matches"):
        row = conn.execute("SELECT sport FROM matches LIMIT 1").fetchone()
        if row:
            report.sport = row[0]

    # Collection run info
    if table_exists(conn, "collection_runs"):
        row = conn.execute("SELECT sport FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone()
        if row and not report.sport:
            report.sport = row[0]


def analyze_trade_quality(conn: sqlite3.Connection, report: FitnessReport):
    """Check whether captured trades are from our configured markets."""
    if report.trade_count == 0:
        report.issues.append("NO TRADES: Zero trades captured")
        return

    # How many distinct market_ids in trades vs in markets table
    trade_markets = conn.execute("SELECT COUNT(DISTINCT market_id) FROM trades").fetchone()[0]
    config_markets = report.market_count

    overlap = conn.execute("""
        SELECT COUNT(DISTINCT t.market_id) FROM trades t
        WHERE t.market_id IN (SELECT market_id FROM markets)
    """).fetchone()[0] if config_markets > 0 else 0

    report.trade_market_total = trade_markets
    report.trade_market_overlap = overlap

    if config_markets > 0:
        report.trades_in_config_markets = conn.execute("""
            SELECT COUNT(*) FROM trades
            WHERE market_id IN (SELECT market_id FROM markets)
        """).fetchone()[0]
        report.trades_outside_config = report.trade_count - report.trades_in_config_markets

    report.avg_trades_per_market = safe_div(report.trade_count, trade_markets)

    # Flag issues
    if config_markets > 0 and overlap == 0:
        report.issues.append(
            f"TRADE-MARKET MISMATCH: 0/{config_markets} configured markets have trades. "
            f"Trades come from {trade_markets} other markets — likely REST Data API "
            f"returning event-wide trades, not filtered to config."
        )
    elif config_markets > 0 and report.trades_outside_config > report.trades_in_config_markets * 5:
        report.issues.append(
            f"TRADE LEAKAGE: {report.trades_outside_config} trades from non-config markets "
            f"vs {report.trades_in_config_markets} from config markets."
        )

    if report.avg_trades_per_market < 5:
        report.issues.append(
            f"LOW TRADE DENSITY: avg {report.avg_trades_per_market:.1f} trades/market. "
            f"Need higher frequency for overshoot detection."
        )


def analyze_liquidity(conn: sqlite3.Connection, report: FitnessReport):
    """Check whether markets are liquid enough for meaningful price signals."""
    if report.snapshot_count == 0:
        return

    r = conn.execute("""
        SELECT
            AVG(CASE WHEN is_empty THEN 1.0 ELSE 0.0 END) * 100,
            AVG(CASE WHEN NOT is_empty AND spread > 0.10 THEN 1.0 ELSE 0.0 END) * 100,
            AVG(CASE WHEN NOT is_empty THEN spread END),
            AVG(CASE WHEN NOT is_empty THEN book_depth_usd END)
        FROM order_book_snapshots
    """).fetchone()
    report.empty_book_pct = r[0] or 0
    report.wide_spread_pct = r[1] or 0
    report.avg_spread = r[2] or 0
    report.avg_depth_usd = r[3] or 0

    # Median spread
    median_row = conn.execute("""
        SELECT spread FROM order_book_snapshots
        WHERE NOT is_empty AND spread IS NOT NULL
        ORDER BY spread
        LIMIT 1 OFFSET (
            SELECT COUNT(*) / 2 FROM order_book_snapshots
            WHERE NOT is_empty AND spread IS NOT NULL
        )
    """).fetchone()
    report.median_spread = median_row[0] if median_row else 0

    # Per-token liquidity classification
    rows = conn.execute("""
        SELECT token_id, AVG(spread) as avg_sp
        FROM order_book_snapshots
        WHERE NOT is_empty
        GROUP BY token_id
    """).fetchall()
    report.liquid_token_count = sum(1 for _, sp in rows if sp and sp < 0.05)
    report.illiquid_token_count = sum(1 for _, sp in rows if sp and sp >= 0.10)

    # Verdicts
    if report.wide_spread_pct > 50:
        report.issues.append(
            f"ILLIQUID MAJORITY: {fmt_pct(report.wide_spread_pct)} of snapshots have spread >10c. "
            f"Price moves in these markets may reflect spread noise, not sentiment."
        )
    if report.liquid_token_count > 0:
        report.strengths.append(
            f"{report.liquid_token_count} tokens are liquid (avg spread <5c) — "
            f"focus overshoot analysis on these."
        )
    if report.avg_depth_usd > 5000:
        report.strengths.append(f"Good avg book depth: {fmt_usd(report.avg_depth_usd)}")


def analyze_price_dynamics(conn: sqlite3.Connection, report: FitnessReport):
    """Check whether prices move enough to detect overshoots."""
    if report.snapshot_count == 0:
        return

    # Per-token price ranges
    rows = conn.execute("""
        SELECT token_id,
               MIN(mid_price), MAX(mid_price),
               MAX(mid_price) - MIN(mid_price) as range_val,
               COUNT(*) as ct
        FROM order_book_snapshots
        WHERE mid_price IS NOT NULL AND mid_price > 0
        GROUP BY token_id
    """).fetchall()

    ranges = [r[3] for r in rows if r[3] is not None]
    report.tokens_with_movement = sum(1 for r in ranges if r > 0.05)
    report.max_price_range = max(ranges) if ranges else 0
    report.avg_price_range = sum(ranges) / len(ranges) if ranges else 0

    if report.tokens_with_movement == 0:
        report.issues.append("NO PRICE MOVEMENT: No tokens moved >5c. Nothing to analyze.")
    elif report.tokens_with_movement < report.token_count * 0.3:
        report.issues.append(
            f"LIMITED MOVEMENT: Only {report.tokens_with_movement}/{report.token_count} "
            f"tokens moved >5c over the collection window."
        )
    else:
        report.strengths.append(
            f"{report.tokens_with_movement}/{report.token_count} tokens show meaningful "
            f"price movement (>5c range). Max range: {report.max_price_range:.3f}"
        )

    # Spike detection: find 5-minute windows where mid_price moves >5c then partially reverts
    # Use the most liquid tokens for this
    liquid_tokens = conn.execute("""
        SELECT token_id FROM order_book_snapshots
        WHERE NOT is_empty
        GROUP BY token_id
        HAVING AVG(spread) < 0.05
        ORDER BY AVG(spread)
        LIMIT 10
    """).fetchall()

    spike_count = 0
    for (tid,) in liquid_tokens:
        rows = conn.execute("""
            SELECT server_ts_ms, mid_price
            FROM order_book_snapshots
            WHERE token_id = ? AND mid_price IS NOT NULL AND mid_price > 0
            ORDER BY server_ts_ms
        """, (tid,)).fetchall()

        if len(rows) < 20:
            continue

        # Sliding window spike detection
        for i in range(len(rows)):
            t0, p0 = rows[i]
            # Find max deviation in next 5 minutes
            max_dev = 0
            max_j = i
            for j in range(i + 1, len(rows)):
                if rows[j][0] - t0 > 300_000:  # 5 min
                    break
                dev = abs(rows[j][1] - p0)
                if dev > max_dev:
                    max_dev = dev
                    max_j = j

            if max_dev < 0.05:
                continue

            # Check for partial reversion in the next 5 min after the peak
            t_peak = rows[max_j][0]
            p_peak = rows[max_j][1]
            for k in range(max_j + 1, len(rows)):
                if rows[k][0] - t_peak > 300_000:
                    break
                reversion = abs(p_peak - rows[k][1])
                if reversion > max_dev * 0.3:  # 30%+ reversion
                    spike_count += 1
                    break

    report.spike_candidates = spike_count
    if spike_count > 0:
        report.strengths.append(
            f"Found {spike_count} potential overshoot patterns (>5c spike + >30% reversion "
            f"within 5 min) in liquid tokens."
        )


def analyze_temporal_resolution(conn: sqlite3.Connection, report: FitnessReport):
    """Check whether temporal resolution is sufficient for event correlation."""
    if report.snapshot_count == 0:
        return

    # Snapshot interval
    r = conn.execute("""
        SELECT AVG(delta_ms), COUNT(*)
        FROM (
            SELECT (server_ts_ms - LAG(server_ts_ms) OVER
                (PARTITION BY token_id ORDER BY server_ts_ms)) as delta_ms
            FROM order_book_snapshots
        ) WHERE delta_ms IS NOT NULL AND delta_ms > 0 AND delta_ms < 60000
    """).fetchone()
    report.avg_snapshot_interval_ms = r[0] or 0

    # Price signal interval (if available)
    if report.signal_count > 0:
        r = conn.execute("""
            SELECT AVG(delta_ms)
            FROM (
                SELECT (server_ts_ms - LAG(server_ts_ms) OVER
                    (PARTITION BY token_id ORDER BY server_ts_ms)) as delta_ms
                FROM price_signals
            ) WHERE delta_ms IS NOT NULL AND delta_ms > 0 AND delta_ms < 60000
        """).fetchone()
        report.avg_signal_interval_ms = r[0] or 0

    # Temporal coverage: what % of 10s windows have at least one data point?
    if report.duration_hours > 0:
        ts_range = conn.execute(
            "SELECT MIN(server_ts_ms), MAX(server_ts_ms) FROM order_book_snapshots"
        ).fetchone()
        if ts_range[0] and ts_range[1]:
            total_windows = (ts_range[1] - ts_range[0]) / 10_000  # 10s windows
            # Count distinct 10s buckets with data
            filled = conn.execute("""
                SELECT COUNT(DISTINCT server_ts_ms / 10000)
                FROM order_book_snapshots
            """).fetchone()[0]
            report.snapshot_coverage_pct = safe_div(filled, total_windows) * 100

    if report.avg_snapshot_interval_ms > 0:
        interval_s = report.avg_snapshot_interval_ms / 1000
        if interval_s <= 3:
            report.strengths.append(f"Good snapshot cadence: {interval_s:.1f}s avg interval")
        elif interval_s > 10:
            report.issues.append(
                f"SLOW SNAPSHOTS: {interval_s:.1f}s avg interval. May miss sub-10s overshoots."
            )

    if report.signal_count > 0 and report.avg_signal_interval_ms > 0:
        sig_s = report.avg_signal_interval_ms / 1000
        if sig_s <= 2:
            report.strengths.append(
                f"Sub-second BBO signals: {sig_s:.2f}s avg ({report.signal_count:,} total)"
            )


def analyze_event_readiness(conn: sqlite3.Connection, report: FitnessReport):
    """Check game event data availability."""
    if report.event_count > 0:
        report.has_game_events = True
        rows = conn.execute(
            "SELECT event_type, COUNT(*) FROM match_events GROUP BY event_type ORDER BY COUNT(*) DESC"
        ).fetchall()
        report.event_types = dict(rows)
        report.strengths.append(
            f"Game events present: {report.event_count} events "
            f"({len(report.event_types)} types: {', '.join(list(report.event_types.keys())[:5])})"
        )
    else:
        report.has_game_events = False
        report.issues.append(
            "NO GAME EVENTS: match_events table is empty. Cannot correlate price moves "
            "to in-game events. This is the critical missing piece for the overreaction "
            "hypothesis. Check game state client configuration."
        )


def compute_fitness_score(report: FitnessReport):
    """Compute an overall 0-100 fitness score and verdict."""
    score = 0

    # Data presence (30 pts)
    if report.snapshot_count > 1000:
        score += 10
    if report.trade_count > 50:
        score += 5
    elif report.trade_count > 10:
        score += 2
    if report.signal_count > 100:
        score += 10
    if report.event_count > 0:
        score += 5

    # Liquidity (25 pts)
    if report.liquid_token_count > 0:
        liquid_ratio = report.liquid_token_count / max(report.token_count, 1)
        score += int(min(liquid_ratio * 25, 25))

    # Price dynamics (20 pts)
    if report.tokens_with_movement > 0:
        move_ratio = report.tokens_with_movement / max(report.token_count, 1)
        score += int(min(move_ratio * 15, 15))
    if report.spike_candidates > 0:
        score += min(report.spike_candidates, 5)  # up to 5 pts

    # Trade quality (10 pts)
    if report.trade_market_overlap > 0:
        score += 5
    if report.avg_trades_per_market > 10:
        score += 5
    elif report.avg_trades_per_market > 3:
        score += 2

    # Event-price readiness (15 pts)
    if report.has_game_events:
        score += 15

    report.fitness_score = min(score, 100)

    if score >= 75:
        report.verdict = "READY — data supports overreaction analysis"
    elif score >= 50:
        report.verdict = "PARTIAL — some signals present but key gaps remain"
    elif score >= 25:
        report.verdict = "WEAK — significant data quality issues"
    else:
        report.verdict = "NOT READY — missing critical data for hypothesis testing"


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyze_db(db_path: str) -> FitnessReport:
    conn = sqlite3.connect(db_path)
    report = FitnessReport(db_name=Path(db_path).stem)

    analyze_completeness(conn, report)
    analyze_trade_quality(conn, report)
    analyze_liquidity(conn, report)
    analyze_price_dynamics(conn, report)
    analyze_temporal_resolution(conn, report)
    analyze_event_readiness(conn, report)
    compute_fitness_score(report)

    conn.close()
    return report


def print_report(r: FitnessReport):
    w = 65
    print(f"\n{'=' * w}")
    print(f"  {r.db_name}")
    print(f"  Sport: {r.sport or 'unknown'}  |  Duration: {r.duration_hours:.1f}h  |  {r.time_range}")
    print(f"{'=' * w}")

    print(f"\n  DATA INVENTORY")
    print(f"  {'Snapshots':<20} {r.snapshot_count:>10,}")
    print(f"  {'Trades':<20} {r.trade_count:>10,}")
    print(f"  {'Price signals':<20} {r.signal_count:>10,}")
    print(f"  {'Game events':<20} {r.event_count:>10,}")
    print(f"  {'Data gaps':<20} {r.gap_count:>10,}")
    print(f"  {'Markets (config)':<20} {r.market_count:>10}")
    print(f"  {'Tokens tracked':<20} {r.token_count:>10}")

    print(f"\n  TRADE QUALITY")
    if r.market_count > 0:
        print(f"  Market overlap:   {r.trade_market_overlap}/{r.market_count} config markets have trades")
        print(f"  In-config trades: {r.trades_in_config_markets} / {r.trade_count}")
    print(f"  Distinct markets: {r.trade_market_total} (in trade data)")
    print(f"  Avg trades/mkt:   {r.avg_trades_per_market:.1f}")

    print(f"\n  LIQUIDITY")
    print(f"  Empty books:      {fmt_pct(r.empty_book_pct)}")
    print(f"  Wide spread (>10c): {fmt_pct(r.wide_spread_pct)}")
    print(f"  Avg spread:       {r.avg_spread:.4f}")
    print(f"  Median spread:    {r.median_spread:.4f}")
    print(f"  Avg book depth:   {fmt_usd(r.avg_depth_usd)}")
    print(f"  Liquid tokens:    {r.liquid_token_count} (<5c avg spread)")
    print(f"  Illiquid tokens:  {r.illiquid_token_count} (>10c avg spread)")

    print(f"\n  PRICE DYNAMICS")
    print(f"  Tokens w/ movement (>5c): {r.tokens_with_movement}/{r.token_count}")
    print(f"  Max price range:  {r.max_price_range:.3f}")
    print(f"  Avg price range:  {r.avg_price_range:.3f}")
    print(f"  Spike candidates: {r.spike_candidates} (>5c spike + 30% reversion in 5min)")

    print(f"\n  TEMPORAL RESOLUTION")
    print(f"  Avg snapshot interval: {r.avg_snapshot_interval_ms:.0f}ms ({r.avg_snapshot_interval_ms / 1000:.1f}s)")
    if r.signal_count > 0:
        print(f"  Avg signal interval:   {r.avg_signal_interval_ms:.0f}ms ({r.avg_signal_interval_ms / 1000:.2f}s)")
    print(f"  10s window coverage:   {fmt_pct(r.snapshot_coverage_pct)}")

    print(f"\n  EVENT-PRICE READINESS")
    if r.has_game_events:
        print(f"  Game events: YES ({r.event_count})")
        for et, ct in list(r.event_types.items())[:5]:
            print(f"    {et}: {ct}")
    else:
        print(f"  Game events: NONE")

    # Verdicts
    if r.strengths:
        print(f"\n  STRENGTHS")
        for s in r.strengths:
            print(f"    + {s}")

    if r.issues:
        print(f"\n  ISSUES")
        for issue in r.issues:
            print(f"    ! {issue}")

    print(f"\n  {'─' * (w - 4)}")
    bar = "█" * (r.fitness_score // 5) + "░" * (20 - r.fitness_score // 5)
    print(f"  FITNESS SCORE: {r.fitness_score}/100  [{bar}]")
    print(f"  VERDICT: {r.verdict}")
    print(f"{'=' * w}")


def print_cross_db_summary(reports: list[FitnessReport]):
    """Print actionable summary across all databases."""
    w = 65
    print(f"\n{'=' * w}")
    print(f"  CROSS-DATABASE SUMMARY")
    print(f"  {len(reports)} databases analyzed")
    print(f"{'=' * w}")

    # Score table
    print(f"\n  {'Database':<40} {'Score':>6} {'Verdict'}")
    print(f"  {'─' * 40} {'─' * 6} {'─' * 15}")
    for r in sorted(reports, key=lambda x: x.fitness_score, reverse=True):
        short_verdict = r.verdict.split("—")[0].strip()
        print(f"  {r.db_name:<40} {r.fitness_score:>5}/100 {short_verdict}")

    # Aggregate issues
    issue_counts = defaultdict(int)
    for r in reports:
        for issue in r.issues:
            tag = issue.split(":")[0]
            issue_counts[tag] += 1

    if issue_counts:
        print(f"\n  RECURRING ISSUES")
        for tag, count in sorted(issue_counts.items(), key=lambda x: -x[1]):
            print(f"    {tag}: {count}/{len(reports)} databases")

    # Actionable recommendations
    print(f"\n  RECOMMENDATIONS")
    any_events = any(r.has_game_events for r in reports)
    any_trade_mismatch = any("TRADE-MARKET MISMATCH" in i for r in reports for i in r.issues)
    any_signals = any(r.signal_count > 0 for r in reports)
    any_liquid = any(r.liquid_token_count > 0 for r in reports)

    rec_num = 1
    if not any_events:
        print(f"  {rec_num}. [CRITICAL] Game state clients are not producing events.")
        print(f"     The overreaction hypothesis REQUIRES game events to correlate with")
        print(f"     price moves. Debug NBA CDN / OpenDota clients. Without events,")
        print(f"     you have price data but no way to identify WHAT caused moves.")
        rec_num += 1

    if any_trade_mismatch:
        print(f"  {rec_num}. [HIGH] REST trade data has market_id mismatch with config.")
        print(f"     The Data API returns trades from the entire event, not filtered to")
        print(f"     your markets. WS trades (source='ws') should not have this issue.")
        print(f"     After WS validation passes, this resolves itself.")
        rec_num += 1

    if not any_signals:
        print(f"  {rec_num}. [MEDIUM] No price_signals data in older DBs. The WS pipeline")
        print(f"     captures sub-second BBO — focus analysis on WS-collected DBs.")
        rec_num += 1
    elif any_signals:
        ws_dbs = [r for r in reports if r.signal_count > 0]
        if ws_dbs:
            print(f"  {rec_num}. [INFO] {len(ws_dbs)} DB(s) have price_signals from WS pipeline.")
            print(f"     These are the best candidates for sub-second overshoot analysis.")
            rec_num += 1

    if any_liquid:
        liquid_all = sum(r.liquid_token_count for r in reports)
        print(f"  {rec_num}. [INFO] {liquid_all} liquid tokens found across all DBs.")
        print(f"     Focus analysis on moneyline/spread markets, not player props.")
        print(f"     Player prop markets tend to have wide spreads and thin books.")
        rec_num += 1

    # Data sufficiency for hypothesis
    print(f"\n  HYPOTHESIS READINESS CHECK")
    checks = [
        ("Sub-second price data", any_signals),
        ("Game events for correlation", any_events),
        ("Liquid markets for clean signals", any_liquid),
        ("Detectable price overshoots", any(r.spike_candidates > 0 for r in reports)),
        ("Trade data from tracked markets", not any_trade_mismatch or any(
            r.trades_in_config_markets > 0 for r in reports
        )),
    ]
    for label, passed in checks:
        status = "PASS" if passed else "FAIL"
        icon = "+" if passed else "!"
        print(f"    [{icon}] {label}: {status}")

    passed_count = sum(1 for _, p in checks if p)
    print(f"\n  {passed_count}/{len(checks)} checks passed.")
    if passed_count == len(checks):
        print(f"  Data collection is ready for Phase 3 analysis.")
    else:
        failed = [label for label, p in checks if not p]
        print(f"  Fix these before Phase 3: {', '.join(failed)}")
    print(f"{'=' * w}")


def main():
    json_mode = "--json" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--json"]

    if args:
        db_files = args
    else:
        db_files = sorted(glob.glob("data/*.db"))

    if not db_files:
        print("No databases found. Specify paths or run from project root with data/ dir.")
        sys.exit(1)

    reports = []
    for dbf in db_files:
        try:
            r = analyze_db(dbf)
            reports.append(r)
            if not json_mode:
                print_report(r)
        except Exception as e:
            print(f"\nERROR processing {dbf}: {e}")

    if json_mode:
        print(json.dumps([r.to_dict() for r in reports], indent=2, default=str))
    elif len(reports) > 1:
        print_cross_db_summary(reports)


if __name__ == "__main__":
    main()
