#!/usr/bin/env python3
"""Download channel profile pictures next to the site, refresh them within a month, and mark which channels have one.

Usage: python sync_avatars.py <data.json> <avatars_dir> [max_downloads_per_run]
Serving the pictures from the site itself avoids hot-linking Google's image server on every page view (it rate-limits).
Missing pictures are fetched first, then the oldest ones are fetched again (each at most REFRESH_DAYS old); pictures of
channels that left the list are deleted. Downloads are slow and capped per run, a picture that failed is tried again a day
later rather than on every run, and everything stops at the first HTTP 429.
The script names itself honestly in its User-Agent. It also rewrites data-lite.json with the profile-picture flags, and
tells the workflow (changed=true) when the folder changed and is worth saving for the next run.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "ViralRadar/1.0 (+https://github.com/sparyryan-collab/outlier-radar)"
HOST = re.compile(r"https://(yt3\.googleusercontent\.com|yt3\.ggpht\.com)/")
CHANNEL = re.compile(r"UC[\w-]{22}")
REFRESH_DAYS = 25
RETRY_HOURS = 24


def looks_like_image(b):
    return b[:3] == b"\xff\xd8\xff" or b[:8] == b"\x89PNG\r\n\x1a\n" or (b[:4] == b"RIFF" and b[8:12] == b"WEBP") or b[:3] == b"GIF"


def fetch(url):
    base = re.sub(r"=[^=/?#]*$", "", url)
    for size in ("240", "176"):
        req = urllib.request.Request(f"{base}=s{size}-c-k-c0x00ffffff-no-rj", headers={"User-Agent": UA, "Accept": "image/*"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                blob = r.read(400_000)
            if looks_like_image(blob):
                return blob
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise
        except Exception:
            pass
    return None


def write_lite(data, path):
    """The hottest channels only, for a fast first screen (the same shape scan.py writes)."""
    lite = {k: v for k, v in data.items() if k not in ("channels", "_state", "queries")}
    lite["channels"] = [{k: v for k, v in c.items() if k not in ("hist", "descr")} for c in sorted(data.get("channels", []), key=lambda c: -(c.get("score") or 0))[:300]]
    path.write_text(json.dumps(lite, separators=(",", ":")), encoding="utf-8")


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    data_path, av_dir = Path(sys.argv[1]), Path(sys.argv[2])
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    data = json.loads(data_path.read_text(encoding="utf-8"))
    av_dir.mkdir(parents=True, exist_ok=True)
    index_path, failed_path = av_dir / "_index.json", av_dir / "_failed.json"   # when each picture was downloaded / last failed
    index, failed = read_json(index_path), read_json(failed_path)
    now = time.time()
    chans = {c["ch_id"]: c for c in data.get("channels", []) if CHANNEL.fullmatch(c.get("ch_id", ""))}
    removed = 0
    for f in av_dir.glob("UC*.jpg"):   # channels that left the list
        if f.stem not in chans:
            f.unlink()
            index.pop(f.stem, None)
            removed += 1
    failed = {k: v for k, v in failed.items() if k in chans and now - v < RETRY_HOURS * 3600}
    missing = [c for cid, c in chans.items() if not (av_dir / f"{cid}.jpg").exists() and cid not in failed]
    stale = sorted((c for cid, c in chans.items() if (av_dir / f"{cid}.jpg").exists() and now - index.get(cid, 0) > REFRESH_DAYS * 86400
                    and cid not in failed), key=lambda c: index.get(c["ch_id"], 0))
    got = tried = rate = 0
    for c in missing + stale:
        if got >= limit or tried >= 2 * limit:
            break
        url = c.get("avatar_url", "")
        if not HOST.match(url):
            continue
        tried += 1
        try:
            blob = fetch(url)
        except urllib.error.HTTPError:
            rate = 1
            break
        if blob:
            (av_dir / f"{c['ch_id']}.jpg").write_bytes(blob)
            index[c["ch_id"]] = int(now)
            got += 1
        else:
            failed[c["ch_id"]] = int(now)
        time.sleep(0.4)
    index = {k: v for k, v in index.items() if k in chans}
    index_path.write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    failed_path.write_text(json.dumps(failed, separators=(",", ":")), encoding="utf-8")
    for cid, c in chans.items():
        c["avatar_ok"] = (av_dir / f"{cid}.jpg").exists()
    data_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    write_lite(data, data_path.parent / "data-lite.json")
    have = sum(1 for c in chans.values() if c.get("avatar_ok"))
    print(f"profile pictures: {have}/{len(chans)} available, {got} downloaded this run ({tried - got} failed, tried again in a day), "
          f"{removed} removed" + (" (stopped early: rate limited)" if rate else ""))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if got or removed else 'false'}\n")


if __name__ == "__main__":
    main()
