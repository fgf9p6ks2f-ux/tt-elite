"""TT paper ledger — the live, audited track record of the flags.

Every actionable flag check_today produces is logged here as a 1-unit ($100) paper bet
at -120, keyed by the exact 24live match id — so grading is against precisely the match
that was flagged (same-day rematches between a pair are common and would otherwise
cross-grade). The daily workflow grades bets as results land in tt.sqlite and rewrites
paper.md. This is the honest live counterpart to the historical validation: if the edge
decays, it shows up here first.

    python paper_ledger.py            # grade whatever has results + rewrite paper.md
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from h2h import DB

HERE = Path(__file__).resolve().parent
REPORT = HERE / "paper.md"
UNIT_USD = 100.0
WIN_UNITS = 100.0 / 120.0            # flat 1u at -120 — conservative default (TT totals juice ~-115/-120,
                                     # and real book lines release too erratically to collect reliably)


DDL = """CREATE TABLE IF NOT EXISTS paper_bets (
    mid TEXT PRIMARY KEY, league TEXT, p1 TEXT, p2 TEXT, side TEXT, line REAL,
    conf REAL, raw REAL, n INTEGER, start_ts INTEGER, flagged_at TEXT,
    total INTEGER, result TEXT, pnl REAL, graded_at TEXT)"""


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()


def log_flags(bets, line=None):
    """Record each flagged bet once (PK = its source match id). `line` is the default;
    a bet may carry its own (the ESB line-conditional flags bet pair-specific lines)."""
    con = sqlite3.connect(DB)
    con.execute(DDL)
    ts = _now()
    added = 0
    for b in bets:
        if not b.get("mid"):
            continue
        cur = con.execute(
            "INSERT OR IGNORE INTO paper_bets "
            "(mid, league, p1, p2, side, line, conf, raw, n, start_ts, flagged_at, "
            " total, result, pnl, graded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["mid"], b["league"], b["p1"], b["p2"], b["side"],
             b.get("line", line), b["hit"], b["raw"], b["n"], b["ts"], ts,
             None, None, None, None))
        added += cur.rowcount
    con.commit()
    con.close()
    return added


def grade():
    """Settle open paper bets whose match result has landed in tt.sqlite."""
    con = sqlite3.connect(DB)
    con.execute(DDL)
    # re-price every settled WIN to the current default (idempotent) — so a price change
    # (-110 -> -120) applies to the WHOLE ledger, not just bets settled from here on. Losses = -1u.
    con.execute("UPDATE paper_bets SET pnl=? WHERE result='W' AND ABS(COALESCE(pnl,0)-?)>1e-6",
                (WIN_UNITS, WIN_UNITS))
    con.commit()
    ts = _now()
    graded = 0
    for mid, side, line in con.execute(
            "SELECT mid, side, line FROM paper_bets WHERE result IS NULL").fetchall():
        row = con.execute("SELECT total_points FROM matches WHERE match_id=? "
                          "AND total_points IS NOT NULL", (mid,)).fetchone()
        if not row:
            continue
        total = row[0]
        won = (total > line) == (side == "over")
        con.execute("UPDATE paper_bets SET total=?, result=?, pnl=?, graded_at=? "
                    "WHERE mid=?",
                    (total, "W" if won else "L", WIN_UNITS if won else -1.0, ts, mid))
        graded += 1
    con.commit()
    con.close()
    return graded


# A usable real line = one within the range the user's books actually post (they cap ~78.5).
# (Real-line grading removed 2026-07-11: books release TT totals too erratically to collect —
#  many never post one, some only go live at match start. The ledger is now a single honest flat
#  proxy: 1u at -120 graded vs the actual match total at 74.5. See WIN_UNITS above.)


def _agg(con, where="", args=()):
    g = con.execute(f"SELECT result, pnl FROM paper_bets WHERE result IS NOT NULL{where}",
                    args).fetchall()
    w = sum(1 for r in g if r[0] == "W")
    l = sum(1 for r in g if r[0] == "L")
    pnl = sum(r[1] or 0 for r in g)
    return w, l, pnl


def report():
    con = sqlite3.connect(DB)
    con.execute(DDL)
    # split the headline: the BETTABLE record (active leagues we actually push) vs SHADOW leagues
    # (still logged + graded here to keep validating the cut, but not bet). Shadow set = LEAGUE_CFG.
    from h2h import LEAGUE_CFG
    shadow = sorted(lg for lg, c in LEAGUE_CFG.items() if c.get("tier") == "shadow")
    ph = ",".join("?" * len(shadow)) if shadow else "''"

    def rec(op):
        r = con.execute(f"SELECT COALESCE(SUM(result='W'),0), COALESCE(SUM(result='L'),0), "
                        f"COALESCE(SUM(pnl),0) FROM paper_bets WHERE result IS NOT NULL "
                        f"AND league {op} ({ph})", tuple(shadow)).fetchone()
        return r[0], r[1], r[2]

    aw, al, apnl = rec("NOT IN")                          # bettable leagues (Elite + Setka Cup)
    sw, sl, spnl = rec("IN")                              # shadow leagues (validating only)
    n = aw + al
    open_n = con.execute(f"SELECT COUNT(*) FROM paper_bets WHERE result IS NULL "
                         f"AND league NOT IN ({ph})", tuple(shadow)).fetchone()[0]
    _sl = {"Czech Liga Pro": "Liga Pro", "Setka Women": "Setka W"}
    lines = ["# TT paper ledger — live flag track record", "",
             f"_{_now()} UTC · every flag logged as 1u ($100) at -120 · this is the live "
             f"out-of-sample test of the league rules_", "",
             f"- **Bet record (Elite + Setka Cup):** {aw}-{al}"
             f"  ·  **P&L:** {apnl:+.2f}u (${apnl*UNIT_USD:+,.0f})"
             + (f"  ·  **hit {aw/n*100:.1f}%** (break-even 52.4%)" if n else "")
             + f"  ·  **Open:** {open_n}"]
    if sw + sl:
        lines.append(f"- **Shadow (validating, not bet — {', '.join(_sl.get(x, x) for x in shadow)}):**"
                     f"  {sw}-{sl}  ·  {spnl:+.2f}u  ·  hit {sw/(sw+sl)*100:.0f}%")
    lines.append("")
    rows = con.execute(
        "SELECT league, COUNT(*), SUM(result='W'), SUM(result='L'), SUM(COALESCE(pnl,0)) "
        "FROM paper_bets WHERE result IN ('W','L') GROUP BY league ORDER BY league").fetchall()
    if rows:
        lines += ["| league | settled | W-L | hit | P&L (u) |", "|---|---|---|---|---|"]
        for lg, cnt, lw, ll, lpnl in rows:
            hit = f"{lw/(lw+ll)*100:.0f}%" if (lw + ll) else "—"
            lines.append(f"| {lg} | {cnt} | {lw}-{ll} | {hit} | {lpnl:+.2f} |")
        lines.append("")
    recent = con.execute(
        "SELECT graded_at, league, p1, p2, side, line, total, result, pnl FROM paper_bets "
        "WHERE result IN ('W','L') ORDER BY graded_at DESC, start_ts DESC LIMIT 25").fetchall()
    if recent:
        lines += ["### recent settled", "",
                  "| graded | league | matchup | bet | total | result | P&L |",
                  "|---|---|---|---|---|---|---|"]
        for ga, lg, p1, p2, sd, ln, tot, res, pn in recent:
            lines.append(f"| {str(ga)[:10]} | {lg} | {p1} vs {p2} | {sd} {ln} | {tot} | "
                         f"{res} | {pn:+.2f} |")
        lines.append("")
    con.close()
    REPORT.write_text("\n".join(lines) + "\n")
    return aw, al, apnl, open_n                        # the BETTABLE record (Elite + Setka Cup)


def main():
    g = grade()
    w, l, pnl, open_n = report()
    print(f"paper ledger: {g} newly graded · record {w}-{l} · {pnl:+.2f}u · {open_n} open")


if __name__ == "__main__":
    main()
