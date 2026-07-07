"""Load a SCORE-repo-style table-tennis CSV (Player1,Player2,Date,P1_G1..P2_G5) into
tt.sqlite. Free real data to prototype/validate on before paying for a full-history API.

    python ingest_csv.py setka_sept2022.csv --league "Setka Cup"
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "tt.sqlite"


def _date(s):
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return s.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="path to the CSV")
    ap.add_argument("--league", default="Setka Cup")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY, league TEXT, date TEXT, p1 TEXT, p2 TEXT,
        total_points INTEGER, sets TEXT, scores TEXT)""")
    rows, n = [], 0
    with open(args.csv, encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f)):
            pts, sets = 0, []
            ok = False
            for g in range(1, 6):
                a, b = r.get(f"P1_G{g}"), r.get(f"P2_G{g}")
                try:
                    ha, hb = int(a), int(b)
                except (TypeError, ValueError):
                    continue
                pts += ha + hb
                sets.append(f"{ha}-{hb}")
                ok = True
            if not ok:
                continue
            mid = f"{args.league}:{r.get('X', i)}:{r['Player1']}:{r['Player2']}:{r['Date']}"
            rows.append((mid, args.league, _date(r["Date"]), r["Player1"].strip(),
                         r["Player2"].strip(), pts, f"{r.get('Sets_P1')}-{r.get('Sets_P2')}",
                         ",".join(sets)))
    con.executemany("INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM matches WHERE league=?", (args.league,)).fetchone()[0]
    con.close()
    print(f"loaded {len(rows)} rows · {n} {args.league} matches now in {DB}")


if __name__ == "__main__":
    main()
