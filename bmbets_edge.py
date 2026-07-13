"""bmbets_edge.py — the TT line-shopping ALERT: validated H2H lean × real soft book price.

This is the payoff layer. For each match in the latest bmbets per-book snapshot, it:
  1. pulls the pair's H2H total-points history (tt.sqlite) at the posted line,
  2. applies the WALK-FORWARD-VALIDATED per-league shrunk model (h2h.LEAGUE_CFG) -> side + strength,
  3. combines that with the SOFTEST price bmbets found across books,
  4. reports an HONEST EV — the strength credibility-shrunk toward the market (cred=0.45, the same
     correction kelly_units uses, because the raw H2H strength is holdout-optimistic and the live
     rate is lower) — plus a fractional-Kelly stake.

Only the BET-tier leagues (TT Elite, Setka Cup) push to ntfy. Czech Liga Pro + TT Cup are SHADOW
(the live ledger showed them -EV) so they're shown but not pushed unless --push-shadow. The edge is
real where bmbets is deep AND the league is bet-tier — i.e. **Setka Cup** most of all.

    python bmbets_edge.py                 # print today's spots (bet + shadow)
    python bmbets_edge.py --push          # push NEW bet-tier spots to ntfy
    python bmbets_edge.py --push --push-shadow   # also push shadow (you were warned)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

from h2h import DB as TTDB, LEAGUE_CFG, DEFAULT_CFG, decide, kelly_units
from kambi_odds import npair                       # canonical pair key (bmbets rows store it too)

HERE = Path(__file__).resolve().parent
BMDB = HERE / "bmbets.sqlite"
SEEN = HERE / "bmbets_seen.json"
EV_MIN = float(os.environ.get("BMBETS_EV_MIN", "0.03"))
CRED = 0.45     # credibility shrink toward the market — raw H2H strength is holdout-optimistic


def _today():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def history():
    """npair -> [(date, total)] from tt.sqlite matches (bmbets pair_key is the same npair)."""
    idx = defaultdict(list)
    if not TTDB.exists():
        return idx
    con = sqlite3.connect(TTDB)
    for d, p1, p2, tot in con.execute(
            "SELECT date,p1,p2,total_points FROM matches WHERE total_points IS NOT NULL"):
        idx[npair(p1, p2)].append((d, tot))
    con.close()
    return idx


def latest_bm():
    if not BMDB.exists():
        return []
    con = sqlite3.connect(BMDB)
    con.row_factory = sqlite3.Row
    ts = con.execute("SELECT MAX(collected_at) FROM bmbets_odds").fetchone()[0]
    rows = [dict(r) for r in con.execute("SELECT * FROM bmbets_odds WHERE collected_at=?", (ts,))] if ts else []
    con.close()
    return rows


def spots(ev_min=EV_MIN):
    """Best +EV (H2H-lean × soft-price) spot per match, honest-EV sorted desc."""
    hist = history()
    out = {}
    for r in latest_bm():
        cfg = LEAGUE_CFG.get(r["league"], DEFAULT_CFG)
        if cfg.get("rule") == "off":
            continue
        totals = hist.get(r["pair_key"], [])
        meets = [(d, t, t > r["line"]) for d, t in totals]     # over/under at THIS posted line
        dec = decide(meets, cfg)
        if not dec:
            continue
        side, strength, n, raw = dec
        price = r["best_over"] if side == "over" else r["best_under"]
        if not price or price < 1.1:
            continue
        be = 1.0 / price
        p_adj = be + CRED * (strength - be)                    # honest (market-shrunk) prob
        ev = p_adj * price - 1
        if ev < ev_min:
            continue
        k = r["match_id"]
        if k not in out or ev > out[k]["ev"]:
            out[k] = {"league": r["league"], "tier": cfg.get("tier", "bet"),
                      "p1": r["p1"], "p2": r["p2"], "line": r["line"], "side": side,
                      "price": price, "ev": ev, "raw": raw, "n": n, "nbk": r["n_books"],
                      "stake": kelly_units(strength, price)}
    return sorted(out.values(), key=lambda s: -s["ev"])


def fmt(s):
    return (f"{s['league']}: {s['p1']} v {s['p2']}\n"
            f"  {s['side'].upper()} {s['line']:g} @ {s['price']:.2f}  +{s['ev'] * 100:.1f}% EV  "
            f"{s['stake']:g}u   ({s['n']} H2H {s['raw'] * 100:.0f}%, {s['nbk']}bk)")


def _load_seen():
    try:
        d = json.loads(SEEN.read_text())
        return d if d.get("date") == _today() else {"date": _today(), "keys": []}
    except Exception:
        return {"date": _today(), "keys": []}


def _key(s):
    return f"{s['league']}|{s['p1']}|{s['p2']}|{s['side']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--push-shadow", action="store_true", help="also push shadow leagues (historically -EV)")
    args = ap.parse_args()

    all_s = spots()
    bet = [s for s in all_s if s["tier"] == "bet"]
    shadow = [s for s in all_s if s["tier"] != "bet"]
    print(f"=== BET-tier (TT Elite / Setka Cup): {len(bet)} spot(s) ===")
    for s in bet:
        print(fmt(s))
    print(f"\n=== SHADOW (Czech Liga Pro / TT Cup — historically -EV, not pushed): {len(shadow)} ===")
    for s in shadow:
        print(fmt(s))

    if not args.push:
        return
    pushable = bet + (shadow if args.push_shadow else [])
    seen = _load_seen()
    fresh = [s for s in pushable if _key(s) not in seen["keys"]]
    topic = os.environ.get("NTFY_TOPIC")
    if fresh and topic:
        import requests
        body = "\U0001F3D3 TT SOFT LINE +EV (bmbets × H2H)\n\n" + "\n".join(fmt(s) for s in fresh)
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                          headers={"Title": "TT soft +EV", "Tags": "ping_pong", "Priority": "high"},
                          timeout=15).raise_for_status()
            seen["keys"] += [_key(s) for s in fresh]
            SEEN.write_text(json.dumps(seen))
            print(f"\npushed {len(fresh)} new spot(s)")
        except Exception as e:
            print("push failed:", str(e)[:70])
    else:
        print("\nno new spots to push" if topic else "\nNTFY_TOPIC unset — printed only")


if __name__ == "__main__":
    main()
