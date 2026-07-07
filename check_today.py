"""Today's actionable TT Elite bets.

Cross-references upcoming fixtures against the flagged H2H over/under pairs already in
tt.sqlite (built from history). The flags are stable, so this needs only today's FIXTURES
(who's playing) — not fresh results. Prints exactly which matches to bet and which side.

    BETSAPI_TOKEN=xxx python check_today.py --min 12 --pct 0.70

The fixtures source is pluggable (`fixtures()`); today it uses BetsAPI (works while you
have a token). See README for the free-source options for running this on GitHub Actions.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from betsapi_client import get, mode
from h2h import h2h_records, load, pair_key

HERE = Path(__file__).resolve().parent

try:
    from zoneinfo import ZoneInfo
    MT = ZoneInfo("America/Denver")
except Exception:                        # fallback: fixed MDT offset
    MT = dt.timezone(dt.timedelta(hours=-6))


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
        msg = (f"{b['side'].upper()} {line} — {b['p1']} vs {b['p2']} "
               f"({b['hit']*100:.0f}%, n{b['n']}, {b['when']})")
        new.append(msg)
        remind_at = b["ts"] - 300                        # 5 min before tip
        if remind_at > now + 30:                         # only schedule future reminders
            reminders.append(f"{remind_at}\tSTARTS SOON — {msg}")
    (HERE / "alert.txt").write_text("\n".join(new))
    (HERE / "reminders.txt").write_text("\n".join(reminders))
    notif.write_text("\n".join(sorted(seen)[-3000:]))    # cap history
    return new


# current persistent BetsAPI league ids for the high-frequency leagues we cover
LEAGUE_IDS = {29128: "TT Elite Series", 22307: "Setka Cup", 29097: "TT Cup",
              22742: "Czech Liga Pro"}


def fixtures_betsapi(league_ids=tuple(LEAGUE_IDS)):
    """Upcoming fixtures across the covered leagues via BetsAPI: [(p1, p2, start_ts)]."""
    out = []
    for lid in league_ids:
        j = get("/v3/events/upcoming", sport_id=92, league_id=lid)
        for ev in (j.get("results") or []):
            out.append(((ev.get("home") or {}).get("name") or "?",
                        (ev.get("away") or {}).get("name") or "?", ev.get("time")))
    return out


def actionable(fixtures, rows, line, min_h2h, pct):
    rec = h2h_records(rows, line)
    bets = []
    for p1, p2, ts in fixtures:
        meets = rec.get(pair_key(p1, p2), [])
        n = len(meets)
        if n < min_h2h:
            continue
        overs = sum(o for _, _, o in meets)
        po = overs / n
        side, hit = ("over", po) if po >= 1 - po else ("under", 1 - po)
        if hit >= pct:
            avg = sum(t for _, t, _ in meets) / n
            bets.append({"hit": hit, "n": n, "side": side, "p1": p1, "p2": p2,
                         "avg": avg, "when": mt_time(ts), "ts": int(ts) if ts else 0})
    return sorted(bets, key=lambda b: -b["hit"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", type=float, default=74.5)
    ap.add_argument("--min", type=int, default=12)
    ap.add_argument("--pct", type=float, default=0.70)
    ap.add_argument("--league", default=None, help="restrict to one league (default: all)")
    args = ap.parse_args()
    if not mode():
        raise SystemExit("set BETSAPI_TOKEN (fixtures source) — see README for free options")

    rows = load(league=args.league)
    fx = fixtures_betsapi()
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
    print(f"  {'when':<12}{'matchup':<40}{'bet':>6}{'hit':>6}{'n':>5}{'avg':>7}")
    for b in bets:
        print(f"  {b['when']:<12}{b['p1']+' vs '+b['p2']:<40}"
              f"{b['side'].upper():>6}{b['hit']*100:>5.0f}%{b['n']:>5}{b['avg']:>7.1f}")
    print(f"\nBet {args.line} on the shown side at your book. 'hit' = historical H2H hit rate "
          f"on that side; 'avg' = their average total points.\n")


if __name__ == "__main__":
    main()
