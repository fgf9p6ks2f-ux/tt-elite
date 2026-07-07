"""Shared BetsAPI client — works with a DIRECT BetsAPI token or a RapidAPI key.

Set ONE of:
    BETSAPI_TOKEN=xxxxx          # key from betsapi.com directly
    BETSAPI_RAPIDAPI_KEY=xxxxx   # key from rapidapi.com (betsapi2 listing)
"""
import os
import time

import requests

TOKEN = os.environ.get("BETSAPI_TOKEN", "")
RAPIDAPI_KEY = os.environ.get("BETSAPI_RAPIDAPI_KEY", "")
RAPID_HOST = os.environ.get("BETSAPI_RAPIDAPI_HOST", "betsapi2.p.rapidapi.com")


def mode():
    return "rapidapi" if RAPIDAPI_KEY else "direct" if TOKEN else None


def get(path, **params):
    if RAPIDAPI_KEY:
        url = f"https://{RAPID_HOST}{path}"
        headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPID_HOST}
    elif TOKEN:
        url = f"https://api.b365api.com{path}"
        params = {**params, "token": TOKEN}
        headers = {}
    else:
        raise SystemExit("set BETSAPI_TOKEN (direct) or BETSAPI_RAPIDAPI_KEY (rapidapi)")
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403, 429):     # auth / rate — surface it
                return {"_status": r.status_code, "_body": r.text[:200]}
        except requests.RequestException:
            pass
        time.sleep(1.0 * (attempt + 1))
    return {}
