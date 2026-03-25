"""Dataclasses for parsed API responses and internal state."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class OrderLevel:
    price: float
    size: float


@dataclass
class OrderBookSnapshot:
    market_id: str
    token_id: str
    local_ts: str  # ISO 8601 UTC
    local_mono_ns: int
    server_ts_raw: str  # ms epoch string from API
    server_ts_ms: int  # normalized ms epoch
    fetch_latency_ms: float
    best_bid: float | None
    best_bid_size: float | None
    best_ask: float | None
    best_ask_size: float | None
    mid_price: float | None
    spread: float | None
    bid_depth_json: str  # JSON [[price, size], ...]
    ask_depth_json: str
    book_depth_usd: float
    inside_liquidity_usd: float
    is_empty: bool
    last_trade_price: float | None
    seconds_since_last_trade: float | None
    imbalance: float | None = None

    @classmethod
    def from_api(
        cls,
        raw: dict,
        fetch_latency_ms: float,
        prev_last_trade_price: float | None = None,
        prev_snapshot_ts: float | None = None,
    ) -> OrderBookSnapshot:
        """Legacy/validation-only: used by validate_polymarket.py and tests.
        WS collection uses from_ws() exclusively since 2026-03-25."""
        now = datetime.now(timezone.utc)
        mono_ns = time.monotonic_ns()

        bids = [(float(b["price"]), float(b["size"])) for b in raw.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in raw.get("asks", [])]

        # Sort: bids descending, asks ascending
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        is_empty = len(bids) == 0 or len(asks) == 0
        best_bid = bids[0][0] if bids else None
        best_bid_size = bids[0][1] if bids else None
        best_ask = asks[0][0] if asks else None
        best_ask_size = asks[0][1] if asks else None

        if best_bid is not None and best_ask is not None:
            mid_price = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
        else:
            mid_price = None
            spread = None

        # Book depth: total $ within 5% of mid on both sides
        book_depth_usd = 0.0
        if mid_price and mid_price > 0:
            low = mid_price * 0.95
            high = mid_price * 1.05
            for p, s in bids:
                if p >= low:
                    book_depth_usd += p * s
            for p, s in asks:
                if p <= high:
                    book_depth_usd += p * s

        # Inside liquidity: notional at best bid + best ask (always available if book isn't empty)
        inside_liquidity_usd = 0.0
        if best_bid is not None and best_bid_size is not None:
            inside_liquidity_usd += best_bid * best_bid_size
        if best_ask is not None and best_ask_size is not None:
            inside_liquidity_usd += best_ask * best_ask_size

        last_trade_price_raw = raw.get("last_trade_price")
        last_trade_price = float(last_trade_price_raw) if last_trade_price_raw else None

        seconds_since_last_trade = None
        if (
            prev_last_trade_price is not None
            and last_trade_price is not None
            and last_trade_price != prev_last_trade_price
            and prev_snapshot_ts is not None
        ):
            seconds_since_last_trade = 0.0  # trade happened between polls
        # If price hasn't changed and we have a prev time, accumulate
        elif prev_snapshot_ts is not None and last_trade_price == prev_last_trade_price:
            seconds_since_last_trade = time.time() - prev_snapshot_ts

        # Order book imbalance: best_bid_size / (best_bid_size + best_ask_size)
        imbalance = None
        if best_bid_size is not None and best_ask_size is not None:
            total = best_bid_size + best_ask_size
            if total > 0:
                imbalance = round(best_bid_size / total, 6)

        server_ts_raw = str(raw.get("timestamp", ""))
        try:
            server_ts_ms = int(server_ts_raw)
        except (ValueError, TypeError):
            server_ts_ms = int(now.timestamp() * 1000)

        return cls(
            market_id=raw.get("market", ""),
            token_id=raw.get("asset_id", ""),
            local_ts=now.isoformat(),
            local_mono_ns=mono_ns,
            server_ts_raw=server_ts_raw,
            server_ts_ms=server_ts_ms,
            fetch_latency_ms=fetch_latency_ms,
            best_bid=best_bid,
            best_bid_size=best_bid_size,
            best_ask=best_ask,
            best_ask_size=best_ask_size,
            mid_price=mid_price,
            spread=spread,
            bid_depth_json=json.dumps(bids[:10]),
            ask_depth_json=json.dumps(asks[:10]),
            book_depth_usd=round(book_depth_usd, 2),
            inside_liquidity_usd=round(inside_liquidity_usd, 2),
            is_empty=is_empty,
            last_trade_price=last_trade_price,
            seconds_since_last_trade=seconds_since_last_trade,
            imbalance=imbalance,
        )

    @classmethod
    def from_ws(cls, raw: dict) -> OrderBookSnapshot:
        """Parse a WS book event into an OrderBookSnapshot."""
        now = datetime.now(timezone.utc)
        mono_ns = time.monotonic_ns()

        bids = [(float(b["price"]), float(b["size"])) for b in raw.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in raw.get("asks", [])]
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        is_empty = len(bids) == 0 or len(asks) == 0
        best_bid = bids[0][0] if bids else None
        best_bid_size = bids[0][1] if bids else None
        best_ask = asks[0][0] if asks else None
        best_ask_size = asks[0][1] if asks else None

        if best_bid is not None and best_ask is not None:
            mid_price = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
        else:
            mid_price = None
            spread = None

        book_depth_usd = 0.0
        if mid_price and mid_price > 0:
            low = mid_price * 0.95
            high = mid_price * 1.05
            for p, s in bids:
                if p >= low:
                    book_depth_usd += p * s
            for p, s in asks:
                if p <= high:
                    book_depth_usd += p * s

        inside_liquidity_usd = 0.0
        if best_bid is not None and best_bid_size is not None:
            inside_liquidity_usd += best_bid * best_bid_size
        if best_ask is not None and best_ask_size is not None:
            inside_liquidity_usd += best_ask * best_ask_size

        last_trade_price_raw = raw.get("last_trade_price")
        last_trade_price = float(last_trade_price_raw) if last_trade_price_raw else None

        # Order book imbalance: best_bid_size / (best_bid_size + best_ask_size)
        imbalance = None
        if best_bid_size is not None and best_ask_size is not None:
            total = best_bid_size + best_ask_size
            if total > 0:
                imbalance = round(best_bid_size / total, 6)

        server_ts_raw = str(raw.get("timestamp", ""))
        try:
            server_ts_ms = int(server_ts_raw)
        except (ValueError, TypeError):
            server_ts_ms = int(now.timestamp() * 1000)

        return cls(
            market_id=raw.get("market", ""),
            token_id=raw.get("asset_id", ""),
            local_ts=now.isoformat(),
            local_mono_ns=mono_ns,
            server_ts_raw=server_ts_raw,
            server_ts_ms=server_ts_ms,
            fetch_latency_ms=0.0,  # no HTTP round-trip
            best_bid=best_bid,
            best_bid_size=best_bid_size,
            best_ask=best_ask,
            best_ask_size=best_ask_size,
            mid_price=mid_price,
            spread=spread,
            bid_depth_json=json.dumps(bids[:10]),
            ask_depth_json=json.dumps(asks[:10]),
            book_depth_usd=round(book_depth_usd, 2),
            inside_liquidity_usd=round(inside_liquidity_usd, 2),
            is_empty=is_empty,
            last_trade_price=last_trade_price,
            seconds_since_last_trade=None,  # not tracked for WS
            imbalance=imbalance,
        )


@dataclass
class Trade:
    market_id: str  # conditionId
    token_id: str  # asset
    local_ts: str
    server_ts_raw: int  # seconds epoch
    server_ts_ms: int  # ms epoch
    transaction_hash: str
    price: float
    size: float
    side: str
    outcome: str
    outcome_index: int
    source: str = "rest"  # 'rest' or 'ws' — for dual-write validation

    @classmethod
    def from_api(cls, raw: dict) -> Trade:
        """Legacy/validation-only: used by validate_polymarket.py and tests.
        WS collection uses from_ws() exclusively since 2026-03-25."""
        ts = int(raw.get("timestamp", 0))
        return cls(
            market_id=raw.get("conditionId", ""),
            token_id=raw.get("asset", ""),
            local_ts=datetime.now(timezone.utc).isoformat(),
            server_ts_raw=ts,
            server_ts_ms=ts * 1000,
            transaction_hash=raw.get("transactionHash", ""),
            price=float(raw.get("price", 0)),
            size=float(raw.get("size", 0)),
            side=raw.get("side", ""),
            outcome=raw.get("outcome", ""),
            outcome_index=int(raw.get("outcomeIndex", 0)),
        )

    @classmethod
    def from_ws(
        cls,
        raw: dict,
        token_to_outcome: dict[str, tuple[str, int]],
    ) -> Trade:
        """Parse a WS last_trade_price event into a Trade."""
        asset_id = raw.get("asset_id", "")
        ts_ms = int(raw.get("timestamp", 0))
        outcome, outcome_index = token_to_outcome.get(asset_id, (None, -1))
        if outcome is None:
            import logging
            logging.getLogger(__name__).error(
                "Unknown token_id in WS trade: %s", asset_id[:16]
            )
        return cls(
            market_id=raw.get("market", ""),
            token_id=asset_id,
            local_ts=datetime.now(timezone.utc).isoformat(),
            server_ts_raw=ts_ms // 1000,
            server_ts_ms=ts_ms,
            transaction_hash=raw.get("transaction_hash", ""),
            price=float(raw.get("price", 0)),
            size=float(raw.get("size", 0)),
            side=raw.get("side", ""),
            outcome=outcome or "",
            outcome_index=outcome_index if outcome_index >= 0 else 0,
            source="ws",
        )


@dataclass
class MatchEvent:
    match_id: str
    local_ts: str
    server_ts_raw: str
    server_ts_ms: int
    sport: str
    event_type: str
    map_number: int | None = None
    map_name: str | None = None
    round_number: int | None = None
    game_number: int | None = None
    quarter: int | None = None
    team1_score: int | None = None
    team2_score: int | None = None
    event_team: str | None = None
    ct_team: str | None = None
    gold_lead: int | None = None
    building_state: int | None = None
    timestamp_quality: str = "server"  # "server" or "local"
    raw_event_json: str = ""


@dataclass
class MarketConfig:
    market_id: str
    question: str
    relationship: str
    outcomes: list[str]
    token_ids: list[str]


@dataclass
class MatchConfig:
    match_id: str
    sport: str
    team1: str
    team2: str
    tournament: str
    best_of: int | None
    scheduled_start: str
    data_source: str
    markets: list[MarketConfig]
    external_id: str = ""
    polymarket_event_slug: str = ""
    polymarket_volume: float = 0.0


@dataclass
class PriceSignal:
    token_id: str
    server_ts_ms: int
    local_ts: str
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float
    event_type: str
    imbalance: float | None = None

    @classmethod
    def from_ws(cls, raw: dict, imbalance: float | None = None) -> PriceSignal:
        """Parse a WS best_bid_ask event into a PriceSignal."""
        best_bid = float(raw.get("best_bid", 0))
        best_ask = float(raw.get("best_ask", 0))
        spread = float(raw.get("spread", 0))
        mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
        ts_ms = int(raw.get("timestamp", 0))
        return cls(
            token_id=raw.get("asset_id", ""),
            server_ts_ms=ts_ms,
            local_ts=datetime.now(timezone.utc).isoformat(),
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=round(mid_price, 6),
            spread=spread,
            event_type=raw.get("event_type", "best_bid_ask"),
            imbalance=imbalance,
        )


@dataclass
class TradeWatermark:
    token_id: str
    last_timestamp: int  # seconds epoch
    recent_hashes: list[str] = field(default_factory=list)
