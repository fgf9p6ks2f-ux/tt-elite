"""Walk-forward calibration of the real-line probability engine.

Flipping the live flagger to bet against FanDuel's actual line means trusting the
model's P(total > L) at whatever L FanDuel posts (56.5-77.5). There are NO historical
FD lines to backtest bet outcomes against — but P(total > L) itself IS testable: for a
sweep of lines across FanDuel's range, walk the Elite history chronologically and, using
ONLY prior meetings, predict each match's P(over L), then check the predictions against
what actually happened (reliability + Brier vs baselines).

If the shrunk-posterior estimate is well-calibrated and beats the league-base baseline
out-of-sample, the engine is sound at arbitrary lines and the live flip is justified;
the edge magnitude then proves out forward (needs real odds we can't backfill).

    python backtest_realline.py
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent / "tt.sqlite"
K = 16.0                                   # Elite shrink strength (matches LEAGUE_CFG)
MIN_N = 12                                 # only score pairs the live rule would bet
WARMUP = 3000                              # skip until league base(L) has settled
LINES = [56.5, 60.5, 65.5, 70.5, 74.5, 77.5]


def load_elite():
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "SELECT date, p1, p2, total_points FROM matches "
            "WHERE total_points IS NOT NULL AND league LIKE '%TT Elite%' ORDER BY date"
        ).fetchall()
    finally:
        con.close()
    return [(d, p1.strip(), p2.strip(), t) for d, p1, p2, t in rows]


def pair_key(a, b):
    return tuple(sorted((a, b)))


def brier(preds):
    return sum((p - a) ** 2 for p, a in preds) / len(preds) if preds else float("nan")


def reliability(preds, nbins=10):
    """Return [(lo, hi, mean_pred, mean_act, count)] over equal-width prob bins."""
    bins = defaultdict(list)
    for p, a in preds:
        b = min(nbins - 1, int(p * nbins))
        bins[b].append((p, a))
    out = []
    for b in range(nbins):
        v = bins.get(b, [])
        if not v:
            continue
        mp = sum(p for p, _ in v) / len(v)
        ma = sum(a for _, a in v) / len(v)
        out.append((b / nbins, (b + 1) / nbins, mp, ma, len(v)))
    return out


def run():
    rows = load_elite()
    over_cnt = {L: 0 for L in LINES}       # running league counts (for base(L))
    seen = 0
    pair_tots = defaultdict(list)          # pair -> prior totals (chronological)

    # predictions[L] = [(p_shrunk, actual)]; also baselines for comparison
    P_shrunk = {L: [] for L in LINES}
    P_base = {L: [] for L in LINES}
    P_raw = {L: [] for L in LINES}

    for d, p1, p2, tot in rows:
        pk = pair_key(p1, p2)
        prior = pair_tots[pk]
        n = len(prior)
        # score BEFORE updating state (strict walk-forward, prior-only)
        if seen >= WARMUP and n >= MIN_N:
            for L in LINES:
                base = over_cnt[L] / seen
                overs = sum(1 for t in prior if t > L)
                raw = overs / n
                shrunk = (overs + K * base) / (n + K)
                act = 1.0 if tot > L else 0.0
                P_shrunk[L].append((shrunk, act))
                P_base[L].append((base, act))
                P_raw[L].append((raw, act))
        # update running state
        for L in LINES:
            if tot > L:
                over_cnt[L] += 1
        seen += 1
        pair_tots[pk].append(tot)

    npred = len(P_shrunk[LINES[0]])
    print(f"=== Real-line calibration — walk-forward OOS ===")
    print(f"scored {npred} (match × line) predictions on pairs with >={MIN_N} prior "
          f"meetings, after {WARMUP}-match warmup\n")

    print(f"{'line':>6} {'n':>7} {'Brier:shrunk':>13} {'base':>8} {'rawpair':>9}   "
          f"{'shrunk vs base':>15}")
    for L in LINES:
        bs, bb, br = brier(P_shrunk[L]), brier(P_base[L]), brier(P_raw[L])
        edge = (bb - bs) / bb * 100 if bb else 0
        tag = "✅" if bs < bb and bs < br else ("~" if bs < bb else "⚠️")
        print(f"{L:>6} {len(P_shrunk[L]):>7} {bs:>13.4f} {bb:>8.4f} {br:>9.4f}   "
              f"{edge:>+13.1f}% {tag}")

    print("\n=== Reliability (shrunk), pooled across all lines ===")
    pooled = [pa for L in LINES for pa in P_shrunk[L]]
    print(f"{'pred bin':>12} {'mean_pred':>10} {'mean_actual':>12} {'n':>8}  {'gap':>7}")
    for lo, hi, mp, ma, c in reliability(pooled):
        print(f"{lo:.1f}-{hi:.1f}     {mp:>10.3f} {ma:>12.3f} {c:>8}  {ma-mp:>+7.3f}")
    print(f"\npooled Brier shrunk={brier(pooled):.4f}  "
          f"base={brier([pa for L in LINES for pa in P_base[L]]):.4f}  "
          f"raw={brier([pa for L in LINES for pa in P_raw[L]]):.4f}")


if __name__ == "__main__":
    run()
