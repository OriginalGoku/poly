#!/usr/bin/env python3
"""Collection night summary report.

Produces a per-sport aggregated table showing game count, avg snapshots/trades/
signals/events per game, data gaps, and health status — designed for quick
post-collection triage.

Usage:
    python scripts/collection_summary.py                      # all DBs in data/
    python scripts/collection_summary.py data/nba-*.db        # specific DBs
    python scripts/collection_summary.py --json               # JSON output
"""

import glob
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class GameSummary:
    """Per-database summary."""
    name: str
    sport: str = ""
    db_path: str = ""
    snapshots: int = 0
    trades: int = 0
    signals: int = 0
    events: int = 0
    gaps: int = 0
    markets: int = 0
    tokens: int = 0
    duration_hours: float = 0.0
    time_range: str = ""
    event_types: dict = field(default_factory=dict)
    trade_dupes: int = 0
    empty_book_pct: float = 0.0
    liquid_tokens: int = 0
    spike_candidates: int = 0
    status: str = ""
    issues: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class SportSummary:
    """Aggregated per-sport summary."""
    sport: str
    games: int = 0
    total_snapshots: int = 0
    total_trades: int = 0
    total_signals: int = 0
    total_events: int = 0
    total_gaps: int = 0
    total_spikes: int = 0
    avg_snapshots: float = 0.0
    avg_trades: float = 0.0
    avg_signals: float = 0.0
    avg_events: float = 0.0
    avg_gaps: float = 0.0
    avg_liquid_tokens: float = 0.0
    status: str = ""
    issues: list = field(default_factory=list)
    game_details: list = field(default_factory=list)


def analyze_game(db_path: str) -> GameSummary:
    """Extract summary metrics from a single database."""
    conn = sqlite3.connect(db_path)
    g = GameSummary(name=Path(db_path).stem, db_path=db_path)

    # Sport from collection_runs or matches
    for table, col in [("collection_runs", "sport"), ("matches", "sport")]:
        try:
            row = conn.execute(f"SELECT {col} FROM {table} LIMIT 1").fetchone()
            if row and row[0]:
                g.sport = row[0]
                break
        except sqlite3.OperationalError:
            continue

    # Core counts
    for table, attr in [
        ("order_book_snapshots", "snapshots"),
        ("trades", "trades"),
        ("match_events", "events"),
        ("data_gaps", "gaps"),
        ("markets", "markets"),
    ]:
        try:
            setattr(g, attr, conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.OperationalError:
            pass

    try:
        g.signals = conn.execute("SELECT COUNT(*) FROM price_signals").fetchone()[0]
    except sqlite3.OperationalError:
        pass

    # Distinct tokens
    if g.snapshots > 0:
        g.tokens = conn.execute(
            "SELECT COUNT(DISTINCT token_id) FROM order_book_snapshots"
        ).fetchone()[0]

    # Time range
    if g.snapshots > 0:
        r = conn.execute(
            "SELECT MIN(server_ts_ms), MAX(server_ts_ms) FROM order_book_snapshots"
        ).fetchone()
        if r[0] and r[1]:
            g.duration_hours = (r[1] - r[0]) / 1000 / 3600
            t0 = datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc).strftime("%H:%M")
            t1 = datetime.fromtimestamp(r[1] / 1000, tz=timezone.utc).strftime("%H:%M UTC")
            g.time_range = f"{t0}-{t1}"

    # Event types
    try:
        rows = conn.execute(
            "SELECT event_type, COUNT(*) FROM match_events GROUP BY event_type "
            "ORDER BY COUNT(*) DESC"
        ).fetchall()
        g.event_types = dict(rows)
    except sqlite3.OperationalError:
        pass

    # Trade dupes
    try:
        g.trade_dupes = conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT transaction_hash, token_id FROM trades "
            "GROUP BY transaction_hash, token_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        pass

    # Empty book %
    if g.snapshots > 0:
        try:
            g.empty_book_pct = round(conn.execute(
                "SELECT AVG(CASE WHEN is_empty THEN 1.0 ELSE 0.0 END) * 100 "
                "FROM order_book_snapshots"
            ).fetchone()[0] or 0, 1)
        except sqlite3.OperationalError:
            pass

    # Liquid tokens (avg spread < 5c)
    if g.snapshots > 0:
        try:
            rows = conn.execute(
                "SELECT token_id, AVG(spread) FROM order_book_snapshots "
                "WHERE NOT is_empty GROUP BY token_id"
            ).fetchall()
            g.liquid_tokens = sum(1 for _, sp in rows if sp and sp < 0.05)
        except sqlite3.OperationalError:
            pass

    # Spike candidates (simplified: count liquid tokens with >5c range)
    if g.snapshots > 0:
        try:
            liquid_tids = conn.execute(
                "SELECT token_id FROM order_book_snapshots "
                "WHERE NOT is_empty GROUP BY token_id HAVING AVG(spread) < 0.05 "
                "ORDER BY AVG(spread) LIMIT 10"
            ).fetchall()
            spikes = 0
            for (tid,) in liquid_tids:
                rows = conn.execute(
                    "SELECT server_ts_ms, mid_price FROM order_book_snapshots "
                    "WHERE token_id = ? AND mid_price IS NOT NULL AND mid_price > 0 "
                    "ORDER BY server_ts_ms", (tid,)
                ).fetchall()
                if len(rows) < 20:
                    continue
                for i in range(len(rows)):
                    t0_ms, p0 = rows[i]
                    max_dev = 0
                    max_j = i
                    for j in range(i + 1, len(rows)):
                        if rows[j][0] - t0_ms > 300_000:
                            break
                        dev = abs(rows[j][1] - p0)
                        if dev > max_dev:
                            max_dev = dev
                            max_j = j
                    if max_dev < 0.05:
                        continue
                    t_peak = rows[max_j][0]
                    p_peak = rows[max_j][1]
                    for k in range(max_j + 1, len(rows)):
                        if rows[k][0] - t_peak > 300_000:
                            break
                        if abs(p_peak - rows[k][1]) > max_dev * 0.3:
                            spikes += 1
                            break
            g.spike_candidates = spikes
        except sqlite3.OperationalError:
            pass

    conn.close()

    # Determine status
    g.issues = _classify_issues(g)
    if not g.issues:
        g.status = "Healthy"
    elif any("CRITICAL" in i for i in g.issues):
        g.status = "Degraded"
    else:
        g.status = "Warning"

    return g


def _classify_issues(g: GameSummary) -> list[str]:
    """Identify issues with a game's data."""
    issues = []
    if g.trades == 0:
        issues.append("CRITICAL: 0 trades")
    if g.events == 0 and g.sport in ("nba", "nhl", "tennis", "mlb", "soccer", "cricket"):
        issues.append("0 match events")
    if g.empty_book_pct > 50:
        issues.append(f"CRITICAL: {g.empty_book_pct}% empty books")
    if g.trade_dupes > 0:
        issues.append(f"{g.trade_dupes} trade dupes")
    if g.gaps > 5:
        issues.append(f"{g.gaps} data gaps")
    return issues


def aggregate_by_sport(games: list[GameSummary]) -> list[SportSummary]:
    """Group games by sport and compute aggregates."""
    by_sport: dict[str, list[GameSummary]] = defaultdict(list)
    for g in games:
        by_sport[g.sport or "unknown"].append(g)

    summaries = []
    for sport, sport_games in sorted(by_sport.items()):
        s = SportSummary(sport=sport, games=len(sport_games))
        s.total_snapshots = sum(g.snapshots for g in sport_games)
        s.total_trades = sum(g.trades for g in sport_games)
        s.total_signals = sum(g.signals for g in sport_games)
        s.total_events = sum(g.events for g in sport_games)
        s.total_gaps = sum(g.gaps for g in sport_games)
        s.total_spikes = sum(g.spike_candidates for g in sport_games)

        s.avg_snapshots = s.total_snapshots / s.games
        s.avg_trades = s.total_trades / s.games
        s.avg_signals = s.total_signals / s.games
        s.avg_events = s.total_events / s.games
        s.avg_gaps = s.total_gaps / s.games
        s.avg_liquid_tokens = sum(g.liquid_tokens for g in sport_games) / s.games

        # Sport-level status
        degraded = sum(1 for g in sport_games if g.status == "Degraded")
        warning = sum(1 for g in sport_games if g.status == "Warning")
        if degraded == s.games:
            s.status = "Degraded"
        elif degraded > 0:
            s.status = f"Mixed ({degraded}/{s.games} degraded)"
        elif warning > 0:
            s.status = f"Warning ({warning}/{s.games})"
        else:
            s.status = "Healthy"

        # Collect unique issues
        all_issues = set()
        for g in sport_games:
            all_issues.update(g.issues)
        s.issues = sorted(all_issues)

        s.game_details = [g.name for g in sport_games]
        summaries.append(s)

    return summaries


def _fmt_num(n: float) -> str:
    """Format number with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    if isinstance(n, float):
        return f"{n:.0f}"
    return str(n)


def print_sport_table(summaries: list[SportSummary]):
    """Print the main per-sport summary table."""
    print()
    print("=" * 100)
    print("  COLLECTION SUMMARY")
    print("=" * 100)

    # Header
    print(f"\n  {'Sport':<10} {'Status':<22} {'Games':>5} "
          f"{'Snaps/game':>11} {'Trades/game':>12} {'Signals/game':>13} "
          f"{'Events/game':>12} {'Spikes':>7}")
    print(f"  {'─' * 10} {'─' * 22} {'─' * 5} "
          f"{'─' * 11} {'─' * 12} {'─' * 13} {'─' * 12} {'─' * 7}")

    for s in summaries:
        print(f"  {s.sport:<10} {s.status:<22} {s.games:>5} "
              f"{_fmt_num(s.avg_snapshots):>11} {_fmt_num(s.avg_trades):>12} "
              f"{_fmt_num(s.avg_signals):>13} {_fmt_num(s.avg_events):>12} "
              f"{_fmt_num(s.total_spikes):>7}")

    # Totals
    total_games = sum(s.games for s in summaries)
    total_snaps = sum(s.total_snapshots for s in summaries)
    total_trades = sum(s.total_trades for s in summaries)
    total_signals = sum(s.total_signals for s in summaries)
    total_events = sum(s.total_events for s in summaries)
    total_spikes = sum(s.total_spikes for s in summaries)
    print(f"  {'─' * 10} {'─' * 22} {'─' * 5} "
          f"{'─' * 11} {'─' * 12} {'─' * 13} {'─' * 12} {'─' * 7}")
    print(f"  {'TOTAL':<10} {'':22} {total_games:>5} "
          f"{_fmt_num(total_snaps):>11} {_fmt_num(total_trades):>12} "
          f"{_fmt_num(total_signals):>13} {_fmt_num(total_events):>12} "
          f"{_fmt_num(total_spikes):>7}")


def print_game_details(games: list[GameSummary]):
    """Print per-game detail rows."""
    print()
    print("=" * 100)
    print("  PER-GAME DETAILS")
    print("=" * 100)

    # Header
    print(f"\n  {'Game':<40} {'Sport':<8} {'Snaps':>7} {'Trades':>7} "
          f"{'Signals':>8} {'Events':>7} {'Liquid':>6} {'Spikes':>7} {'Status'}")
    print(f"  {'─' * 40} {'─' * 8} {'─' * 7} {'─' * 7} "
          f"{'─' * 8} {'─' * 7} {'─' * 6} {'─' * 7} {'─' * 12}")

    for g in sorted(games, key=lambda x: (x.sport, x.name)):
        status_str = g.status
        if g.issues:
            status_str += f" ({'; '.join(g.issues[:2])})"
        print(f"  {g.name:<40} {g.sport:<8} {_fmt_num(g.snapshots):>7} "
              f"{_fmt_num(g.trades):>7} {_fmt_num(g.signals):>8} "
              f"{_fmt_num(g.events):>7} {g.liquid_tokens:>6} "
              f"{_fmt_num(g.spike_candidates):>7} {status_str}")


def print_issues_summary(games: list[GameSummary]):
    """Print a summary of issues across all games."""
    all_issues = [(g.name, g.sport, issue) for g in games for issue in g.issues]
    if not all_issues:
        print(f"\n  No issues found across {len(games)} games.")
        return

    print()
    print("=" * 100)
    print(f"  ISSUES ({len(all_issues)} across {sum(1 for g in games if g.issues)}/{len(games)} games)")
    print("=" * 100)

    by_sport = defaultdict(list)
    for name, sport, issue in all_issues:
        by_sport[sport].append((name, issue))

    for sport in sorted(by_sport):
        print(f"\n  {sport.upper()}")
        for name, issue in by_sport[sport]:
            print(f"    {name}: {issue}")


def print_recommendations(summaries: list[SportSummary], games: list[GameSummary]):
    """Print actionable recommendations."""
    print()
    print("=" * 100)
    print("  RECOMMENDATIONS")
    print("=" * 100)

    rec_num = 1

    # Games with 0 events but should have them
    no_events = [g for g in games if g.events == 0
                 and g.sport in ("nba", "nhl", "tennis", "mlb", "soccer", "cricket")]
    if no_events:
        sports = set(g.sport for g in no_events)
        print(f"\n  {rec_num}. {len(no_events)} game(s) have 0 match events "
              f"(sports: {', '.join(sorted(sports))})")
        for g in no_events:
            print(f"     - {g.name}")
        rec_num += 1

    # Cricket/stale data
    cricket = [g for g in games if "cri" in g.sport.lower() if g.trades == 0]
    if cricket:
        print(f"\n  {rec_num}. {len(cricket)} cricket game(s) have 0 trades — "
              f"likely stale configs (check game dates)")
        rec_num += 1

    # Spike-rich games
    spike_games = sorted([g for g in games if g.spike_candidates > 100],
                         key=lambda x: -x.spike_candidates)
    if spike_games:
        print(f"\n  {rec_num}. Top spike-candidate games for Phase 3 analysis:")
        for g in spike_games[:5]:
            print(f"     - {g.name}: {g.spike_candidates:,} spikes, "
                  f"{g.liquid_tokens} liquid tokens, {g.events} events")
        rec_num += 1

    print()


def main():
    json_mode = "--json" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--json"]

    db_files = args if args else sorted(glob.glob("data/*.db"))

    if not db_files:
        print("No databases found. Specify paths or run from project root with data/ dir.")
        sys.exit(1)

    games = []
    for dbf in db_files:
        try:
            games.append(analyze_game(dbf))
        except Exception as e:
            print(f"ERROR processing {dbf}: {e}")

    if not games:
        print("No databases could be analyzed.")
        sys.exit(1)

    summaries = aggregate_by_sport(games)

    if json_mode:
        output = {
            "by_sport": [
                {
                    "sport": s.sport,
                    "games": s.games,
                    "status": s.status,
                    "total_snapshots": s.total_snapshots,
                    "total_trades": s.total_trades,
                    "total_signals": s.total_signals,
                    "total_events": s.total_events,
                    "total_spikes": s.total_spikes,
                    "avg_snapshots": round(s.avg_snapshots),
                    "avg_trades": round(s.avg_trades),
                    "avg_signals": round(s.avg_signals),
                    "avg_events": round(s.avg_events),
                    "issues": s.issues,
                }
                for s in summaries
            ],
            "games": [g.to_dict() for g in games],
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        print_sport_table(summaries)
        print_game_details(games)
        print_issues_summary(games)
        print_recommendations(summaries, games)


if __name__ == "__main__":
    main()
