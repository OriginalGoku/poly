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
    is_empty: bool
    last_trade_price: float | None
    seconds_since_last_trade: float | None

    @classmethod
    def from_api(
        cls,
        raw: dict,
        fetch_latency_ms: float,
        prev_last_trade_price: float | None = None,
        prev_snapshot_ts: float | None = None,
    ) -> OrderBookSnapshot:
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
            is_empty=is_empty,
            last_trade_price=last_trade_price,
            seconds_since_last_trade=seconds_since_last_trade,
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

    @classmethod
    def from_api(cls, raw: dict) -> Trade:
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
class TradeWatermark:
    token_id: str
    last_timestamp: int  # seconds epoch
    recent_hashes: list[str] = field(default_factory=list)
