"""H2H over/under 74.5 flagger for TT Elite Series.

Your strategy: for each specific pair of players, take their historical meetings, compute
how often the total points went over/under the line, and flag pairs that hit one side
>=70% of the time with a minimum number of meetings. Also computes a player-level view
(each player's overall over-rate), which has a bigger sample and is often more stable.

    python h2h.py                       # flag from tt.sqlite
    python h2h.py --line 74.5 --min 12 --pct 0.70
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "tt.sqlite"


def load(db=DB, league=None):
    con = sqlite3.connect(db)
    q = "SELECT date, p1, p2, total_points FROM matches WHERE total_points IS NOT NULL"
    args = []
    if league:
        q += " AND league LIKE ?"
        args.append(f"%{league}%")
    try:
        rows = con.execute(q + " ORDER BY date", args).fetchall()
    finally:
        con.close()
    return rows                                   # [(date, p1, p2, total)]


def pair_key(a, b):
    return tuple(sorted((a.strip(), b.strip())))


def h2h_records(rows, line):
    """{pair: [(date, total, over_bool)]} chronological."""
    rec = defaultdict(list)
    for date, p1, p2, tot in rows:
        rec[pair_key(p1, p2)].append((date, tot, tot > line))
    return rec


def flag_pairs(rows, line=74.5, min_h2h=10, pct=0.70):
    rec = h2h_records(rows, line)
    flagged = []
    for pair, meets in rec.items():
        n = len(meets)
        if n < min_h2h:
            continue
        overs = sum(1 for _, _, o in meets if o)
        po = overs / n
        side, hit = ("over", po) if po >= 1 - po else ("under", 1 - po)
        if hit >= pct:
            avg = sum(t for _, t, _ in meets) / n
            flagged.append({"pair": pair, "n": n, "side": side, "hit_rate": hit,
                            "over_pct": po, "avg_total": avg})
    return sorted(flagged, key=lambda x: (-x["hit_rate"], -x["n"]))


def player_view(rows, line=74.5, min_games=30, pct=0.62):
    tally = defaultdict(lambda: [0, 0])          # player -> [overs, games]
    for _, p1, p2, tot in rows:
        for p in (p1, p2):
            tally[p.strip()][0] += tot > line
            tally[p.strip()][1] += 1
    out = []
    for p, (ov, g) in tally.items():
        if g >= min_games:
            po = ov / g
            side, hit = ("over", po) if po >= 1 - po else ("under", 1 - po)
            if hit >= pct:
                out.append({"player": p, "games": g, "side": side, "hit_rate": hit})
    return sorted(out, key=lambda x: (-x["hit_rate"], -x["games"]))


def check_pair(rows, a, b, line=74.5):
    meets = h2h_records(rows, line).get(pair_key(a, b), [])
    if not meets:
        return f"no history for {a} vs {b}"
    n = len(meets); overs = sum(o for _, _, o in meets); po = overs / n
    side, hit = ("OVER", po) if po >= 1 - po else ("UNDER", 1 - po)
    avg = sum(t for _, t, _ in meets) / n
    flag = "✅ FLAG" if (hit >= 0.70 and n >= 10) else "— (below threshold)"
    return (f"{a} vs {b}: {n} meetings, {overs} over / {n-overs} under {line} "
            f"({po*100:.0f}% over) · lean {side} {hit*100:.0f}% · avg {avg:.1f} · {flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", type=float, default=74.5)
    ap.add_argument("--min", type=int, default=10, help="min head-to-head meetings")
    ap.add_argument("--pct", type=float, default=0.70, help="min one-side hit rate")
    ap.add_argument("--pair", nargs=2, metavar=("A", "B"), help="look up one matchup")
    ap.add_argument("--league", help="filter to one league (substring, e.g. 'TT Elite')")
    args = ap.parse_args()

    rows = load(league=args.league)
    if args.pair:
        print(check_pair(rows, args.pair[0], args.pair[1], args.line))
        return
    print(f"{len(rows)} matches loaded · line {args.line} · "
          f"min {args.min} H2H · flag ≥{args.pct*100:.0f}%\n")
    flagged = flag_pairs(rows, args.line, args.min, args.pct)
    print(f"=== {len(flagged)} flagged H2H pairs ===")
    print(f"{'matchup':<40}{'n':>4}{'side':>7}{'hit':>7}{'avg':>7}")
    for f in flagged[:60]:
        a, b = f["pair"]
        print(f"{a+' vs '+b:<40}{f['n']:>4}{f['side']:>7}{f['hit_rate']*100:>6.0f}%{f['avg_total']:>7.1f}")
    print("\n⚠ raw flags include coin-flip noise — run validate.py to see which persist.")


if __name__ == "__main__":
    main()
