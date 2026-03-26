"""Central registry of implemented game state data sources.

Single source of truth — imported by config.py, __main__.py, and discover_markets.py.
"""

from __future__ import annotations

# Data sources with working client implementations.
# Keys are data_source strings used in match config JSON files.
IMPLEMENTED_SOURCES: dict[str, dict] = {
    "nba_cdn": {"sport": "nba", "module": "nba_client", "has_lookup": True},
    "nhl_api": {"sport": "nhl", "module": "nhl_client", "has_lookup": True},
    "opendota": {"sport": "dota2", "module": "dota2_client", "has_lookup": False},
    "polymarket_sports_ws": {"sport": "multi", "module": "sports_ws_client", "has_lookup": False},
}

# Sports covered by the Polymarket Sports WebSocket (live game state broadcast).
SPORTS_WS_SPORTS: set[str] = {"tennis", "mlb", "soccer", "cricket", "cs2", "valorant", "lol", "cbb"}

# Sports collected for order-book/trade data only (no game state client planned or available).
CONTROL_GROUP_SPORTS: set[str] = {"ufc", "nfl"}

# Sports that have at least one implemented game state client.
# Includes polling clients + Sports WS sports.
SPORTS_WITH_GAME_STATE: set[str] = (
    {v["sport"] for v in IMPLEMENTED_SOURCES.values() if v["sport"] != "multi"}
    | SPORTS_WS_SPORTS
)

# Data sources referenced in configs but not yet implemented.
ASPIRATIONAL_SOURCES: set[str] = {"pandascore", "riot"}
