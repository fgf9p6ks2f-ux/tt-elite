"""Free TT Elite Series data via 24live.com — no token, works from a datacenter IP.

24live's tournament endpoint returns, in ONE call, finished matches WITH per-set
point scores (the total the H2H strategy needs), plus upcoming fixtures — no
per-match requests. It sits behind Cloudflare, which 403s a bare client but serves
200 to anything sending browser-like headers (below), so it runs on GitHub Actions.

This is the free replacement for BetsAPI on the leagues 24live carries. `seasonId`
is intentionally omitted so the endpoint always tracks the current season.

    24live "Surname Firstname"  ->  tt.sqlite "Firstname Surname"  (verified 88/88)
"""
from __future__ import annotations

import datetime as dt

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://24live.com/",
    "X-Requested-With": "XMLHttpRequest",
}
URL = "https://24live.com/api/tournament/{tid}?lang=en&section=all&short=0&limit={limit}"

# 24live tournament id -> league name (as stored in tt.sqlite)
LEAGUES = {22357: "TT Elite Series"}


def _swap(name: str) -> str:
    """'Sikon Mateusz' -> 'Mateusz Sikon' (24live surname-first -> BetsAPI first-last)."""
    p = name.split()
    return f"{p[-1]} {' '.join(p[:-1])}" if len(p) >= 2 else name


def _fetch(tid: int, limit: int) -> dict:
    r = requests.get(URL.format(tid=tid, limit=limit), headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json().get("data", {}) or {}


def _pair(m: dict):
    parts = m.get("participants") or []
    if len(parts) != 2:
        return None
    return _swap(parts[0].get("name", "?")), _swap(parts[1].get("name", "?"))


def _total(m: dict):
    """(total_points, sets 'a-b', per-set 'h-a,h-a,...') from the finished score, or None."""
    sc = m.get("score") or {}
    per = [(p.get("home_team"), p.get("away_team")) for p in (sc.get("periods") or [])
           if p.get("home_team") is not None and p.get("away_team") is not None]
    if not per:
        return None
    total = sum(h + a for h, a in per)
    return total, f"{sc.get('home_team')}-{sc.get('away_team')}", \
        ",".join(f"{h}-{a}" for h, a in per)


def results(tid: int = 22357, limit: int = 500):
    """Finished matches as tt.sqlite rows: (match_id, league, date, p1, p2, total, sets, scores)."""
    league = LEAGUES.get(tid, "TT Elite Series")
    out = []
    for m in _fetch(tid, limit).get("finished") or []:
        if m.get("code_state") != "ended":
            continue
        pair, tot = _pair(m), _total(m)
        if not pair or not tot:
            continue
        total, sets, scores = tot
        date = (m.get("start_date") or "")[:10]
        out.append((f"24l_{m['id']}", league, date, pair[0], pair[1], total, sets, scores))
    return out


def fixtures(tid: int = 22357, limit: int = 200):
    """Upcoming fixtures as (p1, p2, start_ts) — feeds check_today."""
    out = []
    for m in _fetch(tid, limit).get("not_started") or []:
        pair = _pair(m)
        if not pair:
            continue
        sd = m.get("start_date")
        try:
            ts = int(dt.datetime.fromisoformat(sd).timestamp()) if sd else 0
        except ValueError:
            ts = 0
        out.append((pair[0], pair[1], ts))
    return out
