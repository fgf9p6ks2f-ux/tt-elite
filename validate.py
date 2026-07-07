"""Does the H2H over/under trend actually persist? — the honest test.

Flagging pairs at >=70% over 10-15 games is *selection*: with a coin-flip line, a chunk of
pairs hit 70% by chance, and betting those forward regresses toward ~50% and loses to the
vig. The only thing that matters is whether a trend, once established, PREDICTS unseen
meetings. Two clean tests:

  1. WALK-FORWARD — go meeting by meeting; whenever a pair is already on a >=pct trend,
     "bet" its NEXT (unseen) meeting and record the result. That aggregate hit rate is the
     real out-of-sample number. (A pair's next game is independent of its past under pure
     chance, so a true coin-flip pair scores ~50% here — no leakage.)
  2. SPLIT-HALF — flag on each pair's FIRST half, measure hit rate on its strictly-later
     SECOND half. Persists => real; regresses to the base rate => noise.

Benchmarks: break-even at -110 = 52.38%; and a GLOBAL-RESAMPLE null (every outcome redrawn
at the league base rate, destroying any per-pair tendency) = the hit rate selection alone
buys. Real signal => actual well above break-even AND above the global null.

    python validate.py --line 74.5 --min 10 --pct 0.70
"""
from __future__ import annotations

import argparse
import random
from math import sqrt

from h2h import h2h_records, load

BREAK_EVEN_110 = 0.5238


def walk_forward(rec, min_h2h, pct):
    hits = bets = 0
    for meets in rec.values():
        overs = 0
        for i, (_, _, over) in enumerate(meets):
            if i >= min_h2h:
                po = overs / i
                side_over = po >= 1 - po
                if (po if side_over else 1 - po) >= pct:
                    bets += 1
                    hits += (over == side_over)
            overs += over
    return hits, bets


def split_half(rec, min_half, pct):
    """Flag on first half, test on second half (strictly held out)."""
    hits = bets = 0
    for meets in rec.values():
        n = len(meets)
        if n < 2 * min_half:
            continue
        h = n // 2
        first, second = meets[:h], meets[h:]
        po = sum(o for _, _, o in first) / h
        side_over = po >= 1 - po
        if (po if side_over else 1 - po) >= pct:
            for _, _, over in second:
                bets += 1
                hits += (over == side_over)
    return hits, bets


def global_null(rec, min_h2h, pct, base, test, trials=150, seed=0):
    """Redraw every outcome ~ Bernoulli(base): no per-pair tendency exists. The hit rate
    the flag-then-bet procedure still scores = the pure-selection floor."""
    rng = random.Random(seed)
    rates = []
    shapes = [len(m) for m in rec.values()]
    for _ in range(trials):
        fake = {i: [(None, None, rng.random() < base) for _ in range(n)]
                for i, n in enumerate(shapes)}
        h, b = test(fake, min_h2h, pct)
        if b:
            rates.append(h / b)
    return sum(rates) / len(rates) if rates else float("nan")


def _z(wr, n, p0=BREAK_EVEN_110):
    return (wr - p0) / sqrt(p0 * (1 - p0) / n) if n else 0.0


def report(rows, line, min_h2h, pct):
    rec = h2h_records(rows, line)
    allm = [m for meets in rec.values() for m in meets]
    base = sum(o for _, _, o in allm) / max(len(allm), 1)
    wf_h, wf_b = walk_forward(rec, min_h2h, pct)
    sh_h, sh_b = split_half(rec, min_h2h, pct)
    null = global_null(rec, min_h2h, pct, base, walk_forward)

    print(f"{len(rows)} matches · line {line} · min {min_h2h} H2H · trend ≥{pct*100:.0f}%")
    print(f"league base rate over {line}: {base*100:.1f}%   ·   break-even -110: {BREAK_EVEN_110*100:.1f}%\n")
    if not wf_b:
        print("no walk-forward bets triggered — pull more history (--days) or lower --min.")
        return
    wf = wf_h / wf_b
    roi = (wf * (1 / 0.5238 - 1) - (1 - wf)) * 100
    print(f"WALK-FORWARD  {wf_h}/{wf_b} = {wf*100:.1f}%   (z vs break-even {_z(wf, wf_b):+.1f}"
          f", est ROI at -110 {roi:+.1f}%)")
    if sh_b:
        sh = sh_h / sh_b
        print(f"SPLIT-HALF    {sh_h}/{sh_b} = {sh*100:.1f}%   (flag 1st half, test 2nd half)")
    print(f"GLOBAL NULL   {null*100:.1f}%   (selection floor — no real pair tendency)\n")
    real = wf > BREAK_EVEN_110 and _z(wf, wf_b) > 2 and wf > null + 0.04
    marg = wf > BREAK_EVEN_110 and not real
    print("VERDICT:", "✅ REAL & tradeable — trend persists out-of-sample above break-even"
          if real else "🟨 marginal — above break-even but not clearly beyond selection noise"
          if marg else "❌ NOT an edge — regresses to the selection/base floor, loses to vig")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", type=float, default=74.5)
    ap.add_argument("--min", type=int, default=10)
    ap.add_argument("--pct", type=float, default=0.70)
    ap.add_argument("--league", default=None, help="restrict to one league")
    args = ap.parse_args()
    report(load(league=args.league), args.line, args.min, args.pct)


if __name__ == "__main__":
    main()
