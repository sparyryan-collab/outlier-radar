#!/usr/bin/env python3
"""End-to-end check of scan.py and trends.py against a fake YouTube Data API (no key, no network, no quota).

Run:  python web/scanner/selftest.py
It starts a local fake API, runs the scanner several times (later runs simulate hours later with more views and a new upload),
and checks scoring, quota accounting, growth rates, new-upload detection, state handling (saved state, refusing to start
from nothing, old imports), the run budget, search-bucket exhaustion, the Shorts orientation filter, the trend feeds and the
profile-picture sync.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOW = dt.datetime.now(dt.timezone.utc)


def cid(n):
    return "UC" + f"{n:022d}"


def vid(c, i):
    return f"v{c}x{i:04d}".ljust(11, "_")


WORLD = {"extra": False, "bump": 0, "search_out": False, "horizontal": False}
CHANNELS = {cid(1): {"name": "Small Hit Channel", "subs": 20000, "base": 400000}, cid(2): {"name": "Big Quiet Channel", "subs": 5000000, "base": 30000},
            cid(3): {"name": "Clip Friendly Streamer", "subs": 2000000, "base": 2000000,
                     "desc": "Streams every night." + chr(10) + "Feel free to clip my streams! Paid clipping program on whop.com/streamer-clips"}}
# viewers' comments on channel 3's long video: three point at the big fail (2:15), a timestamp list, a comment with no time, two at the ending
COMMENTS = [("2:15 I can't stop laughing at the big fail", 340), ("2:20 the fail lmao", 120), ("2:18 💀💀💀", 50), ("0:00 1:00 2:00 3:00 4:00 5:00", 5),
            ("great video", 900), ("7:30 the Q and A ending got me", 10), ("7:41 wait what", 3)]
CHAPTERS = "Streaming highlights" + chr(10) + "0:00 Intro" + chr(10) + "2:10 The big fail" + chr(10) + "6:00 Questions and answers"
QUOTA_BODY = {"error": {"code": 403, "message": "The request cannot be completed because you have exceeded your quota.", "errors": [{"reason": "quotaExceeded"}]}}


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
            if WORLD["search_out"]:   # the 100-a-day search bucket is empty; nothing else is
                return self.reply(403, QUOTA_BODY)
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
            with_player = "player" in q.get("part", [""])[0]
            for i in q["id"][0].split(","):
                if i in vs:
                    v = vs[i]
                    title = "Big fail had him laughing 😂 what if" if i == vid(1, 13) else f"Video {i} 🐱 what if"
                    item = {"id": i, "snippet": {"title": title, "channelId": cid(v["n"]), "channelTitle": CHANNELS[cid(v["n"])]["name"],
                                                 "publishedAt": v["pub"].strftime("%Y-%m-%dT%H:%M:%SZ"), "description": CHAPTERS if i == vid(3, 5) else ""},
                            "statistics": {"viewCount": str(v["views"]), "likeCount": str(v["views"] // 40), "commentCount": str(v["views"] // 2000)},
                            "contentDetails": {"duration": v["dur"]}}
                    if "status" in q.get("part", [""])[0]:   # channel 2's newest Short is marked Made for Kids
                        item["status"] = {"madeForKids": i == vid(2, 13)}
                    if with_player:   # a 45-second horizontal clip on channel 1 when WORLD["horizontal"]; everything else vertical
                        flat = WORLD["horizontal"] and i == vid(1, 12)
                        item["player"] = {"embedHtml": "<iframe></iframe>", "embedWidth": "480" if flat else "270", "embedHeight": "270" if flat else "480"}
                    items.append(item)
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


def scan(tmp, offset, port, prev=None, state=None, allow_seed=False, key="testkey", extra_env=None, out_name=None):
    """Runs scan.py once; returns (exit code, public data or None, full saved state or None, public path, output)."""
    env = dict(os.environ, YT_API_BASE=f"http://127.0.0.1:{port}", YT_API_KEY=key, SEARCH_EVERY="1", PROBES_PER_RUN="6",
               SCAN_TIME_OFFSET=str(offset), PYTHONIOENCODING="utf-8")
    env.update(extra_env or {})
    out = Path(tmp) / (out_name or f"out{offset}.json")
    seed = Path(tmp) / "seed.json"
    if not seed.exists():
        seed.write_text(json.dumps({"channels": [], "_state": {}}), encoding="utf-8")
    queries = Path(tmp) / "queries.json"
    queries.write_text(json.dumps([{"q": "cats what if"}]), encoding="utf-8")
    picks = Path(tmp) / "picks.json"
    picks.write_text("[]", encoding="utf-8")
    extra = Path(tmp) / "extra.json"
    if not extra.exists():   # one channel found by the PC app: only its id is used, as a candidate for the API to check
        extra.write_text(json.dumps([{"ch_id": cid(9), "name": "PC Found Channel", "subs": 1000, "score": 91.0, "tags": [], "queries": ["cats"], "lang": "en",
                                      "english": True, "series": [{"id": vid(9, i), "t": "x", "v": 500000} for i in range(8)],
                                      "last_probe": NOW.timestamp(), "med_recent": 500000}]), encoding="utf-8")
    cmd = [sys.executable, str(HERE / "scan.py"), "--seed", str(seed), "--queries", str(queries), "--picks", str(picks), "--out", str(out), "--extra", str(extra)]
    if prev:
        cmd += ["--prev-url", Path(prev).resolve().as_uri()]
    if state:
        cmd += ["--state", str(state)]
    if allow_seed:
        cmd += ["--allow-seed"]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", cwd=tmp)
    assert key == "testkey" or key not in (r.stdout + r.stderr), "the API key must never be printed"
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() and r.returncode != 2 else None
    full = json.loads((Path(state) / "data.json").read_text(encoding="utf-8")) if state and (Path(state) / "data.json").exists() else None
    return r.returncode, data, full, out, r.stdout + r.stderr


def run_scan(tmp, offset, port, **kw):
    code, data, full, out, text = scan(tmp, offset, port, **kw)
    if code != 0:
        print(text)
        raise SystemExit("scan.py failed")
    return data, full, out


def data_clip_ok(trends_json):
    return all("allow" in c for c in trends_json["clips"] if not c.get("small"))


def main():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sys.path.insert(0, str(HERE))
    import scan as scan_mod
    import trends as trends_mod
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        # refusing to start from nothing: no saved state, no live file, no bootstrap
        code, data, _, out, text = scan(tmp, 0, port, out_name="refused.json")
        assert code == 2 and not out.exists() and "REFUSING" in text, (code, text)
        print("refuse OK: no saved state and no live data, so nothing was written (exit 2)")

        d1, s1, out1 = run_scan(tmp, 0, port, state=state, allow_seed=True)
        st1 = s1["_state"]
        assert "_state" not in d1, "the public file carries no scanner state"
        ch = {c["name"]: c for c in d1["channels"]}
        assert len(ch) == 2, f"expected 2 scored channels, got {list(ch)}"
        hit, quiet = ch["Small Hit Channel"], ch["Big Quiet Channel"]
        assert hit["score"] > quiet["score"] + 20, (hit["score"], quiet["score"])   # small channel with huge views must outrank the big quiet one
        assert all(s["id"] != vid(1, 5) for s in hit["series"]), "12-minute video must be filtered out (not a Short)"
        assert hit["avatar_url"].startswith("https://yt3.ggpht.com/"), hit["avatar_url"]
        assert all(s.get("d") == 45 for s in hit["series"]), "each Short keeps its length in seconds"
        assert all("c" in s and "l" in s for s in hit["series"]), "comments and likes are kept for the comment signal"
        assert all(s.get("o") == "v" for s in hit["series"]), "the player's shape is recorded"
        assert {s["id"] for s in quiet["series"] if s.get("k")} == {vid(2, 13)} and not any(s.get("k") for s in hit["series"]), "Made for Kids is looked up and kept only when true"
        assert hit["created"] == 1785542400, hit.get("created")   # channel creation date (2026-08-01)
        assert hit["lang"] in ("en", "und") and hit["english"], hit["lang"]
        assert hit["src"] == "api" and hit["settled_n"] == 5 and hit["base_n"] == 5, (hit.get("settled_n"), hit.get("base_n"))   # 5 Shorts are a week old or more
        assert hit["trend"] is None and "Rising" not in hit["tags"] and "Fading" not in hit["tags"], "too few settled Shorts to judge momentum"
        assert all(abs(s["x"] - round(s["v"] / hit["med_recent"], 1)) < 0.11 for s in hit["series"]), "× usual uses the same baseline as usual views"
        used1 = st1["quota_used"]
        expect = 0 + 1 + 1 + 2 * 2 + 1  # search (own bucket) + videos(search) + channels + (playlistItems+videos) per channel + featured lookup for the top channel
        assert used1 == expect, f"quota accounting: used {used1}, expected {expect} ({Fake.calls})"
        assert d1["status"]["quota"]["used_today"] == used1
        assert st1["searches_used"] == 1 and d1["status"]["quota"]["searches_today"] == 1, d1["status"]["quota"]
        assert "PC Found Channel" not in [c["name"] for c in d1["channels"]] and st1["extra_hash"], "nothing the PC app measured is published"
        assert st1["failed"].get(cid(9)) == 1 and cid(9) in st1["pending"], "the PC app's channel was tried through the API (it doesn't exist there)"
        assert cid(1) in st1["featured"], "featured channels of a top channel were looked up"
        assert (Path(tmp) / "version.json").exists() and (Path(tmp) / "data-lite.json").exists(), "the small files for polling and first paint are written"
        print(f"run 1 OK: scored {len(ch)} channels, hit={hit['score']} quiet={quiet['score']}, quota {used1} units")

        WORLD.update(extra=True, bump=5000)               # more views, plus one new upload on channel 1
        d2, s2, out2 = run_scan(tmp, 4 * 3600, port, prev=out1, state=state)
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
        assert s2["_state"]["runs"] == 2 and s2["_state"]["quota_used"] > 0
        assert len(s2["_state"]["run_times"]) == 2, s2["_state"]["run_times"]
        assert len(hit2["hist"]) == 2 and hit2["hist"][0][0] < hit2["hist"][1][0], hit2["hist"]                              # one growth point per check
        assert hit2["found"] == hit["found"] and abs(hit["found"] - NOW.timestamp()) < 120, (hit.get("found"), hit2.get("found"))   # first-seen time is kept
        print(f"run 2 OK: {len(grown)} videos grew by 5000 over 4.0h, new upload detected ({int((NOW.timestamp() - newest['p'])/60)} min old), quota today {s2['_state']['quota_used']}")

        # the public file alone (no saved counters) still works: quota use is estimated from the time of day
        code, d3, _, out3, text = scan(tmp, 9 * 3600, port, prev=out2, key="wrong", out_name="out3.json")
        assert out3.exists() and code == 1, (code, text)
        assert len(d3["channels"]) == 2 and "estimating" in text, text
        print("bad-key run OK: data kept, non-zero exit, key not leaked")

        # a channel a visitor asked to track stays in the data even when it ranks too low to be kept
        tstate = Path(tmp) / "state-tracked"
        shutil.copytree(state, tstate)
        d4, s4, _ = run_scan(tmp, 10 * 3600, port, state=tstate, extra_env={"SEARCH_EVERY": "99", "KEEP_CHANNELS": "1", "FIRESTORE_BASE": f"http://127.0.0.1:{port}/v1",
                                                                             "FIREBASE_PROJECT_ID": "demo", "FIREBASE_API_KEY": "fbkey"})
        names4 = [c["name"] for c in d4["channels"]]
        assert len(names4) == 2 and "Big Quiet Channel" in names4, names4   # the top channel, plus the tracked one
        assert cid(2) in s4["_state"]["tracked"] and s4["_state"]["tracked_at"], s4["_state"].get("tracked")
        print(f"tracked channels OK: kept {len(names4)} with KEEP_CHANNELS=1 (the tracked one is pinned)")

        # old imports (yt-dlp records: no upload dates) are private until the API refreshes them, then dropped if it can't
        legacy = json.loads(json.dumps(s2))
        legacy["channels"] = [c for c in legacy["channels"] if c["ch_id"] != cid(1)] + [
            {"ch_id": cid(1), "name": "Small Hit Channel", "subs": 20000, "score": 60, "last_probe": NOW.timestamp() - 4 * 86400,
             "series": [{"id": vid(1, i), "t": "old", "v": 1000} for i in range(8)], "tags": []},
            {"ch_id": cid(8), "name": "Gone Channel", "subs": 5000, "score": 70, "last_probe": NOW.timestamp() - 4 * 86400,
             "series": [{"id": vid(8, i), "t": "old", "v": 9000} for i in range(8)], "tags": []},
            {"ch_id": cid(6), "name": "Stale Channel", "subs": 5000, "score": 99, "src": "api", "last_probe": NOW.timestamp() - 40 * 86400,
             "series": [{"id": vid(6, i), "t": "old", "v": 9000, "p": NOW.timestamp() - 50 * 86400} for i in range(8)], "tags": []}]
        legacy["generated"] = NOW.timestamp() + 11 * 3600
        lstate = Path(tmp) / "state-legacy"
        lstate.mkdir()
        (lstate / "data.json").write_text(json.dumps(legacy), encoding="utf-8")
        d5, s5, _ = run_scan(tmp, 11 * 3600 + 60, port, state=lstate, extra_env={"SEARCH_EVERY": "99"})
        pub5 = {c["ch_id"]: c for c in d5["channels"]}
        all5 = {c["ch_id"]: c for c in s5["channels"]}
        assert pub5[cid(1)]["src"] == "api" and len(pub5[cid(1)]["hist"]) == 1, "an old import is refreshed through the API first, with a fresh history"
        assert cid(8) not in pub5 and cid(8) in all5 and s5["_state"]["failed"].get(cid(8)) == 1, "an old import that failed once stays private"
        assert cid(6) not in all5, "a channel the API couldn't refresh for a month is dropped"
        d6, s6, _ = run_scan(tmp, 11 * 3600 + 120, port, state=lstate, extra_env={"SEARCH_EVERY": "99"})
        assert cid(8) not in {c["ch_id"] for c in s6["channels"]}, "an old import that fails twice is dropped"
        print("old imports OK: refreshed through the API before publishing, dropped when the API can't find them, month-old records removed")

        # the search bucket running dry stops searching, not re-checking
        WORLD.update(search_out=True)
        qstate = Path(tmp) / "state-search"
        shutil.copytree(state, qstate)
        d7, s7, _ = run_scan(tmp, 30 * 3600, port, state=qstate)
        assert s7["_state"]["search_out"] == scan_mod.pacific_day(NOW.timestamp() + 30 * 3600), s7["_state"].get("search_out")
        assert any(abs(c["last_probe"] - (NOW.timestamp() + 30 * 3600)) < 120 for c in d7["channels"]), "channels were re-checked anyway"
        assert any("Searches used up" in x for x in d7["status"]["log"]), d7["status"]["log"][-3:]
        WORLD.update(search_out=False)
        print("search bucket OK: an empty search bucket only stops searching")

        # the run budget follows how often GitHub really starts runs
        t0 = NOW.timestamp()
        slow, gap_slow = scan_mod.run_budget({"run_times": [t0 - 18 * 3600, t0 - 12 * 3600, t0 - 6 * 3600], "quota_used": 0}, t0)
        fast, gap_fast = scan_mod.run_budget({"run_times": [t0 - 2700, t0 - 1800, t0 - 900], "quota_used": 0}, t0)
        spent, _ = scan_mod.run_budget({"run_times": [], "quota_used": 8000}, t0)
        assert gap_slow == 6 * 3600 and gap_fast == 900 and slow > fast and spent == 0, (slow, gap_slow, fast, gap_fast, spent)
        assert slow >= 1300, slow   # about a quarter of the day's budget when runs come every 6 hours
        print(f"run budget OK: {slow} units per run when runs come every 6 h, {fast} when every 15 min")

        # statistics history: YouTube allows 30 days, or up to 36 months once the Analytics & Reporting amendment is accepted (STATS_DAYS)
        rec = {"hist": [[int(t0 - 40 * 86400), 1000, 5000, 50], [int(t0 - 5 * 86400), 1100, 5200, 52]], "last_probe": t0 - 5 * 86400, "med_recent": 5200,
               "src": "api", "series": [{"id": "x", "p": 1}]}
        h = scan_mod.add_hist(rec, t0, 1200, {"med_recent": 5300, "score": 53})
        assert scan_mod.STATS_DAYS == 30 and [x[1] for x in h] == [1100, 1200], h
        print("statistics OK: subscriber history older than 30 days is dropped")

        # Shorts shape: horizontal short videos are left out, but only once the player sizes have proved reliable
        WORLD.update(horizontal=True)
        ostate = Path(tmp) / "state-orient"
        shutil.copytree(state, ostate)
        full = json.loads((ostate / "data.json").read_text(encoding="utf-8"))
        full["_state"]["orient"] = {"n": 150, "v": 150}
        (ostate / "data.json").write_text(json.dumps(full), encoding="utf-8")
        d8, s8, _ = run_scan(tmp, 40 * 3600, port, state=ostate, extra_env={"SEARCH_EVERY": "99"})
        h8 = next(c for c in d8["channels"] if c["ch_id"] == cid(1))
        assert any(s["id"] == vid(1, 12) and s["o"] == "h" for s in h8["series"]) and not d8["orient"]["trusted"], "not trusted yet: kept"
        full = json.loads((ostate / "data.json").read_text(encoding="utf-8"))
        full["_state"]["orient"] = {"n": 400, "v": 390}
        (ostate / "data.json").write_text(json.dumps(full), encoding="utf-8")
        d9, s9, _ = run_scan(tmp, 41 * 3600 + 3 * 3600, port, state=ostate, extra_env={"SEARCH_EVERY": "99"})
        h9 = next(c for c in d9["channels"] if c["ch_id"] == cid(1))
        assert d9["orient"]["trusted"] and all(s["id"] != vid(1, 12) for s in h9["series"]), "trusted: the horizontal video is not a Short"
        WORLD.update(horizontal=False)
        print("Shorts shape OK: horizontal short videos dropped only after vertical Shorts proved the player sizes")

        # momentum and the other numbers, on the SeedThePast case from the audit (7 Shorts; big hits are the older ones)
        radar = scan_mod.load_radar()
        ages = [3.3, 7.1, 13.8, 20.7, 32.6, 34.5, 39.4]
        views = [1540882, 282144, 7782417, 159240, 6839314, 14991840, 1989379]
        seed_like = [{"id": f"s{i}", "v": v, "p": t0 - a * 86400} for i, (a, v) in enumerate(zip(ages, views))]
        m = radar.compute_metrics(seed_like, 59400, t0)
        assert m["trend"] is None and "Rising" not in m["tags"], m   # 6 settled Shorts: not enough to compare two groups of 4
        assert m["settled_n"] == 6 and m["base_n"] == 6 and m["med_recent"] == sorted(views[1:])[2:4][0] / 2 + sorted(views[1:])[2:4][1] / 2, m
        assert m["top_share"] == round(max(views) / sum(views), 3)
        hidden = radar.compute_metrics(seed_like, 0, t0)
        assert hidden["ratio"] is None and hidden["score"] < m["score"], (hidden["score"], m["score"])   # a hidden subscriber count earns no points
        rising = [{"id": f"r{i}", "v": v, "p": t0 - (8 + i) * 86400} for i, v in enumerate([900000] * 6 + [300000] * 6)]
        assert radar.compute_metrics(rising, 50000, t0)["trend"] == 3.0, "newer settled Shorts at 3× the older ones"
        print("metrics OK: SeedThePast gets 'not enough history' instead of '2.2× rising'; hidden subscribers earn no ratio points")

        # edge cases: nothing to score, zero views, one giant video, only brand-new uploads, hidden or tiny subscriber counts, no dates
        def ser(views, ages):
            return [{"id": f"e{i:010d}", "t": "x", "v": v, "p": t0 - a * 86400, "d": 30} for i, (v, a) in enumerate(zip(views, ages))]
        cm = radar.compute_metrics
        assert cm([], 1000, t0) is None and cm(ser([50000], [10]), 1000, t0) is None, "too little to score"
        zero = cm(ser([0] * 10, range(8, 18)), 1000, t0)
        assert zero["med_recent"] == 0 and zero["score"] <= 5 and not zero["tags"], zero
        giant = cm(ser([50_000_000] + [10_000] * 11, range(8, 20)), 5000, t0)
        assert "One-hit" in giant["tags"] and giant["top_share"] > 0.99 and giant["score"] < 10, giant   # one video carrying the channel is penalised
        fresh = cm(ser([100_000] * 8, [1, 2, 2, 3, 4, 5, 5, 6]), 2000, t0)
        assert fresh["settled_n"] == 0 and fresh["base_n"] == 8 and fresh["trend"] is None, fresh   # all still collecting views: no momentum claim
        steady = cm(ser([200_000] * 12, range(8, 20)), 0, t0)
        assert steady["ratio"] is None and "Consistent engine" in steady["tags"], steady
        tiny = cm(ser([600] * 12, range(8, 20)), 2, t0)
        assert tiny["ratio"] == 300 and tiny["score"] < 50, tiny   # a huge ratio on tiny views stays below the 'worth watching' level
        undated = cm([{"id": f"u{i:010d}", "t": "x", "v": 100_000, "d": 30} for i in range(12)], 5000, t0)
        assert undated["settled_n"] == 9, undated   # without upload dates the 3 newest are left out instead
        print("metrics edge cases OK: empty, zero views, one giant video, only new uploads, hidden and tiny subscriber counts, no dates")

        # trend feeds: YouTube charts by country and category, Wikipedia's most-read list, history between runs
        tstate2 = Path(tmp) / "state-trends"

        def run_trends(key, prev=None, offset=0, data=None, disc="0", seed=False, use_state=False):
            out = Path(tmp) / f"trends{offset}{key}.json"
            env = dict(os.environ, YT_API_BASE=f"http://127.0.0.1:{port}", WIKI_BASE=f"http://127.0.0.1:{port}/wiki", YT_API_KEY=key, DISCOVER_PER_DAY=disc,
                       TRENDS_YT_EVERY="0", SCAN_TIME_OFFSET=str(offset), PREV_TRENDS_URL=prev.resolve().as_uri() if prev else "", PYTHONIOENCODING="utf-8",
                       ALLOW_SEED="1" if seed else "")
            cmd = [sys.executable, str(HERE / "trends.py"), "--out", str(out)] + (["--data", str(data)] if data else []) + (["--state", str(tstate2)] if use_state else [])
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", cwd=tmp)
            assert r.returncode == 0 and out.exists(), (r.returncode, r.stdout, r.stderr)
            assert key not in (r.stdout + r.stderr), "the API key must never be printed"
            return json.loads(out.read_text(encoding="utf-8")), out
        r0 = subprocess.run([sys.executable, str(HERE / "trends.py"), "--out", str(Path(tmp) / "t-refused.json")], capture_output=True, text=True, encoding="utf-8", cwd=tmp,
                            env=dict(os.environ, YT_API_KEY="testkey", YT_API_BASE=f"http://127.0.0.1:{port}", PREV_TRENDS_URL="", PYTHONIOENCODING="utf-8", ALLOW_SEED=""))
        assert r0.returncode == 2 and not (Path(tmp) / "t-refused.json").exists(), (r0.returncode, r0.stderr)
        WORLD.update(extra=False)
        t1, f1 = run_trends("testkey", seed=True, use_state=True)
        assert (tstate2 / "trends.json").exists(), "the next run's copy is saved"
        th = t1["yt"]["regions"]["TH"]
        assert set(t1["yt"]["regions"]) == {"TH", "US"} and "tech" not in th and len(th["all"]) == 3, list(th)
        assert all(x["pr"] is None and x["f"] and x["d"] == 40 for x in th["all"]), th["all"][0]
        wiki = t1["wiki"]["en"]["items"]
        assert [w["a"] for w in wiki] == ["Big_Event", "Steady_Page"] and wiki[0]["pv"] and wiki[0]["v"] > wiki[0]["pv"], wiki   # Main_Page dropped; yesterday vs the day before
        assert t1["clipq"]["units"] > 0, t1["clipq"]
        WORLD.update(extra=True)
        t2, f2 = run_trends("testkey", offset=3600, use_state=True)
        ranks = {x["id"][:2]: (x["r"], x["pr"]) for x in t2["yt"]["regions"]["US"]["all"]}
        assert ranks == {"tC": (1, 3), "tA": (2, 1), "tD": (3, None)}, ranks   # climbed, dropped, new (history came from the saved state)
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
        assert hot["t:#mario"][1] == 4.0, hot["t:#mario"]   # (6 + 2) / (0 + 2): small counts are pulled toward 1
        assert hot["t:#mario"][3] == 3, hot["t:#mario"]   # three channels posted it (alerts name them)
        assert "t:quiet" not in hot and "t:a quiet" not in hot, "old uploads only count as the baseline"
        t5, _ = run_trends("wrong", prev=Path(tmp) / "trends18000testkey.json", offset=6 * 3600)
        assert t5["hot"] == hot, "without this run's data the previous hot list is kept"
        assert t3["yt"]["regions"] == t2["yt"]["regions"], "a refused key keeps the previous charts"
        assert trends_mod.title_terms("I’m done with mom yelling") == {"mom", "yelling", "mom yelling"}, "contractions are stop words, curly or straight"
        assert not trends_mod.title_terms("बच्चे का ऐसा") & {"का ऐसा", "का", "ऐसा"}, "Hindi function words are stop words"
        assert trends_mod.title_terms("ทดสอบ #ไทย #ไวรัล") == {"#ไทย"}, "Thai counts only by hashtag; #viral-style tags are noise"
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
        assert [a["ch"] for a in al] == [cid(3)] and al[0]["paid"] and al[0]["quote"].startswith("Feel free to clip") and al[0]["checked"], al   # the small channel with no invitation is dropped
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
        week = json.loads(f8.read_text(encoding="utf-8"))   # a week on: the invitation is re-checked against the channel description
        week["allowed"][0]["checked"] -= 8 * 86400
        (Path(tmp) / "trends-week.json").write_text(json.dumps(week), encoding="utf-8")
        t10, _ = run_trends("testkey", prev=Path(tmp) / "trends-week.json", offset=7 * 3600 + 240, disc="0")
        assert t10["allowed"][0]["checked"] > week["allowed"][0]["checked"] and t10["allowed"][0]["subs"] == 2000000, t10["allowed"]
        WORLD.update(search_out=True)
        t11, _ = run_trends("testkey", prev=Path(tmp) / "trends-week.json", offset=8 * 86400, disc="4")
        assert t11["clipq"].get("search_out") and t11["yt"]["regions"], "an empty search bucket stops searching; charts still refresh"
        WORLD.update(search_out=False, longchart=False)
        assert trends_mod.video_chapters("0:00 Intro" + chr(10) + "1:02:03 Late part" + chr(10) + "no time here", 4000) == [(0, "Intro"), (3723, "Late part")]
        assert trends_mod.clean_comment("12:34 lol https://x.y/z   this part!") == "lol this part!"

        class NoComments:
            def call(self, path, **kw):
                raise trends_mod.ApiError("HTTP 403 on commentThreads: disabled")
        assert trends_mod.find_moments(NoComments(), [{"id": "abc", "t": "x", "v": 9, "d": 600, "p": 0}], [], "Joe") == ([], {"abc": {"t": "x", "v": 9, "d": 600, "p": 0, "h": "", "cv": 0}})
        print(f"invites OK: found {al[0]['name']} by its clipping program and researched it first; moments at 2:12 (1 Short already) and 7:27 (none yet); weekly re-check")

        # profile pictures: new ones first, then month-old ones; a picture that failed waits a day; the folder is saved only when it changed
        import contextlib
        import io
        import types
        import urllib.error
        import sync_avatars as av
        site = Path(tmp) / "av-site"
        av_dir, dpath = site / "avatars", site / "data.json"
        av_dir.mkdir(parents=True)
        jpeg, url, calls = b"\xff\xd8\xff" + b"0" * 50, "https://yt3.ggpht.com/abc=s88-c-k", []
        for n in (20, 23, 24):   # 20 left the list, 23 is a month old, 24 is fresh
            (av_dir / f"{cid(n)}.jpg").write_bytes(jpeg)
        (av_dir / "_index.json").write_text(json.dumps({cid(20): 1, cid(23): int(time.time()) - 30 * 86400, cid(24): int(time.time())}), encoding="utf-8")
        dpath.write_text(json.dumps({"generated": 1, "channels": [
            {"ch_id": cid(21), "avatar_url": url, "score": 5}, {"ch_id": cid(22), "avatar_url": url + "#bad", "score": 4},
            {"ch_id": cid(25), "avatar_url": "https://example.com/x.jpg", "score": 3}, {"ch_id": cid(23), "avatar_url": url, "score": 2},
            {"ch_id": cid(24), "avatar_url": url, "score": 1}]}), encoding="utf-8")

        def fake_fetch(u):
            calls.append(u)
            if WORLD.get("av429"):
                raise urllib.error.HTTPError(u, 429, "Too Many Requests", {}, None)
            return None if u.endswith("#bad") else jpeg
        av.fetch, av.time = fake_fetch, types.SimpleNamespace(time=time.time, sleep=lambda s: None)

        def sync(limit):
            out, old_argv, old_out = site / "github-output.txt", sys.argv, os.environ.get("GITHUB_OUTPUT")
            out.write_text("", encoding="utf-8")
            calls.clear()
            sys.argv, os.environ["GITHUB_OUTPUT"] = ["sync_avatars.py", str(dpath), str(av_dir), str(limit)], str(out)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    av.main()
            finally:
                sys.argv = old_argv
                if old_out is None:
                    os.environ.pop("GITHUB_OUTPUT")
                else:
                    os.environ["GITHUB_OUTPUT"] = old_out
            return out.read_text(encoding="utf-8").strip(), {c["ch_id"]: c["avatar_ok"] for c in json.loads(dpath.read_text(encoding="utf-8"))["channels"]}

        changed, ok = sync(10)
        idx = json.loads((av_dir / "_index.json").read_text(encoding="utf-8"))
        lite = json.loads((site / "data-lite.json").read_text(encoding="utf-8"))
        assert changed == "changed=true" and not (av_dir / f"{cid(20)}.jpg").exists() and cid(20) not in idx, "a departed channel's picture is deleted"
        assert ok == {cid(21): True, cid(22): False, cid(25): False, cid(23): True, cid(24): True}, ok
        assert len(calls) == 3 and time.time() - idx[cid(23)] < 60, calls   # new 21, failing 22, month-old 23; never the other site's address or the fresh one
        assert lite["channels"][0]["ch_id"] == cid(21) and lite["channels"][0]["avatar_ok"], lite["channels"][0]
        changed, _ = sync(10)
        assert changed == "changed=false" and calls == [], calls   # the failed picture waits a day and nothing else is due
        d = json.loads(dpath.read_text(encoding="utf-8"))
        d["channels"] += [{"ch_id": cid(100 + i), "avatar_url": url + "#bad", "score": 0} for i in range(10)]
        dpath.write_text(json.dumps(d), encoding="utf-8")
        changed, _ = sync(2)
        assert len(calls) == 4 and changed == "changed=false", calls   # failures stop at twice the download limit
        WORLD["av429"] = True
        changed, _ = sync(10)
        WORLD["av429"] = False
        failed = json.loads((av_dir / "_failed.json").read_text(encoding="utf-8"))
        assert len(calls) == 1 and len(failed) == 5 and changed == "changed=false", (calls, failed)   # a 429 stops the run and blames no channel
        print("profile pictures OK: new and month-old pictures fetched, departed channels' deleted, failures wait a day and are capped, a 429 stops the run")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
