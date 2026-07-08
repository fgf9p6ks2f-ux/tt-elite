"""Ingest ESportsBattle esoccer/ebasketball results into tt.sqlite — FREE, no token.

Same table and idempotency model as the TT leagues (INSERT OR IGNORE on esb_* ids;
ESB is the only source for these leagues so no cross-source dedupe is needed).

    python ingest_esb.py --days 2                 # daily top-up (both sports)
    python ingest_esb.py --sport esoccer --days 60   # backfill
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from pathlib import Path

import source_esb as esb

DB = Path(__file__).resolve().parent / "tt.sqlite"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", choices=list(esb.SPORTS) + ["all"], default="all")
    ap.add_argument("--days", type=int, default=2, help="days back from today (UTC)")
    args = ap.parse_args()
    sports = list(esb.SPORTS) if args.sport == "all" else [args.sport]

    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY, league TEXT, date TEXT, p1 TEXT, p2 TEXT,
        total_points INTEGER, sets TEXT, scores TEXT)""")
    today = dt.datetime.now(dt.timezone.utc).date()
    for sport in sports:
        league = esb.SPORTS[sport][1]
        added = 0
        for back in range(args.days):
            day = (today - dt.timedelta(days=back)).isoformat()
            try:
                rows = esb.day_results(sport, day)
            except RuntimeError as e:
                print(f"{league} {day}: FETCH FAILED ({e})")
                continue
            before = con.total_changes
            con.executemany("INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?,?,?)", rows)
            con.commit()
            added += con.total_changes - before
            if back and back % 10 == 0:
                print(f"  ...{league} {back}/{args.days} days, +{added}", flush=True)
        tot = con.execute("SELECT COUNT(*) FROM matches WHERE league=?", (league,)).fetchone()[0]
        print(f"{league}: +{added} new · total {tot:,}")
    con.close()


if __name__ == "__main__":
    main()
