"""WebSocket Sports API client for live game state from Polymarket.

Connects to wss://sports-api.polymarket.com/ws — a broadcast feed of live
game state for all sports (no auth, no subscription required). Filters
messages to find our game using league abbreviation + fuzzy team matching,
then locks to a gameId after 2 consecutive matches.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import websockets
from websockets.exceptions import ConnectionClosed

from .models import MatchEvent

logger = logging.getLogger(__name__)

SPORTS_WS_URL = "wss://sports-api.polymarket.com/ws"
RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30]

# Sport -> list of leagueAbbreviation values (case-insensitive).
# Start permissive; tighten as we collect more samples.
LEAGUE_MAP: dict[str, list[str]] = {
    "tennis": ["atp", "wta", "challenger"],
    "nba": ["nba"],
    "mlb": ["mlb"],
    "nhl": ["nhl"],
    "cbb": ["cbb", "ncaab"],
    "soccer": ["epl", "ucl", "laliga", "seriea", "bundesliga", "mls", "fifa"],
    "cricket": ["ipl", "t20", "cricket"],
    "cs2": ["cs2", "csgo"],
    "valorant": ["valorant", "vct"],
    "lol": ["lol", "lck", "lpl", "lcs", "lec"],
    "dota2": ["dota2", "dota"],
}

# Diagnostic interval (seconds)
_DIAG_INTERVAL = 60


class WebSocketSportsClient:
    def __init__(
        self,
        match_id: str,
        sport: str,
        team1: str,
        team2: str,
        queue: asyncio.Queue[list[MatchEvent]],
    ):
        self.match_id = match_id
        self.sport = sport
        self.team1 = team1
        self.team2 = team2
        self._queue = queue

        # Allowed league abbreviations for this sport
        self._leagues = [l.lower() for l in LEAGUE_MAP.get(sport, [])]

        # gameId lock-on state
        self._locked_game_id: int | None = None
        self._lock_candidate: tuple[int, int] | None = None  # (gameId, consecutive_count)

        # Last known state (for change detection)
        self._last_score: str | None = None
        self._last_period: str | None = None
        self._last_status: str | None = None
        self._last_ended: bool | None = None

        # Connection state
        self._running = False
        self._ws: websockets.WebSocketClientProtocol | None = None  # type: ignore[name-defined]

        # Diagnostics
        self._msg_count = 0
        self._last_diag: float = 0.0
        self.event_count = 0
        self._observed_leagues: set[str] = set()
        self._target_league_teams: list[str] = []

    async def run(self) -> None:
        """Main loop: connect, receive, reconnect on failure."""
        self._running = True
        attempt = 0

        while self._running:
            try:
                await self._connect_and_receive()
                attempt = 0
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                if not self._running:
                    break
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                logger.warning(
                    "Sports WS disconnected (%s), reconnecting in %ds...", e, delay
                )
                await asyncio.sleep(delay)
                attempt += 1
            except Exception:
                if not self._running:
                    break
                logger.exception("Sports WS unexpected error")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        """Signal the client to stop."""
        self._running = False
        if self._ws:
            await self._ws.close()

    # --- Connection lifecycle ---

    async def _connect_and_receive(self) -> None:
        async with websockets.connect(SPORTS_WS_URL, ping_interval=None) as ws:
            self._ws = ws
            logger.info("Sports WS connected")
            self._last_diag = time.time()
            await self._receive_loop(ws)

    async def _receive_loop(self, ws: websockets.WebSocketClientProtocol) -> None:  # type: ignore[name-defined]
        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                logger.warning("Sports WS no message in 30s, forcing reconnect")
                return

            # Text ping/pong
            if raw == "ping":
                await ws.send("pong")
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            self._msg_count += 1

            # Track observed leagues and target-league teams
            league = str(data.get("leagueAbbreviation", "")).lower()
            if league:
                self._observed_leagues.add(league)
                if self._leagues and league in self._leagues:
                    home = data.get("homeTeam", "")
                    away = data.get("awayTeam", "")
                    if home and away:
                        pair = f"{home} vs {away}"
                        if pair not in self._target_league_teams:
                            self._target_league_teams.append(pair)

            # Diagnostics
            now = time.time()
            if now - self._last_diag >= _DIAG_INTERVAL and self._locked_game_id is None:
                logger.info(
                    "Sports WS: %d msgs, no match for %s vs %s | "
                    "leagues seen: %s | %s games in target league(s) %s: %s",
                    self._msg_count,
                    self.team1,
                    self.team2,
                    sorted(self._observed_leagues) if self._observed_leagues else "(none)",
                    len(self._target_league_teams),
                    self._leagues or "(any)",
                    "; ".join(self._target_league_teams[:5]) or "(none)",
                )
                self._last_diag = now

            # Filter and process
            if self._locked_game_id is not None:
                if data.get("gameId") == self._locked_game_id:
                    self._process_message(data)
            elif self._matches_our_game(data):
                game_id = data.get("gameId")
                if game_id is not None:
                    self._try_lock(game_id, data)
                self._process_message(data)

    # --- Game matching ---

    def _matches_our_game(self, data: dict) -> bool:
        """Check if a WS message belongs to our game (league + team match)."""
        # League filter
        league = str(data.get("leagueAbbreviation", "")).lower()
        if self._leagues and league not in self._leagues:
            return False

        # Fuzzy team match
        home = str(data.get("homeTeam", "")).lower()
        away = str(data.get("awayTeam", "")).lower()
        t1 = self.team1.lower()
        t2 = self.team2.lower()

        return _fuzzy_team_match(t1, t2, home, away)

    def _try_lock(self, game_id: int, data: dict) -> None:
        """Try to lock onto a gameId after 2 consecutive matches."""
        if self._lock_candidate is not None and self._lock_candidate[0] == game_id:
            count = self._lock_candidate[1] + 1
            if count >= 2:
                self._locked_game_id = game_id
                logger.info(
                    "Sports WS: locked to gameId=%d (%s vs %s)",
                    game_id,
                    data.get("homeTeam", "?"),
                    data.get("awayTeam", "?"),
                )
                return
            self._lock_candidate = (game_id, count)
        else:
            self._lock_candidate = (game_id, 1)

    # --- Event detection ---

    def _process_message(self, data: dict) -> None:
        """Compare to last known state, detect changes, emit MatchEvents."""
        status = data.get("status")
        score = data.get("score")
        period = data.get("period")
        ended = data.get("ended")
        event_state = data.get("eventState", {})

        server_ts_ms, ts_quality = self._parse_server_ts(
            event_state.get("updatedAt") if isinstance(event_state, dict) else None
        )

        events: list[MatchEvent] = []

        # Game start: status transitions to "inprogress"
        if status == "inprogress" and self._last_status != "inprogress":
            events.append(self._make_event(
                "game_start", score, server_ts_ms, ts_quality, data
            ))

        # Score change
        if score is not None and score != self._last_score and self._last_score is not None:
            events.append(self._make_event(
                "score_change", score, server_ts_ms, ts_quality, data
            ))

        # Period change
        if period is not None and period != self._last_period and self._last_period is not None:
            events.append(self._make_event(
                "period_change", score, server_ts_ms, ts_quality, data
            ))

        # Game end
        if ended is True and self._last_ended is not True:
            events.append(self._make_event(
                "game_end", score, server_ts_ms, ts_quality, data
            ))

        # Update tracked state
        if status is not None:
            self._last_status = status
        if score is not None:
            self._last_score = score
        if period is not None:
            self._last_period = period
        if ended is not None:
            self._last_ended = ended

        if events:
            for e in events:
                logger.info("Sports WS event: %s | %s", e.event_type, score)
                self.event_count += 1
            self._queue.put_nowait(events)

    def _make_event(
        self,
        event_type: str,
        score: str | None,
        server_ts_ms: int,
        ts_quality: str,
        raw_data: dict,
    ) -> MatchEvent:
        t1_score, t2_score = self._parse_score(score)
        return MatchEvent(
            match_id=self.match_id,
            local_ts=datetime.now(timezone.utc).isoformat(),
            server_ts_raw=str(server_ts_ms),
            server_ts_ms=server_ts_ms,
            sport=self.sport,
            event_type=event_type,
            team1_score=t1_score,
            team2_score=t2_score,
            timestamp_quality=ts_quality,
            raw_event_json=json.dumps(raw_data),
        )

    # --- Parsing helpers ---

    @staticmethod
    def _parse_score(score_str: str | None) -> tuple[int | None, int | None]:
        """Best-effort 'X-Y' numeric parse. Returns (None, None) on failure."""
        if not score_str:
            return None, None
        parts = score_str.split("-")
        if len(parts) != 2:
            return None, None
        try:
            return int(parts[0].strip()), int(parts[1].strip())
        except (ValueError, TypeError):
            return None, None

    @staticmethod
    def _parse_server_ts(updated_at: str | None) -> tuple[int, str]:
        """ISO 8601 -> ms epoch. Falls back to local time if unparseable."""
        if updated_at:
            try:
                # Handle nanosecond precision by truncating to microseconds
                cleaned = updated_at
                if "." in cleaned:
                    base, frac_and_tz = cleaned.split(".", 1)
                    # Separate fractional seconds from timezone
                    tz_part = ""
                    frac = frac_and_tz
                    for tz_char in ("Z", "+", "-"):
                        if tz_char in frac_and_tz[1:]:  # skip first char for negative
                            idx = frac_and_tz.index(tz_char, 1) if tz_char != "Z" else frac_and_tz.index(tz_char)
                            frac = frac_and_tz[:idx]
                            tz_part = frac_and_tz[idx:]
                            break
                        elif tz_char == "Z" and frac_and_tz.endswith("Z"):
                            frac = frac_and_tz[:-1]
                            tz_part = "Z"
                            break
                    # Truncate to 6 decimal places (microseconds)
                    frac = frac[:6]
                    cleaned = f"{base}.{frac}{tz_part}"
                dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
                return int(dt.timestamp() * 1000), "server"
            except (ValueError, TypeError, IndexError):
                pass
        return int(time.time() * 1000), "local"


def _fuzzy_team_match(t1: str, t2: str, home: str, away: str) -> bool:
    """Case-insensitive substring/token matching for team names.

    Either (t1 matches home AND t2 matches away) or (t1 matches away AND t2 matches home).
    A match is: one name is a substring of the other, or they share a meaningful token.
    """
    def _name_match(a: str, b: str) -> bool:
        if not a or not b:
            return False
        # Substring match (either direction)
        if a in b or b in a:
            return True
        # Token overlap (words >= 3 chars)
        a_tokens = {w for w in a.split() if len(w) >= 3}
        b_tokens = {w for w in b.split() if len(w) >= 3}
        return bool(a_tokens & b_tokens)

    return (
        (_name_match(t1, home) and _name_match(t2, away))
        or (_name_match(t1, away) and _name_match(t2, home))
    )
