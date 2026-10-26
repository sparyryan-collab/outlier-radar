#!/usr/bin/env python3
"""Trend feeds for the site: what's trending on YouTube by country and category, and what people are reading about on Wikipedia.

Usage: python trends.py --out _site/trends.json
Env:   YT_API_KEY (the scanner's key), PREV_TRENDS_URL (the live site's trends.json, which carries the history between runs)

Standard library only. Each YouTube chart costs 1 unit (about 24 per refresh), and charts refresh at most every 3 hours.
Wikipedia's daily most-read lists are open data (CC0) from the Wikimedia pageviews API and refresh every 6 hours.
Whatever fails, the previous feeds are kept, so the site never loses its trends because of one bad run.
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # works however Python is started (including isolated mode)
from yt_api import ApiError, QuotaExceeded, YouTube, iso_to_ts, parse_duration  # noqa: E402

REGIONS = ["TH", "US"]
CATEGORIES = {"all": None, "games": "20", "animation": "1", "entertainment": "24", "comedy": "23", "music": "10",
              "sports": "17", "news": "25", "animals": "15", "howto": "26", "tech": "28", "people": "22"}
YT_EVERY = int(os.environ.get("TRENDS_YT_EVERY", str(3 * 3600)))
WIKI_EVERY = 6 * 3600
WIKI_BASE = os.environ.get("WIKI_BASE", "https://wikimedia.org/api/rest_v1/metrics/pageviews/top")
WIKIS = {"en": "en.wikipedia", "th": "th.wikipedia"}
UA = "ViralRadar/1.0 (https://github.com/sparyryan-collab/outlier-radar)"   # Wikimedia asks every client to name itself
KEEP, WIKI_KEEP, SEEN_DAYS = 25, 40, 14
# The page's games and cartoons boards; names must match the GAMES and CARTOONS lists in index.html (they are the alert keys)
GAMES = [("Roblox", r"roblox"), ("Minecraft", r"minecraft"), ("Fortnite", r"fortnite"), ("GTA", r"\bgta\b|grand theft auto"), ("GTA 6", r"\bgta ?(6|vi)\b"),
         ("Brawl Stars", r"brawl ?stars"), ("Free Fire", r"free ?fire|ฟีฟาย"), ("PUBG", r"pubg"), ("ROV", r"\brov\b|arena of valor"), ("Mobile Legends", r"mobile legends|\bmlbb\b"),
         ("Valorant", r"valorant"), ("Genshin Impact", r"genshin"), ("Honkai", r"honkai"), ("Among Us", r"among ?us"), ("Poppy Playtime", r"poppy playtime|huggy wuggy|catnap"),
         ("FNAF", r"\bfnaf\b|five nights at freddy"), ("Garten of Banban", r"banban"), ("Clash Royale", r"clash royale"), ("Clash of Clans", r"clash of clans"),
         ("Pokémon", r"pok[eé]mon|pikachu"), ("Mario", r"\bmario\b"), ("Sonic", r"\bsonic\b"), ("Call of Duty", r"call of duty|warzone"), ("Apex Legends", r"apex legends"),
         ("Overwatch", r"overwatch"), ("Counter-Strike", r"counter[- ]?strike|\bcs2\b"), ("Elden Ring", r"elden ring"), ("Silksong", r"silksong|hollow knight"),
         ("Stardew Valley", r"stardew"), ("Terraria", r"terraria"), ("Subway Surfers", r"subway surfers"), ("Geometry Dash", r"geometry dash"), ("Rocket League", r"rocket league"),
         ("EA FC / FIFA", r"\bea ?fc\b|\bfifa\b|\bfc ?2[4-7]\b"), ("eFootball", r"efootball"), ("NBA 2K", r"nba ?2k"), ("Blox Fruits", r"blox fruits?"), ("Brookhaven", r"brookhaven"),
         ("Grow a Garden", r"grow a garden"), ("Steal a Brainrot", r"steal a brainrot"), ("99 Nights in the Forest", r"99 nights"), ("Dead Rails", r"dead rails"), ("Forsaken", r"forsaken"),
         ("Bedwars", r"bedwars"), ("Stumble Guys", r"stumble guys"), ("Fall Guys", r"fall guys"), ("Honor of Kings", r"honor of kings"), ("Delta Force", r"delta force"),
         ("Marvel Rivals", r"marvel rivals"), ("Wuthering Waves", r"wuthering waves"), ("Plants vs Zombies", r"plants vs\.? zombies|\bpvz\b"), ("Chess", r"\bchess\b|หมากรุก")]
CARTOONS = [("Sprunki", r"sprunki"), ("Skibidi Toilet", r"skibidi"), ("Italian Brainrot", r"brainrot|tralalero|tung ?tung|sahur|bombardiro|cappuccina|lirili|chimpanzini"),
            ("Labubu", r"labubu"), ("KPop Demon Hunters", r"demon hunters|huntrix|saja boys"), ("Bluey", r"\bbluey\b"), ("SpongeBob", r"sponge ?bob"), ("Peppa Pig", r"peppa"),
            ("Cocomelon", r"cocomelon"), ("Doraemon", r"doraemon|โดราเอม่อน"), ("Shin-chan", r"shin ?chan|ชินจัง"), ("Pokémon", r"pok[eé]mon|pikachu"), ("Minions", r"\bminions?\b"),
            ("Hello Kitty & Sanrio", r"hello kitty|sanrio|kuromi|cinnamoroll|my melody"), ("Demon Slayer", r"demon slayer|kimetsu"), ("One Piece", r"one piece|luffy"),
            ("Naruto", r"naruto"), ("Dragon Ball", r"dragon ?ball|goku"), ("Jujutsu Kaisen", r"jujutsu|gojo"), ("Chainsaw Man", r"chainsaw man"), ("Spy x Family", r"spy ?x ?family"),
            ("Smurfs", r"\bsmurfs?\b"), ("Toy Story", r"toy story"), ("Zootopia", r"zootopia"), ("Frozen", r"\bfrozen\b|\belsa\b"), ("Moana", r"moana"), ("Shrek", r"shrek"),
            ("Garfield", r"garfield"), ("Tom and Jerry", r"tom (and|&) jerry"), ("The Amazing Digital Circus", r"digital circus|pomni"), ("Murder Drones", r"murder drones"),
            ("Hazbin Hotel", r"hazbin|helluva"), ("Rainbow Friends", r"rainbow friends"), ("Siren Head", r"siren head|cartoon cat"), ("Stickman", r"stick ?man"),
            ("Anime", r"\banime\b|อนิเมะ"), ("Cartoons", r"cartoon|การ์ตูน")]
RECENT, WINDOW = 3 * 86400, 21 * 86400
NO_SPACES = re.compile(r"[฀-๿぀-ヿ㐀-鿿가-힯]")   # scripts without spaces: only their hashtags are counted here


def title_terms(title):
    """Hashtags, words and two-word phrases, lowercased, the way the page counts them (words only for languages that use spaces)."""
    t = (title or "").lower()
    out = set(re.findall(r"#\w{2,}", t))
    words = [w for w in re.findall(r"[^\W\d_][\w'’]*", re.sub(r"#\w+", " ", t)) if not NO_SPACES.search(w)]
    for i, w in enumerate(words):
        out.add(w)
        if i + 1 < len(words):
            out.add(w + " " + words[i + 1])
    return out


def hot_trends(data_path, t):
    """Trends taking off right now: at least 3 new Shorts from 2+ channels in 3 days, at least twice the usual rate. Keys match the page's."""
    try:
        data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    now = data.get("generated") or t
    stats = {}
    boards = [("g:" + n, re.compile(rx, re.I)) for n, rx in GAMES] + [("c:" + n, re.compile(rx, re.I)) for n, rx in CARTOONS]

    def add(key, age, ch):
        s = stats.setdefault(key, [0, 0, set()])
        if age <= RECENT:
            s[0] += 1
            s[2].add(ch)
        else:
            s[1] += 1
    for c in data.get("channels", []):
        for v in c.get("series", []):
            age = now - (v.get("p") or 0)
            if not v.get("p") or age < 0 or age > WINDOW:
                continue
            for term in title_terms(v.get("t")):
                add("t:" + term, age, c["ch_id"])
            for key, rx in boards:
                if rx.search(v.get("t") or ""):
                    add(key, age, c["ch_id"])
    hot = {}
    for key, (r, b, chs) in stats.items():
        lift = (r / 3) / ((b + 1) / 18)
        if r >= 3 and len(chs) >= 2 and lift >= 2:
            hot[key] = [r, round(lift, 1), key[2:]]
    return dict(sorted(hot.items(), key=lambda kv: -kv[1][0] * min(kv[1][1], 10))[:4000])
SKIP_WIKI = re.compile(r"^(Main_Page$|หน้าหลัก$|Special:|Wikipedia:|File:|Portal:|Help:|Category:|Template:|Talk:|User:|"
                       r"พิเศษ:|วิกิพีเดีย:|ไฟล์:|สถานีย่อย:|หมวดหมู่:|แม่แบบ:|-$|Search$|Cleopatra$|\.)", re.I)
ADULT = re.compile(r"xxx|porn|xvideos|xnxx|onlyfans|hentai|nude|เย็ด|โป๊|ควย|^หี$", re.I)   # bots and adult pages crowd these lists


# ---------- clip opportunities ----------
CLIP_PER_RUN = int(os.environ.get("CLIP_PER_RUN", "6"))     # creators researched per run (one YouTube search each)
CLIP_PER_DAY = int(os.environ.get("CLIP_PER_DAY", "30"))    # Google allows about 100 searches a day; the scanner uses at most 45
CLIP_KEEP_DAYS, CLIP_MIN_SUBS = 7, 300_000
NOT_CLIPPABLE = {"music", "news", "sports", "animation"}    # labels, newsrooms, leagues and studios strike re-uploads
BRANDS = re.compile(r"vevo|records|official|music|news|\btv\b|studios?|pictures|films?\b|netflix|disney|marvel|warner|universal|sony|hbo|paramount|"
                    r"\bnba\b|\bnfl\b|fifa|uefa|premier league|espn|\bbbc\b|\bcnn\b|\bfox\b|ch3|one31|gmm|workpoint|thairath|amarin|\bch ?7|mono29|pptv|true4u|"
                    r"channel ?[0-9]|ช่อง ?[0-9]|ไทยรัฐ|อมรินทร์|เวิร์คพอยท์", re.I)
# What creators write about clipping, in their channel description or under their videos (English and Thai)
ALLOW_RX = re.compile(r"clip(s|ping)? (program|campaign|team)|clippers? (wanted|welcome|needed)|join .{0,20}clipp|feel free to (clip|use)|you (can|may) clip|"
                      r"clip (my|our) (videos|content|streams?|podcasts?)|clips? (are|is) (allowed|welcome|encouraged)|get paid (to|for) clip|earn .{0,25}clipping|"
                      r"whop\.com/\S*clip|content rewards|vyro|clipfarm|clipping (discord|server)|"
                      r"ตัดคลิป.{0,12}(ได้|อนุญาต|ฟรี)|อนุญาต.{0,12}ตัดคลิป|รับสมัคร.{0,12}ตัดคลิป|ตัดคลิป.{0,8}(ไปลง|ไปโพสต์)", re.I)
FORBID_RX = re.compile(r"(do not|don'?t|no) re-?upload|re-?upload(ing|s)? .{0,30}(not allowed|prohibited|forbidden|strike)|will be (taken down|struck|reported)|"
                       r"do not (steal|copy|repost) (my|our)|unauthori[sz]ed (use|re-?upload)|"
                       r"ห้าม.{0,10}(รีอัพ|นำไป|คัดลอก|ดัดแปลง|ตัดต่อ|reup)|ไม่อนุญาต.{0,16}(นำไป|ตัด|เผยแพร่)", re.I)
INVITES = ALLOW_RX
PAID_RX = re.compile(r"whop|vyro|content rewards|clipfarm|get paid|paid (per|for)|earn|per (1k|1,000|thousand|million) views|\bcpm\b|\$\s?\d|campaign|ค่าจ้าง|รายได้|จ่ายเงิน", re.I)

# Creators who invite clippers: a few searches a day for clipping-program phrases, kept only when their own words say so
DISCOVER_PER_DAY = int(os.environ.get("DISCOVER_PER_DAY", "4"))
DISCOVER_QUERIES = [("channel", "clipping program"), ("video", "join my clipping program"), ("channel", "clippers wanted"), ("video", "feel free to clip"),
                    ("channel", "clip my streams"), ("video", "content rewards clipping"), ("channel", "ตัดคลิป อนุญาต"), ("video", "รับสมัครคนตัดคลิป")]
ALLOWED_KEEP_DAYS, ALLOWED_MIN_SUBS, ALLOWED_MAX = 45, 100_000, 150

# The moments worth clipping, read from a video's own comments: viewers write timestamps ("12:34 I lost it") at the parts they loved
TS_RX = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)(?![\d:])")
CHAPTER_RX = re.compile(r"^\s*[\[(]?((?:\d{1,2}:)?[0-5]?\d:[0-5]\d)[\])]?\s*[-–—:|.)]?\s*(\S.{1,79}?)\s*$")
WORD_RX = re.compile(r"[^\W\d_]{4,}")
STOPWORDS = set("this that with from have what when they them were your just like about there their would could should because really video videos part full "
                "clip clips shorts short episode official best moment moments funny lmao time times guys going know think people thing things love loved after "
                "before while where which here than then some more most much very into over only even still also back being been dont didnt cant wont youre "
                "thats isnt".split())
MOMENT_VIDEOS, MOMENTS_KEEP, HEAT_BUCKETS = 3, 6, 32
MOMENTS_PER_DAY = int(os.environ.get("MOMENTS_PER_DAY", "40"))   # daily re-reads of a creator's newest videos (about 5 units each)


def to_sec(m):
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def video_chapters(desc, d):
    """Chapters the creator wrote in the description (0:00 Intro, 4:12 The fight, ...)."""
    out = []
    for line in (desc or "").splitlines():
        m = CHAPTER_RX.match(line)
        if m:
            sec = to_sec(TS_RX.search(m.group(1)))
            if sec < d:
                out.append((sec, m.group(2).strip()))
    return sorted(out) if len(out) >= 2 else []


def words_of(s, drop=()):
    return {w for w in (x.lower() for x in WORD_RX.findall(s or "")) if w not in STOPWORDS and w not in drop}


def clean_comment(text):
    s = re.sub(r"https?://\S+", " ", TS_RX.sub(" ", text or ""))
    s = re.sub(r"\s+", " ", s).strip(" -–—:|,.")
    return s[:110] + ("…" if len(s) > 110 else "")


def heat_strip(marks, d):
    """Where in the video viewers point, as a row of digits (0 = nobody, 9 = the busiest part)."""
    b = [0.0] * HEAT_BUCKETS
    for m in marks:
        b[min(HEAT_BUCKETS - 1, int(m[0] / max(d, 1) * HEAT_BUCKETS))] += m[1]
    top = max(b) or 1
    return "".join(str(min(9, math.ceil(9 * x / top))) if x else "0" for x in b)


def clip_matches(tokens, clip_titles):
    """How many Shorts already made about the creator look like this (two shared key words, or one long one)."""
    n = 0
    for title in clip_titles:
        shared = tokens & words_of(title)
        if len(shared) >= 2 or any(len(w) >= 7 for w in shared):
            n += 1
    return n


def video_moments(v, items, clip_titles, name_words):
    """The parts of one long video that viewers point to in its comments, and how many Shorts already cover each."""
    d, marks, texts = v["d"], [], []
    for it in items:
        s = ((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        text, likes = s.get("textOriginal") or s.get("textDisplay") or "", int(s.get("likeCount") or 0)
        secs = [x for x in (to_sec(m) for m in TS_RX.finditer(text)) if 0 < x < d]
        if not secs:
            continue
        w = (1 + math.log2(1 + likes)) / (len(secs) if len(secs) > 2 else 1)   # a long list of timestamps is a table of contents, not a reaction
        texts.append((clean_comment(text), likes))
        marks += [(x, w, len(texts) - 1, len(secs) <= 2) for x in secs]
    marks.sort()
    groups, cur = [], []
    for m in marks:   # timestamps within 30 seconds of each other point at the same moment
        if cur and m[0] - cur[0][0] > 30:
            groups.append(cur)
            cur = []
        cur.append(m)
    if cur:
        groups.append(cur)
    chaps, out = video_chapters(v.get("desc"), d), []
    for g in groups:
        core = [m for m in g if m[3]] or g   # where reactions point; timestamp lists only add weight
        weight, who = sum(m[1] for m in g), {m[2] for m in core}
        if len(who) < 2 and weight < 4:
            continue
        first, last = core[0][0], core[-1][0]
        s = max(0, first - 3)
        e = min(d, s + 58, max(last + 15, s + 30))
        quote = max((texts[i] for i in who if len(texts[i][0]) >= 3), key=lambda x: x[1], default=("", 0))[0]
        chap = next((c for sec, c in reversed(chaps) if sec <= first), "")
        out.append({"v": v["id"], "s": s, "e": e, "n": len(who), "w": round(weight, 1), "q": quote, "c": chap[:80],
                    "comp": None if clip_titles is None else clip_matches(words_of(quote + " " + chap, name_words), clip_titles)})
    return out, heat_strip(marks, d) if marks else ""


def moment_score(m, views):
    """0-100: how many viewers point at it, how big the video is, and how few Shorts cover it yet."""
    reach = min(1, max(0.3, (math.log10(max(views, 1)) - 4) / 2.5))
    crowd = 0.8 if m["comp"] is None else 1 if m["comp"] == 0 else 0.6 if m["comp"] == 1 else 0.35   # None: the Shorts about them weren't kept (researched before)
    return round(100 * (1 - math.exp(-m["w"] / 8)) * reach * crowd)


def find_moments(yt, longs, clip_titles, name):
    """Reads the top comments of a creator's biggest recent long videos (1 unit each); returns the best moments and a summary of each video."""
    name_words, moments, mvids = words_of(name), [], {}
    for v in sorted(longs, key=lambda x: -x["v"])[:MOMENT_VIDEOS]:
        try:
            items = yt.call("commentThreads", part="snippet", videoId=v["id"], order="relevance", maxResults=100, textFormat="plainText").get("items", [])
        except ApiError:   # comments turned off
            items = []
        ms, heat = video_moments(v, items, clip_titles, name_words)
        for m in ms:
            m["sc"] = moment_score(m, v["v"])
        moments += sorted(ms, key=lambda m: -m["sc"])[:3]
        mvids[v["id"]] = {"t": v["t"][:140], "v": v["v"], "d": v["d"], "p": v["p"], "h": heat,
                          "cv": None if clip_titles is None else clip_matches(words_of(v["t"], name_words), clip_titles)}
    return sorted(moments, key=lambda m: -m["sc"])[:MOMENTS_KEEP], mvids


def long_uploads(yt, uploads, t, n=25):
    """A creator's newest uploads: (long videos from the last 30 days, every video's description, the newest long video)."""
    longs, descs = [], []
    if not uploads:
        return longs, descs
    vids = [x["contentDetails"]["videoId"] for x in yt.call("playlistItems", part="contentDetails", playlistId=uploads, maxResults=n).get("items", [])
            if x.get("contentDetails", {}).get("videoId")]
    if vids:
        for v in yt.call("videos", part="snippet,contentDetails,statistics", id=",".join(vids)).get("items", []):
            sn = v.get("snippet") or {}
            descs.append({"id": v["id"], "desc": sn.get("description") or ""})
            d, pub = parse_duration((v.get("contentDetails") or {}).get("duration")), iso_to_ts(sn.get("publishedAt") or "") or 0
            if d >= 240 and t - pub <= 30 * 86400:
                longs.append({"id": v["id"], "t": (sn.get("title") or "")[:140], "v": int((v.get("statistics") or {}).get("viewCount") or 0), "d": d, "p": int(pub),
                              "desc": sn.get("description") or ""})
    return longs, descs


def clip_permission(channel_desc, videos):
    """('allows'|'forbids'|'unknown', quote, where, video id). A clear 'no' anywhere wins over a 'yes'."""
    found = {}
    for where, text, vid in [("channel", channel_desc or "", None)] + [("video", v.get("desc") or "", v.get("id")) for v in videos]:
        for kind, rx in (("forbids", FORBID_RX), ("allows", ALLOW_RX)):
            m = rx.search(text)
            if m and kind not in found:
                line = next((ln for ln in text.splitlines() if m.group(0) in ln), m.group(0)).strip()
                found[kind] = (kind, line[:160], where, vid)
    return found.get("forbids") or found.get("allows") or ("unknown", "", "", None)


def pacific_day(ts):
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.fromtimestamp(ts, ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:
        return dt.datetime.fromtimestamp(ts - 8 * 3600, dt.timezone.utc).strftime("%Y-%m-%d")


def clip_candidates(regions):
    """Creators behind long videos on the trending charts (the biggest video per channel), minus brands and broadcasters."""
    found = {}
    for region, cats in (regions or {}).items():
        for cat, items in cats.items():
            for x in items:
                if not x.get("ch") or (x.get("d") or 0) < 240:
                    continue
                c = found.setdefault(x["ch"], {"ch": x["ch"], "name": x.get("cht") or "", "regions": set(), "cats": set(), "video": x})
                c["regions"].add(region)
                c["cats"].add(cat)
                if (x.get("v") or 0) > (c["video"].get("v") or 0):
                    c["video"] = x
    return [c for c in found.values() if not (c["cats"] & NOT_CLIPPABLE) and not BRANDS.search(c["name"])]


def name_hit(name, title):
    n = re.sub(r"[^\w]", "", (name or "").lower())
    words = [w for w in re.findall(r"\w{5,}", (name or "").lower())]
    tl = (title or "").lower()
    return (n and n in re.sub(r"[^\w]", "", tl)) or any(w in tl for w in words)


def clip_scores(c):
    """The three checks: a big audience, few clippers, and clips that go viral (with enough long videos to clip from)."""
    big = c["subs"] >= 1_000_000
    few = c["clippers"] <= 8
    viral = (c["clip_med"] >= 50_000 if c["clips"] >= 3 else (c.get("top_views") or 0) >= 1_000_000) and c["long30"] >= 2
    a = min(1, max(0, (math.log10(max(c["subs"], 1)) - 5.5) / 2))
    f = 1 - min(1, c["clippers"] / 25)
    v = min(1, max(0, (math.log10(max(c["clip_med"] if c["clips"] >= 3 else (c.get("top_views") or 1), 1)) - 3.5) / 2.5)) * (1 if c["long30"] >= 2 else 0.6)
    return [big, few, viral], round(100 * (0.35 * a + 0.4 * f + 0.25 * v))


def research_clips(yt, cands, prev_clips, quota, t):
    """Researches the next few creators (one Shorts search each; creators who invite clippers first) and keeps every result for a week."""
    keep = {c["ch"]: c for c in prev_clips if t - c.get("checked", 0) < CLIP_KEEP_DAYS * 86400}
    todo = sorted((c for c in cands if c["ch"] not in keep), key=lambda c: (not c.get("allowed"), -((c.get("video") or {}).get("v") or c.get("subs") or 0)))
    todo = todo[:max(0, min(CLIP_PER_RUN, CLIP_PER_DAY - quota["used"]))]
    if not todo:
        return list(keep.values())
    info = {it["id"]: it for it in yt.call("channels", part="snippet,statistics,contentDetails", id=",".join(c["ch"] for c in todo)).get("items", [])}
    since = dt.datetime.fromtimestamp(t - 30 * 86400, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for c in todo:
        it = info.get(c["ch"])
        if not it:
            continue
        sn, st = it.get("snippet") or {}, it.get("statistics") or {}
        subs = 0 if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount") or 0)
        name = sn.get("title") or c["name"]
        if subs < (ALLOWED_MIN_SUBS if c.get("allowed") else CLIP_MIN_SUBS):
            keep[c["ch"]] = {"ch": c["ch"], "checked": int(t), "small": True}
            continue
        quota["used"] += 1
        found = yt.call("search", part="id", type="video", videoDuration="short", order="viewCount", q=name, publishedAfter=since, maxResults=50)
        ids = [x["id"]["videoId"] for x in found.get("items", []) if x.get("id", {}).get("videoId")]
        shorts = []
        if ids:
            for v in yt.call("videos", part="snippet,statistics,contentDetails", id=",".join(ids)).get("items", []):
                vs, vt = v.get("snippet") or {}, v.get("statistics") or {}
                if vs.get("channelId") != c["ch"] and parse_duration((v.get("contentDetails") or {}).get("duration")) <= 180:
                    shorts.append({"id": v["id"], "t": (vs.get("title") or "")[:140], "v": int(vt.get("viewCount") or 0), "ch": vs.get("channelId"), "cht": (vs.get("channelTitle") or "")[:80]})
        named = [x for x in shorts if name_hit(name, x["t"])]
        clips = sorted(named if len(named) >= 3 else shorts, key=lambda x: -x["v"])
        views = sorted(x["v"] for x in clips)
        uploads = (((it.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"))
        longs, descs = long_uploads(yt, uploads, t)
        newest = max(longs, key=lambda x: x["p"], default=None)
        video = c.get("video") or max(longs, key=lambda x: x["v"], default=None) or {}   # creators found by their invitation have no chart video
        rec = {"ch": c["ch"], "name": name[:80], "avatar": ((sn.get("thumbnails") or {}).get("default") or {}).get("url", ""), "subs": subs,
               "regions": sorted(c["regions"]), "cats": sorted(c["cats"]), "top": {k: video.get(k) for k in ("id", "t", "v")}, "top_views": video.get("v") or 0,
               "clips": len(clips), "clippers": len({x["ch"] for x in clips}), "clip_med": views[len(views) // 2] if views else 0,
               "clip_top": clips[0] if clips else None, "long30": len(longs), "long_hours": round(sum(x["d"] for x in longs) / 3600, 1),
               "latest": {k: newest[k] for k in ("id", "t", "v", "p")} if newest else None, "checked": int(t), "ct": [x["t"][:60] for x in clips[:12]]}
        rec["allow"], rec["allow_quote"], rec["allow_where"], rec["allow_vid"] = clip_permission(sn.get("description"), descs[:12])
        rec["invites"] = rec["allow"] == "allows"
        rec["paid"] = rec["invites"] and bool(PAID_RX.search(rec["allow_quote"]))
        if c.get("allowed"):
            rec["found_by"] = "invite"
        rec["ok"], rec["score"] = clip_scores(rec)
        rec["moments"], rec["mvids"] = find_moments(yt, longs, [x["t"] for x in clips], name)
        rec["mchecked"] = int(t)
        keep[c["ch"]] = rec
    return list(keep.values())


def refresh_permissions(yt, clips, limit=20):
    """Fills in the clipping-permission check for creators researched before it existed (no search needed: about 3 units each)."""
    todo = [c for c in clips if not c.get("small") and "allow" not in c][:limit]
    if not todo:
        return
    info = {it["id"]: it for it in yt.call("channels", part="snippet,contentDetails", id=",".join(c["ch"] for c in todo)).get("items", [])}
    for c in todo:
        it = info.get(c["ch"]) or {}
        uploads = ((it.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        descs = []
        if uploads:
            vids = [x["contentDetails"]["videoId"] for x in yt.call("playlistItems", part="contentDetails", playlistId=uploads, maxResults=12).get("items", [])
                    if x.get("contentDetails", {}).get("videoId")]
            if vids:
                descs = [{"id": v["id"], "desc": (v.get("snippet") or {}).get("description") or ""} for v in yt.call("videos", part="snippet", id=",".join(vids)).get("items", [])]
        c["allow"], c["allow_quote"], c["allow_where"], c["allow_vid"] = clip_permission((it.get("snippet") or {}).get("description"), descs)
        c["invites"] = c["allow"] == "allows"


def refresh_moments(yt, clips, quota, t, per_run=3):
    """Re-reads the newest videos and their comments for creators researched earlier, about once a day each (creators who invite clippers first)."""
    todo = sorted((c for c in clips if not c.get("small") and t - c.get("mchecked", 0) >= 86400), key=lambda c: (c.get("allow") != "allows", -c.get("score", 0)))
    todo = todo[:max(0, min(per_run, MOMENTS_PER_DAY - quota.get("moments", 0)))]
    if not todo:
        return
    info = {it["id"]: it for it in yt.call("channels", part="contentDetails", id=",".join(c["ch"] for c in todo)).get("items", [])}
    for c in todo:
        quota["moments"] = quota.get("moments", 0) + 1
        longs, _ = long_uploads(yt, (((info.get(c["ch"]) or {}).get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"), t, n=15)
        c["moments"], c["mvids"] = find_moments(yt, longs, c.get("ct"), c.get("name") or "")
        newest = max(longs, key=lambda x: x["p"], default=None)
        if newest:
            c["latest"] = {k: newest[k] for k in ("id", "t", "v", "p")}
        c["mchecked"] = int(t)


def discover_allowed(yt, prev, quota, t, turn):
    """One search for a clipping-program phrase (they take turns); a creator is kept only when their own channel or video description invites clippers."""
    keep = {c["ch"]: c for c in prev if t - c.get("found", 0) < ALLOWED_KEEP_DAYS * 86400}
    hour = (t % 86400) / 86400
    if quota.get("disc", 0) >= min(DISCOVER_PER_DAY, int(DISCOVER_PER_DAY * hour) + 1):   # spread over the day
        return list(keep.values()), turn
    kind, q = DISCOVER_QUERIES[turn % len(DISCOVER_QUERIES)]
    quota["disc"], turn = quota.get("disc", 0) + 1, turn + 1
    found, evidence = yt.call("search", part="id", type=kind, q=q, maxResults=50).get("items", []), {}
    if kind == "channel":
        chs = [x["id"]["channelId"] for x in found if x.get("id", {}).get("channelId")]
    else:
        ids = [x["id"]["videoId"] for x in found if x.get("id", {}).get("videoId")]
        for v in (yt.call("videos", part="snippet", id=",".join(ids)).get("items", []) if ids else []):
            sn = v.get("snippet") or {}
            if sn.get("channelId") and ALLOW_RX.search(sn.get("description") or ""):
                evidence.setdefault(sn["channelId"], []).append({"id": v["id"], "desc": sn.get("description") or ""})
        chs = list(evidence)
    chs = [ch for ch in dict.fromkeys(chs) if ch not in keep][:50]
    for it in (yt.call("channels", part="snippet,statistics", id=",".join(chs)).get("items", []) if chs else []):
        sn, st = it.get("snippet") or {}, it.get("statistics") or {}
        subs = 0 if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount") or 0)
        allow, quote, where, vid = clip_permission(sn.get("description"), evidence.get(it["id"], []))
        if subs >= ALLOWED_MIN_SUBS and allow == "allows":
            keep[it["id"]] = {"ch": it["id"], "name": (sn.get("title") or "")[:80], "avatar": ((sn.get("thumbnails") or {}).get("default") or {}).get("url", ""),
                              "subs": subs, "quote": quote, "where": where, "vid": vid, "paid": bool(PAID_RX.search(quote)), "found": int(t)}
    return sorted(keep.values(), key=lambda c: -c["subs"])[:ALLOWED_MAX], turn


def now():
    return time.time() + float(os.environ.get("SCAN_TIME_OFFSET", "0"))


def get_json(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=timeout) as r:
        return json.load(r)


def load_prev(url):
    if not url:
        return {}
    try:
        data = get_json(url)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"no previous trends ({type(e).__name__}); starting fresh", file=sys.stderr)
        return {}


def chart_item(it, rank):
    sn, st, cd = it.get("snippet") or {}, it.get("statistics") or {}, it.get("contentDetails") or {}
    num = lambda k: int(st[k]) if st.get(k) is not None else None   # noqa: E731
    published = iso_to_ts(sn.get("publishedAt") or "")
    return {"id": it.get("id"), "t": (sn.get("title") or "")[:200], "ch": sn.get("channelId") or "", "cht": (sn.get("channelTitle") or "")[:100],
            "v": num("viewCount"), "l": num("likeCount"), "c": num("commentCount"), "p": int(published) if published else None,
            "d": parse_duration(cd.get("duration")), "r": rank}


def youtube_charts(yt, prev_regions, seen, t):
    """The top of YouTube's trending chart for each country and category, with each video's previous rank."""
    out = {}
    for region in REGIONS:
        out[region], old = {}, prev_regions.get(region) or {}
        for cat, cat_id in CATEGORIES.items():
            params = {"part": "snippet,statistics,contentDetails", "chart": "mostPopular", "regionCode": region, "maxResults": KEEP}
            if cat_id:
                params["videoCategoryId"] = cat_id
            try:
                data = yt.call("videos", **params)
            except ApiError as e:   # not every category has a chart in every country
                print(f"chart {region}/{cat}: {e}", file=sys.stderr)
                continue
            before = {x.get("id"): x.get("r") for x in old.get(cat) or []}
            items = []
            for i, it in enumerate((data.get("items") or [])[:KEEP]):
                x = chart_item(it, i + 1)
                if not x["id"]:
                    continue
                x["pr"] = before.get(x["id"])                  # rank in the previous refresh (None = new on the chart)
                x["f"] = seen.setdefault(x["id"], int(t))      # when it first showed up on any chart
                items.append(x)
            if items:
                out[region][cat] = items
    return out


def wiki_day(project, day):
    data = get_json(f"{WIKI_BASE}/{project}/all-access/{day:%Y/%m/%d}")
    return [(a["article"], int(a["views"])) for a in data["items"][0]["articles"]
            if not SKIP_WIKI.match(a.get("article", "")) and not ADULT.search(a.get("article", ""))]


def wikipedia(t):
    """Yesterday's most-read articles in English and Thai, with the day before's views to show what's climbing."""
    today, out = dt.datetime.fromtimestamp(t, dt.timezone.utc).date(), {}
    for lang, project in WIKIS.items():
        for back in (1, 2):   # yesterday's list appears a few hours after midnight UTC
            day = today - dt.timedelta(days=back)
            try:
                cur = wiki_day(project, day)
            except Exception as e:
                print(f"wikipedia {lang} {day}: {type(e).__name__}", file=sys.stderr)
                continue
            try:
                before = dict(wiki_day(project, day - dt.timedelta(days=1)))
            except Exception:
                before = {}
            out[lang] = {"day": day.isoformat(), "items": [{"a": a, "v": v, "pv": before.get(a)} for a, v in cur[:WIKI_KEEP]]}
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--data", default="", help="this run's data.json, for the hot-trend list alerts use")
    args = ap.parse_args()
    t = now()
    prev = load_prev(os.environ.get("PREV_TRENDS_URL", ""))
    yt_prev, wiki_prev = prev.get("yt") or {}, prev.get("wiki") or {}
    seen = {k: v for k, v in (prev.get("seen") or {}).items() if isinstance(v, (int, float)) and t - v < SEEN_DAYS * 86400}
    out = {"generated": int(t), "yt": yt_prev, "wiki": wiki_prev, "hot": prev.get("hot") or {}, "clips": prev.get("clips") or [],
           "allowed": prev.get("allowed") or [], "disc_i": int(prev.get("disc_i") or 0)}
    clipq = prev.get("clipq") or {}
    if clipq.get("day") != pacific_day(t):
        clipq = {"day": pacific_day(t), "used": 0}
    if args.data:
        hot = hot_trends(args.data, t)
        if hot is not None:
            out["hot"] = hot

    key = os.environ.get("YT_API_KEY", "").strip()
    if key and t - (yt_prev.get("updated") or 0) >= YT_EVERY:
        yt = YouTube(key, int(os.environ.get("TRENDS_BUDGET", "40")))
        try:
            regions = youtube_charts(yt, yt_prev.get("regions") or {}, seen, t)
            if any(regions.values()):
                out["yt"] = {"updated": int(t), "regions": regions}
        except QuotaExceeded as e:
            print(f"YouTube charts skipped: {e}", file=sys.stderr)
        except Exception as e:   # keep the previous charts
            print(f"YouTube charts failed: {type(e).__name__}: {e}", file=sys.stderr)
        print(f"YouTube charts: {yt.used} units")
    elif not key:
        print("no YT_API_KEY: keeping the previous YouTube charts", file=sys.stderr)
    if key:   # clip opportunities: creators who invite clippers, a few more creators researched each run, and their best moments
        cy = YouTube(key, int(os.environ.get("CLIP_BUDGET", "120")))
        try:
            out["allowed"], out["disc_i"] = discover_allowed(cy, out["allowed"], clipq, t, out["disc_i"])
        except QuotaExceeded as e:
            print(f"clipping-program search stopped: {e}", file=sys.stderr)
        except Exception as e:
            print(f"clipping-program search failed: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            cands = {c["ch"]: c for c in clip_candidates((out["yt"] or {}).get("regions"))}
            for a in out["allowed"]:
                cands.setdefault(a["ch"], {"ch": a["ch"], "name": a["name"], "regions": set(), "cats": set(), "video": None, "subs": a["subs"]})["allowed"] = True
            out["clips"] = research_clips(cy, list(cands.values()), out["clips"], clipq, t)
            refresh_permissions(cy, out["clips"])
            refresh_moments(cy, out["clips"], clipq, t)
        except QuotaExceeded as e:
            print(f"clip research stopped: {e}", file=sys.stderr)
        except Exception as e:   # keep what was researched before
            print(f"clip research failed: {type(e).__name__}: {e}", file=sys.stderr)
        print(f"clip research: {cy.searches} searches, {cy.used} units")
    out["clipq"] = clipq

    if t - (wiki_prev.get("checked") or 0) >= WIKI_EVERY:
        try:
            w = wikipedia(t)
            if w:
                out["wiki"] = dict(w, checked=int(t))
        except Exception as e:
            print(f"Wikipedia failed: {type(e).__name__}: {e}", file=sys.stderr)

    out["seen"] = seen
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    charts = sum(len(v) for v in (out["yt"].get("regions") or {}).values())
    print(f"trends: {charts} YouTube charts, Wikipedia {', '.join(k for k in WIKIS if k in out['wiki']) or 'none'}, {len(out['hot'])} hot trends, "
          f"{sum(1 for c in out['clips'] if not c.get('small'))} clip creators")


if __name__ == "__main__":
    main()
