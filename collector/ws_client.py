"""WebSocket Market channel client for Polymarket order books, trades, and price signals."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import websockets
from websockets.exceptions import ConnectionClosed

from .models import OrderBookSnapshot, PriceSignal, Trade

logger = logging.getLogger(__name__)

WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

FLUSH_INTERVAL = 5.0  # seconds
FLUSH_BATCH_SIZE = 50
RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30]  # exponential backoff caps at 30s
GAP_THRESHOLD = 5.0  # log data gap if disconnected longer than this


@dataclass
class WriteBatch:
    """Collected data ready for DB insertion."""
    snapshots: list[OrderBookSnapshot] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    signals: list[PriceSignal] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.snapshots) + len(self.trades) + len(self.signals)


class WebSocketMarketClient:
    def __init__(
        self,
        token_ids: list[str],
        token_to_market: dict[str, str],
        token_to_outcome: dict[str, tuple[str, int]],
        queue: asyncio.Queue[WriteBatch] | None = None,
        name: str = "default",
    ):
        self.token_ids = token_ids
        self.token_to_market = token_to_market
        self.token_to_outcome = token_to_outcome
        self.name = name

        # Write queue for DB writer (shared if provided, else internal)
        self._queue: asyncio.Queue[WriteBatch] = queue if queue is not None else asyncio.Queue()

        # Internal buffer for batching
        self._buffer = WriteBatch()
        self._last_flush: float = 0.0

        # Last known imbalance per token (from book events)
        self._last_imbalance: dict[str, float] = {}

        # Counters
        self.snapshot_count = 0
        self.trade_count = 0
        self.signal_count = 0
        self.message_count = 0

        # Connection state
        self._ws: websockets.WebSocketClientProtocol | None = None  # type: ignore[name-defined]
        self._running = False
        self._connected = False
        self._received_data = False
        self._disconnect_ts: float | None = None

    async def run(self) -> None:
        """Main loop: connect, subscribe, receive, reconnect on failure."""
        self._running = True
        attempt = 0

        while self._running:
            try:
                await self._connect_and_receive()
                attempt = 0  # reset on clean disconnect
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                if not self._running:
                    break
                if self._received_data:
                    attempt = 0  # had a real session with data
                self._received_data = False
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                logger.warning("WS [%s] disconnected (%s), reconnecting in %ds...", self.name, e, delay)
                self._connected = False
                if self._disconnect_ts is None:
                    self._disconnect_ts = time.time()
                await asyncio.sleep(delay)
                attempt += 1
            except Exception:
                if not self._running:
                    break
                logger.exception("WS [%s] unexpected error", self.name)
                await asyncio.sleep(5)

        # Flush remaining buffer
        await self._flush()

    async def stop(self) -> None:
        """Signal the client to stop."""
        self._running = False
        if self._ws:
            await self._ws.close()

    async def get_batch(self) -> WriteBatch:
        """Block until a write batch is available."""
        return await self._queue.get()

    def get_batch_nowait(self) -> WriteBatch | None:
        """Non-blocking batch retrieval."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    # --- Connection lifecycle ---

    async def _connect_and_receive(self) -> None:
        async with websockets.connect(WS_MARKET_URL, ping_interval=30, ping_timeout=10) as ws:
            self._ws = ws
            self._connected = True
            logger.info("WS [%s] connected (%d tokens)", self.name, len(self.token_ids))

            # Check for data gap
            if self._disconnect_ts is not None:
                gap_duration = time.time() - self._disconnect_ts
                if gap_duration > GAP_THRESHOLD:
                    gap_start = datetime.fromtimestamp(
                        self._disconnect_ts, tz=timezone.utc
                    ).isoformat()
                    gap_end = datetime.now(timezone.utc).isoformat()
                    logger.warning(
                        "WS [%s] data gap: %.1fs (%s to %s)",
                        self.name, gap_duration, gap_start, gap_end,
                    )
                    gap_batch = WriteBatch()
                    gap_batch._gap = (gap_start, gap_end, f"WS [{self.name}] disconnected {gap_duration:.1f}s")  # type: ignore[attr-defined]
                    await self._queue.put(gap_batch)
                self._disconnect_ts = None

            # Subscribe
            await self._subscribe(ws)
            self._last_flush = time.time()

            await self._receive_loop(ws)

    async def _subscribe(self, ws: websockets.WebSocketClientProtocol) -> None:  # type: ignore[name-defined]
        sub_msg = {
            "assets_ids": self.token_ids,
            "type": "market",
            "custom_feature_enabled": True,
        }
        await ws.send(json.dumps(sub_msg))
        logger.info("WS [%s] subscribed to %d tokens", self.name, len(self.token_ids))

        # The first response is a JSON array of book snapshots for all tokens
        raw = await asyncio.wait_for(ws.recv(), timeout=30)

        initial_books = json.loads(raw)
        if isinstance(initial_books, list):
            for book_raw in initial_books:
                self._handle_book(book_raw)
            logger.info("WS [%s] initial book snapshots: %d", self.name, len(initial_books))
        else:
            # Single message, dispatch normally
            self._dispatch(initial_books)
        self._received_data = True

    async def _receive_loop(self, ws: websockets.WebSocketClientProtocol) -> None:  # type: ignore[name-defined]
        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
            except asyncio.TimeoutError:
                logger.warning("WS [%s] no message in 60s, forcing reconnect", self.name)
                return

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("Non-JSON WS message: %s", raw[:100])
                continue

            if isinstance(data, list):
                for item in data:
                    self._dispatch(item)
            else:
                self._dispatch(data)

            self.message_count += 1

            # Periodic flush
            now = time.time()
            if len(self._buffer) >= FLUSH_BATCH_SIZE or (now - self._last_flush) >= FLUSH_INTERVAL:
                await self._flush()

    # --- Message dispatch ---

    def _dispatch(self, data: dict) -> None:
        event_type = data.get("event_type", "")
        if event_type == "book":
            self._handle_book(data)
        elif event_type == "last_trade_price":
            self._handle_trade(data)
        elif event_type == "best_bid_ask":
            self._handle_signal(data)
        else:
            logger.debug("Discarding WS event: %s", event_type)

    def _handle_book(self, raw: dict) -> None:
        snap = OrderBookSnapshot.from_ws(raw)
        # Override market_id from our config mapping if available
        if snap.token_id in self.token_to_market:
            snap.market_id = self.token_to_market[snap.token_id]
        # Cache imbalance for carrying forward to price signals
        if snap.imbalance is not None:
            self._last_imbalance[snap.token_id] = snap.imbalance
        self._buffer.snapshots.append(snap)
        self.snapshot_count += 1

    def _handle_trade(self, raw: dict) -> None:
        trade = Trade.from_ws(raw, self.token_to_outcome)
        # Override market_id from our config mapping if available
        if trade.token_id in self.token_to_market:
            trade.market_id = self.token_to_market[trade.token_id]
        self._buffer.trades.append(trade)
        self.trade_count += 1

    def _handle_signal(self, raw: dict) -> None:
        token_id = raw.get("asset_id", "")
        imbalance = self._last_imbalance.get(token_id)
        signal = PriceSignal.from_ws(raw, imbalance=imbalance)
        self._buffer.signals.append(signal)
        self.signal_count += 1

    # --- Flush ---

    async def _flush(self) -> None:
        if len(self._buffer) == 0:
            return
        batch = self._buffer
        self._buffer = WriteBatch()
        self._last_flush = time.time()
        await self._queue.put(batch)
        logger.debug(
            "Queued batch: %d snaps, %d trades, %d signals",
            len(batch.snapshots), len(batch.trades), len(batch.signals),
        )
