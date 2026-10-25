#!/usr/bin/env python3
"""Build the hosted site folder: the dashboard page switched into web mode, plus the installable-app files. The scanner adds data.json.

Usage: python build_site.py [output_dir]
Optional env: BROWSER_KEY (a referrer-restricted API key), CUSTOM_DOMAIN,
              FIREBASE_API_KEY + FIREBASE_PROJECT_ID (+ FIREBASE_AUTH_DOMAIN) to switch on Google sign-in and sync
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "outlier-radar" / "static"
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site"
out.mkdir(parents=True, exist_ok=True)
KEY = re.compile(r"AIza[\w-]{30,}")


def find_key(text):
    """The API key inside whatever was pasted (a bare key, or a line like apiKey: \"AIza...\",)."""
    m = re.search(r"AIza[0-9A-Za-z_-]{35}", text or "")
    return m.group(0) if m else (text or "").strip()

html = (STATIC / "index.html").read_text(encoding="utf-8")
cfg = {"browserKey": find_key(os.environ.get("BROWSER_KEY", ""))}
if cfg["browserKey"] and not KEY.fullmatch(cfg["browserKey"]):
    print("WARNING: BROWSER_KEY does not look like a Google API key (they start with AIza and are 39 characters); live search stays off", file=sys.stderr)
    cfg["browserKey"] = ""
fb_key, fb_project = find_key(os.environ.get("FIREBASE_API_KEY", "")), os.environ.get("FIREBASE_PROJECT_ID", "").strip()
if fb_key or fb_project:
    if KEY.fullmatch(fb_key) and re.fullmatch(r"[a-z0-9-]{4,40}", fb_project):
        cfg["firebase"] = {"apiKey": fb_key, "projectId": fb_project,
                           "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", "").strip() or f"{fb_project}.firebaseapp.com"}
    else:
        print("WARNING: FIREBASE_API_KEY / FIREBASE_PROJECT_ID look wrong (the key starts with AIza; the project id is lowercase letters, digits and dashes); sign-in stays off", file=sys.stderr)

swaps = [
    ("window.RADAR_STATIC = false/*__STATIC__*/", "window.RADAR_STATIC = true"),
    ("window.RADAR_CFG = {}/*__CFG__*/", "window.RADAR_CFG = " + json.dumps(cfg).replace("</", "<\\/")),
    ("img-src 'self' https://i.ytimg.com data:", "img-src 'self' https://i.ytimg.com https://yt3.ggpht.com https://yt3.googleusercontent.com data:"),
    ("connect-src 'self'", "connect-src 'self' https://www.googleapis.com"),
    ("<title>Viral Radar</title>", '<title>Viral Radar</title>\n<link rel="manifest" href="manifest.webmanifest">\n<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">\n'
                                   '<meta name="theme-color" content="#ff0033">\n<meta name="apple-mobile-web-app-capable" content="yes">\n<meta name="apple-mobile-web-app-title" content="Viral Radar">'),
]
if "firebase" in cfg:   # Google sign-in (Firebase Auth) and sync (Cloud Firestore) need these hosts
    auth_host = cfg["firebase"]["authDomain"]
    swaps += [
        ("script-src 'unsafe-inline'", "script-src 'unsafe-inline' https://www.gstatic.com https://apis.google.com"),
        ("https://yt3.googleusercontent.com data:", "https://yt3.googleusercontent.com https://lh3.googleusercontent.com data:"),
        ("connect-src 'self' https://www.googleapis.com", "connect-src 'self' https://www.googleapis.com https://firestore.googleapis.com https://identitytoolkit.googleapis.com https://securetoken.googleapis.com"),
        ("frame-src https://www.youtube-nocookie.com", f"frame-src https://www.youtube-nocookie.com https://{auth_host} https://accounts.google.com"),
    ]
for old, new in swaps:
    if old not in html:
        sys.exit(f"build_site: expected text not found in index.html: {old[:50]}")
    html = html.replace(old, new, 1)

(out / "index.html").write_text(html, encoding="utf-8")
for name in ("manifest.webmanifest", "sw.js"):   # the installable app
    shutil.copyfile(STATIC / name, out / name)
shutil.copytree(STATIC / "icons", out / "icons", dirs_exist_ok=True)
(out / ".nojekyll").write_text("", encoding="utf-8")
domain = os.environ.get("CUSTOM_DOMAIN", "").strip()
if domain:
    (out / "CNAME").write_text(domain + "\n", encoding="utf-8")
print(f"built {out / 'index.html'}" + (f" for {domain}" if domain else "") + (" with Google sign-in" if "firebase" in cfg else ""))
