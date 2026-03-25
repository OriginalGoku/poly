"""CLI entry point for the data collector.

Usage: python -m collector --config configs/match_example.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import build_token_shards, load_config
from .db import Database
from .game_state.base import GameNotStarted, GameStateClient
from .game_state.registry import IMPLEMENTED_SOURCES
from .game_state.dota2_client import Dota2Client
from .game_state.nba_client import NbaClient
from .game_state.nba_client import lookup_game_id as nba_lookup_game_id
from .game_state.nhl_client import NhlClient
from .game_state.nhl_client import lookup_game_id as nhl_lookup_game_id
from .models import MatchConfig
from .polymarket_client import PolymarketClient
from .settings import get_game_state_poll_lead_minutes
from .ws_client import WebSocketMarketClient, WriteBatch

logger = logging.getLogger("collector")


def truncate_id(val: str, length: int = 12) -> str:
    """Shorten long token IDs / tx hashes for log readability."""
    if len(val) > length + 4:
        return f"{val[:length]}...{val[-4:]}"
    return val


def setup_logging(match_id: str, file_level: str = "INFO") -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"collector_{match_id}_{ts}.log"

    formatter = logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    )

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(getattr(logging, file_level))

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    stderr_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)

    # Silence noisy third-party loggers unless full DEBUG requested
    if getattr(logging, file_level) > logging.DEBUG:
        for noisy in ("aiosqlite", "httpcore", "httpx", "websockets", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    logger.info("Logging to %s (file_level=%s)", log_file, file_level)


async def build_game_state_client(config: MatchConfig) -> GameStateClient | None:
    if config.data_source == "none":
        return None

    if config.data_source == "nba_cdn":
        game_id = config.external_id
        if not game_id:
            logger.info("No external_id — looking up NBA game ID from scoreboard...")
            game_id = await nba_lookup_game_id(config.team1, config.team2)
        if not game_id:
            logger.warning("Could not resolve NBA game ID — skipping game state")
            return None
        return NbaClient(
            match_id=config.match_id,
            game_id=game_id,
            team1=config.team1,
            team2=config.team2,
        )

    if config.data_source == "nhl_api":
        game_id = config.external_id
        if not game_id:
            logger.info("No external_id — looking up NHL game ID from scoreboard...")
            game_id = await nhl_lookup_game_id(config.team1, config.team2)
        if not game_id:
            logger.warning("Could not resolve NHL game ID — skipping game state")
            return None
        return NhlClient(
            match_id=config.match_id,
            game_id=game_id,
            team1=config.team1,
            team2=config.team2,
        )

    if config.data_source == "opendota":
        if not config.external_id:
            logger.warning("Dota 2 match requires external_id (match ID) — skipping game state")
            return None
        return Dota2Client(
            match_id=config.match_id,
            external_match_id=config.external_id,
            team1=config.team1,
            team2=config.team2,
        )

    logger.info(
        "Game state client for '%s' not implemented. Known sources: %s",
        config.data_source,
        ", ".join(sorted(IMPLEMENTED_SOURCES)),
    )
    return None


async def run_ws_client(ws_client: WebSocketMarketClient) -> None:
    await ws_client.run()


async def run_ws_db_writer(queue: asyncio.Queue, db: Database) -> None:
    while True:
        batch = await queue.get()
        # Handle data gap records
        if hasattr(batch, "_gap"):
            gap_start, gap_end, reason = batch._gap
            await db.log_gap("ws_market", gap_start, gap_end, reason)
            continue
        await db.insert_snapshots(batch.snapshots)
        await db.insert_trades(batch.trades)
        await db.insert_price_signals(batch.signals)


async def run_game_state_poller(
    gs_client: GameStateClient, db: Database, scheduled_start: str = ""
) -> None:
    lead_minutes = get_game_state_poll_lead_minutes()

    # --- WAITING state: sleep until scheduled_start - lead_minutes ---
    if scheduled_start:
        try:
            start_dt = datetime.fromisoformat(
                scheduled_start.replace("Z", "+00:00")
            )
            poll_start = start_dt - timedelta(minutes=lead_minutes)
            now = datetime.now(timezone.utc)
            if poll_start > now:
                wait_seconds = (poll_start - now).total_seconds()
                logger.info(
                    "Game state polling delayed until %s UTC (%d min before scheduled start)",
                    poll_start.strftime("%H:%M"),
                    lead_minutes,
                )
                await asyncio.sleep(wait_seconds)
        except (ValueError, TypeError):
            pass  # unparseable — skip to BACKOFF

    # --- BACKOFF state: exponential backoff until first HTTP 200 ---
    backoff_delay = 30.0
    max_backoff = 120.0
    logged_backoff = False
    in_backoff = True

    while in_backoff:
        try:
            events = await gs_client.poll()
            # Success — transition to LIVE
            in_backoff = False
            logger.info("Game state API responding, switching to normal polling")
            if events:
                inserted = await db.insert_match_events(events)
                for e in events:
                    logger.info(
                        "Game event: %s | %s %s-%s",
                        e.event_type,
                        e.event_team or "",
                        e.team1_score,
                        e.team2_score,
                    )
        except GameNotStarted:
            if not logged_backoff:
                logger.info("Game state API not ready, backing off until available")
                logged_backoff = True
            await asyncio.sleep(backoff_delay)
            backoff_delay = min(backoff_delay * 2, max_backoff)
        except Exception:
            logger.exception("Game state poll error during backoff")
            await asyncio.sleep(backoff_delay)
            backoff_delay = min(backoff_delay * 2, max_backoff)

    # --- LIVE state: normal polling ---
    while True:
        try:
            events = await gs_client.poll()
            if events:
                inserted = await db.insert_match_events(events)
                for e in events:
                    logger.info(
                        "Game event: %s | %s %s-%s",
                        e.event_type,
                        e.event_team or "",
                        e.team1_score,
                        e.team2_score,
                    )
        except GameNotStarted:
            logger.warning("Unexpected GameNotStarted in LIVE state")
        except Exception:
            logger.exception("Game state poll error")
        await asyncio.sleep(gs_client.poll_interval_seconds)


async def main(config_path: str, db_path: str | None = None, log_level: str = "INFO") -> None:
    config = load_config(config_path)
    setup_logging(config.match_id, file_level=log_level)

    logger.info(
        "Starting collector for %s vs %s (%s)",
        config.team1,
        config.team2,
        config.sport,
    )
    logger.info(
        "Markets: %d, Tokens: %d",
        len(config.markets),
        sum(len(m.token_ids) for m in config.markets),
    )

    # Database
    if db_path is None:
        db_path = f"data/{config.match_id}.db"
    db = Database(db_path)
    await db.open()

    # Insert match and markets
    await db.insert_match(config)
    token_to_market: dict[str, str] = {}
    token_to_outcome: dict[str, tuple[str, int]] = {}
    all_token_ids: list[str] = []

    for m in config.markets:
        await db.insert_market(
            market_id=m.market_id,
            question=m.question,
            outcomes=m.outcomes,
            token_ids=m.token_ids,
        )
        await db.insert_market_match_mapping(
            market_id=m.market_id,
            match_id=config.match_id,
            relationship=m.relationship,
        )
        for i, tid in enumerate(m.token_ids):
            token_to_market[tid] = m.market_id
            all_token_ids.append(tid)
            if i < len(m.outcomes):
                token_to_outcome[tid] = (m.outcomes[i], i)

    # Fetch market metadata (tick_size, min_order_size)
    pm_client = PolymarketClient(
        token_ids=all_token_ids,
        token_to_market=token_to_market,
    )
    await pm_client.start()

    logger.info("Fetching market metadata...")
    seen_markets: set[str] = set()
    for m in config.markets:
        if m.market_id in seen_markets:
            continue
        seen_markets.add(m.market_id)
        if m.token_ids:
            try:
                meta = await pm_client.fetch_market_metadata(m.token_ids[0])
                await db.update_market_metadata(
                    m.market_id, meta["tick_size"], meta["min_order_size"]
                )
                logger.info(
                    "  %s: tick_size=%.3f, min_order_size=%.1f",
                    m.question[:50],
                    meta["tick_size"],
                    meta["min_order_size"],
                )
            except Exception:
                logger.exception("Failed to fetch metadata for %s", m.market_id[:16])

    # Start collection run
    run_id = await db.start_collection_run(
        match_id=config.match_id,
        sport=config.sport,
        config_json=json.dumps(
            {
                "match_id": config.match_id,
                "sport": config.sport,
                "team1": config.team1,
                "team2": config.team2,
                "markets": len(config.markets),
                "tokens": len(all_token_ids),
                "data_source": config.data_source,
            }
        ),
    )

    # Build game state client
    gs_client = await build_game_state_client(config)
    if gs_client:
        if hasattr(gs_client, "start"):
            await gs_client.start()
        logger.info("Game state client: %s (%s)", gs_client.sport, config.data_source)
    else:
        logger.info("No game state client — order book + trades only")

    # WebSocket client sharding
    shards = build_token_shards(config.markets)
    shared_queue: asyncio.Queue[WriteBatch] = asyncio.Queue()
    ws_clients: list[WebSocketMarketClient] = []

    for shard_name, shard_tokens in shards.items():
        client = WebSocketMarketClient(
            token_ids=shard_tokens,
            token_to_market=token_to_market,
            token_to_outcome=token_to_outcome,
            queue=shared_queue,
            name=shard_name,
        )
        ws_clients.append(client)
        logger.info("WS shard '%s': %d tokens", shard_name, len(shard_tokens))

    # Create async tasks
    tasks: list[asyncio.Task] = []
    shutdown_event = asyncio.Event()

    def handle_signal(sig: int, frame: object) -> None:
        logger.info("Received signal %d, shutting down...", sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    for wsc in ws_clients:
        tasks.append(asyncio.create_task(run_ws_client(wsc), name=f"ws_{wsc.name}"))
    tasks.append(asyncio.create_task(run_ws_db_writer(shared_queue, db), name="ws_db_writer"))

    if gs_client:
        tasks.append(
            asyncio.create_task(
                run_game_state_poller(gs_client, db, config.scheduled_start),
                name="game_state_poller",
            )
        )

    # Status reporting task
    async def status_reporter() -> None:
        while not shutdown_event.is_set():
            await asyncio.sleep(60)
            total_snaps = sum(c.snapshot_count for c in ws_clients)
            total_trades = sum(c.trade_count for c in ws_clients)
            total_signals = sum(c.signal_count for c in ws_clients)
            total_msgs = sum(c.message_count for c in ws_clients)
            logger.info(
                "Status: %d snapshots, %d trades, %d signals, %d events, %d WS msgs (%d shards)",
                total_snaps,
                total_trades,
                total_signals,
                await db.count_events(config.match_id),
                total_msgs,
                len(ws_clients),
            )

    tasks.append(asyncio.create_task(status_reporter(), name="status"))

    logger.info("Collector running (%d WS shards). Press Ctrl+C to stop.", len(ws_clients))

    # Wait for shutdown
    await shutdown_event.wait()

    # Cancel all tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # Cleanup
    for wsc in ws_clients:
        await wsc.stop()
    await pm_client.close()
    if gs_client:
        await gs_client.close()

    # Finalize collection run
    gap_count = await db.count_gaps()
    event_count = await db.count_events(config.match_id)
    signal_count = await db.count_price_signals()
    total_snaps = sum(c.snapshot_count for c in ws_clients)
    total_trades = sum(c.trade_count for c in ws_clients)

    await db.finish_collection_run(
        run_id=run_id,
        snapshot_count=total_snaps,
        trade_count=total_trades,
        event_count=event_count,
        gap_count=gap_count,
    )

    logger.info(
        "Collection complete: %d snapshots, %d trades, %d signals, %d events, %d gaps",
        total_snaps,
        total_trades,
        signal_count,
        event_count,
        gap_count,
    )

    await db.close()


def cli() -> None:
    parser = argparse.ArgumentParser(description="Polymarket live event data collector")
    parser.add_argument("--config", required=True, help="Path to match config JSON")
    parser.add_argument("--db", default=None, help="SQLite database path (default: data/<match_id>.db)")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING"],
        default="INFO",
        help="Log file verbosity (default: INFO)",
    )
    args = parser.parse_args()

    asyncio.run(main(args.config, args.db, log_level=args.log_level))


if __name__ == "__main__":
    cli()
