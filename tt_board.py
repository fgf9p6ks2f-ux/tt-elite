"""Emit today's actionable TT bets as JSON for the phone dashboard's Table Tennis tab.

Reuses check_today's fixture + flag logic (read-only), writes tt_board.json. A separate
step (push_tt_board.py) ships it to the public dashboard repo. Grouped/sorted on the
dashboard side by league then game time.

    python tt_board.py
"""
from __future__ import annotations

import datetime as dt
import json
import statistics as st
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
# TT Elite record RESET: the FanDuel-line 70%-hit-rate rule went live 2026-07-16 ~05:47 UTC.
# The Elite card counts only bets flagged from here (real-line, odds NOT NULL) — the earlier
# +EV-engine bets stay in the ledger as history but are excluded from the live Elite record.
ELITE_EPOCH = "2026-07-16T05:45:00"
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
    """Live record/units for the Tracker tab — TT Elite Series ONLY, graded at the REAL FanDuel line
    + odds each bet carries (odds NOT NULL), since ELITE_EPOCH. FanDuel prices ONLY TT Elite, so per
    the user's FanDuel-lines-only rule (2026-07-20) the shadow leagues (Setka/Liga Pro/TT Cup/Setka
    Women, graded at the discredited fixed-74.5 -120 proxy) are excluded from the record entirely —
    they still log to the ledger for reference, but never surface here or in the headline."""
    con = sqlite3.connect(DB)
    # the 80-90-UNDER LEAK (loss profile 2026-07-21): SHADOWED — still graded, but excluded from the
    # headline/pending/recent (we don't bet it). Tracked separately in `filtered` to validate forward.
    FILT = "AND NOT (side='under' AND line >= 80 AND line < 90)"
    rows = con.execute(
        "SELECT result, pnl FROM paper_bets WHERE result IN ('W','L') "
        f"AND league='TT Elite Series' AND odds IS NOT NULL {FILT} AND flagged_at >= ?",
        (ELITE_EPOCH,)).fetchall()
    shadow = con.execute(
        "SELECT result, pnl FROM paper_bets WHERE result IN ('W','L') AND league='TT Elite Series' "
        "AND odds IS NOT NULL AND side='under' AND line >= 80 AND line < 90 AND flagged_at >= ?",
        (ELITE_EPOCH,)).fetchall()
    ep = con.execute(
        "SELECT COUNT(*) FROM paper_bets WHERE league='TT Elite Series' "
        f"AND odds IS NOT NULL AND result IS NULL {FILT} AND flagged_at >= ?", (ELITE_EPOCH,)).fetchone()[0]
    # last-48h graded Elite bets for the dashboard's 24h dropdown, grouped by settle day
    rb = con.execute(
        "SELECT p1, p2, side, line, result, pnl, graded_at FROM paper_bets "
        f"WHERE league='TT Elite Series' AND odds IS NOT NULL AND result IN ('W','L') {FILT} "
        "AND graded_at >= datetime('now','-2 day') ORDER BY graded_at DESC").fetchall()
    con.close()
    from collections import OrderedDict
    rdays = OrderedDict()
    for p1, p2, side, line, res, pnl, ga in rb:
        rdays.setdefault((ga or "")[:10], []).append(
            {"name": "%s v %s %s%g" % (p1.split()[-1], p2.split()[-1],
                                       "o" if side == "over" else "u", line),
             "won": res == "W", "pnl": round(pnl or 0, 2)})
    recent = [{"date": d, "w": sum(1 for b in bs if b["won"]),
               "l": sum(1 for b in bs if not b["won"]),
               "u": round(sum(b["pnl"] for b in bs), 2), "bets": bs}
              for d, bs in rdays.items()]
    w = sum(1 for r in rows if r[0] == "W")
    l = sum(1 for r in rows if r[0] == "L")
    u = round(sum(r[1] or 0 for r in rows), 2)
    sw = sum(1 for r in shadow if r[0] == "W")
    sl = sum(1 for r in shadow if r[0] == "L")
    su = round(sum(r[1] or 0 for r in shadow), 2)
    return {"w": w, "l": l, "u": u,
            "leagues": [{"league": "TT Elite Series", "w": w, "l": l, "u": u}],
            "elite_pending": ep, "recent": recent,
            "filtered": {"w": sw, "l": sl, "u": su, "note": "80-90 unders (shadow, not bet)"}}


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


def elite_h2h(bets):
    """For every Elite match on FanDuel's board: the pair's RAW H2H total-points list (so the
    dashboard renders the record + hit rate AT the live FanDuel line), plus the model's +EV PICK
    when there is one. The pick comes straight from the real-line engine's flagged bets — which
    only fire when there's an edge OVER or UNDER at the ACTUAL FanDuel line — so the dashboard
    only flags a side on games you'd genuinely bet. No board -> empty."""
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
    picks = {}                                          # frozenset(norm) -> {side, hit, line}
    for b in bets:                                      # Elite real-line bets carry FanDuel odds
        if b.get("league") == "TT Elite Series" and b.get("odds") is not None:
            picks[frozenset((fd_tt.norm(b["p1"]), fd_tt.norm(b["p2"])))] = {
                "side": b["side"], "hit": round((b.get("raw") or 0) * 100), "line": b.get("line")}
    out, seen = [], set()
    for m in matches:
        p1n, p2n = m.get("p1_norm"), m.get("p2_norm")
        if not (p1n and p2n):
            continue
        key = frozenset((p1n, p2n))
        if key in seen:
            continue
        seen.add(key)
        entry = {"p1n": p1n, "p2n": p2n, "totals": tot_by_norm.get(key) or []}
        if key in picks:
            entry["pick"] = picks[key]
        if entry["totals"] or "pick" in entry:
            out.append(entry)
    return out


PROJ_WINDOW_H = 24    # project the FULL day's Elite slate ahead (24live lists ~28h out) so the VM
                      # dashboard can render it fresh each cycle without recomputing (the VM can't
                      # reach 24live — Cloudflare-blocks its IP — so projections are computed here in
                      # Actions once, then the VM re-filters/dedups the whole-day list at render speed).


def _player_total_avg(con, player, n=40):
    """Mean total points across the player's most recent matches — the basis for a projected line
    before FanDuel prices the match."""
    v = [r[0] for r in con.execute(
        "SELECT total_points FROM matches WHERE (p1=? OR p2=?) AND total_points IS NOT NULL "
        "ORDER BY date DESC LIMIT ?", (player, player, n))]
    return st.mean(v) if v else None


def elite_upcoming(fixtures, board_norms):
    """The upcoming TT Elite slate from 24live — FURTHER ahead than FanDuel posts (fixtures list ~28h
    out; FanDuel prices ~9 near-term). Each carries a PROJECTED line (blend of the two players' recent
    match-total averages) + the pair's H2H lean at that line, so the board shows the day's games as
    soon as the matchup is known. Pairs already on the FanDuel board are omitted (the live card shows
    those with the real line). PROJECTED-only — informational, never bet or graded."""
    con = sqlite3.connect(DB)
    # matches(p1)/(p2) index — without it the per-fixture total lookups full-scan 246k rows (minutes).
    con.execute("CREATE INDEX IF NOT EXISTS idx_matches_p1 ON matches(p1)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_matches_p2 ON matches(p2)")
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    # last-name fallback: a pair counts as "on the FanDuel board" even if 24live/FanDuel spell the
    # first names differently — so a projection is never generated for an already-priced match.
    _lt = lambda x: x.split()[-1] if x and x.split() else ""
    board_last = {frozenset(_lt(n) for n in fs) for fs in board_norms}
    out = []
    for p1, p2, ts, lg, _mid in fixtures:
        if lg != "TT Elite Series" or not ts or ts <= now or ts > now + PROJ_WINDOW_H * 3600:
            continue
        n1, n2 = fd_tt.norm(p1), fd_tt.norm(p2)
        if (frozenset((n1, n2)) in board_norms
                or frozenset((_lt(n1), _lt(n2))) in board_last):
            continue                                        # FanDuel already prices it -> live card
        a = _player_total_avg(con, p1)
        b = _player_total_avg(con, p2)
        if not (a and b):
            continue
        proj = round(((a + b) / 2) * 2) / 2                 # nearest 0.5
        if proj == int(proj):
            proj -= 0.5                                     # keep it a half-point line (no push)
        tots = [r[0] for r in con.execute(
            "SELECT total_points FROM matches WHERE ((p1=? AND p2=?) OR (p1=? AND p2=?)) "
            "AND total_points IS NOT NULL", (p1, p2, p2, p1))]
        n = len(tots)
        over = sum(1 for t in tots if t > proj)
        # LIKELY FLAG ONLY (2026-07-20, user): keep a projected game only if the pair hits a side
        # >=70% at the projected line with enough H2H — the SAME bar the live Elite flag uses
        # (ELITE_HIT_THR=0.70, min 12). Toss-ups / thin history are dropped: the card shows only
        # the bets we'd actually flag once FanDuel prices them, not the whole slate.
        side, hit = None, None
        if n >= 12:
            rate = over / n
            if rate >= 0.70:
                side, hit = "over", rate
            elif rate <= 0.30:
                side, hit = "under", 1 - rate
        if side is None:
            continue
        out.append({"p1": p1, "p2": p2, "p1n": fd_tt.norm(p1), "p2n": fd_tt.norm(p2),
                    "ts": int(ts), "proj": proj, "n": n, "over": over,
                    "side": side, "hit": round(hit * 100)})
    con.close()
    out.sort(key=lambda e: e["ts"])
    return out[:60]


def _board_norms():
    try:
        return {frozenset((m["p1_norm"], m["p2_norm"]))
                for m in json.loads(FD_BOARD.read_text()).get("matches", [])
                if m.get("p1_norm") and m.get("p2_norm")}
    except (ValueError, OSError):
        return set()


def build():
    rows = load(with_league=True)
    fixtures = CT.all_fixtures()
    bets = CT.actionable(fixtures, rows, 74.5)
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
    upcoming = elite_upcoming(fixtures, _board_norms())
    OUT.write_text(json.dumps({"updated": dt.datetime.now(dt.timezone.utc).isoformat(),
                               "bets": out, "tracker": trk, "model_line": MODEL_LINE,
                               "elite_h2h": elite_h2h(bets), "elite_upcoming": upcoming}))
    print(f"tt_board: {len(out)} actionable bets, {len(upcoming)} upcoming projected, "
          f"tracker {trk['w']}-{trk['l']} ({trk['u']:+.1f}u) -> {OUT}")


if __name__ == "__main__":
    build()
