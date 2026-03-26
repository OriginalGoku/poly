# Collection Logs

Structured records of data collection sessions — capturing intent, hypotheses, and outcomes.

## Collection Index

| Date | Type | Games | Sports | Outcome | Notes | Log |
|------|------|-------|--------|---------|-------|-----|
| | | | | | | |

## Game State Coverage

Coverage varies by league within the same sport. Entries move from `Unknown` to `Yes`/`No` only from actual `match_events` counts confirmed in a `/collection-review`.

| Sport | League | Game State? | Source | Confirmed | Notes |
|-------|--------|-------------|--------|-----------|-------|
| nba | nba | Yes | nba_cdn | 2026-03-25 | CDN play-by-play |
| nhl | nhl | Yes | nhl_api | 2026-03-25 | Local timestamps only |
| cbb | cbb | Yes | polymarket_sports_ws | 2026-03-25 | Confirmed Sweet 16 |
| mlb | mlb | Yes | polymarket_sports_ws | 2026-03-25 | Mid-game start |
| tennis | atp | Yes | polymarket_sports_ws | 2026-03-25 | |
| tennis | challenger | Yes | polymarket_sports_ws | 2026-03-25 | Needed LEAGUE_MAP fix |
| dota2 | - | Yes | opendota | 2026-03-26 | OpenDota live diff |
| cricket | psl | Unknown | polymarket_sports_ws | - | First test 2026-03-26 |
| soccer | uef | Unknown | polymarket_sports_ws | - | UEFA qualifiers |
| soccer | fif | Unknown | polymarket_sports_ws | - | FIFA qualifiers |
| cs2 | - | No | pandascore | - | Needs API token |
| valorant | - | No | riot | - | Needs API key |
