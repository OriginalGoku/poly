#!/usr/bin/env python3
"""WebSocket Research Spike — connect to Polymarket WS channels and dump raw messages.

Usage:
    python scripts/ws_research_spike.py --config configs/match_nba-orl-cle-2026-03-24.json [--duration 300]
"""

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

import websockets

MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPORTS_WS_URL = "wss://sports-api.polymarket.com/ws"

DATA_DIR = Path("data")


async def market_channel(token_ids: list[str], output_file: Path, duration: int):
    """Connect to Market channel, subscribe to all tokens, dump messages."""
    print(f"\n=== MARKET CHANNEL (subscribing to {len(token_ids)} tokens) ===")
    try:
        async with websockets.connect(MARKET_WS_URL) as ws:
            # Subscribe
            sub_msg = {
                "assets_ids": token_ids,
                "type": "market",
                "custom_feature_enabled": True,
            }
            await ws.send(json.dumps(sub_msg))
            print(f"  Subscribed to {len(token_ids)} tokens")

            # Heartbeat task
            async def heartbeat():
                while True:
                    await asyncio.sleep(10)
                    try:
                        await ws.send("PING")
                    except Exception:
                        break

            hb_task = asyncio.create_task(heartbeat())

            # Message collection
            start = time.time()
            count = 0
            type_counts = defaultdict(int)
            first_by_type = {}

            with open(output_file, "w") as f:
                while time.time() - start < duration:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=15)
                        ts = time.time()

                        if msg == "PONG":
                            continue

                        try:
                            data = json.loads(msg)

                            # Handle array messages (initial book snapshot)
                            if isinstance(data, list):
                                event_type = "book_snapshot"
                                if event_type not in first_by_type and data:
                                    first_by_type[event_type] = data[0]
                                # Count each book in the array
                                for item in data:
                                    et = item.get("event_type", "book")
                                    type_counts[et] += 1
                                    if et not in first_by_type:
                                        first_by_type[et] = item
                                type_counts["book_snapshot_msgs"] += 1
                            else:
                                # Try multiple field names for event type
                                event_type = "unknown"
                                for key in ("event_type", "type", "channel"):
                                    if key in data:
                                        event_type = str(data[key])
                                        break

                                # Capture first example of each type
                                if event_type not in first_by_type:
                                    first_by_type[event_type] = data

                                type_counts[event_type] += 1
                        except json.JSONDecodeError:
                            event_type = f"raw:{msg[:60]}"
                            type_counts["raw"] += 1

                        f.write(json.dumps({"ts": ts, "raw": msg}) + "\n")
                        count += 1

                        if count <= 5 or count % 100 == 0:
                            elapsed = ts - start
                            print(f"  [{count:>5}] t={elapsed:>6.1f}s type={event_type}")

                    except asyncio.TimeoutError:
                        elapsed = time.time() - start
                        print(f"  No message in 15s (elapsed={elapsed:.0f}s)")

            hb_task.cancel()

            # Analysis
            elapsed = time.time() - start
            print(f"\n=== MARKET CHANNEL ANALYSIS ===")
            print(f"Total messages: {count}")
            print(f"Duration: {elapsed:.1f}s")
            print(f"Messages/second: {count / elapsed:.2f}" if elapsed > 0 else "N/A")
            print(f"By type: {json.dumps(dict(type_counts), indent=2)}")

            # Print first example of each type
            for etype, sample in first_by_type.items():
                print(f"\nSample '{etype}' event:")
                print(json.dumps(sample, indent=2)[:2000])

            return first_by_type

    except Exception as e:
        print(f"  Market channel error: {e}")
        return {}


async def sports_channel(output_file: Path, duration: int):
    """Connect to Sports channel, dump messages."""
    print(f"\n=== SPORTS CHANNEL ===")
    try:
        async with websockets.connect(SPORTS_WS_URL) as ws:
            start = time.time()
            count = 0
            sports_seen = set()
            first_by_type = {}
            type_counts = defaultdict(int)

            with open(output_file, "w") as f:
                while time.time() - start < duration:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=15)
                        ts = time.time()

                        if msg in ("ping", "PING"):
                            await ws.send("pong")
                            continue

                        try:
                            data = json.loads(msg)

                            # Try to classify the message
                            event_type = "unknown"
                            for key in ("event_type", "type", "channel", "event"):
                                if key in data:
                                    event_type = str(data[key])
                                    break

                            # Track sports/leagues
                            for key in ("leagueAbbreviation", "league", "sport"):
                                if key in data:
                                    sports_seen.add(str(data[key]))

                            if event_type not in first_by_type:
                                first_by_type[event_type] = data

                            type_counts[event_type] += 1
                        except json.JSONDecodeError:
                            event_type = f"raw:{msg[:60]}"
                            type_counts["raw"] += 1

                        f.write(json.dumps({"ts": ts, "raw": msg}) + "\n")
                        count += 1

                        if count <= 10 or count % 50 == 0:
                            elapsed = ts - start
                            league = next(iter(sports_seen), "?")
                            print(f"  [{count:>5}] t={elapsed:>6.1f}s type={event_type} leagues={sorted(sports_seen)}")

                    except asyncio.TimeoutError:
                        elapsed = time.time() - start
                        print(f"  No message in 15s (elapsed={elapsed:.0f}s)")

            # Analysis
            elapsed = time.time() - start
            print(f"\n=== SPORTS CHANNEL ANALYSIS ===")
            print(f"Total messages: {count}")
            print(f"Duration: {elapsed:.1f}s")
            print(f"Messages/second: {count / elapsed:.2f}" if elapsed > 0 else "N/A")
            print(f"Sports seen: {sorted(sports_seen)}")
            print(f"By type: {json.dumps(dict(type_counts), indent=2)}")

            for etype, sample in first_by_type.items():
                print(f"\nSample '{etype}' event:")
                print(json.dumps(sample, indent=2)[:2000])

            return first_by_type

    except Exception as e:
        print(f"  Sports channel error: {e}")
        return {}


def save_fixtures(market_samples: dict, sports_samples: dict):
    """Save first example of key event types as test fixtures."""
    fixtures_dir = Path("tests/fixtures")
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    mapping = {
        "book": "ws_book_sample.json",
        "price_change": "ws_price_change_sample.json",
        "last_trade_price": "ws_last_trade_price_sample.json",
        "best_bid_ask": "ws_best_bid_ask_sample.json",
        "tick_size_change": "ws_tick_size_change_sample.json",
    }

    saved = []
    for event_type, filename in mapping.items():
        if event_type in market_samples:
            path = fixtures_dir / filename
            with open(path, "w") as f:
                json.dump(market_samples[event_type], f, indent=2)
            saved.append(filename)
            print(f"  Saved {path}")

    # Save any sports samples
    for event_type, sample in sports_samples.items():
        if event_type != "unknown":
            filename = f"ws_sport_{event_type}_sample.json"
            path = fixtures_dir / filename
            with open(path, "w") as f:
                json.dump(sample, f, indent=2)
            saved.append(filename)
            print(f"  Saved {path}")

    # Also save the first sports sample regardless of type
    if sports_samples:
        first_type = next(iter(sports_samples))
        path = fixtures_dir / "ws_sport_result_sample.json"
        with open(path, "w") as f:
            json.dump(sports_samples[first_type], f, indent=2)
        saved.append("ws_sport_result_sample.json")
        print(f"  Saved {path}")

    if not saved:
        print("  No fixtures saved (no samples captured)")

    return saved


async def main():
    parser = argparse.ArgumentParser(description="WebSocket Research Spike")
    parser.add_argument("--config", required=True, help="Match config JSON path")
    parser.add_argument("--duration", type=int, default=300, help="Duration in seconds (default: 300)")
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    with open(config_path) as f:
        config = json.load(f)

    # Extract all token IDs
    token_ids = []
    for market in config["markets"]:
        token_ids.extend(market["token_ids"])

    print(f"Config: {config['match_id']}")
    print(f"Markets: {len(config['markets'])}")
    print(f"Token IDs: {len(token_ids)}")
    print(f"Duration: {args.duration}s")

    # Create output directory
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    market_output = DATA_DIR / "ws_spike_market.jsonl"
    sports_output = DATA_DIR / "ws_spike_sports.jsonl"

    # Run both channels concurrently
    market_samples, sports_samples = await asyncio.gather(
        market_channel(token_ids, market_output, args.duration),
        sports_channel(sports_output, args.duration),
    )

    # Save fixtures
    print(f"\n=== SAVING FIXTURES ===")
    save_fixtures(market_samples, sports_samples)

    # Print blocking-questions summary
    print(f"\n=== ANSWERS TO BLOCKING QUESTIONS ===")
    print(f"(Review the raw JSONL files and sample outputs above to answer)")
    print(f"Market output: {market_output}")
    print(f"Sports output: {sports_output}")
    print(f"\nMarket event types seen: {list(market_samples.keys())}")
    print(f"Sports event types seen: {list(sports_samples.keys())}")
    print(f"Token count tested: {len(token_ids)} (subscription limit test)")


if __name__ == "__main__":
    asyncio.run(main())
