"""NBA CDN play-by-play game-state client."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from ..models import MatchEvent
from .base import GameNotStarted, GameStateClient

logger = logging.getLogger(__name__)

NBA_SCOREBOARD_URL = (
    "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
)
NBA_PBP_URL = "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game_id}.json"

# Action types that indicate scoring
SCORING_ACTIONS = {"2pt", "3pt", "freethrow"}


async def lookup_game_id(team1: str, team2: str) -> str | None:
    """Look up today's NBA game ID from the scoreboard by matching team names.

    Matches against teamName (e.g. "Nuggets"), teamCity (e.g. "Denver"),
    and teamTricode (e.g. "DEN"). Returns the gameId string or None.
    """
    async with httpx.AsyncClient(
        timeout=15.0,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/"},
    ) as client:
        try:
            resp = await client.get(NBA_SCOREBOARD_URL)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception("Failed to fetch NBA scoreboard for game ID lookup")
            return None

    games = data.get("scoreboard", {}).get("games", [])
    search_terms = {t.lower() for t in [team1, team2]}

    for game in games:
        home = game.get("homeTeam", {})
        away = game.get("awayTeam", {})
        game_teams = {
            home.get("teamName", "").lower(),
            home.get("teamCity", "").lower(),
            home.get("teamTricode", "").lower(),
            away.get("teamName", "").lower(),
            away.get("teamCity", "").lower(),
            away.get("teamTricode", "").lower(),
        }
        # Match if both config teams appear in the game's team identifiers
        if all(any(term in gt for gt in game_teams) for term in search_terms):
            game_id = game.get("gameId", "")
            logger.info(
                "Auto-resolved NBA game ID: %s (%s vs %s)",
                game_id, home.get("teamTricode"), away.get("teamTricode"),
            )
            return game_id

    logger.warning(
        "Could not find NBA game for %s vs %s in today's scoreboard (%d games)",
        team1, team2, len(games),
    )
    return None


class NbaClient(GameStateClient):
    sport = "nba"
    poll_interval_seconds = 10.0

    def __init__(
        self,
        match_id: str,
        game_id: str,
        team1: str,
        team2: str,
    ):
        self.match_id = match_id
        self.game_id = game_id
        self.team1 = team1
        self.team2 = team2
        self._http: httpx.AsyncClient | None = None

        # Track last seen action number and scores
        self._last_action_number: int = 0
        self._last_home_score: int = 0
        self._last_away_score: int = 0
        self._last_period: int = 0
        self._game_ended: bool = False

    async def start(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=15.0,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.nba.com/",
            },
        )

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Client not started")
        return self._http

    async def poll(self) -> list[MatchEvent]:
        if self._game_ended:
            return []

        try:
            url = NBA_PBP_URL.format(game_id=self.game_id)
            resp = await self.http.get(url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (403, 404):
                raise GameNotStarted(f"NBA API returned {exc.response.status_code}")
            logger.exception("NBA PBP fetch error for game %s", self.game_id)
            return []
        except Exception:
            logger.exception("NBA PBP fetch error for game %s", self.game_id)
            return []

        actions = data.get("game", {}).get("actions", [])
        if not actions:
            return []

        events: list[MatchEvent] = []

        for action in actions:
            action_num = action.get("actionNumber", 0)
            if action_num <= self._last_action_number:
                continue

            action_type = action.get("actionType", "")
            home_score = int(action.get("scoreHome", 0))
            away_score = int(action.get("scoreAway", 0))
            period = action.get("period", 0)
            time_actual = action.get("timeActual", "")

            # Normalize timestamp with local fallback
            server_ts_ms = 0
            timestamp_quality = "server"
            if time_actual:
                try:
                    dt = datetime.fromisoformat(time_actual.replace("Z", "+00:00"))
                    server_ts_ms = int(dt.timestamp() * 1000)
                except (ValueError, TypeError):
                    pass
            if server_ts_ms == 0:
                server_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                timestamp_quality = "local"

            team_tricode = action.get("teamTricode", "")

            # Score change detection
            if home_score != self._last_home_score or away_score != self._last_away_score:
                if action_type in SCORING_ACTIONS and action.get("shotResult") == "Made":
                    events.append(
                        MatchEvent(
                            match_id=self.match_id,
                            local_ts=datetime.now(timezone.utc).isoformat(),
                            server_ts_raw=time_actual,
                            server_ts_ms=server_ts_ms,
                            sport="nba",
                            event_type="score_change",
                            quarter=period,
                            team1_score=home_score,
                            team2_score=away_score,
                            event_team=team_tricode,
                            timestamp_quality=timestamp_quality,
                            raw_event_json=json.dumps(action),
                        )
                    )
                    self._last_home_score = home_score
                    self._last_away_score = away_score

            # Quarter/period transitions
            if action_type == "period":
                subtype = action.get("subType", "")
                if subtype == "end":
                    event_type = "quarter_end"
                    if period == 2:
                        event_type = "half_end"
                    events.append(
                        MatchEvent(
                            match_id=self.match_id,
                            local_ts=datetime.now(timezone.utc).isoformat(),
                            server_ts_raw=time_actual,
                            server_ts_ms=server_ts_ms,
                            sport="nba",
                            event_type=event_type,
                            quarter=period,
                            team1_score=home_score,
                            team2_score=away_score,
                            timestamp_quality=timestamp_quality,
                            raw_event_json=json.dumps(action),
                        )
                    )

            # Timeout detection
            if action_type == "timeout":
                events.append(
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw=time_actual,
                        server_ts_ms=server_ts_ms,
                        sport="nba",
                        event_type="timeout",
                        quarter=period,
                        team1_score=home_score,
                        team2_score=away_score,
                        event_team=team_tricode,
                        timestamp_quality=timestamp_quality,
                        raw_event_json=json.dumps(action),
                    )
                )

            # Game end detection
            if action_type == "game" and action.get("subType") == "end":
                self._game_ended = True
                events.append(
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw=time_actual,
                        server_ts_ms=server_ts_ms,
                        sport="nba",
                        event_type="game_end",
                        quarter=period,
                        team1_score=home_score,
                        team2_score=away_score,
                        timestamp_quality=timestamp_quality,
                        raw_event_json=json.dumps(action),
                    )
                )

            # Foul detection (momentum shift, foul trouble)
            if action_type == "foul":
                events.append(
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw=time_actual,
                        server_ts_ms=server_ts_ms,
                        sport="nba",
                        event_type="foul",
                        quarter=period,
                        team1_score=home_score,
                        team2_score=away_score,
                        event_team=team_tricode,
                        timestamp_quality=timestamp_quality,
                        raw_event_json=json.dumps(action),
                    )
                )

            # Turnover detection (unexpected possession change)
            if action_type == "turnover":
                events.append(
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw=time_actual,
                        server_ts_ms=server_ts_ms,
                        sport="nba",
                        event_type="turnover",
                        quarter=period,
                        team1_score=home_score,
                        team2_score=away_score,
                        event_team=team_tricode,
                        timestamp_quality=timestamp_quality,
                        raw_event_json=json.dumps(action),
                    )
                )

            # Coach's challenge (uncertainty → resolution spike)
            if action_type == "challenge":
                events.append(
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw=time_actual,
                        server_ts_ms=server_ts_ms,
                        sport="nba",
                        event_type="challenge",
                        quarter=period,
                        team1_score=home_score,
                        team2_score=away_score,
                        event_team=team_tricode,
                        timestamp_quality=timestamp_quality,
                        raw_event_json=json.dumps(action),
                    )
                )

            # Substitution (star player exit/entry)
            if action_type == "substitution":
                events.append(
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw=time_actual,
                        server_ts_ms=server_ts_ms,
                        sport="nba",
                        event_type="substitution",
                        quarter=period,
                        team1_score=home_score,
                        team2_score=away_score,
                        event_team=team_tricode,
                        timestamp_quality=timestamp_quality,
                        raw_event_json=json.dumps(action),
                    )
                )

            # Violation (rare, unexpected)
            if action_type == "violation":
                events.append(
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw=time_actual,
                        server_ts_ms=server_ts_ms,
                        sport="nba",
                        event_type="violation",
                        quarter=period,
                        team1_score=home_score,
                        team2_score=away_score,
                        event_team=team_tricode,
                        timestamp_quality=timestamp_quality,
                        raw_event_json=json.dumps(action),
                    )
                )

            self._last_action_number = action_num
            self._last_period = period

        return events

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
