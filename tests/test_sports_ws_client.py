"""Tests for the Sports WebSocket game state client."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from collector.sports_ws_client import (
    LEAGUE_MAP,
    WebSocketSportsClient,
    _fuzzy_team_match,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_msg() -> dict:
    with open(FIXTURES / "ws_sport_result_sample.json") as f:
        return json.load(f)


def _make_client(
    sport: str = "tennis",
    team1: str = "Vacherot",
    team2: str = "Fils",
) -> WebSocketSportsClient:
    queue: asyncio.Queue[list] = asyncio.Queue()
    return WebSocketSportsClient(
        match_id="test-match",
        sport=sport,
        team1=team1,
        team2=team2,
        queue=queue,
    )


# --- Score parsing ---


def test_parse_score_simple():
    assert WebSocketSportsClient._parse_score("1-2") == (1, 2)


def test_parse_score_zero():
    assert WebSocketSportsClient._parse_score("0-0") == (0, 0)


def test_parse_score_invalid():
    assert WebSocketSportsClient._parse_score("6-4, 3-2") == (None, None)


def test_parse_score_empty():
    assert WebSocketSportsClient._parse_score("") == (None, None)


def test_parse_score_none():
    assert WebSocketSportsClient._parse_score(None) == (None, None)


# --- Timestamp parsing ---


def test_parse_server_ts():
    ts_ms, quality = WebSocketSportsClient._parse_server_ts(
        "2026-03-24T18:09:43.113413404Z"
    )
    assert quality == "server"
    # Should be roughly 2026-03-24T18:09:43 UTC in ms
    assert 1_774_000_000_000 < ts_ms < 1_780_000_000_000


def test_parse_server_ts_missing():
    ts_ms, quality = WebSocketSportsClient._parse_server_ts(None)
    assert quality == "local"
    assert ts_ms > 0


def test_parse_server_ts_unparseable():
    ts_ms, quality = WebSocketSportsClient._parse_server_ts("not-a-date")
    assert quality == "local"


# --- League filtering ---


def test_league_filter_match(sample_msg):
    client = _make_client(sport="tennis", team1="Vacherot", team2="Fils")
    assert client._matches_our_game(sample_msg) is True


def test_league_filter_no_match(sample_msg):
    client = _make_client(sport="nba", team1="Vacherot", team2="Fils")
    # ATP message should not match NBA sport
    assert client._matches_our_game(sample_msg) is False


def test_league_map_has_expected_sports():
    assert "tennis" in LEAGUE_MAP
    assert "nba" in LEAGUE_MAP
    assert "mlb" in LEAGUE_MAP
    assert "soccer" in LEAGUE_MAP
    assert "cbb" in LEAGUE_MAP


def test_league_filter_cbb():
    """CBB league abbreviation 'cbb' is in the LEAGUE_MAP for cbb sport."""
    assert "cbb" in LEAGUE_MAP["cbb"]
    assert "ncaab" in LEAGUE_MAP["cbb"]


# --- Fuzzy team matching ---


def test_team_fuzzy_match():
    assert _fuzzy_team_match("vacherot", "fils", "valentin vacherot", "arthur fils")


def test_team_fuzzy_match_reversed():
    assert _fuzzy_team_match("fils", "vacherot", "valentin vacherot", "arthur fils")


def test_team_no_match():
    assert not _fuzzy_team_match("djokovic", "nadal", "valentin vacherot", "arthur fils")


def test_team_fuzzy_case_insensitive():
    assert _fuzzy_team_match("celtics", "lakers", "boston celtics", "los angeles lakers")


def test_team_fuzzy_cbb_dayton():
    """CBB team name 'Dayton' matches 'Dayton Flyers'."""
    assert _fuzzy_team_match("dayton", "illinois state", "dayton flyers", "illinois state redbirds")


# --- Event detection ---


def test_score_change_detection(sample_msg):
    client = _make_client()
    # First message triggers game_start (status=inprogress, _last_status=None)
    client._process_message(sample_msg)
    assert client._queue.qsize() == 1
    first_events = client._queue.get_nowait()
    assert any(e.event_type == "game_start" for e in first_events)

    # Change score
    msg2 = {**sample_msg, "score": "2-2"}
    msg2["eventState"] = {**sample_msg["eventState"], "score": "2-2"}
    client._process_message(msg2)
    assert client._queue.qsize() == 1
    events = client._queue.get_nowait()
    assert any(e.event_type == "score_change" for e in events)


def test_period_change_detection(sample_msg):
    client = _make_client()
    client._process_message(sample_msg)  # sets initial state (+ game_start)
    client._queue.get_nowait()  # drain game_start

    msg2 = {**sample_msg, "period": "S2"}
    msg2["eventState"] = {**sample_msg["eventState"], "period": "S2"}
    client._process_message(msg2)
    assert client._queue.qsize() == 1
    events = client._queue.get_nowait()
    assert any(e.event_type == "period_change" for e in events)


def test_game_end_detection(sample_msg):
    client = _make_client()
    client._process_message(sample_msg)  # sets ended=False (+ game_start)
    client._queue.get_nowait()  # drain game_start

    msg2 = {**sample_msg, "ended": True}
    msg2["eventState"] = {**sample_msg["eventState"], "ended": True}
    client._process_message(msg2)
    assert client._queue.qsize() == 1
    events = client._queue.get_nowait()
    assert any(e.event_type == "game_end" for e in events)


def test_game_start_detection():
    client = _make_client()
    msg = {
        "gameId": 123,
        "leagueAbbreviation": "atp",
        "homeTeam": "Vacherot",
        "awayTeam": "Fils",
        "status": "scheduled",
        "score": "0-0",
        "period": "S1",
        "ended": False,
        "eventState": {"updatedAt": "2026-03-24T18:00:00Z"},
    }
    client._process_message(msg)  # status=scheduled, no event

    msg2 = {**msg, "status": "inprogress"}
    client._process_message(msg2)
    assert client._queue.qsize() == 1
    events = client._queue.get_nowait()
    assert any(e.event_type == "game_start" for e in events)


def test_no_event_on_duplicate(sample_msg):
    client = _make_client()
    client._process_message(sample_msg)  # game_start on first msg
    client._queue.get_nowait()  # drain game_start
    client._process_message(sample_msg)  # same state — no new events
    assert client._queue.qsize() == 0


# --- GameId lock-on ---


def test_gameid_lockon(sample_msg):
    client = _make_client()
    assert client._locked_game_id is None

    # First match
    client._try_lock(sample_msg["gameId"], sample_msg)
    assert client._locked_game_id is None  # needs 2 consecutive

    # Second consecutive match with same gameId
    client._try_lock(sample_msg["gameId"], sample_msg)
    assert client._locked_game_id == sample_msg["gameId"]


def test_gameid_lockon_reset_on_different():
    client = _make_client()
    client._try_lock(111, {"homeTeam": "A", "awayTeam": "B"})
    client._try_lock(222, {"homeTeam": "A", "awayTeam": "B"})  # different gameId
    assert client._locked_game_id is None
    assert client._lock_candidate == (222, 1)


# --- Ping/pong ---


@pytest.mark.asyncio
async def test_ping_pong():
    """Text 'ping' should trigger 'pong' response."""
    client = _make_client()
    client._running = True

    mock_ws = AsyncMock()
    # Simulate: ping, then timeout to exit loop
    mock_ws.recv = AsyncMock(side_effect=["ping", asyncio.TimeoutError()])
    mock_ws.send = AsyncMock()

    await client._receive_loop(mock_ws)

    mock_ws.send.assert_called_once_with("pong")


# --- MatchEvent output ---


def test_league_filter_challenger():
    """Challenger league messages are accepted for tennis sport."""
    client = _make_client(sport="tennis", team1="Rico", team2="Bertran")
    msg = {
        "gameId": 456,
        "leagueAbbreviation": "challenger",
        "homeTeam": "Rico",
        "awayTeam": "Bertran",
        "status": "inprogress",
        "score": "1-0",
        "period": "S1",
        "ended": False,
        "eventState": {"updatedAt": "2026-03-25T12:00:00Z"},
    }
    assert client._matches_our_game(msg) is True


# --- MatchEvent output ---


# --- Observed-leagues diagnostics ---


@pytest.mark.asyncio
async def test_observed_leagues_tracking():
    """Receive loop tracks league abbreviations and target-league teams."""
    client = _make_client(sport="mlb", team1="Yankees", team2="Giants")
    client._running = True

    msgs = [
        json.dumps({
            "gameId": 1, "leagueAbbreviation": "nba",
            "homeTeam": "Celtics", "awayTeam": "Lakers",
            "status": "inprogress", "score": "50-48",
        }),
        json.dumps({
            "gameId": 2, "leagueAbbreviation": "mlb",
            "homeTeam": "San Francisco Giants", "awayTeam": "New York Yankees",
            "status": "scheduled", "score": "0-0",
        }),
        json.dumps({
            "gameId": 3, "leagueAbbreviation": "mlb",
            "homeTeam": "Red Sox", "awayTeam": "Dodgers",
            "status": "inprogress", "score": "3-1",
        }),
    ]

    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=msgs + [asyncio.TimeoutError()])
    mock_ws.send = AsyncMock()

    await client._receive_loop(mock_ws)

    assert client._observed_leagues == {"nba", "mlb"}
    assert "San Francisco Giants vs New York Yankees" in client._target_league_teams
    assert "Red Sox vs Dodgers" in client._target_league_teams
    assert len(client._target_league_teams) == 2  # only mlb teams, not nba


@pytest.mark.asyncio
async def test_observed_leagues_no_duplicates():
    """Same team pair doesn't get added twice to target league teams."""
    client = _make_client(sport="nba", team1="Celtics", team2="Lakers")
    client._running = True

    msg = json.dumps({
        "gameId": 1, "leagueAbbreviation": "nba",
        "homeTeam": "Celtics", "awayTeam": "Lakers",
        "status": "inprogress", "score": "50-48",
    })

    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=[msg, msg, asyncio.TimeoutError()])
    mock_ws.send = AsyncMock()

    await client._receive_loop(mock_ws)

    assert len(client._target_league_teams) == 1


# --- MatchEvent output ---


def test_event_has_raw_json(sample_msg):
    client = _make_client()
    client._process_message(sample_msg)  # sets initial state (+ game_start)
    client._queue.get_nowait()  # drain game_start

    msg2 = {**sample_msg, "score": "2-2"}
    msg2["eventState"] = {**sample_msg["eventState"], "score": "2-2"}
    client._process_message(msg2)

    events = client._queue.get_nowait()
    for e in events:
        assert e.raw_event_json
        parsed = json.loads(e.raw_event_json)
        assert "gameId" in parsed
        assert e.match_id == "test-match"
        assert e.sport == "tennis"
