#!/usr/bin/env python3
"""Trend feeds for the site: what's trending on YouTube by country and category, and what people are reading about on Wikipedia.

Usage: python trends.py --out _site/trends.json [--data _site/data.json] [--state state/]
Env:   YT_API_KEY (the scanner's key), PREV_TRENDS_URL (the live site's trends.json), STATE_DIR (the private folder the workflow caches)

History between runs comes from the saved state folder or the live site, whichever is newer. If neither can be read the script
refuses to run (exit code 2) rather than publish feeds that start from nothing; ALLOW_SEED=1 allows a fresh start on a new site.

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
import unicodedata
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # works however Python is started (including isolated mode)
from yt_api import ApiError, QuotaExceeded, SearchQuotaExceeded, YouTube, iso_to_ts, parse_duration  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TRENDS_DAILY_UNITS = int(os.environ.get("TRENDS_DAILY_UNITS", "1200"))   # this script's share of the 10,000 daily units (scan.py keeps to 8,000)

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
def load_words():
    """Word lists and the curated game and cartoon names, from the trendWords block in index.html (the page reads the same block)."""
    html = (ROOT / "outlier-radar" / "static" / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<script type="application/json" id="trendWords">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


_LISTS = load_words()
GAMES = [tuple(x) for x in _LISTS["games"]]
CARTOONS = [tuple(x) for x in _LISTS["cartoons"]]
WORDS = {k: set(_LISTS[k]) for k in ("noise", "generic", "stop")}
RECENT, WINDOW = 3 * 86400, 21 * 86400
NO_SPACES = re.compile(r"[฀-๿぀-ヿ㐀-鿿가-힯]")   # scripts without spaces (Thai, Japanese, Chinese, Korean): only their hashtags count
CASED = re.compile(r"[A-Za-zÀ-ɏḀ-ỿͰ-ϿЀ-ӿ]")   # Latin, Greek and Cyrillic letters: scripts with capital letters
DROP_MARKS = re.compile("[\ufe0e\ufe0f\u200d\u20e3]")   # emoji variation selectors, joiners and keycaps
APOS = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'"})


def _word_char(c, tag=False):
    return c.isalnum() or unicodedata.category(c).startswith("M") or (c == "_" if tag else c == "'")


def _runs(text):
    out, cur = [], []
    for c in text:
        if _word_char(c):
            cur.append(c)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def split_title(title):
    """(hashtags, word tokens with their case) the way the page splits a title for trends."""
    t = DROP_MARKS.sub("", str(title or "").translate(APOS))
    tags, rest, i = [], [], 0
    while i < len(t):
        if t[i] == "#":
            j = i + 1
            while j < len(t) and _word_char(t[j], True):
                j += 1
            if j - i - 1 >= 2:
                tags.append(t[i:j].lower())
                rest.append(" ")
                i = j
                continue
        rest.append(t[i])
        i += 1
    toks = []
    for w in _runs("".join(rest)):
        w = w.strip("'")
        if w and any(c.isalnum() for c in w):
            toks.append(w)
    return tags, toks


def word_ok(w):
    return bool(w) and len(w) >= 2 and not re.fullmatch(r"[0-9]+", w) and w not in WORDS["stop"] and w not in WORDS["noise"] and w not in WORDS["generic"]


def title_terms(title):
    """Hashtags, words and two-word phrases, lowercased, the way the page counts them (words only for languages that use spaces)."""
    tags, toks = split_title(title)
    out = {x for x in tags if x[1:] not in WORDS["noise"]}
    words = [None if NO_SPACES.search(w) else w.lower() for w in toks]
    for i, w in enumerate(words):
        if not word_ok(w):
            continue
        out.add(w)
        if i + 1 < len(words) and word_ok(words[i + 1]):
            out.add(w + " " + words[i + 1])
    return out


def caps_in(title):
    """Is the title an ordinary sentence (not ALL CAPS or Title Case), and which words in it start with a capital?"""
    _, toks = split_title(title)
    cased = [w for w in toks if w.lower() != w.upper()]
    caps = [w for w in cased if w[0].isupper()]
    return len(cased) >= 3 and len(caps) / len(cased) <= 0.6, {w.lower() for i, w in enumerate(toks) if i > 0 and w[0].isupper()}


def coverage_weight(series, now):
    """How much one channel's older uploads count toward a term's baseline. Only its newest 24 Shorts are kept, so a channel that posts
    several a day shows no history past a few days; its baseline is scaled up to 18 days, or the channel is left out when there is
    too little history to tell what is new. Returns None to leave the channel out."""
    dated = [v["p"] for v in series if v.get("p")]
    if not dated:
        return None
    days = 21.0 if len(series) < 24 else min(21.0, (now - min(dated)) / 86400)
    if days < 4:
        return None
    return 1.0 if days >= 21 else min(6.0, 18 / (days - 3))


def trend_ref(c, now):
    """Ages count from the channel's own last check, so a channel checked yesterday still has a full 3-day window.
    A channel not checked for 2 days can't say what is recent and is left out (None)."""
    ref = c.get("last_probe") or now
    return None if now - ref > 2 * 86400 else ref


def lift_of(r, b):
    """Shorts in the last 3 days against what the 18 days before predict, pulled toward 1 when the counts are small."""
    return (r + 2) / (b / 6 + 2)


def is_hot(r, chans, lift):
    return r >= 4 and chans >= 2 and lift >= 2


def hot_trends(data_path, t):
    """Trends taking off right now (the alert list): at least 4 new Shorts from 2+ channels in 3 days and twice the usual rate.
    Keys and rules match the page's, so an alert never names a trend the page doesn't show as hot."""
    try:
        data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    now = data.get("generated") or t
    stats = {}
    boards = [("g:" + n, re.compile(rx, re.I)) for n, rx in GAMES] + [("c:" + n, re.compile(rx, re.I)) for n, rx in CARTOONS]

    def add(key, age, ch, w):
        s = stats.setdefault(key, [0, 0.0, set()])
        if age <= RECENT:
            s[0] += 1
            s[2].add(ch)
        else:
            s[1] += w
    for c in data.get("channels", []):
        series = c.get("series", [])
        ref = trend_ref(c, now)
        w = None if ref is None else coverage_weight(series, ref)
        if w is None:
            continue
        for v in series:
            age = ref - (v.get("p") or 0)
            if not v.get("p") or age < 0 or age > WINDOW:
                continue
            for term in title_terms(v.get("t")):
                add("t:" + term, age, c["ch_id"], w)
            for key, rx in boards:
                if rx.search(v.get("t") or ""):
                    add(key, age, c["ch_id"], w)
    hot = {}
    for key, (r, b, chs) in stats.items():
        lift = lift_of(r, b)
        if is_hot(r, len(chs), lift):
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
ALLOWED_KEEP_DAYS, ALLOWED_MIN_SUBS, ALLOWED_MAX = 28, 100_000, 150   # kept only while re-checked within 28 days

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
        mvids[v["id"]] = {"t": v["t"][:140], "v": v["v"], "d": v["d"], "p": v["p"], "h": heat, **({"k": 1} if v.get("k") else {}),
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
        for v in yt.call("videos", part="snippet,contentDetails,statistics,status", id=",".join(vids)).get("items", []):
            sn = v.get("snippet") or {}
            descs.append({"id": v["id"], "desc": sn.get("description") or ""})
            d, pub = parse_duration((v.get("contentDetails") or {}).get("duration")), iso_to_ts(sn.get("publishedAt") or "") or 0
            if d >= 240 and t - pub <= 30 * 86400:
                longs.append({"id": v["id"], "t": (sn.get("title") or "")[:140], "v": int((v.get("statistics") or {}).get("viewCount") or 0), "d": d, "p": int(pub),
                              "desc": sn.get("description") or "", **mfk(v)})
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


def pacific_now(ts):
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.fromtimestamp(ts, ZoneInfo("America/Los_Angeles"))
    except Exception:
        return dt.datetime.fromtimestamp(ts - 8 * 3600, dt.timezone.utc)


def pacific_day(ts):
    return pacific_now(ts).strftime("%Y-%m-%d")


def day_fraction(ts):
    t = pacific_now(ts)
    return (t.hour * 3600 + t.minute * 60 + t.second) / 86400


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
    todo = todo[:max(0, min(CLIP_PER_RUN, CLIP_PER_DAY - quota["used"]))] if not quota.get("search_out") else []
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
        try:
            found = yt.call("search", part="id", type="video", videoDuration="short", order="viewCount", q=name, publishedAfter=since, maxResults=50)
        except SearchQuotaExceeded:   # the day's searches are gone: keep what is researched, try the rest tomorrow
            quota["search_out"] = True
            break
        quota["used"] += 1
        ids = [x["id"]["videoId"] for x in found.get("items", []) if x.get("id", {}).get("videoId")]
        shorts = []
        if ids:
            for v in yt.call("videos", part="snippet,statistics,contentDetails,status", id=",".join(ids)).get("items", []):
                vs, vt = v.get("snippet") or {}, v.get("statistics") or {}
                if vs.get("channelId") != c["ch"] and parse_duration((v.get("contentDetails") or {}).get("duration")) <= 180:
                    shorts.append({"id": v["id"], "t": (vs.get("title") or "")[:140], "v": int(vt.get("viewCount") or 0), "ch": vs.get("channelId"), "cht": (vs.get("channelTitle") or "")[:80], **mfk(v)})
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
               "latest": {k: newest[k] for k in ("id", "t", "v", "p", "k") if k in newest} if newest else None, "checked": int(t), "ct": [x["t"][:60] for x in clips[:12]]}
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
            c["latest"] = {k: newest[k] for k in ("id", "t", "v", "p", "k") if k in newest}
        c["mchecked"] = int(t)


def discover_allowed(yt, prev, quota, t, turn):
    """One search for a clipping-program phrase (they take turns); a creator is kept only when their own channel or video description invites clippers."""
    keep = {c["ch"]: c for c in prev if t - c.get("checked", c.get("found", 0)) < ALLOWED_KEEP_DAYS * 86400}
    stale = [c for c in keep.values() if t - c.get("checked", c.get("found", 0)) >= 7 * 86400][:50]
    if stale:   # weekly re-check of what they say and how big they are (1 unit for up to 50 creators)
        info = {it["id"]: it for it in yt.call("channels", part="snippet,statistics", id=",".join(c["ch"] for c in stale)).get("items", [])}
        for c in stale:
            it = info.get(c["ch"])
            sn, st = (it or {}).get("snippet") or {}, (it or {}).get("statistics") or {}
            subs = 0 if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount") or 0)
            still = it is not None and subs >= ALLOWED_MIN_SUBS
            if still and c.get("where") == "channel":   # their invitation was in the channel description: it must still be there
                allow, quote, _, _ = clip_permission(sn.get("description"), [])
                still = allow == "allows"
                if still:
                    c["quote"], c["paid"] = quote, bool(PAID_RX.search(quote))
            if not still:
                keep.pop(c["ch"], None)
                continue
            c.update(subs=subs, name=(sn.get("title") or c.get("name", ""))[:80], checked=int(t),
                     avatar=((sn.get("thumbnails") or {}).get("default") or {}).get("url", c.get("avatar", "")))
    if quota.get("search_out") or quota.get("disc", 0) >= min(DISCOVER_PER_DAY, int(DISCOVER_PER_DAY * day_fraction(t)) + 1):   # spread over the Pacific day
        return list(keep.values()), turn
    kind, q = DISCOVER_QUERIES[turn % len(DISCOVER_QUERIES)]
    try:
        found, evidence = yt.call("search", part="id", type=kind, q=q, maxResults=50).get("items", []), {}
    except SearchQuotaExceeded:
        quota["search_out"] = True
        return list(keep.values()), turn
    quota["disc"], turn = quota.get("disc", 0) + 1, turn + 1
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
                              "subs": subs, "quote": quote, "where": where, "vid": vid, "paid": bool(PAID_RX.search(quote)), "found": int(t), "checked": int(t)}
    return sorted(keep.values(), key=lambda c: -c["subs"])[:ALLOWED_MAX], turn


def now():
    return time.time() + float(os.environ.get("SCAN_TIME_OFFSET", "0"))


def get_json(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=timeout) as r:
        return json.load(r)


def load_prev(url, state_dir, allow_seed):
    """The newest previous trends: the saved state folder or the live site. Returns (data, source) or (None, reason)."""
    found = []
    if state_dir and (Path(state_dir) / "trends.json").exists():
        try:
            d = json.loads((Path(state_dir) / "trends.json").read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("generated"):
                found.append((d["generated"], 1, d, "saved state"))
        except Exception as e:
            print(f"saved trends unreadable ({type(e).__name__})", file=sys.stderr)
    if url:
        try:
            d = get_json(url)
            if isinstance(d, dict) and d.get("generated"):
                found.append((d["generated"], 0, d, "live site"))
        except Exception as e:
            print(f"no previous trends from the site ({type(e).__name__})", file=sys.stderr)
    if found:
        _, _, d, source = max(found, key=lambda x: (x[0], x[1]))
        return d, source
    if allow_seed:
        return {}, "fresh start"
    return None, "no saved trends and the live site could not be read"


def mfk(it):
    """YouTube's Made for Kids flag ({"k": 1}); the developer policies require looking it up for every video a site embeds."""
    return {"k": 1} if (it.get("status") or {}).get("madeForKids") else {}


def chart_item(it, rank):
    sn, st, cd = it.get("snippet") or {}, it.get("statistics") or {}, it.get("contentDetails") or {}
    num = lambda k: int(st[k]) if st.get(k) is not None else None   # noqa: E731
    published = iso_to_ts(sn.get("publishedAt") or "")
    return {"id": it.get("id"), "t": (sn.get("title") or "")[:200], "ch": sn.get("channelId") or "", "cht": (sn.get("channelTitle") or "")[:100],
            "v": num("viewCount"), "l": num("likeCount"), "c": num("commentCount"), "p": int(published) if published else None,
            "d": parse_duration(cd.get("duration")), "r": rank, **mfk(it)}


def youtube_charts(yt, prev_regions, seen, t):
    """The top of YouTube's trending chart for each country and category, with each video's previous rank."""
    out = {}
    for region in REGIONS:
        out[region], old = {}, prev_regions.get(region) or {}
        for cat, cat_id in CATEGORIES.items():
            params = {"part": "snippet,statistics,contentDetails,status", "chart": "mostPopular", "regionCode": region, "maxResults": KEEP}
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
    ap.add_argument("--state", default=os.environ.get("STATE_DIR", ""), help="private folder kept between runs (the workflow caches it)")
    args = ap.parse_args()
    t = now()
    prev, source = load_prev(os.environ.get("PREV_TRENDS_URL", ""), args.state, os.environ.get("ALLOW_SEED", "").strip().lower() in ("1", "true", "yes"))
    if prev is None:
        print(f"REFUSING TO RUN: {source}. Nothing was written, so the site keeps its last good trends.", file=sys.stderr)
        sys.exit(2)
    print(f"trends history from {source}")
    yt_prev, wiki_prev = prev.get("yt") or {}, prev.get("wiki") or {}
    seen = {k: v for k, v in (prev.get("seen") or {}).items() if isinstance(v, (int, float)) and t - v < SEEN_DAYS * 86400}
    out = {"generated": int(t), "yt": yt_prev, "wiki": wiki_prev, "hot": prev.get("hot") or {}, "clips": prev.get("clips") or [],
           "allowed": prev.get("allowed") or [], "disc_i": int(prev.get("disc_i") or 0)}
    clipq = prev.get("clipq") or {}
    if clipq.get("day") != pacific_day(t):
        clipq = {"day": pacific_day(t), "used": 0}

    def units_left():
        return max(0, TRENDS_DAILY_UNITS - clipq.get("units", 0))
    if args.data:
        hot = hot_trends(args.data, t)
        if hot is not None:
            out["hot"] = hot

    key = os.environ.get("YT_API_KEY", "").strip()
    if key and t - (yt_prev.get("updated") or 0) >= YT_EVERY and units_left() >= 24:
        yt = YouTube(key, min(int(os.environ.get("TRENDS_BUDGET", "40")), units_left()))
        try:
            regions = youtube_charts(yt, yt_prev.get("regions") or {}, seen, t)
            if any(regions.values()):
                out["yt"] = {"updated": int(t), "regions": regions}
        except QuotaExceeded as e:
            print(f"YouTube charts skipped: {e}", file=sys.stderr)
        except Exception as e:   # keep the previous charts
            print(f"YouTube charts failed: {type(e).__name__}: {e}", file=sys.stderr)
        clipq["units"] = clipq.get("units", 0) + yt.used
        print(f"YouTube charts: {yt.used} units")
    elif not key:
        print("no YT_API_KEY: keeping the previous YouTube charts", file=sys.stderr)
    if key and units_left() > 0:   # clip opportunities: creators who invite clippers, a few more creators researched each run, and their best moments
        cy = YouTube(key, min(int(os.environ.get("CLIP_BUDGET", "120")), units_left()))
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
        clipq["units"] = clipq.get("units", 0) + cy.used
        print(f"clip research: {cy.searches} searches, {cy.used} units (today {clipq['units']}/{TRENDS_DAILY_UNITS})")
    out["clipq"] = clipq

    if t - (wiki_prev.get("checked") or 0) >= WIKI_EVERY:
        try:
            w = wikipedia(t)
            if w:
                out["wiki"] = dict(w, checked=int(t))
        except Exception as e:
            print(f"Wikipedia failed: {type(e).__name__}: {e}", file=sys.stderr)

    out["seen"] = seen
    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    for path in [Path(args.out)] + ([Path(args.state) / "trends.json"] if args.state else []):   # the site, and the copy the next run starts from
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    charts = sum(len(v) for v in (out["yt"].get("regions") or {}).values())
    print(f"trends: {charts} YouTube charts, Wikipedia {', '.join(k for k in WIKIS if k in out['wiki']) or 'none'}, {len(out['hot'])} hot trends, "
          f"{sum(1 for c in out['clips'] if not c.get('small'))} clip creators")


if __name__ == "__main__":
    main()
