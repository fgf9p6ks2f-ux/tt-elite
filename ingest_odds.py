"""Pull historical total-points LINES + closing odds for TT matches (BetsAPI, same trial).

Why this matters: `validate.py` tests the H2H tendency against a FIXED 74.5. But the book
shades the line for known high/low-scoring pairs — if a "80% over 74.5" pair gets posted at
76.5, the edge is gone. This pulls the ACTUAL posted total line + closing odds per match so
we can validate against the number you'd really have bet (and measure how often the line
was near 74.5 at all). This is what turns a toy backtest into a real one.

Odds are per-event and the market layout for TT totals isn't documented, so run EXPLORE
first (dumps the raw structure for a few matches). Once we see the market key, the full pull
is a one-liner change away.

    BETSAPI_TOKEN=xxx python ingest_odds.py --explore 5     # confirm coverage + structure
    BETSAPI_TOKEN=xxx python ingest_odds.py --pull          # full pull (after we set KEY)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from betsapi_client import get as _get, mode

HERE = Path(__file__).resolve().parent
DB = HERE / "tt.sqlite"
# BetsAPI Bet365 over/under market key for table tennis — CONFIRM via --explore, then set.
TOTAL_MARKET_KEY = "92_3"


def event_ids(limit=None):
    con = sqlite3.connect(DB)
    q = "SELECT match_id, p1, p2, total_points FROM matches ORDER BY date DESC"
    if limit:
        q += f" LIMIT {limit}"
    rows = con.execute(q).fetchall()
    con.close()
    return rows


def explore(n):
    for mid, p1, p2, tot in event_ids(n):
        print(f"\n=== {p1} vs {p2} (actual total {tot}) event {mid} ===")
        summ = _get("/v1/event/odds/summary", event_id=mid)
        res = (summ.get("results") or {})
        if not res:
            print("  no odds summary (coverage gap for this event)")
        for book, data in res.items():
            markets = (data.get("odds") or {})
            # surface any over/under-looking markets and their keys
            tt = {k: v for k, v in markets.items()
                  if any(t in k.lower() for t in ("ou", "3", "total", "handicap"))}
            print(f"  book {book}: market keys = {list(markets.keys())[:12]}")
            for k, v in list(tt.items())[:3]:
                print(f"     {k}: {json.dumps(v)[:160]}")
        time.sleep(0.3)
    print("\n→ paste this output back; I'll set TOTAL_MARKET_KEY + the open/close fields, "
          "then `--pull` grabs every line.")


def pull():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS odds (
        match_id TEXT PRIMARY KEY, open_line REAL, close_line REAL,
        over_odds REAL, under_odds REAL)""")
    n = 0
    for (mid, *_ ) in event_ids():
        summ = _get("/v1/event/odds/summary", event_id=mid)
        book = next(iter((summ.get("results") or {}).values()), {})
        m = (book.get("odds") or {}).get(TOTAL_MARKET_KEY)
        if not m:
            continue
        # NOTE: field names (start/end handicap + od) confirmed via --explore before running
        try:
            con.execute("INSERT OR REPLACE INTO odds VALUES (?,?,?,?,?)",
                        (mid, float(m.get("start_handicap") or m.get("handicap")),
                         float(m.get("end_handicap") or m.get("handicap")),
                         float(m.get("over_od") or 0) or None,
                         float(m.get("under_od") or 0) or None))
            n += 1
        except (TypeError, ValueError):
            continue
        if n % 200 == 0:
            con.commit(); print(f"  ...{n} lines", flush=True)
    con.commit(); con.close()
    print(f"done — {n} total lines in tt.sqlite:odds")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--explore", type=int, metavar="N", help="dump odds structure for N matches")
    ap.add_argument("--pull", action="store_true")
    args = ap.parse_args()
    if not mode():
        sys.exit("set BETSAPI_TOKEN (direct) or BETSAPI_RAPIDAPI_KEY (rapidapi)")
    if args.explore:
        explore(args.explore)
    elif args.pull:
        pull()
    else:
        print("use --explore N first, then --pull")


if __name__ == "__main__":
    main()
