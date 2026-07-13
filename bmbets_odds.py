"""bmbets.com — the SOFT per-book POINTS-totals layer for Czech Liga Pro + TT Elite.

This is the source that actually unlocks the line-shopping edge: bmbets compares the
full-match Over/Under-by-points line across ~10 books INCLUDING the user's soft ones
(1xBet, 22Bet, GGBet, Dafabet, Betano, Betway) and a sharp anchor (bet365, Betfair).

Access split:
  * MATCH DISCOVERY is open — the league list pages render fine via curl_cffi.
  * The ODDS load via `POST /oddsdata`, which is gated by reCAPTCHA-v3, so a plain
    request gets "window.location.reload();". We therefore render each match's
    `#!/overunder-by-points` view in a REAL browser (Playwright/Chromium) — reCAPTCHA v3
    auto-executes in-browser — and scrape the rendered `#oddsContent` table.

Per match we store the whole line ladder with, per line: the BEST (softest) over/under
available and the median (consensus) — so the edge = best-vs-consensus (or vs Kambi).

⚠️ reCAPTCHA v3 scores by IP reputation; on a datacenter IP (GitHub Actions) it may score
low and the odds won't render. Set BMBETS_PROXY=http://user:pass@host:port to route through
a residential proxy if that happens. Run headed under xvfb (BMBETS_HEADED=1) — headed
Chromium scores better than headless.

    python bmbets_odds.py --limit 30        # scrape up to 30 matches -> tt.sqlite:bmbets_odds
    python bmbets_odds.py --limit 5 --headed --show
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sqlite3
import statistics as st
import sys
from pathlib import Path

from curl_cffi import requests as cr

from kambi_odds import npair                       # ONE canonical pair key across odds sources

HERE = Path(__file__).resolve().parent
DB = HERE / "bmbets.sqlite"     # own small DB — avoids colliding with daily.yml's 41MB tt.sqlite commits
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
PROXY = os.environ.get("BMBETS_PROXY") or None
HEADED = os.environ.get("BMBETS_HEADED") == "1"

LEAGUES = {   # names match tt.sqlite / 24live so pair_key + H2H flags join
    "Czech Liga Pro": "https://bmbets.com/table-tennis/czech-republic/czech-liga-pro/",
    "TT Elite Series": "https://bmbets.com/table-tennis/poland/tt-elite-series/",
    "Setka Cup": "https://bmbets.com/table-tennis/ukraine/setka-cup/",
    "TT Cup": "https://bmbets.com/table-tennis/ukraine/tt-cup/",
}
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_DATECOL = re.compile(r'<td[^>]*class="[^"]*date-col[^"]*"[^>]*>(.*?)</td>', re.S)
_LINK = re.compile(r"(/table-tennis/[a-z-]+/[a-z0-9-]+/([a-z0-9-]+)-v-([a-z0-9-]+)-(\d+)/)")
_TIME = re.compile(r"^\d{1,2}:\d{2}$")


def _title(slug: str) -> str:
    return slug.replace("-", " ").title()


def discover(prematch_only=True):
    """Open league pages (no reCAPTCHA) -> matches. PRE-MATCH ONLY by default: bmbets shows a
    bare start time (HH:MM) in the row's date-col for scheduled matches, and FIN/LIVE/a set score
    once it starts. The rich 8-book depth (the edge) is on pre-match; live matches thin to 1 book,
    so we skip them. Rows are page-ordered ~chronologically, so matches[:limit] = the soonest tips
    (freshest openers to shop)."""
    s = cr.Session(impersonate="chrome124")
    out, seen = [], set()
    for lg, u in LEAGUES.items():
        try:
            html = s.get(u, headers={"Accept": "text/html"}, timeout=25).text
        except Exception as e:
            print(f"discover {lg}: {str(e)[:60]}"); continue
        for rm in _ROW.finditer(html):
            row = rm.group(1)
            lm = _LINK.search(row)
            if not lm:
                continue
            full, p1, p2, mid = lm.groups()
            if mid in seen:
                continue
            dc = _DATECOL.search(row)
            status = re.sub(r"<[^>]+>", "", dc.group(1)).strip() if dc else ""
            if prematch_only and not _TIME.match(status):     # keep only scheduled (upcoming) rows
                continue
            seen.add(mid)
            out.append({"league": lg, "url": "https://bmbets.com" + full,
                        "id": mid, "p1": _title(p1), "p2": _title(p2), "start": status})
    # SOONEST-TO-TIP first (bmbets times are GMT/UTC). Books post the O/U only ~1-2h before
    # tip, so far-out matches have NO market yet (empty scrape); the closest matches are the
    # ones with odds up AND the deepest book coverage. Sorting across both leagues fixes the
    # bug where 143 far-out Czech Liga Pro rows crowded out the imminent TT Elite matches.
    now = dt.datetime.now(dt.timezone.utc)
    def _mins(hhmm):
        try:
            h, mm = map(int, hhmm.split(":"))
        except ValueError:
            return 1e9
        tip = now.replace(hour=h, minute=mm, second=0, microsecond=0)
        if tip < now - dt.timedelta(minutes=30):      # already >30min past -> it's tomorrow's card
            tip += dt.timedelta(days=1)
        return (tip - now).total_seconds()
    out.sort(key=lambda m: _mins(m["start"]))
    return out


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def aggregate(rows):
    """rows = [(line, over, under)] scraped per book -> {line: {n, best_over, best_under,
    med_over, med_under}}. best = softest (highest) price you could shop; med = consensus."""
    by = {}
    for line, ov, un in rows:
        ln, o, u = _num(line.lstrip("+")), _num(ov), _num(un)
        if ln is None or (o is None and u is None):
            continue
        d = by.setdefault(ln, {"o": [], "u": []})
        if o and o > 1:
            d["o"].append(o)
        if u and u > 1:
            d["u"].append(u)
    agg = {}
    for ln, d in by.items():
        if not d["o"] and not d["u"]:
            continue
        agg[ln] = {"n": max(len(d["o"]), len(d["u"])),
                   "best_over": max(d["o"]) if d["o"] else None,
                   "best_under": max(d["u"]) if d["u"] else None,
                   "med_over": round(st.median(d["o"]), 3) if d["o"] else None,
                   "med_under": round(st.median(d["u"]), 3) if d["u"] else None}
    return agg


# JS run in-page to pull the rendered O/U-by-points table: [line, over, under] per book row.
_EXTRACT = """() => [...document.querySelectorAll('#oddsContent tr')].map(tr => {
    const h = tr.querySelector('td.odd-han'); const v = tr.querySelectorAll('td.odd-v');
    if (!h || v.length < 2) return null;
    return [h.innerText.trim(), v[0].innerText.trim(), v[1].innerText.trim()];
}).filter(Boolean)"""


def scrape(page, match):
    """Render a match's over/under-by-points view and return its per-line aggregate (or {})."""
    try:
        page.goto(match["url"] + "#!/overunder-by-points", wait_until="domcontentloaded", timeout=30000)
        # the reCAPTCHA'd /oddsdata fills #oddsContent; wait for a real line cell to appear
        page.wait_for_selector("#oddsContent td.odd-han", timeout=15000)
        rows = page.evaluate(_EXTRACT)
        return aggregate(rows)
    except Exception:
        return {}                                   # timeout = blocked (reCAPTCHA) or no O/U posted


def _store(conn, now, match, agg):
    conn.execute("""CREATE TABLE IF NOT EXISTS bmbets_odds(
        collected_at TEXT, league TEXT, p1 TEXT, p2 TEXT, pair_key TEXT, match_id TEXT,
        line REAL, n_books INTEGER, best_over REAL, best_under REAL, med_over REAL, med_under REAL,
        PRIMARY KEY (match_id, line, collected_at))""")
    pk = npair(match["p1"], match["p2"])
    conn.executemany(
        "INSERT OR REPLACE INTO bmbets_odds VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(now, match["league"], match["p1"], match["p2"], pk, match["id"],
          ln, a["n"], a["best_over"], a["best_under"], a["med_over"], a["med_under"])
         for ln, a in agg.items()])


def collect(limit=40, show=False):
    matches = discover()
    print(f"bmbets: {len(matches)} matches listed (Czech Liga Pro + TT Elite); scraping up to {limit}")
    if not matches:
        return 0
    from playwright.sync_api import sync_playwright
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    conn = sqlite3.connect(DB)
    ok = blocked = lines = 0
    with sync_playwright() as pw:
        launch = {"headless": not HEADED, "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"]}
        if PROXY:
            launch["proxy"] = {"server": PROXY}
        browser = pw.chromium.launch(**launch)
        ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        for m in matches[:limit]:
            agg = scrape(page, m)
            if agg:
                _store(conn, now, m, agg); conn.commit()
                ok += 1; lines += len(agg)
                if show:
                    for ln, a in sorted(agg.items()):
                        print(f"  {m['league'][:14]:14} {m['p1'][:12]:12} v {m['p2'][:12]:12} "
                              f"o/u {ln:>5}  best {a['best_over']}/{a['best_under']}  med {a['med_over']}/{a['med_under']} ({a['n']}bk)")
            else:
                blocked += 1
            if ok == 0 and blocked >= 10:      # 10 imminent matches all empty w/ 0 hits -> likely reCAPTCHA
                print("10 empties, 0 successes on the soonest matches — likely reCAPTCHA-blocked; aborting (set BMBETS_PROXY)")
                break
        browser.close()
    conn.close()
    print(f"bmbets: {ok} matches scraped ({lines} lines), {blocked} empty/blocked "
          + ("(reCAPTCHA?  set BMBETS_PROXY)" if blocked and not ok else ""))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--headed", action="store_true", help="headed Chromium (needs a display / xvfb) — passes reCAPTCHA better")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    global HEADED
    HEADED = HEADED or args.headed
    n = collect(args.limit, args.show)
    sys.exit(0 if n >= 0 else 1)


if __name__ == "__main__":
    main()
