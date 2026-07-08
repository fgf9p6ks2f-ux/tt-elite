"""Free esoccer/ebasketball data via ESportsBattle's official API — no token.

ESportsBattle operates the "Esoccer Battle" / "Ebasketball Battle" products books
carry. Their scoreboard API is open JSON (no Cloudflare fight):

    {base}/api/tournaments?page&dateFrom&dateTo     dateFrom="YYYY/MM/DD HH:MM" (UTC)
    {base}/api/tournaments/{id}/matches             nicknames + final & period scores

Structure is TT-Elite-like but denser: ~930 esoccer matches/day across ~80 players,
with almost every pair rematching the same day — H2H samples accrue in days.
The strategy entity is the PLAYER NICKNAME (teams rotate around them).
"""
from __future__ import annotations

import time
import urllib.parse as up

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}

# sport key -> (api base, league name stored in tt.sqlite, match_id prefix)
SPORTS = {
    "esoccer":     ("https://football.esportsbattle.com",   "Esoccer Battle",     "esb_f"),
    "ebasketball": ("https://basketball.esportsbattle.com", "Ebasketball Battle", "esb_b"),
}


def _get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"ESB GET failed: {url[:80]} ({last})")


def day_tournaments(base, day):
    """All tournament ids for a UTC day ('YYYY-MM-DD')."""
    d = day.replace("-", "/")
    ids, page = [], 1
    while True:
        q = up.urlencode({"page": page, "dateFrom": f"{d} 00:00", "dateTo": f"{d} 23:59"})
        j = _get(f"{base}/api/tournaments?{q}")
        ids += [t["id"] for t in j.get("tournaments") or []]
        if page >= (j.get("totalPages") or 1):
            return ids
        page += 1


def day_fixtures(sport, day, sleep=0.12):
    """Upcoming matches for a UTC day: [(p1, p2, start_ts, league, match_id)].
    A match is upcoming while its participants have no score yet."""
    import datetime as dt
    base, league, prefix = SPORTS[sport]
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    out = []
    for tid in day_tournaments(base, day):
        try:
            ms = _get(f"{base}/api/tournaments/{tid}/matches")
        except RuntimeError:
            continue
        for m in ms:
            p1, p2 = m.get("participant1") or {}, m.get("participant2") or {}
            n1, n2 = p1.get("nickname"), p2.get("nickname")
            if p1.get("score") is not None or not n1 or not n2:
                continue
            try:
                ts = int(dt.datetime.fromisoformat(
                    str(m.get("date")).replace("Z", "+00:00")).timestamp())
            except ValueError:
                continue
            if ts > now - 300:
                out.append((n1, n2, ts, league, f"{prefix}_{m['id']}"))
        time.sleep(sleep)
    return out


def day_results(sport, day, sleep=0.12):
    """Finished matches for a UTC day as tt.sqlite rows:
    (match_id, league, date, p1, p2, total_points, sets, scores)."""
    base, league, prefix = SPORTS[sport]
    rows = []
    for tid in day_tournaments(base, day):
        try:
            ms = _get(f"{base}/api/tournaments/{tid}/matches")
        except RuntimeError:
            continue
        for m in ms:
            p1, p2 = m.get("participant1") or {}, m.get("participant2") or {}
            s1, s2 = p1.get("score"), p2.get("score")
            n1, n2 = p1.get("nickname"), p2.get("nickname")
            if s1 is None or s2 is None or not n1 or not n2:
                continue                       # not finished (or bye)
            date = (m.get("date") or "")[:10]
            per = ",".join(f"{a}-{b}" for a, b in zip(p1.get("prevPeriodsScores") or [],
                                                      p2.get("prevPeriodsScores") or []))
            rows.append((f"{prefix}_{m['id']}", league, date, n1, n2,
                         int(s1) + int(s2), f"{s1}-{s2}", per))
        time.sleep(sleep)
    return rows
