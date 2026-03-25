"""Config file loading and validation."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .game_state.registry import IMPLEMENTED_SOURCES, SPORTS_WITH_GAME_STATE
from .models import MarketConfig, MatchConfig

logger = logging.getLogger(__name__)


def load_config(path: str | Path) -> MatchConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        data = json.load(f)

    required = ["match_id", "sport", "team1", "team2", "markets"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"Config missing required fields: {missing}")

    markets = []
    for m in data["markets"]:
        markets.append(
            MarketConfig(
                market_id=m["market_id"],
                question=m.get("question", ""),
                relationship=m.get("relationship", "unknown"),
                outcomes=m.get("outcomes", []),
                token_ids=m.get("token_ids", []),
            )
        )

    if not markets:
        raise ValueError("Config must have at least one market")

    all_tokens = [tid for m in markets for tid in m.token_ids]
    if not all_tokens:
        raise ValueError("No token_ids found in any market")

    config = MatchConfig(
        match_id=data["match_id"],
        sport=data["sport"],
        team1=data["team1"],
        team2=data["team2"],
        tournament=data.get("tournament", ""),
        best_of=data.get("best_of"),
        scheduled_start=data.get("scheduled_start", ""),
        data_source=data.get("data_source", "none"),
        markets=markets,
        external_id=data.get("external_id", ""),
        polymarket_event_slug=data.get("polymarket_event_slug", ""),
        polymarket_volume=data.get("polymarket_volume", 0.0),
    )

    if config.sport in SPORTS_WITH_GAME_STATE and config.data_source == "none":
        logger.warning(
            "Config %s has sport=%s but data_source='none' — "
            "game state events will not be collected. "
            "Set data_source to enable game state tracking.",
            path.name,
            config.sport,
        )
    elif config.data_source not in IMPLEMENTED_SOURCES and config.data_source != "none":
        logger.warning(
            "Config %s has data_source='%s' which is not implemented — "
            "game state will not be collected. Implemented sources: %s",
            path.name,
            config.data_source,
            ", ".join(sorted(IMPLEMENTED_SOURCES)),
        )

    return config


# Player prop stat keywords — matches "PlayerName: Stat O/U N.N"
_PROP_PATTERN = re.compile(
    r"^.+:\s+(?:Points|Rebounds|Assists|Steals|Blocks|Threes|Strikeouts|Hits|"
    r"Home Runs|Passing Yards|Rushing Yards|Receiving Yards|Touchdowns|"
    r"Goals|Saves|Shots|Fantasy Score|Aces|Double Faults|Games Won)\s+O/U\b",
    re.IGNORECASE,
)


def categorize_market(question: str) -> str:
    """Categorize a market question as 'core' or 'prop'.

    Core: moneyline, spread, game O/U, 1H lines — liquid, hypothesis-relevant.
    Prop: player stat props — thin liquidity, not critical for analysis.
    Unrecognized patterns default to 'core' as a fail-safe.
    """
    if _PROP_PATTERN.match(question):
        return "prop"
    return "core"


def build_token_shards(
    markets: list[MarketConfig], max_per_shard: int = 25
) -> dict[str, list[str]]:
    """Group market tokens into shards for WS connection splitting.

    Returns a dict of shard_name -> list of token_ids.
    Core markets get priority (stable connection), props are secondary.
    """
    core_tokens: list[str] = []
    prop_tokens: list[str] = []

    for m in markets:
        category = categorize_market(m.question)
        if category == "prop":
            prop_tokens.extend(m.token_ids)
        else:
            core_tokens.extend(m.token_ids)

    shards: dict[str, list[str]] = {}

    # Core shards
    if core_tokens:
        if len(core_tokens) <= max_per_shard:
            shards["core"] = core_tokens
        else:
            for i in range(0, len(core_tokens), max_per_shard):
                chunk = core_tokens[i : i + max_per_shard]
                shard_num = i // max_per_shard + 1
                shards[f"core_{shard_num}"] = chunk

    # Prop shards
    if prop_tokens:
        for i in range(0, len(prop_tokens), max_per_shard):
            chunk = prop_tokens[i : i + max_per_shard]
            shard_num = i // max_per_shard + 1
            shards[f"prop_{shard_num}"] = chunk

    return shards
