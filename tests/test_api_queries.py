"""Tests for api.queries — pure helpers and query functions with fixture DBs."""

import json
import sqlite3
from pathlib import Path

import pytest

from api.queries import (
    ALIGNMENT_VERSION,
    LOCAL_QUALITY_PAD_MS,
    WINDOW_AFTER_MS,
    WINDOW_BEFORE_MS,
    _build_token_labels,
    _guess_sport,
    get_event_windows,
    get_signals,
    list_databases,
)


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


class TestGuessSport:
    def test_nba(self):
        assert _guess_sport("nba-atl-det-2026-03-25") == "nba"

    def test_nhl(self):
        assert _guess_sport("nhl-bos-buf-2026-03-25") == "nhl"

    def test_cricket_crint(self):
        assert _guess_sport("crint-afg-lka-2026-03-25") == "crint"

    def test_cricket_criclcl(self):
        assert _guess_sport("criclcl-3rd-4th-2026-03-25") == "criclcl"

    def test_cbb(self):
        assert _guess_sport("cbb-duke-unc") == "cbb"

    def test_unknown(self):
        assert _guess_sport("random-name") == "unknown"

    def test_empty(self):
        assert _guess_sport("") == "unknown"


class TestBuildTokenLabels:
    def test_parses_markets_table(self, fixture_db):
        conn = sqlite3.connect(str(fixture_db))
        labels = _build_token_labels(conn)
        conn.close()
        assert labels["tok_a1"] == "Yes (Team A vs Team B)"
        assert labels["tok_a2"] == "No (Team A vs Team B)"

    def test_empty_markets(self, tmp_path):
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE markets (question TEXT, outcomes_json TEXT, token_ids_json TEXT)")
        conn.commit()
        labels = _build_token_labels(conn)
        conn.close()
        assert labels == {}

    def test_missing_table(self, tmp_path):
        db_path = tmp_path / "nope.db"
        conn = sqlite3.connect(str(db_path))
        labels = _build_token_labels(conn)
        conn.close()
        assert labels == {}


# ---------------------------------------------------------------------------
# Fixture: in-memory DB with schema matching collector/db.py
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    """Create a minimal test DB and point DATA_DIR to tmp_path."""
    import api.queries as mod
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)

    db_path = tmp_path / "test-game.db"
    conn = sqlite3.connect(str(db_path))

    conn.executescript("""
        CREATE TABLE markets (
            market_id TEXT PRIMARY KEY,
            question TEXT,
            outcomes_json TEXT,
            token_ids_json TEXT
        );
        CREATE TABLE matches (
            match_id TEXT PRIMARY KEY,
            sport TEXT
        );
        CREATE TABLE price_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT,
            server_ts_ms INTEGER,
            best_bid REAL,
            best_ask REAL,
            mid_price REAL,
            spread REAL,
            event_type TEXT,
            imbalance REAL
        );
        CREATE TABLE match_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            local_ts TEXT,
            server_ts_raw TEXT,
            server_ts_ms INTEGER,
            sport TEXT,
            event_type TEXT,
            team1_score INTEGER,
            team2_score INTEGER,
            event_team TEXT,
            timestamp_quality TEXT DEFAULT 'server',
            raw_event_json TEXT,
            map_number INTEGER, map_name TEXT, round_number INTEGER,
            game_number INTEGER, quarter INTEGER, ct_team TEXT,
            gold_lead INTEGER, building_state INTEGER
        );
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT,
            server_ts_ms INTEGER
        );
        CREATE INDEX idx_signals_token_ms ON price_signals(token_id, server_ts_ms);
    """)

    # Markets
    conn.execute(
        "INSERT INTO markets VALUES (?, ?, ?, ?)",
        ("mkt1", "Team A vs Team B", json.dumps(["Yes", "No"]), json.dumps(["tok_a1", "tok_a2"])),
    )
    conn.execute("INSERT INTO matches VALUES ('game1', 'nba')")

    # Price signals for tok_a1: 10 points around ts=1000000
    base_ts = 1_000_000
    for i in range(20):
        ts = base_ts + i * 1000  # 1s apart
        mid = 0.50 + (i % 5) * 0.01
        conn.execute(
            "INSERT INTO price_signals (token_id, server_ts_ms, best_bid, best_ask, mid_price, spread) VALUES (?,?,?,?,?,?)",
            ("tok_a1", ts, mid - 0.005, mid + 0.005, mid, 0.01),
        )

    # Match events
    conn.execute(
        "INSERT INTO match_events (match_id, server_ts_ms, sport, event_type, team1_score, team2_score, event_team, timestamp_quality) VALUES (?,?,?,?,?,?,?,?)",
        ("game1", base_ts + 10_000, "nba", "score_change", 2, 0, "A", "server"),
    )
    conn.execute(
        "INSERT INTO match_events (match_id, server_ts_ms, sport, event_type, team1_score, team2_score, event_team, timestamp_quality) VALUES (?,?,?,?,?,?,?,?)",
        ("game1", base_ts + 15_000, "nhl", "score_change", 1, 0, "B", "local"),
    )

    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Query function tests
# ---------------------------------------------------------------------------


class TestListDatabases:
    def test_finds_dbs(self, fixture_db, monkeypatch):
        result = list_databases()
        assert len(result) == 1
        assert result[0]["name"] == "test-game"
        assert result[0]["sport"] == "nba"
        assert result[0]["match_events"] == 2
        assert result[0]["price_signals"] == 20

    def test_empty_dir(self, tmp_path, monkeypatch):
        import api.queries as mod
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
        assert list_databases() == []


class TestGetSignals:
    def test_returns_all_signals(self, fixture_db):
        result = get_signals("test-game")
        assert result["count"] == 20
        assert "tok_a1" in result["tokens"]
        assert result["token_labels"]["tok_a1"] == "Yes (Team A vs Team B)"

    def test_filter_by_token(self, fixture_db):
        result = get_signals("test-game", token_id="tok_a1")
        assert result["count"] == 20
        assert all(s["token_id"] == "tok_a1" for s in result["signals"])

    def test_filter_by_time_range(self, fixture_db):
        result = get_signals("test-game", start_ms=1_005_000, end_ms=1_010_000)
        for s in result["signals"]:
            assert 1_005_000 <= s["server_ts_ms"] <= 1_010_000

    def test_limit(self, fixture_db):
        result = get_signals("test-game", limit=3)
        assert result["count"] == 3

    def test_missing_db_raises(self, fixture_db):
        with pytest.raises(FileNotFoundError):
            get_signals("nonexistent")


class TestGetEventWindows:
    def test_returns_windows_with_alignment_version(self, fixture_db):
        result = get_event_windows("test-game")
        assert result["alignment_version"] == ALIGNMENT_VERSION
        assert result["event_count"] == 2

    def test_filter_by_event_type(self, fixture_db):
        result = get_event_windows("test-game", event_type="score_change")
        assert result["event_count"] == 2
        assert all(w["event_type"] == "score_change" for w in result["windows"])

    def test_filter_by_ts_quality(self, fixture_db):
        result = get_event_windows("test-game", ts_quality="local")
        assert result["event_count"] == 1
        w = result["windows"][0]
        assert w["timestamp_quality"] == "local"

    def test_local_quality_widens_window(self, fixture_db):
        result = get_event_windows("test-game", ts_quality="local")
        w = result["windows"][0]
        assert w["window_before_ms"] == WINDOW_BEFORE_MS + LOCAL_QUALITY_PAD_MS
        assert w["window_after_ms"] == WINDOW_AFTER_MS + LOCAL_QUALITY_PAD_MS

    def test_server_quality_uses_default_window(self, fixture_db):
        result = get_event_windows("test-game", ts_quality="server")
        w = result["windows"][0]
        assert w["window_before_ms"] == WINDOW_BEFORE_MS
        assert w["window_after_ms"] == WINDOW_AFTER_MS

    def test_bps_from_baseline(self, fixture_db):
        result = get_event_windows("test-game", token_id="tok_a1", ts_quality="server")
        w = result["windows"][0]
        assert len(w["token_curves"]) > 0
        curve = w["token_curves"][0]
        # First point should be ~0 bps (or close to it) since it's the baseline
        if curve["points"]:
            assert curve["points"][0]["bps"] == 0.0

    def test_no_events_returns_empty(self, fixture_db):
        result = get_event_windows("test-game", event_type="timeout")
        assert result["event_count"] == 0
        assert result["windows"] == []

    def test_missing_db_raises(self, fixture_db):
        with pytest.raises(FileNotFoundError):
            get_event_windows("nonexistent")
