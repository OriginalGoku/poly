"""Abstract base class for game-state clients."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import MatchEvent


class GameNotStarted(Exception):
    """Raised when the game-state API returns 403/404 because the game hasn't started."""


class GameStateClient(ABC):
    sport: str
    poll_interval_seconds: float

    @abstractmethod
    async def poll(self) -> list[MatchEvent]:
        """Poll API, return new events since last poll."""

    @abstractmethod
    async def close(self) -> None:
        """Cleanup."""
