#!/usr/bin/env python3
"""Hand channels found by the PC app (yt-dlp, no quota) to the website: writes web/seed/extra.json plus their profile pictures.

Usage: python web/scanner/export_extra.py [--live https://<you>.github.io/outlier-radar/data.json] [--max 1200]
Only channels the live site does not already have are exported. The website's scanner merges the file once
(on the next run after it changes) and then keeps them fresh with the official API like every other channel.
"""
import argparse
import json
import shutil
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "outlier-radar" / "data" / "radar.db"
AVATARS = ROOT / "outlier-radar" / "data" / "avatars"
OUT = ROOT / "web" / "seed" / "extra.json"
SEED_AVATARS = ROOT / "web" / "seed" / "avatars"


def looks_like_image(b):
    return b[:3] == b"\xff\xd8\xff" or b[:8] == b"\x89PNG\r\n\x1a\n" or (b[:4] == b"RIFF" and b[8:12] == b"WEBP")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", default="https://sparyryan-collab.github.io/outlier-radar/data.json")
    ap.add_argument("--max", type=int, default=1200)
    args = ap.parse_args()
    with urllib.request.urlopen(args.live, timeout=60) as r:
        have = {c["ch_id"] for c in json.load(r)["channels"]}
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM channels WHERE score IS NOT NULL ORDER BY score DESC").fetchall()
    out, pics = [], 0
    for r in rows:
        series = json.loads(r["series"] or "[]")
        if r["ch_id"] in have or len(series) < 6:
            continue
        lang = r["lang"] or "und"
        out.append({
            "ch_id": r["ch_id"], "name": r["name"], "handle": r["handle"] or "", "subs": r["subs"] or 0, "n": r["n"],
            "med_recent": r["med_recent"], "med_all": r["med_all"], "max_views": r["max_views"], "share_100k": r["share_100k"],
            "share_1m": r["share_1m"], "trend": r["trend"], "peak_vs_med": r["peak_vs_med"], "ratio": r["ratio"], "score": r["score"],
            "tags": json.loads(r["tags"] or "[]"), "lang": lang, "english": lang in ("en", "und"), "queries": json.loads(r["queries"] or "[]"),
            "series": series, "last_probe": r["last_probe"], "descr": r["descr"] or "", "avatar_url": r["avatar"] or "",
            "hit_views": r["hit_views"] or 0, **({"found": int(r["first_seen"])} if r["first_seen"] else {}),
        })
        src = AVATARS / f"{r['ch_id']}.img"
        dst = SEED_AVATARS / f"{r['ch_id']}.jpg"
        if src.exists() and not dst.exists() and looks_like_image(src.read_bytes()[:16]):
            shutil.copyfile(src, dst)
            pics += 1
        if len(out) >= args.max:
            break
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    thai = sum(1 for c in out if c["lang"] == "th")
    print(f"exported {len(out)} new channels ({thai} Thai, {sum(1 for c in out if c['score'] >= 50)} scoring 50+) to {OUT}; copied {pics} profile pictures")


if __name__ == "__main__":
    main()
