"""Polymarket CLOB API client — market metadata only.

REST trade and book polling removed after WS validation (2026-03-25):
WS captures 98.5-99.5% of configured-token trades; REST Data API
ignores asset_id param and returns event-wide noise.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"


class PolymarketClient:
    def __init__(
        self,
        token_ids: list[str],
        token_to_market: dict[str, str],
    ):
        self.token_ids = token_ids
        self.token_to_market = token_to_market
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Client not started")
        return self._http

    async def fetch_market_metadata(self, token_id: str) -> dict:
        resp = await self.http.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()
        return {
            "tick_size": float(data.get("tick_size", 0)),
            "min_order_size": float(data.get("min_order_size", 0)),
        }
