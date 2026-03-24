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
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .db import Database
from .game_state.base import GameStateClient
from .game_state.dota2_client import Dota2Client
from .game_state.nba_client import NbaClient
from .models import MatchConfig
from .polymarket_client import PolymarketClient

logger = logging.getLogger("collector")


def setup_logging(match_id: str) -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"collector_{match_id}_{ts}.log"

    formatter = logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    )

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    stderr_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)

    logger.info("Logging to %s", log_file)


def build_game_state_client(config: MatchConfig) -> GameStateClient | None:
    if config.data_source == "none":
        return None

    if config.data_source == "nba_cdn":
        if not config.external_id:
            logger.warning("NBA game requires external_id (game ID) — skipping game state")
            return None
        return NbaClient(
            match_id=config.match_id,
            game_id=config.external_id,
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

    logger.info("Game state client for %s not yet implemented", config.data_source)
    return None


async def run_book_poller(client: PolymarketClient) -> None:
    await client.poll_books()


async def run_trade_poller(client: PolymarketClient) -> None:
    await client.poll_trades()


async def run_game_state_poller(
    gs_client: GameStateClient, db: Database
) -> None:
    event_count = 0
    while True:
        try:
            events = await gs_client.poll()
            if events:
                inserted = await db.insert_match_events(events)
                event_count += inserted
                for e in events:
                    logger.info(
                        "Game event: %s | %s %s-%s",
                        e.event_type,
                        e.event_team or "",
                        e.team1_score,
                        e.team2_score,
                    )
        except Exception:
            logger.exception("Game state poll error")
        await asyncio.sleep(gs_client.poll_interval_seconds)


async def main(config_path: str, db_path: str | None = None) -> None:
    config = load_config(config_path)
    setup_logging(config.match_id)

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
        for tid in m.token_ids:
            token_to_market[tid] = m.market_id
            all_token_ids.append(tid)

    # Fetch market metadata (tick_size, min_order_size)
    pm_client = PolymarketClient(
        db=db,
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
    gs_client = build_game_state_client(config)
    if gs_client:
        if hasattr(gs_client, "start"):
            await gs_client.start()
        logger.info("Game state client: %s (%s)", gs_client.sport, config.data_source)
    else:
        logger.info("No game state client — order book + trades only")

    # Create async tasks
    tasks: list[asyncio.Task] = []
    shutdown_event = asyncio.Event()

    def handle_signal(sig: int, frame: object) -> None:
        logger.info("Received signal %d, shutting down...", sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    tasks.append(asyncio.create_task(run_book_poller(pm_client), name="book_poller"))
    tasks.append(asyncio.create_task(run_trade_poller(pm_client), name="trade_poller"))

    if gs_client:
        tasks.append(
            asyncio.create_task(
                run_game_state_poller(gs_client, db), name="game_state_poller"
            )
        )

    # Status reporting task
    async def status_reporter() -> None:
        while not shutdown_event.is_set():
            await asyncio.sleep(60)
            logger.info(
                "Status: %d snapshots, %d trades, %d events",
                pm_client.snapshot_count,
                pm_client.trade_count,
                await db.count_events(config.match_id),
            )

    tasks.append(asyncio.create_task(status_reporter(), name="status"))

    logger.info("Collector running. Press Ctrl+C to stop.")

    # Wait for shutdown
    await shutdown_event.wait()

    # Cancel all tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # Cleanup
    await pm_client.close()
    if gs_client:
        await gs_client.close()

    # Finalize collection run
    gap_count = await db.count_gaps()
    event_count = await db.count_events(config.match_id)

    await db.finish_collection_run(
        run_id=run_id,
        snapshot_count=pm_client.snapshot_count,
        trade_count=pm_client.trade_count,
        event_count=event_count,
        gap_count=gap_count,
    )

    logger.info(
        "Collection complete: %d snapshots, %d trades, %d events, %d gaps",
        pm_client.snapshot_count,
        pm_client.trade_count,
        event_count,
        gap_count,
    )

    await db.close()


def cli() -> None:
    parser = argparse.ArgumentParser(description="Polymarket live event data collector")
    parser.add_argument("--config", required=True, help="Path to match config JSON")
    parser.add_argument("--db", default=None, help="SQLite database path (default: data/<match_id>.db)")
    args = parser.parse_args()

    asyncio.run(main(args.config, args.db))


if __name__ == "__main__":
    cli()
