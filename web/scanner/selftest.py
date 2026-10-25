#!/usr/bin/env python3
"""End-to-end check of scan.py against a fake YouTube Data API (no key, no network, no quota).

Run:  python web/scanner/selftest.py
It starts a local fake API, runs the scanner twice (the second run simulates 4 hours later with more views and a new upload),
and checks scoring, quota accounting, growth rates and new-upload detection.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOW = dt.datetime.now(dt.timezone.utc)


def cid(n):
    return "UC" + f"{n:022d}"


def vid(c, i):
    return f"v{c}x{i:04d}".ljust(11, "_")


WORLD = {"extra": False, "bump": 0}
CHANNELS = {cid(1): {"name": "Small Hit Channel", "subs": 20000, "base": 400000}, cid(2): {"name": "Big Quiet Channel", "subs": 5000000, "base": 30000}}


def channel_videos(n):
    """Newest first. Videos are identified by a serial number (0 = oldest); serial 14 is the new upload that appears in the second run."""
    c = CHANNELS[cid(n)]
    total = 15 if (WORLD["extra"] and n == 1) else 14
    vids = []
    for rank in range(total):
        serial = total - 1 - rank
        age_hours = 1 if serial == 14 else 2 + 20 * (13 - serial)
        views = int(c["base"] * (0.05 if serial == 14 else 1.4 - 0.05 * (13 - serial))) + (WORLD["bump"] if rank < 3 else 0)
        vids.append({"id": vid(n, serial), "views": views, "dur": "PT12M" if serial == 5 else "PT45S", "pub": NOW - dt.timedelta(hours=age_hours), "n": n})
    return vids


def all_videos():
    return {v["id"]: v for n in (1, 2) for v in channel_videos(n)}


def search_ids():
    a, b = channel_videos(1), channel_videos(2)
    mixed = [v for pair in zip(a, b) for v in pair]
    return [v["id"] for v in mixed if v["dur"] == "PT45S"][:10]


class Fake(BaseHTTPRequestHandler):
    calls = {"search": 0, "videos": 0, "channels": 0, "playlistItems": 0}

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path.rsplit("/", 1)[-1]
        Fake.calls[path] = Fake.calls.get(path, 0) + 1
        if q.get("key") != ["testkey"]:
            return self.reply(403, {"error": {"message": "bad key", "errors": [{"reason": "keyInvalid"}]}})
        if path == "search":
            return self.reply(200, {"items": [{"id": {"videoId": i}} for i in search_ids()]})
        if path == "videos":
            vs = all_videos()
            items = []
            for i in q["id"][0].split(","):
                if i in vs:
                    v = vs[i]
                    items.append({"id": i, "snippet": {"title": f"Video {i} 🐱 what if", "channelId": cid(v["n"]), "channelTitle": CHANNELS[cid(v["n"])]["name"],
                                                       "publishedAt": v["pub"].strftime("%Y-%m-%dT%H:%M:%SZ")},
                                  "statistics": {"viewCount": str(v["views"])}, "contentDetails": {"duration": v["dur"]}})
            return self.reply(200, {"items": items})
        if path == "channels":
            items = []
            for c in q["id"][0].split(","):
                if c in CHANNELS:
                    items.append({"id": c, "snippet": {"title": CHANNELS[c]["name"], "customUrl": "@" + CHANNELS[c]["name"].replace(" ", "").lower(),
                                                       "description": "A test channel about cats", "thumbnails": {"medium": {"url": "https://yt3.ggpht.com/test=s240"}}},
                                  "statistics": {"subscriberCount": str(CHANNELS[c]["subs"])}})
            return self.reply(200, {"items": items})
        if path == "channelSections":   # channel 1 features channel 2 (already known, so nothing new is added)
            ch = q["channelId"][0]
            return self.reply(200, {"items": [{"snippet": {"type": "multiplechannels"}, "contentDetails": {"channels": [cid(2)]}}] if ch == cid(1) else []})
        if path == "playlistItems":
            n = int(q["playlistId"][0][2:])
            if cid(n) not in CHANNELS:
                return self.reply(404, {"error": {"message": "playlist not found"}})
            return self.reply(200, {"items": [{"contentDetails": {"videoId": v["id"]}} for v in channel_videos(n)]})
        self.reply(404, {"error": {"message": "unknown"}})

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_scan(tmp, offset, prev=None, port=0):
    env = dict(os.environ, YT_API_BASE=f"http://127.0.0.1:{port}", YT_API_KEY="testkey", SEARCH_EVERY="1", PROBES_PER_RUN="6",
               SCAN_TIME_OFFSET=str(offset), PYTHONIOENCODING="utf-8")
    out = Path(tmp) / f"out{offset}.json"
    seed = Path(tmp) / "seed.json"
    if not seed.exists():
        seed.write_text(json.dumps({"channels": [], "_state": {}}), encoding="utf-8")
    queries = Path(tmp) / "queries.json"
    queries.write_text(json.dumps([{"q": "cats what if"}]), encoding="utf-8")
    picks = Path(tmp) / "picks.json"
    picks.write_text("[]", encoding="utf-8")
    extra = Path(tmp) / "extra.json"
    if not extra.exists():   # one channel scored by the PC app, merged once
        extra.write_text(json.dumps([{"ch_id": cid(9), "name": "PC Found Channel", "subs": 1000, "score": 91.0, "tags": [], "queries": ["cats"], "lang": "en",
                                      "english": True, "series": [{"id": vid(9, i), "t": "x", "v": 500000} for i in range(8)],
                                      "last_probe": NOW.timestamp(), "med_recent": 500000}]), encoding="utf-8")
    cmd = [sys.executable, str(HERE / "scan.py"), "--seed", str(seed), "--queries", str(queries), "--picks", str(picks), "--out", str(out), "--extra", str(extra)]
    if prev:
        cmd += ["--prev-url", Path(prev).resolve().as_uri()]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", cwd=tmp)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit("scan.py failed")
    return json.loads(out.read_text(encoding="utf-8")), out


def main():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as tmp:
        d1, out1 = run_scan(tmp, 0, port=port)
        ch = {c["name"]: c for c in d1["channels"] if c["name"] != "PC Found Channel"}
        assert len(ch) == 2, f"expected 2 scored channels, got {list(ch)}"
        hit, quiet = ch["Small Hit Channel"], ch["Big Quiet Channel"]
        assert hit["score"] > quiet["score"] + 20, (hit["score"], quiet["score"])   # small channel with huge views must outrank the big quiet one
        assert all(s["id"] != vid(1, 5) for s in hit["series"]), "12-minute video must be filtered out (not a Short)"
        assert hit["avatar_url"].startswith("https://yt3.ggpht.com/"), hit["avatar_url"]
        assert all(s.get("d") == 45 for s in hit["series"]), "each Short keeps its length in seconds"
        assert hit["lang"] in ("en", "und") and hit["english"], hit["lang"]
        used1 = d1["_state"]["quota_used"]
        expect = 0 + 1 + 1 + 2 * 2 + 2  # search (own bucket) + videos(search) + channels + (playlistItems+videos) per channel + featured lookups for the 2 top channels
        assert used1 == expect, f"quota accounting: used {used1}, expected {expect} ({Fake.calls})"
        assert d1["status"]["quota"]["used_today"] == used1
        assert d1["_state"]["searches_used"] == 1 and d1["status"]["quota"]["searches_today"] == 1, d1["status"]["quota"]
        assert "PC Found Channel" in [c["name"] for c in d1["channels"]] and d1["_state"]["extra_hash"], "PC-found channels are merged"
        assert cid(1) in d1["_state"]["featured"], "featured channels of a top channel were looked up"
        print(f"run 1 OK: scored {len(ch)} channels, hit={hit['score']} quiet={quiet['score']}, quota {used1} units")

        WORLD.update(extra=True, bump=5000)               # more views, plus one new upload on channel 1
        d2, out2 = run_scan(tmp, 4 * 3600, prev=out1, port=port)
        ch2 = {c["name"]: c for c in d2["channels"]}
        hit2 = ch2["Small Hit Channel"]
        grown = [s for s in hit2["series"] if "g" in s and s["g"] > 0]
        assert len(grown) == 2 and all(s["g"] == 5000 for s in grown), [(s["id"], s.get("g")) for s in hit2["series"][:4]]   # the two old videos that gained views
        assert all(abs(s["h"] - 4.0) < 0.2 for s in grown), [s["h"] for s in grown]                                           # growth measured over the 4 hours between scans
        newest = hit2["series"][0]
        assert newest["id"] == vid(1, 14) and "g" not in newest, newest                                                       # the new upload is first, with no growth baseline yet
        assert newest["p"] > NOW.timestamp() - 2 * 3600, "publish time of the new upload should be kept"
        assert d2["_state"]["runs"] == 2 and d2["_state"]["quota_used"] > 0
        assert len(hit2["hist"]) == 2 and hit2["hist"][0][0] < hit2["hist"][1][0], hit2["hist"]                              # one growth point per check
        assert hit2["found"] == hit["found"] and abs(hit["found"] - NOW.timestamp()) < 120, (hit.get("found"), hit2.get("found"))   # first-seen time is kept
        print(f"run 2 OK: {len(grown)} videos grew by 5000 over 4.0h, new upload detected ({int((NOW.timestamp() - newest['p'])/60)} min old), quota today {d2['_state']['quota_used']}")

        # a wrong key must not crash the whole pipeline: data.json is still written
        env = dict(os.environ, YT_API_BASE=f"http://127.0.0.1:{port}", YT_API_KEY="wrong", SEARCH_EVERY="1", SCAN_TIME_OFFSET=str(9 * 3600), PYTHONIOENCODING="utf-8")
        out3 = Path(tmp) / "out3.json"
        r = subprocess.run([sys.executable, str(HERE / "scan.py"), "--seed", str(Path(tmp) / "seed.json"), "--queries", str(Path(tmp) / "queries.json"),
                            "--picks", str(Path(tmp) / "picks.json"), "--out", str(out3), "--prev-url", out2.resolve().as_uri()],
                           env=env, capture_output=True, text=True, encoding="utf-8", cwd=tmp)
        assert out3.exists() and r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert "wrong" not in (r.stdout + r.stderr), "the API key must never be printed"
        assert len(json.loads(out3.read_text(encoding="utf-8"))["channels"]) == 3
        print("bad-key run OK: data kept, non-zero exit, key not leaked")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
