#!/usr/bin/env python3
"""
Phase 1a, Step 3: Discover Polymarket sports/esports markets.

Uses Gamma API /events with tag_slug to find sports/esports events,
which return embedded markets. Filters for match-specific events (vs patterns)
and outputs match config JSON files.
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from collector.game_state.registry import IMPLEMENTED_SOURCES

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Tag slugs that work on the /events endpoint
TAG_SLUGS = [
    "esports", "cs2", "valorant", "dota",
    "nba", "basketball", "nfl", "baseball", "hockey",
    "soccer", "tennis", "mma", "cricket",
    "sports",
]

SPORT_CLASSIFY = [
    (["counter-strike", "cs2", "csgo", "cs:go"], "cs2", "pandascore"),
    (["dota 2", "dota2", "dota "], "dota2", "opendota"),
    (["league of legends", "lol:", "lol "], "lol", "riot"),
    (["valorant"], "valorant", "riot"),
    (["nba", "basketball"], "nba", "nba_cdn"),
    (["nfl", "super bowl", "pro football"], "nfl", "none"),
    (["mlb", "baseball"], "mlb", "none"),
    (["nhl", "hockey", "stanley cup"], "nhl", "nhl_api"),
    (["ufc", "mma"], "ufc", "none"),
    (["soccer", "premier league", "champions league", "la liga", "serie a", "bundesliga", "fifa"], "soccer", "none"),
    (["tennis", "atp", "wta", "wimbledon", "open", "dubai", "lugano"], "tennis", "none"),
    (["cricket", "ipl", "t20", "national t20"], "cricket", "none"),
]


def classify_sport(title: str, tags: list) -> tuple[str, str]:
    """Classify sport and data source from event title and tags."""
    tag_strs = [t if isinstance(t, str) else t.get("label", t.get("slug", str(t))) for t in tags]
    text = (title + " " + " ".join(tag_strs)).lower()
    for keywords, sport, source in SPORT_CLASSIFY:
        if any(kw in text for kw in keywords):
            return sport, source
    return "unknown", "none"


def is_match_event(title: str) -> bool:
    """Check if event title looks like a specific match (vs pattern)."""
    return bool(re.search(r'\bvs\.?\b|\bv\b', title, re.IGNORECASE))


def guess_relationship(question: str) -> str:
    """Guess the market relationship from question text."""
    q = question.lower()
    if any(x in q for x in ["odd/even"]):
        return "odd_even"
    if any(x in q for x in ["handicap"]):
        return "handicap"
    if any(x in q for x in ["o/u", "over/under", "total"]):
        return "over_under"
    if "map 1" in q or "game 1" in q or "set 1" in q:
        if "winner" in q:
            return "map_1_winner"
        return "map_1_prop"
    if "map 2" in q or "game 2" in q or "set 2" in q:
        if "winner" in q:
            return "map_2_winner"
        return "map_2_prop"
    if "map 3" in q or "game 3" in q or "set 3" in q:
        if "winner" in q:
            return "map_3_winner"
        return "map_3_prop"
    if "winner" in q or "win" in q:
        return "match_winner"
    if "toss" in q:
        return "toss"
    if "top batter" in q or "top bowler" in q:
        return "player_prop"
    if "sixes" in q or "fours" in q:
        return "stat_prop"
    if "completed" in q:
        return "completion"
    return "unknown"


def extract_best_of(title: str) -> int | None:
    """Extract best_of from title like (BO3)."""
    m = re.search(r'\(BO(\d)\)', title, re.IGNORECASE)
    return int(m.group(1)) if m else None


def extract_teams(title: str) -> tuple[str, str]:
    """Extract team names from 'Team A vs Team B' pattern."""
    for sep in [" vs ", " vs. ", " v "]:
        if sep in title.lower():
            idx = title.lower().index(sep)
            left = title[:idx].strip()
            right = title[idx + len(sep):].strip()
            # Clean up: remove tournament suffix like "- VCT EMEA..."
            right = re.split(r'\s*[-–]\s*(?:VCT|PGL|ESL|IEM|LPL|LCK|LCS|A1|United|Aorus|Asia|National|ATX|Dubai|Lugano|Australian|French|WTA|ATP)', right, maxsplit=1)[0].strip()
            # Remove (BO3) etc from team names
            left = re.sub(r'\s*\(BO\d\)\s*', '', left).strip()
            right = re.sub(r'\s*\(BO\d\)\s*', '', right).strip()
            # Remove sport prefix like "Counter-Strike: ", "Valorant: ", "LoL: "
            left = re.sub(r'^(?:Counter-Strike|Valorant|LoL|Dota\s*2?):\s*', '', left).strip()
            return left, right
    return "TBD", "TBD"


def build_config(event: dict, sport: str, data_source: str) -> dict:
    """Build match config from event with embedded markets."""
    title = event.get("title", "")
    team1, team2 = extract_teams(title)
    best_of = extract_best_of(title)

    markets_raw = event.get("markets", [])
    market_entries = []
    for m in markets_raw:
        token_ids = json.loads(m["clobTokenIds"]) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds", [])
        outcomes = json.loads(m["outcomes"]) if isinstance(m.get("outcomes"), str) else m.get("outcomes", [])
        if not token_ids:
            continue

        question = m.get("question", "")
        market_entries.append({
            "market_id": m.get("conditionId", ""),
            "question": question,
            "relationship": guess_relationship(question),
            "outcomes": outcomes,
            "token_ids": token_ids,
        })

    slug = event.get("slug", "")
    return {
        "match_id": slug,
        "external_id": "",
        "sport": sport,
        "team1": team1,
        "team2": team2,
        "tournament": title,
        "best_of": best_of,
        "scheduled_start": event.get("startDate", ""),
        "data_source": data_source,
        "polymarket_event_slug": slug,
        "polymarket_volume": float(event.get("volume", 0)),
        "markets": market_entries,
    }


async def main():
    print("=== Polymarket Sports/Esports Market Discovery ===\n")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        all_events = {}  # slug -> event

        # Fetch events by tag_slug (markets are embedded in event response)
        for tag in TAG_SLUGS:
            print(f"  Fetching tag_slug={tag}...", end="", flush=True)
            resp = await client.get(
                f"{GAMMA_BASE}/events",
                params={"tag_slug": tag, "active": "true", "closed": "false", "limit": 100},
            )
            if resp.status_code == 200:
                events = resp.json()
                new = 0
                for e in events:
                    slug = e.get("slug", "")
                    if slug and slug not in all_events:
                        all_events[slug] = e
                        new += 1
                print(f" {len(events)} events ({new} new)")
            else:
                print(f" error {resp.status_code}")
            await asyncio.sleep(0.3)

        # Filter for match-specific events
        match_events = {slug: e for slug, e in all_events.items() if is_match_event(e.get("title", ""))}
        season_events = {slug: e for slug, e in all_events.items() if slug not in match_events}

        print(f"\nTotal events: {len(all_events)}")
        print(f"Match events (with 'vs'): {len(match_events)}")
        print(f"Season/futures events: {len(season_events)}")

        # Classify and build configs for match events
        by_sport: dict[str, list] = {}
        configs = []

        for slug, event in match_events.items():
            tags = event.get("tags", []) if isinstance(event.get("tags"), list) else []
            sport, data_source = classify_sport(event.get("title", ""), tags)

            config = build_config(event, sport, data_source)
            if not config["markets"]:
                continue

            if sport not in by_sport:
                by_sport[sport] = []
            by_sport[sport].append(config)
            configs.append(config)

        # Summary table
        print(f"\n{'Sport':<15} {'Matches':<9} {'Markets':<9} {'Tokens':<8} {'Data Source':<15} {'Vol ($)':<12}")
        print("-" * 68)
        for sport in sorted(by_sport.keys()):
            items = by_sport[sport]
            total_markets = sum(len(c["markets"]) for c in items)
            total_tokens = sum(sum(len(m["token_ids"]) for m in c["markets"]) for c in items)
            total_vol = sum(c.get("polymarket_volume", 0) for c in items)
            sources = list({c["data_source"] for c in items})
            print(f"{sport:<15} {len(items):<9} {total_markets:<9} {total_tokens:<8} {', '.join(sources):<15} ${total_vol:>10,.0f}")

        # Detail by sport — top matches by volume
        print(f"\n=== Top Matches by Sport (by volume) ===")
        for sport in sorted(by_sport.keys()):
            items = sorted(by_sport[sport], key=lambda c: c.get("polymarket_volume", 0), reverse=True)
            print(f"\n  [{sport.upper()}] ({len(items)} matches)")
            for c in items[:10]:
                vol = c.get("polymarket_volume", 0)
                print(f"    {c['team1']} vs {c['team2']}")
                print(f"      markets={len(c['markets'])}  bo={c['best_of']}  vol=${vol:,.0f}  source={c['data_source']}")

        # Save configs
        os.makedirs("configs", exist_ok=True)
        for config in configs:
            safe_id = config['match_id'][:60].replace(" ", "_").replace("/", "_")
            filename = f"configs/match_{safe_id}.json"
            with open(filename, "w") as f:
                json.dump(config, f, indent=2)

        # Summary JSON
        summary = {
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "total_events": len(all_events),
            "match_events": len(match_events),
            "season_events": len(season_events),
            "configs_saved": len(configs),
            "by_sport": {
                sport: {
                    "matches": len(items),
                    "total_markets": sum(len(c["markets"]) for c in items),
                    "total_tokens": sum(sum(len(m["token_ids"]) for m in c["markets"]) for c in items),
                    "total_volume": sum(c.get("polymarket_volume", 0) for c in items),
                    "data_sources": sorted({c["data_source"] for c in items}),
                    "implemented_sources": sorted(
                        {c["data_source"] for c in items} & set(IMPLEMENTED_SOURCES)
                    ),
                    "has_game_state": bool(
                        {c["data_source"] for c in items} & set(IMPLEMENTED_SOURCES)
                    ),
                }
                for sport, items in by_sport.items()
            },
        }
        with open("configs/discovery_summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        print(f"\n=== Saved {len(configs)} config files to configs/ ===")
        print(f"=== Summary: configs/discovery_summary.json ===")


if __name__ == "__main__":
    asyncio.run(main())
