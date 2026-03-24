"""Tests for game-state clients: NBA CDN and OpenDota/Dota2."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from collector.game_state.dota2_client import Dota2Client
from collector.game_state.nba_client import NbaClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def nba_pbp():
    with open(FIXTURES / "nba_pbp_sample.json") as f:
        return json.load(f)


@pytest.fixture
def nba_scoreboard():
    with open(FIXTURES / "nba_scoreboard_sample.json") as f:
        return json.load(f)


@pytest.fixture
def opendota_live():
    with open(FIXTURES / "opendota_live_sample.json") as f:
        return json.load(f)


class TestNbaClient:
    @pytest_asyncio.fixture
    async def nba_client(self):
        client = NbaClient(
            match_id="test-nba-1",
            game_id="0022501038",
            team1="DET",
            team2="LAL",
        )
        # Mock HTTP client
        client._http = AsyncMock()
        yield client

    @pytest.mark.asyncio
    async def test_detects_score_changes(self, nba_client, nba_pbp):
        """Score changes are detected from made shots and free throws."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"game": {"actions": nba_pbp}}
        mock_resp.raise_for_status = MagicMock()
        nba_client._http.get = AsyncMock(return_value=mock_resp)

        events = await nba_client.poll()

        score_events = [e for e in events if e.event_type == "score_change"]
        assert len(score_events) > 0

        # First score change should be the free throw (0→1)
        first_score = score_events[0]
        assert first_score.sport == "nba"
        assert first_score.team2_score > 0 or first_score.team1_score > 0

    @pytest.mark.asyncio
    async def test_score_change_has_team(self, nba_client, nba_pbp):
        """Score change events have event_team set."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"game": {"actions": nba_pbp}}
        mock_resp.raise_for_status = MagicMock()
        nba_client._http.get = AsyncMock(return_value=mock_resp)

        events = await nba_client.poll()
        score_events = [e for e in events if e.event_type == "score_change"]
        for e in score_events:
            assert e.event_team in ("ATL", "MEM", "DET", "LAL", "")

    @pytest.mark.asyncio
    async def test_server_ts_ms_populated(self, nba_client, nba_pbp):
        """All events have server_ts_ms set from timeActual."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"game": {"actions": nba_pbp}}
        mock_resp.raise_for_status = MagicMock()
        nba_client._http.get = AsyncMock(return_value=mock_resp)

        events = await nba_client.poll()
        for e in events:
            assert e.server_ts_ms > 0

    @pytest.mark.asyncio
    async def test_no_duplicate_events_on_repoll(self, nba_client, nba_pbp):
        """Second poll with same data returns no new events."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"game": {"actions": nba_pbp}}
        mock_resp.raise_for_status = MagicMock()
        nba_client._http.get = AsyncMock(return_value=mock_resp)

        events1 = await nba_client.poll()
        events2 = await nba_client.poll()
        assert len(events2) == 0

    @pytest.mark.asyncio
    async def test_quarter_tracking(self, nba_client, nba_pbp):
        """Events have quarter field set."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"game": {"actions": nba_pbp}}
        mock_resp.raise_for_status = MagicMock()
        nba_client._http.get = AsyncMock(return_value=mock_resp)

        events = await nba_client.poll()
        for e in events:
            assert e.quarter is not None
            assert e.quarter >= 1


class TestDota2Client:
    @pytest_asyncio.fixture
    async def dota2_client(self):
        client = Dota2Client(
            match_id="test-dota-1",
            external_match_id="8741914801",
            team1="REKONIX",
            team2="PARIVISION",
        )
        client._http = AsyncMock()
        yield client

    @pytest.mark.asyncio
    async def test_first_poll_no_events(self, dota2_client, opendota_live):
        """First poll initializes state, no events emitted."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = opendota_live
        mock_resp.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp)

        events = await dota2_client.poll()
        assert len(events) == 0
        assert dota2_client._match_found is True

    @pytest.mark.asyncio
    async def test_detects_score_change(self, dota2_client, opendota_live):
        """Score change detected when radiant_score increases."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = opendota_live
        mock_resp.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp)

        # First poll: initialize
        await dota2_client.poll()

        # Modify score for second poll
        modified = json.loads(json.dumps(opendota_live))
        for m in modified:
            if str(m["match_id"]) == "8741914801":
                m["radiant_score"] = 15  # was 11
                break

        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = modified
        mock_resp2.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp2)

        events = await dota2_client.poll()
        score_events = [e for e in events if e.event_type == "score_change"]
        assert len(score_events) == 1
        assert score_events[0].team1_score == 15
        assert score_events[0].event_team == "REKONIX"

    @pytest.mark.asyncio
    async def test_detects_building_destroy(self, dota2_client, opendota_live):
        """Building destroy detected when building_state changes."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = opendota_live
        mock_resp.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp)

        await dota2_client.poll()

        modified = json.loads(json.dumps(opendota_live))
        for m in modified:
            if str(m["match_id"]) == "8741914801":
                m["building_state"] = 5374240  # changed from 5374244
                break

        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = modified
        mock_resp2.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp2)

        events = await dota2_client.poll()
        building_events = [e for e in events if e.event_type == "building_destroy"]
        assert len(building_events) == 1

    @pytest.mark.asyncio
    async def test_detects_gold_lead_swing(self, dota2_client, opendota_live):
        """Gold lead swing detected when lead changes by >= threshold."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = opendota_live
        mock_resp.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp)

        await dota2_client.poll()

        modified = json.loads(json.dumps(opendota_live))
        for m in modified:
            if str(m["match_id"]) == "8741914801":
                # Was -34353, swing by 5000+
                m["radiant_lead"] = -29000
                break

        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = modified
        mock_resp2.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp2)

        events = await dota2_client.poll()
        swing_events = [e for e in events if e.event_type == "gold_lead_swing"]
        assert len(swing_events) == 1

    @pytest.mark.asyncio
    async def test_detects_game_end(self, dota2_client, opendota_live):
        """Game end detected when match disappears from /live."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = opendota_live
        mock_resp.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp)

        await dota2_client.poll()

        # Remove the target match
        filtered = [m for m in opendota_live if str(m["match_id"]) != "8741914801"]
        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = filtered
        mock_resp2.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp2)

        events = await dota2_client.poll()
        end_events = [e for e in events if e.event_type == "game_end"]
        assert len(end_events) == 1

    @pytest.mark.asyncio
    async def test_no_events_after_game_end(self, dota2_client, opendota_live):
        """No events after game has ended."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = opendota_live
        mock_resp.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp)

        await dota2_client.poll()

        # Game disappears
        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = []
        mock_resp2.raise_for_status = MagicMock()
        dota2_client._http.get = AsyncMock(return_value=mock_resp2)

        await dota2_client.poll()  # game_end

        # Third poll — should return nothing
        events3 = await dota2_client.poll()
        assert len(events3) == 0
