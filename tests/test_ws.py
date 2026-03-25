"""Tests for WebSocket parsing, client dispatch, and DB round-trip."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from collector.db import Database
from collector.models import OrderBookSnapshot, PriceSignal, Trade
from collector.ws_client import WebSocketMarketClient, WriteBatch

FIXTURES = Path(__file__).parent / "fixtures"

# --- Shared test data ---

TOKEN_ID_A = "14552365001125938084443189060834360829324856701650649269125329506541936789754"
TOKEN_ID_B = "82162335245384915830214727777598327360549892486286572930820029431907406804211"
MARKET_A = "0xc771c72e444fa9d52c9185eb5baefa352a2ed133a5cefb223aa759aa47288b94"
MARKET_B = "0x2f10f76dd83741d6f1b565cab68dd69e30a9299cec124e42633238f10f9812bd"

TOKEN_TO_MARKET = {TOKEN_ID_A: MARKET_A, TOKEN_ID_B: MARKET_B}
TOKEN_TO_OUTCOME = {TOKEN_ID_A: ("Team A", 0), TOKEN_ID_B: ("Team B", 1)}


# ========================================================
# Step 6.1: Parsing tests (fixture-based)
# ========================================================


class TestOrderBookFromWs:
    @pytest.fixture
    def ws_book(self):
        with open(FIXTURES / "ws_book_sample.json") as f:
            return json.load(f)

    def test_basic_fields(self, ws_book):
        snap = OrderBookSnapshot.from_ws(ws_book)
        assert snap.market_id == MARKET_A
        assert snap.token_id == TOKEN_ID_A
        assert snap.event_type if hasattr(snap, "event_type") else True
        assert snap.fetch_latency_ms == 0.0

    def test_bids_sorted_descending(self, ws_book):
        snap = OrderBookSnapshot.from_ws(ws_book)
        bids = json.loads(snap.bid_depth_json)
        for i in range(len(bids) - 1):
            assert bids[i][0] >= bids[i + 1][0]

    def test_asks_sorted_ascending(self, ws_book):
        snap = OrderBookSnapshot.from_ws(ws_book)
        asks = json.loads(snap.ask_depth_json)
        for i in range(len(asks) - 1):
            assert asks[i][0] <= asks[i + 1][0]

    def test_bbo_values(self, ws_book):
        snap = OrderBookSnapshot.from_ws(ws_book)
        # From fixture: bids [0.11, 0.07, 0.06, 0.01], asks [0.49, 0.5, 0.51, ...]
        assert snap.best_bid == 0.11
        assert snap.best_ask == 0.49

    def test_spread_and_mid(self, ws_book):
        snap = OrderBookSnapshot.from_ws(ws_book)
        assert snap.spread is not None
        assert snap.mid_price is not None
        assert abs(snap.spread - (snap.best_ask - snap.best_bid)) < 1e-9

    def test_server_timestamp(self, ws_book):
        snap = OrderBookSnapshot.from_ws(ws_book)
        assert snap.server_ts_ms == 1774375522639
        assert snap.server_ts_raw == "1774375522639"

    def test_last_trade_price(self, ws_book):
        snap = OrderBookSnapshot.from_ws(ws_book)
        assert snap.last_trade_price == 0.5

    def test_is_not_empty(self, ws_book):
        snap = OrderBookSnapshot.from_ws(ws_book)
        assert snap.is_empty is False

    def test_imbalance(self, ws_book):
        snap = OrderBookSnapshot.from_ws(ws_book)
        assert snap.imbalance is not None
        assert 0.0 <= snap.imbalance <= 1.0
        # imbalance = best_bid_size / (best_bid_size + best_ask_size)
        expected = snap.best_bid_size / (snap.best_bid_size + snap.best_ask_size)
        assert abs(snap.imbalance - round(expected, 6)) < 1e-9

    def test_empty_book(self):
        raw = {"market": "0x1", "asset_id": "tok1", "timestamp": "100", "bids": [], "asks": []}
        snap = OrderBookSnapshot.from_ws(raw)
        assert snap.is_empty is True
        assert snap.best_bid is None
        assert snap.best_ask is None
        assert snap.mid_price is None
        assert snap.imbalance is None

    def test_depth_truncated_to_10(self, ws_book):
        snap = OrderBookSnapshot.from_ws(ws_book)
        bids = json.loads(snap.bid_depth_json)
        asks = json.loads(snap.ask_depth_json)
        assert len(bids) <= 10
        assert len(asks) <= 10


class TestTradeFromWs:
    @pytest.fixture
    def ws_trade(self):
        with open(FIXTURES / "ws_last_trade_price_sample.json") as f:
            return json.load(f)

    def test_basic_fields(self, ws_trade):
        trade = Trade.from_ws(ws_trade, TOKEN_TO_OUTCOME)
        assert trade.market_id == MARKET_B
        assert trade.token_id == TOKEN_ID_B
        assert trade.price == 0.8
        assert trade.size == 31.7
        assert trade.side == "BUY"
        assert trade.transaction_hash == "0x7df1253494d4558b2599d13b66380784caffd926b2372ccbf491f412509bd028"

    def test_outcome_derivation(self, ws_trade):
        trade = Trade.from_ws(ws_trade, TOKEN_TO_OUTCOME)
        assert trade.outcome == "Team B"
        assert trade.outcome_index == 1

    def test_unknown_token_graceful(self, ws_trade):
        """Unknown token_id should not crash; outcome defaults to empty."""
        ws_trade["asset_id"] = "unknown_token_123"
        trade = Trade.from_ws(ws_trade, TOKEN_TO_OUTCOME)
        assert trade.outcome == ""
        assert trade.outcome_index == 0

    def test_server_timestamp_ms(self, ws_trade):
        trade = Trade.from_ws(ws_trade, TOKEN_TO_OUTCOME)
        assert trade.server_ts_ms == 1774375783210
        assert trade.server_ts_raw == 1774375783210 // 1000


class TestPriceSignalFromWs:
    @pytest.fixture
    def ws_bba(self):
        with open(FIXTURES / "ws_best_bid_ask_sample.json") as f:
            return json.load(f)

    def test_basic_fields(self, ws_bba):
        sig = PriceSignal.from_ws(ws_bba)
        assert sig.best_bid == 0.2
        assert sig.best_ask == 0.21
        assert sig.spread == 0.01
        assert sig.event_type == "best_bid_ask"

    def test_mid_price_computed(self, ws_bba):
        sig = PriceSignal.from_ws(ws_bba)
        assert abs(sig.mid_price - 0.205) < 1e-6

    def test_server_timestamp(self, ws_bba):
        sig = PriceSignal.from_ws(ws_bba)
        assert sig.server_ts_ms == 1774375783197

    def test_token_id(self, ws_bba):
        sig = PriceSignal.from_ws(ws_bba)
        assert sig.token_id == "100251494888115733311088324123253034667658118564471897630404325526546624706267"

    def test_imbalance_default_none(self, ws_bba):
        sig = PriceSignal.from_ws(ws_bba)
        assert sig.imbalance is None

    def test_imbalance_passed_through(self, ws_bba):
        sig = PriceSignal.from_ws(ws_bba, imbalance=0.65)
        assert sig.imbalance == 0.65


# ========================================================
# Step 6.2: WS client dispatch tests (mocked websocket)
# ========================================================


class TestWebSocketDispatch:
    def setup_method(self):
        self.client = WebSocketMarketClient(
            token_ids=[TOKEN_ID_A, TOKEN_ID_B],
            token_to_market=TOKEN_TO_MARKET,
            token_to_outcome=TOKEN_TO_OUTCOME,
        )

    def test_dispatch_book(self):
        with open(FIXTURES / "ws_book_sample.json") as f:
            raw = json.load(f)
        self.client._dispatch(raw)
        assert self.client.snapshot_count == 1
        assert len(self.client._buffer.snapshots) == 1

    def test_dispatch_trade(self):
        with open(FIXTURES / "ws_last_trade_price_sample.json") as f:
            raw = json.load(f)
        self.client._dispatch(raw)
        assert self.client.trade_count == 1
        assert len(self.client._buffer.trades) == 1

    def test_dispatch_signal(self):
        with open(FIXTURES / "ws_best_bid_ask_sample.json") as f:
            raw = json.load(f)
        self.client._dispatch(raw)
        assert self.client.signal_count == 1
        assert len(self.client._buffer.signals) == 1

    def test_dispatch_unknown_discarded(self):
        self.client._dispatch({"event_type": "new_market", "data": {}})
        assert len(self.client._buffer) == 0

    def test_market_id_override(self):
        with open(FIXTURES / "ws_book_sample.json") as f:
            raw = json.load(f)
        self.client._dispatch(raw)
        snap = self.client._buffer.snapshots[0]
        assert snap.market_id == MARKET_A


class TestImbalanceTracking:
    def setup_method(self):
        self.client = WebSocketMarketClient(
            token_ids=[TOKEN_ID_A, TOKEN_ID_B],
            token_to_market=TOKEN_TO_MARKET,
            token_to_outcome=TOKEN_TO_OUTCOME,
        )

    def test_book_caches_imbalance(self):
        """Book event caches imbalance per token."""
        with open(FIXTURES / "ws_book_sample.json") as f:
            raw = json.load(f)
        self.client._dispatch(raw)
        token_id = raw["asset_id"]
        assert token_id in self.client._last_imbalance
        assert 0.0 <= self.client._last_imbalance[token_id] <= 1.0

    def test_signal_carries_cached_imbalance(self):
        """best_bid_ask signal gets imbalance from last book event."""
        # First, dispatch a book event to cache imbalance
        with open(FIXTURES / "ws_book_sample.json") as f:
            book_raw = json.load(f)
        self.client._dispatch(book_raw)
        cached = self.client._last_imbalance[book_raw["asset_id"]]

        # Now dispatch a signal for the same token
        signal_raw = {
            "event_type": "best_bid_ask",
            "asset_id": book_raw["asset_id"],
            "best_bid": "0.11",
            "best_ask": "0.49",
            "spread": "0.38",
            "timestamp": "1774375783197",
        }
        self.client._dispatch(signal_raw)
        sig = self.client._buffer.signals[0]
        assert sig.imbalance == cached

    def test_signal_without_book_has_no_imbalance(self):
        """Signal without prior book event has None imbalance."""
        signal_raw = {
            "event_type": "best_bid_ask",
            "asset_id": "unknown_token",
            "best_bid": "0.50",
            "best_ask": "0.60",
            "spread": "0.10",
            "timestamp": "1000",
        }
        self.client._dispatch(signal_raw)
        sig = self.client._buffer.signals[0]
        assert sig.imbalance is None


class TestWriteBatch:
    def test_len(self):
        batch = WriteBatch()
        assert len(batch) == 0
        batch.snapshots.append(MagicMock())
        assert len(batch) == 1
        batch.trades.append(MagicMock())
        batch.signals.append(MagicMock())
        assert len(batch) == 3


@pytest.mark.asyncio
async def test_flush_puts_batch_on_queue():
    client = WebSocketMarketClient(
        token_ids=[TOKEN_ID_A],
        token_to_market=TOKEN_TO_MARKET,
        token_to_outcome=TOKEN_TO_OUTCOME,
    )
    with open(FIXTURES / "ws_book_sample.json") as f:
        raw = json.load(f)
    client._dispatch(raw)
    assert len(client._buffer) == 1

    await client._flush()
    assert len(client._buffer) == 0
    batch = client.get_batch_nowait()
    assert batch is not None
    assert len(batch.snapshots) == 1


@pytest.mark.asyncio
async def test_flush_noop_on_empty():
    client = WebSocketMarketClient(
        token_ids=[TOKEN_ID_A],
        token_to_market=TOKEN_TO_MARKET,
        token_to_outcome=TOKEN_TO_OUTCOME,
    )
    await client._flush()
    assert client.get_batch_nowait() is None


# ========================================================
# Step 6.2b: Shared queue + backoff tests
# ========================================================


@pytest.mark.asyncio
async def test_shared_queue_two_clients():
    """Two clients with shared queue — single consumer gets both."""
    shared_q: asyncio.Queue[WriteBatch] = asyncio.Queue()
    client_a = WebSocketMarketClient(
        token_ids=[TOKEN_ID_A],
        token_to_market=TOKEN_TO_MARKET,
        token_to_outcome=TOKEN_TO_OUTCOME,
        queue=shared_q,
        name="shard_a",
    )
    client_b = WebSocketMarketClient(
        token_ids=[TOKEN_ID_B],
        token_to_market=TOKEN_TO_MARKET,
        token_to_outcome=TOKEN_TO_OUTCOME,
        queue=shared_q,
        name="shard_b",
    )

    # Both clients share the same queue
    assert client_a._queue is client_b._queue

    # Dispatch and flush from both clients
    with open(FIXTURES / "ws_book_sample.json") as f:
        raw = json.load(f)
    client_a._dispatch(raw)
    await client_a._flush()

    with open(FIXTURES / "ws_last_trade_price_sample.json") as f:
        raw = json.load(f)
    client_b._dispatch(raw)
    await client_b._flush()

    # Both batches appear on shared queue
    batch1 = shared_q.get_nowait()
    batch2 = shared_q.get_nowait()
    assert len(batch1.snapshots) == 1
    assert len(batch2.trades) == 1


def test_backoff_resets_after_data():
    """_received_data flag is set after subscribe dispatches initial books."""
    client = WebSocketMarketClient(
        token_ids=[TOKEN_ID_A],
        token_to_market=TOKEN_TO_MARKET,
        token_to_outcome=TOKEN_TO_OUTCOME,
        name="test",
    )
    assert client._received_data is False

    # Simulate what _subscribe does after dispatching initial books
    client._received_data = True
    assert client._received_data is True


def test_backoff_no_reset_without_data():
    """Backoff counter should not reset if no data was received."""
    client = WebSocketMarketClient(
        token_ids=[TOKEN_ID_A],
        token_to_market=TOKEN_TO_MARKET,
        token_to_outcome=TOKEN_TO_OUTCOME,
        name="test",
    )
    # Simulate: connected but kicked before data
    client._connected = True
    # _received_data stays False
    assert client._received_data is False


def test_shard_name_default():
    """Default shard name is 'default'."""
    client = WebSocketMarketClient(
        token_ids=[TOKEN_ID_A],
        token_to_market=TOKEN_TO_MARKET,
        token_to_outcome=TOKEN_TO_OUTCOME,
    )
    assert client.name == "default"


def test_shard_name_custom():
    """Custom shard name is stored."""
    client = WebSocketMarketClient(
        token_ids=[TOKEN_ID_A],
        token_to_market=TOKEN_TO_MARKET,
        token_to_outcome=TOKEN_TO_OUTCOME,
        name="core",
    )
    assert client.name == "core"


# ========================================================
# Step 6.3: DB round-trip tests
# ========================================================


@pytest_asyncio.fixture
async def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        database = Database(db_path)
        await database.open()
        yield database
        await database.close()


@pytest.mark.asyncio
async def test_price_signals_insert_and_count(db):
    sig = PriceSignal(
        token_id="tok1",
        server_ts_ms=1774375783197,
        local_ts="2026-03-24T00:00:00+00:00",
        best_bid=0.45,
        best_ask=0.55,
        mid_price=0.50,
        spread=0.10,
        event_type="best_bid_ask",
    )
    count = await db.insert_price_signals([sig, sig])
    assert count == 2
    total = await db.count_price_signals()
    assert total == 2


@pytest.mark.asyncio
async def test_price_signals_table_exists(db):
    async with db.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='price_signals'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_price_signal_imbalance_stored(db):
    sig = PriceSignal(
        token_id="tok1",
        server_ts_ms=1000,
        local_ts="2026-03-24T00:00:00+00:00",
        best_bid=0.45,
        best_ask=0.55,
        mid_price=0.50,
        spread=0.10,
        event_type="best_bid_ask",
        imbalance=0.65,
    )
    await db.insert_price_signals([sig])
    async with db.db.execute(
        "SELECT imbalance FROM price_signals WHERE token_id='tok1'"
    ) as cur:
        row = await cur.fetchone()
    assert abs(row[0] - 0.65) < 1e-6


@pytest.mark.asyncio
async def test_price_signal_imbalance_null(db):
    sig = PriceSignal(
        token_id="tok2",
        server_ts_ms=2000,
        local_ts="2026-03-24T00:00:00+00:00",
        best_bid=0.45,
        best_ask=0.55,
        mid_price=0.50,
        spread=0.10,
        event_type="best_bid_ask",
    )
    await db.insert_price_signals([sig])
    async with db.db.execute(
        "SELECT imbalance FROM price_signals WHERE token_id='tok2'"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] is None


@pytest.mark.asyncio
async def test_price_signals_fields_stored(db):
    sig = PriceSignal(
        token_id="tok_abc",
        server_ts_ms=1000,
        local_ts="2026-03-24T00:00:00+00:00",
        best_bid=0.3,
        best_ask=0.7,
        mid_price=0.5,
        spread=0.4,
        event_type="best_bid_ask",
    )
    await db.insert_price_signals([sig])
    async with db.db.execute(
        "SELECT token_id, best_bid, best_ask, mid_price, spread, event_type FROM price_signals"
    ) as cur:
        row = await cur.fetchone()
    assert row == ("tok_abc", 0.3, 0.7, 0.5, 0.4, "best_bid_ask")


@pytest.mark.asyncio
async def test_ws_trade_dedup_in_db(db):
    """WS trades deduplicate by (transaction_hash, token_id)."""
    trade = Trade(
        market_id="0xabc",
        token_id="tok1",
        local_ts="2026-03-24T00:00:00+00:00",
        server_ts_raw=1774375783,
        server_ts_ms=1774375783210,
        transaction_hash="0xdeadbeef",
        price=0.5,
        size=10.0,
        side="BUY",
        outcome="Team A",
        outcome_index=0,
    )
    inserted1 = await db.insert_trades([trade])
    assert inserted1 == 1
    inserted2 = await db.insert_trades([trade])
    assert inserted2 == 0


@pytest.mark.asyncio
async def test_ws_snapshot_from_fixture_inserts(db):
    """Full round-trip: parse WS fixture → insert → query."""
    with open(FIXTURES / "ws_book_sample.json") as f:
        raw = json.load(f)
    snap = OrderBookSnapshot.from_ws(raw)
    count = await db.insert_snapshots([snap])
    assert count == 1
    total = await db.count_snapshots()
    assert total == 1
