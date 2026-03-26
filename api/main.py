"""FastAPI data layer for the Polymarket analytics dashboard.

Usage:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .queries import get_event_windows, get_signals, list_databases

app = FastAPI(title="Polymarket Dashboard API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
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
):
    """Event-aligned price windows with bps-from-baseline curves."""
    try:
        return get_event_windows(
            name,
            event_type=event_type,
            token_id=token,
            ts_quality=ts_quality,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Database '{name}' not found")
