"""Ingest ALL four TT leagues (with total points) from 24live.com — FREE, no token.

The token-free replacement for ingest_betsapi.py: TT Elite, Setka Cup, Czech Liga
Pro, TT Cup from one uniform source. Pulls each league's recent finished window
(default 500 ≈ several days) and upserts into tt.sqlite. Idempotent — INSERT OR
IGNORE on the 24l_<id> key — safe to run every few hours; a missed run is covered
by the next run's window.

24live uses different match ids than BetsAPI, so during the handoff (days BetsAPI
already collected) the SAME match would land twice and double-count in the H2H
records. We dedupe on the source-independent identity (date, unordered pair,
total_points) against what's already in the DB before inserting. Names resolve
against each league's existing roster (see source_24live.resolve); ambiguous
jr./sr. identities are skipped, never guessed.

    python ingest_24live.py                    # all leagues, 500-match window
    python ingest_24live.py --tids 22357       # one league
    python ingest_24live.py --limit 2000       # wider backfill
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
    ap.add_argument("--tids", default=",".join(str(t) for t in src.LEAGUES),
                    help="comma-separated 24live tournament ids")
    ap.add_argument("--limit", type=int, default=500, help="finished-match window per league")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY, league TEXT, date TEXT, p1 TEXT, p2 TEXT,
        total_points INTEGER, sets TEXT, scores TEXT)""")

    for tid in [int(t) for t in args.tids.split(",") if t.strip()]:
        league = src.LEAGUES.get(tid, str(tid))
        try:
            roster = src.league_roster(con, league)
            rows, skipped = src.results(tid, args.limit, roster)
        except RuntimeError as e:                 # one flaky league must not kill the rest
            print(f"{league}: FETCH FAILED ({e})")
            continue
        existing = {_identity(d, a, b, t) for d, a, b, t in con.execute(
            "SELECT date, p1, p2, total_points FROM matches "
            "WHERE league LIKE ? AND match_id NOT LIKE '24l_%'", (f"%{league}%",))}
        fresh = [r for r in rows if _identity(r[2], r[3], r[4], r[5]) not in existing]
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?,?,?)", fresh)
        con.commit()
        added = con.total_changes - before
        tot = con.execute("SELECT COUNT(*) FROM matches WHERE league LIKE ?",
                          (f"%{league}%",)).fetchone()[0]
        print(f"{league}: fetched {len(rows)}, +{added} new"
              + (f", {skipped} ambiguous-name skipped" if skipped else "")
              + f" · total {tot:,}")
    con.close()


if __name__ == "__main__":
    main()
