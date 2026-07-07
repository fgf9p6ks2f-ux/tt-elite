"""Ingest table-tennis match history (with total points) from BetsAPI into tt.sqlite.

Pulls EVERY high-frequency TT league (TT Elite, TT Cup, Setka Cup, Liga Pro, ...) in one
run — the H2H over/under strategy applies to all of them, and results history is permanent
(pull once, keep forever). Total points per match = sum of both players' points across all
games (BetsAPI `scores` field is per-game points).

Buy the "Everything API — One Day Trial" ($2, includes the Events API — the $1 bookmaker
trials do NOT include table tennis). One day is enough to pull the full 2016+ history.

    BETSAPI_TOKEN=xxx python ingest_betsapi.py --days 3      # TEST first (confirm scores)
    BETSAPI_TOKEN=xxx python ingest_betsapi.py --days 4000   # full history
    BETSAPI_TOKEN=xxx python ingest_betsapi.py --leagues "TT Elite,TT Cup,Setka,Liga Pro"
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path

from betsapi_client import get as _get, mode

HERE = Path(__file__).resolve().parent
DB = HERE / "tt.sqlite"
SPORT_TT = 92
# default: the recurring-pair, 24/7 betting leagues where the H2H strategy works
DEFAULT_LEAGUES = "TT Elite,TT Cup,Setka,Liga Pro,Challenger Series,Win Cup"


def discover_leagues(name_filters):
    """Find TT league ids whose name matches any of the given substrings."""
    wanted = [f.strip().lower() for f in name_filters]
    found, page = {}, 1
    while True:
        j = _get("/v1/league", sport_id=SPORT_TT, page=page)
        results = j.get("results") or []
        for lg in results:
            nm = (lg.get("name") or "")
            if any(w in nm.lower() for w in wanted):
                found[str(lg["id"])] = nm
        pager = j.get("pager") or {}
        if not results or page * int(pager.get("per_page", 50) or 50) >= int(pager.get("total", 0) or 0):
            break
        page += 1
        time.sleep(0.2)
    return found


def total_points(ev):
    scores = ev.get("scores") or {}
    p1, p2 = [], []
    for period in sorted(scores, key=lambda k: int(k) if str(k).isdigit() else 0):
        s = scores[period]
        try:
            p1.append(int(s.get("home"))); p2.append(int(s.get("away")))
        except (TypeError, ValueError):
            continue
    if not p1:
        return None
    return sum(p1) + sum(p2), p1, p2, ev.get("ss", "")


def fetch_day(day, league_id):
    out, page = [], 1
    while True:
        j = _get("/v3/events/ended", sport_id=SPORT_TT, league_id=league_id, day=day, page=page)
        results = j.get("results") or []
        out += results
        pager = j.get("pager") or {}
        if not results or page * int(pager.get("per_page", 50) or 50) >= int(pager.get("total", 0) or 0):
            break
        page += 1
        time.sleep(0.2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4000)
    ap.add_argument("--leagues", default=DEFAULT_LEAGUES,
                    help="comma-separated name substrings to include (uses discovery)")
    ap.add_argument("--ids", help="comma-separated league ids (skips discovery; exact)")
    args = ap.parse_args()
    if not mode():
        sys.exit("set BETSAPI_TOKEN (direct) or BETSAPI_RAPIDAPI_KEY (rapidapi)")

    if args.ids:
        leagues = {i.strip(): i.strip() for i in args.ids.split(",")}   # name from events
    else:
        leagues = discover_leagues(args.leagues.split(","))
    if not leagues:
        sys.exit("no matching TT leagues found — check --leagues/--ids or the token.")
    print("pulling leagues:", ", ".join(leagues))

    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY, league TEXT, date TEXT, p1 TEXT, p2 TEXT,
        total_points INTEGER, sets TEXT, scores TEXT)""")
    today = dt.date.today()
    added = 0
    for back in range(args.days):
        day = (today - dt.timedelta(days=back)).strftime("%Y%m%d")
        for lid, lname in leagues.items():
            rows = []
            for ev in fetch_day(day, lid):
                tp = total_points(ev)
                if not tp:
                    continue
                total, p1p, p2p, ss = tp
                d = dt.datetime.utcfromtimestamp(int(ev.get("time", 0))).date().isoformat() \
                    if ev.get("time") else day
                league = (ev.get("league") or {}).get("name") or lname
                rows.append((str(ev.get("id")), league, d,
                             (ev.get("home") or {}).get("name") or "?",
                             (ev.get("away") or {}).get("name") or "?",
                             total, ss, ",".join(f"{h}-{a}" for h, a in zip(p1p, p2p))))
            if rows:
                con.executemany("INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?,?,?)", rows)
                con.commit()
                added = con.total_changes          # cumulative inserts (not per-batch)
        if back % 100 == 0:
            print(f"  ...{back}/{args.days} days · {added} matches", flush=True)
    by = con.execute("SELECT league, COUNT(*) FROM matches GROUP BY league").fetchall()
    con.close()
    print("done:", dict(by))


if __name__ == "__main__":
    main()
