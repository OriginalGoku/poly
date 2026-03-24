#!/usr/bin/env python3
"""
Phase 1a, Step 1: Validate Polymarket CLOB API endpoints.

Tests:
  1. GET /book?token_id=<id> — response shape, tick_size, min_order_size
  2. POST /books with multiple token IDs — batch response
  3. Sustained 3s polling for 10 minutes — latency p50/p95, throttling detection
  4. Batch size test — 10-20 token IDs, find latency degradation point
  5. GET /trades — response shape, pagination fields
  6. Trade pagination — cursor/since parameter incremental fetching
"""

import asyncio
import json
import statistics
import sys
import time

import httpx

CLOB_BASE = "https://clob.polymarket.com"

# Known active token IDs — we'll discover these dynamically from the Gamma API
GAMMA_BASE = "https://gamma-api.polymarket.com"


async def find_active_tokens(client: httpx.AsyncClient, count: int = 5) -> list[str]:
    """Find active market token IDs from the Gamma API."""
    print("\n=== Finding active markets via Gamma API ===")
    resp = await client.get(
        f"{GAMMA_BASE}/markets",
        params={"active": "true", "closed": "false", "limit": count},
    )
    resp.raise_for_status()
    markets = resp.json()

    token_ids = []
    for m in markets:
        tokens = json.loads(m.get("clobTokenIds", "[]")) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds", [])
        question = m.get("question", "?")
        for tid in tokens:
            token_ids.append(tid)
            print(f"  Market: {question[:60]}  token: {tid[:20]}...")
        if len(token_ids) >= count:
            break

    print(f"  Found {len(token_ids)} token IDs")
    return token_ids[:count]


async def test_single_book(client: httpx.AsyncClient, token_id: str):
    """Test 1: GET /book for a single token."""
    print("\n=== Test 1: GET /book (single token) ===")
    start = time.monotonic()
    resp = await client.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
    elapsed_ms = (time.monotonic() - start) * 1000

    print(f"  Status: {resp.status_code}  Latency: {elapsed_ms:.0f}ms")
    if resp.status_code != 200:
        print(f"  ERROR: {resp.text[:200]}")
        return None

    data = resp.json()
    print(f"  Response keys: {sorted(data.keys())}")
    print(f"  market: {data.get('market', '?')[:40]}")
    print(f"  asset_id: {data.get('asset_id', '?')[:40]}")
    print(f"  timestamp: {data.get('timestamp')}")
    print(f"  tick_size: {data.get('tick_size')}")
    print(f"  min_order_size: {data.get('min_order_size')}")
    print(f"  last_trade_price: {data.get('last_trade_price')}")

    bids = data.get("bids", [])
    asks = data.get("asks", [])
    print(f"  Bids: {len(bids)} levels  Asks: {len(asks)} levels")
    if bids:
        print(f"  Best bid: {bids[0]}")
    if asks:
        print(f"  Best ask: {asks[0]}")

    return data


async def test_batch_books(client: httpx.AsyncClient, token_ids: list[str]):
    """Test 2: POST /books with multiple token IDs."""
    print(f"\n=== Test 2: POST /books ({len(token_ids)} tokens) ===")
    # POST /books with JSON body: [{"token_id": "..."}, ...]
    payload = [{"token_id": tid} for tid in token_ids]
    start = time.monotonic()
    resp = await client.post(
        f"{CLOB_BASE}/books",
        json=payload,
    )
    elapsed_ms = (time.monotonic() - start) * 1000

    print(f"  Status: {resp.status_code}  Latency: {elapsed_ms:.0f}ms")
    if resp.status_code != 200:
        print(f"  ERROR: {resp.text[:300]}")
        return None

    data = resp.json()
    if isinstance(data, list):
        print(f"  Returned {len(data)} books (list)")
        for i, book in enumerate(data):
            asset = book.get("asset_id", "?")[:20]
            bids = len(book.get("bids", []))
            asks = len(book.get("asks", []))
            print(f"    [{i}] asset={asset}... bids={bids} asks={asks}")
    elif isinstance(data, dict):
        print(f"  Returned dict with keys: {sorted(data.keys())}")
    else:
        print(f"  Unexpected type: {type(data)}")

    return data


async def test_sustained_polling(client: httpx.AsyncClient, token_ids: list[str], duration_s: int = 120, interval_s: float = 3.0):
    """Test 3: Sustained polling — latency stats and throttle detection."""
    print(f"\n=== Test 3: Sustained polling ({duration_s}s at {interval_s}s intervals) ===")
    latencies = []
    errors = 0
    start_time = time.monotonic()

    while time.monotonic() - start_time < duration_s:
        poll_start = time.monotonic()
        try:
            payload = [{"token_id": tid} for tid in token_ids]
            resp = await client.post(
                f"{CLOB_BASE}/books",
                json=payload,
            )
            elapsed_ms = (time.monotonic() - poll_start) * 1000
            if resp.status_code == 200:
                latencies.append(elapsed_ms)
            elif resp.status_code == 429:
                print(f"  THROTTLED at poll {len(latencies) + errors + 1} ({elapsed_ms:.0f}ms)")
                errors += 1
            else:
                print(f"  HTTP {resp.status_code} at poll {len(latencies) + errors + 1}")
                errors += 1
        except Exception as e:
            elapsed_ms = (time.monotonic() - poll_start) * 1000
            print(f"  Exception at poll {len(latencies) + errors + 1}: {e}")
            errors += 1

        # Sleep remainder of interval
        elapsed = time.monotonic() - poll_start
        if elapsed < interval_s:
            await asyncio.sleep(interval_s - elapsed)

    if latencies:
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        print(f"  Polls: {len(latencies)} OK, {errors} errors")
        print(f"  Latency p50={p50:.0f}ms  p95={p95:.0f}ms  p99={p99:.0f}ms")
        print(f"  Min={min(latencies):.0f}ms  Max={max(latencies):.0f}ms")
    else:
        print(f"  No successful polls! {errors} errors")


async def test_batch_sizes(client: httpx.AsyncClient, token_ids: list[str]):
    """Test 4: Test increasing batch sizes to find latency degradation."""
    print("\n=== Test 4: Batch size latency test ===")
    # We need more tokens for this test
    if len(token_ids) < 5:
        print("  Need more tokens for batch size test, fetching more...")
        # Fetch more from Gamma
        resp = await client.get(
            f"{GAMMA_BASE}/markets",
            params={"active": "true", "closed": "false", "limit": 30},
        )
        resp.raise_for_status()
        markets = resp.json()
        all_tokens = []
        for m in markets:
            tokens = json.loads(m.get("clobTokenIds", "[]")) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds", [])
            all_tokens.extend(tokens)
        token_ids = list(dict.fromkeys(all_tokens))  # dedupe, preserve order
        print(f"  Expanded to {len(token_ids)} tokens")

    for size in [1, 3, 5, 10, 15, 20]:
        batch = token_ids[:size]
        if len(batch) < size:
            print(f"  Only {len(batch)} tokens available, stopping at batch size {len(batch)}")
            break

        latencies = []
        for _ in range(3):  # 3 samples per size
            payload = [{"token_id": tid} for tid in batch]
            start = time.monotonic()
            resp = await client.post(
                f"{CLOB_BASE}/books",
                json=payload,
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                latencies.append(elapsed_ms)
            await asyncio.sleep(0.5)

        if latencies:
            avg = statistics.mean(latencies)
            print(f"  Batch={size:>2}  avg={avg:.0f}ms  samples={latencies}")


async def test_trades(client: httpx.AsyncClient, token_id: str):
    """Test 5: GET /trades — response shape."""
    print("\n=== Test 5: GET /trades ===")

    # CLOB /trades requires API key auth (POLY_API_KEY + signature headers)
    print("  CLOB /trades: requires auth (POLY_API_KEY + signature) — skipping")
    print("  Note: CLOB trades endpoint returns {maker_address, price, side, status, trader_side}")
    print("  Pagination: next_cursor (base64), before/after (unix timestamp)")

    # Data API — public trade feed (no auth needed)
    data_base = "https://data-api.polymarket.com"
    print("\n  --- Data API /trades (public, no auth) ---")
    resp = await client.get(
        f"{data_base}/trades",
        params={"asset_id": token_id, "limit": 5},
    )
    print(f"  Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        if isinstance(data, list) and data:
            print(f"  Trade count: {len(data)}")
            print(f"  Keys: {sorted(data[0].keys())}")
            print(f"  First trade: {json.dumps(data[0], indent=2)[:500]}")

            with open("tests/fixtures/data_api_trades_sample.json", "w") as f:
                json.dump(data, f, indent=2)
            print("  Saved fixture: tests/fixtures/data_api_trades_sample.json")
        elif isinstance(data, dict):
            print(f"  Keys: {sorted(data.keys())}")
            if "data" in data:
                items = data["data"]
                print(f"  Trade count: {len(items)}")
                if items:
                    print(f"  First item keys: {sorted(items[0].keys())}")
                    print(f"  First item: {json.dumps(items[0], indent=2)[:500]}")
    else:
        print(f"  Error: {resp.text[:300]}")

    # Gamma API — also has trade/activity data
    print("\n  --- Gamma API /activity (public) ---")
    resp2 = await client.get(
        f"{GAMMA_BASE}/activity",
        params={"asset_id": token_id, "limit": 5},
    )
    print(f"  Status: {resp2.status_code}")
    if resp2.status_code == 200:
        data2 = resp2.json()
        if isinstance(data2, list) and data2:
            print(f"  Activity count: {len(data2)}")
            print(f"  Keys: {sorted(data2[0].keys())}")
    else:
        print(f"  Error (may not exist): {resp2.status_code}")


async def test_trade_pagination(client: httpx.AsyncClient, token_id: str):
    """Test 6: Trade pagination — cursor/since parameter."""
    print("\n=== Test 6: Trade pagination ===")
    data_base = "https://data-api.polymarket.com"

    # Test Data API pagination
    print(f"\n  Testing Data API pagination...")
    params = {"asset_id": token_id, "limit": 3}
    resp = await client.get(f"{data_base}/trades", params=params)
    if resp.status_code != 200:
        print(f"  Status {resp.status_code}, skipping")
        return

    data = resp.json()
    trades = data if isinstance(data, list) else data.get("data", [])
    if not trades:
        print("  No trades returned")
        return

    print(f"  Page 1: {len(trades)} trades")
    last = trades[-1]

    # Try various cursor fields
    cursor_candidates = ["id", "trade_id", "timestamp", "created_at"]
    for field in cursor_candidates:
        if field in last:
            print(f"  Potential cursor field: {field} = {last[field]}")

    # Try paginating with different param names
    for cursor_param in ["after", "cursor", "since", "before", "next_cursor", "offset"]:
        for field in cursor_candidates:
            if field not in last:
                continue
            params2 = {**params, cursor_param: last[field]}
            resp2 = await client.get(f"{data_base}/trades", params=params2)
            if resp2.status_code == 200:
                data2 = resp2.json()
                trades2 = data2 if isinstance(data2, list) else data2.get("data", [])
                if trades2:
                    ids1 = {t.get("id") or t.get("trade_id") or t.get("transactionHash") for t in trades}
                    ids2 = {t.get("id") or t.get("trade_id") or t.get("transactionHash") for t in trades2}
                    overlap = len(ids1 & ids2)
                    print(f"  {cursor_param}={field} → {len(trades2)} trades, overlap={overlap}")


async def main():
    duration = 120  # default 2 minutes for sustained test (use 600 for full 10 min)
    if "--full" in sys.argv:
        duration = 600

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Find active tokens
        token_ids = await find_active_tokens(client, count=5)
        if not token_ids:
            print("ERROR: No active tokens found!")
            sys.exit(1)

        # Test 1: Single book
        book = await test_single_book(client, token_ids[0])

        # Save fixture
        if book:
            with open("tests/fixtures/polymarket_book_sample.json", "w") as f:
                json.dump(book, f, indent=2)
            print("  Saved fixture: tests/fixtures/polymarket_book_sample.json")

        # Test 2: Batch books
        batch_data = await test_batch_books(client, token_ids[:3])
        if batch_data:
            with open("tests/fixtures/polymarket_books_batch_sample.json", "w") as f:
                json.dump(batch_data, f, indent=2)
            print("  Saved fixture: tests/fixtures/polymarket_books_batch_sample.json")

        # Test 3: Sustained polling
        await test_sustained_polling(client, token_ids[:3], duration_s=duration)

        # Test 4: Batch sizes
        await test_batch_sizes(client, token_ids)

        # Test 5: Trades
        await test_trades(client, token_ids[0])

        # Test 6: Trade pagination
        await test_trade_pagination(client, token_ids[0])

        print("\n=== Polymarket validation complete ===")


if __name__ == "__main__":
    asyncio.run(main())
