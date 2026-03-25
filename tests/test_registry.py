"""Tests for the game state data source registry."""

from collector.game_state.registry import (
    ASPIRATIONAL_SOURCES,
    CONTROL_GROUP_SPORTS,
    IMPLEMENTED_SOURCES,
    SPORTS_WITH_GAME_STATE,
)


def test_implemented_sources_non_empty():
    assert len(IMPLEMENTED_SOURCES) > 0


def test_sports_with_game_state_derived():
    expected = {v["sport"] for v in IMPLEMENTED_SOURCES.values()}
    assert SPORTS_WITH_GAME_STATE == expected


def test_no_overlap_implemented_aspirational():
    assert not set(IMPLEMENTED_SOURCES) & ASPIRATIONAL_SOURCES


def test_all_entries_have_required_keys():
    for name, entry in IMPLEMENTED_SOURCES.items():
        assert "sport" in entry, f"{name} missing 'sport'"
        assert "module" in entry, f"{name} missing 'module'"
        assert "has_lookup" in entry, f"{name} missing 'has_lookup'"
