# TT Elite Series — H2H Over/Under 74.5 Tool

Flags head-to-head player pairs whose total-points history leans hard over/under the 74.5
line, **and honestly tests whether that lean actually predicts future meetings** (most raw
"70% in 12 games" flags are coin-flip selection noise, not edges).

## The strategy (yours)
For each specific pair of players, take their past meetings, compute how often the match
total went over/under 74.5, and bet the side that hit ≥70% of the time — but only with a
real sample (≥10–15 meetings).

## The pieces
| file | does |
|---|---|
| `ingest_betsapi.py` | pulls TT Elite match history **with total points** into `tt.sqlite` |
| `h2h.py` | flags the ≥70% H2H pairs (your strategy) + `--pair "A" "B"` lookup |
| `validate.py` | **the honest test**: does a flagged trend persist out-of-sample? |

## Data — where it comes from (and why)
You need historical **total points per match** (= sum of both players' points across all
games), which requires **set-by-set scores**. Reality after probing every source:
- **Pinnacle / FanDuel / DraftKings** don't give us this data programmatically (no free feed;
  DK is IP-blocked). *(You bet the line on FanDuel — the tool just needs history, not odds.)*
- **Sofascore / scores24 / bsportsfan / betsapi.com HTML** — all Cloudflare-walled from scripts.
- **TheSportsDB (free)** — only has major TT (Olympics/Worlds), not TT Elite.
- ✅ **BetsAPI API** — the one reliable structured source with set scores, history since 2016.

**What to buy:** the **"Everything API — One Day Trial" ($2)** — it includes the Events API
(which carries table tennis). The $1 trials are bookmaker-only (Bet365/Betfair/…) and do
NOT include table tennis events, so don't buy those. One day is enough to pull the *entire*
2016+ history in one run and keep it locally forever. To refresh later: the **"Table Tennis
API" ($10/mo)**. Test on `--days 3` first to confirm the `scores` field is populated before
the full pull.

```bash
BETSAPI_TOKEN=your_trial_token python ingest_betsapi.py --days 4000   # full history
python h2h.py --line 74.5 --min 12 --pct 0.70                         # flag pairs
python validate.py --line 74.5 --min 12 --pct 0.70                    # is it real?
python h2h.py --pair "Player A" "Player B"                            # check a matchup
```
(Free alternative: scrape Sofascore/scores24 from your Mac — a residential IP dodges
Cloudflare — but it's fragile; the $2 BetsAPI pull is far cleaner.)

### Maximize the one-day trial (pull everything with permanent value, once)
The trial is 24h of access to ALL feeds. History is one-time; live data we get free. Order:
1. `ingest_betsapi.py --days 3` — **test** the results feed (confirm total points populate).
2. `ingest_odds.py --explore 5` — **test** the odds feed; paste the output so we lock the
   total-line market key.
3. `ingest_betsapi.py --days 4000` — full **results** history, ALL TT leagues (Elite, Cup,
   Setka, Liga Pro …). ~minutes-to-an-hour.
4. `ingest_odds.py --pull` — full **posted total-line + closing-odds** history (validates
   against the real line, not a fixed 74.5). ~1–3h; let it run.
5. **Tennis** (other tool, un-repeatable data): in `../tennis-betting/`, run
   `python scripts/ingest_betsapi_tennis.py --explore 6` (paste me the output to lock the
   set-betting key), then `--pull --days 3` to test, then `--pull` for full history —
   results + winner/total-games/**set-betting** closing odds → finally backtests the tennis
   set-market edge.

**"Never again" = the historical backlog.** Keeping the tool *current* with new matches
still needs periodic refresh (another $2 trial, $10/mo Table Tennis API, or a residential
scrape) — but you only pay to catch up the gap, never to re-pull the years you already have.

## Daily use — `check_today.py`
Cross-references today's TT Elite fixtures against your flagged pairs and prints exactly
which matches to bet and which side:
```bash
BETSAPI_TOKEN=xxx python check_today.py --min 12 --pct 0.70
```
The flags come from the 77k-match history (stable), so this only needs today's *fixtures* —
run it during Polish daytime when the slate is posted. Verified on real data (Gorski vs Wnek
→ UNDER 100% over 19 meetings; Ruszkiewicz vs Adamus → OVER 94% over 17).

## Keeping it running for free (the honest source situation)
There is **no clean, reliable FREE structured source** for these amateur leagues that works
from a datacenter/GitHub-Actions IP — which is *exactly why the market stays soft*. Options,
honest and ranked:
1. **BetsAPI token in GitHub Secrets (most reliable).** GitHub Actions compute is free; only
   the data source costs. Store the token as a repo secret and the collector + checker run
   daily for free. Your historical 77k-match base stays valid forever regardless of the token.
2. **You don't need daily collection to bet.** The 369 flags are stable; `check_today.py`
   needs only today's fixtures. Ongoing collection just refreshes stats + logs results.
3. **Flashscore scraper (free, fragile).** Flashscore *is* reachable from datacenter, but TT
   Elite is buried in tournament-specific feeds needing the tournament id + per-match detail
   for set points + delimited parsing — a real RE build that can break when they change the
   format. Buildable, not guaranteed durable.

## Read `validate.py` before betting a dollar
It runs three checks and prints a verdict:
- **Walk-forward** — whenever a pair is already on a ≥70% trend, bet its *next* meeting; the
  aggregate hit rate is the true out-of-sample number.
- **Split-half** — flag on each pair's first half, test on the second half.
- **Global null** — the ~50% floor you'd get from selection alone (no real tendency).

Validated on synthetic data: pure-noise data → walk-forward 51.8%, verdict **❌ loses to
vig**; data with real style-driven pairs → 70.8% (split-half 71.2%, z +10.8), verdict **✅
tradeable**. So when you run it on real TT Elite history, the walk-forward number tells you
whether your 70% is a real, persistent edge or selection luck.

## Two honest caveats
1. **Selection bias is the default.** A 74.5 line is ~a coin flip, so scanning hundreds of
   pairs *will* surface many that hit 70% by chance. `validate.py` is the guard — trust the
   walk-forward/split-half number, not the raw flag count.
2. **Line shading eats the edge.** The book knows the high-scoring pairs too. If a "80% over
   74.5" pair gets posted at **76.5** when they actually play, your historical edge is gone.
   The tool measures the tendency at a *fixed* 74.5; real profit also needs the book's posted
   line to stay near it. Log the line you actually get and compare.

## Match-fixing note
TT Elite is a betting-first amateur league with documented integrity issues (see
`../tennis-betting/reports/table_tennis_scope.md`). Some historical "trends" can reflect
fixed matches that won't repeat. The out-of-sample tests partly guard against this, but keep
stakes small and treat a great backtest with healthy skepticism.
