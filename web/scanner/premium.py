#!/usr/bin/env python3
"""Viral Radar Pro, server side: uploads the full trend feeds to the server worker (web/pay/worker.js), and only when Pro is paid
(PRO_GATE on) trims the public trends.json to the free previews, so premium lists can't be read from the public file.

Usage: python premium.py _site/trends.json
Env:   API_URL (the worker's address), UPLOAD_TOKEN (a secret shared with the worker), PRO_GATE
Does nothing unless API_URL and UPLOAD_TOKEN are both set, and never trims the public file unless the upload succeeded.

What this protects: everything that lives only in trends.json (YouTube's charts, the news lists, clip creators and their best
moments, creators who invite clippers). Lists the page works out from the public channel data (rising words, the games and
cartoons boards, the creator tools) stay readable by anyone who downloads data.json; see web/README.md.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

FREE = {"chart": 5, "wiki": 6, "clips": 3, "moments": 1, "allowed": 3}   # a little more than the page shows before its lock card


def trim(t):
    out = dict(t)
    regions = ((t.get("yt") or {}).get("regions") or {})
    out["yt"] = dict(t.get("yt") or {}, regions={r: {cat: items[:FREE["chart"]] for cat, items in cats.items()} for r, cats in regions.items()})
    out["wiki"] = {k: (dict(v, items=v.get("items", [])[:FREE["wiki"]]) if isinstance(v, dict) and "items" in v else v) for k, v in (t.get("wiki") or {}).items()}
    researched = [c for c in t.get("clips") or [] if not c.get("small")][:FREE["clips"]]
    out["clips"] = [dict(c, moments=(c.get("moments") or [])[:FREE["moments"]]) for c in researched]
    out["allowed"] = (t.get("allowed") or [])[:FREE["allowed"]]
    out["trimmed"] = True
    return out


def main():
    path = Path(sys.argv[1])
    api, token = os.environ.get("API_URL", "").strip().rstrip("/"), os.environ.get("UPLOAD_TOKEN", "").strip()
    gate = os.environ.get("PRO_GATE", "").strip().lower() in ("1", "true", "yes", "on")
    if not api or not token:
        print("premium: the server worker isn't set up (API_URL and UPLOAD_TOKEN); nothing to do")
        return
    raw = path.read_bytes()
    req = urllib.request.Request(f"{api}/admin/put/trends-full.json", data=raw, method="PUT",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "viral-radar-workflow"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            ok = 200 <= r.status < 300
    except Exception as e:
        print(f"premium: upload failed ({type(e).__name__}); the public trends file is left whole")
        return
    if not ok:
        print("premium: upload refused; the public trends file is left whole")
        return
    if gate:
        path.write_text(json.dumps(trim(json.loads(raw)), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print("premium: full feeds uploaded for Pro members; public trends.json trimmed to the free previews")
    else:
        print("premium: full feeds uploaded (Pro isn't paid yet, so the public file stays whole)")


if __name__ == "__main__":
    main()
