# Sports WebSocket Game State Client

> Connect to Polymarket's Sports API WebSocket to capture live game state (scores, periods, game start/end) for sports that lack dedicated polling clients — tennis, MLB, soccer, cricket, CS2, Valorant, LoL.

---

## Problem Statement

The collector has per-sport polling clients for NBA, NHL, and Dota2, but **zero game state collection** for tennis (ATP/WTA), MLB, soccer, cricket, CS2, Valorant, and LoL. Polymarket operates a Sports API WebSocket at `wss://sports-api.polymarket.com/ws` that broadcasts live game state for ALL sports with no auth or subscription required. A research spike (`scripts/ws_research_spike.py`) proved it works. The decision to use this WS was made in `executed_plans/Multi_Sport_Data_Registry_Plan.md` (decision D2) but never implemented.

## Design Decisions

### D1: Event-driven client, NOT a GameStateClient subclass

**Decision:** `WebSocketSportsClient` follows the `WebSocketMarketClient` pattern (persistent connection, reconnect with backoff, queue-based DB writing). It does NOT inherit from the `GameStateClient` ABC.

**Rationale:** `GameStateClient` is poll-based (`poll()` + `close()`). The Sports WS is event-driven — a persistent connection that receives pushes. The Market WS pattern is the correct architectural fit.

### D2: Dedicated MatchEvent queue, not shared WriteBatch queue

**Decision:** The Sports WS client uses its own `asyncio.Queue[list[MatchEvent]]` with a dedicated writer coroutine, separate from the Market WS `WriteBatch` queue.

**Rationale:** `MatchEvent` is a different type than `WriteBatch` (snapshots/trades/signals). Mixing them would require modifying `WriteBatch` and the existing writer. A separate queue is simpler and doesn't touch working code.

### D3: League filter + fuzzy team match + gameId lock-on

**Decision:** Match messages to our collection in three stages:
1. **Filter by `leagueAbbreviation`** using a mapping (e.g., `{"nba": ["nba"], "tennis": ["atp", "wta"], "mlb": ["mlb"]}`)
2. **Fuzzy match `homeTeam`/`awayTeam`** against config `team1`/`team2` (case-insensitive substring/token matching)
3. **Lock to `gameId`** after 2 consecutive matches with the same gameId and consistent team names — then stop fuzzy matching entirely

**Rationale:** The Sports WS has no `slug` or `match_id` field (LESSONS_LEARNED.md). Fuzzy matching alone is brittle across leagues. League filtering narrows the search space; gameId lock-on makes it deterministic after initial match. Requiring 2 consecutive matches prevents locking to a wrong game on a fluke.

**Trade-off:** A REST lookup for `gameId` was considered but rejected — no documented Polymarket API exists for this resolution.

### D4: Best-effort score parsing

**Decision:** Try strict `"X-Y"` numeric parse. If it fails (tennis sets, cricket runs/wickets), set `team1_score`/`team2_score` to `None` and still emit the event with `raw_event_json`.

**Rationale:** Score formats vary by sport. Partial data (event_type + timestamp + raw JSON) is still valuable for price-event correlation. Don't let parsing failures drop events.

### D5: Separate lifecycle from build_game_state_client()

**Decision:** `build_game_state_client()` returns `None` for `"polymarket_sports_ws"` with a distinct info log. The Sports WS client is created and launched directly in `main()`.

**Rationale:** `build_game_state_client()` builds polling clients. The Sports WS is a different paradigm. Forcing it through that function would require awkward abstractions. Keeping them separate is cleaner.

### D6: Timestamp fallback

**Decision:** Use `eventState.updatedAt` as `server_ts_ms` with `timestamp_quality="server"`. If `updatedAt` is missing or unparseable, use local time with `timestamp_quality="local"`.

**Rationale:** Missing timestamps shouldn't drop events. The quality field lets downstream analysis know which timestamps are reliable.

## Implementation Plan

### Step 1: Create `collector/sports_ws_client.py`

New file. Core class: `WebSocketSportsClient`.

**Constructor args:**
- `match_id: str`, `sport: str`, `team1: str`, `team2: str`
- `queue: asyncio.Queue[list[MatchEvent]]`

**Constants:**
- `SPORTS_WS_URL = "wss://sports-api.polymarket.com/ws"`
- `RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30]`
- `LEAGUE_MAP`: sport → list of leagueAbbreviation values (e.g., `"tennis": ["atp", "wta"]`, `"nba": ["nba"]`, `"mlb": ["mlb"]`, `"soccer": ["epl", "ucl", "laliga", "seriea", "bundesliga", "mls", "fifa"]`, `"cricket": ["ipl", "t20", "cricket"]`, `"cs2": ["cs2", "csgo"]`, `"valorant": ["valorant", "vct"]`, `"lol": ["lol", "lck", "lpl", "lcs", "lec"]`)
  - Start permissive; tighten as we collect more samples

**Key methods:**
- `run()` — main loop: connect, receive, reconnect on failure (mirrors `WebSocketMarketClient.run()`)
- `stop()` — signal stop, close WS
- `_connect_and_receive()` — connect to WS, enter receive loop
- `_receive_loop(ws)` — receive messages, handle ping/pong (text `"ping"` → respond `"pong"`), parse JSON, filter and process
- `_matches_our_game(data) -> bool` — league filter + fuzzy team match (only runs before gameId lock-on)
- `_process_message(data)` — compare to last known state, detect changes, emit MatchEvents
- `_parse_score(score_str) -> tuple[int|None, int|None]` — best-effort "X-Y" parse
- `_parse_server_ts(updated_at) -> tuple[int, str]` — ISO 8601 → ms epoch, returns (ms, quality)

**State tracking (per locked gameId):**
- `_locked_game_id: int | None`
- `_lock_candidate: tuple[int, int]` — (gameId, consecutive_match_count) for 2-match confirmation
- `_last_score: str | None`
- `_last_period: str | None`
- `_last_status: str | None`
- `_last_ended: bool | None`

**Event emission logic:**
- `status` changes to `"inprogress"` and wasn't before → `"game_start"`
- `score` changes → `"score_change"`
- `period` changes → `"period_change"`
- `ended` becomes `True` → `"game_end"`
- Multiple changes in one message → emit multiple events (e.g., score + period change)

**Diagnostics:**
- Log every 60s if no match found yet: "Sports WS: N messages received, no match for {team1} vs {team2} yet"
- Log on gameId lock-on: "Sports WS: locked to gameId={id} ({homeTeam} vs {awayTeam})"
- Log on each emitted event: "Sports WS event: {event_type} | {score}"

### Step 2: Update `collector/game_state/registry.py`

- Add to `IMPLEMENTED_SOURCES`:
  ```python
  "polymarket_sports_ws": {"sport": "multi", "module": "sports_ws_client", "has_lookup": False},
  ```
- Remove from `CONTROL_GROUP_SPORTS`: `"mlb"` (now has Sports WS coverage)
- Add new set for Sports WS sports:
  ```python
  SPORTS_WS_SPORTS: set[str] = {"tennis", "mlb", "soccer", "cricket", "cs2", "valorant", "lol"}
  ```
- Update `SPORTS_WITH_GAME_STATE` to include Sports WS sports

### Step 3: Update `collector/__main__.py`

- Import `WebSocketSportsClient` from `collector.sports_ws_client`
- In `build_game_state_client()`, add early return for `"polymarket_sports_ws"`:
  ```python
  if config.data_source == "polymarket_sports_ws":
      logger.info("Game state via Sports WS (launched separately)")
      return None
  ```
- In `main()`, after the game state client block:
  ```python
  sports_ws_client = None
  if config.data_source == "polymarket_sports_ws":
      sports_event_queue: asyncio.Queue[list[MatchEvent]] = asyncio.Queue()
      sports_ws_client = WebSocketSportsClient(
          match_id=config.match_id,
          sport=config.sport,
          team1=config.team1,
          team2=config.team2,
          queue=sports_event_queue,
      )
      tasks.append(asyncio.create_task(sports_ws_client.run(), name="sports_ws"))
      tasks.append(asyncio.create_task(
          run_sports_ws_writer(sports_event_queue, db), name="sports_ws_writer"
      ))
      logger.info("Sports WS client: %s (%s vs %s)", config.sport, config.team1, config.team2)
  ```
- Add `run_sports_ws_writer()` coroutine:
  ```python
  async def run_sports_ws_writer(queue: asyncio.Queue, db: Database) -> None:
      while True:
          events = await queue.get()
          await db.insert_match_events(events)
  ```
- In cleanup section, add:
  ```python
  if sports_ws_client:
      await sports_ws_client.stop()
  ```

### Step 4: Update `scripts/discover_markets.py`

Change `SPORT_CLASSIFY` data_source from `"none"` to `"polymarket_sports_ws"` for:
- `"mlb"` (was `"none"`)
- `"soccer"` (was `"none"`)
- `"tennis"` (was `"none"`)
- `"cricket"` (was `"none"`)

Keep as-is:
- `"ufc"` → `"none"` (no WS game state for MMA — not a score-based sport)
- `"nfl"` → `"none"` (off-season, revisit later)
- `"cs2"` → `"pandascore"` (keep aspirational; Sports WS may not have CS2 game state — verify first)
- `"valorant"` → `"riot"` (keep aspirational)
- `"lol"` → `"riot"` (keep aspirational)

### Step 5: Update `collector/config.py`

The existing warning logic at lines 63-78 already handles unknown sources correctly. The addition of `"polymarket_sports_ws"` to `IMPLEMENTED_SOURCES` will make configs with that source pass validation without warnings. No changes needed.

### Step 6: Create tests

New file: `tests/test_sports_ws_client.py`

**Test cases:**
- `test_parse_score_simple` — "1-2" → (1, 2)
- `test_parse_score_zero` — "0-0" → (0, 0)
- `test_parse_score_invalid` — "6-4, 3-2" → (None, None)
- `test_parse_score_empty` — "" → (None, None)
- `test_parse_server_ts` — ISO 8601 with nanoseconds → ms epoch
- `test_parse_server_ts_missing` — None → (local_ms, "local")
- `test_league_filter_match` — ATP message matches tennis sport
- `test_league_filter_no_match` — NBA message doesn't match tennis sport
- `test_team_fuzzy_match` — case-insensitive substring matching
- `test_team_no_match` — wrong teams don't match
- `test_score_change_detection` — state tracker detects score change
- `test_period_change_detection` — state tracker detects period change
- `test_game_end_detection` — ended=True triggers game_end event
- `test_game_start_detection` — status="inprogress" triggers game_start
- `test_no_event_on_duplicate` — same state → no events emitted
- `test_gameid_lockon` — after 2 consecutive matches, locks to gameId
- `test_ping_pong` — text "ping" message triggers "pong" response

Use fixture: `tests/fixtures/ws_sport_result_sample.json`

### Step 7: Update `test_registry.py`

Update any assertions about `CONTROL_GROUP_SPORTS` membership (mlb removal) and `IMPLEMENTED_SOURCES` count.

### Step 8: Update documentation

- `CLAUDE.md`: Add `sports_ws_client.py` to project structure, add Sports WS URL to key API details, add `polymarket_sports_ws` to data source descriptions
- `LESSONS_LEARNED.md`: Add bullet about league abbreviation filtering and gameId lock-on pattern if implementation reveals new insights

## Verification

1. **Unit tests pass:** `python -m pytest tests/test_sports_ws_client.py tests/test_registry.py -v`
2. **All existing tests still pass:** `python -m pytest tests/ -v`
3. **Config loading works:** Load a tennis config with `data_source: "polymarket_sports_ws"` — no warnings
4. **Live smoke test:** Run collector against one of the live matches:
   ```bash
   python -m collector --config configs/match_atp-landalu-lehecka-2026-03-25.json --log-level DEBUG
   ```
   Verify:
   - "Sports WS client: tennis" in logs
   - "Sports WS: locked to gameId=..." appears
   - match_events rows appear in the DB
   - Existing Market WS shards still function normally
5. **Discovery script:** Run `python scripts/discover_markets.py` and verify tennis/MLB/soccer configs now show `data_source: "polymarket_sports_ws"`
