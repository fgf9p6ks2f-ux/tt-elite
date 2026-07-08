"""Line-conditional flags for ESB esoccer / ebasketball.

Books post DYNAMIC per-match totals on these, so a blind fixed-line flag is useless.
Instead, for every upcoming fixture whose pair has a deep H2H history, this computes
the pair's historical hit rate AT EACH PLAUSIBLE LINE and flags when the center line
(the pair's trailing-10 median — the best proxy for what a book posts) has a side
hitting >= the report threshold. YOU check the book's posted line against the ladder
and bet only if it sits in a >=70% zone.

Deployable rule = exactly what validated (walk-forward, 2026-07-08):
    esoccer      >=70%: 74.2% (z=+54) · >=80%: 80.9% · >=85%: 84.2%
    ebasketball  >=70%: 68.5% (z=+16) · >=75%: 71.1% · >=80%: 73.9%

Outputs: esb_today.md (full >=report slate + ladders) · esb_alert.txt (NEW >=push
flags, deduped via esb_notified.txt — the phone tier) · paper-ledger rows at the
center line so the live track record accrues automatically.

    python check_esb.py
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict
from pathlib import Path

import source_esb as esb
from paper_ledger import log_flags
from validate_esb import half_line

HERE = Path(__file__).resolve().parent
DB = HERE / "tt.sqlite"

try:
    from zoneinfo import ZoneInfo
    MT = ZoneInfo("America/Denver")
except Exception:
    MT = dt.timezone(dt.timedelta(hours=-6))

CFG = {
    "Esoccer Battle":     {"sport": "esoccer", "tag": "Esoc", "min_n": 15,
                           "report": 0.70, "push": 0.80, "step": 1.0},
    "Ebasketball Battle": {"sport": "ebasketball", "tag": "Ebball", "min_n": 15,
                           "report": 0.70, "push": 0.75, "step": 2.0},
}


def mt_time(ts):
    return dt.datetime.fromtimestamp(int(ts), MT).strftime("%a %-I:%M%p MT") if ts else "?"


def histories(league):
    """{pair: [totals in chronological order]} for one ESB league."""
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT match_id, date, p1, p2, total_points FROM matches "
                       "WHERE league=? AND total_points IS NOT NULL", (league,)).fetchall()
    con.close()
    def seq(mid):
        try:
            return int(mid.rsplit("_", 1)[1])
        except ValueError:
            return 0
    rows.sort(key=lambda r: (r[1], seq(r[0])))
    h = defaultdict(list)
    for _, _, a, b, t in rows:
        h[tuple(sorted((a, b)))].append(t)
    return h


def zones(h, line0, step, thr=0.70):
    """Bet zones vs WHATEVER line the book posts: 'O≤X | U≥Y' — Over hits >=thr
    historically at any line up to X, Under hits >=thr at any line from Y up.
    (Over-rate falls and under-rate rises monotonically in the line, so the two
    thresholds fully describe the pair.) Grid = ±3 steps around the center line."""
    n = len(h)
    grid = [line0 + i * step for i in range(-3, 4)]
    over_max = None
    for L in grid:
        if sum(1 for t in h if t > L) / n >= thr:
            over_max = L
    under_min = None
    for L in grid:
        if sum(1 for t in h if t < L) / n >= thr:
            under_min = L
            break
    parts = []
    if over_max is not None:
        parts.append(f"O≤{over_max:g}")
    if under_min is not None:
        parts.append(f"U≥{under_min:g}")
    return " | ".join(parts) if parts else "no zone"


def flags():
    today = dt.datetime.now(dt.timezone.utc).date()
    out = []
    for league, cfg in CFG.items():
        hist = histories(league)
        fx = []
        for d in (today, today + dt.timedelta(days=1)):
            try:
                fx += esb.day_fixtures(cfg["sport"], d.isoformat())
            except RuntimeError as e:
                print(f"  ({league} fixtures skipped: {e})")
        for p1, p2, ts, _, mid in fx:
            h = hist.get(tuple(sorted((p1, p2))), [])
            if len(h) < cfg["min_n"]:
                continue
            k = h[-10:]
            line0 = half_line(sorted(k)[len(k) // 2])   # same formula the harness validated
            po = sum(1 for t in h if t > line0) / len(h)
            side, conf = ("over", po) if po >= 0.5 else ("under", 1 - po)
            if conf < cfg["report"]:
                continue
            out.append({"league": league, "tag": cfg["tag"], "p1": p1, "p2": p2,
                        "side": side, "line": line0, "hit": conf, "raw": conf,
                        "n": len(h), "ts": ts, "mid": mid,
                        "push": conf >= cfg["push"], "avg": sum(h) / len(h),
                        "zones": zones(h, line0, cfg["step"])})
    return sorted(out, key=lambda b: -b["hit"])


def write_outputs(bets, push_cap=35):
    # phone tier: new pushes only, deduped by match id. Capped per run (ntfy messages
    # top out ~4KB); overflow stays un-seen and drains on the next runs, strongest first.
    notif = HERE / "esb_notified.txt"
    seen = set(notif.read_text().splitlines()) if notif.exists() else set()
    new = []
    for b in bets:
        if not b["push"] or b["mid"] in seen or len(new) >= push_cap:
            continue
        seen.add(b["mid"])
        new.append(f"[{b['tag']}] {b['p1']} v {b['p2']} · {mt_time(b['ts'])} · "
                   f"{b['zones']} · avg {b['avg']:.1f} · {b['n']}g")
    (HERE / "esb_alert.txt").write_text("\n".join(new))
    notif.write_text("\n".join(sorted(seen)[-8000:]))

    # full report
    lines = ["# Esoccer / Ebasketball — bet-zone flags", "",
             f"_{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC · MARKET: match total "
             "goals O/U (Esoccer Battle 8 mins) / match total points O/U incl. OT "
             "(Ebasketball Battle 4x5 mins). Read the zone against the book's POSTED line: "
             "bet Over if it's ≤ the O number, Under if ≥ the U number, skip between._", "",
             "Validated walk-forward: esoccer 74-81% inside zones · ebasketball 69-74%. "
             "📱 = pushed to phone.", ""]
    if bets:
        lines += ["| when | league | matchup | zones | avg | games | |",
                  "|---|---|---|---|---|---|---|"]
        for b in bets:
            lines.append(f"| {mt_time(b['ts'])} | {b['tag']} | {b['p1']} vs {b['p2']} | "
                         f"{b['zones']} | {b['avg']:.1f} | {b['n']} | "
                         f"{'📱' if b['push'] else ''} |")
    else:
        lines.append("_no qualifying fixtures right now — slates roll all day, next run "
                     "will catch them._")
    (HERE / "esb_today.md").write_text("\n".join(lines) + "\n")
    return new


def main():
    bets = flags()
    new = write_outputs(bets)
    logged = log_flags(bets)                 # paper track record at the center line
    print(f"esb: {len(bets)} flags (≥report) · {len(new)} new phone alert(s) · "
          f"{logged} paper-logged")
    for b in bets[:12]:
        print(f"  {mt_time(b['ts']):<15} {b['tag']:<7} {b['p1']+' v '+b['p2']:<30} "
              f"{b['zones']:<16} avg {b['avg']:>5.1f}  {b['n']}g"
              f"{' 📱' if b['push'] else ''}")


if __name__ == "__main__":
    main()
