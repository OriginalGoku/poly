"""FastAPI data layer for the Polymarket analytics dashboard.

Usage:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .queries import (
    get_event_windows,
    get_game_timeline,
    get_heatmap,
    get_signals,
    get_spike_candidates,
    list_databases,
)

app = FastAPI(title="Polymarket Dashboard API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/databases")
def databases():
    """List all SQLite databases in data/ with lightweight stats."""
    return list_databases()


@app.get("/db/{name}/signals")
def signals(
    name: str,
    token: str | None = Query(None, description="Filter by token_id"),
    start: int | None = Query(None, description="Start timestamp (ms)"),
    end: int | None = Query(None, description="End timestamp (ms)"),
    limit: int = Query(10_000, le=100_000),
):
    """Price signals for a database, optionally filtered by token and time range."""
    try:
        return get_signals(name, token_id=token, start_ms=start, end_ms=end, limit=limit)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Database '{name}' not found")


@app.get("/db/{name}/event-windows")
def event_windows(
    name: str,
    event_type: str | None = Query(None, description="Filter by event type"),
    token: str | None = Query(None, description="Filter by token_id"),
    ts_quality: str | None = Query(None, description="Filter by timestamp quality (server|local)"),
    smart_link: bool = Query(False, description="Use smart event-to-token linking (moneyline focus)"),
):
    """Event-aligned price windows with bps-from-baseline curves."""
    try:
        return get_event_windows(
            name,
            event_type=event_type,
            token_id=token,
            ts_quality=ts_quality,
            smart_link=smart_link,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Database '{name}' not found")


@app.get("/db/{name}/heatmap")
def heatmap(
    name: str,
    metric: str = Query("displacement", description="displacement or reversion"),
    min_signals: int = Query(20, description="Minimum signals per window for inclusion"),
):
    """Overreaction heatmap: event types x time offsets with median bps values."""
    try:
        return get_heatmap(name, metric=metric, min_signals=min_signals)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Database '{name}' not found")


@app.get("/db/{name}/spike-candidates")
def spike_candidates(
    name: str,
    sort: str = Query("reversion_pct", description="Sort field"),
    min_displacement: float = Query(0, description="Minimum peak displacement in bps"),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
):
    """Spike candidate table: events with large displacement and reversion."""
    try:
        return get_spike_candidates(
            name, sort=sort, min_displacement=min_displacement,
            limit=limit, offset=offset,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Database '{name}' not found")


@app.get("/db/{name}/game-timeline")
def game_timeline(
    name: str,
    token: str | None = Query(None, description="Token ID (default: most active moneyline)"),
):
    """Full-game price timeline with events and period boundaries."""
    try:
        return get_game_timeline(name, token_id=token)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Database '{name}' not found")
