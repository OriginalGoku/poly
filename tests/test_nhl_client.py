"""Tests for NHL game-state client timestamp handling."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from collector.game_state.nhl_client import NhlClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def nhl_pbp():
    with open(FIXTURES / "nhl_pbp_sample.json") as f:
        return json.load(f)


class TestNhlClient:
    @pytest_asyncio.fixture
    async def nhl_client(self):
        client = NhlClient(
            match_id="test-nhl-1",
            game_id="2025020100",
            team1="VAN",
            team2="EDM",
        )
        client._http = AsyncMock()
        yield client

    def _mock_response(self, data):
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status = MagicMock()
        return resp

    @pytest.mark.asyncio
    async def test_distinct_server_ts_ms_in_batch(self, nhl_client, nhl_pbp):
        """Multiple events from a single poll() get distinct server_ts_ms values."""
        nhl_client._http.get = AsyncMock(return_value=self._mock_response(nhl_pbp))

        events = await nhl_client.poll()
        assert len(events) >= 2

        ts_values = [e.server_ts_ms for e in events]
        assert len(ts_values) == len(set(ts_values)), (
            f"Duplicate server_ts_ms values found: {ts_values}"
        )

    @pytest.mark.asyncio
    async def test_server_ts_ms_monotonically_increasing(self, nhl_client, nhl_pbp):
        """server_ts_ms values are monotonically increasing with sortOrder."""
        nhl_client._http.get = AsyncMock(return_value=self._mock_response(nhl_pbp))

        events = await nhl_client.poll()
        ts_values = [e.server_ts_ms for e in events]
        for i in range(1, len(ts_values)):
            assert ts_values[i] > ts_values[i - 1], (
                f"server_ts_ms not increasing: {ts_values[i-1]} >= {ts_values[i]}"
            )

    @pytest.mark.asyncio
    async def test_timestamp_quality_is_local(self, nhl_client, nhl_pbp):
        """All NHL events have timestamp_quality set to 'local'."""
        nhl_client._http.get = AsyncMock(return_value=self._mock_response(nhl_pbp))

        events = await nhl_client.poll()
        for e in events:
            assert e.timestamp_quality == "local"

    @pytest.mark.asyncio
    async def test_no_duplicate_events_on_repoll(self, nhl_client, nhl_pbp):
        """Second poll with same data returns no new events."""
        nhl_client._http.get = AsyncMock(return_value=self._mock_response(nhl_pbp))

        events1 = await nhl_client.poll()
        assert len(events1) > 0

        events2 = await nhl_client.poll()
        assert len(events2) == 0

    @pytest.mark.asyncio
    async def test_detects_goals(self, nhl_client, nhl_pbp):
        """Goals are detected as score_change events with correct scores."""
        nhl_client._http.get = AsyncMock(return_value=self._mock_response(nhl_pbp))

        events = await nhl_client.poll()
        goals = [e for e in events if e.event_type == "score_change"]
        assert len(goals) == 3

        # First goal: away scores (1-0)
        assert goals[0].team1_score == 1
        assert goals[0].team2_score == 0

    @pytest.mark.asyncio
    async def test_detects_penalties(self, nhl_client, nhl_pbp):
        """Penalties are detected."""
        nhl_client._http.get = AsyncMock(return_value=self._mock_response(nhl_pbp))

        events = await nhl_client.poll()
        penalties = [e for e in events if e.event_type == "timeout"]
        assert len(penalties) == 1

    @pytest.mark.asyncio
    async def test_detects_game_end(self, nhl_client, nhl_pbp):
        """Game end is detected and stops further polling."""
        nhl_client._http.get = AsyncMock(return_value=self._mock_response(nhl_pbp))

        events = await nhl_client.poll()
        end_events = [e for e in events if e.event_type == "game_end"]
        assert len(end_events) == 1
        assert nhl_client._game_ended is True

        # No more events after game end
        events2 = await nhl_client.poll()
        assert len(events2) == 0

    @pytest.mark.asyncio
    async def test_server_ts_raw_has_period_clock(self, nhl_client, nhl_pbp):
        """All events have period clock info in server_ts_raw."""
        nhl_client._http.get = AsyncMock(return_value=self._mock_response(nhl_pbp))

        events = await nhl_client.poll()
        for e in events:
            assert e.server_ts_raw.startswith("P"), (
                f"server_ts_raw should start with period: {e.server_ts_raw}"
            )

    @pytest.mark.asyncio
    async def test_poll_interval_is_5s(self, nhl_client):
        """Poll interval should be 5 seconds (reduced from 10)."""
        assert nhl_client.poll_interval_seconds == 5.0

    @pytest.mark.asyncio
    async def test_period_end_half_end_at_p2(self, nhl_client, nhl_pbp):
        """Period 2 end in REG is detected as half_end."""
        nhl_client._http.get = AsyncMock(return_value=self._mock_response(nhl_pbp))

        events = await nhl_client.poll()
        half_ends = [e for e in events if e.event_type == "half_end"]
        assert len(half_ends) == 1
        assert half_ends[0].quarter == 2
