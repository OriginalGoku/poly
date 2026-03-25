"""SQLite schema creation and write operations."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite

from .models import MatchConfig, MatchEvent, OrderBookSnapshot, PriceSignal, Trade, TradeWatermark

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY,
    condition_id TEXT,
    question TEXT,
    outcomes_json TEXT,
    token_ids_json TEXT,
    market_slug TEXT,
    tick_size REAL,
    min_order_size REAL,
    active BOOLEAN DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS market_match_mapping (
    market_id TEXT REFERENCES markets(market_id),
    match_id TEXT REFERENCES matches(match_id),
    relationship TEXT,
    PRIMARY KEY (market_id, match_id)
);

CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    external_id TEXT,
    sport TEXT,
    team1 TEXT,
    team2 TEXT,
    tournament TEXT,
    best_of INTEGER,
    scheduled_start TEXT,
    actual_start TEXT,
    end_time TEXT,
    status TEXT,
    data_source TEXT,
    has_game_state BOOLEAN DEFAULT 0
);

CREATE TABLE IF NOT EXISTS order_book_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT REFERENCES markets(market_id),
    token_id TEXT,
    local_ts TEXT,
    local_mono_ns INTEGER,
    server_ts_raw TEXT,
    server_ts_ms INTEGER,
    fetch_latency_ms REAL,
    best_bid REAL,
    best_bid_size REAL,
    best_ask REAL,
    best_ask_size REAL,
    mid_price REAL,
    spread REAL,
    bid_depth_json TEXT,
    ask_depth_json TEXT,
    book_depth_usd REAL,
    inside_liquidity_usd REAL,
    is_empty BOOLEAN,
    last_trade_price REAL,
    seconds_since_last_trade REAL,
    imbalance REAL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT,
    token_id TEXT,
    local_ts TEXT,
    server_ts_raw INTEGER,
    server_ts_ms INTEGER,
    transaction_hash TEXT,
    price REAL,
    size REAL,
    side TEXT,
    outcome TEXT,
    outcome_index INTEGER,
    source TEXT DEFAULT 'rest',
    UNIQUE(transaction_hash, token_id, source)
);

CREATE TABLE IF NOT EXISTS trade_watermarks (
    token_id TEXT PRIMARY KEY,
    last_timestamp INTEGER,
    recent_hashes TEXT
);

CREATE TABLE IF NOT EXISTS match_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT REFERENCES matches(match_id),
    local_ts TEXT,
    server_ts_raw TEXT,
    server_ts_ms INTEGER,
    sport TEXT,
    event_type TEXT,
    map_number INTEGER,
    map_name TEXT,
    round_number INTEGER,
    game_number INTEGER,
    quarter INTEGER,
    team1_score INTEGER,
    team2_score INTEGER,
    event_team TEXT,
    ct_team TEXT,
    gold_lead INTEGER,
    building_state INTEGER,
    timestamp_quality TEXT DEFAULT 'server',
    raw_event_json TEXT
);

CREATE TABLE IF NOT EXISTS match_events_enriched (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT REFERENCES matches(match_id),
    event_time_game_seconds INTEGER,
    event_type TEXT,
    raw_event_json TEXT,
    enriched_at TEXT
);

CREATE TABLE IF NOT EXISTS data_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collector TEXT,
    gap_start TEXT,
    gap_end TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT,
    sport TEXT,
    start_time TEXT,
    end_time TEXT,
    config_json TEXT,
    ntp_offset_ms REAL,
    polymarket_snapshot_count INTEGER DEFAULT 0,
    trade_count INTEGER DEFAULT 0,
    match_event_count INTEGER DEFAULT 0,
    gap_count INTEGER DEFAULT 0,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_market_ts ON order_book_snapshots(market_id, local_ts);
CREATE INDEX IF NOT EXISTS idx_obs_token_ts ON order_book_snapshots(token_id, local_ts);
CREATE INDEX IF NOT EXISTS idx_obs_server_ms ON order_book_snapshots(token_id, server_ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_match_ts ON match_events(match_id, local_ts);
CREATE INDEX IF NOT EXISTS idx_events_match_ms ON match_events(match_id, server_ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_sport ON match_events(sport, event_type);
CREATE INDEX IF NOT EXISTS idx_trades_market_ts ON trades(market_id, local_ts);
CREATE INDEX IF NOT EXISTS idx_trades_market_ms ON trades(market_id, server_ts_ms);
CREATE INDEX IF NOT EXISTS idx_trades_dedupe ON trades(transaction_hash, token_id);
CREATE INDEX IF NOT EXISTS idx_matches_sport ON matches(sport, status);

CREATE TABLE IF NOT EXISTS price_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT,
    server_ts_ms INTEGER,
    local_ts TEXT,
    best_bid REAL,
    best_ask REAL,
    mid_price REAL,
    spread REAL,
    event_type TEXT,
    imbalance REAL
);

CREATE INDEX IF NOT EXISTS idx_signals_token_ms ON price_signals(token_id, server_ts_ms);
"""


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(SCHEMA_SQL)
        await self._migrate_schema()
        await self._db.commit()
        logger.info("Database opened: %s", self.db_path)

    async def _migrate_schema(self) -> None:
        """Add columns that may be missing from older DBs."""
        async with self.db.execute("PRAGMA table_info(order_book_snapshots)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "imbalance" not in columns:
            await self.db.execute(
                "ALTER TABLE order_book_snapshots ADD COLUMN imbalance REAL"
            )
            logger.info("Migrated order_book_snapshots: added imbalance column")

        async with self.db.execute("PRAGMA table_info(price_signals)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "imbalance" not in columns:
            await self.db.execute(
                "ALTER TABLE price_signals ADD COLUMN imbalance REAL"
            )
            logger.info("Migrated price_signals: added imbalance column")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not open")
        return self._db

    # --- Match & market setup ---

    async def insert_match(self, config: MatchConfig) -> None:
        await self.db.execute(
            """INSERT OR IGNORE INTO matches
               (match_id, external_id, sport, team1, team2, tournament, best_of,
                scheduled_start, status, data_source, has_game_state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'upcoming', ?, ?)""",
            (
                config.match_id,
                config.external_id,
                config.sport,
                config.team1,
                config.team2,
                config.tournament,
                config.best_of,
                config.scheduled_start,
                config.data_source,
                config.data_source != "none",
            ),
        )
        await self.db.commit()

    async def insert_market(
        self,
        market_id: str,
        question: str,
        outcomes: list[str],
        token_ids: list[str],
        tick_size: float | None = None,
        min_order_size: float | None = None,
    ) -> None:
        await self.db.execute(
            """INSERT OR IGNORE INTO markets
               (market_id, question, outcomes_json, token_ids_json,
                tick_size, min_order_size, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                market_id,
                question,
                json.dumps(outcomes),
                json.dumps(token_ids),
                tick_size,
                min_order_size,
            ),
        )
        await self.db.commit()

    async def update_market_metadata(
        self, market_id: str, tick_size: float, min_order_size: float
    ) -> None:
        await self.db.execute(
            "UPDATE markets SET tick_size=?, min_order_size=? WHERE market_id=?",
            (tick_size, min_order_size, market_id),
        )
        await self.db.commit()

    async def insert_market_match_mapping(
        self, market_id: str, match_id: str, relationship: str
    ) -> None:
        await self.db.execute(
            """INSERT OR IGNORE INTO market_match_mapping
               (market_id, match_id, relationship) VALUES (?, ?, ?)""",
            (market_id, match_id, relationship),
        )
        await self.db.commit()

    # --- Order book snapshots ---

    async def insert_snapshots(self, snapshots: list[OrderBookSnapshot]) -> int:
        if not snapshots:
            return 0
        await self.db.executemany(
            """INSERT INTO order_book_snapshots
               (market_id, token_id, local_ts, local_mono_ns, server_ts_raw,
                server_ts_ms, fetch_latency_ms, best_bid, best_bid_size,
                best_ask, best_ask_size, mid_price, spread, bid_depth_json,
                ask_depth_json, book_depth_usd, inside_liquidity_usd,
                is_empty, last_trade_price, seconds_since_last_trade,
                imbalance)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    s.market_id, s.token_id, s.local_ts, s.local_mono_ns,
                    s.server_ts_raw, s.server_ts_ms, s.fetch_latency_ms,
                    s.best_bid, s.best_bid_size, s.best_ask, s.best_ask_size,
                    s.mid_price, s.spread, s.bid_depth_json, s.ask_depth_json,
                    s.book_depth_usd, s.inside_liquidity_usd, s.is_empty,
                    s.last_trade_price, s.seconds_since_last_trade,
                    s.imbalance,
                )
                for s in snapshots
            ],
        )
        await self.db.commit()
        return len(snapshots)

    # --- Trades ---

    async def insert_trades(self, trades: list[Trade]) -> int:
        if not trades:
            return 0
        inserted = 0
        for t in trades:
            try:
                await self.db.execute(
                    """INSERT INTO trades
                       (market_id, token_id, local_ts, server_ts_raw,
                        server_ts_ms, transaction_hash, price, size, side,
                        outcome, outcome_index, source)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        t.market_id, t.token_id, t.local_ts, t.server_ts_raw,
                        t.server_ts_ms, t.transaction_hash, t.price, t.size,
                        t.side, t.outcome, t.outcome_index, t.source,
                    ),
                )
                inserted += 1
            except aiosqlite.IntegrityError:
                pass  # duplicate (transaction_hash, token_id)
        await self.db.commit()
        return inserted

    # --- Price signals ---

    async def insert_price_signals(self, signals: list[PriceSignal]) -> int:
        if not signals:
            return 0
        await self.db.executemany(
            """INSERT INTO price_signals
               (token_id, server_ts_ms, local_ts, best_bid, best_ask,
                mid_price, spread, event_type, imbalance)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (
                    s.token_id, s.server_ts_ms, s.local_ts, s.best_bid,
                    s.best_ask, s.mid_price, s.spread, s.event_type,
                    s.imbalance,
                )
                for s in signals
            ],
        )
        await self.db.commit()
        return len(signals)

    async def count_price_signals(self) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM price_signals") as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    # --- Trade watermarks ---

    async def get_watermark(self, token_id: str) -> TradeWatermark | None:
        async with self.db.execute(
            "SELECT last_timestamp, recent_hashes FROM trade_watermarks WHERE token_id=?",
            (token_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return TradeWatermark(
                token_id=token_id,
                last_timestamp=row[0],
                recent_hashes=json.loads(row[1]) if row[1] else [],
            )

    async def set_watermark(self, wm: TradeWatermark) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO trade_watermarks
               (token_id, last_timestamp, recent_hashes)
               VALUES (?, ?, ?)""",
            (wm.token_id, wm.last_timestamp, json.dumps(wm.recent_hashes)),
        )
        await self.db.commit()

    # --- Match events ---

    async def insert_match_events(self, events: list[MatchEvent]) -> int:
        if not events:
            return 0
        await self.db.executemany(
            """INSERT INTO match_events
               (match_id, local_ts, server_ts_raw, server_ts_ms, sport,
                event_type, map_number, map_name, round_number, game_number,
                quarter, team1_score, team2_score, event_team, ct_team,
                gold_lead, building_state, timestamp_quality, raw_event_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    e.match_id, e.local_ts, e.server_ts_raw, e.server_ts_ms,
                    e.sport, e.event_type, e.map_number, e.map_name,
                    e.round_number, e.game_number, e.quarter, e.team1_score,
                    e.team2_score, e.event_team, e.ct_team, e.gold_lead,
                    e.building_state, e.timestamp_quality, e.raw_event_json,
                )
                for e in events
            ],
        )
        await self.db.commit()
        return len(events)

    # --- Data gaps ---

    async def log_gap(self, collector: str, gap_start: str, gap_end: str, reason: str) -> None:
        await self.db.execute(
            "INSERT INTO data_gaps (collector, gap_start, gap_end, reason) VALUES (?,?,?,?)",
            (collector, gap_start, gap_end, reason),
        )
        await self.db.commit()

    # --- Collection runs ---

    async def start_collection_run(
        self, match_id: str, sport: str, config_json: str
    ) -> int:
        cursor = await self.db.execute(
            """INSERT INTO collection_runs
               (match_id, sport, start_time, config_json)
               VALUES (?, ?, datetime('now'), ?)""",
            (match_id, sport, config_json),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def finish_collection_run(
        self,
        run_id: int,
        snapshot_count: int,
        trade_count: int,
        event_count: int,
        gap_count: int,
        notes: str = "",
    ) -> None:
        await self.db.execute(
            """UPDATE collection_runs SET
               end_time=datetime('now'),
               polymarket_snapshot_count=?,
               trade_count=?,
               match_event_count=?,
               gap_count=?,
               notes=?
               WHERE id=?""",
            (snapshot_count, trade_count, event_count, gap_count, notes, run_id),
        )
        await self.db.commit()

    # --- Query helpers (for verification) ---

    async def count_snapshots(self, match_id: str | None = None) -> int:
        if match_id:
            sql = """SELECT COUNT(*) FROM order_book_snapshots obs
                     JOIN market_match_mapping mmm ON obs.market_id = mmm.market_id
                     WHERE mmm.match_id = ?"""
            async with self.db.execute(sql, (match_id,)) as cur:
                row = await cur.fetchone()
        else:
            async with self.db.execute("SELECT COUNT(*) FROM order_book_snapshots") as cur:
                row = await cur.fetchone()
        return row[0] if row else 0

    async def count_trades(self) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM trades") as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def count_events(self, match_id: str | None = None) -> int:
        if match_id:
            async with self.db.execute(
                "SELECT COUNT(*) FROM match_events WHERE match_id=?", (match_id,)
            ) as cur:
                row = await cur.fetchone()
        else:
            async with self.db.execute("SELECT COUNT(*) FROM match_events") as cur:
                row = await cur.fetchone()
        return row[0] if row else 0

    async def count_gaps(self) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM data_gaps") as cur:
            row = await cur.fetchone()
        return row[0] if row else 0
