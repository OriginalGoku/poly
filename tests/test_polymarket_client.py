"""Tests for Polymarket client: parsing, quality metrics, trade dedup."""

import json
from pathlib import Path

import pytest

from collector.models import OrderBookSnapshot, Trade

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def single_book():
    with open(FIXTURES / "polymarket_book_sample.json") as f:
        return json.load(f)


@pytest.fixture
def batch_books():
    with open(FIXTURES / "polymarket_books_batch_sample.json") as f:
        return json.load(f)


@pytest.fixture
def trades_data():
    with open(FIXTURES / "data_api_trades_sample.json") as f:
        return json.load(f)


class TestOrderBookParsing:
    def test_string_to_float_casting(self, single_book):
        """price and size are strings in API — must be cast to float."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=120.0)
        assert isinstance(snap.best_bid, float)
        assert isinstance(snap.best_ask, float)
        assert isinstance(snap.best_bid_size, float)
        assert isinstance(snap.best_ask_size, float)

    def test_bids_sorted_descending(self, single_book):
        """Best bid is highest price."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        bids = json.loads(snap.bid_depth_json)
        for i in range(len(bids) - 1):
            assert bids[i][0] >= bids[i + 1][0]

    def test_asks_sorted_ascending(self, single_book):
        """Best ask is lowest price."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        asks = json.loads(snap.ask_depth_json)
        for i in range(len(asks) - 1):
            assert asks[i][0] <= asks[i + 1][0]

    def test_spread_computation(self, single_book):
        """Spread = best_ask - best_bid."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        assert snap.spread is not None
        assert snap.spread == pytest.approx(snap.best_ask - snap.best_bid, abs=1e-6)

    def test_mid_price_computation(self, single_book):
        """Mid = (best_bid + best_ask) / 2."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        assert snap.mid_price is not None
        expected = (snap.best_bid + snap.best_ask) / 2
        assert snap.mid_price == pytest.approx(expected, abs=1e-6)

    def test_book_depth_usd(self, single_book):
        """book_depth_usd is positive for a non-empty book."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        assert snap.book_depth_usd > 0

    def test_is_empty_false(self, single_book):
        """Book with bids and asks is not empty."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        assert snap.is_empty is False

    def test_is_empty_true(self):
        """Book with no bids is empty."""
        raw = {
            "market": "0xtest",
            "asset_id": "123",
            "timestamp": "1774366505417",
            "bids": [],
            "asks": [{"price": "0.5", "size": "100"}],
            "last_trade_price": "0.5",
        }
        snap = OrderBookSnapshot.from_api(raw, fetch_latency_ms=100.0)
        assert snap.is_empty is True
        assert snap.best_bid is None
        assert snap.spread is None

    def test_timestamp_parsing(self, single_book):
        """server_ts_ms is parsed from ms epoch string."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        assert snap.server_ts_ms == 1774366505417
        assert snap.server_ts_raw == "1774366505417"

    def test_last_trade_price(self, single_book):
        """last_trade_price is parsed from string."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        assert snap.last_trade_price == 0.048

    def test_depth_limited_to_10_levels(self, single_book):
        """bid/ask depth JSON has at most 10 levels."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        bids = json.loads(snap.bid_depth_json)
        asks = json.loads(snap.ask_depth_json)
        assert len(bids) <= 10
        assert len(asks) <= 10

    def test_imbalance_computed(self, single_book):
        """Imbalance is computed from best bid/ask sizes."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        assert snap.imbalance is not None
        assert 0.0 <= snap.imbalance <= 1.0
        expected = snap.best_bid_size / (snap.best_bid_size + snap.best_ask_size)
        assert snap.imbalance == pytest.approx(expected, abs=1e-6)

    def test_imbalance_none_for_empty_book(self):
        """Imbalance is None when book has no bids."""
        raw = {
            "market": "0xtest", "asset_id": "123",
            "timestamp": "100", "bids": [],
            "asks": [{"price": "0.5", "size": "100"}],
        }
        snap = OrderBookSnapshot.from_api(raw, fetch_latency_ms=100.0)
        assert snap.imbalance is None

    def test_market_and_token_ids(self, single_book):
        """market_id and token_id are extracted."""
        snap = OrderBookSnapshot.from_api(single_book, fetch_latency_ms=100.0)
        assert snap.market_id == single_book["market"]
        assert snap.token_id == single_book["asset_id"]


class TestBatchBooks:
    def test_parse_batch(self, batch_books):
        """All books in batch are parseable."""
        snaps = [OrderBookSnapshot.from_api(b, fetch_latency_ms=115.0) for b in batch_books]
        assert len(snaps) == 3

    def test_different_tokens(self, batch_books):
        """Batch contains distinct token_ids."""
        snaps = [OrderBookSnapshot.from_api(b, fetch_latency_ms=115.0) for b in batch_books]
        token_ids = {s.token_id for s in snaps}
        assert len(token_ids) == 3

    def test_tick_size_varies(self, batch_books):
        """Different markets can have different tick_sizes."""
        tick_sizes = set()
        for b in batch_books:
            ts = float(b.get("tick_size", 0))
            tick_sizes.add(ts)
        # The fixture has both 0.001 and 0.01
        assert len(tick_sizes) >= 2


class TestTradeParsing:
    def test_parse_trade(self, trades_data):
        """Trade fields are parsed correctly."""
        trade = Trade.from_api(trades_data[0])
        assert trade.side == "SELL"
        assert trade.price == 0.999
        assert trade.size == 1334.17
        assert trade.server_ts_raw == 1774366423
        assert trade.server_ts_ms == 1774366423000
        assert trade.transaction_hash.startswith("0x")
        assert trade.outcome == "No"
        assert trade.outcome_index == 1

    def test_all_trades_parseable(self, trades_data):
        """All fixture trades parse without error."""
        trades = [Trade.from_api(t) for t in trades_data]
        assert len(trades) == 5

    def test_unique_transaction_hashes(self, trades_data):
        """Each trade has a unique transaction hash."""
        trades = [Trade.from_api(t) for t in trades_data]
        hashes = [t.transaction_hash for t in trades]
        assert len(set(hashes)) == len(hashes)

    def test_timestamp_normalization(self, trades_data):
        """server_ts_ms = timestamp * 1000."""
        for raw in trades_data:
            trade = Trade.from_api(raw)
            assert trade.server_ts_ms == trade.server_ts_raw * 1000
