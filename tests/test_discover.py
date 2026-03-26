"""Tests for discover_markets.py classify_sport()."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.discover_markets import classify_sport


# --- Slug-based classification ---


def test_slug_cbb_prefix():
    """cbb- slug prefix routes to CBB sport with Sports WS game state."""
    sport, source = classify_sport("Dayton vs Illinois State", [], "cbb-dayton-illst-2026-03-25")
    assert sport == "cbb"
    assert source == "polymarket_sports_ws"


def test_slug_cbb_ignores_basketball_keyword():
    """cbb- slug takes priority even if title contains 'basketball'."""
    sport, source = classify_sport("College Basketball: Team A vs B", [{"label": "Basketball"}], "cbb-teama-teamb-2026-03-25")
    assert sport == "cbb"
    assert source == "polymarket_sports_ws"


# --- Keyword fallback ---


def test_keyword_ncaa():
    sport, source = classify_sport("NCAA March Madness: Team A vs B", [], "some-slug")
    assert sport == "cbb"
    assert source == "polymarket_sports_ws"


def test_keyword_march_madness():
    sport, source = classify_sport("March Madness Round of 64", [], "")
    assert sport == "cbb"
    assert source == "polymarket_sports_ws"


def test_keyword_college_basketball():
    sport, source = classify_sport("College Basketball: Duke vs UNC", [], "")
    assert sport == "cbb"
    assert source == "polymarket_sports_ws"


def test_keyword_ncaab():
    sport, source = classify_sport("NCAAB Tournament Game", [], "")
    assert sport == "cbb"
    assert source == "polymarket_sports_ws"


# --- No NBA regression ---


def test_nba_slug_still_works():
    """NBA events with nba- slug still classified as NBA."""
    sport, source = classify_sport("Hawks vs Celtics", [{"label": "Basketball"}], "nba-atl-bos-2026-03-27")
    assert sport == "nba"
    assert source == "nba_cdn"


def test_nba_keyword_no_slug():
    """Legacy: NBA classified by keyword when no slug provided."""
    sport, source = classify_sport("Hawks vs Celtics", [{"label": "Basketball"}], "")
    assert sport == "nba"
    assert source == "nba_cdn"


def test_nba_basketball_tag_only():
    """Basketball tag alone routes to NBA (not CBB)."""
    sport, source = classify_sport("Some Game", [{"label": "Basketball"}], "")
    assert sport == "nba"
    assert source == "nba_cdn"


# --- Other sports unaffected ---


def test_tennis_unaffected():
    sport, source = classify_sport("Djokovic vs Nadal - ATP Finals", [], "")
    assert sport == "tennis"
    assert source == "polymarket_sports_ws"


def test_challenger_keyword():
    """Challenger keyword classifies as tennis."""
    sport, source = classify_sport("Challenger Braga: Rico vs Bertran", [], "")
    assert sport == "tennis"
    assert source == "polymarket_sports_ws"


def test_valorant_challengers_not_tennis():
    """Valorant Challengers must not be misclassified as tennis (ordering defense)."""
    sport, source = classify_sport("Valorant Challengers: Team A vs Team B", ["Valorant"], "")
    assert sport == "valorant"
    assert source == "riot"


def test_unknown_fallback():
    sport, source = classify_sport("Random Event", [], "random-slug")
    assert sport == "unknown"
    assert source == "none"
