"""Nightly TT paper-ledger digest — today + all-time record/units per league.

The table-tennis counterpart to the +EV ledger's daily_digest. Reads paper_bets
(every flag logged as 1u at -110, graded from tt.sqlite), pushes a per-league
scoreboard at ~11:45pm MT. Separate from the +EV digest by design — different repo,
different bankroll story, paper (line-assumed 74.5) not real-price.

    python tt_digest.py [--force]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3

import requests

from h2h import DB

try:
    from zoneinfo import ZoneInfo
    MT = ZoneInfo("America/Denver")
except Exception:
    MT = dt.timezone(dt.timedelta(hours=-6))

TAG = {"TT Elite Series": "Elite", "Setka Cup": "Setka", "Czech Liga Pro": "LigaPro",
       "TT Cup": "TTCup", "Setka Women": "SetkaW"}


def _u(u):
    return f"{u:+.2f}u (${u*100:+,.0f})"


def build(now_utc):
    mt = now_utc.astimezone(MT)
    lo = mt.replace(hour=0, minute=0, second=0, microsecond=0) \
        .astimezone(dt.timezone.utc).replace(tzinfo=None).isoformat()
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT league, result, pnl, graded_at FROM paper_bets "
                       "WHERE result IS NOT NULL").fetchall()
    con.close()
    rows = [r for r in rows if r[0] in TAG]      # table-tennis leagues only (ESB esoccer/
                                                 # ebasketball paper flags live in paper.md,
                                                 # not this TT scoreboard)

    def agg(rs):
        w = sum(1 for r in rs if r[0] == "W"); l = sum(1 for r in rs if r[0] == "L")
        return w, l, sum(r[1] or 0 for r in rs)

    day = [(r[1], r[2]) for r in rows if str(r[3]) >= lo]
    dw, dl, dp = agg(day)
    aw, al, ap = agg([(r[1], r[2]) for r in rows])
    lines = [f"TT paper ledger - {mt.date()} (MT)",
             f"TODAY: {dw}-{dl}  {_u(dp)}" if day else "TODAY: no flags settled",
             f"ALL-TIME: {aw}-{al}  {_u(ap)}", ""]
    for lg in sorted({r[0] for r in rows}):
        d = [(r[1], r[2]) for r in rows if r[0] == lg and str(r[3]) >= lo]
        a = [(r[1], r[2]) for r in rows if r[0] == lg]
        dw, dl, dp = agg(d); tw, tl, tp = agg(a)
        dpart = f"today {dw}-{dl} {dp:+.2f}u · " if d else "today - · "
        lines.append(f"{TAG.get(lg, lg)}: {dpart}all-time {tw}-{tl} {_u(tp)}")
    return "\n".join(lines), str(mt.date())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    ref = now - dt.timedelta(minutes=50)
    if not args.force and ref.astimezone(MT).hour not in (22, 23):
        print(f"not digest time (MT {now.astimezone(MT):%H:%M}) - exiting")
        return
    body, mt_date = build(ref if not args.force else now)
    print(body)
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                          headers={"Title": "TT paper results", "Tags": "ping_pong"},
                          timeout=15)
            print("pushed")
        except requests.RequestException as e:
            print("push failed:", e)


if __name__ == "__main__":
    main()
