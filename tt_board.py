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
from h2h import DB, kelly_units, load

OUT = Path(__file__).resolve().parent / "tt_board.json"
EPOCH = "2026-07-09"                       # fresh-start record epoch (matches tt_digest)
TT_LEAGUES = {"TT Elite Series", "Setka Cup", "Czech Liga Pro", "TT Cup", "Setka Women"}


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
        out.append({
            "league": b["league"], "tag": CT.TAG.get(b["league"], b["league"]),
            "p1": b["p1"], "p2": b["p2"], "side": b["zone"],
            "conf": round(b["hit"] * 100), "raw": round(b["raw"] * 100),
            "rec": f"{w}-{b['n'] - w}", "n": b["n"], "avg": round(b["avg"], 1),
            "ts": b["ts"], "u": round(kelly_units(b["hit"]), 1),
            "tier": b.get("tier") or "",
        })
    trk = tracker()
    OUT.write_text(json.dumps({"updated": dt.datetime.now(dt.timezone.utc).isoformat(),
                               "bets": out, "tracker": trk}))
    print(f"tt_board: {len(out)} actionable bets, tracker {trk['w']}-{trk['l']} "
          f"({trk['u']:+.1f}u) -> {OUT}")


if __name__ == "__main__":
    build()
