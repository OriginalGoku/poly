"""Streamlit dashboard for inspecting collected Polymarket data.

Usage: streamlit run dashboard.py
"""

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path("data")
LOGS_DIR = Path("logs")

# --- Helpers ---


def list_dbs() -> list[Path]:
    return sorted(DATA_DIR.glob("*.db"), key=lambda p: p.name)


def get_conn(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] > 0


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(c[1] == column for c in cols)
    except Exception:
        return False


def load_markets(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM markets", conn)


def get_running_collectors() -> set[str]:
    """Return set of match_ids currently running as collector processes."""
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True
        )
        running = set()
        for line in result.stdout.splitlines():
            if "python -m collector" in line and "--config" in line:
                # Extract config path
                parts = line.split("--config")
                if len(parts) > 1:
                    cfg = parts[1].strip().split()[0]
                    match_id = Path(cfg).stem.replace("match_", "")
                    running.add(match_id)
        return running
    except Exception:
        return set()


def quick_stats(db_path: Path) -> dict:
    """Get key stats for a single DB without loading everything."""
    stats = {
        "name": db_path.stem.replace("-VALIDATE", ""),
        "size_mb": db_path.stat().st_size / (1024 * 1024),
        "snapshots": 0,
        "trades": 0,
        "price_signals": 0,
        "game_events": 0,
        "data_gaps": 0,
        "last_ts": None,
        "duration_min": 0,
        "sport": "unknown",
        "ws_capture_pct": None,
    }
    try:
        conn = sqlite3.connect(str(db_path))

        for table, key in [
            ("order_book_snapshots", "snapshots"),
            ("trades", "trades"),
            ("price_signals", "price_signals"),
            ("match_events", "game_events"),
            ("data_gaps", "data_gaps"),
        ]:
            if table_exists(conn, table):
                stats[key] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        # Last activity timestamp
        ts_queries = [
            "SELECT MAX(server_ts_ms) FROM order_book_snapshots",
            "SELECT MAX(server_ts_ms) FROM price_signals",
        ]
        max_ts = 0
        for q in ts_queries:
            try:
                row = conn.execute(q).fetchone()
                if row and row[0]:
                    max_ts = max(max_ts, row[0])
            except Exception:
                pass
        if max_ts:
            stats["last_ts"] = datetime.fromtimestamp(max_ts / 1000, tz=timezone.utc)

        # Duration
        try:
            row = conn.execute("SELECT MIN(server_ts_ms), MAX(server_ts_ms) FROM order_book_snapshots").fetchone()
            if row and row[0] and row[1]:
                stats["duration_min"] = (row[1] - row[0]) / 60000
        except Exception:
            pass

        # Sport from markets table
        try:
            row = conn.execute("SELECT sport FROM markets LIMIT 1").fetchone()
            if row:
                stats["sport"] = row[0]
        except Exception:
            # derive from name
            name = stats["name"]
            for s in ["nba", "nhl", "val", "cs2", "atp", "wta", "lol", "dota2"]:
                if name.startswith(s):
                    stats["sport"] = s
                    break

        # WS capture rate (dual-write)
        if column_exists(conn, "trades", "source") and stats["trades"] > 0:
            try:
                ws_min = conn.execute("SELECT MIN(server_ts_ms) FROM trades WHERE source='ws'").fetchone()[0]
                if ws_min:
                    ws = conn.execute("SELECT COUNT(*) FROM trades WHERE source='ws' AND server_ts_ms >= ?", (ws_min,)).fetchone()[0]
                    rest_only = conn.execute(f"""
                        SELECT COUNT(*) FROM (
                            SELECT transaction_hash, token_id FROM trades WHERE source='rest' AND server_ts_ms >= {ws_min}
                            EXCEPT
                            SELECT transaction_hash, token_id FROM trades WHERE source='ws' AND server_ts_ms >= {ws_min}
                        )
                    """).fetchone()[0]
                    total = ws + rest_only
                    if total > 0:
                        stats["ws_capture_pct"] = ws / total * 100
            except Exception:
                pass

        conn.close()
    except Exception:
        pass
    return stats


# --- Page config ---

st.set_page_config(page_title="Polymarket Collector", layout="wide")
st.title("Polymarket Data Inspector")

# --- DB selector (sidebar) ---

dbs = list_dbs()
if not dbs:
    st.error("No databases found in data/")
    st.stop()

db_names = [p.name for p in dbs]
db_labels = {n: f"✓ {n}" if "VALIDATE" in n.upper() else n for n in db_names}
selected = st.sidebar.selectbox("Database", db_names, format_func=lambda n: db_labels[n])
db_path = DATA_DIR / selected
conn = get_conn(db_path)

size_mb = db_path.stat().st_size / (1024 * 1024)
st.sidebar.caption(f"{size_mb:.1f} MB")

if st.sidebar.button("Refresh"):
    st.rerun()

# --- Tabs ---

tab_names = ["Summary", "Price Signals", "Trades", "Order Books", "Dual-Write Validation", "Data Gaps"]
tabs = st.tabs(tab_names)


# ===================== SUMMARY =====================

with tabs[0]:
    st.subheader("Collection Summary")

    running = get_running_collectors()

    # Load stats for all DBs
    with st.spinner("Loading stats for all databases..."):
        all_stats = [quick_stats(p) for p in dbs]

    # Totals row
    total_snapshots = sum(s["snapshots"] for s in all_stats)
    total_trades = sum(s["trades"] for s in all_stats)
    total_signals = sum(s["price_signals"] for s in all_stats)
    total_events = sum(s["game_events"] for s in all_stats)
    total_gaps = sum(s["data_gaps"] for s in all_stats)
    n_running = len(running)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Collectors Running", n_running)
    c2.metric("Total Snapshots", f"{total_snapshots:,}")
    c3.metric("Total Trades", f"{total_trades:,}")
    c4.metric("Price Signals", f"{total_signals:,}")
    c5.metric("Game Events", f"{total_events:,}")
    c6.metric("Data Gaps", total_gaps, delta_color="inverse")

    st.divider()

    # Per-DB table grouped by sport
    rows = []
    for s in all_stats:
        match_id = s["name"]
        is_running = match_id in running
        last_seen = s["last_ts"].strftime("%H:%M:%S UTC") if s["last_ts"] else "—"
        ws_pct = f"{s['ws_capture_pct']:.0f}%" if s["ws_capture_pct"] is not None else "—"
        rows.append({
            "Status": "🟢" if is_running else "🔴",
            "Match": match_id,
            "Sport": s["sport"],
            "Size MB": round(s["size_mb"], 2),
            "Snapshots": s["snapshots"],
            "Trades": s["trades"],
            "Signals": s["price_signals"],
            "Events": s["game_events"],
            "Gaps": s["data_gaps"],
            "Duration (min)": round(s["duration_min"], 1),
            "Last Active": last_seen,
            "WS Capture": ws_pct,
        })

    df = pd.DataFrame(rows)

    # Sport filter
    sports = ["All"] + sorted(df["Sport"].unique().tolist())
    sport_filter = st.selectbox("Filter by sport", sports)
    if sport_filter != "All":
        df = df[df["Sport"] == sport_filter]

    # Highlight rows with game events
    def highlight_events(row):
        if row["Events"] > 0:
            return ["background-color: #1a3a1a"] * len(row)
        if row["Status"] == "🔴":
            return ["opacity: 0.5"] * len(row)
        return [""] * len(row)

    st.dataframe(
        df.style.apply(highlight_events, axis=1),
        use_container_width=True,
        hide_index=True,
    )
    st.caption("🟢 = collector running  |  green rows = have game events")

    # WS capture summary for DBs with enough data
    ws_dbs = [s for s in all_stats if s["ws_capture_pct"] is not None and s["trades"] >= 10]
    if ws_dbs:
        st.divider()
        st.subheader("WS Capture Rates")
        cols = st.columns(min(len(ws_dbs), 4))
        for i, s in enumerate(ws_dbs):
            pct = s["ws_capture_pct"]
            col = cols[i % 4]
            if pct >= 98:
                col.metric(s["name"][:25], f"{pct:.1f}%", "PASS ✓")
            else:
                col.metric(s["name"][:25], f"{pct:.1f}%", "FAIL ✗", delta_color="inverse")

    # Auto-fix report
    report_files = sorted(LOGS_DIR.glob("auto_fix_report.txt"))
    fitness_logs = sorted(LOGS_DIR.glob("fitness_check_*.log"))

    if report_files:
        st.divider()
        st.subheader("Auto-Fix Report")
        with st.expander("View report", expanded=True):
            st.code(report_files[0].read_text(), language=None)

    if fitness_logs:
        st.divider()
        st.subheader("Latest Fitness Check Log")
        log_names = [f.name for f in fitness_logs]
        selected_log = st.selectbox("Log file", log_names, index=len(log_names) - 1)
        log_path = LOGS_DIR / selected_log
        with st.expander("View full log", expanded=False):
            st.code(log_path.read_text(), language=None)


# ===================== OVERVIEW (single DB) =====================

st.header(f"Detail: {selected}")

counts = {}
for table in ["order_book_snapshots", "trades", "price_signals", "match_events", "data_gaps"]:
    if table_exists(conn, table):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    else:
        counts[table] = None

cols = st.columns(5)
labels = ["Snapshots", "Trades", "Price Signals", "Game Events", "Data Gaps"]
keys = ["order_book_snapshots", "trades", "price_signals", "match_events", "data_gaps"]
for col, label, key in zip(cols, labels, keys):
    v = counts[key]
    col.metric(label, f"{v:,}" if v is not None else "N/A")

markets_df = load_markets(conn)
token_map: dict[str, str] = {}
for _, row in markets_df.iterrows():
    try:
        tids = json.loads(row["token_ids_json"])
        outcomes = json.loads(row["outcomes_json"])
        for tid, outcome in zip(tids, outcomes):
            token_map[tid] = f"{outcome} ({row['question'][:40]})"
    except Exception:
        pass

if not markets_df.empty:
    st.subheader("Markets")
    display_cols = ["question", "outcomes_json", "tick_size", "min_order_size"]
    display_cols = [c for c in display_cols if c in markets_df.columns]
    st.dataframe(markets_df[display_cols], use_container_width=True, hide_index=True)


# ===================== PRICE SIGNALS =====================

with tabs[1]:
    if not table_exists(conn, "price_signals") or counts["price_signals"] == 0:
        st.info("No price signals in this database.")
    else:
        st.subheader("Price Signals (best_bid_ask)")

        signals_tokens = pd.read_sql(
            "SELECT DISTINCT token_id FROM price_signals", conn
        )["token_id"].tolist()

        token_labels = {t: token_map.get(t, t[:20] + "...") for t in signals_tokens}
        selected_token = st.selectbox(
            "Token", signals_tokens, format_func=lambda t: token_labels[t], key="sig_token"
        )

        df = pd.read_sql(
            "SELECT server_ts_ms, best_bid, best_ask, mid_price, spread FROM price_signals WHERE token_id=? ORDER BY server_ts_ms",
            conn,
            params=(selected_token,),
        )
        df["time"] = pd.to_datetime(df["server_ts_ms"], unit="ms")

        col1, col2 = st.columns(2)
        with col1:
            st.caption(f"{len(df):,} signals")
            st.line_chart(df.set_index("time")[["best_bid", "best_ask", "mid_price"]])
        with col2:
            st.caption("Spread over time")
            st.line_chart(df.set_index("time")[["spread"]])

        if not df.empty:
            st.caption("Summary statistics")
            st.dataframe(df[["best_bid", "best_ask", "mid_price", "spread"]].describe().T, use_container_width=True)


# ===================== TRADES =====================

with tabs[2]:
    if counts["trades"] == 0:
        st.info("No trades in this database.")
    else:
        st.subheader("Trades")

        has_source = column_exists(conn, "trades", "source")

        trades_df = pd.read_sql(
            f"SELECT server_ts_ms, token_id, price, size, side, outcome, outcome_index, transaction_hash{', source' if has_source else ''} FROM trades ORDER BY server_ts_ms",
            conn,
        )
        trades_df["time"] = pd.to_datetime(trades_df["server_ts_ms"], unit="ms")
        trades_df["notional"] = trades_df["price"] * trades_df["size"]
        trades_df["label"] = trades_df["token_id"].map(token_map).fillna(trades_df["outcome"])

        col1, col2, col3 = st.columns(3)
        col1.metric("Total trades", f"{len(trades_df):,}")
        col2.metric("Total notional", f"${trades_df['notional'].sum():,.0f}")
        if has_source:
            source_counts = trades_df["source"].value_counts()
            col3.metric("Sources", ", ".join(f"{k}: {v}" for k, v in source_counts.items()))

        st.caption("Trade prices over time")
        for label in trades_df["label"].unique():
            subset = trades_df[trades_df["label"] == label]
            st.caption(f"**{label}** ({len(subset)} trades)")
            st.line_chart(subset.set_index("time")[["price"]])

        st.caption("Trade size distribution")
        st.bar_chart(trades_df["size"].describe())

        st.caption("Recent trades")
        display_cols = ["time", "label", "price", "size", "side", "notional", "transaction_hash"]
        if has_source:
            display_cols.insert(-1, "source")
        st.dataframe(
            trades_df[display_cols].tail(50).sort_values("time", ascending=False),
            use_container_width=True,
            hide_index=True,
        )


# ===================== ORDER BOOKS =====================

with tabs[3]:
    if counts["order_book_snapshots"] == 0:
        st.info("No order book snapshots.")
    else:
        st.subheader("Order Book Snapshots")

        snap_tokens = pd.read_sql(
            "SELECT DISTINCT token_id FROM order_book_snapshots", conn
        )["token_id"].tolist()
        snap_labels = {t: token_map.get(t, t[:20] + "...") for t in snap_tokens}

        selected_snap_token = st.selectbox(
            "Token", snap_tokens, format_func=lambda t: snap_labels[t], key="snap_token"
        )

        snap_cols = [c[1] for c in conn.execute("PRAGMA table_info(order_book_snapshots)").fetchall()]
        select_cols = ["server_ts_ms", "best_bid", "best_ask", "mid_price", "spread", "is_empty"]
        optional = ["book_depth_usd", "inside_liquidity_usd", "fetch_latency_ms"]
        for c in optional:
            if c in snap_cols:
                select_cols.append(c)

        snap_df = pd.read_sql(
            f"SELECT {', '.join(select_cols)} FROM order_book_snapshots WHERE token_id=? ORDER BY server_ts_ms",
            conn,
            params=(selected_snap_token,),
        )
        snap_df["time"] = pd.to_datetime(snap_df["server_ts_ms"], unit="ms")

        col1, col2, col3 = st.columns(3)
        col1.metric("Snapshots", f"{len(snap_df):,}")
        col2.metric("Empty books", f"{snap_df['is_empty'].sum()}")
        if "fetch_latency_ms" in snap_df.columns:
            col3.metric("Avg latency", f"{snap_df['fetch_latency_ms'].mean():.0f}ms")

        st.caption("Mid price over time")
        st.line_chart(snap_df.set_index("time")[["mid_price"]])

        col1, col2 = st.columns(2)
        with col1:
            if "book_depth_usd" in snap_df.columns:
                st.caption("Book depth (USD within 5% of mid)")
                st.line_chart(snap_df.set_index("time")[["book_depth_usd"]])
        with col2:
            if "inside_liquidity_usd" in snap_df.columns:
                st.caption("Inside liquidity (best bid + ask notional)")
                st.line_chart(snap_df.set_index("time")[["inside_liquidity_usd"]])

        st.caption("Spread over time")
        st.line_chart(snap_df.set_index("time")[["spread"]])


# ===================== DUAL-WRITE VALIDATION =====================

with tabs[4]:
    has_source = column_exists(conn, "trades", "source")
    if not has_source:
        st.info("No `source` column — this DB wasn't run with `--validate`.")
    else:
        st.subheader("Dual-Write Validation: WS vs REST")

        source_counts = pd.read_sql(
            "SELECT source, COUNT(*) as count FROM trades GROUP BY source", conn
        )
        st.dataframe(source_counts, use_container_width=True, hide_index=True)

        overlap = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT transaction_hash, token_id FROM trades WHERE source='ws'
                INTERSECT
                SELECT transaction_hash, token_id FROM trades WHERE source='rest'
            )
        """).fetchone()[0]

        ws_only = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT transaction_hash, token_id FROM trades WHERE source='ws'
                EXCEPT
                SELECT transaction_hash, token_id FROM trades WHERE source='rest'
            )
        """).fetchone()[0]

        rest_only = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT transaction_hash, token_id FROM trades WHERE source='rest'
                EXCEPT
                SELECT transaction_hash, token_id FROM trades WHERE source='ws'
            )
        """).fetchone()[0]

        col1, col2, col3 = st.columns(3)
        col1.metric("Both sources", overlap)
        col2.metric("WS only", ws_only)
        col3.metric("REST only", rest_only)

        ws_min_ts = conn.execute(
            "SELECT MIN(server_ts_ms) FROM trades WHERE source='ws'"
        ).fetchone()[0]

        if ws_min_ts:
            st.divider()
            st.caption(f"Trades after WS connected (ts >= {ws_min_ts})")

            ws_after = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE source='ws' AND server_ts_ms >= ?",
                (ws_min_ts,),
            ).fetchone()[0]
            rest_after = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE source='rest' AND server_ts_ms >= ?",
                (ws_min_ts,),
            ).fetchone()[0]
            overlap_after = conn.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT transaction_hash, token_id FROM trades WHERE source='ws' AND server_ts_ms >= {ws_min_ts}
                    INTERSECT
                    SELECT transaction_hash, token_id FROM trades WHERE source='rest' AND server_ts_ms >= {ws_min_ts}
                )
            """).fetchone()[0]
            rest_only_after = conn.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT transaction_hash, token_id FROM trades WHERE source='rest' AND server_ts_ms >= {ws_min_ts}
                    EXCEPT
                    SELECT transaction_hash, token_id FROM trades WHERE source='ws' AND server_ts_ms >= {ws_min_ts}
                )
            """).fetchone()[0]

            total_unique_after = overlap_after + (ws_after - overlap_after) + rest_only_after
            ws_pct = ws_after / total_unique_after * 100 if total_unique_after > 0 else 0

            col1, col2, col3 = st.columns(3)
            col1.metric("WS trades", ws_after)
            col2.metric("REST trades", rest_after)
            col3.metric("WS capture rate", f"{ws_pct:.1f}%")

            if ws_pct >= 98 and total_unique_after >= 10:
                st.success(f"PASS: WS captures {ws_pct:.1f}% of trades ({total_unique_after} unique)")
            elif total_unique_after < 10:
                st.warning(f"Insufficient data: {total_unique_after} unique trades. Need more time.")
            else:
                st.error(f"FAIL: WS captures {ws_pct:.1f}% (need >= 98%). REST-only: {rest_only_after}")

            st.caption("Trade timeline by source")
            timeline_df = pd.read_sql(
                f"SELECT server_ts_ms, source, price, size FROM trades WHERE server_ts_ms >= {ws_min_ts} ORDER BY server_ts_ms",
                conn,
            )
            timeline_df["time"] = pd.to_datetime(timeline_df["server_ts_ms"], unit="ms")
            if not timeline_df.empty:
                pivot = timeline_df.pivot_table(
                    index="time", columns="source", values="price", aggfunc="first"
                )
                st.line_chart(pivot)
        else:
            st.warning("No WS trades yet. Keep the collector running.")


# ===================== DATA GAPS =====================

with tabs[5]:
    if counts["data_gaps"] == 0:
        st.success("No data gaps recorded.")
    else:
        st.subheader("Data Gaps")
        gaps_df = pd.read_sql("SELECT * FROM data_gaps ORDER BY gap_start", conn)
        st.dataframe(gaps_df, use_container_width=True, hide_index=True)

conn.close()
