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
import json
import sqlite3
from collections import defaultdict
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


# A usable real line = one within the range the user's books actually post (they cap ~78.5).
# Kambi's Czech Liga Pro market runs a higher-scoring format and posts totals in the 90s that
# sit above those pairs' historical max — not a line the user can bet — so drop out-of-range.
REAL_LINE_LO, REAL_LINE_HI = 65.0, 82.0


def _slate_date(start_ts):
    if not start_ts:
        return None
    return dt.datetime.utcfromtimestamp(start_ts).strftime("%Y-%m-%d")


FD_JSONL = HERE / "fanduel_odds.jsonl"
ODDS_DDL = """CREATE TABLE IF NOT EXISTS odds(
    collected_at TEXT, source TEXT, event_id TEXT, date TEXT, league TEXT,
    p1 TEXT, p2 TEXT, pair_key TEXT, line REAL, over_od REAL, under_od REAL)"""


def _ingest_fanduel_jsonl(con):
    """Upsert FanDuel lines (fanduel_odds.jsonl, committed from the user's Alberta machine — CI
    can't reach geo-locked FanDuel Canada) into the odds table, so grade_real can prefer them."""
    if not FD_JSONL.exists():
        return 0
    con.execute(ODDS_DDL)
    n = 0
    for ln in FD_JSONL.read_text().splitlines():
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("source") != "fanduel" or not o.get("event_id"):
            continue
        con.execute("DELETE FROM odds WHERE source='fanduel' AND event_id=?", (str(o["event_id"]),))
        con.execute("INSERT INTO odds(collected_at,source,event_id,date,league,p1,p2,pair_key,"
                    "line,over_od,under_od) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (o.get("collected_at"), "fanduel", str(o["event_id"]), o.get("date"),
                     o.get("league"), o.get("p1"), o.get("p2"), o.get("pair_key"),
                     o.get("line"), o.get("over_od"), o.get("under_od")))
        n += 1
    con.commit()
    return n


def grade_real():
    """Attach the REAL posted book line + over/under odds (Kambi, `odds` table) to each graded
    bet and compute the real-line result + P&L — the honest test vs the price actually offered,
    not the flat 74.5/-110 proxy. Matched on canonical pair key + slate date (±1 day for the
    UTC boundary); only in-range lines (what the user can actually bet) are used."""
    import kambi_odds as K
    con = sqlite3.connect(DB)
    con.execute(DDL)
    con.execute(ODDS_DDL)
    _ingest_fanduel_jsonl(con)                     # pull in locally-collected FanDuel lines first
    have = {r[1] for r in con.execute("PRAGMA table_info(paper_bets)")}
    for col, typ in (("real_line", "REAL"), ("real_od", "REAL"), ("real_result", "TEXT"),
                     ("real_pnl", "REAL"), ("real_src", "TEXT")):
        if col not in have:
            con.execute(f"ALTER TABLE paper_bets ADD COLUMN {col} {typ}")
    odds = defaultdict(list)                       # pair_key -> [(source, date, line, over, under)]
    for src, pk, d, ln, oo, uo in con.execute(
            "SELECT source, pair_key, date, line, over_od, under_od FROM odds").fetchall():
        odds[pk].append((src, d, ln, oo, uo))
    graded = 0
    rows = con.execute("SELECT mid, side, p1, p2, total, start_ts FROM paper_bets "
                       "WHERE result IS NOT NULL AND total IS NOT NULL "
                       "AND real_result IS NULL").fetchall()
    for mid, side, p1, p2, total, start_ts in rows:
        cands = odds.get(K.npair(p1, p2))
        if not cands:
            continue
        bd = _slate_date(start_ts)

        def _daydiff(c):
            return abs((dt.date.fromisoformat(c[1]) - dt.date.fromisoformat(bd)).days) \
                if (c[1] and bd) else 99
        # prefer the user's real book (FanDuel) over the Kambi proxy, then the closest-dated line
        best = min(cands, key=lambda c: (0 if c[0] == "fanduel" else 1, _daydiff(c)))
        src, d, ln, oo, uo = best
        if bd and d and _daydiff(best) > 1:
            continue                              # no line near this match's date
        if not (REAL_LINE_LO <= ln <= REAL_LINE_HI):
            continue                              # out of the user's bettable range
        od = oo if side == "over" else uo
        if not od:
            continue
        won = (total > ln) == (side == "over")
        con.execute("UPDATE paper_bets SET real_line=?, real_od=?, real_result=?, real_pnl=?, "
                    "real_src=? WHERE mid=?",
                    (ln, od, "W" if won else "L", round((od - 1.0) if won else -1.0, 3), src, mid))
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
    # real-line record: the same flags graded at the ACTUAL Kambi book total + odds (Elite +
    # in-range Liga Pro) — the honest edge-vs-price, accumulating forward as lined matches settle
    have_real = "real_result" in {r[1] for r in con.execute("PRAGMA table_info(paper_bets)")}
    rr = con.execute("SELECT real_result, real_pnl, COALESCE(real_src,'kambi') FROM paper_bets "
                     "WHERE real_result IS NOT NULL").fetchall() if have_real else []
    if rr:
        rw = sum(1 for r in rr if r[0] == "W")
        rpnl = sum(r[1] or 0 for r in rr)
        fd = sum(1 for r in rr if r[2] == "fanduel")
        src = f"{fd} FanDuel, {len(rr) - fd} Kambi" if fd else "Kambi"
        lines += [f"- **Real-line record ({src}):** {rw}-{len(rr) - rw}"
                  f"  ·  {rpnl:+.2f}u on {len(rr)} bets priced at the ACTUAL posted total + odds"
                  f"  ·  {'beats' if rpnl > 0 else 'below'} the price"
                  f"  (vs the flat 74.5 proxy above)", ""]
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
    gr = grade_real()
    w, l, pnl, open_n = report()
    print(f"paper ledger: {g} newly graded · record {w}-{l} · {pnl:+.2f}u · {open_n} open"
          f" · {gr} priced at real Kambi lines")


if __name__ == "__main__":
    main()
