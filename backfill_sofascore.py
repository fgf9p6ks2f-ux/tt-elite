"""Deepen TT history from Sofascore — the 3 shallow leagues only have ~7 weeks.

24live serves only the current season and the BetsAPI backfill only reached mid-May
2026, so Setka Cup / Czech Liga Pro / TT Cup pairs show far fewer meetings than
reality (you saw 7-2 where Sofascore had 20+). Sofascore keeps all-time history WITH
per-set point scores and is reachable from a datacenter IP via curl_cffi impersonation.

Per player in our roster: search -> Sofascore team id -> paginate finished events ->
total points from period scores. Names map back to our roster by canon(surname+given);
matches dedupe against tt.sqlite on (date, unordered pair, total). Rows land under the
existing league name with a 'sofa_' id so provenance is clear and re-runs are idempotent.

    python backfill_sofascore.py --league "Setka Cup" --max-pages 40
    python backfill_sofascore.py --pair "Eduard Rubtsov" "Oleksandr V Kovalchuk"  # verify
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

from curl_cffi import requests as cr

from h2h import pair_key

DB = Path(__file__).resolve().parent / "tt.sqlite"
API = "https://api.sofascore.com/api/v1"
H = {"Accept": "application/json"}


def _get(url, tries=3):
    for i in range(tries):
        try:
            r = cr.get(url, impersonate="chrome124", timeout=25, headers=H)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                return r.json()
            if r.status_code == 404:
                return None
        except Exception:
            pass
        time.sleep(1.0 * (i + 1))
    return None


def _canon(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


def _name_variants(name):
    """Our 'Firstname Surname' -> canon keys to match Sofascore 'Surname, Firstname'."""
    toks = name.split()
    if len(toks) < 2:
        return {_canon(name)}
    first, last = toks[0], toks[-1]
    return {_canon(first + last), _canon(last + first)}


def _candidate_ids(name):
    toks = name.split()
    q = f"{toks[-1]} {toks[0]}" if len(toks) >= 2 else name
    j = _get(f"{API}/search/all?q={q.replace(' ', '%20')}")
    want = _name_variants(name)
    out = []
    for r in (j or {}).get("results") or []:
        e = r.get("entity") or {}
        if (e.get("sport") or {}).get("slug") == "table-tennis" and \
                _canon(e.get("name", "").replace(",", "")) in want:
            out.append(e["id"])
    return out


def verified_team_id(name, our_signature):
    """Return the Sofascore id ONLY if its recent matches overlap what we already have
    for this player (>=3 shared (date,total) pairs). This anchors identity on trusted
    data — the surname 'Kovalchuk' has several distinct players and stale ids, and a
    wrong merge would corrupt the very H2H we're trying to deepen. None if unverifiable."""
    if len(our_signature) < 3:
        return None                     # not enough of our own data to verify against
    for tid in _candidate_ids(name):
        j = _get(f"{API}/team/{tid}/events/last/0")
        sig = set()
        for e in (j or {}).get("events") or []:
            hs, as_ = e.get("homeScore") or {}, e.get("awayScore") or {}
            per = [k for k in hs if k.startswith("period")]
            ts = e.get("startTimestamp")
            if per and ts:
                d = dt.datetime.utcfromtimestamp(ts).date().isoformat()
                sig.add((d, sum((hs.get(k) or 0) + (as_.get(k) or 0) for k in per)))
        if len(sig & our_signature) >= 3:
            return tid
    return None


def player_matches(team_id, max_pages):
    """[(date, home_name, away_name, total_points)] across the player's finished events."""
    out = []
    for page in range(max_pages):
        j = _get(f"{API}/team/{team_id}/events/last/{page}")
        evs = (j or {}).get("events") or []
        if not evs:
            break
        for e in evs:
            if (e.get("status") or {}).get("type") != "finished":
                continue
            hs, as_ = e.get("homeScore") or {}, e.get("awayScore") or {}
            per = [k for k in hs if k.startswith("period")]
            if not per:
                continue
            tot = sum((hs.get(k) or 0) + (as_.get(k) or 0) for k in per)
            ts = e.get("startTimestamp")
            date = dt.datetime.utcfromtimestamp(ts).date().isoformat() if ts else None
            out.append((date, (e.get("homeTeam") or {}).get("name", ""),
                        (e.get("awayTeam") or {}).get("name", ""), tot))
        time.sleep(0.3)
    return out


def _sofa_to_roster(sofa_name, surinit):
    """Map a Sofascore event team name to a roster player. Event names are abbreviated
    ('Kovalchuk O.', 'Matiushenko A.') — match on (surname, first-initial), and REQUIRE
    that pair to be UNIQUE in the roster. Ambiguous (two 'Kovalchuk O.') or absent -> None
    (skipped), so we never guess which same-surname player it was."""
    nm = sofa_name.replace(",", "").replace(".", "").split()
    if len(nm) < 2:
        return None
    key = (_canon(nm[0]), _canon(nm[1])[:1])          # (surname, first initial)
    hits = surinit.get(key)
    return hits[0] if hits and len(hits) == 1 else None


def backfill(league, max_pages, only_pair=None):
    con = sqlite3.connect(DB)
    roster = set()
    for a, b in con.execute("SELECT DISTINCT p1, p2 FROM matches WHERE league=?", (league,)):
        roster.add(a)
        roster.add(b)
    # roster index by (surname, first-initial) for abbreviated-name mapping
    surinit = {}
    for nm in roster:
        toks = nm.split()
        if len(toks) >= 2:
            surinit.setdefault((_canon(toks[-1]), _canon(toks[0])[:1]), []).append(nm)
    # per-player trusted signature (date,total) to verify Sofascore identity
    sig = {}
    for p, d, t in con.execute(
            "SELECT p1, date, total_points FROM matches WHERE league=?", (league,)):
        sig.setdefault(p, set()).add((d, t))
    for p2, d, t in con.execute(
            "SELECT p2, date, total_points FROM matches WHERE league=?", (league,)):
        sig.setdefault(p2, set()).add((d, t))
    existing = {(d, pair_key(a, b), t) for d, a, b, t in con.execute(
        "SELECT date, p1, p2, total_points FROM matches WHERE league=?", (league,))}

    targets = [only_pair[0], only_pair[1]] if only_pair else sorted(roster)
    batch, verified = [], 0
    for i, nm in enumerate(targets):
        tid = verified_team_id(nm, sig.get(nm, set()))
        if not tid:
            continue
        verified += 1
        for date, home, away, tot in player_matches(tid, max_pages):
            p1 = _sofa_to_roster(home, surinit)
            p2 = _sofa_to_roster(away, surinit)
            if not (p1 and p2 and date and tot) or p1 == p2:
                continue          # both endpoints must map UNIQUELY to this league's roster
            ident = (date, pair_key(p1, p2), tot)
            if ident in existing:
                continue
            existing.add(ident)
            mid = f"sofa_{league[:3].lower()}_{date}_{_canon(p1)[:6]}_{_canon(p2)[:6]}_{tot}"
            batch.append((mid, league, date, p1, p2, tot, "", ""))
        if not only_pair and i % 20 == 0:
            print(f"  {league}: {i}/{len(targets)} players, {verified} verified, "
                  f"+{len(batch)} new", flush=True)
    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?,?,?)", batch)
    con.commit()
    added = con.total_changes - before
    tot = con.execute("SELECT COUNT(*), MIN(date) FROM matches WHERE league=?",
                      (league,)).fetchone()
    con.close()
    print(f"{league}: {verified}/{len(targets)} players verified, +{added} new. "
          f"now {tot[0]:,} matches back to {tot[1]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="Setka Cup")
    ap.add_argument("--max-pages", type=int, default=40)   # ~30 events/page
    ap.add_argument("--pair", nargs=2, help="verify one pair only")
    args = ap.parse_args()
    backfill(args.league, args.max_pages, only_pair=args.pair)


if __name__ == "__main__":
    main()
