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

# Per-league flag rules — chosen by walk-forward tuning on the first 70% of each
# league's history and CONFIRMED on the held-out last 30% (2026-07-08 sweep):
#   TT Elite  beta-shrunk posterior (k=16 toward league base, thr .675, n>=12)
#             -> holdout 73.6% on 526 bets (+40.5% ROI) vs 65.1% for the raw rule.
#   Setka     lighter shrink (k=8, thr .65, n>=8) -> 60.6% on 198 (raw was 59.7%/67).
#   Liga Pro  raw rule n>=15 — every shrunk config DEGRADED in its holdout (overfit).
#   TT Cup    raw rule n>=12 — shrinkage hurt here too.
# 'base' = league base over-rate at line 74.5, pinned so live flags don't drift with
# each ingest (recompute deliberately, not implicitly).
LEAGUE_CFG = {
    "TT Elite Series": {"rule": "shrunk", "k": 16.0, "thr": 0.675, "min": 12, "base": 0.512},
    # Retuned 2026-07-09 on the DEEPENED Sofascore history (50k+ matches back to 2020),
    # holdout-validated (tune on first 70% of the timeline, score last 30%). The shrunk
    # rule + a higher bar filters to genuine signal now that samples are deep — every
    # league jumped in win% and ROI vs the old shallow-window configs:
    #   Setka  60.9%/+16% -> 70.2%/+34%   ·  TT Cup   63%/+21% -> 77.1%/+47%
    #   LigaPro 58.6%/+12% -> 62.7%/+20%  ·  SetkaW   61.8%/+18% -> 67.2%/+28%
    # Fewer, higher-conviction bets (Setka ~12/day -> a handful). No longer thin-margin,
    # so the ·VOL volume tag is dropped.
    "Setka Cup":       {"rule": "shrunk", "k": 10.0, "thr": 0.72, "min": 8, "base": 0.515},
    "Czech Liga Pro":  {"rule": "shrunk", "k": 6.0,  "thr": 0.72, "min": 8, "base": 0.512},
    "TT Cup":          {"rule": "shrunk", "k": 10.0, "thr": 0.68, "min": 8, "base": 0.536},
    "Setka Women":     {"rule": "shrunk", "k": 10.0, "thr": 0.65, "min": 8, "base": 0.450},
    # TT Challenger: COLLECT-ONLY. Too young to validate (1 pair with >=10 meetings),
    # and its base over-74.5 rate is 36.5% — books post much lower totals here, so the
    # fixed-74.5 flag would be nonsense. Revisit ~Oct 2026 with a league-specific line.
    "TT Challenger Series": {"rule": "off"},
    # ESportsBattle esoccer/ebasketball: COLLECT-ONLY. Books post DYNAMIC per-match
    # totals here (not a lazy fixed line like TT's 74.5), so flags require the posted
    # line as an input. Collected free via source_esb; validation in validate_esb.py.
    "Esoccer Battle":     {"rule": "off"},
    "Ebasketball Battle": {"rule": "off"},
}
DEFAULT_CFG = {"rule": "raw", "pct": 0.70, "min": 15}


def kelly_units(p, dec_odds=1.9091, bankroll_u=None, frac=0.25, cred=0.45,
                cap=3.0, floor=0.5):
    """Recommended stake in units (1u = $100) — fractional Kelly, made honest:

    f* = (p·b − q)/b is optimal ONLY if p is exact. Ours are threshold-selected
    estimates, so raw Kelly overbets. Three standard corrections:
      · credibility shrink: p' = BE + cred·(p − BE)  (45% credibility — tuned so sizes
        SPREAD across the 62-90% confidence range instead of saturating the cap —
        absorbs selection bias and estimation error)
      · quarter Kelly (frac=0.25) — the practitioner default for noisy edges
      · cap at 3u (void/limit risk on these markets) and floor at 0.5u
    Bankroll defaults to 50u ($5,000); override with env BANKROLL_UNITS."""
    import os
    bankroll_u = bankroll_u or float(os.environ.get("BANKROLL_UNITS", 50))
    b = dec_odds - 1.0
    be = 1.0 / dec_odds
    p_adj = be + cred * (p - be)
    f = (p_adj * b - (1 - p_adj)) / b
    if f <= 0:
        return floor
    return max(floor, min(cap, round(frac * f * bankroll_u * 2) / 2))


def decide(meets, cfg):
    if cfg.get("rule") == "off":               # league is collect-only, never flags
        return None
    return _decide(meets, cfg)


def _decide(meets, cfg):
    """Apply a league's flag rule to a pair's chronological meetings.
    Returns (side, strength, n, raw_side_rate) or None. 'strength' is the rule's own
    confidence (posterior prob for shrunk, raw rate for raw); raw_side_rate is the
    unshrunk H2H hit rate of the CHOSEN side (both numbers read the same way)."""
    n = len(meets)
    if n < cfg["min"]:
        return None
    overs = sum(o for _, _, o in meets)
    raw = overs / n
    if cfg["rule"] == "shrunk":
        po = (overs + cfg["k"] * cfg["base"]) / (n + cfg["k"])
        side, s = ("over", po) if po >= 0.5 else ("under", 1 - po)
        rside = raw if side == "over" else 1 - raw
        return (side, s, n, rside) if s >= cfg["thr"] else None
    side, s = ("over", raw) if raw >= 0.5 else ("under", 1 - raw)
    return (side, s, n, s) if s >= cfg["pct"] else None


def load(db=DB, league=None, with_league=False):
    con = sqlite3.connect(db)
    cols = "date, p1, p2, total_points" + (", league" if with_league else "")
    q = f"SELECT {cols} FROM matches WHERE total_points IS NOT NULL"
    args = []
    if league:
        q += " AND league LIKE ?"
        args.append(f"%{league}%")
    try:
        rows = con.execute(q + " ORDER BY date", args).fetchall()
    finally:
        con.close()
    return rows                    # [(date, p1, p2, total)] (+ league if with_league)


def pair_key(a, b):
    return tuple(sorted((a.strip(), b.strip())))


def h2h_records(rows, line):
    """{pair: [(date, total, over_bool)]} chronological. Rows may carry extra
    trailing columns (e.g. league) — only the first four are used."""
    rec = defaultdict(list)
    for date, p1, p2, tot, *_ in rows:
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
