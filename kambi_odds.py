"""Real posted TOTAL-POINTS lines for TT Elite Series + Czech Liga Pro, free + keyless.

Source: Kambi's public offering API (`eu-offering-api.kambicdn.com`) — the B2B platform
behind Unibet / LeoVegas / BetRivers. It's a CDN, datacenter-reachable (works on GitHub
Actions), no token. This replaces the blind 74.5 the flags currently assume with the line
the book is actually posting, per match.

Coverage (verified 2026-07-09): Kambi carries exactly TWO of our five leagues —
**TT Elite Series** and **Czech Liga Pro** — regardless of book tenant/market. Setka Cup,
TT Cup and Setka Women are NOT on Kambi (only bet365/1xbet carry them, and those hard-block
our IP). For those three, the model line + the dashboard's manual ladder still stand.

    python kambi_odds.py            # fetch current lines, match to today's fixtures -> tt.sqlite:odds
    python kambi_odds.py --show     # also print the matched lines vs our flags

Line encoding note: Kambi returns odds and lines as integers ×1000 (odds 1850 = 1.85,
line 76500 = 76.5).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import unicodedata
from pathlib import Path

from h2h import DB

HERE = Path(__file__).resolve().parent
BASE = "https://eu-offering-api.kambicdn.com/offering/v2018/kambi"
PARAMS = "lang=en_GB&market=GB"
# Kambi's league label -> our DB league name (they already match, but pin it explicitly so
# a Kambi rename can't silently mis-tag).
LEAGUE_MAP = {"TT Elite Series": "TT Elite Series", "Czech Liga Pro": "Czech Liga Pro"}


def _sess():
    from curl_cffi import requests as cr      # lazy: matching helpers import w/o curl_cffi
    return cr.Session(impersonate="chrome")


# Latin-extended letters NFKD does NOT decompose to ASCII (common in Polish/Nordic/Balkan
# names) — fold them explicitly, else 'Jarosław' -> 'jarosaw' won't match our 'Jaroslaw'.
_SPECIAL = str.maketrans({"ł": "l", "Ł": "l", "ø": "o", "Ø": "o", "đ": "d", "Đ": "d",
                          "ı": "i", "İ": "i", "ħ": "h", "ß": "ss", "æ": "ae", "œ": "oe",
                          "þ": "th", "ð": "d"})


def norm(name: str) -> str:
    """Accent-fold, lowercase, drop dots, and sort tokens WITHIN a name so 'Martin Huk'
    and 'Huk Martin' collapse to the same key (Kambi vs our fixture ordering can differ)."""
    s = (name or "").translate(_SPECIAL)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return " ".join(sorted(s.replace(".", " ").split()))


def npair(a: str, b: str) -> str:
    """Canonical string key for a matchup — used to join Kambi events to our fixtures.
    A STRING (not tuple) so it round-trips cleanly through SQLite."""
    return "|".join(sorted((norm(a), norm(b))))


def fetch(sess, url):
    r = sess.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def total_points(offers):
    """Extract the main Total Points line + over/under decimal odds from an event's
    betOffers. Kambi may post several alt lines; take the one the book marks as the main
    handicap (fallback: the median line). Returns (line, over_od, under_od) or None."""
    tp = [o for o in offers if (o.get("criterion", {}).get("label") or "") == "Total Points"]
    if not tp:
        return None
    # prefer a non-suspended main line; among candidates pick the median line so an
    # extreme alt rung can't masquerade as the main number.
    cands = []
    for o in tp:
        ocs = {oc.get("type", oc.get("label", "")).lower(): oc for oc in o.get("outcomes", [])}
        over = next((oc for k, oc in ocs.items() if "over" in k), None)
        under = next((oc for k, oc in ocs.items() if "under" in k), None)
        if not (over and under):
            continue
        line = over.get("line")
        if line is None:
            continue
        cands.append((line / 1000.0, over.get("odds", 0) / 1000.0, under.get("odds", 0) / 1000.0))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])
    return cands[len(cands) // 2]                      # median line = the main total


def collect(show=False):
    sess = _sess()
    lv = fetch(sess, f"{BASE}/listView/table_tennis.json?{PARAMS}")
    events = lv.get("events", [])
    ours = [e for e in events
            if (e.get("event", e).get("group") in LEAGUE_MAP)]
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    rows = []
    for wrap in ours:
        ev = wrap.get("event", wrap)
        eid = ev.get("id")
        name = ev.get("name") or ""
        league = LEAGUE_MAP[ev.get("group")]
        # "P1 - P2"
        parts = [p.strip() for p in name.split(" - ")]
        if len(parts) != 2:
            continue
        p1, p2 = parts
        try:
            bo = fetch(sess, f"{BASE}/betoffer/event/{eid}.json?{PARAMS}")
        except Exception:
            continue
        tp = total_points(bo.get("betOffers", []))
        if not tp:
            continue
        line, over_od, under_od = tp
        start = ev.get("start")
        rows.append((now, "kambi", str(eid), (start or "")[:10], league, p1, p2,
                     npair(p1, p2), line, over_od, under_od))

    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS odds (
        collected_at TEXT, source TEXT, event_id TEXT, date TEXT, league TEXT,
        p1 TEXT, p2 TEXT, pair_key TEXT, line REAL, over_od REAL, under_od REAL,
        PRIMARY KEY (source, event_id, collected_at))""")
    con.executemany("INSERT OR REPLACE INTO odds VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    print(f"[{now}] kambi: {len(rows)} Total-Points lines "
          f"({sum(1 for r in rows if r[4]=='TT Elite Series')} Elite, "
          f"{sum(1 for r in rows if r[4]=='Czech Liga Pro')} Liga Pro) -> {DB}:odds")
    if show:
        for r in sorted(rows, key=lambda r: (r[4], r[5])):
            print(f"    {r[4]:16} {r[5]} v {r[6]}: total {r[8]:g}  O {r[9]:.2f} / U {r[10]:.2f}")
    return rows


def latest_lines(con, date=None):
    """{normalized-pair_key: {'line','over_od','under_od','league'}} from the newest snapshot
    per event — for check_today / tt_board to read the real posted line by fixture."""
    q = "SELECT pair_key, league, line, over_od, under_od, collected_at FROM odds"
    args = []
    if date:
        q += " WHERE date=?"
        args.append(date)
    q += " ORDER BY collected_at"
    out = {}
    for pk, lg, ln, oo, uo, _ in con.execute(q, args):
        out[pk] = {"league": lg, "line": ln, "over_od": oo, "under_od": uo}  # last write wins
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="print matched lines")
    collect(show=ap.parse_args().show)
