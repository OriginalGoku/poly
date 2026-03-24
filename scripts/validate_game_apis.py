#!/usr/bin/env python3
"""
Phase 1a, Step 2: Validate game-state APIs.

Tests each API for field availability, response shape, and update frequency.
APIs: PandaScore (CS2), OpenDota (Dota 2), Riot Games (LoL), NBA CDN.

Requires env vars for APIs needing keys:
  PANDASCORE_TOKEN  — PandaScore API token
  RIOT_API_KEY      — Riot Games API key
"""

import asyncio
import json
import os
import statistics
import sys
import time

import httpx


# ── PandaScore (CS2) ────────────────────────────────────────────────────────

async def validate_pandascore(client: httpx.AsyncClient):
    token = os.environ.get("PANDASCORE_TOKEN")
    if not token:
        print("\n=== PandaScore (CS2): SKIPPED — set PANDASCORE_TOKEN ===")
        return

    print("\n=== PandaScore (CS2) ===")
    headers = {"Authorization": f"Bearer {token}"}
    base = "https://api.pandascore.co"

    # 1. Upcoming CS2 matches
    print("\n  --- Upcoming matches ---")
    resp = await client.get(
        f"{base}/csgo/matches/upcoming",
        headers=headers,
        params={"per_page": 5, "sort": "begin_at"},
    )
    print(f"  Status: {resp.status_code}")
    if resp.status_code == 200:
        matches = resp.json()
        print(f"  Upcoming matches: {len(matches)}")
        for m in matches[:3]:
            teams = " vs ".join(t["name"] for t in (m.get("opponents") or []) if "name" in t.get("opponent", t))
            # Handle nested opponent structure
            opp_names = []
            for opp in (m.get("opponents") or []):
                if isinstance(opp, dict) and "opponent" in opp:
                    opp_names.append(opp["opponent"].get("name", "?"))
                elif isinstance(opp, dict):
                    opp_names.append(opp.get("name", "?"))
            print(f"    {' vs '.join(opp_names)} | {m.get('begin_at')} | {m.get('league', {}).get('name', '?')}")
            print(f"      id={m.get('id')}  status={m.get('status')}  bo={m.get('number_of_games')}")

        if matches:
            with open("tests/fixtures/pandascore_upcoming_sample.json", "w") as f:
                json.dump(matches[:3], f, indent=2)
    else:
        print(f"  Error: {resp.text[:300]}")

    # 2. Recent completed match with round data
    print("\n  --- Recent completed match ---")
    resp = await client.get(
        f"{base}/csgo/matches/past",
        headers=headers,
        params={"per_page": 1, "sort": "-end_at"},
    )
    if resp.status_code == 200:
        past = resp.json()
        if past:
            match = past[0]
            match_id = match["id"]
            print(f"  Match: {match.get('name')} (id={match_id})")
            print(f"  Keys: {sorted(match.keys())}")

            # Check for games/rounds data
            games = match.get("games", [])
            print(f"  Games in series: {len(games)}")
            for g in games[:2]:
                print(f"    Game {g.get('position')}: {g.get('status')}  winner_type={g.get('winner', {}).get('type', '?')}")
                print(f"      Keys: {sorted(g.keys())}")
                rounds = g.get("rounds", [])
                print(f"      Rounds: {len(rounds)}")
                if rounds:
                    print(f"      Round sample keys: {sorted(rounds[0].keys())}")
                    print(f"      Round sample: {json.dumps(rounds[0], indent=2)[:300]}")

            with open("tests/fixtures/pandascore_match_sample.json", "w") as f:
                json.dump(match, f, indent=2)
    else:
        print(f"  Error: {resp.text[:300]}")

    # 3. Running matches (for live polling test)
    print("\n  --- Running matches ---")
    resp = await client.get(
        f"{base}/csgo/matches/running",
        headers=headers,
        params={"per_page": 5},
    )
    if resp.status_code == 200:
        running = resp.json()
        print(f"  Running matches: {len(running)}")
        if running:
            print("  Live polling test available — run with --live-pandascore to test")


# ── OpenDota (Dota 2) ───────────────────────────────────────────────────────

async def validate_opendota(client: httpx.AsyncClient):
    print("\n=== OpenDota (Dota 2) ===")
    base = "https://api.opendota.com/api"

    # 1. Live matches
    print("\n  --- Live matches ---")
    resp = await client.get(f"{base}/live")
    print(f"  Status: {resp.status_code}")
    if resp.status_code == 200:
        live = resp.json()
        print(f"  Live matches: {len(live)}")
        # Show pro matches if any
        pro = [m for m in live if m.get("league_id")]
        print(f"  Pro/league matches: {len(pro)}")
        for m in pro[:3]:
            print(f"    match_id={m.get('match_id')}  league={m.get('league_id')}  "
                  f"radiant={m.get('team_name_radiant', '?')} vs dire={m.get('team_name_dire', '?')}  "
                  f"duration={m.get('game_time', '?')}s")
        if live:
            print(f"  Sample keys: {sorted(live[0].keys())}")
            with open("tests/fixtures/opendota_live_sample.json", "w") as f:
                json.dump(live[:3], f, indent=2)
    else:
        print(f"  Error: {resp.text[:300]}")

    # 2. Recent pro match with parsed data
    print("\n  --- Recent pro match ---")
    resp = await client.get(f"{base}/proMatches", params={"less_than_match_id": ""})
    if resp.status_code == 200:
        pro_matches = resp.json()
        if pro_matches:
            match_id = pro_matches[0]["match_id"]
            print(f"  Fetching match {match_id} details...")
            resp2 = await client.get(f"{base}/matches/{match_id}")
            if resp2.status_code == 200:
                match = resp2.json()
                print(f"  Match keys: {sorted(match.keys())}")
                print(f"  Duration: {match.get('duration')}s  Winner: {'Radiant' if match.get('radiant_win') else 'Dire'}")

                # Check for objectives/teamfights
                objectives = match.get("objectives", [])
                teamfights = match.get("teamfights", [])
                print(f"  Objectives: {len(objectives)}  Teamfights: {len(teamfights)}")
                if objectives:
                    types = list({o.get("type") for o in objectives})
                    print(f"  Objective types: {types}")
                    print(f"  Objective sample: {json.dumps(objectives[0], indent=2)[:300]}")

                with open("tests/fixtures/opendota_match_sample.json", "w") as f:
                    json.dump(match, f, indent=2)
            else:
                print(f"  Match fetch error: {resp2.status_code}")
    else:
        print(f"  Error: {resp.text[:300]}")


# ── Riot Games API (LoL) ────────────────────────────────────────────────────

async def validate_riot(client: httpx.AsyncClient):
    api_key = os.environ.get("RIOT_API_KEY")
    if not api_key:
        print("\n=== Riot Games (LoL): SKIPPED — set RIOT_API_KEY ===")
        return

    print("\n=== Riot Games (LoL) ===")

    # LoL Esports API (no key needed for some endpoints)
    print("\n  --- LoL Esports API ---")
    esports_base = "https://esports-api.lolesports.com/persisted/gw"
    esports_headers = {"x-api-key": "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"}  # public key

    resp = await client.get(
        f"{esports_base}/getLive",
        headers=esports_headers,
        params={"hl": "en-US"},
    )
    print(f"  getLive status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        events = data.get("data", {}).get("schedule", {}).get("events", [])
        print(f"  Live events: {len(events)}")
        for ev in events[:3]:
            print(f"    {ev.get('league', {}).get('name', '?')}: {ev.get('blockName', '?')}")
            print(f"      state={ev.get('state')} type={ev.get('type')}")

    # Recent completed match timeline
    print("\n  --- Recent match timeline (Riot API) ---")
    # Use a known recent match or search
    headers = {"X-Riot-Token": api_key}
    # Try to get a recent pro match from LoL esports
    resp = await client.get(
        f"{esports_base}/getSchedule",
        headers=esports_headers,
        params={"hl": "en-US"},
    )
    if resp.status_code == 200:
        schedule = resp.json()
        events = schedule.get("data", {}).get("schedule", {}).get("events", [])
        completed = [e for e in events if e.get("state") == "completed"]
        print(f"  Completed events in schedule: {len(completed)}")
        if completed:
            ev = completed[0]
            match_obj = ev.get("match", {})
            games = match_obj.get("games", [])
            print(f"  Event: {ev.get('league', {}).get('name')} — {ev.get('blockName')}")
            print(f"  Games: {len(games)}")
            for g in games[:2]:
                print(f"    Game {g.get('number')}: state={g.get('state')} id={g.get('id')}")

    with open("tests/fixtures/riot_esports_sample.json", "w") as f:
        json.dump({"note": "populate after successful API calls"}, f, indent=2)


# ── NBA CDN ──────────────────────────────────────────────────────────────────

async def validate_nba(client: httpx.AsyncClient):
    print("\n=== NBA CDN ===")
    base = "https://cdn.nba.com/static/json"

    # 1. Today's scoreboard
    print("\n  --- Today's scoreboard ---")
    resp = await client.get(
        f"{base}/liveData/scoreboard/todaysScoreboard_00.json",
        headers={"Referer": "https://www.nba.com/", "Accept": "application/json"},
    )
    print(f"  Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        scoreboard = data.get("scoreboard", {})
        games = scoreboard.get("games", [])
        print(f"  Games today: {len(games)}")
        for g in games[:5]:
            home = g.get("homeTeam", {})
            away = g.get("awayTeam", {})
            print(f"    {away.get('teamTricode', '?')} @ {home.get('teamTricode', '?')}  "
                  f"status={g.get('gameStatusText', '?')}  "
                  f"score={away.get('score', '?')}-{home.get('score', '?')}")
            print(f"      gameId={g.get('gameId')}")

        if games:
            with open("tests/fixtures/nba_scoreboard_sample.json", "w") as f:
                json.dump(games[:2], f, indent=2)
    else:
        print(f"  Error: {resp.text[:300]}")

    # 2. Play-by-play for a recent game
    print("\n  --- Play-by-play ---")
    # Try to find a recent game ID
    # Use the schedule endpoint for recent games
    resp2 = await client.get(
        f"https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
        headers={"Referer": "https://www.nba.com/"},
    )
    game_id = None
    if resp2.status_code == 200:
        sched = resp2.json()
        dates = sched.get("leagueSchedule", {}).get("gameDates", [])
        # Find most recent date with completed games
        for d in reversed(dates[-30:]):  # last 30 dates
            for g in d.get("games", []):
                if g.get("gameStatus") == 3:  # completed
                    game_id = g.get("gameId")
                    print(f"  Found completed game: {game_id}")
                    break
            if game_id:
                break

    if game_id:
        resp3 = await client.get(
            f"{base}/liveData/playbyplay/playbyplay_{game_id}.json",
            headers={"Referer": "https://www.nba.com/"},
        )
        print(f"  PBP status: {resp3.status_code}")
        if resp3.status_code == 200:
            pbp = resp3.json()
            actions = pbp.get("game", {}).get("actions", [])
            print(f"  Actions: {len(actions)}")
            if actions:
                print(f"  Action keys: {sorted(actions[0].keys())}")
                # Show sample scoring plays
                scoring = [a for a in actions if a.get("scoreHome") != a.get("scoreAway") or a.get("actionType") in ("2pt", "3pt", "freethrow")][:5]
                for a in scoring[:3]:
                    print(f"    Q{a.get('period')} {a.get('clock', '?')}: {a.get('teamTricode', '?')} "
                          f"{a.get('actionType', '?')} — {a.get('description', '?')[:60]}")
                    print(f"      score: {a.get('scoreHome')}-{a.get('scoreAway')}")

                with open("tests/fixtures/nba_pbp_sample.json", "w") as f:
                    json.dump(actions[:20], f, indent=2)
        else:
            print(f"  Error: {resp3.text[:300]}")
    else:
        print("  No recent completed game found for PBP test")


# ── Live polling test ────────────────────────────────────────────────────────

async def live_poll_test(client: httpx.AsyncClient, api: str, duration_s: int = 60):
    """Poll a live endpoint and measure update frequency."""
    print(f"\n=== Live poll test: {api} ({duration_s}s) ===")

    last_hash = None
    change_intervals = []
    last_change_time = time.monotonic()
    polls = 0
    interval = 5.0

    start = time.monotonic()
    while time.monotonic() - start < duration_s:
        poll_start = time.monotonic()

        try:
            if api == "opendota":
                resp = await client.get("https://api.opendota.com/api/live")
                data_hash = hash(resp.text) if resp.status_code == 200 else None
            elif api == "nba":
                resp = await client.get(
                    "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json",
                    headers={"Referer": "https://www.nba.com/"},
                )
                data_hash = hash(resp.text) if resp.status_code == 200 else None
            else:
                break

            polls += 1
            if data_hash and data_hash != last_hash:
                if last_hash is not None:
                    interval_s = time.monotonic() - last_change_time
                    change_intervals.append(interval_s)
                last_hash = data_hash
                last_change_time = time.monotonic()

        except Exception as e:
            print(f"  Poll error: {e}")

        elapsed = time.monotonic() - poll_start
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)

    print(f"  Polls: {polls}  State changes: {len(change_intervals)}")
    if change_intervals:
        p50 = statistics.median(change_intervals)
        p95 = sorted(change_intervals)[int(len(change_intervals) * 0.95)] if len(change_intervals) > 1 else change_intervals[0]
        print(f"  Change interval p50={p50:.1f}s  p95={p95:.1f}s")


async def main():
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        await validate_pandascore(client)
        await validate_opendota(client)
        await validate_riot(client)
        await validate_nba(client)

        # Live poll tests if requested
        if "--live-opendota" in sys.argv:
            await live_poll_test(client, "opendota", duration_s=120)
        if "--live-nba" in sys.argv:
            await live_poll_test(client, "nba", duration_s=120)

    print("\n=== Game API validation complete ===")


if __name__ == "__main__":
    asyncio.run(main())
