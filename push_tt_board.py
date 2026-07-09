"""Ship tt_board.json to the PUBLIC tennis-odds-collector repo so the dashboard's Table
Tennis tab can read it. Uses the GitHub Contents API with PUBLIC_REPO_PAT (a fine-grained
PAT with Contents: read+write on the public repo). No-ops silently if the PAT isn't set,
and skips the write when the content is unchanged (no no-op commits).

This keeps tt-elite PRIVATE (the model/code) while publishing only today's bet list.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import requests

REPO = "fgf9p6ks2f-ux/tennis-odds-collector"
FILE = "tt_board.json"
API = f"https://api.github.com/repos/{REPO}/contents/{FILE}"


def main():
    pat = os.environ.get("PUBLIC_REPO_PAT")
    if not pat:
        print("PUBLIC_REPO_PAT not set — TT dashboard push skipped")
        return
    local = (Path(__file__).resolve().parent / FILE)
    if not local.exists():
        print("no tt_board.json to push")
        return
    content_b64 = base64.b64encode(local.read_bytes()).decode()
    h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json",
         "User-Agent": "tt-bot"}
    r = requests.get(API, headers=h, timeout=20)
    sha, remote = None, None
    if r.status_code == 200:
        j = r.json()
        sha = j.get("sha")
        remote = (j.get("content") or "").replace("\n", "")
    if remote == content_b64:                       # unchanged -> no commit
        print("TT board unchanged, skip")
        return
    body = {"message": "tt board update [skip ci]", "content": content_b64}
    if sha:
        body["sha"] = sha
    r = requests.put(API, headers=h, json=body, timeout=20)
    print("TT board pushed to dashboard" if r.status_code in (200, 201)
          else f"TT push failed {r.status_code}: {r.text[:120]}")


if __name__ == "__main__":
    main()
