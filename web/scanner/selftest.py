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
CHANNELS = {cid(1): {"name": "Small Hit Channel", "subs": 20000, "base": 400000}, cid(2): {"name": "Big Quiet Channel", "subs": 5000000, "base": 30000},
            cid(3): {"name": "Clip Friendly Streamer", "subs": 2000000, "base": 2000000,
                     "desc": "Streams every night." + chr(10) + "Feel free to clip my streams! Paid clipping program on whop.com/streamer-clips"}}
# viewers' comments on channel 3's long video: three point at the big fail (2:15), a timestamp list, a comment with no time, two at the ending
COMMENTS = [("2:15 I can't stop laughing at the big fail", 340), ("2:20 the fail lmao", 120), ("2:18 💀💀💀", 50), ("0:00 1:00 2:00 3:00 4:00 5:00", 5),
            ("great video", 900), ("7:30 the Q and A ending got me", 10), ("7:41 wait what", 3)]
CHAPTERS = "Streaming highlights" + chr(10) + "0:00 Intro" + chr(10) + "2:10 The big fail" + chr(10) + "6:00 Questions and answers"


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
    return {v["id"]: v for n in (1, 2, 3) for v in channel_videos(n)}


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
        if u.path.endswith("/documents/track"):   # Firestore: channels visitors asked to track (public list)
            return self.reply(200, {"documents": [{"name": f"projects/demo/databases/(default)/documents/track/{cid(2)}"}]})
        if u.path.startswith("/wiki/"):   # Wikimedia's most-read list for a day (no key)
            day = int(u.path.rstrip("/").rsplit("/", 1)[-1])
            arts = [{"article": "Main_Page", "views": 900000}, {"article": "Big_Event", "views": 5000 * day}, {"article": "Steady_Page", "views": 3000}]
            return self.reply(200, {"items": [{"articles": arts}]})
        if q.get("key") != ["testkey"]:
            return self.reply(403, {"error": {"message": "bad key", "errors": [{"reason": "keyInvalid"}]}})
        if path == "search":
            if q.get("type") == ["channel"]:   # a search for creators with a clipping program
                return self.reply(200, {"items": [{"id": {"kind": "youtube#channel", "channelId": c}} for c in (cid(3), cid(1))]})
            return self.reply(200, {"items": [{"id": {"videoId": i}} for i in search_ids()]})
        if path == "commentThreads":   # channel 3's long video has timestamp comments; channel 2 turned comments off
            if q["videoId"][0] == vid(2, 5):
                return self.reply(403, {"error": {"message": "The video identified by the videoId parameter has disabled comments.", "errors": [{"reason": "commentsDisabled"}]}})
            rows = COMMENTS if q["videoId"][0] == vid(3, 5) else []
            return self.reply(200, {"items": [{"snippet": {"topLevelComment": {"snippet": {"textOriginal": c, "likeCount": n}}}} for c, n in rows]})
        if path == "videos" and q.get("chart"):   # YouTube's trending chart; one category has no chart in this country
            if q.get("videoCategoryId") == ["28"]:
                return self.reply(404, {"error": {"message": "The requested video chart is not supported or is not available."}})
            if WORLD.get("longchart"):   # a big creator's long video on the chart, plus a record label that must be skipped
                if q.get("videoCategoryId", [""])[0] in ("10", "25", "17", "1"):   # music, news, sports and film charts don't list them
                    return self.reply(200, {"items": []})
                return self.reply(200, {"items": [
                    {"id": "longvideo01", "snippet": {"title": "Big Quiet podcast", "channelId": cid(2), "channelTitle": "Big Quiet Channel", "publishedAt": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")},
                     "statistics": {"viewCount": "2500000"}, "contentDetails": {"duration": "PT45M"}},
                    {"id": "longvideo02", "snippet": {"title": "New single", "channelId": cid(7), "channelTitle": "Some Records", "publishedAt": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")},
                     "statistics": {"viewCount": "9000000"}, "contentDetails": {"duration": "PT4M30S"}}]})
            order = ["tA", "tB", "tC"] if not WORLD["extra"] else ["tC", "tA", "tD"]
            return self.reply(200, {"items": [{"id": (i + "_" * 11)[:11], "snippet": {"title": f"Trending {i}", "channelId": cid(9), "channelTitle": "Chart Channel",
                                                                                      "publishedAt": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")},
                                               "statistics": {"viewCount": "1000"}, "contentDetails": {"duration": "PT40S"}} for i in order]})
        if path == "videos":
            vs = all_videos()
            items = []
            for i in q["id"][0].split(","):
                if i in vs:
                    v = vs[i]
                    title = "Big fail had him laughing 😂 what if" if i == vid(1, 13) else f"Video {i} 🐱 what if"
                    items.append({"id": i, "snippet": {"title": title, "channelId": cid(v["n"]), "channelTitle": CHANNELS[cid(v["n"])]["name"],
                                                       "publishedAt": v["pub"].strftime("%Y-%m-%dT%H:%M:%SZ"), "description": CHAPTERS if i == vid(3, 5) else ""},
                                  "statistics": {"viewCount": str(v["views"]), "likeCount": str(v["views"] // 40), "commentCount": str(v["views"] // 2000)},
                                  "contentDetails": {"duration": v["dur"]}})
            return self.reply(200, {"items": items})
        if path == "channels":
            items = []
            for c in q["id"][0].split(","):
                if c in CHANNELS:
                    items.append({"id": c, "snippet": {"title": CHANNELS[c]["name"], "customUrl": "@" + CHANNELS[c]["name"].replace(" ", "").lower(),
                                                       "description": CHANNELS[c].get("desc", "A test channel about cats"), "thumbnails": {"medium": {"url": "https://yt3.ggpht.com/test=s240"}},
                                                       "publishedAt": "2026-08-01T00:00:00Z"},
                                  "statistics": {"subscriberCount": str(CHANNELS[c]["subs"])}, "contentDetails": {"relatedPlaylists": {"uploads": "UU" + c[2:]}}})
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


def data_clip_ok(trends_json):
    return all("allow" in c for c in trends_json["clips"] if not c.get("small"))


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
        assert all("c" in s and "l" in s for s in hit["series"]), "comments and likes are kept for the comment signal"
        assert hit["created"] == 1785542400, hit.get("created")   # channel creation date (2026-08-01)
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
        assert newest["vh"][0][1] == 0 and newest["vh"][-1][1] == newest["v"], newest.get("vh")   # growth curve starts at the upload with 0 views
        older = next(s for s in hit2["series"] if s.get("g") and len(s.get("vh") or []) == 3)   # upload, previous check, this check
        assert older["vh"][0][1] == 0 and older["vh"][1][1] < older["vh"][2][1] == older["v"], older["vh"]
        assert d2["_state"]["runs"] == 2 and d2["_state"]["quota_used"] > 0
        assert len(hit2["hist"]) == 2 and hit2["hist"][0][0] < hit2["hist"][1][0], hit2["hist"]                              # one growth point per check
        assert hit2["found"] == hit["found"] and abs(hit["found"] - NOW.timestamp()) < 120, (hit.get("found"), hit2.get("found"))   # first-seen time is kept
        print(f"run 2 OK: {len(grown)} videos grew by 5000 over 4.0h, new upload detected ({int((NOW.timestamp() - newest['p'])/60)} min old), quota today {d2['_state']['quota_used']}")

        # a wrong key must not crash the whole pipeline: data.json is still written
        env = dict(os.environ, YT_API_BASE=f"http://127.0.0.1:{port}", YT_API_KEY="wrong", SEARCH_EVERY="1", SCAN_TIME_OFFSET=str(9 * 3600), PYTHONIOENCODING="utf-8")
        out3 = Path(tmp) / "out3.json"
        r = subprocess.run([sys.executable, str(HERE / "scan.py"), "--seed", str(Path(tmp) / "seed.json"), "--queries", str(Path(tmp) / "queries.json"),
                            "--picks", str(Path(tmp) / "picks.json"), "--out", str(out3), "--prev-url", out2.resolve().as_uri(),
                            "--extra", str(Path(tmp) / "extra.json")],
                           env=env, capture_output=True, text=True, encoding="utf-8", cwd=tmp)
        assert out3.exists() and r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert "wrong" not in (r.stdout + r.stderr), "the API key must never be printed"
        assert len(json.loads(out3.read_text(encoding="utf-8"))["channels"]) == 3
        print("bad-key run OK: data kept, non-zero exit, key not leaked")

        # a channel a visitor asked to track stays in the data even when it ranks too low to be kept
        env = dict(os.environ, YT_API_BASE=f"http://127.0.0.1:{port}", YT_API_KEY="testkey", SEARCH_EVERY="99", KEEP_CHANNELS="1", SCAN_TIME_OFFSET=str(10 * 3600),
                   FIRESTORE_BASE=f"http://127.0.0.1:{port}/v1", FIREBASE_PROJECT_ID="demo", FIREBASE_API_KEY="fbkey", PYTHONIOENCODING="utf-8")
        out4 = Path(tmp) / "out4.json"
        r = subprocess.run([sys.executable, str(HERE / "scan.py"), "--seed", str(Path(tmp) / "seed.json"), "--queries", str(Path(tmp) / "queries.json"),
                            "--picks", str(Path(tmp) / "picks.json"), "--out", str(out4), "--prev-url", out2.resolve().as_uri(), "--extra", str(Path(tmp) / "extra.json")],
                           env=env, capture_output=True, text=True, encoding="utf-8", cwd=tmp)
        d4 = json.loads(out4.read_text(encoding="utf-8"))
        names4 = [c["name"] for c in d4["channels"]]
        assert len(names4) == 2 and "Big Quiet Channel" in names4, names4   # the top channel, plus the tracked one
        assert cid(2) in d4["_state"]["tracked"], d4["_state"].get("tracked")
        print(f"tracked channels OK: kept {len(names4)} with KEEP_CHANNELS=1 (the tracked one is pinned)")

        # trend feeds: YouTube charts by country and category, Wikipedia's most-read list, history between runs
        def run_trends(key, prev=None, offset=0, data=None, disc="0"):
            out = Path(tmp) / f"trends{offset}{key}.json"
            env = dict(os.environ, YT_API_BASE=f"http://127.0.0.1:{port}", WIKI_BASE=f"http://127.0.0.1:{port}/wiki", YT_API_KEY=key, DISCOVER_PER_DAY=disc,
                       TRENDS_YT_EVERY="0", SCAN_TIME_OFFSET=str(offset), PREV_TRENDS_URL=prev.resolve().as_uri() if prev else "", PYTHONIOENCODING="utf-8")
            r = subprocess.run([sys.executable, str(HERE / "trends.py"), "--out", str(out)] + (["--data", str(data)] if data else []), env=env, capture_output=True, text=True, encoding="utf-8", cwd=tmp)
            assert r.returncode == 0 and out.exists(), (r.returncode, r.stdout, r.stderr)
            assert key not in (r.stdout + r.stderr), "the API key must never be printed"
            return json.loads(out.read_text(encoding="utf-8")), out
        WORLD.update(extra=False)
        t1, f1 = run_trends("testkey")
        th = t1["yt"]["regions"]["TH"]
        assert set(t1["yt"]["regions"]) == {"TH", "US"} and "tech" not in th and len(th["all"]) == 3, list(th)
        assert all(x["pr"] is None and x["f"] and x["d"] == 40 for x in th["all"]), th["all"][0]
        wiki = t1["wiki"]["en"]["items"]
        assert [w["a"] for w in wiki] == ["Big_Event", "Steady_Page"] and wiki[0]["pv"] and wiki[0]["v"] > wiki[0]["pv"], wiki   # Main_Page dropped; yesterday vs the day before
        WORLD.update(extra=True)
        t2, f2 = run_trends("testkey", prev=f1, offset=3600)
        ranks = {x["id"][:2]: (x["r"], x["pr"]) for x in t2["yt"]["regions"]["US"]["all"]}
        assert ranks == {"tC": (1, 3), "tA": (2, 1), "tD": (3, None)}, ranks   # climbed, dropped, new
        assert t2["seen"][th["all"][0]["id"]] == th["all"][0]["f"], "first-seen time is kept between runs"
        t3, _ = run_trends("wrong", prev=f2, offset=4 * 3600)
        now_ts = int(NOW.timestamp())
        trend_data = {"generated": now_ts, "channels": [{"ch_id": cid(n), "series": [
            {"t": f"Mario speedrun part {i} #mario", "p": now_ts - 3600 * (i + 1), "v": 1000} for i in range(2)] + [
            {"t": "A quiet day", "p": now_ts - 10 * 86400, "v": 500}]} for n in (1, 2, 3)]}
        (Path(tmp) / "trend-data.json").write_text(json.dumps(trend_data), encoding="utf-8")
        t4, _ = run_trends("testkey", prev=f2, offset=5 * 3600, data=Path(tmp) / "trend-data.json")
        hot = t4["hot"]
        assert hot["t:#mario"][0] == 6 and hot["g:Mario"][0] == 6 and hot["t:mario speedrun"][2] == "mario speedrun", {k: hot[k] for k in list(hot)[:6]}
        assert "t:quiet" not in hot and "t:a quiet" not in hot, "old uploads only count as the baseline"
        t5, _ = run_trends("wrong", prev=Path(tmp) / "trends18000testkey.json", offset=6 * 3600)
        assert t5["hot"] == hot, "without this run's data the previous hot list is kept"
        assert t3["yt"]["regions"] == t2["yt"]["regions"], "a refused key keeps the previous charts"
        print(f"trends OK: {sum(len(v) for v in t1['yt']['regions'].values())} charts, rank changes tracked, Wikipedia filtered, bad key keeps old charts, {len(hot)} hot trends")

        # clip opportunities: a big creator on the charts, how many channels clip them, how their clips do
        WORLD.update(longchart=True)
        t6, f6 = run_trends("testkey", prev=Path(tmp) / "trends18000testkey.json", offset=7 * 3600)
        clips = [c for c in t6["clips"] if not c.get("small")]
        assert [c["name"] for c in clips] == ["Big Quiet Channel"], [c.get("name") for c in t6["clips"]]   # the record label is never researched
        bq = clips[0]
        assert bq["subs"] == 5000000 and bq["clippers"] == 1 and bq["clips"] >= 3, bq   # channel 1's Shorts are the only clips found
        assert bq["clip_top"]["ch"] == cid(1) and bq["clip_med"] > 0 and bq["top"]["id"] == "longvideo01", bq
        assert bq["ok"][0] and bq["ok"][1] and 0 < bq["score"] <= 100, (bq["ok"], bq["score"])   # big audience and few clippers
        assert t6["clipq"]["used"] == 1, t6["clipq"]
        t7, _ = run_trends("testkey", prev=f6, offset=7 * 3600 + 60)
        assert t7["clipq"]["used"] == 1 and t7["clips"] == t6["clips"], "a creator researched this week isn't searched again"
        WORLD.update(longchart=False)
        sys.path.insert(0, str(HERE))
        import trends as trends_mod
        cands = trends_mod.clip_candidates({"US": {"music": [{"ch": "UCa", "cht": "Star", "d": 900, "v": 1}], "games": [{"ch": "UCb", "cht": "Gamer Joe", "d": 1800, "v": 5},
                                                                                                                       {"ch": "UCc", "cht": "Workpoint TV", "d": 1800, "v": 9}, {"ch": "UCd", "cht": "Joe Clips", "d": 50, "v": 9}]}})
        assert [c["ch"] for c in cands] == ["UCb"], [c["ch"] for c in cands]   # no music charts, broadcasters or Shorts-only entries
        perm = trends_mod.clip_permission
        assert perm("Gaming every day!" + chr(10) + "Feel free to clip my streams, just credit me.", [])[:3] == ("allows", "Feel free to clip my streams, just credit me.", "channel")
        assert perm("", [{"id": "v1", "desc": "Join the clipping program: whop.com/joe-clips"}])[::3] == ("allows", "v1")
        assert perm("Feel free to clip", [{"id": "v2", "desc": "Do not reupload this video."}])[0] == "forbids", "a clear no wins"
        assert perm("ช่องเกมสนุก ๆ ตัดคลิปได้เลย ใส่เครดิตด้วย", [])[0] == "allows"
        assert perm("ห้ามรีอัพโหลดคลิปของช่อง", [])[0] == "forbids"
        assert perm("New videos every Friday", [{"id": "v3", "desc": "Thanks for watching"}])[0] == "unknown"
        assert data_clip_ok(t7), "researched creators carry the permission check"
        assert bq["moments"] == [] and bq["mvids"][vid(2, 5)]["h"] == "", "comments turned off: no moments, nothing breaks"
        print(f"clip finder OK: researched {bq['name']} ({bq['clippers']} clipper, score {bq['score']}), skipped labels and broadcasters, one search per creator per week")

        # creators who invite clippers: found by a clipping-program search, checked in their own words, researched first, with the moments viewers point to
        WORLD.update(longchart=True)
        t8, f8 = run_trends("testkey", prev=f6, offset=7 * 3600 + 120, disc="4")
        al = t8["allowed"]
        assert [a["ch"] for a in al] == [cid(3)] and al[0]["paid"] and al[0]["quote"].startswith("Feel free to clip"), al   # the small channel with no invitation is dropped
        assert t8["disc_i"] == 1 and t8["clipq"]["disc"] == 1 and t8["clipq"]["used"] == 2, t8["clipq"]
        cf = next(c for c in t8["clips"] if c["ch"] == cid(3))
        assert cf["found_by"] == "invite" and cf["allow"] == "allows" and cf["paid"] and cf["ct"], {k: cf.get(k) for k in ("found_by", "allow", "paid")}
        ms = {m["s"]: m for m in cf["moments"]}
        assert set(ms) == {132, 447}, cf["moments"]                                # 3 seconds before the first reaction; the timestamp list doesn't move it
        fail, end = ms[132], ms[447]
        assert fail["n"] == 3 and fail["c"] == "The big fail" and fail["q"] == "I can't stop laughing at the big fail" and fail["e"] == 162, fail
        assert fail["comp"] == 1 and end["comp"] == 0 and end["c"] == "Questions and answers" and end["n"] == 2, (fail, end)   # one Short already covers the fail
        assert cf["moments"][0]["sc"] >= cf["moments"][1]["sc"] > 0, cf["moments"]
        heat = cf["mvids"][vid(3, 5)]["h"]
        assert len(heat) == 32 and heat[6] == "9" and heat[0] == "0", heat   # 2:15 of 12:00 falls in bucket 6
        t9, _ = run_trends("testkey", prev=f8, offset=7 * 3600 + 180, disc="4")
        assert t9["clipq"]["disc"] <= 4 and t9["allowed"] == t8["allowed"], "already-known creators are kept"
        WORLD.update(longchart=False)
        assert trends_mod.video_chapters("0:00 Intro" + chr(10) + "1:02:03 Late part" + chr(10) + "no time here", 4000) == [(0, "Intro"), (3723, "Late part")]
        assert trends_mod.clean_comment("12:34 lol https://x.y/z   this part!") == "lol this part!"

        class NoComments:
            def call(self, path, **kw):
                raise trends_mod.ApiError("HTTP 403 on commentThreads: disabled")
        assert trends_mod.find_moments(NoComments(), [{"id": "abc", "t": "x", "v": 9, "d": 600, "p": 0}], [], "Joe") == ([], {"abc": {"t": "x", "v": 9, "d": 600, "p": 0, "h": "", "cv": 0}})
        print(f"invites OK: found {al[0]['name']} by its clipping program and researched it first; moments at 2:12 (1 Short already) and 7:27 (none yet)")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
