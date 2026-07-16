"""Emit today's actionable TT bets as JSON for the phone dashboard's Table Tennis tab.

Reuses check_today's fixture + flag logic (read-only), writes tt_board.json. A separate
step (push_tt_board.py) ships it to the public dashboard repo. Grouped/sorted on the
dashboard side by league then game time.

    python tt_board.py
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import sqlite3

import check_today as CT
import fd_tt                                # norm() for the FanDuel<->24live name join
from h2h import (DB, DEFAULT_CFG, LEAGUE_CFG, h2h_records, kelly_units, load,
                 pair_key)
from kambi_odds import npair               # canonical pair key to join the bmbets odds

HERE = Path(__file__).resolve().parent
OUT = HERE / "tt_board.json"
FD_BOARD = HERE / "fd_board.json"          # FanDuel.ca board (fetched by daily.yml before this runs)
BMDB = Path(__file__).resolve().parent / "bmbets.sqlite"   # per-book soft totals (bmbets scraper)
EPOCH = "2026-07-09"                       # fresh-start record epoch (matches tt_digest)
TT_LEAGUES = {"TT Elite Series", "Setka Cup", "Czech Liga Pro", "TT Cup", "Setka Women"}
MODEL_LINE = 74.5                          # the line the flag rules are tuned at
LADDER = [70.5 + i for i in range(11)]     # 70.5 .. 80.5 — one row per posted .5 line


def play_to(lad, over_side, league):
    """Furthest line the flagged side is still worth playing: the last ladder line whose raw
    H2H hit rate clears the league's validated bar (the SAME bar the flag rule uses). Over%
    falls / under% rises with the line, so the playable lines are contiguous — max line for an
    over, min for an under. The 74.5 flag line always qualifies (mirrors line_zone). This is
    the conservative, profitable cutoff — deliberately NOT stretched to the pair's high-scoring
    tail, so the ◄ stays at the safe, tuned line."""
    cfg = LEAGUE_CFG.get(league, DEFAULT_CFG)
    bar = (cfg.get("thr") or cfg.get("pct") or 0.70) * 100
    ok = [MODEL_LINE]
    for r in lad:
        rate = r["op"] if over_side else 100 - r["op"]
        if rate >= bar:
            ok.append(r["line"])
    return max(ok) if over_side else min(ok)


def ladder(totals):
    """Raw H2H over/under hit rate at every line a book might post (fixed 70.5–80.5), so the
    user can read the hit rate at THEIR exact line. Totals are integers and lines are .5, so
    there's never a push: unders = n - overs exactly. Over% is monotone decreasing down."""
    n = len(totals)
    if not n:
        return []
    out = []
    for L in LADDER:
        o = sum(1 for t in totals if t > L)
        out.append({"line": round(L, 1), "o": o, "u": n - o, "op": round(o / n * 100)})
    return out


def tracker():
    """Live record/units for the Tracker tab, from graded paper_bets since the epoch (flat
    1u). Settles as each match ends (paper_ledger grades in the TT loop every ~9 min)."""
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT league, result, pnl FROM paper_bets "
                       "WHERE result IS NOT NULL AND graded_at >= ?", (EPOCH,)).fetchall()
    con.close()
    dec = [r for r in rows if r[0] in TT_LEAGUES and r[1] in ("W", "L")]
    w = sum(1 for r in dec if r[1] == "W")
    u = sum(r[2] or 0 for r in dec)
    return {"w": w, "l": len(dec) - w, "u": round(u, 1)}


def bmbets_odds():
    """npair -> {line: (best_over, best_under, n_books)} from the latest bmbets snapshot, so
    the board can show the ACTUAL soft price at each line next to the H2H hit rate."""
    from collections import defaultdict as dd
    idx = dd(dict)
    if not BMDB.exists():
        return idx
    try:
        con = sqlite3.connect(f"file:{BMDB}?mode=ro", uri=True)
        ts = con.execute("SELECT MAX(collected_at) FROM bmbets_odds").fetchone()[0]
        if ts:
            for pk, ln, bo, bu, nb in con.execute(
                    "SELECT pair_key,line,best_over,best_under,n_books FROM bmbets_odds "
                    "WHERE collected_at=?", (ts,)):
                idx[pk][round(ln, 1)] = (bo, bu, nb)
        con.close()
    except Exception:
        pass
    return idx


def elite_h2h():
    """For every Elite match on FanDuel's board, the pair's RAW H2H total-points list from
    tt.sqlite (normalized-name keyed). The dashboard renders the record + hit rate AT the live
    FanDuel line by counting these totals over/under whatever line FanDuel currently posts — so
    the record always matches the displayed (moving) line. Empty if the board isn't present."""
    if not FD_BOARD.exists():
        return []
    try:
        matches = json.loads(FD_BOARD.read_text()).get("matches", [])
    except (ValueError, OSError):
        return []
    rec = h2h_records(load(league="TT Elite", with_league=True), 74.5)   # totals are line-independent
    tot_by_norm = {}                                     # frozenset(norm p1, norm p2) -> [totals]
    for (a, b), meets in rec.items():
        tot_by_norm[frozenset((fd_tt.norm(a), fd_tt.norm(b)))] = [t for _, t, _ in meets]
    out, seen = [], set()
    for m in matches:
        p1n, p2n = m.get("p1_norm"), m.get("p2_norm")
        if not (p1n and p2n):
            continue
        key = frozenset((p1n, p2n))
        if key in seen:
            continue
        seen.add(key)
        totals = tot_by_norm.get(key)
        if totals:
            out.append({"p1n": p1n, "p2n": p2n, "totals": totals})
    return out


def build():
    rows = load(with_league=True)
    bets = CT.actionable(CT.all_fixtures(), rows, 74.5)
    bm = bmbets_odds()
    out = []
    for b in bets:
        w = round(b["raw"] * b["n"])
        over_side = b["side"] == "over"
        lad = ladder(b.get("totals", []))
        pt = play_to(lad, over_side, b["league"])
        # overlay the ACTUAL bmbets soft price for the flagged side onto each ladder line,
        # and surface the book's MAIN line (where the most books cluster) as the "actual line".
        bmlines = bm.get(npair(b["p1"], b["p2"]), {})
        for r in lad:
            q = bmlines.get(r["line"])
            if q and (q[0] if over_side else q[1]):
                r["od"] = round(q[0] if over_side else q[1], 2)
                r["nb"] = q[2]
        book = None
        if bmlines:
            ml = max(bmlines, key=lambda L: bmlines[L][2] or 0)      # main line = most books
            price = bmlines[ml][0] if over_side else bmlines[ml][1]
            if price:
                book = {"line": ml, "od": round(price, 2), "nb": bmlines[ml][2]}
        out.append({
            "league": b["league"], "tag": CT.TAG.get(b["league"], b["league"]),
            "p1": b["p1"], "p2": b["p2"],
            "side": f"O≤{pt:g}" if over_side else f"U≥{pt:g}",   # card zone == dropdown cutoff
            "conf": round(b["hit"] * 100), "raw": round(b["raw"] * 100),
            "rec": f"{w}-{b['n'] - w}", "n": b["n"], "avg": round(b["avg"], 1),
            "ts": b["ts"], "u": round(kelly_units(b["hit"]), 1),
            "tier": b.get("tier") or "",
            "ladder": lad,
            "play_to": pt,
            "book": book,               # actual bmbets main line + soft price (None if no odds yet)
        })
    trk = tracker()
    OUT.write_text(json.dumps({"updated": dt.datetime.now(dt.timezone.utc).isoformat(),
                               "bets": out, "tracker": trk, "model_line": MODEL_LINE,
                               "elite_h2h": elite_h2h()}))
    print(f"tt_board: {len(out)} actionable bets, tracker {trk['w']}-{trk['l']} "
          f"({trk['u']:+.1f}u) -> {OUT}")


if __name__ == "__main__":
    build()
