"""Verify your BetsAPI key + dump the REAL data structure so I can lock the parsers.

Run this and paste the WHOLE output back (it prints no secrets — just structure).

    BETSAPI_TOKEN=xxxxx python test_key.py            # direct betsapi.com key
    BETSAPI_RAPIDAPI_KEY=xxxxx python test_key.py     # rapidapi key
"""
import datetime as dt
import json

from betsapi_client import get, mode

print("=== mode:", mode(), "===")

print("\n[1] league discovery — sport 92 (table tennis):")
j = get("/v1/league", sport_id=92, page=1)
if j.get("_status"):
    raise SystemExit(f"AUTH/RATE ERROR {j['_status']}: {j.get('_body')}\n"
                     "-> if you used a rapidapi key set BETSAPI_RAPIDAPI_KEY (not TOKEN), "
                     "or vice-versa; also check the key is active.")
res = j.get("results") or []
print(f"    {len(res)} leagues returned; matches for our targets:")
for lg in res:
    nm = (lg.get("name") or "")
    if any(w in nm.lower() for w in ("elite", "cup", "setka", "liga", "pro")):
        print("     ", lg.get("id"), "|", nm)

print("\n[2] TT Elite (league 29128) ended events — find a day with matches:")
found = None
for back in range(0, 10):
    day = (dt.date.today() - dt.timedelta(days=back)).strftime("%Y%m%d")
    j = get("/v3/events/ended", sport_id=92, league_id=29128, day=day)
    r = j.get("results") or []
    print(f"     {day}: {len(r)} events")
    if r:
        found = r[0]
        break

if not found:
    print("     no TT Elite events found in the last 10 days — try a different league id "
          "from [1], or the league may be off-season.")
else:
    print("\n[3] RAW ended event (the fields the parser reads):")
    print(json.dumps({k: found.get(k) for k in
                      ("id", "time", "home", "away", "ss", "scores", "league")}, indent=1))
    print("\n[4] odds summary for that event (to lock the total-line + set-betting keys):")
    o = get("/v1/event/odds/summary", event_id=found.get("id"))
    print(json.dumps(o, indent=1)[:2000])
