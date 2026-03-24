"""Tests for database schema creation, insert/query round-trip, and deduplication."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from collector.db import Database
from collector.models import (
    MatchConfig,
    MatchEvent,
    MarketConfig,
    OrderBookSnapshot,
    Trade,
    TradeWatermark,
)


@pytest_asyncio.fixture
async def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        database = Database(db_path)
        await database.open()
        yield database
        await database.close()


@pytest.fixture
def sample_config():
    return MatchConfig(
        match_id="test-match-1",
        sport="nba",
        team1="Team A",
        team2="Team B",
        tournament="Test Tournament",
        best_of=None,
        scheduled_start="2026-03-25T18:00:00Z",
        data_source="nba_cdn",
        external_id="0022501038",
        markets=[
            MarketConfig(
                market_id="0xabc",
                question="Team A vs Team B",
                relationship="match_winner",
                outcomes=["Team A", "Team B"],
                token_ids=["0x111", "0x222"],
            )
        ],
    )


@pytest.mark.asyncio
async def test_schema_creation(db):
    """Tables and indexes are created."""
    async with db.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        tables = [row[0] for row in await cur.fetchall()]
    expected = [
        "collection_runs",
        "data_gaps",
        "market_match_mapping",
        "markets",
        "match_events",
        "match_events_enriched",
        "matches",
        "order_book_snapshots",
        "price_signals",
        "trade_watermarks",
        "trades",
    ]
    for t in expected:
        assert t in tables, f"Missing table: {t}"


@pytest.mark.asyncio
async def test_insert_match_and_market(db, sample_config):
    """Match and market insertion and query."""
    await db.insert_match(sample_config)
    await db.insert_market(
        market_id="0xabc",
        question="Team A vs Team B",
        outcomes=["Team A", "Team B"],
        token_ids=["0x111", "0x222"],
        tick_size=0.01,
        min_order_size=5.0,
    )
    await db.insert_market_match_mapping("0xabc", "test-match-1", "match_winner")

    async with db.db.execute("SELECT * FROM matches WHERE match_id='test-match-1'") as cur:
        row = await cur.fetchone()
    assert row is not None

    async with db.db.execute("SELECT tick_size, min_order_size FROM markets WHERE market_id='0xabc'") as cur:
        row = await cur.fetchone()
    assert row == (0.01, 5.0)


@pytest.mark.asyncio
async def test_insert_snapshots(db):
    """Snapshot insert and count."""
    snap = OrderBookSnapshot(
        market_id="0xabc",
        token_id="0x111",
        local_ts="2026-03-25T18:00:00+00:00",
        local_mono_ns=123456789,
        server_ts_raw="1774366505417",
        server_ts_ms=1774366505417,
        fetch_latency_ms=120.0,
        best_bid=0.45,
        best_bid_size=100.0,
        best_ask=0.55,
        best_ask_size=200.0,
        mid_price=0.50,
        spread=0.10,
        bid_depth_json="[[0.45, 100]]",
        ask_depth_json="[[0.55, 200]]",
        book_depth_usd=155.0,
        inside_liquidity_usd=155.0,
        is_empty=False,
        last_trade_price=0.48,
        seconds_since_last_trade=None,
    )
    count = await db.insert_snapshots([snap, snap])
    assert count == 2
    total = await db.count_snapshots()
    assert total == 2


@pytest.mark.asyncio
async def test_insert_snapshots_quality_metrics(db):
    """Quality metrics (spread, book_depth_usd, is_empty) are stored correctly."""
    snap = OrderBookSnapshot(
        market_id="0xabc",
        token_id="0x111",
        local_ts="2026-03-25T18:00:00+00:00",
        local_mono_ns=0,
        server_ts_raw="1774366505417",
        server_ts_ms=1774366505417,
        fetch_latency_ms=100.0,
        best_bid=None,
        best_bid_size=None,
        best_ask=None,
        best_ask_size=None,
        mid_price=None,
        spread=None,
        bid_depth_json="[]",
        ask_depth_json="[]",
        book_depth_usd=0.0,
        inside_liquidity_usd=0.0,
        is_empty=True,
        last_trade_price=None,
        seconds_since_last_trade=None,
    )
    await db.insert_snapshots([snap])
    async with db.db.execute(
        "SELECT is_empty, spread, book_depth_usd FROM order_book_snapshots WHERE token_id='0x111'"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == 1  # is_empty
    assert row[1] is None  # spread
    assert row[2] == 0.0  # book_depth_usd


@pytest.mark.asyncio
async def test_trade_deduplication(db):
    """Duplicate (transaction_hash, token_id) trades are skipped."""
    trade = Trade(
        market_id="0xabc",
        token_id="0x111",
        local_ts="2026-03-25T18:00:00+00:00",
        server_ts_raw=1774366423,
        server_ts_ms=1774366423000,
        transaction_hash="0xdeadbeef",
        price=0.50,
        size=100.0,
        side="BUY",
        outcome="Team A",
        outcome_index=0,
    )
    inserted1 = await db.insert_trades([trade])
    assert inserted1 == 1
    inserted2 = await db.insert_trades([trade])
    assert inserted2 == 0
    total = await db.count_trades()
    assert total == 1


@pytest.mark.asyncio
async def test_watermark_persistence(db):
    """Watermark round-trip: set and get."""
    wm = TradeWatermark(
        token_id="0x111",
        last_timestamp=1774366423,
        recent_hashes=["0xaaa", "0xbbb"],
    )
    await db.set_watermark(wm)

    loaded = await db.get_watermark("0x111")
    assert loaded is not None
    assert loaded.last_timestamp == 1774366423
    assert loaded.recent_hashes == ["0xaaa", "0xbbb"]

    # Update watermark
    wm2 = TradeWatermark(
        token_id="0x111",
        last_timestamp=1774366500,
        recent_hashes=["0xccc"],
    )
    await db.set_watermark(wm2)
    loaded2 = await db.get_watermark("0x111")
    assert loaded2 is not None
    assert loaded2.last_timestamp == 1774366500


@pytest.mark.asyncio
async def test_gap_logging(db):
    """Gap logging and count."""
    await db.log_gap("polymarket", "2026-03-25T18:00:00Z", "2026-03-25T18:01:00Z", "test gap")
    count = await db.count_gaps()
    assert count == 1


@pytest.mark.asyncio
async def test_match_events(db, sample_config):
    """Match event insert and count."""
    await db.insert_match(sample_config)
    event = MatchEvent(
        match_id="test-match-1",
        local_ts="2026-03-25T18:00:00+00:00",
        server_ts_raw="2026-03-25T18:00:00Z",
        server_ts_ms=1774366505000,
        sport="nba",
        event_type="score_change",
        quarter=1,
        team1_score=3,
        team2_score=5,
        event_team="ATL",
    )
    inserted = await db.insert_match_events([event])
    assert inserted == 1
    count = await db.count_events("test-match-1")
    assert count == 1


@pytest.mark.asyncio
async def test_collection_run_lifecycle(db, sample_config):
    """Start and finish a collection run."""
    await db.insert_match(sample_config)
    run_id = await db.start_collection_run("test-match-1", "nba", '{"test": true}')
    assert run_id is not None

    await db.finish_collection_run(
        run_id=run_id,
        snapshot_count=100,
        trade_count=50,
        event_count=10,
        gap_count=1,
        notes="test run",
    )

    async with db.db.execute(
        "SELECT polymarket_snapshot_count, trade_count, match_event_count, gap_count FROM collection_runs WHERE id=?",
        (run_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row == (100, 50, 10, 1)


@pytest.mark.asyncio
async def test_server_ts_ms_not_null(db):
    """server_ts_ms is populated on all row types."""
    snap = OrderBookSnapshot(
        market_id="0xabc", token_id="0x111",
        local_ts="2026-03-25T18:00:00+00:00", local_mono_ns=0,
        server_ts_raw="1774366505417", server_ts_ms=1774366505417,
        fetch_latency_ms=100.0, best_bid=0.5, best_bid_size=100.0,
        best_ask=0.6, best_ask_size=200.0, mid_price=0.55, spread=0.1,
        bid_depth_json="[]", ask_depth_json="[]", book_depth_usd=0.0,
        inside_liquidity_usd=170.0, is_empty=False, last_trade_price=0.5,
        seconds_since_last_trade=None,
    )
    await db.insert_snapshots([snap])

    trade = Trade(
        market_id="0xabc", token_id="0x111",
        local_ts="2026-03-25T18:00:00+00:00",
        server_ts_raw=1774366423, server_ts_ms=1774366423000,
        transaction_hash="0x123", price=0.5, size=10.0,
        side="BUY", outcome="Yes", outcome_index=0,
    )
    await db.insert_trades([trade])

    event = MatchEvent(
        match_id="m1", local_ts="2026-03-25T18:00:00+00:00",
        server_ts_raw="2026-03-25T18:00:00Z", server_ts_ms=1774366505000,
        sport="nba", event_type="score_change",
    )
    await db.insert_match_events([event])

    # Check no NULLs
    for table in ["order_book_snapshots", "trades", "match_events"]:
        async with db.db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE server_ts_ms IS NULL"
        ) as cur:
            row = await cur.fetchone()
        assert row[0] == 0, f"NULL server_ts_ms in {table}"
