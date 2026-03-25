"""Tests for config loading and validation."""

import json
import logging
import tempfile
from pathlib import Path

import pytest

from collector.config import build_token_shards, categorize_market, load_config
from collector.models import MarketConfig


@pytest.fixture
def make_config(tmp_path):
    """Write a minimal config JSON and return its path."""
    def _make(sport="nba", data_source="nba_cdn", **overrides):
        data = {
            "match_id": "test-1",
            "sport": sport,
            "team1": "A",
            "team2": "B",
            "data_source": data_source,
            "markets": [
                {
                    "market_id": "0xabc",
                    "question": "A vs B",
                    "outcomes": ["A", "B"],
                    "token_ids": ["0x111", "0x222"],
                }
            ],
            **overrides,
        }
        path = tmp_path / "test_config.json"
        path.write_text(json.dumps(data))
        return path
    return _make


def test_load_config_basic(make_config):
    path = make_config()
    cfg = load_config(path)
    assert cfg.match_id == "test-1"
    assert cfg.sport == "nba"
    assert len(cfg.markets) == 1


def test_warns_nba_without_game_state(make_config, caplog):
    path = make_config(sport="nba", data_source="none")
    with caplog.at_level(logging.WARNING, logger="collector.config"):
        load_config(path)
    assert any("data_source='none'" in msg for msg in caplog.messages)


def test_warns_nhl_without_game_state(make_config, caplog):
    path = make_config(sport="nhl", data_source="none")
    with caplog.at_level(logging.WARNING, logger="collector.config"):
        load_config(path)
    assert any("data_source='none'" in msg for msg in caplog.messages)


def test_no_warning_for_correct_config(make_config, caplog):
    path = make_config(sport="nba", data_source="nba_cdn")
    with caplog.at_level(logging.WARNING, logger="collector.config"):
        load_config(path)
    assert not any("data_source='none'" in msg for msg in caplog.messages)


def test_no_warning_for_non_game_state_sport(make_config, caplog):
    path = make_config(sport="tennis", data_source="none")
    with caplog.at_level(logging.WARNING, logger="collector.config"):
        load_config(path)
    assert not any("data_source='none'" in msg for msg in caplog.messages)


def test_warns_unimplemented_data_source(make_config, caplog):
    """Sport has data_source set to something not in IMPLEMENTED_SOURCES."""
    path = make_config(sport="lol", data_source="riot")
    with caplog.at_level(logging.WARNING, logger="collector.config"):
        load_config(path)
    assert any("not implemented" in msg for msg in caplog.messages)


def test_warns_dota2_without_game_state(make_config, caplog):
    """Dota2 has a client but config says data_source='none'."""
    path = make_config(sport="dota2", data_source="none")
    with caplog.at_level(logging.WARNING, logger="collector.config"):
        load_config(path)
    assert any("data_source='none'" in msg for msg in caplog.messages)


def test_no_warning_implemented_source(make_config, caplog):
    """Correctly configured source should produce no warnings."""
    path = make_config(sport="dota2", data_source="opendota")
    with caplog.at_level(logging.WARNING, logger="collector.config"):
        load_config(path)
    assert not caplog.messages


# ========================================================
# Market categorization tests
# ========================================================


def test_categorize_market_moneyline():
    assert categorize_market("Hawks vs. Pistons") == "core"


def test_categorize_market_spread():
    assert categorize_market("Spread: Pistons (-3.5)") == "core"


def test_categorize_market_game_ou():
    assert categorize_market("Hawks vs. Pistons: O/U 226.5") == "core"


def test_categorize_market_player_prop_points():
    assert categorize_market("LeBron James: Points O/U 27.5") == "prop"


def test_categorize_market_player_prop_assists():
    assert categorize_market("CJ McCollum: Assists O/U 3.5") == "prop"


def test_categorize_market_player_prop_rebounds():
    assert categorize_market("Anthony Davis: Rebounds O/U 10.5") == "prop"


def test_categorize_market_unknown_defaults_core():
    """Unrecognized question patterns should default to core (fail-safe)."""
    assert categorize_market("Some weird market format") == "core"


def test_categorize_market_case_insensitive():
    assert categorize_market("Player Name: points o/u 15.5") == "prop"


# ========================================================
# Token shard building tests
# ========================================================


def _make_market(question: str, token_ids: list[str]) -> MarketConfig:
    return MarketConfig(
        market_id=f"0x{hash(question) % 10**8:08x}",
        question=question,
        relationship="unknown",
        outcomes=["Yes", "No"],
        token_ids=token_ids,
    )


def test_build_token_shards_basic():
    """Core and prop markets split into separate shards."""
    markets = [
        _make_market("Team A vs Team B", ["t1", "t2"]),
        _make_market("Player X: Points O/U 20.5", ["t3", "t4"]),
    ]
    shards = build_token_shards(markets)
    assert "core" in shards
    assert "prop_1" in shards
    assert set(shards["core"]) == {"t1", "t2"}
    assert set(shards["prop_1"]) == {"t3", "t4"}


def test_build_token_shards_respects_max():
    """Shards are split when exceeding max_per_shard."""
    # 6 prop tokens with max_per_shard=2 -> 3 prop shards
    markets = [
        _make_market(f"Player {i}: Points O/U {i}.5", [f"t{i}a", f"t{i}b"])
        for i in range(3)
    ]
    shards = build_token_shards(markets, max_per_shard=2)
    prop_shards = {k: v for k, v in shards.items() if k.startswith("prop")}
    assert len(prop_shards) == 3
    for tokens in prop_shards.values():
        assert len(tokens) <= 2


def test_build_token_shards_small_count():
    """All tokens fit in one shard when under max."""
    markets = [
        _make_market("Team A vs Team B", ["t1", "t2"]),
        _make_market("Spread: Team A (-3.5)", ["t3", "t4"]),
    ]
    shards = build_token_shards(markets)
    assert "core" in shards
    assert len(shards) == 1  # no prop shards
    assert shards["core"] == ["t1", "t2", "t3", "t4"]


def test_build_token_shards_core_split():
    """Core tokens split into numbered shards when exceeding max."""
    markets = [
        _make_market(f"Team {i} vs Team {i+1}", [f"t{i}"])
        for i in range(30)
    ]
    shards = build_token_shards(markets, max_per_shard=10)
    core_shards = {k: v for k, v in shards.items() if k.startswith("core")}
    assert len(core_shards) == 3
    assert all(len(v) <= 10 for v in core_shards.values())
