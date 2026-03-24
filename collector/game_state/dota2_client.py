"""OpenDota /live game-state client with coarse event detection via diffing."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from ..models import MatchEvent
from .base import GameStateClient

logger = logging.getLogger(__name__)

OPENDOTA_LIVE_URL = "https://api.opendota.com/api/live"

# Gold lead swing threshold for detecting significant momentum shifts
GOLD_SWING_THRESHOLD = 2000


class Dota2Client(GameStateClient):
    sport = "dota2"
    poll_interval_seconds = 5.0

    def __init__(
        self,
        match_id: str,
        external_match_id: str,
        team1: str,
        team2: str,
        gold_swing_threshold: int = GOLD_SWING_THRESHOLD,
    ):
        self.match_id = match_id
        self.external_match_id = external_match_id
        self.team1 = team1  # radiant
        self.team2 = team2  # dire
        self.gold_swing_threshold = gold_swing_threshold
        self._http: httpx.AsyncClient | None = None

        # Previous state for diffing
        self._prev_radiant_score: int | None = None
        self._prev_dire_score: int | None = None
        self._prev_building_state: int | None = None
        self._prev_radiant_lead: int | None = None
        self._match_found: bool = False
        self._match_disappeared: bool = False

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Client not started")
        return self._http

    async def poll(self) -> list[MatchEvent]:
        if self._match_disappeared:
            return []

        try:
            resp = await self.http.get(OPENDOTA_LIVE_URL)
            resp.raise_for_status()
            live_matches = resp.json()
        except Exception:
            logger.exception("OpenDota /live fetch error")
            return []

        # Find our match
        target = None
        for m in live_matches:
            if str(m.get("match_id")) == self.external_match_id:
                target = m
                break

        # Match disappeared — game ended
        if target is None:
            if self._match_found and not self._match_disappeared:
                self._match_disappeared = True
                return [
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw="",
                        server_ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                        sport="dota2",
                        event_type="game_end",
                        team1_score=self._prev_radiant_score,
                        team2_score=self._prev_dire_score,
                        gold_lead=self._prev_radiant_lead,
                        building_state=self._prev_building_state,
                    )
                ]
            return []

        self._match_found = True

        radiant_score = target.get("radiant_score", 0)
        dire_score = target.get("dire_score", 0)
        building_state = target.get("building_state", 0)
        radiant_lead = target.get("radiant_lead", 0)
        last_update = target.get("last_update_time", 0)
        server_ts_ms = last_update * 1000

        events: list[MatchEvent] = []

        # Score change
        if self._prev_radiant_score is not None:
            if radiant_score != self._prev_radiant_score or dire_score != self._prev_dire_score:
                # Determine which team scored
                event_team = None
                if radiant_score > self._prev_radiant_score:
                    event_team = self.team1
                elif dire_score > self._prev_dire_score:
                    event_team = self.team2

                events.append(
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw=str(last_update),
                        server_ts_ms=server_ts_ms,
                        sport="dota2",
                        event_type="score_change",
                        team1_score=radiant_score,
                        team2_score=dire_score,
                        event_team=event_team,
                        gold_lead=radiant_lead,
                        building_state=building_state,
                        raw_event_json=json.dumps(target),
                    )
                )

        # Building destroy
        if self._prev_building_state is not None:
            if building_state != self._prev_building_state:
                events.append(
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw=str(last_update),
                        server_ts_ms=server_ts_ms,
                        sport="dota2",
                        event_type="building_destroy",
                        team1_score=radiant_score,
                        team2_score=dire_score,
                        gold_lead=radiant_lead,
                        building_state=building_state,
                        raw_event_json=json.dumps(target),
                    )
                )

        # Gold lead swing
        if self._prev_radiant_lead is not None:
            swing = abs(radiant_lead - self._prev_radiant_lead)
            if swing >= self.gold_swing_threshold:
                events.append(
                    MatchEvent(
                        match_id=self.match_id,
                        local_ts=datetime.now(timezone.utc).isoformat(),
                        server_ts_raw=str(last_update),
                        server_ts_ms=server_ts_ms,
                        sport="dota2",
                        event_type="gold_lead_swing",
                        team1_score=radiant_score,
                        team2_score=dire_score,
                        gold_lead=radiant_lead,
                        building_state=building_state,
                        raw_event_json=json.dumps(target),
                    )
                )

        # Update state
        self._prev_radiant_score = radiant_score
        self._prev_dire_score = dire_score
        self._prev_building_state = building_state
        self._prev_radiant_lead = radiant_lead

        return events

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
