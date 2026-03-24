# Lessons Learned

- Polymarket `/books` batch endpoint is `POST` with JSON body `[{"token_id": "..."}]`, not GET with query params. GET returns 400 "Invalid payload".
- Gamma API `/markets` endpoint ignores `tag`, `event_slug`, and `_q` params — always returns the same default results. Use `/events` with `tag_slug` instead; markets are embedded in the event response.
- CLOB `/trades` requires full API key auth (POLY_API_KEY + signature headers). The Data API at `data-api.polymarket.com/trades` returns public trade data without auth.
- Data API trade pagination params (`after`, `before`, `cursor`, etc.) don't shift the result window — may need timestamp windowing or CLOB API key for proper incremental fetching.
- `riot_esports_sample.json` fixture was saved as a placeholder (`{"note": "populate after successful API calls"}`) because no Riot API key was set — don't rely on it for tests.
