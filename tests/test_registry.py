"""Tests for the game state data source registry."""

from collector.game_state.registry import (
    ASPIRATIONAL_SOURCES,
    CONTROL_GROUP_SPORTS,
    IMPLEMENTED_SOURCES,
    SPORTS_WITH_GAME_STATE,
    SPORTS_WS_SPORTS,
)


def test_implemented_sources_non_empty():
    assert len(IMPLEMENTED_SOURCES) >= 4  # nba_cdn, nhl_api, opendota, polymarket_sports_ws


def test_sports_with_game_state_includes_polling_clients():
    polling_sports = {v["sport"] for v in IMPLEMENTED_SOURCES.values() if v["sport"] != "multi"}
    assert polling_sports.issubset(SPORTS_WITH_GAME_STATE)


def test_sports_with_game_state_includes_ws_sports():
    assert SPORTS_WS_SPORTS.issubset(SPORTS_WITH_GAME_STATE)


def test_polymarket_sports_ws_in_implemented():
    assert "polymarket_sports_ws" in IMPLEMENTED_SOURCES


def test_no_overlap_implemented_aspirational():
    assert not set(IMPLEMENTED_SOURCES) & ASPIRATIONAL_SOURCES


def test_control_group_excludes_ws_sports():
    assert not CONTROL_GROUP_SPORTS & SPORTS_WS_SPORTS


def test_all_entries_have_required_keys():
    for name, entry in IMPLEMENTED_SOURCES.items():
        assert "sport" in entry, f"{name} missing 'sport'"
        assert "module" in entry, f"{name} missing 'module'"
        assert "has_lookup" in entry, f"{name} missing 'has_lookup'"
