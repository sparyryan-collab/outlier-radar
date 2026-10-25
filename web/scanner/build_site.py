#!/usr/bin/env python3
"""Build the hosted site folder: the dashboard page switched into web mode. The scanner adds data.json next to it.

Usage: python build_site.py [output_dir]      Optional env: BROWSER_KEY (a referrer-restricted API key), CUSTOM_DOMAIN
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site"
out.mkdir(parents=True, exist_ok=True)

html = (ROOT / "outlier-radar" / "static" / "index.html").read_text(encoding="utf-8")
cfg = {"browserKey": os.environ.get("BROWSER_KEY", "").strip()}
if cfg["browserKey"] and not re.fullmatch(r"AIza[\w-]{30,}", cfg["browserKey"]):
    print("WARNING: BROWSER_KEY does not look like a Google API key (they start with AIza and are 39 characters); live search stays off", file=sys.stderr)
    cfg["browserKey"] = ""

swaps = [
    ("window.RADAR_STATIC = false/*__STATIC__*/", "window.RADAR_STATIC = true"),
    ("window.RADAR_CFG = {}/*__CFG__*/", "window.RADAR_CFG = " + json.dumps(cfg).replace("</", "<\\/")),
    ("img-src 'self' https://i.ytimg.com data:", "img-src 'self' https://i.ytimg.com https://yt3.ggpht.com https://yt3.googleusercontent.com data:"),
    ("connect-src 'self'", "connect-src 'self' https://www.googleapis.com"),
]
for old, new in swaps:
    if old not in html:
        sys.exit(f"build_site: expected text not found in index.html: {old[:50]}")
    html = html.replace(old, new, 1)

(out / "index.html").write_text(html, encoding="utf-8")
(out / ".nojekyll").write_text("", encoding="utf-8")
domain = os.environ.get("CUSTOM_DOMAIN", "").strip()
if domain:
    (out / "CNAME").write_text(domain + "\n", encoding="utf-8")
print(f"built {out / 'index.html'}" + (f" for {domain}" if domain else ""))
