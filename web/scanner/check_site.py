#!/usr/bin/env python3
"""The last check before publishing: the files the site needs exist, parse and are fresh.

Usage: python check_site.py _site
A problem stops the workflow before the deploy step, so the live site keeps its last good version instead of a broken one.
"""
import json
import os
import sys
import time
from pathlib import Path


def main():
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "_site")
    now = time.time() + float(os.environ.get("SCAN_TIME_OFFSET", "0"))
    problems = []

    def load(name):
        try:
            return json.loads((site / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            problems.append(f"{name} is missing")
        except Exception as e:
            problems.append(f"{name} could not be read ({type(e).__name__})")
        return None

    for name in ("index.html", "manifest.webmanifest", "sw.js"):
        if not (site / name).exists():
            problems.append(f"{name} is missing")
    data = load("data.json")
    if data is not None:
        if not isinstance(data.get("channels"), list) or not data["channels"]:
            problems.append("data.json has no channels")
        if now - (data.get("generated") or 0) > 6 * 3600:
            problems.append("data.json was not written by this run")
        if "_state" in data:
            problems.append("data.json still carries the scanner's private state")
        if any(c.get("src") != "api" and c.get("series") and not any(s.get("p") for s in c["series"]) for c in data.get("channels", [])):
            problems.append("data.json contains records that were never refreshed through the YouTube API")
    trends = load("trends.json")
    if trends is not None and not isinstance(trends, dict):
        problems.append("trends.json is not an object")
    lite, version = load("data-lite.json"), load("version.json")
    if data is not None and version is not None and version.get("generated") != data.get("generated"):
        problems.append("version.json does not match data.json")
    if problems:
        print("NOT PUBLISHING:\n  " + "\n  ".join(problems), file=sys.stderr)
        sys.exit(1)
    print(f"site OK: {len(data['channels'])} channels, data from {time.strftime('%H:%M UTC', time.gmtime(data['generated']))}")


if __name__ == "__main__":
    main()
