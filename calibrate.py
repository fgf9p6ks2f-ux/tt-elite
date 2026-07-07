"""Calibration grid — find the sweet spot for the H2H over/under strategy.

For every combination of flag threshold (70/75/80/85% historical hit rate) and minimum H2H
sample (10/15/20/25 meetings), reports the OUT-OF-SAMPLE win rate (walk-forward: bet the
next meeting whenever a pair is already on that trend). Higher thresholds/samples = stronger
signal but fewer bets — this shows the tradeoff and where the edge peaks.

    python calibrate.py --line 74.5 --league "TT Elite"
"""
from __future__ import annotations

import argparse
from math import sqrt

from h2h import h2h_records, load
from validate import split_half, walk_forward

BE = 0.5238                       # break-even at -110


def z(wr, n):
    return (wr - BE) / sqrt(BE * (1 - BE) / n) if n else 0.0


def roi(wr):
    return (wr * (1 / BE - 1) - (1 - wr)) * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", type=float, default=74.5)
    ap.add_argument("--league", default="TT Elite")
    ap.add_argument("--pcts", default="0.70,0.75,0.80,0.85")
    ap.add_argument("--mins", default="10,15,20,25")
    ap.add_argument("--min-vol", type=int, default=100, help="min bets to rank a cell")
    args = ap.parse_args()
    PCTS = [float(x) for x in args.pcts.split(",")]
    MINS = [int(x) for x in args.mins.split(",")]

    rows = load(league=args.league)
    rec = h2h_records(rows, args.line)
    print(f"\n{len(rows):,} {args.league} matches · line {args.line} · "
          f"break-even (-110) {BE*100:.1f}%")

    # grid of walk-forward win rate
    print("\nOUT-OF-SAMPLE win rate (walk-forward) — cell = win% · n bets\n")
    print("   min H2H |" + "".join(f"   flag ≥{int(p*100)}%   " for p in PCTS))
    print("   --------+" + "-" * (16 * len(PCTS)))
    grid = {}
    for m in MINS:
        cells = []
        for p in PCTS:
            h, b = walk_forward(rec, m, p)
            wr = h / b if b else 0.0
            grid[(m, p)] = (wr, b)
            cells.append(f"{wr*100:5.1f}% n={b:<5}".ljust(16))
        print(f"   {m:>7} |" + "".join(cells))

    # rank cells with enough volume by ROI, confirm with split-half
    print(f"\nSWEET SPOT — cells with ≥{args.min_vol} bets, ranked by ROI:\n")
    ranked = []
    for (m, p), (wr, b) in grid.items():
        if b >= args.min_vol:
            sh_h, sh_b = split_half(rec, m, p)
            sh = sh_h / sh_b if sh_b else 0.0
            ranked.append((roi(wr), wr, z(wr, b), b, m, p, sh, sh_b))
    ranked.sort(reverse=True)
    print(f"   {'config':<22}{'win%':>7}{'ROI':>8}{'z':>7}{'bets':>7}{'split-half':>13}")
    for r, wr, zz, b, m, p, sh, shb in ranked:
        print(f"   min {m:>2}, flag ≥{int(p*100)}%   {wr*100:>6.1f}%{r:>+7.1f}%{zz:>+7.1f}"
              f"{b:>7}{sh*100:>9.1f}% (n={shb})")
    if ranked:
        r, wr, zz, b, m, p, sh, shb = ranked[0]
        print(f"\n→ best risk-adjusted: min {m} H2H, flag ≥{int(p*100)}% "
              f"→ {wr*100:.1f}% OOS ({b} bets, ROI {r:+.1f}%, split-half {sh*100:.1f}%)")


if __name__ == "__main__":
    main()
