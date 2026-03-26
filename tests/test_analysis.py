"""Tests for api.analysis — team mapping, market classification, event-to-token linking."""

import json
import sqlite3

import pytest

from api.analysis import (
    NBA_TRICODE_TO_NAME,
    NHL_TEAM_ID_TO_NAME,
    MarketInfo,
    build_market_lookup,
    dedup_events,
    link_event_to_tokens,
    normalize_team_name,
    resolve_event_team,
)


# ---------------------------------------------------------------------------
# resolve_event_team
# ---------------------------------------------------------------------------


class TestResolveEventTeam:
    def test_nba_tricode(self):
        assert resolve_event_team("OKC", "nba") == "Thunder"

    def test_nba_all_30_teams(self):
        assert len(NBA_TRICODE_TO_NAME) == 30
        # Spot-check a few
        assert resolve_event_team("LAL", "nba") == "Lakers"
        assert resolve_event_team("BOS", "nba") == "Celtics"

    def test_nhl_team_id(self):
        assert resolve_event_team("10", "nhl") == "Maple Leafs"

    def test_nhl_all_teams(self):
        assert len(NHL_TEAM_ID_TO_NAME) >= 32
        assert resolve_event_team("6", "nhl") == "Bruins"
        assert resolve_event_team("54", "nhl") == "Golden Knights"

    def test_sports_ws_returns_none(self):
        assert resolve_event_team(None, "cbb") is None
        assert resolve_event_team("", "mlb") is None

    def test_unknown_tricode_returns_none(self):
        assert resolve_event_team("ZZZ", "nba") is None

    def test_none_sport(self):
        assert resolve_event_team("OKC", None) is None


# ---------------------------------------------------------------------------
# normalize_team_name
# ---------------------------------------------------------------------------


class TestNormalizeTeamName:
    def test_lowercase(self):
        assert normalize_team_name("Thunder") == "thunder"

    def test_strips_non_alnum(self):
        assert normalize_team_name("Trail Blazers") == "trailblazers"
        assert normalize_team_name("76ers") == "76ers"

    def test_empty(self):
        assert normalize_team_name("") == ""


# ---------------------------------------------------------------------------
# build_market_lookup (with in-memory DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def market_db():
    """In-memory DB with markets + mapping + matches for testing classification."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE markets (
            market_id TEXT PRIMARY KEY,
            question TEXT,
            outcomes_json TEXT,
            token_ids_json TEXT
        );
        CREATE TABLE market_match_mapping (
            market_id TEXT,
            match_id TEXT,
            relationship TEXT,
            PRIMARY KEY (market_id, match_id)
        );
        CREATE TABLE matches (
            match_id TEXT PRIMARY KEY,
            team1 TEXT,
            team2 TEXT,
            sport TEXT
        );
    """)

    # Match
    conn.execute("INSERT INTO matches VALUES ('game1', 'Hawks', 'Pistons', 'nba')")

    # Match-winner market (relationship=unknown, but outcomes match team names)
    conn.execute(
        "INSERT INTO markets VALUES (?, ?, ?, ?)",
        ("mkt_mw", "Hawks vs. Pistons", json.dumps(["Hawks", "Pistons"]), json.dumps(["tok_h", "tok_p"])),
    )
    conn.execute("INSERT INTO market_match_mapping VALUES ('mkt_mw', 'game1', 'unknown')")

    # Over/under market
    conn.execute(
        "INSERT INTO markets VALUES (?, ?, ?, ?)",
        ("mkt_ou", "Hawks vs. Pistons: O/U 220.5", json.dumps(["Over", "Under"]), json.dumps(["tok_ov", "tok_un"])),
    )
    conn.execute("INSERT INTO market_match_mapping VALUES ('mkt_ou', 'game1', 'over_under')")

    # Explicit match_winner market
    conn.execute(
        "INSERT INTO markets VALUES (?, ?, ?, ?)",
        ("mkt_mw2", "Who will win?", json.dumps(["Hawks", "Pistons"]), json.dumps(["tok_h2", "tok_p2"])),
    )
    conn.execute("INSERT INTO market_match_mapping VALUES ('mkt_mw2', 'game1', 'match_winner')")

    conn.commit()
    return conn


class TestBuildMarketLookup:
    def test_classifies_match_winner_from_outcomes(self, market_db):
        lookup = build_market_lookup(market_db)
        assert lookup["tok_h"].market_type == "match_winner"
        assert lookup["tok_p"].market_type == "match_winner"

    def test_preserves_over_under(self, market_db):
        lookup = build_market_lookup(market_db)
        assert lookup["tok_ov"].market_type == "over_under"

    def test_explicit_match_winner(self, market_db):
        lookup = build_market_lookup(market_db)
        assert lookup["tok_h2"].market_type == "match_winner"

    def test_team_name_resolved(self, market_db):
        lookup = build_market_lookup(market_db)
        assert lookup["tok_h"].team_name == "Hawks"
        assert lookup["tok_p"].team_name == "Pistons"

    def test_over_under_no_team(self, market_db):
        lookup = build_market_lookup(market_db)
        assert lookup["tok_ov"].team_name is None

    def test_3_way_market(self):
        """Soccer match with draw option should return all 3 tokens."""
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE markets (market_id TEXT, question TEXT, outcomes_json TEXT, token_ids_json TEXT);
            CREATE TABLE market_match_mapping (market_id TEXT, match_id TEXT, relationship TEXT);
            CREATE TABLE matches (match_id TEXT, team1 TEXT, team2 TEXT, sport TEXT);
        """)
        conn.execute("INSERT INTO matches VALUES ('s1', 'Arsenal', 'Chelsea', 'soccer')")
        conn.execute(
            "INSERT INTO markets VALUES (?, ?, ?, ?)",
            ("mkt_s", "Arsenal vs Chelsea", json.dumps(["Arsenal", "Draw", "Chelsea"]),
             json.dumps(["tok_ars", "tok_draw", "tok_che"])),
        )
        conn.execute("INSERT INTO market_match_mapping VALUES ('mkt_s', 's1', 'match_winner')")
        conn.commit()

        lookup = build_market_lookup(conn)
        assert lookup["tok_ars"].market_type == "match_winner"
        assert lookup["tok_draw"].market_type == "match_winner"
        assert lookup["tok_che"].market_type == "match_winner"


# ---------------------------------------------------------------------------
# link_event_to_tokens
# ---------------------------------------------------------------------------


class TestLinkEventToTokens:
    def test_score_change_with_team(self, market_db):
        lookup = build_market_lookup(market_db)
        tokens = link_event_to_tokens("score_change", "ATL", "nba", lookup)
        # ATL -> Hawks, should return both moneyline tokens for that match
        assert len(tokens) >= 2
        token_ids = set(tokens)
        assert "tok_h" in token_ids or "tok_h2" in token_ids

    def test_foul_returns_all_moneyline(self, market_db):
        lookup = build_market_lookup(market_db)
        tokens = link_event_to_tokens("foul", "ATL", "nba", lookup)
        assert len(tokens) >= 2

    def test_no_team_returns_all_moneyline(self, market_db):
        lookup = build_market_lookup(market_db)
        tokens = link_event_to_tokens("score_change", None, "cbb", lookup)
        assert len(tokens) >= 2

    def test_excludes_over_under(self, market_db):
        lookup = build_market_lookup(market_db)
        tokens = link_event_to_tokens("score_change", "ATL", "nba", lookup)
        assert "tok_ov" not in tokens
        assert "tok_un" not in tokens

    def test_empty_lookup(self):
        tokens = link_event_to_tokens("score_change", "ATL", "nba", {})
        assert tokens == []


# ---------------------------------------------------------------------------
# dedup_events
# ---------------------------------------------------------------------------


class TestDedupEvents:
    def test_deduplicates_period_end_within_60s(self):
        events = [
            {"id": 1, "event_type": "period_end", "server_ts_ms": 1000, "quarter": 1},
            {"id": 2, "event_type": "period_end", "server_ts_ms": 20_000, "quarter": 1},  # 20s later
            {"id": 3, "event_type": "period_end", "server_ts_ms": 200_000, "quarter": 1},  # >60s later
        ]
        result = dedup_events(events)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 3

    def test_does_not_dedup_score_change(self):
        events = [
            {"id": 1, "event_type": "score_change", "server_ts_ms": 1000},
            {"id": 2, "event_type": "score_change", "server_ts_ms": 2000},
        ]
        result = dedup_events(events)
        assert len(result) == 2

    def test_deduplicates_half_end(self):
        events = [
            {"id": 1, "event_type": "half_end", "server_ts_ms": 1000, "quarter": 2},
            {"id": 2, "event_type": "half_end", "server_ts_ms": 30_000, "quarter": 2},
        ]
        result = dedup_events(events)
        assert len(result) == 1

    def test_different_quarters_not_deduped(self):
        events = [
            {"id": 1, "event_type": "period_end", "server_ts_ms": 1000, "quarter": 1},
            {"id": 2, "event_type": "period_end", "server_ts_ms": 2000, "quarter": 2},
        ]
        result = dedup_events(events)
        assert len(result) == 2

    def test_empty_list(self):
        assert dedup_events([]) == []

    def test_mixed_events_preserve_order(self):
        events = [
            {"id": 1, "event_type": "score_change", "server_ts_ms": 1000},
            {"id": 2, "event_type": "period_end", "server_ts_ms": 2000, "quarter": 1},
            {"id": 3, "event_type": "foul", "server_ts_ms": 3000},
            {"id": 4, "event_type": "period_end", "server_ts_ms": 10_000, "quarter": 1},  # within 60s
        ]
        result = dedup_events(events)
        assert len(result) == 3
        assert [e["id"] for e in result] == [1, 2, 3]
