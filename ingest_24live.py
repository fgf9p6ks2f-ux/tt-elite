"""Ingest TT Elite results (with total points) from 24live.com — FREE, no token.

The token-free replacement for ingest_betsapi.py on the leagues 24live carries.
Pulls the recent finished window (default 500 ≈ several days of TT Elite) and
upserts into tt.sqlite. Idempotent — INSERT OR IGNORE on the 24l_<id> key — so it
is safe to run every few hours; a missed run is covered by the next run's window.

24live uses different match ids than BetsAPI, so during the handoff (days BetsAPI
already collected) the SAME match would land twice and double-count in the H2H
records. We dedupe on the source-independent identity (date, unordered pair,
total_points) against what's already in the DB before inserting.

    python ingest_24live.py               # TT Elite, 500-match window
    python ingest_24live.py --limit 2000  # wider backfill
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import source_24live as src
from h2h import pair_key

DB = Path(__file__).resolve().parent / "tt.sqlite"


def _identity(date, p1, p2, total):
    return (date, pair_key(p1, p2), total)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tid", type=int, default=22357, help="24live tournament id")
    ap.add_argument("--limit", type=int, default=500, help="finished-match window")
    ap.add_argument("--no-dedupe", action="store_true",
                    help="skip cross-source dedupe (only for a from-empty backfill)")
    args = ap.parse_args()

    league = src.LEAGUES.get(args.tid, "TT Elite Series")
    rows = src.results(args.tid, args.limit)

    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY, league TEXT, date TEXT, p1 TEXT, p2 TEXT,
        total_points INTEGER, sets TEXT, scores TEXT)""")

    if not args.no_dedupe:
        existing = {_identity(d, a, b, t) for d, a, b, t in con.execute(
            "SELECT date, p1, p2, total_points FROM matches "
            "WHERE league LIKE ? AND match_id NOT LIKE '24l_%'", (f"%{league}%",))}
        rows = [r for r in rows if _identity(r[2], r[3], r[4], r[5]) not in existing]

    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    added = con.total_changes - before
    tot = con.execute("SELECT COUNT(*) FROM matches WHERE league LIKE ?",
                      (f"%{league}%",)).fetchone()[0]
    con.close()
    print(f"24live: {len(rows)} after dedupe, +{added} new. {league} total now {tot:,}.")


if __name__ == "__main__":
    main()
