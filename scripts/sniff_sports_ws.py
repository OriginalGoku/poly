#!/usr/bin/env python3
"""Temporary: sniff the Polymarket Sports WS to discover leagueAbbreviation values.

Connect for ~30s, print unique league abbreviations and any messages
matching known CBB team names. Delete after use.
"""

import asyncio
import json

import websockets

WS_URL = "wss://sports-api.polymarket.com/ws"
LISTEN_SECONDS = 45

# Known CBB team names to watch for
CBB_TEAMS = [
    "dayton", "illinois state", "nevada", "auburn", "duke", "gonzaga",
    "kentucky", "purdue", "houston", "tennessee", "alabama", "kansas",
    "villanova", "marquette", "michigan state", "uconn", "creighton",
    "wolf pack", "tigers", "wolves",
]


async def main():
    leagues: set[str] = set()
    cbb_hits: list[dict] = []
    msg_count = 0

    print(f"Connecting to {WS_URL} for {LISTEN_SECONDS}s...")

    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        deadline = asyncio.get_event_loop().time() + LISTEN_SECONDS

        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue

            if raw == "ping":
                await ws.send("pong")
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_count += 1
            league = data.get("leagueAbbreviation", "")
            if league:
                leagues.add(league)

            # Print every unique game seen
            game_id = data.get("gameId")
            home = str(data.get("homeTeam", ""))
            away = str(data.get("awayTeam", ""))
            status = data.get("status", "")
            score = data.get("score", "")
            print(f"  [{league}] gameId={game_id} {away} @ {home} | {score} ({status})")

            # Check for CBB team names
            home = str(data.get("homeTeam", "")).lower()
            away = str(data.get("awayTeam", "")).lower()
            for team in CBB_TEAMS:
                if team in home or team in away:
                    cbb_hits.append({
                        "gameId": data.get("gameId"),
                        "league": league,
                        "home": data.get("homeTeam"),
                        "away": data.get("awayTeam"),
                        "status": data.get("status"),
                    })
                    break

    print(f"\n--- Results ({msg_count} messages) ---")
    print(f"\nUnique leagueAbbreviation values: {sorted(leagues)}")
    if cbb_hits:
        print(f"\nCBB-related messages ({len(cbb_hits)}):")
        seen = set()
        for h in cbb_hits:
            key = (h["gameId"], h["league"])
            if key not in seen:
                seen.add(key)
                print(f"  gameId={h['gameId']} league={h['league']} "
                      f"{h['home']} vs {h['away']} ({h['status']})")
    else:
        print("\nNo CBB team matches found (need a live CBB game).")


if __name__ == "__main__":
    asyncio.run(main())
