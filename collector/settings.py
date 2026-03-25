"""Project settings loaded from settings.json."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"
_DEFAULT_LEAD_MINUTES = 30

_settings: dict = {}

try:
    _settings = json.loads(_SETTINGS_PATH.read_text())
except (FileNotFoundError, json.JSONDecodeError):
    pass


def get_game_state_poll_lead_minutes() -> int:
    """Return minutes before scheduled_start to begin game-state polling."""
    try:
        val = _settings["game_state_poll_lead_minutes"]["value"]
        return int(val)
    except (KeyError, TypeError, ValueError):
        return _DEFAULT_LEAD_MINUTES
