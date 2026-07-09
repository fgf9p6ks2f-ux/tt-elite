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
from pathlib import Path

import requests

from h2h import DB

try:
    from zoneinfo import ZoneInfo
    MT = ZoneInfo("America/Denver")
except Exception:
    MT = dt.timezone(dt.timedelta(hours=-6))

LOG = Path(__file__).resolve().parent / "tt_digests.md"

TAG = {"TT Elite Series": "Elite", "Setka Cup": "Setka", "Czech Liga Pro": "LigaPro",
       "TT Cup": "TTCup", "Setka Women": "SetkaW"}


def _u(u):
    return f"{u:+.2f}u (${u*100:+,.0f})"


def build(target_date):
    lo_dt = dt.datetime.combine(target_date, dt.time(0, 0), tzinfo=MT)
    hi_dt = lo_dt + dt.timedelta(days=1)
    lo = lo_dt.astimezone(dt.timezone.utc).replace(tzinfo=None).isoformat()
    hi = hi_dt.astimezone(dt.timezone.utc).replace(tzinfo=None).isoformat()
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

    in_day = lambda r: lo <= str(r[3]) < hi
    day = [(r[1], r[2]) for r in rows if in_day(r)]
    dw, dl, dp = agg(day)
    aw, al, ap = agg([(r[1], r[2]) for r in rows])
    lines = [f"TT paper ledger - {target_date} (MT)",
             f"TODAY: {dw}-{dl}  {_u(dp)}" if day else "TODAY: no flags settled",
             f"ALL-TIME: {aw}-{al}  {_u(ap)}", ""]
    for lg in sorted({r[0] for r in rows}):
        d = [(r[1], r[2]) for r in rows if r[0] == lg and in_day(r)]
        a = [(r[1], r[2]) for r in rows if r[0] == lg]
        dw, dl, dp = agg(d); tw, tl, tp = agg(a)
        dpart = f"today {dw}-{dl} {dp:+.2f}u · " if d else "today - · "
        lines.append(f"{TAG.get(lg, lg)}: {dpart}all-time {tw}-{tl} {_u(tp)}")
    return "\n".join(lines), target_date.isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    # Same delay-tolerant logic as the +EV digest: GitHub cron fires late/drops, so accept
    # a wide window and target the COMPLETED MT day; dedupe via tt_digests.md day header
    # so multiple crons (and the daytime daily.yml call) send exactly once.
    now_mt = dt.datetime.now(dt.timezone.utc).astimezone(MT)
    if args.force:
        target = now_mt.date()
    elif now_mt.hour >= 20:                        # 20:00-23:59 MT: today is ending
        target = now_mt.date()
    elif now_mt.hour < 14:                         # 00:00-13:59 MT: delayed -> yesterday
        target = (now_mt - dt.timedelta(days=1)).date()
    else:
        print(f"not digest time (MT {now_mt:%H:%M}) - exiting")
        return
    body, mt_date = build(target)
    if not args.force and LOG.exists() and f"## {mt_date}" in LOG.read_text():
        print(f"digest for {mt_date} already sent - exiting")
        return
    print(body)
    hdr = f"## {mt_date} (forced)" if args.force else f"## {mt_date}"
    with open(LOG, "a") as f:
        f.write(f"\n{hdr}\n\n```\n{body}\n```\n")
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
