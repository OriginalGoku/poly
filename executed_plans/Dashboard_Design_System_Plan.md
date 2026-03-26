# Dashboard Design System Plan

> Define a complete visual design system and implementation plan for migrating the Polymarket analytics dashboard from Streamlit to a React-based application with a FastAPI data layer.

---

## Problem Statement

The current Streamlit dashboard (`dashboard.py`) is functional but visually default and limited by Streamlit's constraints — no custom chart annotations, no event rails, no interactive tooltips, no depth visualizations. Phase 3 analysis (event-price correlation, overshoot detection) requires purpose-built visualizations that Streamlit cannot support. This plan defines the visual design system and architecture for a React replacement.

## Design Decisions

### D1: Clean analytical aesthetic, dark mode default

**Decision:** Vercel Analytics / Stripe Dashboard aesthetic. Dark mode default, generous whitespace, restrained color, polished charts.

**Rationale:** Researcher stares at charts during evening sports sessions. Dark mode reduces eye strain. Clean aesthetic keeps dense financial data readable without Bloomberg-terminal clutter.

### D2: FastAPI data layer between SQLite and React

**Decision:** Python FastAPI server serves JSON from SQLite databases. Precomputed views for expensive queries (event windows, spike candidates), on-demand with downsampling for time-series.

**Rationale:** React can't query SQLite directly. Static JSON exports can't cover the combinatorics of exploratory analysis (filter by sport, token, event type, time window). FastAPI is natural given the existing Python codebase.

**Trade-off:** Adds a server process. Alternative was staying with Streamlit for faster hypothesis validation, but Streamlit can't render the distinctive features (event annotation rail, custom chart interactions).

### D3: Framework stack — Next.js + shadcn/ui + Lightweight Charts + TanStack

**Decision:** Next.js (App Router), shadcn/ui (Tailwind), TradingView Lightweight Charts (financial time-series), visx/D3 (heatmap, event-aligned curves, depth chart), TanStack Table + TanStack Query.

**Rationale:** Minimum-complexity stack covering all 21 components. Lightweight Charts handles bid/ask/mid with step interpolation and annotations at 35KB. shadcn gives card/nav/layout primitives. visx fills custom chart gaps. TanStack handles tables and data fetching.

### D4: Blue bid / orange ask, indigo accent

**Decision:** Bid lines blue (#3B82F6), ask lines orange (#F97316), accent indigo (#6366F1). Green/red reserved for directional (up/down) semantic meaning only.

**Rationale:** Avoids green/red for chart series (colorblind unsafe, semantic collision with up/down). Indigo accent avoids collision with blue bid lines.

### D5: Event annotation rail as dedicated strip

**Decision:** Horizontal strip below price charts with sport-specific colored icons on a shared time axis, separate from inline chart annotations.

**Rationale:** More readable than annotations alone. Enables sport-specific iconography, click-to-highlight interaction, vertical stacking for simultaneous events. No existing trading dashboard has this — it's the visual signature of the product.

### D6: Confidence badge for timestamp quality

**Decision:** Amber badge with "plus/minus 5s" on NHL events (`timestamp_quality="local"`). Event-aligned curves show NHL data as dashed lines vs solid for server-quality timestamps.

**Rationale:** NHL API provides no absolute wall-clock timestamps (only game clock). Users must see quality context to avoid drawing wrong conclusions from event-price alignment.

### D9: Timestamp alignment rules for event-price correlation

**Decision:** `server_ts_ms` is the primary alignment key across all sources. Asymmetric windows: T-5s to T+120s for server-quality timestamps, T-10s to T+125s for local-quality timestamps (widened by ±5s NHL uncertainty). The `event-windows` endpoint accepts a `timestamp_quality` filter and applies window widening automatically. Event-aligned curves carry an `alignment_version` field (integer, incremented when window logic changes) so results from different iterations are never silently mixed.

**Rationale:** Cross-source clock drift between Polymarket WS and sport APIs is unavoidable. Making alignment rules explicit and versioned prevents misleading correlations and ensures reproducibility across analysis iterations.

### D10: Raw data for event-aligned views, LTTB only for full-game

**Decision:** Event-aligned curve views serve raw `price_signals` data (no LTTB). At ~1500 points per 150s window, the payload is small enough to render without downsampling. LTTB is reserved for full-game timelines (1s bins) and trade volume (5s bins).

**Rationale:** LTTB preserves visual shape but can skip brief reversions — exactly the spike-then-revert patterns the hypothesis needs to detect. Since event-aligned windows are small, the downsampling cost/benefit doesn't justify the distortion risk.

### D7: LTTB downsampling strategy

**Decision:** Raw data for event-aligned views (see D10), 1s bins for full-game timelines, 5s bins for trade volume, 50-row pagination for tables.

**Rationale:** A single NBA game produces 100-200K price_signals. Full-game and trade volume views need downsampling; event-aligned views do not (small windows, ~1500 pts each).

### D8: Order book depth — summary metrics first, full ladder in Phase 2

**Decision:** Phase 1 shows summary metrics (depth USD, inside liquidity, imbalance). Phase 2 adds full horizontal ladder with 10 bid/ask levels.

**Rationale:** Summary metrics are already computed and stored. Full ladder is complex to build and less critical than hypothesis-testing charts.

## Visual System Summary

### Typography
- **Primary:** Inter (variable weight) — tabular figures for numeric data
- **Monospace:** JetBrains Mono — token IDs, hashes
- **Scale:** 32px display, 20px heading, 16px subheading, 14px body, 13px table, 12px caption

### Color — Surfaces (Dark Default)
| Token | Value | Usage |
|-------|-------|-------|
| bg-base | #09090B | Page background |
| bg-surface | #18181B | Cards, panels |
| bg-elevated | #27272A | Dropdowns, modals, hover |
| border-default | #3F3F46 | Card borders, dividers |
| border-subtle | #27272A | Chart gridlines |
| text-primary | #FAFAFA | Headings, numbers |
| text-secondary | #A1A1AA | Labels, body |
| text-muted | #71717A | Captions, timestamps |

### Color — Surfaces (Light Mode)
| Token | Value | Usage |
|-------|-------|-------|
| bg-base | #FFFFFF | Page |
| bg-surface | #FAFAFA | Cards |
| bg-elevated | #F4F4F5 | Hover, dropdowns |
| border-default | #E4E4E7 | Borders |
| text-primary | #09090B | Headings |
| text-secondary | #71717A | Labels |
| text-muted | #A1A1AA | Captions |

### Color — Semantic
| Meaning | Value |
|---------|-------|
| positive | #10B981 (emerald) |
| negative | #EF4444 (red) |
| warning | #F59E0B (amber) |
| accent | #6366F1 (indigo) |
| accent-hover | #818CF8 |

### Color — Chart
| Series | Color |
|--------|-------|
| Bid | #3B82F6 (blue) |
| Ask | #F97316 (orange) |
| Mid price | #FAFAFA / #09090B (mode-adaptive) |
| Spread fill | #F97316 at 8% opacity |

### Color — Event Types
| Event | Color |
|-------|-------|
| score_change | #10B981 emerald |
| foul / turnover | #EF4444 red |
| timeout | #F59E0B amber |
| period_end / quarter_end | #8B5CF6 violet |
| game_start / game_end | #6366F1 indigo |
| substitution | #71717A gray |

### Spacing
8px base grid: 4 / 8 / 12 / 16 / 24 / 32 / 64px. Card radius 8px, buttons 6px.

### Motion
Minimal. Chart render 200ms ease-out. Tooltip instant. Filter change 150ms crossfade. Sidebar 150ms. Skeleton loaders, no spinners.

### Chart Rules
- Horizontal gridlines only, 1px border-subtle
- Step interpolation for bid/ask, smooth for mid
- Event annotations: vertical dashed lines behind data, colored dots at top
- Tooltips: bg-elevated card, vertical crosshair, all series values
- Area fills: 12% opacity gradient under lines
- Spread heatmap band: intensity between bid/ask encodes spread width

## Component Inventory (21 total)

### Foundational (10)
1. **Metric Card** — large number + label + optional delta/sparkline
2. **Data Table** — sortable, sticky headers, monospace numbers, pagination
3. **Time-Series Chart** — line/area, step + smooth interpolation, annotations, crosshair, zoom/pan
4. **Status Dot** — 8px circle (green/red/gray)
5. **Filter Pills** — horizontal toggleable tags
6. **Database Selector** — searchable dropdown with sport icon + status
7. **Token Selector** — token_id to "Outcome (Question)" label mapping
8. **Sidebar Nav** — collapsible, icon + label, Monitor/Analyze/Explore sections
9. **Skeleton Loader** — pulsing placeholders matching component shapes
10. **Confidence Badge** — amber badge for timestamp_quality="local" with tooltip

### Hypothesis-Specific (6)
11. **Event-Aligned Curve Chart** — bps from baseline, T-30 to T+120, individual + mean + confidence band, dashed for local-timestamp data
12. **Overreaction Heatmap** — event types x time offsets, color-encoded displacement/reversion
13. **Game Timeline** — full-game price + event rail + period bands + score
14. **Multi-Token Cascade** — single event, multiple tokens overlaid in bps
15. **Spread Dynamics Chart** — dual-panel: spread (top) + trade volume bars (bottom)
16. **Spike Candidate Table** — sortable metrics table with inline sparklines

### Monitoring (5)
17. **Collector Status Row** — match + sport + status + counts + duration + last active
18. **Gap Timeline** — horizontal segments showing uptime/downtime per shard
19. **Capture Rate Gauge** — percentage with pass/fail coloring
20. **Liquidity Grade** — badge (Liquid/Medium/Thin/Empty)
21. **Order Book Depth Chart** — summary metrics (depth USD, inside liquidity, imbalance); Phase 2: full horizontal ladder with 10 bid/ask levels

## Data Layer — API Endpoints

| Endpoint | Strategy |
|----------|----------|
| `GET /databases` | On-demand, lightweight stats |
| `GET /db/{name}/summary` | On-demand metric card data |
| `GET /db/{name}/markets` | On-demand market list with liquidity grades |
| `GET /db/{name}/signals?token=&start=&end=` | On-demand, LTTB downsampled |
| `GET /db/{name}/events?type=&match=` | On-demand with confidence field |
| `GET /db/{name}/event-windows?event_type=&token=&ts_quality=` | Precomputed on first request, cached; raw data (no LTTB); `ts_quality` filter widens windows for local-quality timestamps; response includes `alignment_version` |
| `GET /db/{name}/spike-candidates` | Precomputed, cached |
| `GET /db/{name}/trades?token=&start=&end=` | On-demand, paginated |
| `GET /db/{name}/depth?token=&ts=` | On-demand single snapshot |
| `GET /db/{name}/gaps` | On-demand |
| `GET /db/{name}/heatmap?metric=` | Precomputed, cached |

### Data Resolution

| View | Resolution | Max payload |
|------|------------|-------------|
| Event-aligned curves | Raw (no LTTB) | ~75K pts (50 events x 1500 pts) |
| Full-game timeline | 1s bins | ~10K pts/token |
| Spread dynamics | Native ~3s | ~3.6K pts/token |
| Trade volume | 5s bins | ~2K bars |
| Spike candidates | Precomputed rows | 50/page |
| Heatmap | Precomputed cells | ~60 cells |
| Order book depth | Single snapshot | 20 levels |

## Layout

### Navigation — Left Sidebar (collapsible)
- **MONITOR**: Overview, Live Feed
- **ANALYZE**: Events, Heatmap, Spikes
- **EXPLORE**: Games, Tokens, Quality

### Top Bar
Database selector, sport filter pills, dark/light toggle, last-synced timestamp.

### Page Template
Metric cards row -> primary chart with event rail -> secondary charts (2-col) -> detail table.

## Implementation Plan

### Phase 0.5: Architecture Validation Slice
> **Goal:** Validate the riskiest parts — timestamp alignment, event windowing, curve rendering — with minimum UI surface before committing to 21 components.

1. FastAPI server with 3 endpoints: `GET /databases`, `GET /db/{name}/signals`, `GET /db/{name}/event-windows` (with `ts_quality` filter, `alignment_version` in response, window widening for local-quality timestamps)
2. Next.js skeleton: dark theme, one page, database selector dropdown
3. One event-aligned curve chart (visx): bps from baseline, T-30 to T+120, raw data (no LTTB), dashed lines for local-timestamp data
4. One event annotation rail below the chart: colored icons on shared time axis
5. Confidence badge on NHL events

### Phase 1: Foundation + Monitoring
1. Set up full Next.js project with shadcn/ui, Tailwind, dark/light theme tokens (extending Phase 0.5 skeleton)
2. Expand FastAPI server with remaining endpoints: summary, markets, trades, gaps, depth, spike-candidates, heatmap
3. Implement remaining foundational components (metric card, data table, time-series chart via TradingView Lightweight Charts, sidebar nav, selectors, filter pills)
4. Build Monitor section: collector status, gap timeline, capture rate, liquidity grades
5. Build Explore section: game browser, token explorer with price/spread charts, quality view
6. Order book depth as summary metrics

### Phase 2: Hypothesis Analysis + Depth Ladder
1. Expand precomputed endpoints (spike-candidates, heatmap)
2. Build overreaction heatmap
3. Build game timeline with full event annotation rail + period bands
4. Build multi-token cascade view
5. Build spread dynamics dual-panel chart
6. Build spike candidate table with sparklines
7. Add full order book depth ladder (drill-down from summary)

## Verification

### Phase 0.5 Complete When:
- [ ] FastAPI serves event-windows from any SQLite DB in `data/` with `alignment_version` in response
- [ ] `ts_quality=local` widens windows by ±5s for NHL events
- [ ] Event-aligned curve renders raw bps data with dashed lines for local-timestamp events
- [ ] Annotation rail shows colored event icons on shared time axis below chart
- [ ] Confidence badge appears on NHL events with "±5s" tooltip
- [ ] Dark theme renders correctly

### Phase 1 Complete When:
- [ ] Next.js app renders with dark/light theme matching color tokens
- [ ] FastAPI serves data from any SQLite DB in `data/`
- [ ] Monitor overview shows all databases with status, counts, gaps
- [ ] Token explorer shows bid/ask/mid chart with step interpolation and spread
- [ ] Data table sorts and paginates correctly
- [ ] Downsampled payloads stay under 100K points

### Phase 2 Complete When:
- [ ] Event-aligned curves enhanced with mean + confidence band overlay (extending Phase 0.5 chart)
- [ ] Heatmap renders displacement by event type x time offset
- [ ] Game timeline shows full-game price + event rail + period bands
- [ ] Spike candidate table is sortable by reversion%, filterable by liquidity
- [ ] Multi-token cascade overlays multiple tokens in bps for single event
- [ ] Full depth ladder renders 10 bid/ask levels
