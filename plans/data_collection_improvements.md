# Data Collection Improvements

Analysis of current collection quality and actionable improvements.
Date: 2026-03-25

---

## Current Collection Status (2026-03-24 run)

108 databases collected, ~1.5GB total.

| Sport | DBs | Score Range | Ready | Partial | Not Ready |
|-------|-----|-------------|-------|---------|-----------|
| NBA   | 4   | 73–77       | 1     | 3       | 0         |
| NHL   | 15  | 55–84       | 1     | 14      | 0         |
| ATP   | 61  | 7–62        | 0     | 3       | 58        |
| WTA   | 10  | 12–54       | 0     | 1       | 9         |
| Valorant | 11 | 7–17     | 0     | 0       | 11        |
| CS2   | 7   | 7           | 0     | 0       | 7         |

### NBA Detail (primary focus)

All 4 games have game events — the `lookup_game_id()` fix is confirmed working.

| Game     | Score | Snapshots | Trades | Events | Spikes |
|----------|-------|-----------|--------|--------|--------|
| den-phx  | 77    | 21,904    | 17,865 | 147    | 2,401  |
| nop-nyk  | 74    | 14,527    | 14,798 | 134    | 2,564  |
| orl-cle  | 74    | 15,806    | 15,237 | 167    | 1,460  |
| sac-cha  | 73    | 11,893    | 13,603 | 117    | 709    |

Spike candidates = >5c price move + >30% reversion within 5 minutes.
Score tracking is accurate (e.g. DEN 123 – PHX 125 final).

---

## Dual-Write Validation Results

All 4 NBA games fail the 98% WS threshold.

| Game     | WS%  | REST% |
|----------|------|-------|
| den-phx  | 42%  | 58%   |
| nop-nyk  | 30%  | 71%   |
| orl-cle  | 32%  | 68%   |
| sac-cha  | 23%  | 77%   |

### Root Cause

WS captures trades via the `last_trade_price` field embedded in book snapshot
events. Each book snapshot fires every ~7 seconds and carries only the single
most recent trade at that moment. If 5 trades occur between two snapshots, WS
records 1 and misses 4.

REST polls the Data API in paginated batches (~1,200 trades/hr cap), capturing
historical trades that happened between WS book snapshots.

Evidence: in `den-phx`, REST has only **209 distinct timestamps** across 10,400
trades (batched pages), while WS trades have precise individual timestamps.
Only **61 of 17,804** total unique hashes appear in both sources — they are
capturing almost entirely different trade subsets.

### Implication for Analysis

For the overreaction hypothesis, the primary signal chain is:

```
score_change event → price_signals (continuous) → spike detection
```

Trades are secondary context. The `price_signals` table (60K+ entries/game,
~4s resolution) and game events are what drive the analysis — these are working
well. The WS trade capture rate is a known limitation, not a blocker for Phase 3.

---

## Improvement Opportunities

### 1. Handle `price_change` WS Events (HIGH IMPACT)

**What:** The `price_change` event type is currently silently discarded in
`ws_client.py:_dispatch()`. It fires on every order book change (sub-second
frequency) and contains per-asset data:

```json
{
  "event_type": "price_change",
  "market": "0x...",
  "timestamp": "1774375776049",
  "price_changes": [
    {
      "asset_id": "...",
      "price": "0.12",
      "size": "0",
      "side": "BUY",
      "hash": "e93a5a49...",
      "best_bid": "0.52",
      "best_ask": "0.89"
    }
  ]
}
```

**Impact:**
- Price signal resolution improves from ~4s → sub-second
- Each entry includes a trade `hash`, `price`, `size`, `side` — can populate
  the trades table directly without REST polling
- Would likely push WS trade capture to >90% since every trade triggers a
  `price_change` event

**How:** In `_dispatch()`, add a `price_change` handler that:
1. For each entry in `price_changes[]`, emit a `PriceSignal` (best_bid/best_ask)
2. Emit a `Trade` if `size > 0` (the triggering trade)

---

### 2. Enable NHL Game State (HIGH IMPACT)

**What:** `collector/game_state/nhl_client.py` is fully implemented with
`lookup_game_id()` auto-resolution. But 14/15 NHL games collected on 2026-03-24
have zero game events (only `ana-van` got 18 events, reason unclear).

**Root cause:** NHL configs likely have `external_id: ""` — the same issue that
affected NBA before the `lookup_game_id()` fix. The NHL client exists but may
not be wired into `__main__.py` the same way NBA is.

**Impact:** 14 NHL games would gain score_change, period_end, penalty events.
NHL penalty → power play is a textbook overreaction scenario (moneyline shifts
on 2-minute power plays).

**How:** Verify `__main__.py` sport dispatch includes `"nhl"` → `NhlClient`,
and that NHL configs have `sport: "nhl"` set correctly.

---

### 3. Order Book Imbalance Signal (MEDIUM IMPACT)

**What:** We store `best_bid_size` and `best_ask_size` in every snapshot but
never compute the imbalance ratio. Market microstructure research consistently
shows this is one of the strongest short-term directional predictors.

```
imbalance = bid_size / (bid_size + ask_size)
```

Values above 0.65 → buying pressure, price likely to rise.
Values below 0.35 → selling pressure, price likely to fall.

**Impact:** Adds a quantitative directional signal to every snapshot. Can be
used to filter spike candidates (spikes with confirming imbalance are more
likely true overreactions vs noise).

**How:** Compute and store in both `order_book_snapshots` (new column) and
`price_signals` (new column). Existing schema migration needed.

---

### 4. Additional NBA Event Types (MEDIUM IMPACT)

**What:** NBA PBP data contains many more action types beyond what we currently
capture. Currently: score_change, timeout, quarter_end, half_end, game_end.

Missing events with likely price impact:

| PBP `actionType` | Market Relevance |
|------------------|-----------------|
| `foul`           | Momentum shift; star player in foul trouble |
| `turnover`       | Possession change, often unexpected |
| `challenge`      | Video review = uncertainty → resolution spike |
| `substitution`   | Star player exit/entry (DNP/injury risk) |
| `violation`      | Rare, unexpected |

**Impact:** Denser event stream means more event-price pairs to analyze.
Especially `foul` and `challenge` have clear market uncertainty profiles.

**How:** Add to `nba_client.py:poll()` — the raw PBP action is already
available in `raw_event_json`, just need to emit `MatchEvent` for each.

---

### 5. Sports API WebSocket for Game State (MEDIUM IMPACT)

**What:** `wss://sports-api.polymarket.com/ws` pushes real-time game state for
all sports. The research spike (`scripts/ws_research_spike.py`) already connects
to this. A sample message:

```json
{
  "gameId": 5266482,
  "leagueAbbreviation": "atp",
  "homeTeam": "Valentin Vacherot",
  "awayTeam": "Arthur Fils",
  "status": "inprogress",
  "eventState": {
    "type": "tennis",
    "score": "1-2",
    "period": "S1",
    "live": true,
    "ended": false
  }
}
```

**Impact:** Would provide game state events for ATP/WTA/Valorant/CS2 where
we currently have no game state client. Tennis matches have clear event
structure (set/game/point scores). All 61 ATP and 10 WTA games would gain
score events. This is the biggest unlock for sports other than NBA/NHL.

Also reduces polling latency for NBA/NHL: sports API pushes within ~1s of
the event vs our 10s poll interval.

**How:** Add a second WS connection in `__main__.py` that subscribes to the
sports API channel alongside the market channel. Parse incoming messages into
`MatchEvent` objects. Needs a sport-specific parser (tennis score format differs
from basketball).

---

## Recommended Priority

| # | Improvement | Effort | Impact | Do Next? |
|---|-------------|--------|--------|----------|
| 1 | Handle `price_change` WS events | Medium | High | Yes |
| 2 | Enable NHL game state | Low | High | Yes |
| 3 | Sports API WS for ATP/WTA/esports | High | Medium | After #1/#2 |
| 4 | Order book imbalance signal | Low | Medium | Yes |
| 5 | More NBA event types (foul/turnover/challenge) | Low | Medium | Yes |

Items #2, #4, #5 are small, targeted additions. Item #1 is the biggest
architectural change but also the biggest data quality win. Item #3 requires
a new WS connection and sport-specific parsers.
