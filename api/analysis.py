"""Analysis intelligence: team mapping, market classification, event-to-token linking.

Pure domain logic — no SQL here.  Called by the FastAPI endpoints in queries.py.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# NBA: 3-letter tricode → team name (matches how outcomes appear in markets)
# ---------------------------------------------------------------------------
NBA_TRICODE_TO_NAME: dict[str, str] = {
    "ATL": "Hawks", "BOS": "Celtics", "BKN": "Nets", "CHA": "Hornets",
    "CHI": "Bulls", "CLE": "Cavaliers", "DAL": "Mavericks", "DEN": "Nuggets",
    "DET": "Pistons", "GSW": "Warriors", "HOU": "Rockets", "IND": "Pacers",
    "LAC": "Clippers", "LAL": "Lakers", "MEM": "Grizzlies", "MIA": "Heat",
    "MIL": "Bucks", "MIN": "Timberwolves", "NOP": "Pelicans", "NYK": "Knicks",
    "OKC": "Thunder", "ORL": "Magic", "PHI": "76ers", "PHX": "Suns",
    "POR": "Trail Blazers", "SAC": "Kings", "SAS": "Spurs", "TOR": "Raptors",
    "UTA": "Jazz", "WAS": "Wizards",
}

# ---------------------------------------------------------------------------
# NHL: numeric team ID (string) → team name
# Full list from NHL API /teams — IDs are stable across seasons.
# ---------------------------------------------------------------------------
NHL_TEAM_ID_TO_NAME: dict[str, str] = {
    "1": "Devils", "2": "Islanders", "3": "Rangers", "4": "Flyers",
    "5": "Penguins", "6": "Bruins", "7": "Sabres", "8": "Canadiens",
    "9": "Senators", "10": "Maple Leafs", "12": "Hurricanes", "13": "Panthers",
    "14": "Lightning", "15": "Capitals", "16": "Blackhawks", "17": "Red Wings",
    "18": "Predators", "19": "Blues", "20": "Flames", "21": "Avalanche",
    "22": "Oilers", "23": "Canucks", "24": "Ducks", "25": "Stars",
    "26": "Kings", "28": "Sharks", "29": "Blue Jackets", "30": "Wild",
    "52": "Jets", "53": "Coyotes", "54": "Golden Knights", "55": "Kraken",
    "59": "Utah Hockey Club",
}

# Reverse lookup: normalized team name → set of token outcomes that represent them.
# Built lazily — the primary lookup path is outcomes-based matching.


# ---------------------------------------------------------------------------
# Team name resolution
# ---------------------------------------------------------------------------

def resolve_event_team(
    event_team: str | None,
    sport: str | None,
) -> str | None:
    """Resolve raw ``event_team`` from match_events to a human team name.

    Returns *None* when the sport doesn't populate event_team (Sports WS).
    """
    if not event_team:
        return None

    if sport == "nba":
        return NBA_TRICODE_TO_NAME.get(event_team)
    if sport == "nhl":
        return NHL_TEAM_ID_TO_NAME.get(event_team)

    # Sports WS and others: event_team is not set
    return None


def normalize_team_name(name: str) -> str:
    """Lowercase, strip common city prefixes / suffixes for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# ---------------------------------------------------------------------------
# Market lookup
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketInfo:
    market_id: str
    question: str
    outcome_label: str
    token_id: str
    market_type: str  # "match_winner" | "over_under" | "unknown" | …
    team_name: str | None  # resolved team name for this outcome, if any
    match_id: str | None


def build_market_lookup(conn: sqlite3.Connection) -> dict[str, MarketInfo]:
    """Build ``{token_id -> MarketInfo}`` by joining markets + mapping + matches.

    Classification priority:
    1. ``market_match_mapping.relationship`` if it's something other than "unknown"
    2. Outcomes-based fallback: if outcomes match team1/team2 → "match_winner"
    """
    # Fetch matches for team names
    team_by_match: dict[str, tuple[str, str]] = {}
    for row in conn.execute("SELECT match_id, team1, team2 FROM matches").fetchall():
        team_by_match[row[0]] = (row[1], row[2])

    # Fetch mapping relationships
    rel_by_market: dict[str, tuple[str, str | None]] = {}  # market_id -> (relationship, match_id)
    for row in conn.execute(
        "SELECT market_id, match_id, relationship FROM market_match_mapping"
    ).fetchall():
        rel_by_market[row[0]] = (row[2], row[1])

    # Build lookup
    lookup: dict[str, MarketInfo] = {}
    for row in conn.execute(
        "SELECT market_id, question, outcomes_json, token_ids_json FROM markets"
    ).fetchall():
        market_id, question, outcomes_json, token_ids_json = row
        try:
            outcomes = json.loads(outcomes_json)
            token_ids = json.loads(token_ids_json)
        except (json.JSONDecodeError, TypeError):
            continue

        # Determine relationship
        mapping = rel_by_market.get(market_id)
        relationship = mapping[0] if mapping else "unknown"
        match_id = mapping[1] if mapping else None

        # If relationship is "unknown", try outcomes-based classification
        if relationship == "unknown" and match_id and match_id in team_by_match:
            team1, team2 = team_by_match[match_id]
            norm_teams = {normalize_team_name(team1), normalize_team_name(team2)}
            norm_outcomes = {normalize_team_name(o) for o in outcomes}
            # If outcomes match both team names → match_winner
            if norm_teams & norm_outcomes == norm_teams:
                relationship = "match_winner"

        # Resolve team name per outcome
        teams = team_by_match.get(match_id, (None, None)) if match_id else (None, None)

        for tid, outcome in zip(token_ids, outcomes):
            # Determine which team this outcome belongs to
            team_name: str | None = None
            if teams[0] and normalize_team_name(outcome) == normalize_team_name(teams[0]):
                team_name = teams[0]
            elif teams[1] and normalize_team_name(outcome) == normalize_team_name(teams[1]):
                team_name = teams[1]

            lookup[tid] = MarketInfo(
                market_id=market_id,
                question=question,
                outcome_label=outcome,
                token_id=tid,
                market_type=relationship,
                team_name=team_name,
                match_id=match_id,
            )

    return lookup


# ---------------------------------------------------------------------------
# Event-to-token linker
# ---------------------------------------------------------------------------

def link_event_to_tokens(
    event_type: str,
    event_team: str | None,
    sport: str | None,
    lookup: dict[str, MarketInfo],
) -> list[str]:
    """Return token_ids most relevant to this event, using smart linking.

    Priority:
    - For score_change with a resolved team: both moneyline tokens for that match
    - For other events or when event_team is empty: all moneyline tokens
    - Filters to market_type == "match_winner" only
    """
    resolved = resolve_event_team(event_team, sport)

    # Collect all match_winner tokens
    ml_tokens = [
        info for info in lookup.values()
        if info.market_type == "match_winner"
    ]

    if not ml_tokens:
        return []

    # If we resolved a team and it's a score_change, find the match containing that team
    if resolved and event_type == "score_change":
        # Find moneyline tokens where one outcome matches the scoring team
        matched_match_ids = set()
        for info in ml_tokens:
            if info.team_name and normalize_team_name(info.team_name) == normalize_team_name(resolved):
                matched_match_ids.add(info.match_id)

        if matched_match_ids:
            # Return ALL moneyline tokens for the matched match(es)
            return [
                info.token_id for info in ml_tokens
                if info.match_id in matched_match_ids
            ]

    # Fallback: return all moneyline tokens (both teams / 3-way)
    return [info.token_id for info in ml_tokens]


# ---------------------------------------------------------------------------
# NHL event deduplication
# ---------------------------------------------------------------------------

def dedup_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate period_end and half_end events (NHL intermission noise).

    Only deduplicates events of the same (event_type, quarter) when they are
    within 60s of each other.  Other event types pass through unchanged.
    """
    DEDUP_TYPES = {"period_end", "half_end"}

    result: list[dict[str, Any]] = []
    last_seen: dict[tuple[str, Any], int] = {}  # (event_type, quarter) -> last server_ts_ms

    for ev in events:
        ev_type = ev["event_type"]
        if ev_type in DEDUP_TYPES:
            key = (ev_type, ev.get("quarter"))
            prev_ts = last_seen.get(key)
            ts = ev["server_ts_ms"]
            if prev_ts is not None and abs(ts - prev_ts) < 60_000:
                continue  # skip duplicate
            last_seen[key] = ts

        result.append(ev)

    return result
