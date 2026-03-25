"""NHL API game-state client."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from ..models import MatchEvent
from .base import GameStateClient

logger = logging.getLogger(__name__)

NHL_SCOREBOARD_URL = "https://api-web.nhle.com/v1/scoreboard/now"
NHL_PBP_URL = "https://api-web.nhle.com/v1/gamecenter/{game_id}/play-by-play"

LIVE_STATES = {"LIVE", "CRIT"}
FINAL_STATES = {"FINAL", "OFFICIAL", "OFF"}


async def lookup_game_id(team1: str, team2: str) -> str | None:
    """Look up today's NHL game ID from the scoreboard by matching team names.

    Matches against abbrev (e.g. "VAN"), commonName (e.g. "Canucks"), and
    full name (e.g. "Vancouver Canucks"). Returns the game ID string or None.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(NHL_SCOREBOARD_URL)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception("Failed to fetch NHL scoreboard for game ID lookup")
            return None

    search_terms = {t.lower() for t in [team1, team2]}

    for day in data.get("gamesByDate", []):
        for game in day.get("games", []):
            home = game.get("homeTeam", {})
            away = game.get("awayTeam", {})
            game_teams = set()
            for team in (home, away):
                game_teams.add(team.get("abbrev", "").lower())
                game_teams.add(team.get("commonName", {}).get("default", "").lower())
                game_teams.add(team.get("name", {}).get("default", "").lower())
                game_teams.add(team.get("placeNameWithPreposition", {}).get("default", "").lower())

            if all(any(term in gt for gt in game_teams) for term in search_terms):
                game_id = str(game.get("id", ""))
                logger.info(
                    "Auto-resolved NHL game ID: %s (%s vs %s)",
                    game_id,
                    away.get("abbrev"),
                    home.get("abbrev"),
                )
                return game_id

    logger.warning(
        "Could not find NHL game for %s vs %s in today's scoreboard",
        team1, team2,
    )
    return None


class NhlClient(GameStateClient):
    sport = "nhl"
    poll_interval_seconds = 10.0

    def __init__(self, match_id: str, game_id: str, team1: str, team2: str):
        self.match_id = match_id
        self.game_id = game_id
        self.team1 = team1
        self.team2 = team2
        self._http: httpx.AsyncClient | None = None

        self._last_sort_order: int = 0
        self._last_away_score: int = 0
        self._last_home_score: int = 0
        self._game_ended: bool = False

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Client not started")
        return self._http

    async def poll(self) -> list[MatchEvent]:
        if self._game_ended:
            return []

        try:
            url = NHL_PBP_URL.format(game_id=self.game_id)
            resp = await self.http.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception("NHL PBP fetch error for game %s", self.game_id)
            return []

        plays = data.get("plays", [])
        if not plays:
            return []

        events: list[MatchEvent] = []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        for play in plays:
            sort_order = play.get("sortOrder", 0)
            if sort_order <= self._last_sort_order:
                continue

            type_key = play.get("typeDescKey", "")
            period_desc = play.get("periodDescriptor", {})
            period = period_desc.get("number", 0)
            period_type = period_desc.get("periodType", "REG")
            details = play.get("details", {})

            # Goal — score change
            if type_key == "goal":
                away_score = details.get("awayScore", self._last_away_score)
                home_score = details.get("homeScore", self._last_home_score)
                scoring_team_id = details.get("eventOwnerTeamId")
                events.append(MatchEvent(
                    match_id=self.match_id,
                    local_ts=datetime.now(timezone.utc).isoformat(),
                    server_ts_raw=f"P{period} {play.get('timeInPeriod', '')}",
                    server_ts_ms=now_ms,
                    sport="nhl",
                    event_type="score_change",
                    quarter=period,
                    team1_score=away_score,
                    team2_score=home_score,
                    event_team=str(scoring_team_id) if scoring_team_id else "",
                    timestamp_quality="local",
                    raw_event_json=json.dumps(play),
                ))
                self._last_away_score = away_score
                self._last_home_score = home_score

            # Period end
            elif type_key == "period-end":
                event_type = "period_end"
                if period == 2 and period_type == "REG":
                    event_type = "half_end"
                events.append(MatchEvent(
                    match_id=self.match_id,
                    local_ts=datetime.now(timezone.utc).isoformat(),
                    server_ts_raw=f"P{period} end",
                    server_ts_ms=now_ms,
                    sport="nhl",
                    event_type=event_type,
                    quarter=period,
                    team1_score=self._last_away_score,
                    team2_score=self._last_home_score,
                    timestamp_quality="local",
                    raw_event_json=json.dumps(play),
                ))

            # Game end
            elif type_key == "game-end":
                self._game_ended = True
                events.append(MatchEvent(
                    match_id=self.match_id,
                    local_ts=datetime.now(timezone.utc).isoformat(),
                    server_ts_raw="game-end",
                    server_ts_ms=now_ms,
                    sport="nhl",
                    event_type="game_end",
                    quarter=period,
                    team1_score=self._last_away_score,
                    team2_score=self._last_home_score,
                    timestamp_quality="local",
                    raw_event_json=json.dumps(play),
                ))

            # Penalty (useful for power-play price spikes)
            elif type_key == "penalty":
                events.append(MatchEvent(
                    match_id=self.match_id,
                    local_ts=datetime.now(timezone.utc).isoformat(),
                    server_ts_raw=f"P{period} {play.get('timeInPeriod', '')}",
                    server_ts_ms=now_ms,
                    sport="nhl",
                    event_type="timeout",  # reuse timeout slot for penalties
                    quarter=period,
                    team1_score=self._last_away_score,
                    team2_score=self._last_home_score,
                    event_team=str(details.get("eventOwnerTeamId", "")),
                    timestamp_quality="local",
                    raw_event_json=json.dumps(play),
                ))

            self._last_sort_order = sort_order

        return events

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
