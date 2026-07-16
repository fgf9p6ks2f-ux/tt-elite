"""Real-line +EV engine for TT Elite — bet against FanDuel's ACTUAL total, not 74.5.

The old rule flagged "over 74.5" off a pair's H2H rate at a fixed 74.5. But FanDuel prices
each match on its own line (56.5-77.5) at its own juice, so that flag scored a bet that
wasn't on the board. This evaluates the real proposition: the model's calibrated
P(total > FanDuel's line) vs FanDuel's devigged odds, and bets the +EV side.

Calibration (walk-forward OOS on 79.8k Elite matches, backtest_realline.py):
  · shrunk posterior at the line, shrink k=16 toward a TRAILING-2500 league base
    (trailing, because Elite totals drift UP over the season and an all-history base lags)
  · a +0.18 logit over-lean de-bias — the engine persistently under-predicts overs by
    ~3-4.5% (a moving, drift-driven bias); +0.18 flattens recent reliability to within ~1%
    across 0.3-0.9. RE-ESTIMATE this monthly (or from the live ledger) as the drift walks on.
  · beats the league-base and raw-pair baselines on Brier at every line.

The RANKING is validated on history; the EDGE magnitude vs real odds can only prove out
forward (no historical FD odds exist), so the honest paper ledger is the real judge.
"""
from __future__ import annotations

import math

K = 16.0                     # Elite shrink strength (matches LEAGUE_CFG "TT Elite Series")
MIN_N = 12                   # min prior H2H meetings to trust a pair estimate
BASE_WINDOW = 2500           # trailing Elite matches for the league base(L)
DEBIAS_LOGIT = 0.18          # over-lean correction; SEE NOTE — re-tune as totals drift
EDGE_MIN = 0.05              # require model to beat the devigged market by >=5 pts to bet


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sig(x):
    return 1 / (1 + math.exp(-x))


def american_to_dec(a):
    a = float(a)
    return 1 + a / 100 if a > 0 else 1 + 100 / (-a)


def implied(a):
    return 1 / american_to_dec(a)


def devig(over_odds, under_odds):
    """Two-way no-vig probabilities from American odds -> (p_over_mkt, p_under_mkt)."""
    io, iu = implied(over_odds), implied(under_odds)
    s = io + iu
    return io / s, iu / s


def recent_base(con, line, window=BASE_WINDOW):
    """Trailing-window league over-rate at `line` (tracks the upward total drift)."""
    tots = [r[0] for r in con.execute(
        "SELECT total_points FROM matches WHERE total_points IS NOT NULL "
        "AND league LIKE '%TT Elite%' ORDER BY date DESC LIMIT ?", (window,))]
    return (sum(1 for t in tots if t > line) / len(tots)) if tots else 0.5


def p_over(prior_totals, line, base, debias=DEBIAS_LOGIT):
    """Calibrated P(total > line): shrunk-posterior H2H rate + over-lean de-bias."""
    n = len(prior_totals)
    overs = sum(1 for t in prior_totals if t > line)
    shrunk = (overs + K * base) / (n + K)
    return _sig(_logit(shrunk) + debias)


def ev_pick(prior_totals, line, over_odds, under_odds, base,
            min_n=MIN_N, edge_min=EDGE_MIN, debias=DEBIAS_LOGIT):
    """The +EV side (or None). Returns a dict with model/market probs, edge, real odds.
    'edge' = model P(side) - devigged market P(side); we bet the larger side if it clears
    edge_min. Grading/PnL then use the REAL line + REAL odds carried here."""
    if len(prior_totals) < min_n or over_odds is None or under_odds is None:
        return None
    pmo = p_over(prior_totals, line, base, debias)
    mko, mku = devig(over_odds, under_odds)
    e_over, e_under = pmo - mko, (1 - pmo) - mku
    if e_over >= edge_min and e_over >= e_under:
        return {"side": "over", "line": line, "odds": over_odds,
                "p_model": pmo, "p_mkt": mko, "edge": e_over}
    if e_under >= edge_min:
        return {"side": "under", "line": line, "odds": under_odds,
                "p_model": 1 - pmo, "p_mkt": mku, "edge": e_under}
    return None
