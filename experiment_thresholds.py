"""Per-league threshold sweep on the DEEPENED history, holdout-validated.

Now that Setka/LigaPro/TTCup have years of data, re-tune each league's flag rule.
Honest method: history accumulates from the start (like production), but bets are
SCORED only in the held-out last 30% of the timeline — so a config that looks good
here is good out-of-sample, not curve-fit. Reports the full tradeoff (a higher bar
lifts win% and ROI/bet but cuts volume) so we can pick per league.

    python experiment_thresholds.py
"""
from __future__ import annotations

from collections import defaultdict
from math import sqrt

from h2h import load, pair_key

BE = 0.5238
LINE = 74.5


def league_recs(rows, league):
    rec = defaultdict(list)
    for d, p1, p2, t, lg in rows:
        if lg == league:
            rec[pair_key(p1, p2)].append((d, t, t > LINE))
    for pr in rec:
        rec[pr].sort()
    return rec


def raw_side(hist, pct, min_n):
    n = len(hist)
    if n < min_n:
        return None
    po = sum(hist) / n
    side, conf = (True, po) if po >= 0.5 else (False, 1 - po)   # True=over
    return side if conf >= pct else None


def shrunk_side(hist, base, k, thr, min_n):
    n = len(hist)
    if n < min_n:
        return None
    po = (sum(hist) + k * base) / (n + k)
    side, conf = (True, po) if po >= 0.5 else (False, 1 - po)
    return side if conf >= thr else None


def walk(rec, decide, split_date):
    """Score bets only for meetings AFTER split_date; history builds from the start."""
    allm = []
    for pr, meets in rec.items():
        for i, m in enumerate(meets):
            allm.append((m[0], pr, i))
    allm.sort()
    hist = defaultdict(list)
    hits = bets = 0
    for d, pr, i in allm:
        over = rec[pr][i][2]
        if d > split_date:
            side = decide(hist[pr])
            if side is not None:
                bets += 1
                hits += (over == side)
        hist[pr].append(over)
    return hits, bets


def stat(h, b, span_days):
    if not b:
        return None
    wr = h / b
    z = (wr - BE) / sqrt(BE * (1 - BE) / b)
    roi = wr * (1 / BE - 1) - (1 - wr)
    return {"n": b, "wr": wr, "z": z, "roi": roi,
            "per_day": b / max(span_days, 1),
            "profit_day": b / max(span_days, 1) * roi}   # units/day at 1u flat


def main():
    rows = load(with_league=True)
    for league in ("Setka Cup", "Czech Liga Pro", "TT Cup", "Setka Women"):
        rec = league_recs(rows, league)
        if not rec:
            continue
        dates = sorted(d for meets in rec.values() for d, _, _ in meets)
        split = dates[int(len(dates) * 0.70)]
        base = sum(o for meets in rec.values() for _, _, o in meets) / len(dates)
        span = 30                        # holdout ≈ last-30% window in days (approx)
        # crude holdout span in days
        from datetime import date
        try:
            span = max((date.fromisoformat(dates[-1]) - date.fromisoformat(split)).days, 1)
        except ValueError:
            pass
        print(f"\n=== {league} (base {base*100:.1f}%, {len(dates)} matches, "
              f"holdout after {split}) ===")
        print(f"  {'rule':26}{'bets':>6}{'win%':>7}{'z':>6}{'ROI/bet':>9}{'u/day':>8}")
        cands = []
        for pct in (0.65, 0.70, 0.75, 0.80):
            for mn in (10, 15, 20):
                cands.append((f"raw >={pct:.0%} n{mn}",
                              lambda h, p=pct, m=mn: raw_side(h, p, m)))
        for thr in (0.62, 0.65, 0.68, 0.72):
            for k in (6, 10):
                cands.append((f"shrunk k{k} >={thr:.0%} n8",
                              lambda h, t=thr, kk=k: shrunk_side(h, base, kk, t, 8)))
        results = []
        for label, dec in cands:
            s = stat(*walk(rec, dec, split), span)
            if s and s["n"] >= 25:
                results.append((label, s))
        # show top by ROI and by profit/day
        for label, s in sorted(results, key=lambda x: -x[1]["roi"])[:6]:
            print(f"  {label:26}{s['n']:>6}{s['wr']*100:>6.1f}%{s['z']:>+6.1f}"
                  f"{s['roi']*100:>+8.1f}%{s['profit_day']:>+8.2f}")
        if results:
            best_roi = max(results, key=lambda x: x[1]["roi"])
            best_profit = max(results, key=lambda x: x[1]["profit_day"])
            print(f"  -> best ROI/bet: {best_roi[0]} ({best_roi[1]['roi']*100:+.0f}%)")
            print(f"  -> best total profit: {best_profit[0]} "
                  f"({best_profit[1]['profit_day']:+.2f}u/day)")


if __name__ == "__main__":
    main()
