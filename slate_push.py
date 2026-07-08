"""TT slate digest — the day's flags in ONE long push, ordered by match time.

Complements the per-flag alerts (which fire as flags appear, strongest first) with a
scannable schedule view across every league. Sent twice daily, at the two runs where
fixture coverage is fullest for a Mountain-Time bettor:

    15:00 UTC (~9am MT)  — the full European day is posted
    20:00 UTC (~2pm MT)  — the evening Setka/TTCup slate has loaded

The hour guard lives here (not in the workflow) so every scheduled run can call this
unconditionally; only the two window runs actually send. The 5-min-before reminders
are untouched. --force sends now regardless of the clock.

    python slate_push.py [--force]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os

import requests

from check_today import TAG, actionable, all_fixtures, kelly_units
from h2h import load

SEND_HOURS_UTC = {15, 16, 20, 21}      # each cron may slip into the next hour


def build():
    rows = load(with_league=True)
    bets = actionable(all_fixtures(), rows, 74.5)
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    bets = [b for b in bets if b["ts"] > now]          # only matches still to play
    bets.sort(key=lambda b: b["ts"])                   # schedule order, not confidence
    lines = []
    for b in bets:
        tag = TAG.get(b["league"], b["league"]) + ("·VOL" if b.get("tier") == "volume" else "")
        w = round(b["raw"] * b["n"])
        u = kelly_units(b["hit"])
        lines.append(f"{b['when']} · [{tag}] {b['p1']} v {b['p2']} · {b['zone']} · "
                     f"{w}-{b['n']-w} ({b['raw']*100:.0f}%) · {u:g}u")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="send now regardless of hour")
    args = ap.parse_args()
    hour = dt.datetime.now(dt.timezone.utc).hour
    if not args.force and hour not in SEND_HOURS_UTC:
        print(f"not a slate window (utc hour {hour}) - skipping send")
        return
    lines = build()
    print(f"slate: {len(lines)} upcoming flags")
    for ln in lines:
        print("  " + ln)
    topic = os.environ.get("NTFY_TOPIC")
    if not topic or not lines:
        return
    body = "\n".join(lines[:40])                       # ntfy body cap ~4KB
    try:
        r = requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                          headers={"Title": f"TT slate by time - {len(lines)} bets",
                                   "Tags": "calendar"}, timeout=15)
        print("pushed:", r.status_code)
    except requests.RequestException as e:
        print("push failed:", e)


if __name__ == "__main__":
    main()
