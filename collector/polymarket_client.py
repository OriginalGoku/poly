"""Polymarket CLOB API (order books) and Data API (trades) client."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from .db import Database
from .models import OrderBookSnapshot, Trade, TradeWatermark

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"


class PolymarketClient:
    def __init__(
        self,
        db: Database,
        token_ids: list[str],
        token_to_market: dict[str, str],
        book_interval: float = 3.0,
        trade_interval: float = 15.0,
    ):
        self.db = db
        self.token_ids = token_ids
        self.token_to_market = token_to_market
        self.book_interval = book_interval
        self.trade_interval = trade_interval
        self._http: httpx.AsyncClient | None = None
        self._running = False

        # State for seconds_since_last_trade tracking
        self._prev_last_trade: dict[str, float | None] = {}
        self._prev_snapshot_ts: dict[str, float | None] = {}

        # Snapshot buffer
        self._snapshot_buffer: list[OrderBookSnapshot] = []
        self._last_flush_time: float = 0.0

        # Counters
        self.snapshot_count = 0
        self.trade_count = 0

        # Error tracking for gap detection
        self._book_error_start: float | None = None
        self._trade_error_start: float | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)
        self._running = True
        self._last_flush_time = time.time()

    async def close(self) -> None:
        self._running = False
        await self._flush_snapshots()
        if self._http:
            await self._http.aclose()

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Client not started")
        return self._http

    # --- Order book polling ---

    async def poll_books(self) -> None:
        while self._running:
            try:
                await self._fetch_books()
                self._book_error_start = None
            except Exception:
                logger.exception("Order book poll error")
                now = time.time()
                if self._book_error_start is None:
                    self._book_error_start = now
                elif now - self._book_error_start > 30:
                    await self.db.log_gap(
                        "polymarket",
                        datetime.fromtimestamp(self._book_error_start, tz=timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                        "order book polling down >30s",
                    )
                    self._book_error_start = now
                await asyncio.sleep(5)
                continue
            await asyncio.sleep(self.book_interval)

    async def _fetch_books(self) -> None:
        body = [{"token_id": tid} for tid in self.token_ids]
        t0 = time.monotonic()
        resp = await self.http.post(f"{CLOB_BASE}/books", json=body)
        latency_ms = (time.monotonic() - t0) * 1000
        resp.raise_for_status()
        books = resp.json()

        snapshots = []
        for raw in books:
            token_id = raw.get("asset_id", "")
            snap = OrderBookSnapshot.from_api(
                raw,
                fetch_latency_ms=latency_ms,
                prev_last_trade_price=self._prev_last_trade.get(token_id),
                prev_snapshot_ts=self._prev_snapshot_ts.get(token_id),
            )
            # Override market_id with our mapping if available
            if token_id in self.token_to_market:
                snap.market_id = self.token_to_market[token_id]
            snapshots.append(snap)
            self._prev_last_trade[token_id] = snap.last_trade_price
            self._prev_snapshot_ts[token_id] = time.time()

        self._snapshot_buffer.extend(snapshots)
        self.snapshot_count += len(snapshots)

        # Flush every 10 rows or 30 seconds
        now = time.time()
        if len(self._snapshot_buffer) >= 10 or (now - self._last_flush_time) >= 30:
            await self._flush_snapshots()

    async def _flush_snapshots(self) -> None:
        if not self._snapshot_buffer:
            return
        buf = self._snapshot_buffer
        self._snapshot_buffer = []
        self._last_flush_time = time.time()
        await self.db.insert_snapshots(buf)
        logger.debug("Flushed %d snapshots", len(buf))

    # --- Fetch market metadata ---

    async def fetch_market_metadata(self, token_id: str) -> dict:
        resp = await self.http.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()
        return {
            "tick_size": float(data.get("tick_size", 0)),
            "min_order_size": float(data.get("min_order_size", 0)),
        }

    # --- Trade polling ---

    async def poll_trades(self) -> None:
        while self._running:
            try:
                await self._fetch_trades()
                self._trade_error_start = None
            except Exception:
                logger.exception("Trade poll error")
                now = time.time()
                if self._trade_error_start is None:
                    self._trade_error_start = now
                elif now - self._trade_error_start > 30:
                    await self.db.log_gap(
                        "trades",
                        datetime.fromtimestamp(self._trade_error_start, tz=timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                        "trade polling down >30s",
                    )
                    self._trade_error_start = now
                await asyncio.sleep(5)
                continue
            await asyncio.sleep(self.trade_interval)

    async def _fetch_trades(self) -> None:
        for token_id in self.token_ids:
            try:
                await self._fetch_trades_for_token(token_id)
            except Exception:
                logger.exception("Trade fetch error for token %s", token_id[:16])
            # Rate limit: ~1 req/s to stay safely under Data API limits
            await asyncio.sleep(1.0)

    async def _fetch_trades_for_token(self, token_id: str) -> None:
        wm = await self.db.get_watermark(token_id)

        # Retry once on 429
        for attempt in range(2):
            resp = await self.http.get(
                f"{DATA_API_BASE}/trades",
                params={"asset_id": token_id, "limit": 100},
            )
            if resp.status_code == 429 and attempt == 0:
                await asyncio.sleep(2.0)
                continue
            resp.raise_for_status()
            break
        raw_trades = resp.json()

        if not raw_trades:
            return

        # Saturation detection: if we got exactly 100 trades, we may be missing some
        if len(raw_trades) >= 100:
            ts_values = [int(t.get("timestamp", 0)) for t in raw_trades]
            ts_min, ts_max = min(ts_values), max(ts_values)
            logger.warning(
                "Trade saturation: token %s returned %d trades (ts range %d-%d, span=%ds) — possible data loss",
                token_id[:16],
                len(raw_trades),
                ts_min,
                ts_max,
                ts_max - ts_min,
            )

        # Filter by watermark with overlap backfill (look back 60s to catch missed trades)
        new_trades: list[Trade] = []
        existing_hashes = set(wm.recent_hashes) if wm else set()
        min_ts = (wm.last_timestamp - 60) if wm else 0  # 60s overlap backfill

        for raw in raw_trades:
            ts = int(raw.get("timestamp", 0))
            tx_hash = raw.get("transactionHash", "")

            if ts < min_ts:
                continue
            if tx_hash in existing_hashes:
                continue

            trade = Trade.from_api(raw)
            if token_id in self.token_to_market:
                trade.market_id = self.token_to_market.get(trade.market_id, trade.market_id)
            new_trades.append(trade)

        inserted = await self.db.insert_trades(new_trades)
        self.trade_count += inserted

        # Update watermark
        if raw_trades:
            max_ts = max(int(t.get("timestamp", 0)) for t in raw_trades)
            recent = [
                t.get("transactionHash", "")
                for t in raw_trades
                if int(t.get("timestamp", 0)) == max_ts
            ]
            await self.db.set_watermark(
                TradeWatermark(
                    token_id=token_id,
                    last_timestamp=max_ts,
                    recent_hashes=recent,
                )
            )
