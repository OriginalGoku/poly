"""Streamlit dashboard for inspecting collected Polymarket data.

Usage: streamlit run dashboard.py
"""

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path("data")

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


# --- Page config ---

st.set_page_config(page_title="Polymarket Collector", layout="wide")
st.title("Polymarket Data Inspector")

# --- DB selector ---

dbs = list_dbs()
if not dbs:
    st.error("No databases found in data/")
    st.stop()

db_names = [p.name for p in dbs]
db_labels = {n: f"✓ {n}" if "VALIDATE" in n.upper() else n for n in db_names}
selected = st.sidebar.selectbox("Database", db_names, format_func=lambda n: db_labels[n])
db_path = DATA_DIR / selected
conn = get_conn(db_path)

# File size
size_mb = db_path.stat().st_size / (1024 * 1024)
st.sidebar.caption(f"{size_mb:.1f} MB")

# --- Overview ---

st.header("Overview")

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

# Markets info + build token_map for all tabs
markets_df = load_markets(conn)
token_map: dict[str, str] = {}
for _, row in markets_df.iterrows():
    try:
        import json as _json
        tids = _json.loads(row["token_ids_json"])
        outcomes = _json.loads(row["outcomes_json"])
        for tid, outcome in zip(tids, outcomes):
            token_map[tid] = f"{outcome} ({row['question'][:40]})"
    except Exception:
        pass

if not markets_df.empty:
    st.subheader("Markets")
    display_cols = ["question", "outcomes_json", "tick_size", "min_order_size"]
    display_cols = [c for c in display_cols if c in markets_df.columns]
    st.dataframe(markets_df[display_cols], use_container_width=True, hide_index=True)

# --- Tabs ---

tab_names = ["Price Signals", "Trades", "Order Books", "Dual-Write Validation", "Data Gaps"]
tabs = st.tabs(tab_names)


# ===================== PRICE SIGNALS =====================

with tabs[0]:
    if not table_exists(conn, "price_signals") or counts["price_signals"] == 0:
        st.info("No price signals in this database (pre-WS or no best_bid_ask events).")
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

        # Stats
        if not df.empty:
            st.caption("Summary statistics")
            st.dataframe(df[["best_bid", "best_ask", "mid_price", "spread"]].describe().T, use_container_width=True)


# ===================== TRADES =====================

with tabs[1]:
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

        # Map token to outcome name
        trades_df["label"] = trades_df["token_id"].map(token_map).fillna(trades_df["outcome"])

        col1, col2, col3 = st.columns(3)
        col1.metric("Total trades", f"{len(trades_df):,}")
        col2.metric("Total notional", f"${trades_df['notional'].sum():,.0f}")
        if has_source:
            source_counts = trades_df["source"].value_counts()
            col3.metric("Sources", ", ".join(f"{k}: {v}" for k, v in source_counts.items()))

        # Price over time by outcome
        st.caption("Trade prices over time")
        for label in trades_df["label"].unique():
            subset = trades_df[trades_df["label"] == label]
            st.caption(f"**{label}** ({len(subset)} trades)")
            chart_data = subset.set_index("time")[["price"]]
            st.line_chart(chart_data)

        # Trade size distribution
        st.caption("Trade size distribution")
        st.bar_chart(trades_df["size"].describe())

        # Recent trades table
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

with tabs[2]:
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

        # Query only columns that exist in this DB
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

with tabs[3]:
    has_source = column_exists(conn, "trades", "source")
    if not has_source:
        st.info("No `source` column — this DB wasn't run with `--validate`.")
    else:
        st.subheader("Dual-Write Validation: WS vs REST")

        source_counts = pd.read_sql(
            "SELECT source, COUNT(*) as count FROM trades GROUP BY source", conn
        )
        st.dataframe(source_counts, use_container_width=True, hide_index=True)

        # Overlap
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

        # Filter to only trades after WS started
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

            # Timeline comparison
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

with tabs[4]:
    if counts["data_gaps"] == 0:
        st.success("No data gaps recorded.")
    else:
        st.subheader("Data Gaps")
        gaps_df = pd.read_sql("SELECT * FROM data_gaps ORDER BY gap_start", conn)
        st.dataframe(gaps_df, use_container_width=True, hide_index=True)

conn.close()
