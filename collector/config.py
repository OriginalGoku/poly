"""Config file loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

from .models import MarketConfig, MatchConfig


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

    return MatchConfig(
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
