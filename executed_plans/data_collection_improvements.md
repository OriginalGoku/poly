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

### CORRECTED (2026-03-25): Original analysis was misleading

The original analysis (below, struck) compared WS vs REST without filtering to
config tokens. The REST Data API returns trades from the **entire event** (1,933
markets for DEN-PHX), not just the 41 config markets. This made REST appear
dominant when it was actually capturing unrelated trades.

**Corrected results — filtered to config tokens only:**

| Game     | WS%   | REST% | WS trades | REST trades | Total unique |
|----------|-------|-------|-----------|-------------|--------------|
| den-phx  | 98.5% | 2.3%  | 7,465     | 175         | 7,579        |
| nop-nyk  | 99.4% | 1.9%  | 4,298     | 80          | 4,323        |
| orl-cle  | 99.0% | 1.5%  | 4,800     | 71          | 4,847        |
| sac-cha  | 99.5% | 1.7%  | 3,103     | 53          | 3,118        |

NHL results are even stronger (99.3-100% WS capture across all 15 games).

**WS validation: PASS (all games ≥98%).**

### Why the original numbers were wrong

The unfiltered analysis showed WS at 23-42% because:
- REST pulled 10,339 trades from 1,933 markets (event-wide leakage)
- WS only captured trades for the 39 config-token markets (by design)
- Comparing these directly inflated REST's apparent coverage

The `validate_dual_write.py` script did not filter to config tokens. The
corrected analysis joins trades against the `markets` table `token_ids_json`
to compare only trades for tokens we actually subscribed to.

### Implication for Analysis

WS is the authoritative trade source for config markets. REST adds negligible
value (1-2% of trades) while introducing massive noise from unrelated markets.

The primary signal chain for overreaction analysis remains:

```
score_change event → price_signals (continuous) → spike detection
```

Trades provide secondary context (volume confirmation). Both WS trades (98.5%+)
and price signals (60K+ entries/game, ~4s resolution) are working well.

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

## Recommended Priority (revised 2026-03-25)

With WS validation passed (98.5-100% trade capture) and Phase 3 analysis starting,
priorities shift toward enriching event data and analysis readiness.

| # | Improvement | Effort | Impact | When |
|---|-------------|--------|--------|------|
| 1 | Enable NHL game state | Low | High | Before next collection |
| 2 | More NBA event types (foul/turnover/challenge) | Low | Medium | Before next collection |
| 3 | Order book imbalance signal | Low | Medium | Before next collection |
| 4 | Handle `price_change` WS events | Medium | TBD | Deferred — check if ~4s resolution is sufficient during Phase 3 |
| 5 | Sports API WS for ATP/WTA/esports | High | Medium | Phase 4 candidate |

**Rationale for re-ordering:**
- Items #1-3 are small, targeted additions that improve the next collection run.
- Item #4 (`price_change`) was originally #1, but research suggests overreaction
  patterns operate on seconds-to-minutes timescales, not sub-second. The current
  ~4s signal resolution may be sufficient. Will re-evaluate during Phase 3 analysis.
- Item #5 (Sports WS) is a Phase 4 candidate — adds game state for tennis/esports
  but requires new WS connection and sport-specific parsers.
