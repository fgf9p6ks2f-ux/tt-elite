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
import fd_tt
import realline
from h2h import (DB, DEFAULT_CFG, LEAGUE_CFG, decide, h2h_records, kelly_units,
                 load, pair_key)

ELITE = "TT Elite Series"       # the only league FanDuel prices -> real-line +EV path

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
        if b.get("tier") == "shadow" or b.get("skip_bet"):   # shadow leagues + the 80-90-under leak:
            continue                                     # logged to the paper ledger, never pushed/bet
        a, c = pair_key(b["p1"], b["p2"])
        key = f"{a}|{c}|{b['side']}|{b['ts']}"          # ts makes it per-match, stable
        if key in seen:
            continue
        seen.add(key)
        tag = TAG.get(b["league"], b["league"])
        if b.get("deep"):
            tag = "★" + tag                             # deep rivalry (>=40 H2H) — higher conviction
        if b.get("tier") == "volume":
            tag += "·VOL"                               # thin-margin volume tier — optional
        w = round(b["raw"] * b["n"])                    # side record, e.g. 16-2 (89%)
        dec = realline.american_to_dec(b["odds"]) if b.get("odds") else 1.9091
        u = kelly_units(b["hit"], dec_odds=dec)         # sized off model prob @ the REAL odds
        msg = (f"[{tag}] {b['p1']} v {b['p2']} · {b['when']} · "
               f"{b['zone']} · {w}-{b['n']-w} ({b['raw']*100:.0f}%) · {u:g}u")
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


ELITE_HIT_THR = 0.70    # flag a side only if the pair hits it >=70% of the time AT the FanDuel line
ELITE_MIN_N = 12        # ...over at least this many H2H meetings (guards against small-sample noise)
ELITE_MIN_N_BET = 15    # ...but only BET pairs with >=15 H2H. Loss diagnosis 2026-07-22: the 12-14
                        # bucket went 4-9 / -43% at real FanDuel lines (thin pair-history = unreliable
                        # RAW rate; thin-H2H OVERS were 0-5). 12-14 stay logged+graded for forward
                        # validation, just skipped like the 80-90u leak (never alerted/bet/counted).
DEEP_RIVALRY_N = 40     # ★ higher-conviction TAG (NOT a filter — volume unchanged): pairs with >=40
                        # H2H meetings hit ~77-84% at real FD lines (deep sample = very stable pattern;
                        # 2026-07-22 threshold sweep). Marked ★DEEP on alerts/board, still normal bets.


def _elite_bet(p1, p2, ts, mid, totals, board, con, tier):
    """TT Elite bet vs FanDuel by RAW HIT RATE (no +EV): if the pair has gone OVER or UNDER the
    ACTUAL FanDuel line in >=70% of their H2H meetings (min 12), flag that side. Still stamped with
    the real line + real odds so the ledger grades/pays at the actual FanDuel price."""
    m = board.get(frozenset((fd_tt.norm(p1), fd_tt.norm(p2))))
    if not m or m.get("over_odds") is None or m.get("under_odds") is None:
        return None                                  # no FanDuel price -> can't bet it
    line = m["line"]
    n = len(totals)
    if n < ELITE_MIN_N:                              # too few meetings to trust a 70% rate
        return None
    over_rate = sum(1 for t in totals if t > line) / n
    if over_rate >= ELITE_HIT_THR:
        side, hit, odds = "over", over_rate, m["over_odds"]
    elif (1 - over_rate) >= ELITE_HIT_THR:
        side, hit, odds = "under", 1 - over_rate, m["under_odds"]
    else:
        return None                                  # neither side clears 70% -> no bet
    b = {"hit": hit, "raw": hit, "n": n, "side": side,
         "p1": p1, "p2": p2, "avg": sum(totals) / n, "when": mt_time(ts),
         "ts": int(ts) if ts else 0, "league": ELITE, "mid": mid,
         "line": line, "odds": odds, "edge": None,
         "totals": totals, "tier": tier,
         "zone": f"{'O' if side == 'over' else 'U'}{line:g}",
         "deep": n >= DEEP_RIVALRY_N}
    # 80-90-UNDER LEAK (loss profile 2026-07-21): TT totals are BIMODAL (quick sweep vs deciding-game
    # grind), so unders on 80-90 lines get blown out when a match goes long — 6-9/-4.0u, avg margin
    # -3.8 (genuinely wrong side, not variance). SKIP it: still logged+graded (forward validation) but
    # never alerted/bet, and excluded from the headline record (tt_board tracker). Keep tracking.
    if n < ELITE_MIN_N_BET:                          # thin H2H (12-14): fwd 4-9/-43%, sample too small
        b["skip_bet"] = "thin_h2h"
    elif side == "under" and 80.0 <= line < 90.0:
        b["skip_bet"] = "u80_90"
    elif side == "over" and line < 65.0:
        # LOW-LINE OVER LEAK (real-line loss profile 2026-07-24): overs on a <65 line went 17-21 /
        # 45% / -4.9u at real FanDuel lines (z=+2.6 vs the 69% rest). Same bimodal-totals family as
        # the 80-90u leak — a low posted total means a heavy favorite/likely quick sweep, so betting
        # the pair's usual OVER fights the market's sweep read and busts. Excluding it lifts the
        # bettable book 62%->69% (+24->+29.5u) at lower volume. Logged+graded (forward-confirm the
        # 38-bet sample), never bet. ⚠ CONVICTION FILTERS (raw>=80 / n>=25) do NOT help here — they
        # looked great at the fixed-74.5 PROXY but went 13-16/-4.8u at real lines; don't add them.
        b["skip_bet"] = "o_lowline"
    return b


def actionable(fixtures, rows, line, min_h2h=None, pct=None):
    """H2H records are per-league (same pair in two leagues = different dynamics). TT Elite
    (the only FanDuel-priced league) is flagged by the FanDuel-line 70%-hit-rate rule; the
    remaining shadow leagues keep their own validated fixed-line rule (LEAGUE_CFG). --min/--pct
    force the plain raw rule for ALL leagues (manual override). Collect-only leagues never flag."""
    board = fd_tt.load_board()
    con = sqlite3.connect(DB)
    rec_by_league = {}
    bets = []
    for p1, p2, ts, league, mid in fixtures:
        cfgL = LEAGUE_CFG.get(league, {})
        if cfgL.get("rule") == "off":
            continue
        if league not in rec_by_league:
            rec_by_league[league] = h2h_records(
                [r for r in rows if r[4] == league], line)
        meets = rec_by_league[league].get(pair_key(p1, p2), [])
        # TT Elite = FanDuel-line 70%-hit-rate rule ONLY (unless a manual raw override is in force).
        # If FanDuel hasn't priced the match there's no line to judge, so no bet — NO fallback to the
        # old fixed-74.5 rule (the user wants FanDuel-line bets only).
        if league == ELITE and not (min_h2h or pct):
            m = board.get(frozenset((fd_tt.norm(p1), fd_tt.norm(p2))))
            if m and m.get("over_odds") is not None and m.get("under_odds") is not None:
                b = _elite_bet(p1, p2, ts, mid, [t for _, t, _ in meets],
                               board, con, cfgL.get("tier"))
                if b:
                    bets.append(b)
            continue
        # shadow leagues and manual override: existing fixed-line rule
        cfg = ({"rule": "raw", "pct": pct or 0.70, "min": min_h2h or 10}
               if (min_h2h or pct) else LEAGUE_CFG.get(league, DEFAULT_CFG))
        hit = decide(meets, cfg)
        if hit:
            side, strength, n, raw = hit
            avg = sum(t for _, t, _ in meets) / n
            bets.append({"hit": strength, "raw": raw, "n": n, "side": side,
                         "p1": p1, "p2": p2, "avg": avg, "when": mt_time(ts),
                         "ts": int(ts) if ts else 0, "league": league, "mid": mid,
                         "line": line, "zone": line_zone(meets, side, cfg, line),
                         "totals": [t for _, t, _ in meets],   # raw H2H totals -> per-line ladder
                         "tier": cfgL.get("tier")})
    con.close()
    return sorted(bets, key=lambda b: -b["hit"])


def line_zone(meets, side, cfg, flag_line, spread=3.0):
    """Books post TT totals anywhere from ~71.5 to ~77.5, not just 74.5. This gives
    the bettable RANGE for the flagged side: 'O≤76.5' = take the Over at any posted
    line up to 76.5; 'U≥72.5' = take the Under at any line from 72.5 up. A line
    qualifies when the pair's historical side rate AT THAT LINE still clears the
    league rule's own bar. The flagged line always qualifies by construction."""
    bar = cfg.get("thr") or cfg.get("pct") or 0.70
    n = len(meets)
    grid = [flag_line + i for i in range(-int(spread), int(spread) + 1)]
    ok = [L for L in grid
          if (sum(1 for _, t, _ in meets if (t > L) == (side == "over")) / n) >= bar]
    ok.append(flag_line)
    return f"O≤{max(ok):g}" if side == "over" else f"U≥{min(ok):g}"


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
    print(f"  {'when':<16}{'league':<12}{'matchup':<42}{'zone':>8}{'conf':>6}{'raw':>6}"
          f"{'n':>5}{'avg':>7}")
    for b in bets:
        tag = TAG.get(b["league"], b["league"]) + ("·shadow" if b.get("tier") == "shadow" else "") \
            + ("·SKIP(80-90u leak)" if b.get("skip_bet") else "") \
            + ("·★DEEP" if b.get("deep") else "")
        print(f"  {b['when']:<16}{tag:<12}"
              f"{b['p1']+' vs '+b['p2']:<42}{b['zone']:>8}"
              f"{b['hit']*100:>5.0f}%{b['raw']*100:>5.0f}%{b['n']:>5}{b['avg']:>7.1f}")
    print(f"\n'zone' = bettable line range: O≤X take the Over at any posted total up to X; "
          f"U≥Y take the Under at any total from Y up; posted line outside the zone = skip. "
          f"'conf' = league rule confidence; 'raw' = H2H side rate at 74.5; 'avg' = average "
          f"total.\n")


if __name__ == "__main__":
    main()
