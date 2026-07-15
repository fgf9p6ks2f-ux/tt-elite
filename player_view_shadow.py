"""Player-view SHADOW ledger — validate the player-level signal LIVE, without betting.

The pair-H2H flagger (h2h/check_today) needs a specific PAIR with 12+ meetings and a hard lean, so
it fires rarely (~2-3/day). The player-level view instead uses each PLAYER's own over-rate (30+
games — a much bigger, more stable sample) and flags a match when the two players' combined lean
clears a bar. Walk-forward backtest (2026-07-15): ~62-66% across all leagues at thr 0.62, several
bets/day — far more volume. This logs those flags to a SEPARATE pv_bets table (graded, NOT bet, NOT
pushed) so we can compare live vs the backtest before risking real money. Standing TT prior: back-
tests overstate live, so budget ~3-5 pts below the backtest and only promote if live confirms 60%+.

    python player_view_shadow.py              # flag today's fixtures + grade + rewrite pv.md
    python player_view_shadow.py --backfill    # walk-forward seed pv_bets from the last 21 days
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections import defaultdict
from pathlib import Path

from h2h import DB, load

HERE = Path(__file__).resolve().parent
REPORT = HERE / "pv.md"
LINE = 74.5
THR = 0.62          # combined-lean bar (backtest sweet spot: ~65% hit, real volume)
MIN_G = 30          # min games per player before a lean is trusted
WIN_UNITS = 100.0 / 120.0

DDL = """CREATE TABLE IF NOT EXISTS pv_bets (
    mid TEXT PRIMARY KEY, league TEXT, p1 TEXT, p2 TEXT, side TEXT, line REAL,
    conf REAL, n INTEGER, start_ts INTEGER, flagged_at TEXT,
    total INTEGER, result TEXT, pnl REAL, graded_at TEXT)"""


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()


def _league_rates():
    """{league: {player: [overs, games]}} from ALL settled history."""
    rates = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for _d, p1, p2, tot, lg in load(with_league=True):
        o = 1 if tot > LINE else 0
        for p in (p1, p2):
            r = rates[lg][p.strip()]; r[0] += o; r[1] += 1
    return rates


def _decide_pv(r1, r2):
    """(side, conf) or None from two players' [overs, games] priors (average-combined lean)."""
    if not r1 or not r2 or r1[1] < MIN_G or r2[1] < MIN_G:
        return None
    po = ((r1[0] / r1[1]) + (r2[0] / r2[1])) / 2
    side, conf = ("over", po) if po >= 0.5 else ("under", 1 - po)
    return (side, conf) if conf >= THR else None


def flag_and_log():
    """Flag today's LIVE fixtures with the player-view + log to pv_bets (never bet/pushed)."""
    from check_today import all_fixtures
    rates = _league_rates()
    con = sqlite3.connect(DB); con.execute(DDL)
    ts = _now(); added = 0
    for p1, p2, start_ts, league, mid in all_fixtures():
        if not mid or league not in rates:
            continue
        dec = _decide_pv(rates[league].get(p1.strip()), rates[league].get(p2.strip()))
        if not dec:
            continue
        side, conf = dec
        cur = con.execute(
            "INSERT OR IGNORE INTO pv_bets(mid,league,p1,p2,side,line,conf,n,start_ts,flagged_at,"
            "total,result,pnl,graded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, league, p1, p2, side, LINE, conf,
             min(rates[league][p1.strip()][1], rates[league][p2.strip()][1]),
             int(start_ts) if start_ts else 0, ts, None, None, None, None))
        added += cur.rowcount
    con.commit(); con.close()
    return added


def backfill(days=21):
    """Walk-forward SEED: replay the last `days` of settled matches, flagging each from players'
    PRIOR over-rates only (no leakage), pre-graded. Gives an immediate shadow record to compare."""
    rows = sorted(load(with_league=True), key=lambda r: r[0])
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).date().isoformat()
    rates = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    con = sqlite3.connect(DB); con.execute(DDL); added = 0
    for date, p1, p2, tot, lg in rows:
        if date[:10] >= cutoff:
            dec = _decide_pv(rates[lg].get(p1.strip()), rates[lg].get(p2.strip()))
            if dec:
                side, conf = dec
                mid = f"bf:{lg}:{date}:{p1}:{p2}"          # synthetic key (backfill, no real 24live mid)
                won = (tot > LINE) == (side == "over")
                cur = con.execute(
                    "INSERT OR IGNORE INTO pv_bets(mid,league,p1,p2,side,line,conf,n,start_ts,"
                    "flagged_at,total,result,pnl,graded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, lg, p1, p2, side, LINE, conf,
                     min(rates[lg][p1.strip()][1], rates[lg][p2.strip()][1]), 0, date,
                     tot, "W" if won else "L", WIN_UNITS if won else -1.0, date))
                added += cur.rowcount
        o = 1 if tot > LINE else 0
        for p in (p1, p2):
            r = rates[lg][p.strip()]; r[0] += o; r[1] += 1
    con.commit(); con.close()
    return added


def grade():
    """Settle live-logged pv_bets whose match result has landed (backfill rows are pre-graded)."""
    con = sqlite3.connect(DB); con.execute(DDL)
    con.execute("UPDATE pv_bets SET pnl=? WHERE result='W' AND ABS(COALESCE(pnl,0)-?)>1e-6",
                (WIN_UNITS, WIN_UNITS))
    ts = _now(); graded = 0
    for mid, side, line in con.execute("SELECT mid, side, line FROM pv_bets WHERE result IS NULL").fetchall():
        row = con.execute("SELECT total_points FROM matches WHERE match_id=? AND total_points IS NOT NULL",
                          (mid,)).fetchone()
        if not row:
            continue
        won = (row[0] > line) == (side == "over")
        con.execute("UPDATE pv_bets SET total=?, result=?, pnl=?, graded_at=? WHERE mid=?",
                    (row[0], "W" if won else "L", WIN_UNITS if won else -1.0, ts, mid))
        graded += 1
    con.commit(); con.close()
    return graded


def report():
    con = sqlite3.connect(DB); con.execute(DDL)

    def rec(extra):
        g = con.execute("SELECT result, pnl FROM pv_bets WHERE result IN ('W','L')" + extra).fetchall()
        w = sum(1 for r in g if r[0] == "W"); l = sum(1 for r in g if r[0] == "L")
        return w, l, sum(r[1] or 0 for r in g)

    lw, ll, lpnl = rec(" AND mid NOT LIKE 'bf:%'")        # genuine LIVE shadow (no hindsight)
    bw, bl, bpnl = rec(" AND mid LIKE 'bf:%'")            # walk-forward backfill REFERENCE
    ln, bn = lw + ll, bw + bl
    lines = ["# TT player-view SHADOW — validating, NOT bet", "",
             f"_{_now()} UTC · each match flagged from both players' over-rate (min {MIN_G} games, "
             f"thr {THR}) · logged 1u @ -120. A LIVE test of the player-level signal before it earns real "
             f"money — standing TT prior is that backtests overstate live, so promote only if LIVE holds 60%+._", "",
             "- **LIVE shadow (no hindsight):** " + (f"{lw}-{ll}  ·  **{lpnl:+.2f}u**  ·  hit {lw/ln*100:.0f}%"
              if ln else "0-0  ·  _accumulating — flags log here as fixtures post_")]
    if bn:
        lines.append(f"- _walk-forward reference (backfill, retrospective): {bw}-{bl}  ·  {bpnl:+.2f}u  ·  hit {bw/bn*100:.0f}%_")
    rows = con.execute("SELECT league, SUM(result='W'), SUM(result='L'), SUM(COALESCE(pnl,0)) "
                       "FROM pv_bets WHERE result IN ('W','L') GROUP BY league").fetchall()
    if rows:
        lines += ["", "**By league** (live + reference combined):", "",
                  "| league | W-L | hit | P&L (u) |", "|---|---|---|---|"]
        for lg, xw, xl, xpnl in sorted(rows, key=lambda r: -r[3]):
            hit = f"{xw/(xw+xl)*100:.0f}%" if (xw + xl) else "—"
            lines.append(f"| {lg} | {xw}-{xl} | {hit} | {xpnl:+.2f} |")
    open_n = con.execute("SELECT COUNT(*) FROM pv_bets WHERE result IS NULL").fetchone()[0]
    lines += ["", f"_{open_n} open (awaiting results)_"]
    con.close()
    REPORT.write_text("\n".join(lines) + "\n")
    return lw, ll, lpnl                                   # the LIVE record is what we're validating


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, nargs="?", const=21,
                    help="walk-forward seed pv_bets from the last N days (default 21)")
    args = ap.parse_args()
    if args.backfill:
        print(f"pv backfill: {backfill(args.backfill)} flags seeded (last {args.backfill}d)")
    else:
        print(f"pv shadow: {flag_and_log()} new live flags")
    g = grade(); w, l, pnl = report()
    print(f"pv shadow: {g} graded · record {w}-{l} · {pnl:+.2f}u")


if __name__ == "__main__":
    main()
