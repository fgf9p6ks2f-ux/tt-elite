"""TT paper ledger — the live, audited track record of the flags.

Every actionable flag check_today produces is logged here as a 1-unit ($100) paper bet
at -110, keyed by the exact 24live match id — so grading is against precisely the match
that was flagged (same-day rematches between a pair are common and would otherwise
cross-grade). The daily workflow grades bets as results land in tt.sqlite and rewrites
paper.md. This is the honest live counterpart to the historical validation: if the edge
decays, it shows up here first.

    python paper_ledger.py            # grade whatever has results + rewrite paper.md
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from h2h import DB

HERE = Path(__file__).resolve().parent
REPORT = HERE / "paper.md"
UNIT_USD = 100.0
WIN_UNITS = 100.0 / 110.0            # flat 1u at -110


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
            "INSERT OR IGNORE INTO paper_bets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
    w, l, pnl = _agg(con)
    n = w + l
    open_n = con.execute("SELECT COUNT(*) FROM paper_bets WHERE result IS NULL").fetchone()[0]
    lines = ["# TT paper ledger — live flag track record", "",
             f"_{_now()} UTC · every flag logged as 1u ($100) at -110 · this is the live "
             f"out-of-sample test of the league rules_", "",
             f"- **Record:** {w}-{l}"
             f"  ·  **P&L:** {pnl:+.2f}u (${pnl*UNIT_USD:+,.0f})"
             + (f"  ·  **hit {w/n*100:.1f}%** (break-even 52.4%)" if n else "")
             + f"  ·  **Open:** {open_n}", ""]
    rows = con.execute(
        "SELECT league, COUNT(*), SUM(result='W'), SUM(result='L'), SUM(COALESCE(pnl,0)) "
        "FROM paper_bets WHERE result IS NOT NULL GROUP BY league ORDER BY league").fetchall()
    if rows:
        lines += ["| league | settled | W-L | hit | P&L (u) |", "|---|---|---|---|---|"]
        for lg, cnt, lw, ll, lpnl in rows:
            hit = f"{lw/(lw+ll)*100:.0f}%" if (lw + ll) else "—"
            lines.append(f"| {lg} | {cnt} | {lw}-{ll} | {hit} | {lpnl:+.2f} |")
        lines.append("")
    recent = con.execute(
        "SELECT graded_at, league, p1, p2, side, line, total, result, pnl FROM paper_bets "
        "WHERE result IS NOT NULL ORDER BY graded_at DESC, start_ts DESC LIMIT 25").fetchall()
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
    return w, l, pnl, open_n


def main():
    g = grade()
    w, l, pnl, open_n = report()
    print(f"paper ledger: {g} newly graded · record {w}-{l} · {pnl:+.2f}u · {open_n} open")


if __name__ == "__main__":
    main()
