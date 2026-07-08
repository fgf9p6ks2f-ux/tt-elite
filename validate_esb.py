"""Is there a bettable H2H-totals edge in ESB esoccer / ebasketball? — honest test.

The TT playbook worked because books posted a LAZY FIXED line (74.5 for everyone), so
a pair's mean total alone beat it. Esoccer/ebasketball totals are dynamic per match,
so the edge depends on HOW the book sets its line. We can't see historical lines for
free, so we bracket reality between two simulated books:

  LAZY   line fixed at the league median total for every match. If pair history beats
         this, there's edge whenever a book prices off league-wide numbers. UPPER bound.
  SHARP  line re-set each match at the pair's own trailing-mean total (rounded to .5).
         Beating this requires predicting a pair's DEVIATION from its own history —
         only streak/script autocorrelation survives. LOWER bound.

Real books sit between. Verdicts: edge vs LAZY only => need real posted lines before
betting (if books are pair-aware, no edge). Edge vs SHARP too => strong, script-like
signal worth pursuing aggressively.

Walk-forward as always: flag from PAST meetings only, score the NEXT one. Within-day
ordering uses the numeric match id (ids are assigned in schedule order).

    python validate_esb.py
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from math import sqrt
from pathlib import Path

DB = Path(__file__).resolve().parent / "tt.sqlite"
BE = 0.5238


def load(league):
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT match_id, date, p1, p2, total_points FROM matches "
        "WHERE league=? AND total_points IS NOT NULL", (league,)).fetchall()
    con.close()
    def seq(mid):                          # esb_f_2161802 -> 2161802
        try:
            return int(mid.rsplit("_", 1)[1])
        except ValueError:
            return 0
    rows.sort(key=lambda r: (r[1], seq(r[0])))
    return [(d, tuple(sorted((a, b))), t) for _, d, a, b, t in rows]


def half_line(x):
    """Nearest .5-ending line to x (so pushes are impossible)."""
    return round(x - 0.5) + 0.5


def walk(matches, line_of, min_n=10, pct=0.70):
    """line_of(pair_history, league_median) -> line for the NEXT meeting."""
    hist = defaultdict(list)
    med = sorted(t for _, _, t in matches)[len(matches) // 2]
    hits = bets = 0
    for _, pair, tot in matches:
        h = hist[pair]
        if len(h) >= min_n:
            line = line_of(h, med)
            overs = sum(1 for t in h if t > line)
            po = overs / len(h)
            side, conf = ("over", po) if po >= 0.5 else ("under", 1 - po)
            if conf >= pct and tot != line:
                bets += 1
                hits += ((tot > line) == (side == "over"))
        h.append(tot)
    return hits, bets


def lazy(h, med):
    return half_line(med)


def sharp(h, med):
    k = h[-10:]
    return half_line(sum(k) / len(k))


def fmt(hits, bets):
    if not bets:
        return "0 bets"
    wr = hits / bets
    z = (wr - BE) / sqrt(BE * (1 - BE) / bets)
    roi = (wr * (1 / BE - 1) - (1 - wr)) * 100
    return f"{hits}/{bets} = {wr*100:.1f}%   z={z:+.1f}   ROI={roi:+.1f}%"


def main():
    for league in ("Esoccer Battle", "Ebasketball Battle"):
        ms = load(league)
        if len(ms) < 500:
            print(f"\n{league}: only {len(ms)} matches — keep collecting")
            continue
        pairs = defaultdict(int)
        for _, p, _ in ms:
            pairs[p] += 1
        med = sorted(t for _, _, t in ms)[len(ms) // 2]
        print(f"\n=== {league}: {len(ms)} matches, {len(pairs)} pairs "
              f"({sum(1 for v in pairs.values() if v >= 20)} with ≥20 meetings), "
              f"median total {med} ===")
        for label, fn in (("LAZY  (fixed league line)", lazy),
                          ("SHARP (pair-aware line)  ", sharp)):
            for min_n, pct in ((10, .70), (15, .70), (15, .75)):
                h, b = walk(ms, fn, min_n, pct)
                print(f"  {label} n≥{min_n} ≥{pct:.0%}:  {fmt(h, b)}")
        print("  verdict: bettable only if SHARP shows signal, or LAZY holds AND the "
              "book's real lines prove league-flat (needs a posted-line sample).")


if __name__ == "__main__":
    main()
