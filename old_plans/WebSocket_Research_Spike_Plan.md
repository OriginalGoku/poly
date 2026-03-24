# WebSocket Research Spike Plan

> Connect to both Polymarket WebSocket channels, dump raw messages for 5 minutes, and answer all blocking questions about payload structures, message frequency, and subscription limits.

---

## Problem Statement

The WebSocket Migration Plan (see `plans/WebSocket_Migration_Plan.md`) is blocked by unknown payload structures. The Polymarket docs describe event types (`book`, `price_change`, `last_trade_price`, `sport_result`) but not their field-level contents. We need to inspect real messages before writing any parser code.

## What This Spike Must Answer

### Market Channel Questions
1. **`book` event**: Full snapshot or delta? How many depth levels? Does it include `bids[]`, `asks[]` arrays? Are prices/sizes strings or numbers? Is there a sequence number or checksum?
2. **`price_change` event**: Individual level update or new price only? Fields present?
3. **`last_trade_price` event**: Just price, or also size/side/tx_hash/outcome? Can it populate the `trades` table?
4. **`best_bid_ask` event**: Fields and format?
5. **`tick_size_change` event**: Fields?
6. **Message frequency**: How many messages/second per token for an active market? For a quiet market?
7. **Subscription limits**: Can we subscribe to 88 tokens on one connection? Any errors?
8. **Heartbeat**: Does PING/PONG work as documented? What happens if we miss a PING?

### Sports Channel Questions
1. **`sport_result` event**: Exact payload for an NBA game — does `score` give us "102-98" or per-quarter breakdown? Does it fire on every basket or only on score changes?
2. **Update frequency**: How often during a live NBA game?
3. **Coverage**: Which sports are actively streaming right now?
4. **`slug` format**: Exact format for matching to our config `match_id`?
5. **`period` values**: What do we get for NBA (Q1, Q2, HT, Q3, Q4, OT)?

## Implementation Plan

### Step 1: Create the spike script

Create `scripts/ws_research_spike.py` — a standalone script (no collector dependencies) that:

1. Connects to Market channel (`wss://ws-subscriptions-clob.polymarket.com/ws/market`)
2. Subscribes to tokens from a real match config (load from `configs/`)
3. Connects to Sports channel (`wss://sports-api.polymarket.com/ws`)
4. Logs ALL raw messages to JSON-lines files with timestamps
5. Runs for 5 minutes, then disconnects and prints analysis

### Step 2: Dependencies

```bash
uv pip install websockets
```

No other new dependencies needed.

### Step 3: Script structure

```python
# scripts/ws_research_spike.py
#
# Usage: python scripts/ws_research_spike.py --config configs/<match>.json [--duration 300]

import asyncio
import json
import time
from pathlib import Path

import websockets

MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPORTS_WS_URL = "wss://sports-api.polymarket.com/ws"

async def market_channel(token_ids, output_file, duration):
    """Connect to Market channel, subscribe, dump messages."""
    async with websockets.connect(MARKET_WS_URL) as ws:
        # Subscribe
        sub_msg = {
            "assets_ids": token_ids,
            "type": "market",
            "custom_feature_enabled": True,
        }
        await ws.send(json.dumps(sub_msg))
        print(f"Subscribed to {len(token_ids)} tokens")

        # Heartbeat task
        async def heartbeat():
            while True:
                await asyncio.sleep(10)
                await ws.send("PING")

        hb_task = asyncio.create_task(heartbeat())

        # Message collection
        start = time.time()
        count = 0
        type_counts = {}
        with open(output_file, "w") as f:
            while time.time() - start < duration:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=15)
                    ts = time.time()
                    if msg == "PONG":
                        continue
                    try:
                        data = json.loads(msg)
                        event_type = data.get("event_type", data.get("type", "unknown"))
                        type_counts[event_type] = type_counts.get(event_type, 0) + 1
                    except json.JSONDecodeError:
                        event_type = "raw"
                    f.write(json.dumps({"ts": ts, "raw": msg}) + "\n")
                    count += 1
                    if count <= 3 or count % 100 == 0:
                        print(f"  [{count}] type={event_type}")
                except asyncio.TimeoutError:
                    print("  No message in 15s")

        hb_task.cancel()
        print(f"\nMarket channel: {count} messages in {duration}s")
        print(f"By type: {json.dumps(type_counts, indent=2)}")

async def sports_channel(output_file, duration):
    """Connect to Sports channel, dump messages."""
    async with websockets.connect(SPORTS_WS_URL) as ws:
        start = time.time()
        count = 0
        sports_seen = set()
        with open(output_file, "w") as f:
            while time.time() - start < duration:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=15)
                    ts = time.time()
                    if msg == "ping":
                        await ws.send("pong")
                        continue
                    try:
                        data = json.loads(msg)
                        league = data.get("leagueAbbreviation", "unknown")
                        sports_seen.add(league)
                    except json.JSONDecodeError:
                        pass
                    f.write(json.dumps({"ts": ts, "raw": msg}) + "\n")
                    count += 1
                    if count <= 5 or count % 50 == 0:
                        print(f"  [{count}] league={league}")
                except asyncio.TimeoutError:
                    print("  No message in 15s")

        print(f"\nSports channel: {count} messages in {duration}s")
        print(f"Sports seen: {sorted(sports_seen)}")
```

### Step 4: Run and collect

```bash
# Pick an active NBA match config (or any match with live markets)
python scripts/ws_research_spike.py --config configs/match_nba-sac-cha-2026-03-24.json --duration 300
```

Output files:
- `data/ws_spike_market.jsonl` — all Market channel messages
- `data/ws_spike_sports.jsonl` — all Sports channel messages

### Step 5: Analyze and save fixtures

After the 5-minute run, the script should print:

```
=== MARKET CHANNEL ANALYSIS ===
Total messages: NNN
By type: { "book": N, "price_change": N, "last_trade_price": N, ... }
Messages/second: N.N
Unique tokens seen: N

Sample 'book' event (first seen):
  [pretty-printed JSON]

Sample 'price_change' event (first seen):
  [pretty-printed JSON]

Sample 'last_trade_price' event (first seen):
  [pretty-printed JSON]

=== SPORTS CHANNEL ANALYSIS ===
Total messages: NNN
Sports seen: [NBA, CS2, ...]
Messages/second: N.N

Sample 'sport_result' event:
  [pretty-printed JSON]

=== ANSWERS TO BLOCKING QUESTIONS ===
1. book event is: [full_snapshot | delta]
2. book depth levels: [N]
3. last_trade_price includes tx_hash: [yes | no]
4. last_trade_price includes size/side: [yes | no]
5. 88-token subscription: [success | error]
6. Sports score format: [string example]
7. Sports update frequency: [N msgs/min for active game]
```

### Step 6: Save fixtures

Copy first example of each message type to:
- `tests/fixtures/ws_book_sample.json`
- `tests/fixtures/ws_price_change_sample.json`
- `tests/fixtures/ws_last_trade_price_sample.json`
- `tests/fixtures/ws_sport_result_sample.json`

These become the basis for fixture-based tests in the implementation phase.

## Verification

- [x] Market channel connects and receives messages (2,151 in 300s)
- [x] Sports channel connects and receives messages (92 in 308s)
- [x] At least one `book` event payload captured and inspected
- [x] At least one `last_trade_price` event captured and inspected
- [x] At least one `sport_result` event captured and inspected (tennis/dota2, no NBA)
- [x] 88-token subscription tested — SUCCESS, no errors
- [x] Message frequency measured (7.17 msgs/sec market, 0.30 msgs/sec sports)
- [x] Sample fixtures saved to `tests/fixtures/ws_*.json` (5 files)
- [x] All 8 Market channel questions answered (see WebSocket_Migration_Plan.md)
- [x] 3/5 Sports channel questions answered (no NBA data during spike)
- [x] Go/no-go decision documented — **GO (Path A)**

## Duration

**Target: 30 minutes total**
- 5 min: Write script
- 5 min: Run against Market channel
- 5 min: Run against Sports channel
- 10 min: Analyze payloads, save fixtures, document answers
- 5 min: Update `WebSocket_Migration_Plan.md` with findings
