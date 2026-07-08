"""Free table-tennis data via 24live.com — no token, works from a datacenter IP.

Covers ALL four H2H-strategy leagues (one uniform source; BetsAPI no longer needed):

    22357  TT Elite Series     22353  Setka Cup
    22338  Czech Liga Pro      22339  TT Cup

24live's tournament endpoint returns, in ONE call, finished matches WITH per-set
point scores (the totals the strategy needs) plus upcoming fixtures — no per-match
requests. It sits behind Cloudflare, which 403s a bare client but serves 200 to
anything sending browser-like headers, so it runs on GitHub Actions. `seasonId` is
intentionally omitted so the endpoint always tracks the current season.

Names: 24live is "Surname Given [suffix]", tt.sqlite (BetsAPI-era) is "Given Surname".
Base rule: move the FIRST token (surname) to the end. Verified 88/88 TT Elite,
174/177 Setka, 132/138 Liga Pro, 112/116 TT Cup — the misses are all jr./sr./birth-
year suffix players whose DB spellings are inconsistent ("Jnr" vs "Jr", "Snr" vs
"Sr", "Ladislav Havel 1956"). `resolve()` maps those via the league roster (unique
surname + compatible suffix class); genuinely ambiguous ones (two "Jaroslav Strnad
19xx" seniors) return None and the row is SKIPPED — never guess an identity into a
pair's H2H history.
"""
from __future__ import annotations

import datetime as dt
import re
import time

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

# 24live tournament id -> league name (as stored in tt.sqlite). "Setka Women" is
# deliberately NOT "Setka Cup Women": every LIKE '%Setka Cup%' query in the pipeline
# would silently mix the two leagues' rows.
LEAGUES = {22357: "TT Elite Series", 22353: "Setka Cup",
           22338: "Czech Liga Pro", 22339: "TT Cup",
           22364: "Setka Women",                    # validated 2026-07-08, volume tier
           22341: "TT Challenger Series"}           # collect-only (see LEAGUE_CFG)

_SUFFIX = re.compile(r"^(jr\.?|jnr|sr\.?|snr|19\d\d)$", re.I)


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("\xa0", " ")).strip()


def _split_suffix(name: str):
    """'Dufek Jakub jr.' -> ('Dufek Jakub', 'jr') · 'Strnad Jaroslav 1961' -> (..., '1961')."""
    toks = _clean(name).split()
    if len(toks) >= 3 and _SUFFIX.match(toks[-1]):
        return " ".join(toks[:-1]), toks[-1].rstrip(".").lower()
    return _clean(name), None


def _swap(name: str) -> str:
    """'Marushchak Vitalii S' -> 'Vitalii S Marushchak' (surname-first -> given-first)."""
    p = name.split()
    return f"{' '.join(p[1:])} {p[0]}" if len(p) >= 2 else name


def _suffix_class(tok):
    """jr-class vs sr-class (BetsAPI writes seniors as 'Sr', 'Snr', or a birth year)."""
    if tok is None:
        return None
    if tok in ("jr", "jnr"):
        return "jr"
    return "sr"                                   # sr / snr / 19xx


def _canon_suffix(tok):
    return tok if tok and tok.isdigit() else {"jr": "Jr", "jnr": "Jr",
                                              "sr": "Sr", "snr": "Sr"}.get(tok)


def resolve(name: str, roster: set[str] | None = None):
    """24live name -> tt.sqlite name, or None if ambiguous vs the known roster.

    Exact swap first. Else a unique roster candidate qualifies only if it shares the
    surname AND suffix class AND its given name is close (same initial, high overlap)
    — the last guard keeps a NEW player from being welded onto a same-surname
    veteran's H2H history. No candidate -> pass through swapped (+canonical suffix),
    which is self-consistent for all future 24live rows. >1 candidate -> None (skip):
    never guess an identity into a pair's record."""
    from difflib import SequenceMatcher
    base, suf = _split_suffix(name)
    cand = _swap(base)
    canon = cand if suf is None else f"{cand} {_canon_suffix(suf)}"
    if roster is None:
        return canon
    if canon in roster:
        return canon
    surname, given = base.split()[0], " ".join(base.split()[1:])
    hits = []
    for r in roster:
        rtoks = r.split()
        rsuf = rtoks[-1].rstrip(".").lower() if _SUFFIX.match(rtoks[-1]) else None
        body = rtoks[:-1] if rsuf else rtoks
        if not body or body[-1].lower() != surname.lower():
            continue
        if _suffix_class(suf) != _suffix_class(rsuf):
            continue
        rgiven = " ".join(body[:-1])
        if not (given and rgiven) or given[0].lower() != rgiven[0].lower():
            continue
        if SequenceMatcher(None, given.lower(), rgiven.lower()).ratio() < 0.75:
            continue
        hits.append(r)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return canon                              # genuinely new player
    return None                                   # ambiguous — skip, don't guess


def _fetch(tid: int, limit: int, tries: int = 4) -> dict:
    last = None
    for i in range(tries):
        try:
            r = requests.get(URL.format(tid=tid, limit=limit), headers=HEADERS, timeout=40)
            r.raise_for_status()
            return r.json().get("data", {}) or {}
        except (requests.RequestException, ValueError) as e:   # server flaky under load
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"24live tournament {tid} failed after {tries} tries: {last}")


def _pair(m: dict, roster=None):
    parts = m.get("participants") or []
    if len(parts) != 2:
        return None
    a = resolve(parts[0].get("name", ""), roster)
    b = resolve(parts[1].get("name", ""), roster)
    return (a, b) if a and b else None


def _total(m: dict):
    """(total_points, sets 'a-b', per-set 'h-a,...') from the finished score, or None."""
    sc = m.get("score") or {}
    per = [(p.get("home_team"), p.get("away_team")) for p in (sc.get("periods") or [])
           if p.get("home_team") is not None and p.get("away_team") is not None]
    if not per:
        return None
    total = sum(h + a for h, a in per)
    return total, f"{sc.get('home_team')}-{sc.get('away_team')}", \
        ",".join(f"{h}-{a}" for h, a in per)


def results(tid: int, limit: int = 500, roster=None):
    """Finished matches as tt.sqlite rows: (match_id, league, date, p1, p2, total, sets, scores)."""
    league = LEAGUES.get(tid, str(tid))
    out, skipped = [], 0
    for m in _fetch(tid, limit).get("finished") or []:
        if m.get("code_state") != "ended":
            continue
        pair, tot = _pair(m, roster), _total(m)
        if not tot:
            continue
        if not pair:
            skipped += 1
            continue
        total, sets, scores = tot
        date = (m.get("start_date") or "")[:10]
        out.append((f"24l_{m['id']}", league, date, pair[0], pair[1], total, sets, scores))
    return out, skipped


def fixtures(tid: int, limit: int = 200, roster=None):
    """Upcoming fixtures as (p1, p2, start_ts, league, match_id) — feeds check_today.
    match_id matches the '24l_<id>' key results land under, so a flagged bet can later
    be graded against exactly its own match (same-day rematches are common)."""
    league = LEAGUES.get(tid, str(tid))
    out = []
    for m in _fetch(tid, limit).get("not_started") or []:
        pair = _pair(m, roster)
        if not pair:
            continue
        sd = m.get("start_date")
        try:
            ts = int(dt.datetime.fromisoformat(sd).timestamp()) if sd else 0
        except ValueError:
            ts = 0
        out.append((pair[0], pair[1], ts, league, f"24l_{m['id']}"))
    return out


def league_roster(con, league: str) -> set[str]:
    """All player names already recorded for a league — the identity ground truth."""
    ros = set()
    for a, b in con.execute("SELECT DISTINCT p1, p2 FROM matches WHERE league LIKE ?",
                            (f"%{league}%",)):
        ros.add(a)
        ros.add(b)
    return ros
