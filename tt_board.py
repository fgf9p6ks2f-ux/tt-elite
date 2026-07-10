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
from h2h import DB, DEFAULT_CFG, LEAGUE_CFG, kelly_units, load

OUT = Path(__file__).resolve().parent / "tt_board.json"
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


def build():
    rows = load(with_league=True)
    bets = CT.actionable(CT.all_fixtures(), rows, 74.5)
    out = []
    for b in bets:
        w = round(b["raw"] * b["n"])
        over_side = b["side"] == "over"
        lad = ladder(b.get("totals", []))
        pt = play_to(lad, over_side, b["league"])
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
        })
    trk = tracker()
    OUT.write_text(json.dumps({"updated": dt.datetime.now(dt.timezone.utc).isoformat(),
                               "bets": out, "tracker": trk, "model_line": MODEL_LINE}))
    print(f"tt_board: {len(out)} actionable bets, tracker {trk['w']}-{trk['l']} "
          f"({trk['u']:+.1f}u) -> {OUT}")


if __name__ == "__main__":
    build()
