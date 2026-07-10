"""Leak-free walk-forward backtest of the TT per-league flag model + a recalibration.

For each league, replay every match in date order; each bet uses ONLY that pair's PRIOR
meetings (h2h.decide, the exact live rule) → grade vs the actual total at 74.5. Split the
flagged bets by date into TRAIN (first 70%) and a never-touched TEST tail (last 30%).

Reports, per league, on the HELD-OUT TEST set:
  · CURRENT   — the live LEAGUE_CFG (is its claimed edge real out-of-sample?)
  · RECAL     — raise each league's confidence floor on TRAIN only to the level that clears
                a hit-rate bar with volume; leagues that can't clear it are turned OFF.
Also splits TEST by data SOURCE (24live vs Sofascore backfill) — the live bets are 24live,
so if the edge lives only in Sofascore rows the backtest won't transfer to live.

    python backtest_recalibrate.py
"""
import sqlite3
from collections import defaultdict

import h2h

DB = "tt.sqlite"
LINE = 74.5
BE = 100.0 / (100.0 + 110.0) * 100 / (100/210*0 + 1)   # placeholder, set explicitly below
BE = 1.0 / 1.9091 * 100                                # break-even hit% at -110 = 52.38%
LEAGUES = ["TT Elite Series", "Setka Cup", "Czech Liga Pro", "TT Cup", "Setka Women"]


def load_league(league):
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT date, p1, p2, total_points, match_id FROM matches "
        "WHERE total_points IS NOT NULL AND league LIKE ? ORDER BY date, match_id",
        (f"%{league}%",)).fetchall()
    con.close()
    return rows


def walkforward(rows, cfg):
    """[(date, side, conf, won, is_24live)] — leak-free, each bet uses only prior meetings."""
    hist = defaultdict(list)
    bets = []
    for date, p1, p2, tot, mid in rows:
        key = h2h.pair_key(p1, p2)
        d = h2h.decide(hist[key], cfg)
        if d:
            side, conf, _n, _r = d
            won = (tot > LINE) == (side == "over")
            bets.append((date, side, conf, won, str(mid).startswith("24l_")))
        hist[key].append((date, tot, tot > LINE))
    return bets


def roi(bets):
    """(n, hit%, ROI%) at -110 (win +0.909u, lose -1u)."""
    n = len(bets)
    if not n:
        return 0, float("nan"), float("nan")
    w = sum(1 for b in bets if b[3])
    r = (w * (100.0 / 110.0) - (n - w)) / n * 100
    return n, 100.0 * w / n, r


def recalibrate(train, cfg):
    """Raise the confidence floor on TRAIN to the lowest level that clears a 55% hit bar with
    >=40 bets (comfortably above the 52.4% break-even). Returns the floor, or None = league OFF."""
    grid = [round(cfg.get("thr", 0.65) + 0.025 * i, 3) for i in range(0, 13)]  # thr .. +0.30
    best = None
    for c in grid:
        f = [b for b in train if b[2] >= c]
        n, hit, r = roi(f)
        if n >= 40 and hit >= 55.0:
            best = c                      # lowest floor that clears the bar = most volume
            break
    return best


def line(label, tup):
    n, hit, r = tup
    if not n:
        return f"    {label:14} —  (no bets)"
    flag = "  +EV" if hit > BE else "  -EV"
    return f"    {label:14} n{n:<5} {hit:5.1f}%  ROI {r:+6.1f}%{flag}"


def main():
    print(f"\nTT WALK-FORWARD BACKTEST — leak-free, line {LINE}, break-even @ -110 = {BE:.1f}%")
    print("TRAIN = first 70% of each league's flagged bets by date · TEST = last 30% (held out)\n")
    agg_cur, agg_rec = [], []
    for L in LEAGUES:
        rows = load_league(L)
        cfg = h2h.LEAGUE_CFG.get(L, h2h.DEFAULT_CFG)
        bets = walkforward(rows, cfg)
        bets.sort(key=lambda b: b[0])
        if len(bets) < 60:
            print(f"  {L}: only {len(bets)} lifetime flags — too thin to split, skipping\n")
            continue
        cut = int(len(bets) * 0.70)
        train, test = bets[:cut], bets[cut:]
        floor = recalibrate(train, cfg)
        test_cur = test
        test_rec = [b for b in test if floor is not None and b[2] >= floor]
        agg_cur += test_cur
        agg_rec += test_rec
        print(f"  {L}  (lifetime {len(bets)} flags; TRAIN {len(train)} / TEST {len(test)})")
        print(line("CURRENT cfg", roi(test_cur)))
        if floor is None:
            print(f"    RECAL          -> league OFF (TRAIN never cleared 55% at any conf floor)")
        else:
            print(line(f"RECAL c>={floor}", roi(test_rec)))
        # source transfer check on the TEST tail
        te24 = [b for b in test_cur if b[4]]
        tesof = [b for b in test_cur if not b[4]]
        print(f"    source split:  24live {roi(te24)[0]} bets {roi(te24)[1]:.0f}%  ·  "
              f"Sofascore {roi(tesof)[0]} bets {roi(tesof)[1]:.0f}%")
        print()
    print("  ===== AGGREGATE on the held-out TEST tail (all leagues) =====")
    print(line("CURRENT cfg", roi(agg_cur)))
    print(line("RECALIBRATED", roi(agg_rec)))


if __name__ == "__main__":
    main()
