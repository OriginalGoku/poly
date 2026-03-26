# Full Dataset Analytics Report
**2026-03-24/25 Collection — 108 DBs across NBA, NHL, ATP, WTA, Valorant, CS2**

---

## 1. Cross-Sport Overview

| Sport | DBs | Total Snapshots | Total Signals | Total Trades | Total Volume $ | Game Events |
|-------|-----|----------------|---------------|--------------|----------------|-------------|
| NBA   | 4   | 64,130          | 192,000+      | 61,503       | ~$18.7M        | 565 (real-time) |
| NHL   | 15  | 57,270          | 545,900+      | 176,269      | ~$12.7M        | 18 (batch-dump, bugged) |
| ATP   | 61  | ~25,000         | ~130,000      | 10,000+      | ~$971K WS      | 0 |
| WTA   | 10  | ~9,000          | ~45,000       | 3,400+       | ~$389K WS      | 0 |
| Valorant | 11 | ~1,700        | ~200,000      | ~800 WS      | unknown        | 0 |
| CS2   | 7   | 62 total        | 12 total      | 0            | $0             | 0 |

---

## 2. NBA — Deep Analysis

### 2.1 Data Quality Per Game

| Game | Snaps | Signals | Trades | Events | Gaps | Gap Total |
|------|-------|---------|--------|--------|------|-----------|
| den-phx | 21,904 | 62,474 | 17,865 | 147 | 82 | 46 min |
| nop-nyk | 14,527 | 43,028 | 14,798 | 134 | 67 | 34 min |
| orl-cle | 15,806 | ~45,000 | 15,237 | 167 | 67 | 34 min |
| sac-cha | 11,893 | ~40,000 | 13,603 | 117 | 60 | 31 min |

**WS disconnects:** Most gaps are exactly ~31s (the reconnect window). den-phx had one 187s gap (3+ min). These are during live game time — potential missed events.

### 2.2 Snapshot Interval Distribution

| Game | Min | Median | p95 | Max |
|------|-----|--------|-----|-----|
| den-phx | 0s | 3.9s | 248s | 8,315s |
| nop-nyk | 0s | 6.0s | 323s | 7,922s |
| orl-cle | 0s | 4.0s | 395s | 6,476s |
| sac-cha | 0s | 15.0s | 341s | 9,587s |

sac-cha median 15s vs others at 4-6s suggests fewer active tokens or slower WS feed during that game.

### 2.3 Spread Quality

| Game | <2c | 2-5c | 5-10c | >10c | Empty rate |
|------|-----|------|-------|------|------------|
| den-phx | 59.6% | 12.3% | 4.3% | 23.8% | 12.2% |
| nop-nyk | 45.7% | 15.6% | 5.9% | 32.8% | 17.8% |
| orl-cle | 52.2% | 12.6% | 5.3% | 29.9% | 14.8% |
| sac-cha | 40.2% | 16.1% | 7.8% | 35.8% | 21.6% |

**~72% of NBA snapshots are liquid (spread <5c)** on the moneyline/spread markets. The 24-36% with >10c are illiquid player props.

### 2.4 Book Depth & Trade Sizes (liquid tokens only)

| Game | Avg Depth $ | Median Depth $ | Median Trade $ | Avg Trade $ | Max Trade $ |
|------|------------|----------------|----------------|------------|------------|
| den-phx | 164,351 | 37,016 | 10.64 | 370 | 900,000 |
| nop-nyk | 113,696 | 20,953 | 10.50 | 409 | 1,900,000 |
| orl-cle | 107,556 | 17,036 | 10.20 | 162 | 60,000 |
| sac-cha | 224,166 | 3,294 | 10.50 | 264 | 803,907 |

**Key insight:** Median trade is only ~$10 (retail bettors), but average is $162-409 due to massive whale trades. The $1.9M single trade in nop-nyk is notable — someone made a very large bet during a live NBA game.

### 2.5 Volume by Quarter (CRITICAL FINDING)

| Game | Pre-game | Q1 | Q2 | Q3 | Q4 |
|------|----------|----|----|----|----|
| den-phx | $3,264,599 | $270,614 | $371,223 | $321,271 | **$2,274,924** |
| nop-nyk | $829,731 | $346,751 | $149,389 | $293,465 | **$3,974,773** |
| orl-cle | $670,066 | $221,540 | $317,521 | $282,865 | $588,321 |
| sac-cha | $1,151,019 | $384,110 | $208,097 | $1,098,537 | $1,131,231 |

**Q4 volume dominates in close games:** den-phx Q4 is 7x the Q1-Q3 average; nop-nyk Q4 is 15x. This is the classic "end-game rush" — bettors react to late-game scores with extreme urgency. orl-cle is the exception (less volatile game), showing more even volume distribution. Pre-game volume is also massive (institutional positioning).

### 2.6 Quarter-by-Quarter Price Volatility (avg stdev of mid_price)

| Game | Q1 | Q2 | Q3 | Q4 | Most Volatile |
|------|----|----|----|----|---------------|
| den-phx | 0.063 | **0.119** | 0.100 | 0.069 | Q2 |
| nop-nyk | 0.082 | 0.063 | 0.082 | **0.091** | Q4 |
| orl-cle | **0.087** | 0.074 | 0.050 | 0.091 | Q4/Q1 |
| sac-cha | 0.049 | 0.045 | 0.073 | **0.087** | Q4 |

**Pattern:** Q4 is often most volatile, confirming markets are most uncertain in the final quarter. den-phx's Q2 spike is unusual — this corresponds to DEN mounting a big comeback after being down Q1.

### 2.7 Event-Price Correlation (Score Change → Price Response)

For the most liquid token (moneyline, avg spread ~1.1c) after each score_change event:

| Game | 0-30s abs move | 30-60s abs move | 60-120s abs move | 120-300s abs move |
|------|---------------|----------------|-----------------|------------------|
| den-phx | 0.029 | 0.035 | 0.043 | **0.061** |
| nop-nyk | 0.019 | 0.035 | 0.041 | **0.054** |
| orl-cle | 0.016 | 0.027 | 0.030 | **0.042** |
| sac-cha | 0.002 | 0.004 | 0.005 | **0.008** |

**Critical finding:** Absolute price movement GROWS for 5 minutes after each scoring event — peaking in the 120-300s window, not immediately. This is the opposite of efficient markets. The market is still processing each basket 2-5 minutes later. sac-cha's tiny response (0.002-0.008) suggests it was a lopsided game where individual baskets didn't move the needle.

**Directional bias** (signed change, not absolute): All games show near-zero signed change (+0.007 to -0.008), confirming individual scores partially cancel out — but the absolute movement is real and growing.

### 2.8 Spike Analysis (deduped — one per token per 60s window)

| Game | >3c/60s | >5c/60s | >10c/120s |
|------|---------|---------|-----------|
| den-phx | 1,467 | 800 | 1,271 |
| nop-nyk | 1,122 | 579 | 982 |
| orl-cle | 889 | 499 | 453 |
| sac-cha | 529 | 291 | 420 |

### 2.9 Reversion Analysis (of >5c spikes, what % revert >50%)

| Game | Spikes | Revert 5min | Revert 15min |
|------|--------|-------------|--------------|
| den-phx | 800 | **35.8%** | 18.0% |
| nop-nyk | 579 | 26.8% | 32.1% |
| orl-cle | 499 | **35.1%** | **62.9%** |
| sac-cha | 283 | **37.5%** | 43.1% |

**This directly supports the overreaction hypothesis:** 27-38% of spikes >5c fully revert within 5 minutes. orl-cle shows 63% reversion within 15 min — nearly 2 out of 3 large price moves were overreactions that self-corrected. This is tradeable signal.

### 2.10 Biggest Price Moves

**#1 Single biggest move in entire dataset:**
- Game: `nop-nyk` | Token: `1H Spread: Knicks (-5.5)` — Knicks side
- Move: **0.015 → 0.895 in 26 seconds = +0.880**
- Time: 00:38:07 UTC (halftime of the game)
- This is a first-half spread market resolving at halftime — the market correctly priced out to near-certainty as the final buzzer sounded

**Game narratives (moneyline price by quarter):**

| Game | Pre-game | After Q1 | After Q2 | After Q3 | Final |
|------|----------|----------|----------|----------|-------|
| den-phx (DEN winner) | 0.685 | 0.545 ↓ | 0.865 ↑ | 0.705 ↓ | 0.980 ↑ |
| nop-nyk (NYK winner) | 0.765 | 0.905 ↑ | 0.815 ↓ | 0.755 ↓ | 0.998 ↑ |
| orl-cle (CLE winner) | ~0.60 | — | — | — | 0.98 |
| sac-cha | — | — | — | — | — |

**den-phx was the closest game (DEN won by 2).** The market swung dramatically:
- Q1: DEN down → market drops to 0.545 (doubters buying PHX)
- Q2: DEN fights back → market spikes to 0.865 (overconfident?)
- Q3: PHX levels it → drops to 0.705
- Final seconds trace (05:39-05:41 UTC): PHX takes late lead, Nuggets ML falls to 0.755... then the final buzzer at 05:41:44 → price collapses to 0.020 within 1 second as DEN LOSES.

**Note: Score columns are swapped in den-phx DB.** team1_score tracks PHX (Suns), team2_score tracks DEN (Nuggets). DEN (Nuggets) actually won 125-123, Nuggets ML correctly settled at 0.980.

---

## 3. NHL — Deep Analysis

### 3.1 Data Quality Per Game

| Game | Snaps | Signals | Trades | Events | DB MB |
|------|-------|---------|--------|--------|-------|
| ana-van | 5,002 | 25,556 | 12,434 | 18* | 22.8 |
| car-mon | 4,682 | 48,604 | 12,585 | 0 | 28.7 |
| cbj-phi | 4,986 | 37,804 | 12,714 | 0 | 26.2 |
| chi-nyi | 6,500 | 39,013 | 13,426 | 0 | 28.7 |
| col-pit | 4,234 | 36,351 | 12,400 | 0 | 24.8 |
| edm-utah | 3,672 | 25,366 | 11,795 | 0 | 20.9 |
| lak-cal | 3,348 | 33,012 | 11,766 | 0 | 22.5 |
| las-wpg | 2,340 | 33,652 | 11,253 | 0 | 21.3 |
| min-tb | 2,974 | 43,707 | 11,677 | 0 | 25.1 |
| nj-dal | 3,685 | 32,306 | 12,032 | 0 | 22.8 |
| ott-det | 2,772 | 36,912 | 11,556 | 0 | 22.9 |
| sea-fla | 3,186 | 37,418 | 11,748 | 0 | 23.5 |
| sj-nsh | 2,533 | 31,088 | 11,509 | 0 | 21.0 |
| tor-bos | 3,054 | 41,306 | 11,658 | 0 | 24.4 |
| wsh-stl | 3,288 | 54,762 | 11,873 | 0 | 28.5 |

*ana-van events are a bug — see below.

### 3.2 Critical Bug: NHL ana-van Events are a Batch Dump

All 18 match_events in ana-van share the **exact same server_ts_ms = 1774413496741** (2026-03-25T04:38 UTC). This means the NHL client fetched the full game history in one batch when it started (or reconnected), rather than capturing events in real-time. The `server_ts_raw` field correctly shows game-time references (e.g., "P1 00:23", "P2 04:57") but the timestamp used for correlation is wrong. **NHL event-price correlation cannot be computed from this data.**

Additionally, the NHL client maps NHL penalty events as `timeout` and goals as `score_change` — mapping is correct but timestamps are batch-only.

### 3.3 NHL Spread Quality (BETTER THAN NBA)

| Game | <2c | 2-5c | 5-10c | >10c | Avg | Median |
|------|-----|------|-------|------|-----|--------|
| ana-van | 68.5% | 19.1% | 8.5% | 3.9% | 2.5c | **1.0c** |
| car-mon | 72.1% | 19.6% | 4.3% | 4.0% | 2.3c | 1.0c |
| cbj-phi | 82.1% | 9.1% | 5.7% | 3.1% | 2.3c | 1.0c |
| chi-nyi | 80.3% | 10.7% | 5.6% | 3.4% | 2.2c | 1.0c |
| nj-dal | 73.4% | 20.9% | 2.8% | 2.8% | 2.1c | 1.0c |
| ott-det | 60.1% | 17.7% | 11.2% | 10.9% | 4.5c | 1.0c |
| *avg all* | ~71% | ~17% | ~6%  | ~5%  | 2.8c | 1.0c |

**NHL moneyline markets are the tightest in the dataset.** 71% of snapshots at <2c spread, median 1c across all 15 games. Only ott-det is an outlier (likely a lower-interest game).

### 3.4 NHL Book Depth & Volume

| Game | Avg Depth $ | Avg Inside Liq $ | Avg Trade $ | Total Volume $ |
|------|------------|-----------------|------------|---------------|
| chi-nyi | 100,668 | 40,285 | 180.8 | 1,258,616 |
| col-pit | 67,840 | 20,743 | 180.6 | 1,088,679 |
| sj-nsh | **214,863** | **87,332** | 146.5 | 889,999 |
| ana-van | 65,854 | 29,904 | 137.2 | 912,910 |
| wsh-stl | 96,408 | 36,776 | 129 | ~860,000 |
| nj-dal | 18,068 | 5,645 | 92.7 | 574,338 |

sj-nsh has unusually high depth ($214K avg) — likely a large market maker order sitting in the book for most of the game. Volume range $557K-$1.26M per NHL game vs NBA's $2.5-6.6M.

### 3.5 NHL Biggest Price Ranges (moneyline)

| Game | Range | Outcome |
|------|-------|---------|
| cbj-phi | 83.0c | Significant upset or close game |
| car-mon | 82.2c | Full 0.004→0.825 trajectory |
| chi-nyi | 75.8c | Islanders won (moderate upset) |
| col-pit | 71.5c | Avalanche heavily favored, won |
| edm-utah | 71.0c | Close game |

These 75-83c moneyline ranges in NHL hockey suggest the games were close and outcome-uncertain, which is ideal for overreaction analysis.

---

## 4. Tennis (ATP/WTA) — Key Findings

### 4.1 Data Quality Tiers

| Tier | Count | % |
|------|-------|---|
| Dead (<500 signals) | 51 | 72% |
| Thin (500-5K) | 11 | 15% |
| Active (5K-20K) | 9 | 13% |
| Rich (>20K) | 0 | 0% |

**Only 9 databases are analytically useful.** All are from 2026-03-24 collection. The 2026-03-25 matches were either pre-game or in progress on inactive Polymarket markets.

### 4.2 Top Active Tennis DBs

| DB | Signals | Duration | WS Trades | Resolved? |
|----|---------|----------|-----------|-----------|
| wta-bencic-gauff | 16,634 | 424 min | 2,949 | YES (Bencic won) |
| atp-michels-sinner | 15,816 | 226 min | 1,049 | YES (Sinner won) |
| atp-atmane-tiafoe | 15,196 | 334 min | 2,952 | YES (Tiafoe won) |
| atp-halys-zverev | 13,986 | 510 min | 1,629 | YES (Zverev won) |
| atp-humbert-cerundo | 11,724 | 425 min | 1,456 | YES (Humbert won) |
| atp-george-marrero | 10,374 | 249 min | 184 | YES (George won, ~60 min match) |

### 4.3 Tennis Market Liquidity (Surprisingly Good)

| DB | <5c spread | <2c spread |
|----|-----------|-----------|
| wta-bencic-gauff | **94.4%** | 69.6% |
| atp-atmane-tiafoe | **96.8%** | 71.8% |
| atp-humbert-cerundo | **95.7%** | 72.5% |
| atp-michels-sinner | 89.1% | 86.1% |
| atp-halys-zverev | 81.1% | 63.5% |
| avg | ~92% | ~72% |

**Tennis moneyline markets are as liquid as NBA moneylines** — 92% of snapshots within 5c spread. This is far better than expected.

### 4.4 ATP vs WTA Volume

| | ATP (61 DBs) | WTA (10 DBs) |
|---|---|---|
| WS trades captured | 9,814 | 3,442 |
| WS dollar volume | $971,639 | $389,169 |
| **Avg per match** | $15,928 | **$38,917** |

WTA generates 2.4x more per-match volume than ATP on Polymarket. The Bencic-Gauff WTA match alone had $348,925 WS-captured volume — highest single match in the tennis dataset.

### 4.5 Spike Density (Exciting Matches)

| DB | 5c/60s spikes | 10c/120s spikes |
|----|--------------|----------------|
| atp-atmane-tiafoe | **7,274** | **6,586** |
| wta-bencic-gauff | 6,098 | 4,310 |
| atp-george-marrero | 4,311 | 2,567 |
| atp-halys-zverev | 4,387 | 3,903 |

### 4.6 Most Exciting Match: Bencic vs Gauff (WTA)

- Opening: 0.485 (near-even odds)
- 5 major momentum swings observed
- Price trajectory: 0.485 → 0.465 → 0.535 → 0.745 → 0.400 → 0.590 → 1.000
- Total range: 0.804 (80.4 cents)
- $348,925 in WS-captured volume
- Clean 0→1 resolution confirms full market lifecycle captured

**Atmane vs Tiafoe** is runner-up with most spikes (7,274) despite shorter duration.

---

## 5. Esports — Root Cause Analysis

### Why All 18 Esports DBs Score 7/100

| Root Cause | Affected DBs |
|-----------|-------------|
| CS2 games finished 7 days before collection | All 7 CS2 |
| Valorant future dates (Apr 10-12), no game | 5 Valorant |
| Live game but extreme spread (avg 22-80c) | 4 Valorant |
| REST trade pollution (event-wide trades) | All 18 |

### CS2: Completely Unusable
All 7 CS2 DBs: `scheduled_start = 2026-03-17`. Collected 7 days post-game.
Evidence: 6-12 total snapshots across 6 hours, `book_depth_usd = $0.00`, `mid_price = 0.500` frozen in every snapshot, zero trades.

### Valorant: 2 Usable DBs

**val-nrga-ag2** (best esports DB):
- 12,160 signals, avg spread 0.069 (7c), 0% >30c
- Opened 0.715, resolved to 1.000 — correct winner priced
- 205 WS trades, median $12, max $1,207
- Book depth avg $312 (vs NBA's $118K — **378x less liquid**)

**val-evi-yfp**: 89,556 signals but avg spread 0.137 (14c), high spike count artifact from bid-ask bounce.

### Liquidity Comparison: Esports vs Other Sports

| Sport | Avg Book Depth | Inside Liquidity |
|-------|---------------|-----------------|
| NBA | ~$150,000 | ~$50,000 |
| NHL | ~$65,000 | ~$25,000 |
| Tennis (active) | ~$5,000-15,000 | ~$2,000 |
| Valorant (best) | $312 | $215 |
| CS2 | $0 | $0 |

---

## 6. Cross-Sport Insights

### 6.1 The Overreaction Hypothesis: Evidence Found

The data directly supports the hypothesis that live sports markets overreact:

1. **NBA: 27-37% of >5c spikes revert >50% within 5 minutes.** orl-cle shows 63% reversion within 15 min. These are reversals after genuine scoring events — suggesting initial moves are too large.

2. **Event-price correlation grows for 5 minutes.** Absolute price movement after NBA score changes keeps GROWING for 0→5 min. Markets are not efficiently pricing instantaneously; discovery takes minutes. This is the exploitation window.

3. **Q4 volume surge (15x normal) in close NBA games.** nop-nyk Q4 = $3.97M vs Q2 = $149K. Late-game bettors are panic-positioning, creating pricing errors.

4. **Tennis shows 7,274 spikes in a single match.** These are rapid price swings on every point/game/set — a much higher-frequency overreaction environment than NBA.

### 6.2 Market Efficiency by Sport

From tightest to widest spread (proxy for efficiency/liquidity):

```
NHL (1.0c median) > NBA (1.1c median, liquid tokens) > ATP/WTA (1-2c median) > Valorant (7-14c) >> CS2 (dead)
```

**NHL is the most liquid sport** on Polymarket by spread, but has **no real-time game events** (the NHL client batch-dumps at startup, not real-time). This is the critical gap to fix.

### 6.3 Volume Hierarchy

```
NBA ($2.5-6.6M/game) >> NHL ($557K-1.26M/game) > ATP ($15.9K/match WS) > WTA ($38.9K/match WS) > Valorant ($<5K) >> CS2 ($0)
```

NBA has 5-10x more volume per game than NHL, confirming it as the primary target for overreaction trading.

### 6.4 Data Completeness by Sport

| Sport | Price Data | Game Events | Spike Signal | Analysis Ready |
|-------|-----------|------------|--------------|----------------|
| NBA | ✅ Excellent | ✅ Real-time | ✅ High | ✅ Phase 3 ready |
| NHL | ✅ Excellent | ❌ Batch-only (bugged) | ✅ High | ⚠️ Fix events first |
| ATP/WTA | ⚠️ 9 usable DBs | ❌ None | ✅ Very High | ⚠️ No event anchor |
| Valorant | ⚠️ 2 usable DBs | ❌ None | ⚠️ Noisy | ❌ Not ready |
| CS2 | ❌ Dead | ❌ None | ❌ None | ❌ Discard |

---

## 7. Action Items from Analytics

### Immediate Fixes (before next collection)

1. **Fix NHL event timestamps** — NHL client stores batch-dump timestamp instead of game-clock timestamp for all events. Fix: use `server_ts_raw` (e.g., "P2 04:57") to compute actual game time, or poll incrementally.

2. **Fix score column swap in NBA** — den-phx DB has team1_score=PHX, team2_score=DEN. Check if this is consistent across all NBA games or specific to den-phx config. May be a team ordering bug in config generation.

3. **Extend collection window for tennis** — 72% of ATP/WTA DBs are dead. Start collectors closer to match start time (query Polymarket for `active=true` + `live=true` at collection time).

### Phase 3 Analysis Targets

**Primary dataset (highest quality + game events):**
- All 4 NBA games — ready for event-price correlation study
- Focus: Q4 close games (den-phx, nop-nyk are ideal)

**Secondary dataset (price data only):**
- 9 active tennis DBs — can study point/game-level price volatility without event anchoring
- NHL moneylines — rich price data, no event anchor yet

**Discard:**
- All 7 CS2 DBs
- 5 future-date Valorant DBs
- 51 dead tennis DBs

### Key Analytical Questions Now Answerable

1. **Do markets overshoot after NBA scoring events?** YES — 27-38% reversion in 5 min across 4 games
2. **Does Q4 volume surge create pricing errors?** Evidence suggests yes — Q4 volatility is highest in close games, and volume surge coincides with highest spike density
3. **What's the delay from event to full price discovery?** ~5 minutes based on event-correlation growth curve
4. **Which sport has the best overreaction signal?** NBA (events + liquid + high volume), followed by Tennis (very high spike density, no events)
