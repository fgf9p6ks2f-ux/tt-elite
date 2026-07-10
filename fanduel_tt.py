"""FanDuel-Alberta table-tennis Total-Points collector — REAL book lines, run LOCALLY.

WHY LOCAL, NOT CI: FanDuel Canada geo-locks to Canadian IPs; GitHub's US runners can't reach
it (confirmed 2026-07-10 — FanDuel US carries no table tennis; the Canadian sbapi won't serve
a US datacenter IP). So run this from your Alberta connection — a cron on your Mac, or on
demand. It appends the real FanDuel Total-Points lines to `fanduel_odds.jsonl` (small, git-
committable); the CI daily loop ingests that into tt.sqlite:odds (source='fanduel') and
`paper_ledger.grade_real` PREFERS it over Kambi. Flow: you run this + commit the jsonl → CI
grades your flags at YOUR book's actual total + price.

Alberta FanDuel opens **2026-07-13**. It's STAGED: mirrors FanDuel's proven sbapi shape but the
Canadian region subdomain / TT page slug / league labels aren't knowable until launch. Run
`--probe` from Alberta on day one; it prints exactly what to set, and (if a market doesn't
auto-parse) dumps the structure so the last mile is a one-line fix.

    python fanduel_tt.py --probe    # discover region + TT page + league & market names
    python fanduel_tt.py            # collect -> fanduel_odds.jsonl  (then git add + commit + push)
    python fanduel_tt.py --show     # collect + print the matched lines

Env (override once --probe reveals them on 7/13):
    FD_AB_STATE  Alberta region subdomain     (default 'ab'; probe tries fallbacks)
    FD_AB_AK     auth key (rotates)           (default = the US key; Canada likely differs)
    FD_TT_PAGE   table-tennis customPageId    (default 'table-tennis')
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path

import kambi_odds as K            # reuse npair/norm so odds keys match the ledger exactly

HERE = Path(__file__).resolve().parent
JSONL = HERE / "fanduel_odds.jsonl"
AK = os.environ.get("FD_AB_AK", "FhMFpcPWXMeyZxOx")     # US key; Alberta's likely differs
STATE = os.environ.get("FD_AB_STATE", "ab")
TT_PAGE = os.environ.get("FD_TT_PAGE", "table-tennis")
REGIONS = list(dict.fromkeys([STATE, "ab", "ca", "on", "ny"]))          # probe order, deduped
SLUGS = list(dict.fromkeys([TT_PAGE, "table-tennis", "table_tennis", "tabletennis"]))
TZ = "America%2FEdmonton"                                # Alberta

# FanDuel competition label -> our DB league name. TUNE in --probe once the real labels appear.
LEAGUE_MAP = {
    "tt elite series": "TT Elite Series", "tt elite": "TT Elite Series",
    "czech liga pro": "Czech Liga Pro", "liga pro": "Czech Liga Pro",
    "setka cup women": "Setka Women", "women setka": "Setka Women",
    "setka cup": "Setka Cup", "setka": "Setka Cup",
    "tt cup": "TT Cup", "win cup": "TT Cup",
}


def _sess():
    from curl_cffi import requests as cr
    return cr.Session(impersonate="chrome")


def _base(region):
    return f"https://sbapi.{region}.sportsbook.fanduel.com/api"


def _get(sess, url):
    r = sess.get(url, timeout=25)
    r.raise_for_status()
    return r.json()


def _dec(american):
    if american is None:
        return None
    a = float(american)
    return round(1 + (a / 100 if a > 0 else 100 / -a), 4)


def _runner_dec(runner):
    """FanDuel prices sit in a few shapes across versions — try them all."""
    wr = runner.get("winRunnerOdds") or {}
    ad = wr.get("americanDisplayOdds") or {}
    if ad.get("americanOdds") is not None:
        return _dec(ad["americanOdds"])
    td = wr.get("trueOdds") or {}
    dd = td.get("decimalOdds") if isinstance(td, dict) else None
    if isinstance(dd, dict) and dd.get("decimalOdds") is not None:
        return round(float(dd["decimalOdds"]), 4)
    if wr.get("americanPrice") is not None:
        return _dec(wr["americanPrice"])
    return None


def _map_league(name):
    low = (name or "").lower()
    for k, v in LEAGUE_MAP.items():
        if k in low:
            return v
    return None


def extract_total(m):
    """(line, over_dec, under_dec) from a FanDuel MATCH Total-Points market, else None. Flexible
    on where FanDuel stashes the line (marketName / market.handicap / runner.handicap)."""
    nm = (m.get("marketName") or "").lower()
    mt = (m.get("marketType") or "").lower()
    is_total = ("total" in nm and ("point" in nm or nm.strip().startswith("total"))) \
        or "total_points" in mt or "match_total" in mt
    if not is_total or any(w in nm for w in ("player", "spread", "handicap", "correct", "winner",
                                             "game", "set ")):
        return None
    line = m.get("handicap")
    if line is None:
        mm = re.search(r"(\d{2,3}(?:\.5)?)", nm)
        line = float(mm.group(1)) if mm else None
    over = under = None
    for r in m.get("runners", []) or []:
        rn = (r.get("runnerName") or "").lower()
        if line is None and r.get("handicap") is not None:
            line = float(r["handicap"])
        if rn.startswith("over") or rn == "o":
            over = _runner_dec(r)
        elif rn.startswith("under") or rn == "u":
            under = _runner_dec(r)
    if line and over and under:
        return round(float(line), 1), over, under
    return None


def _events(sess, region, slug):
    page = _get(sess, f"{_base(region)}/content-managed-page?page=CUSTOM&customPageId={slug}"
                      f"&timezone={TZ}&_ak={AK}")
    att = page.get("attachments", {}) or {}
    return att.get("events", {}) or {}, att.get("competitions", {}) or {}


def _markets(sess, region, eid):
    """All markets for an event, walking its tabs (the total may live under any tab)."""
    out = {}
    ev = _get(sess, f"{_base(region)}/event-page?eventId={eid}&_ak={AK}&timezone={TZ}")
    tabs = (ev.get("layout", {}) or {}).get("tabs", {}) or {}
    titles = [(t.get("title") if isinstance(t, dict) else str(t)) for t in tabs.values()]
    for title in (titles or [None]):
        url = f"{_base(region)}/event-page?eventId={eid}&_ak={AK}&timezone={TZ}"
        if title:
            url += "&tab=" + title.lower().replace(" ", "-").replace("'", "")
        try:
            r = _get(sess, url)
        except Exception:
            continue
        out.update((r.get("attachments", {}) or {}).get("markets", {}) or {})
    return out


def _find_page(sess):
    """First (region, slug) that returns TT events, or (None, None, None)."""
    for region in REGIONS:
        for slug in SLUGS:
            try:
                evs, comps = _events(sess, region, slug)
            except Exception:
                continue
            if evs:
                return region, slug, (evs, comps)
    return None, None, None


def probe():
    sess = _sess()
    print("Probing FanDuel for table tennis — RUN THIS FROM YOUR ALBERTA CONNECTION.\n")
    region, slug, hit = _find_page(sess)
    if not hit:
        print("❌ No FanDuel table-tennis page reachable. If before 2026-07-13, Alberta isn't live "
              "yet. If after: confirm you're on an Alberta IP, and if it still fails the auth key "
              "rotated — open sportsbook.fanduel.com, find a content-managed-page request in "
              "DevTools Network, copy its _ak, and set FD_AB_AK.")
        return
    evs, comps = hit
    leagues = sorted({(c.get("name") or "") for c in comps.values()})
    print(f"✅ WORKS:  region={region}  slug={slug}   ({len(evs)} events)")
    print(f"   -> set  FD_AB_STATE={region}  FD_TT_PAGE={slug}\n")
    print(f"   FanDuel's league labels: {leagues}")
    print(f"   (map any missing ones into LEAGUE_MAP at the top of fanduel_tt.py)\n")
    eid, e = next(iter(evs.items()))
    print(f"   sample event: {e.get('name')}")
    try:
        mk = _markets(sess, region, eid)
        names = sorted({(m.get("marketName") or "") for m in mk.values()})
        print(f"   market names: {names[:25]}")
        parsed = next((extract_total(m) for m in mk.values() if extract_total(m)), None)
        if parsed:
            print(f"   ✅ total market auto-parses -> (line, over, under) = {parsed}")
            print("   You're done: run `python fanduel_tt.py --show` to collect.")
        else:
            print("   ⚠ total market did NOT auto-parse. Paste the market-names list above to me "
                  "and I'll tune extract_total() — one-line fix.")
    except Exception as ex:
        print(f"   (couldn't fetch markets: {str(ex)[:60]})")


def _write_jsonl(rows):
    """Merge new rows over existing by event_id, drop >3d old, rewrite (keeps the file small)."""
    keep = {}
    if JSONL.exists():
        for ln in JSONL.read_text().splitlines():
            try:
                o = json.loads(ln)
                keep[o["event_id"]] = o
            except Exception:
                pass
    for r in rows:
        keep[r["event_id"]] = r
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).strftime("%Y-%m-%d")
    fresh = [o for o in keep.values() if (o.get("date") or "9999") >= cutoff]
    JSONL.write_text("".join(json.dumps(o) + "\n" for o in fresh))


def collect(show=False):
    sess = _sess()
    region, slug, hit = _find_page(sess)
    if not hit:
        print("no FanDuel TT page reachable (needs Alberta live 7/13 + Canadian IP + valid _ak). "
              "Run `python fanduel_tt.py --probe` from your Alberta connection.")
        return 0
    evs, comps = hit
    comp_name = {str(cid): (c.get("name") or "") for cid, c in comps.items()}
    ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()
    rows = []
    for eid, e in evs.items():
        nm = e.get("name") or ""
        if not re.search(r"\s+v\.?s?\.?\s+|\s+@\s+", nm, re.I):
            continue
        league = _map_league(comp_name.get(str(e.get("competitionId")), "")) or _map_league(nm)
        if not league:
            continue
        parts = re.split(r"\s+v\.?s?\.?\s+|\s+@\s+", nm, maxsplit=1, flags=re.I)
        if len(parts) != 2:
            continue
        p1, p2 = parts[0].strip(), parts[1].strip()
        try:
            tot = next((extract_total(m) for m in _markets(sess, region, eid).values()
                        if extract_total(m)), None)
        except Exception:
            continue
        if not tot:
            continue
        line, over, under = tot
        date = str(e.get("openDate") or e.get("startTime") or ts)[:10]
        rows.append({"collected_at": ts, "source": "fanduel", "event_id": str(eid), "date": date,
                     "league": league, "p1": p1, "p2": p2, "pair_key": K.npair(p1, p2),
                     "line": line, "over_od": over, "under_od": under})
    _write_jsonl(rows)
    if show:
        for r in rows:
            print(f"  {r['league']:16} {r['p1']} v {r['p2']}: total {r['line']}  "
                  f"O {r['over_od']} / U {r['under_od']}")
    print(f"[{ts}] fanduel: {len(rows)} Total-Points lines (region={region}) -> {JSONL.name} "
          f"— now: git add {JSONL.name} && git commit -m 'fd lines' && git push")
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="discover region/page/leagues (7/13)")
    ap.add_argument("--show", action="store_true", help="collect + print the lines")
    args = ap.parse_args()
    if args.probe:
        probe()
    else:
        collect(show=args.show)


if __name__ == "__main__":
    main()
