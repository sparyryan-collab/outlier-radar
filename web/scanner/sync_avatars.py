#!/usr/bin/env python3
"""Download missing channel profile pictures next to the site and mark which channels have one.

Usage: python sync_avatars.py <data.json> <avatars_dir> [max_downloads_per_run]
Serving the pictures from the site itself avoids hot-linking Google's image server on every page view (it rate-limits).
Downloads are slow and capped per run, and stop at the first HTTP 429.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HOST = re.compile(r"https://(yt3\.googleusercontent\.com|yt3\.ggpht\.com)/")
CHANNEL = re.compile(r"UC[\w-]{22}")


def looks_like_image(b):
    return b[:3] == b"\xff\xd8\xff" or b[:8] == b"\x89PNG\r\n\x1a\n" or (b[:4] == b"RIFF" and b[8:12] == b"WEBP") or b[:3] == b"GIF"


def main():
    data_path, av_dir = Path(sys.argv[1]), Path(sys.argv[2])
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    data = json.loads(data_path.read_text(encoding="utf-8"))
    av_dir.mkdir(parents=True, exist_ok=True)
    got = rate = 0
    for ch in data.get("channels", []):
        cid, url = ch.get("ch_id", ""), ch.get("avatar_url", "")
        f = av_dir / f"{cid}.jpg" if CHANNEL.fullmatch(cid) else None
        if f and not f.exists() and HOST.match(url) and got < limit and not rate:
            base = re.sub(r"=[^=/?#]*$", "", url)
            for size in ("240", "176"):
                try:
                    req = urllib.request.Request(f"{base}=s{size}-c-k-c0x00ffffff-no-rj", headers={"User-Agent": UA, "Accept": "image/*"})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        blob = r.read(400_000)
                    if looks_like_image(blob):
                        f.write_bytes(blob)
                        got += 1
                        break
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        rate = 1
                        break
                except Exception:
                    pass
            time.sleep(0.4)
        ch["avatar_ok"] = bool(f and f.exists())
    data_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    have = sum(1 for c in data.get("channels", []) if c.get("avatar_ok"))
    print(f"profile pictures: {have}/{len(data.get('channels', []))} available, {got} downloaded this run" + (" (stopped early: rate limited)" if rate else ""))


if __name__ == "__main__":
    main()
