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
from h2h import (DB, DEFAULT_CFG, LEAGUE_CFG, decide, h2h_records, kelly_units,
                 load, pair_key)

HERE = Path(__file__).resolve().parent

try:
    from zoneinfo import ZoneInfo
    MT = ZoneInfo("America/Denver")
except Exception:                        # fallback: fixed MDT offset
    MT = dt.timezone(dt.timedelta(hours=-6))

# short league tags for phone alerts / table rows
TAG = {"TT Elite Series": "Elite", "Setka Cup": "Setka",
       "Czech Liga Pro": "LigaPro", "TT Cup": "TTCup",
       "Setka Women": "SetkaW", "TT Challenger Series": "Chall",
       "Esoccer Battle": "Esoc", "Ebasketball Battle": "Ebball"}


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
        tag = TAG.get(b["league"], b["league"])
        if b.get("tier") == "volume":
            tag += "·VOL"                               # thin-margin volume tier — optional
        w = round(b["raw"] * b["n"])                    # side record, e.g. 16-2 (89%)
        u = kelly_units(b["hit"])                       # sized off rule confidence @-110
        msg = (f"[{tag}] {b['p1']} v {b['p2']} · {b['when']} · "
               f"{b['side'][0].upper()}{line:g} · {w}-{b['n']-w} ({b['raw']*100:.0f}%) "
               f"· {u:g}u")
        new.append(msg)
        remind_at = b["ts"] - 300                        # 5 min before tip
        if remind_at > now + 30:                         # only schedule future reminders
            reminders.append(f"{remind_at}\t5MIN — {msg}")
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


def actionable(fixtures, rows, line, min_h2h=None, pct=None):
    """H2H records are per-league (same pair in two leagues = different dynamics).
    Each league is flagged by its own validated rule (LEAGUE_CFG); --min/--pct
    override the rule with the plain raw threshold when given. Collect-only
    leagues (rule 'off') never flag, even under a manual override."""
    rec_by_league = {}
    bets = []
    for p1, p2, ts, league, mid in fixtures:
        if LEAGUE_CFG.get(league, {}).get("rule") == "off":
            continue
        if league not in rec_by_league:
            rec_by_league[league] = h2h_records(
                [r for r in rows if r[4] == league], line)
        meets = rec_by_league[league].get(pair_key(p1, p2), [])
        # either flag alone triggers the manual raw override (missing one gets a default)
        cfg = ({"rule": "raw", "pct": pct or 0.70, "min": min_h2h or 10}
               if (min_h2h or pct) else LEAGUE_CFG.get(league, DEFAULT_CFG))
        hit = decide(meets, cfg)
        if hit:
            side, strength, n, raw = hit
            avg = sum(t for _, t, _ in meets) / n
            bets.append({"hit": strength, "raw": raw, "n": n, "side": side,
                         "p1": p1, "p2": p2, "avg": avg, "when": mt_time(ts),
                         "ts": int(ts) if ts else 0, "league": league, "mid": mid,
                         "tier": LEAGUE_CFG.get(league, {}).get("tier")})
    return sorted(bets, key=lambda b: -b["hit"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", type=float, default=74.5)
    ap.add_argument("--min", type=int, default=None,
                    help="override: raw rule with this min n (else per-league LEAGUE_CFG)")
    ap.add_argument("--pct", type=float, default=None,
                    help="override: raw rule with this threshold (else per-league LEAGUE_CFG)")
    ap.add_argument("--league", default=None, help="restrict to one league (default: all)")
    args = ap.parse_args()

    rows = load(league=args.league, with_league=True)
    fx = all_fixtures()
    if args.league:
        fx = [f for f in fx if args.league.lower() in f[3].lower()]
    bets = actionable(fx, rows, args.line, args.min, args.pct)
    new = write_alerts(bets, args.line)     # alert.txt = new bets for the phone push
    if not (args.min or args.pct):          # paper-track only the real (league-rule) flags
        import paper_ledger
        paper_ledger.log_flags(bets, args.line)
    mode = (f"raw ≥{(args.pct or 0.70)*100:.0f}% over ≥{args.min or 10} H2H"
            if (args.min or args.pct) else "per-league validated rules")
    print(f"\n{len(fx)} upcoming fixtures ({args.league or 'all leagues'}) · {len(rows):,} "
          f"historical matches · line {args.line} · {mode} · {len(new)} new alert(s)\n")
    if not bets:
        print("  no flagged pairs among the upcoming fixtures right now — check back closer "
              "to the slate (fixtures post a few hours ahead).\n")
        return
    print(f"=== {len(bets)} ACTIONABLE BETS TODAY ===")
    print(f"  {'when':<16}{'league':<12}{'matchup':<42}{'bet':>6}{'conf':>6}{'raw':>6}"
          f"{'n':>5}{'avg':>7}")
    for b in bets:
        tag = TAG.get(b["league"], b["league"]) + ("·VOL" if b.get("tier") == "volume" else "")
        print(f"  {b['when']:<16}{tag:<12}"
              f"{b['p1']+' vs '+b['p2']:<42}{b['side'].upper():>6}"
              f"{b['hit']*100:>5.0f}%{b['raw']*100:>5.0f}%{b['n']:>5}{b['avg']:>7.1f}")
    print(f"\nBet the total on the shown side at your book (league tag = competition). "
          f"'conf' = the league rule's confidence (shrunk posterior for Elite/Setka, raw "
          f"H2H rate for LigaPro/TTCup); 'raw' = unshrunk H2H rate; 'avg' = average total.\n"
          f"·VOL = volume tier (Setka): real but thinnest per-bet edge (~62% vs 52.4% "
          f"break-even) at ~12 bets/day — take only when you want volume; skip freely.\n")


if __name__ == "__main__":
    main()
