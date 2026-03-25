"""Tests for delayed game-state polling: settings, GameNotStarted, and three-state poller."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio

from collector.game_state.base import GameNotStarted, GameStateClient
from collector.game_state.nba_client import NbaClient
from collector.game_state.nhl_client import NhlClient
from collector.settings import get_game_state_poll_lead_minutes


# ── Settings tests ────────────────────────────────────────────────


class TestSettings:
    def test_valid_settings(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "game_state_poll_lead_minutes": {"value": 15, "description": "test"}
        }))
        with patch("collector.settings._SETTINGS_PATH", settings_file):
            import importlib
            import collector.settings as mod
            # Reload to pick up patched path
            mod._settings = json.loads(settings_file.read_text())
            assert mod.get_game_state_poll_lead_minutes() == 15

    def test_missing_key_returns_default(self):
        with patch.dict("collector.settings._settings", {}, clear=True):
            assert get_game_state_poll_lead_minutes() == 30

    def test_malformed_value_returns_default(self):
        with patch.dict("collector.settings._settings", {
            "game_state_poll_lead_minutes": {"value": "not_a_number"}
        }):
            assert get_game_state_poll_lead_minutes() == 30

    def test_missing_file_returns_default(self):
        with patch.dict("collector.settings._settings", {}, clear=True):
            assert get_game_state_poll_lead_minutes() == 30


# ── GameNotStarted exception tests ───────────────────────────────


class TestGameNotStarted:
    @pytest_asyncio.fixture
    async def nba_client(self):
        client = NbaClient(
            match_id="test-nba-1",
            game_id="0022501038",
            team1="DET",
            team2="ATL",
        )
        await client.start()
        yield client
        await client.close()

    @pytest_asyncio.fixture
    async def nhl_client(self):
        client = NhlClient(
            match_id="test-nhl-1",
            game_id="2025020001",
            team1="VAN",
            team2="EDM",
        )
        await client.start()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_nba_raises_game_not_started_on_403(self, nba_client):
        mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://test"))
        nba_client._http = AsyncMock()
        nba_client._http.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "403", request=mock_resp.request, response=mock_resp
            )
        )
        with pytest.raises(GameNotStarted):
            await nba_client.poll()

    @pytest.mark.asyncio
    async def test_nba_raises_game_not_started_on_404(self, nba_client):
        mock_resp = httpx.Response(404, request=httpx.Request("GET", "http://test"))
        nba_client._http = AsyncMock()
        nba_client._http.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "404", request=mock_resp.request, response=mock_resp
            )
        )
        with pytest.raises(GameNotStarted):
            await nba_client.poll()

    @pytest.mark.asyncio
    async def test_nba_returns_empty_on_500(self, nba_client):
        mock_resp = httpx.Response(500, request=httpx.Request("GET", "http://test"))
        nba_client._http = AsyncMock()
        nba_client._http.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "500", request=mock_resp.request, response=mock_resp
            )
        )
        result = await nba_client.poll()
        assert result == []

    @pytest.mark.asyncio
    async def test_nhl_raises_game_not_started_on_403(self, nhl_client):
        mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://test"))
        nhl_client._http = AsyncMock()
        nhl_client._http.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "403", request=mock_resp.request, response=mock_resp
            )
        )
        with pytest.raises(GameNotStarted):
            await nhl_client.poll()

    @pytest.mark.asyncio
    async def test_nhl_raises_game_not_started_on_404(self, nhl_client):
        mock_resp = httpx.Response(404, request=httpx.Request("GET", "http://test"))
        nhl_client._http = AsyncMock()
        nhl_client._http.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "404", request=mock_resp.request, response=mock_resp
            )
        )
        with pytest.raises(GameNotStarted):
            await nhl_client.poll()

    @pytest.mark.asyncio
    async def test_nhl_returns_empty_on_500(self, nhl_client):
        mock_resp = httpx.Response(500, request=httpx.Request("GET", "http://test"))
        nhl_client._http = AsyncMock()
        nhl_client._http.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "500", request=mock_resp.request, response=mock_resp
            )
        )
        result = await nhl_client.poll()
        assert result == []


# ── Schedule parsing tests ───────────────────────────────────────


class TestScheduleParsing:
    """Test the schedule parsing logic used in run_game_state_poller."""

    def _parse_scheduled_start(self, s: str) -> datetime | None:
        """Mirror the parsing logic from run_game_state_poller."""
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    def test_empty_string(self):
        assert self._parse_scheduled_start("") is None

    def test_z_suffix(self):
        dt = self._parse_scheduled_start("2026-03-25T18:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_offset_suffix(self):
        dt = self._parse_scheduled_start("2026-03-25T18:00:00+00:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_malformed(self):
        assert self._parse_scheduled_start("not-a-date") is None

    def test_future_time(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        dt = self._parse_scheduled_start(future)
        assert dt is not None
        assert dt > datetime.now(timezone.utc)

    def test_past_time(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        dt = self._parse_scheduled_start(past)
        assert dt is not None
        assert dt < datetime.now(timezone.utc)


# ── Three-state poller tests ────────────────────────────────────


class MockGameClient(GameStateClient):
    """Mock game client that raises GameNotStarted N times then returns events."""

    sport = "test"
    poll_interval_seconds = 0.01

    def __init__(self, not_started_count: int = 0):
        self._not_started_count = not_started_count
        self._poll_count = 0

    async def poll(self):
        self._poll_count += 1
        if self._not_started_count > 0:
            self._not_started_count -= 1
            raise GameNotStarted("not started")
        return []

    async def close(self):
        pass


class TestThreeStatePoller:
    @pytest.mark.asyncio
    async def test_immediate_live_no_scheduled_start(self):
        """No scheduled_start → skip WAITING, BACKOFF succeeds first try → LIVE."""
        from collector.__main__ import run_game_state_poller

        client = MockGameClient(not_started_count=0)
        db = AsyncMock()
        db.insert_match_events = AsyncMock(return_value=0)

        # Run poller briefly then cancel
        with patch("collector.settings.get_game_state_poll_lead_minutes", return_value=30):
            task = asyncio.create_task(run_game_state_poller(client, db, ""))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert client._poll_count >= 1

    @pytest.mark.asyncio
    async def test_backoff_then_live(self):
        """GameNotStarted raised twice, then success → transitions to LIVE."""
        from collector.__main__ import run_game_state_poller

        client = MockGameClient(not_started_count=2)
        db = AsyncMock()
        db.insert_match_events = AsyncMock(return_value=0)

        original_sleep = asyncio.sleep

        async def mock_sleep(seconds, *args, **kwargs):
            # Skip long sleeps (backoff), keep short ones (LIVE polling)
            if seconds > 1:
                return
            await original_sleep(min(seconds, 0.01))

        with patch("collector.settings.get_game_state_poll_lead_minutes", return_value=30), \
             patch("asyncio.sleep", side_effect=mock_sleep):
            task = asyncio.create_task(run_game_state_poller(client, db, ""))
            await original_sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 2 GameNotStarted + 1 success in backoff + at least 1 in LIVE
        assert client._poll_count >= 3

    @pytest.mark.asyncio
    async def test_past_scheduled_start_skips_waiting(self):
        """Past scheduled_start → skip WAITING, go to BACKOFF."""
        from collector.__main__ import run_game_state_poller

        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        client = MockGameClient(not_started_count=0)
        db = AsyncMock()
        db.insert_match_events = AsyncMock(return_value=0)

        with patch("collector.settings.get_game_state_poll_lead_minutes", return_value=30):
            task = asyncio.create_task(run_game_state_poller(client, db, past))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert client._poll_count >= 1

    @pytest.mark.asyncio
    async def test_future_scheduled_start_waits(self):
        """Future scheduled_start → WAITING state sleeps."""
        from collector.__main__ import run_game_state_poller

        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        client = MockGameClient(not_started_count=0)
        db = AsyncMock()
        db.insert_match_events = AsyncMock(return_value=0)

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(seconds, *args, **kwargs):
            sleep_calls.append(seconds)
            # Don't actually sleep long
            if seconds > 1:
                return
            await original_sleep(seconds)

        with patch("collector.settings.get_game_state_poll_lead_minutes", return_value=30), \
             patch("asyncio.sleep", side_effect=mock_sleep):
            task = asyncio.create_task(run_game_state_poller(client, db, future))
            await original_sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # First sleep should be the WAITING delay (roughly 90 min = 5400s)
        assert len(sleep_calls) >= 1
        assert sleep_calls[0] > 3600  # at least 1 hour of waiting

    @pytest.mark.asyncio
    async def test_unparseable_scheduled_start_skips_waiting(self):
        """Unparseable scheduled_start → skip WAITING."""
        from collector.__main__ import run_game_state_poller

        client = MockGameClient(not_started_count=0)
        db = AsyncMock()
        db.insert_match_events = AsyncMock(return_value=0)

        with patch("collector.settings.get_game_state_poll_lead_minutes", return_value=30):
            task = asyncio.create_task(
                run_game_state_poller(client, db, "not-a-date")
            )
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert client._poll_count >= 1
