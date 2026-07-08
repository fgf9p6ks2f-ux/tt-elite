"""Today's actionable table-tennis bets — all four leagues, token-free.

Cross-references upcoming fixtures against the flagged H2H over/under pairs already in
tt.sqlite (built from history). The flags are stable, so this needs only today's FIXTURES
(who's playing) — not fresh results. Prints exactly which matches to bet and which side,
with the league each match is in.

    python check_today.py --min 12 --pct 0.70      # free: 24live fixtures, no token

Fixtures come free from 24live for TT Elite, Setka Cup, Czech Liga Pro, and TT Cup —
no BetsAPI token anywhere in the pipeline.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from pathlib import Path

import source_24live as src
from h2h import DB, h2h_records, load, pair_key

HERE = Path(__file__).resolve().parent

try:
    from zoneinfo import ZoneInfo
    MT = ZoneInfo("America/Denver")
except Exception:                        # fallback: fixed MDT offset
    MT = dt.timezone(dt.timedelta(hours=-6))

# short league tags for phone alerts / table rows
TAG = {"TT Elite Series": "Elite", "Setka Cup": "Setka",
       "Czech Liga Pro": "LigaPro", "TT Cup": "TTCup"}


def mt_time(ts):
    return (dt.datetime.fromtimestamp(int(ts), MT).strftime("%a %-I:%M%p MT")
            if ts else "?")


def write_alerts(bets, line):
    """For each NEW flagged bet (deduped via notified.txt): an immediate alert (alert.txt)
    and a scheduled 5-min-before reminder (reminders.txt: '<unix_ts>\\t<msg>'). Fires once
    per bet, not every run. Returns the new-alert lines."""
    notif = HERE / "notified.txt"
    seen = set(notif.read_text().splitlines()) if notif.exists() else set()
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    new, reminders = [], []
    for b in bets:
        a, c = pair_key(b["p1"], b["p2"])
        key = f"{a}|{c}|{b['side']}|{b['ts']}"          # ts makes it per-match, stable
        if key in seen:
            continue
        seen.add(key)
        msg = (f"[{TAG.get(b['league'], b['league'])}] {b['side'].upper()} {line} — "
               f"{b['p1']} vs {b['p2']} ({b['hit']*100:.0f}%, n{b['n']}, {b['when']})")
        new.append(msg)
        remind_at = b["ts"] - 300                        # 5 min before tip
        if remind_at > now + 30:                         # only schedule future reminders
            reminders.append(f"{remind_at}\tSTARTS SOON — {msg}")
    (HERE / "alert.txt").write_text("\n".join(new))
    (HERE / "reminders.txt").write_text("\n".join(reminders))
    notif.write_text("\n".join(sorted(seen)[-3000:]))    # cap history
    return new


def all_fixtures():
    """Upcoming fixtures from all four leagues, free via 24live:
    [(p1, p2, start_ts, league)]. A flaky league degrades, never blocks the rest."""
    con = sqlite3.connect(DB)
    fx = []
    for tid, league in src.LEAGUES.items():
        try:
            roster = src.league_roster(con, league)
            fx += src.fixtures(tid, roster=roster)
        except RuntimeError as e:
            print(f"  ({league} fixtures skipped: {e})")
    con.close()
    return fx


def actionable(fixtures, rows, line, min_h2h, pct):
    """H2H records are per-league (same pair in two leagues = different dynamics)."""
    rec_by_league = {}
    bets = []
    for p1, p2, ts, league in fixtures:
        if league not in rec_by_league:
            rec_by_league[league] = h2h_records(
                [r for r in rows if r[4] == league], line)
        meets = rec_by_league[league].get(pair_key(p1, p2), [])
        n = len(meets)
        if n < min_h2h:
            continue
        overs = sum(o for _, _, o in meets)
        po = overs / n
        side, hit = ("over", po) if po >= 1 - po else ("under", 1 - po)
        if hit >= pct:
            avg = sum(t for _, t, _ in meets) / n
            bets.append({"hit": hit, "n": n, "side": side, "p1": p1, "p2": p2,
                         "avg": avg, "when": mt_time(ts), "ts": int(ts) if ts else 0,
                         "league": league})
    return sorted(bets, key=lambda b: -b["hit"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", type=float, default=74.5)
    ap.add_argument("--min", type=int, default=12)
    ap.add_argument("--pct", type=float, default=0.70)
    ap.add_argument("--league", default=None, help="restrict to one league (default: all)")
    args = ap.parse_args()

    rows = load(league=args.league, with_league=True)
    fx = all_fixtures()
    if args.league:
        fx = [f for f in fx if args.league.lower() in f[3].lower()]
    bets = actionable(fx, rows, args.line, args.min, args.pct)
    new = write_alerts(bets, args.line)     # alert.txt = new bets for the phone push
    print(f"\n{len(fx)} upcoming fixtures ({args.league or 'all leagues'}) · {len(rows):,} "
          f"historical matches · line {args.line} · flag ≥{args.pct*100:.0f}% over ≥{args.min} "
          f"H2H · {len(new)} new alert(s)\n")
    if not bets:
        print("  no flagged pairs among the upcoming fixtures right now — check back closer "
              "to the slate (fixtures post a few hours ahead).\n")
        return
    print(f"=== {len(bets)} ACTIONABLE BETS TODAY ===")
    print(f"  {'when':<16}{'league':<9}{'matchup':<42}{'bet':>6}{'hit':>6}{'n':>5}{'avg':>7}")
    for b in bets:
        print(f"  {b['when']:<16}{TAG.get(b['league'], b['league']):<9}"
              f"{b['p1']+' vs '+b['p2']:<42}"
              f"{b['side'].upper():>6}{b['hit']*100:>5.0f}%{b['n']:>5}{b['avg']:>7.1f}")
    print(f"\nBet 74.5-style total on the shown side at your book (league tag shows which "
          f"competition). 'hit' = historical H2H hit rate on that side; 'avg' = average total.\n")


if __name__ == "__main__":
    main()
