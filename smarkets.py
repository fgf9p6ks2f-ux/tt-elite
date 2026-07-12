"""Smarkets exchange — the SHARP totals reference for the TT strategy.

The H2H tool flags pairs by their point-total history, but history alone can NEVER
tell you a book's line is soft — only a sharp anchor can. Smarkets is a betting
EXCHANGE (peer-to-peer, ~zero vig): the midpoint of its best bid/offer on the
full-match Over/Under is the market's true probability. We pull it for every
live + upcoming match in the TT leagues and store the fair line + over/under
probability. Once Betway (your soft book) is wired, the bet signal = Betway's
line/price diverging from THIS anchor by enough to clear the vig.

Public JSON API, no auth. Smarkets prices are basis points of probability
(10000 = 100.00%), so fair_decimal = 10000 / mid_price.

    python smarkets.py            # collect a snapshot into tt.sqlite (source='smarkets')
    python smarkets.py --summary  # collect, then print the current sharp lines
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import sys
import time

import requests

DB = "tt.sqlite"
BASE = "https://api.smarkets.com/v3"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# slug segment -> canonical league name (matches how tt.sqlite / 24live store them)
LEAGUES = {
    "czech-liga-pro": "Czech Liga Pro",
    "tt-elite-series": "TT Elite Series",
    "setka-cup": "Setka Cup",
    "tt-cup": "TT Cup",
    "liga-pro": "Czech Liga Pro",
}
_FULL_OU = re.compile(r"^Over/under (\d+\.\d)$")      # full-match total (NOT "Set 1 over/under ...")
_SIDE = re.compile(r"^(Over|Under) (\d+\.\d) points$")

_S = requests.Session()
_S.headers["User-Agent"] = UA


def _get(path, tries=3, **params):
    for i in range(tries):
        try:
            r = _S.get(f"{BASE}{path}", params=params, timeout=25)
            if r.ok:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(0.6 * (i + 1))
    return {}


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def pk(a, b):
    """Canonical pair key (lowercase, pipe-joined, sorted) — shared with the odds table
    so smarkets + betway rows for the same match collide on one key."""
    return "|".join(sorted(x.strip().lower() for x in (a, b)))


def matches():
    """Every live + upcoming TT match: {event_id, start, league, p1, p2}."""
    out, seen = [], set()
    for state in ("live", "upcoming"):
        for e in _get("/events/", type_domain="table_tennis", state=state, limit=200).get("events", []):
            name, slug = e.get("name") or "", e.get("full_slug") or ""
            if " vs " not in name or not e.get("start_datetime") or e["id"] in seen:
                continue
            seg = slug.split("/")
            league = LEAGUES.get(seg[3] if len(seg) > 3 else "", None)
            if not league:                      # only our leagues (women's/others skipped)
                continue
            p1, p2 = (s.strip() for s in name.split(" vs ", 1))
            seen.add(e["id"])
            out.append({"event_id": e["id"], "start": e["start_datetime"],
                        "league": league, "p1": p1, "p2": p2})
    return out


def _totals_markets(event_ids):
    """{event_id: [(market_id, line)]} for the FULL-MATCH over/under only."""
    res = {}
    for batch in _chunks(event_ids, 40):
        ids = ",".join(str(i) for i in batch)
        for m in _get(f"/events/{ids}/markets/").get("markets", []):
            mo = _FULL_OU.match(m.get("name") or "")
            if mo:
                res.setdefault(m["event_id"], []).append((m["id"], float(mo.group(1))))
    return res


def _sides(market_ids):
    """{market_id: {'over': contract_id, 'under': contract_id}}."""
    res = {}
    for batch in _chunks(market_ids, 40):
        ids = ",".join(str(i) for i in batch)
        for c in _get(f"/markets/{ids}/contracts/").get("contracts", []):
            mo = _SIDE.match(c.get("name") or "")
            if mo:
                res.setdefault(c["market_id"], {})[mo.group(1).lower()] = c["id"]
    return res


def _quotes(market_ids):
    """{contract_id: fair_prob} from the mid of best bid/offer. The quotes payload is
    FLAT-keyed by contract_id ({cid: {bids:[...], offers:[...]}}); empty books (no
    liquidity — common on these micro-leagues) are skipped."""
    prob = {}
    for batch in _chunks(market_ids, 40):
        ids = ",".join(str(i) for i in batch)
        for cid, q in _get(f"/markets/{ids}/quotes/").items():
            if not isinstance(q, dict):
                continue
            bids, offers = q.get("bids") or [], q.get("offers") or []
            bb = bids[0]["price"] if bids else None
            bo = offers[0]["price"] if offers else None
            mid = ((bb + bo) / 2) if (bb and bo) else (bb or bo)   # mid, else the one side quoted
            if mid:
                prob[cid] = mid / 10000.0                          # basis points -> probability
    return prob


def collect(db=DB, verbose=False):
    """Pull a snapshot of Smarkets fair full-match totals and append to the odds table."""
    ms = matches()
    if not ms:
        if verbose:
            print("no live/upcoming TT matches on Smarkets right now.")
        return 0
    by_id = {m["event_id"]: m for m in ms}
    tmk = _totals_markets(list(by_id))
    market_ids = [mid for lst in tmk.values() for mid, _ln in lst]
    sides = _sides(market_ids)
    prob = _quotes(market_ids)

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE IF NOT EXISTS odds(
        collected_at TEXT, source TEXT, event_id TEXT, date TEXT, league TEXT,
        p1 TEXT, p2 TEXT, pair_key TEXT, line REAL, over_od REAL, under_od REAL)""")
    rows, n_ev = [], 0
    for eid, lines in tmk.items():
        m = by_id[eid]
        got = False
        for mid, line in lines:
            sd = sides.get(mid, {})
            po, pu = prob.get(sd.get("over")), prob.get(sd.get("under"))
            if not po and not pu:
                continue
            # exchange ~zero vig: fill a missing side from its complement
            po = po or (1 - pu)
            pu = pu or (1 - po)
            over_od = round(1 / po, 3) if po else None
            under_od = round(1 / pu, 3) if pu else None
            rows.append((now, "smarkets", str(eid), m["start"][:10], m["league"],
                         m["p1"], m["p2"], pk(m["p1"], m["p2"]), line, over_od, under_od))
            got = True
        n_ev += got
    if rows:
        con.executemany("INSERT INTO odds(collected_at,source,event_id,date,league,p1,p2,"
                        "pair_key,line,over_od,under_od) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
    con.close()
    if verbose:
        print(f"smarkets: {len(ms)} matches, {n_ev} with a full-match total → {len(rows)} sharp lines stored.")
    return len(rows)


def summary(db=DB):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    ts = con.execute("SELECT MAX(collected_at) FROM odds WHERE source='smarkets'").fetchone()[0]
    if not ts:
        print("no smarkets snapshot yet."); con.close(); return
    rows = con.execute("SELECT * FROM odds WHERE source='smarkets' AND collected_at=? "
                       "ORDER BY league, date", (ts,)).fetchall()
    con.close()
    print(f"SHARP (Smarkets) fair full-match totals — snapshot {ts}\n")
    for r in rows:
        po = 1 / r["over_od"] if r["over_od"] else None
        print(f"  {r['league'][:16]:16} {r['p1'][:16]:16} v {r['p2'][:16]:16}  "
              f"O/U {r['line']:>5}  over {r['over_od']} / under {r['under_od']}"
              + (f"  (P over {po*100:.0f}%)" if po else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true", help="print the sharp lines after collecting")
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()
    n = collect(args.db, verbose=True)
    if args.summary:
        print()
        summary(args.db)
    sys.exit(0 if n >= 0 else 1)


if __name__ == "__main__":
    main()
