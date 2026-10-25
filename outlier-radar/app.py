#!/usr/bin/env python3
"""Outlier Radar: a local dashboard that keeps scanning YouTube Shorts for outlier channels and videos.

Needs only Python 3.9+ and yt-dlp (no other packages).  Run:  python app.py
Then open http://localhost:8765   (add --share to let people on your Wi-Fi view it read-only)
"""
import argparse
import collections
import json
import math
import os
import random
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "radar.db"
CONFIG_PATH = DATA_DIR / "config.json"
AVATAR_DIR = DATA_DIR / "avatars"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
SEED_PATH = APP_DIR / "seed_stage1.json"
PICKS_PATH = APP_DIR / "picks.json"
INDEX_PATH = APP_DIR / "static" / "index.html"

# YouTube search filter params: "this week / this month" + "under 4 min", with and without "sort by views".
SORT_SP = {"week": "CAMSBAgDGAE%3D", "month": "CAMSBAgEGAE%3D"}
PLAIN_SP = {"week": "EgQIAxgB", "month": "EgQIBBgB"}

DEFAULT_QUERIES = [
    # story / animation
    "ai animation story", "ai cartoon story", "3d animation story", "animated story roblox", "minecraft animation",
    "brainrot animation", "fortnite animation", "football animation", "basketball animation", "cat story animation",
    "dog rescue story ai", "baby animals ai", "ai food", "stickman animation", "cute animals", "ai story animation",
    "pov animation story", "roblox story animation", "steal a brainrot animation", "grow a garden animation",
    "poor vs rich animation", "rags to riches animation", "survival story animation", "ai transformation",
    "ai pet salon", "ai asmr", "scary story animation", "horror story animation", "reddit story animation",
    "storytime animation", "funny animation",
    # satisfying / process
    "satisfying cutting", "satisfying process", "asmr food", "asmr cleaning", "restoration", "factory process",
    "hydraulic press", "crushing things", "giant objects", "slime", "3d printing", "woodworking", "diy crafts",
    "magic trick", "optical illusion", "glow up transformation",
    # facts / education
    "history facts", "space facts", "ocean facts", "animals facts", "dinosaurs", "psychology facts",
    "dark psychology", "science experiment", "chemistry experiment", "what if animation",
    "what would happen if animation", "geography animation", "country comparison", "history animation",
    "map animation", "did you know",
    # money / self improvement
    "money tips", "personal finance shorts", "investing shorts", "motivational story", "life hack", "cooking hack",
    "recipe 30 seconds", "fitness", "gym motivation", "relationship advice",
    # entertainment / games / sport
    "comedy skit", "prank", "challenge", "survival challenge", "build challenge minecraft", "valorant", "chess",
    "soccer skills", "nba shorts", "sports highlights",
    # nature / pets
    "wildlife rescue", "animal rescue", "fishing", "dog", "cat", "bird", "monkey", "snake", "shark", "farm",
    # lifestyle
    "mansion", "luxury", "supercar", "car restoration",
]
DEFAULT_CONFIG = {
    "queries": DEFAULT_QUERIES,
    "interval_minutes": 20,
    "workers": 4,
    "max_queries_per_cycle": 60,
    "max_probes_per_cycle": 250,
    "min_hit_views": 300_000,
    "probe_non_english": False,
}

DB_LOCK = threading.RLock()
CFG_LOCK = threading.Lock()
SCAN_NOW = threading.Event()
LOG = collections.deque(maxlen=80)
STATUS = {
    "state": "starting", "phase": "", "done": 0, "total": 0, "cycles": 0, "errors": 0,
    "cycle_started": None, "last_cycle_end": None, "next_scan": None, "message": "",
}
ARGS = None
_YTDLP = None


# ----------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------
def log(msg):
    LOG.append(f"{time.strftime('%H:%M:%S')}  {msg}")
    STATUS["message"] = msg


def ytdlp_base():
    global _YTDLP
    if _YTDLP is None:
        exe = shutil.which("yt-dlp")
        _YTDLP = [exe] if exe else [sys.executable, "-m", "yt_dlp"]
    return _YTDLP


def run_ytdlp(args, timeout=150):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(
            ytdlp_base() + args + ["--no-warnings", "--socket-timeout", "20"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=flags, cwd=str(Path.home()),
        )
        return p.stdout
    except Exception:
        return ""


def clip(x, lo, hi):
    return max(lo, min(hi, x))


def mostly_english(s):
    letters = [c for c in (s or "") if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) >= 0.9


_SCRIPTS = [
    ("ru", "Ѐ-ӿ"), ("ar", "؀-ۿ"), ("hi", "ऀ-ॿ"), ("bn", "ঀ-৿"), ("th", "฀-๿"),
    ("ko", "가-힯"), ("ja", "぀-ヿ"), ("zh", "一-鿿"), ("ta", "஀-௿"), ("te", "ఀ-౿"),
    ("he", "֐-׿"), ("el", "Ͱ-Ͽ"),
]
_SCRIPT_RX = [(code, re.compile(f"[{rng}]")) for code, rng in _SCRIPTS]
_STOP = {
    "en": "the a an and of to in is it you your for with this that what if can how why who when are was be but not on at from i my we they he she his her one all will would could just do did so out up about into than then there their them our over".split(),
    "es": "el la los las un una y de del que en es por con para su sus como pero se lo al muy más mi tu qué cómo cuando porque este esta eso son era fue hay si ya".split(),
    "pt": "o os as um uma e do da dos das que em é por com para seu sua como mas se não muito mais meu você porque este esta isso são foi tem na no nos nas ele ela eu".split(),
    "id": "yang dan di ke dari ini itu untuk dengan pada adalah tidak ada akan juga saya kamu dia mereka kami apa bagaimana kenapa kalau bisa lebih sangat sudah belum atau karena fakta".split(),
    "tr": "ve bir bu için ile çok de da ne gibi ama daha en mi var yok ben sen biz siz".split(),
    "fr": "le la les un une et du des que est pour avec sur dans pas ce cette il elle je tu nous vous qui mais plus très".split(),
    "de": "der die das und ist zu den mit für auf nicht ein eine er sie ich du wir von dem auch aber wie was".split(),
    "it": "il lo gli le un una e di del della che è per con su non si come ma più molto anche".split(),
}
_STOPSETS = {k: set(v) for k, v in _STOP.items()}


def detect_lang(texts):
    """Cheap language guess from titles/description: script first, then common words. 'und' when unsure."""
    text = re.sub(r"#\w+|https?://\S+", " ", " ".join(t for t in texts if t))
    letters = sum(1 for c in text if c.isalpha())
    if not letters:
        return "und"
    counts = {code: len(rx.findall(text)) for code, rx in _SCRIPT_RX}
    if counts["ja"] >= 0.1 * letters:
        return "ja"
    code, cnt = max(counts.items(), key=lambda kv: kv[1])
    if cnt >= 0.3 * letters:
        return code
    words = re.findall(r"[a-zà-ÿ']+", text.lower())
    scores = {k: sum(1 for w in words if w in s) for k, s in _STOPSETS.items()}
    best, bs = max(scores.items(), key=lambda kv: kv[1])
    return best if bs >= 2 else "und"


def load_config():
    with CFG_LOCK:
        cfg = dict(DEFAULT_CONFIG)
        if CONFIG_PATH.exists():
            try:
                cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        return cfg


def save_config(cfg):
    with CFG_LOCK:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=1), encoding="utf-8")


# ----------------------------------------------------------------------------------------------
# database
# ----------------------------------------------------------------------------------------------
def db_connect():
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS channels(
          ch_id TEXT PRIMARY KEY, name TEXT, handle TEXT, subs INTEGER DEFAULT 0,
          hit_views INTEGER DEFAULT 0, hit_title TEXT, hit_vid TEXT, queries TEXT DEFAULT '[]',
          first_seen REAL, last_seen REAL, last_probe REAL, probe_fail INTEGER DEFAULT 0,
          n INTEGER, med_recent REAL, med_all REAL, max_views INTEGER, share_100k REAL, share_1m REAL,
          trend REAL, peak_vs_med REAL, ratio REAL, score REAL, tags TEXT DEFAULT '[]',
          english INTEGER DEFAULT 1, series TEXT DEFAULT '[]', watch INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS hist(vid TEXT, ts REAL, views INTEGER);
        CREATE INDEX IF NOT EXISTS hist_vid ON hist(vid, ts);
        CREATE TABLE IF NOT EXISTS qstats(
          query TEXT PRIMARY KEY, runs INTEGER DEFAULT 0, last_run REAL DEFAULT 0,
          last_rows INTEGER DEFAULT 0, total_rows INTEGER DEFAULT 0);
        """
    )
    have = {r["name"] for r in con.execute("PRAGMA table_info(channels)")}
    for col in ("avatar", "lang", "descr"):
        if col not in have:
            con.execute(f"ALTER TABLE channels ADD COLUMN {col} TEXT")
    con.commit()
    return con


DB = None


def q(sql, params=(), one=False, commit=False):
    with DB_LOCK:
        cur = DB.execute(sql, params)
        if commit:
            DB.commit()
        return (cur.fetchone() if one else cur.fetchall()) if not commit else None


# ----------------------------------------------------------------------------------------------
# scoring
# ----------------------------------------------------------------------------------------------
def compute_metrics(vids, subs):
    """vids: newest-first list of {'id','t','v'}. Returns metrics dict or None."""
    views = [x["v"] for x in vids]
    n = len(views)
    if n < 6:
        return None
    med_recent = statistics.median(views[:12])
    med_all = statistics.median(views)
    mx = max(views)
    share_100k = sum(v >= 100_000 for v in views) / n
    share_1m = sum(v >= 1_000_000 for v in views) / n
    mid = views[3:12] or views
    old = views[12:] or views
    trend = (statistics.median(mid) + 1) / (statistics.median(old) + 1)
    peak_vs_med = mx / max(med_all, 1)
    ratio = med_recent / max(subs or 0, 1000)

    engine = 30 * clip((math.log10(max(med_recent, 1)) - 4.7) / (6.0 - 4.7), 0, 1)       # 50K -> 0, 1M -> 30
    leverage = 35 * clip(math.log10(max(ratio, 1)) / math.log10(30), 0, 1)                # 1 view/sub -> 0, 30+ -> 35
    consistency = 15 * share_100k + 10 * clip(share_1m / 0.25, 0, 1)
    trend_s = 10 * clip((math.log2(max(trend, 0.05)) + 1) / 2, 0, 1)                      # 0.5x -> 0, 1x -> 5, 2x+ -> 10
    fluke = -15 if peak_vs_med > 30 else (-8 if peak_vs_med > 15 else 0)
    score = clip(engine + leverage + consistency + trend_s + fluke, 0, 100)

    tags = []
    if share_100k >= 0.6 and peak_vs_med <= 15 and med_recent >= 150_000:
        tags.append("Consistent engine")
    if peak_vs_med > 30 or (share_100k < 0.2 and mx >= 1_000_000):
        tags.append("One-hit")
    if subs and subs < 25_000 and med_recent >= 200_000:
        tags.append("Tiny & huge")
    if trend >= 1.4:
        tags.append("Rising")
    elif trend <= 0.6:
        tags.append("Fading")

    for x in vids:
        x["x"] = round(x["v"] / max(med_all, 1000), 1)
    return {
        "n": n, "med_recent": med_recent, "med_all": med_all, "max_views": mx, "share_100k": round(share_100k, 2),
        "share_1m": round(share_1m, 2), "trend": round(trend, 2), "peak_vs_med": round(peak_vs_med, 1),
        "ratio": round(ratio, 1), "score": round(score, 1), "tags": tags,
    }


# ----------------------------------------------------------------------------------------------
# scanning
# ----------------------------------------------------------------------------------------------
def search_rows(query, sp):
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}&sp={sp}"
    return parse_search_lines(run_ytdlp([url, "--flat-playlist", "--playlist-end", "40", "--dump-json"]), query)


def plain_search_rows(query, term):
    """Unfiltered YouTube search (works for any phrase and language). Only short-form results are kept."""
    return parse_search_lines(run_ytdlp([f"ytsearch50:{term}", "--flat-playlist", "--dump-json"]), query)


def parse_search_lines(out, query):
    rows = []
    for ln in out.splitlines():
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        is_short = "/shorts/" in (d.get("url") or "")
        dur = d.get("duration")
        if not (is_short or (dur and dur <= 180)):
            continue
        if not d.get("channel_id") or d.get("view_count") is None:
            continue
        rows.append({
            "id": d.get("id"), "title": d.get("title") or "", "views": int(d["view_count"]), "ch": d.get("channel") or d.get("uploader"),
            "ch_id": d["channel_id"], "handle": d.get("uploader_id"), "q": query,
        })
    return rows


def discover(query, span):
    for sp in (SORT_SP[span], PLAIN_SP[span]):
        rows = search_rows(query, sp)
        if rows:
            return rows
    # YouTube's filtered search returns nothing for many phrases; fall back to a plain search once per query
    return plain_search_rows(query, f"{query} shorts") if span == "month" else []


def ingest_rows(rows):
    """Aggregate discovery rows per channel and upsert (keeps best hit, merges niches)."""
    agg = {}
    for r in rows:
        a = agg.setdefault(r["ch_id"], {"name": r["ch"], "handle": r["handle"], "hit": 0, "title": "", "vid": "", "qs": set()})
        a["qs"].add(r["q"])
        if r["views"] > a["hit"]:
            a["hit"], a["title"], a["vid"] = r["views"], r["title"], r["id"]
    now = time.time()
    with DB_LOCK:
        for ch_id, a in agg.items():
            old = DB.execute("SELECT queries, hit_views FROM channels WHERE ch_id=?", (ch_id,)).fetchone()
            if old:
                qs = sorted(set(json.loads(old["queries"])) | a["qs"])
                best = a["hit"] > (old["hit_views"] or 0)
                DB.execute(
                    "UPDATE channels SET name=COALESCE(?,name), handle=COALESCE(?,handle), queries=?, last_seen=?,"
                    " hit_views=MAX(hit_views,?), hit_title=CASE WHEN ? THEN ? ELSE hit_title END,"
                    " hit_vid=CASE WHEN ? THEN ? ELSE hit_vid END WHERE ch_id=?",
                    (a["name"], a["handle"], json.dumps(qs), now, a["hit"], best, a["title"], best, a["vid"], ch_id),
                )
            else:
                DB.execute(
                    "INSERT INTO channels(ch_id,name,handle,hit_views,hit_title,hit_vid,queries,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?)",
                    (ch_id, a["name"], a["handle"], a["hit"], a["title"], a["vid"], json.dumps(sorted(a["qs"])), now, now),
                )
        DB.commit()
    return len(agg)


def rescore_all():
    """Recompute stored metrics from saved series so scores always match the current formula."""
    with DB_LOCK:
        for r in DB.execute("SELECT ch_id, subs, series, descr FROM channels WHERE series IS NOT NULL AND series!='[]' AND n IS NOT NULL").fetchall():
            vids = json.loads(r["series"])
            m = compute_metrics(vids, r["subs"])
            if not m:
                continue
            lang = detect_lang([v["t"] for v in vids] + [r["descr"] or ""])
            DB.execute(
                "UPDATE channels SET n=?, med_recent=?, med_all=?, max_views=?, share_100k=?, share_1m=?, trend=?, peak_vs_med=?, ratio=?,"
                " score=?, tags=?, series=?, lang=? WHERE ch_id=?",
                (m["n"], m["med_recent"], m["med_all"], m["max_views"], m["share_100k"], m["share_1m"], m["trend"], m["peak_vs_med"],
                 m["ratio"], m["score"], json.dumps(m["tags"]), json.dumps(vids), lang, r["ch_id"]),
            )
        DB.commit()


def import_seed():
    if not SEED_PATH.exists() or q("SELECT COUNT(*) c FROM channels", one=True)["c"]:
        return
    try:
        rows = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    good = []
    for r in rows:
        if r.get("ch_id") and r.get("views") is not None and (r.get("is_short") or (r.get("dur") and r["dur"] <= 180)):
            good.append({"id": r.get("id"), "title": r.get("title") or "", "views": int(r["views"]), "ch": r.get("ch"),
                         "ch_id": r["ch_id"], "handle": r.get("handle"), "q": r.get("q") or ""})
    n = ingest_rows(good)
    log(f"Imported {n} channels from the starter scan")


def fetch_channel(url):
    out = run_ytdlp([url, "--flat-playlist", "--playlist-end", "24", "--dump-single-json"])
    try:
        d = json.loads(out)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    vids = []
    for e in d.get("entries") or []:
        if isinstance(e, dict) and e.get("view_count") is not None and e.get("id"):
            vids.append({"id": e["id"], "t": e.get("title") or "", "v": int(e["view_count"])})
    # The profile picture is the square thumbnail; banners are wide and share the same host.
    avatar = ""
    cands = [t for t in (d.get("thumbnails") or []) if re.match(r"https://(yt3\.googleusercontent\.com|yt3\.ggpht\.com)/", t.get("url") or "")]
    best = next((t for t in cands if t.get("width") and t.get("width") == t.get("height")), None) or \
        next((t for t in cands if t.get("id") == "avatar_uncropped"), None)
    if best:
        avatar = re.sub(r"=[^=/?#]*$", "", best["url"]) + "=s900-c-k-c0x00ffffff-no-rj"
    return {
        "ch_id": d.get("channel_id") or d.get("id"), "name": d.get("channel") or d.get("uploader"), "handle": d.get("uploader_id"),
        "subs": d.get("channel_follower_count") or 0, "vids": vids, "avatar": avatar,
        "descr": re.sub(r"\s+", " ", d.get("description") or "").strip()[:400],
    }


def apply_velocity(vids, now):
    ids = [v["id"] for v in vids]
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    rows = q(f"SELECT vid, ts, views FROM hist WHERE vid IN ({marks}) ORDER BY ts", ids)
    seen, first = {}, {}
    for r in rows:
        seen.setdefault(r["vid"], r["ts"])
        age = now - r["ts"]
        if 1800 <= age <= 48 * 3600 and r["vid"] not in first:
            first[r["vid"]] = (r["ts"], r["views"])
    base = min(seen.values()) if seen else None  # this channel's earliest check on record
    for v in vids:
        fs = seen.get(v["id"])
        if fs is not None:
            if fs > base + 120:  # appeared after the channel's first check: a genuinely new upload
                v["s"] = round(fs)
        elif base is not None:
            v["s"] = round(now)
        if v["id"] in first:
            ts, old = first[v["id"]]
            h = (now - ts) / 3600
            v["g"], v["h"] = max(0, v["v"] - old), round(h, 1)


def probe_channel(ch_id):
    res = fetch_channel(f"https://www.youtube.com/channel/{ch_id}/shorts")
    now = time.time()
    if not res or not res["vids"]:
        with DB_LOCK:
            DB.execute("UPDATE channels SET probe_fail=probe_fail+1, last_probe=? WHERE ch_id=?", (now, ch_id))
            DB.commit()
        return False
    m = compute_metrics(res["vids"], res["subs"])
    if not m:
        with DB_LOCK:
            DB.execute("UPDATE channels SET probe_fail=probe_fail+1, last_probe=? WHERE ch_id=?", (now, ch_id))
            DB.commit()
        return False
    apply_velocity(res["vids"], now)
    english = int(sum(mostly_english(v["t"]) for v in res["vids"]) >= 0.6 * len(res["vids"]))
    lang = detect_lang([v["t"] for v in res["vids"]] + [res.get("descr", "")])
    with DB_LOCK:
        DB.executemany("INSERT INTO hist(vid,ts,views) VALUES(?,?,?)", [(v["id"], now, v["v"]) for v in res["vids"]])
        DB.execute(
            "UPDATE channels SET name=COALESCE(?,name), handle=COALESCE(?,handle), subs=?, last_probe=?, probe_fail=0, n=?, med_recent=?,"
            " med_all=?, max_views=?, share_100k=?, share_1m=?, trend=?, peak_vs_med=?, ratio=?, score=?, tags=?, english=?, series=?,"
            " avatar=COALESCE(NULLIF(?,''),avatar), descr=COALESCE(NULLIF(?,''),descr), lang=? WHERE ch_id=?",
            (res["name"], res["handle"], res["subs"], now, m["n"], m["med_recent"], m["med_all"], m["max_views"], m["share_100k"],
             m["share_1m"], m["trend"], m["peak_vs_med"], m["ratio"], m["score"], json.dumps(m["tags"]), english,
             json.dumps(res["vids"]), res.get("avatar", ""), res.get("descr", ""), lang, ch_id),
        )
        DB.commit()
    return True


def pick_probe_targets(cfg):
    now = time.time()
    rows = q("SELECT ch_id, hit_views, hit_title, last_probe, probe_fail, score, watch FROM channels")
    fresh, stale = [], []
    for r in rows:
        if r["probe_fail"] >= 3 and now - (r["last_probe"] or 0) < 24 * 3600:
            continue
        if r["last_probe"] is None and not cfg["probe_non_english"] and not mostly_english(r["hit_title"]):
            continue
        hot = (r["score"] or 0) >= 50 or r["watch"]
        if r["last_probe"] is None:
            if r["hit_views"] >= cfg["min_hit_views"] or r["watch"]:
                fresh.append((r["hit_views"], r["ch_id"]))
        elif now - r["last_probe"] > (3300 if hot else 12 * 3600):
            stale.append((-(r["score"] or 0) - (1000 if r["watch"] else 0), r["ch_id"]))
    fresh.sort(reverse=True)
    stale.sort()
    ordered = [c for _, c in stale[:120]] + [c for _, c in fresh]
    return ordered[: cfg["max_probes_per_cycle"]]


def run_probes(targets, workers):
    def probe(ch_id):
        try:
            ok = probe_channel(ch_id)
        except Exception:
            ok = False
            STATUS["errors"] += 1
        time.sleep(random.uniform(0.1, 0.5))
        STATUS["done"] += 1
        return ok

    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(probe, targets))


def pick_backfill():
    """Scored channels that don't have a profile picture yet, most important first."""
    picks = load_picks()
    rows = q("SELECT ch_id, score, watch FROM channels WHERE score IS NOT NULL AND (avatar IS NULL OR avatar='') AND probe_fail<3")
    rows = sorted(rows, key=lambda r: (r["ch_id"] not in picks, not r["watch"], -(r["score"] or 0)))
    return [r["ch_id"] for r in rows]


def run_cycle():
    cfg = load_config()
    workers = max(1, min(8, int(cfg["workers"])))
    STATUS.update(state="scanning", phase="discovery", done=0, cycle_started=time.time(), next_scan=None)
    import_seed()

    missing = pick_backfill()
    if missing:
        STATUS.update(phase="probing", done=0, total=len(missing))
        log(f"Adding profile pictures for {len(missing)} channels")
        run_probes(missing, workers)
        STATUS.update(phase="discovery", done=0)

    stats = {r["query"]: r for r in q("SELECT * FROM qstats")}
    queries = sorted(cfg["queries"], key=lambda s: (stats[s]["last_run"] if s in stats else 0, random.random()))[: cfg["max_queries_per_cycle"]]
    jobs = [(s, span) for s in queries for span in ("week", "month")]
    STATUS["total"] = len(jobs)
    log(f"Discovery: {len(queries)} niches")
    all_rows, per_q = [], collections.Counter()

    def work(job):
        try:
            rows = discover(*job)
        except Exception:
            rows = []
            STATUS["errors"] += 1
        time.sleep(random.uniform(0.1, 0.6))
        STATUS["done"] += 1
        return job, rows

    with ThreadPoolExecutor(workers) as ex:
        for job, rows in ex.map(work, jobs):
            all_rows.extend(rows)
            per_q[job[0]] += len(rows)
    now = time.time()
    with DB_LOCK:
        for s in queries:
            DB.execute(
                "INSERT INTO qstats(query,runs,last_run,last_rows,total_rows) VALUES(?,1,?,?,?) ON CONFLICT(query) DO UPDATE SET"
                " runs=runs+1,last_run=?,last_rows=?,total_rows=total_rows+?",
                (s, now, per_q[s], per_q[s], now, per_q[s], per_q[s]),
            )
        DB.commit()
    n = ingest_rows(all_rows)
    log(f"Discovery done: {len(all_rows)} Shorts from {n} channels")

    targets = pick_probe_targets(cfg)
    STATUS.update(phase="probing", done=0, total=len(targets))
    log(f"Checking {len(targets)} channels")
    results = run_probes(targets, workers)
    with DB_LOCK:
        DB.execute("DELETE FROM hist WHERE ts<?", (time.time() - 7 * 24 * 3600,))
        DB.commit()
    log(f"Probing done: {sum(results)}/{len(targets)} channels scored")
    STATUS.update(state="idle", phase="", cycles=STATUS["cycles"] + 1, last_cycle_end=time.time())


def scanner_loop():
    first = True
    while True:
        if not first:
            cfg = load_config()
            wait = max(1, int(cfg["interval_minutes"])) * 60
            STATUS["next_scan"] = time.time() + wait
            SCAN_NOW.wait(wait)
            SCAN_NOW.clear()
        first = False
        try:
            run_cycle()
        except Exception as e:  # keep the loop alive no matter what
            STATUS["errors"] += 1
            STATUS.update(state="idle", phase="")
            log(f"Scan error: {type(e).__name__}: {e}")


AVATAR_FAIL = collections.Counter()


def avatar_path(ch_id):
    return AVATAR_DIR / f"{ch_id}.img"


def image_mime(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    return None


def fetch_avatar(url, ch_id):
    """Download one profile picture to disk. Returns 'ok', 'rate' (HTTP 429) or 'fail'."""
    base = re.sub(r"=[^=/?#]*$", "", url)
    for size in ("176", "240", "900"):
        try:
            req = urllib.request.Request(f"{base}=s{size}-c-k-c0x00ffffff-no-rj", headers={"User-Agent": BROWSER_UA, "Accept": "image/*"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read(600_000)
            if image_mime(data):
                AVATAR_DIR.mkdir(parents=True, exist_ok=True)
                tmp = avatar_path(ch_id).with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(avatar_path(ch_id))
                return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 429:
                return "rate"
        except Exception:
            pass
    return "fail"


def avatar_worker():
    """Slowly cache profile pictures locally (picks first, then by score). Backs off when Google's image server says 429."""
    backoff = 0
    while True:
        try:
            AVATAR_DIR.mkdir(parents=True, exist_ok=True)
            picks = load_picks()
            rows = q("SELECT ch_id, avatar, score, watch FROM channels WHERE avatar IS NOT NULL AND avatar!='' AND score IS NOT NULL")
            todo = [r for r in rows if not avatar_path(r["ch_id"]).exists() and AVATAR_FAIL[r["ch_id"]] < 3]
            if not todo:
                time.sleep(20)
                continue
            todo.sort(key=lambda r: (r["ch_id"] not in picks, not r["watch"], -(r["score"] or 0)))
            r = todo[0]
            res = fetch_avatar(r["avatar"], r["ch_id"])
            if res == "ok":
                backoff = 0
                time.sleep(1.5)
            elif res == "rate":
                if backoff == 0:
                    log("Profile-picture server asked us to slow down; pausing image downloads for a bit")
                backoff = min(900, max(60, backoff * 2))
                time.sleep(backoff)
            else:
                AVATAR_FAIL[r["ch_id"]] += 1
                time.sleep(1)
        except Exception:
            time.sleep(10)


def track_channel(text):
    """Add a specific channel (handle, UC id or YouTube URL) to the watchlist and score it right away."""
    text = (text or "").strip()
    m = re.search(r"(UC[\w-]{22})", text)
    h = re.search(r"@([\w.\-]{2,60})", text)
    if m and ("youtube.com" in text or text.startswith("UC")):
        url = f"https://www.youtube.com/channel/{m.group(1)}/shorts"
    elif h:
        url = f"https://www.youtube.com/@{h.group(1)}/shorts"
    elif re.fullmatch(r"[\w.\-]{3,60}", text):
        url = f"https://www.youtube.com/@{text}/shorts"
    else:
        return None, "Use a @handle, a channel ID (UC...) or a youtube.com channel link"
    res = fetch_channel(url)
    if not res or not res["ch_id"] or not res["vids"]:
        return None, "Couldn't read that channel's Shorts"
    now = time.time()
    with DB_LOCK:
        DB.execute(
            "INSERT INTO channels(ch_id,name,handle,queries,first_seen,last_seen,watch) VALUES(?,?,?,?,?,?,1)"
            " ON CONFLICT(ch_id) DO UPDATE SET watch=1", (res["ch_id"], res["name"], res["handle"], "[]", now, now),
        )
        DB.commit()
    probe_channel(res["ch_id"])
    return res["ch_id"], None


# ----------------------------------------------------------------------------------------------
# api payload
# ----------------------------------------------------------------------------------------------
def load_picks():
    try:
        return {p["ch_id"]: p for p in json.loads(PICKS_PATH.read_text(encoding="utf-8"))}
    except Exception:
        return {}


def row_to_channel(r, picks):
    lang = r["lang"] or "und"
    return {
        "ch_id": r["ch_id"], "name": r["name"], "handle": r["handle"], "subs": r["subs"] or 0, "n": r["n"],
        "med_recent": r["med_recent"], "med_all": r["med_all"], "max_views": r["max_views"], "share_100k": r["share_100k"],
        "share_1m": r["share_1m"], "trend": r["trend"], "peak_vs_med": r["peak_vs_med"], "ratio": r["ratio"],
        "score": r["score"], "tags": json.loads(r["tags"]), "lang": lang, "english": lang in ("en", "und"),
        "queries": json.loads(r["queries"]), "series": json.loads(r["series"]), "watch": bool(r["watch"]),
        "last_probe": r["last_probe"], "avatar_ok": avatar_path(r["ch_id"]).exists(), "descr": r["descr"] or "",
        "pick": picks.get(r["ch_id"]),
    }


# ----------------------------------------------------------------------------------------------
# live search: look up any topic on YouTube right now
# ----------------------------------------------------------------------------------------------
SEARCH_JOBS = {}
SEARCH_LOCK = threading.Lock()


def live_search_worker(job):
    try:
        qy = job["query"]
        searches = [lambda: search_rows(qy, SORT_SP["week"]), lambda: search_rows(qy, SORT_SP["month"]),
                    lambda: plain_search_rows(qy, f"{qy} shorts"), lambda: plain_search_rows(qy, qy)]
        rows, seen = [], set()
        with ThreadPoolExecutor(4) as ex:
            for part in ex.map(lambda f: f(), searches):
                for r in part:
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        rows.append(r)
        job["found"] = len(rows)
        job["phase"] = "checking"
        ingest_rows(rows)
        best = {}
        for r in rows:
            best[r["ch_id"]] = max(best.get(r["ch_id"], 0), r["views"])
        ids = [c for c, _ in sorted(best.items(), key=lambda kv: -kv[1])][:18]
        job["total"] = len(ids)

        def one(cid):
            try:
                ok = probe_channel(cid)
            except Exception:
                ok = False
            job["done"] += 1
            if ok:
                job["ch_ids"].append(cid)

        with ThreadPoolExecutor(5) as ex:
            list(ex.map(one, ids))
        job["state"] = "done"
    except Exception as e:
        job["state"] = "error"
        job["error"] = type(e).__name__
    job["finished"] = time.time()


def start_live_search(query):
    query = re.sub(r"\s+", " ", query or "").strip()[:80]
    if len(query) < 2:
        return None, "empty"
    with SEARCH_LOCK:
        if sum(1 for j in SEARCH_JOBS.values() if j["state"] == "running") >= 2:
            return None, "busy"
        job = {"id": uuid.uuid4().hex[:10], "query": query, "state": "running", "phase": "searching", "found": 0,
               "done": 0, "total": 0, "ch_ids": [], "started": time.time()}
        SEARCH_JOBS[job["id"]] = job
        for old in sorted(SEARCH_JOBS.values(), key=lambda j: j["started"])[:-20]:
            SEARCH_JOBS.pop(old["id"], None)
    threading.Thread(target=live_search_worker, args=(job,), daemon=True).start()
    return job, None


def search_payload(job_id):
    job = SEARCH_JOBS.get(job_id)
    if not job:
        return None
    ids = list(job["ch_ids"])
    chans = []
    if ids:
        picks = load_picks()
        marks = ",".join("?" * len(ids))
        rows = q(f"SELECT * FROM channels WHERE ch_id IN ({marks}) AND score IS NOT NULL", ids)
        chans = sorted((row_to_channel(r, picks) for r in rows), key=lambda c: -c["score"])
    return {"id": job["id"], "query": job["query"], "state": job["state"], "phase": job["phase"], "found": job["found"],
            "done": job["done"], "total": job["total"], "error": job.get("error"), "channels": chans}


def build_data():
    picks = load_picks()
    rows = q("SELECT * FROM channels WHERE score IS NOT NULL ORDER BY score DESC LIMIT 700")
    channels = [row_to_channel(r, picks) for r in rows]
    qs = [dict(r) for r in q("SELECT query, runs, last_run, last_rows, total_rows FROM qstats ORDER BY query")]
    tracked = q("SELECT COUNT(*) c FROM channels", one=True)["c"]
    status = dict(STATUS)
    status.update(tracked=tracked, scored=len(channels), log=list(LOG)[-25:])
    return {"generated": time.time(), "status": status, "channels": channels, "queries": qs, "config": {
        k: v for k, v in load_config().items() if k != "queries"}}


# ----------------------------------------------------------------------------------------------
# http
# ----------------------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "OutlierRadar/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        hdrs = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        hdrs.update(extra or {})
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype == "application/json" else ""))
        self.send_header("Content-Length", str(len(data)))
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        if ARGS.share:
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("localhost", "127.0.0.1", "::1")

    FORWARD_HEADERS = ("x-forwarded-for", "forwarded", "cf-connecting-ip", "x-real-ip", "cf-ray", "true-client-ip")

    def _local_origin(self):
        """True only for requests made straight from this computer's own browser, never through a tunnel or proxy."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return (self.client_address[0] in ("127.0.0.1", "::1") and host in ("localhost", "127.0.0.1", "::1")
                and not any(self.headers.get(h) for h in self.FORWARD_HEADERS))

    def _owner(self):
        return self._local_origin() and self.headers.get("X-Radar") == "1"

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "forbidden", "text/plain")
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = INDEX_PATH.read_text(encoding="utf-8")
            return self._send(200, html, "text/html")
        if path == "/api/data":
            data = build_data()
            data["readonly"] = not self._local_origin()
            return self._send(200, json.dumps(data))
        if path == "/api/search":
            if not self._local_origin():
                return self._send(403, json.dumps({"error": "read-only"}))
            ids = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("id") or [""]
            p = search_payload(ids[0])
            return self._send(200 if p else 404, json.dumps(p or {"error": "unknown search"}))
        if path.startswith("/avatar/"):
            ch = path[len("/avatar/"):]
            f = avatar_path(ch) if re.fullmatch(r"UC[\w-]{22}", ch) else None
            if f and f.exists():
                blob = f.read_bytes()
                return self._send(200, blob, image_mime(blob) or "application/octet-stream", {"Cache-Control": "public, max-age=86400"})
            return self._send(404, "no avatar", "text/plain")
        if path == "/api/export":
            data = build_data()
            data["readonly"] = True
            data["snapshot"] = True
            for c in data["channels"]:  # embed profile pictures so the shared file works offline and without image-server limits
                f = avatar_path(c["ch_id"])
                if f.exists():
                    blob = f.read_bytes()
                    mime = image_mime(blob)
                    if mime:
                        c["avatar_data"] = f"data:{mime};base64,{b64encode(blob).decode()}"
            blob = json.dumps(data).replace("</", "<\\/")
            html = INDEX_PATH.read_text(encoding="utf-8").replace("null/*__SNAPSHOT__*/", blob, 1)
            name = time.strftime("outlier-radar-snapshot-%Y%m%d-%H%M.html")
            return self._send(200, html, "text/html", {"Content-Disposition": f'attachment; filename="{name}"'})
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._host_ok() or not self._owner():
            return self._send(403, json.dumps({"error": "read-only"}))
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 100_000)) or b"{}")
        except Exception:
            return self._send(400, json.dumps({"error": "bad json"}))
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/scan":
            SCAN_NOW.set()
            return self._send(200, json.dumps({"ok": True}))
        if path == "/api/watch":
            ch = str(body.get("ch_id", ""))
            if re.fullmatch(r"UC[\w-]{22}", ch):
                q("UPDATE channels SET watch=? WHERE ch_id=?", (1 if body.get("watch") else 0, ch), commit=True)
            return self._send(200, json.dumps({"ok": True}))
        if path == "/api/queries":
            cfg = load_config()
            cur = list(cfg["queries"])
            for s in body.get("add", []):
                s = re.sub(r"\s+", " ", str(s)).strip()[:60]
                if s and s.lower() not in [c.lower() for c in cur]:
                    cur.append(s)
            rm = {str(s).lower() for s in body.get("remove", [])}
            cfg["queries"] = [c for c in cur if c.lower() not in rm]
            save_config(cfg)
            if body.get("add"):
                SCAN_NOW.set()
            return self._send(200, json.dumps({"ok": True, "count": len(cfg["queries"])}))
        if path == "/api/search":
            job, err = start_live_search(str(body.get("query", "")))
            if not job:
                return self._send(429 if err == "busy" else 400, json.dumps({"ok": False, "error": err}))
            return self._send(200, json.dumps({"ok": True, "id": job["id"]}))
        if path == "/api/track":
            ch_id, err = track_channel(str(body.get("channel", "")))
            return self._send(200 if ch_id else 400, json.dumps({"ok": bool(ch_id), "error": err, "ch_id": ch_id}))
        self._send(404, json.dumps({"error": "not found"}))


def main():
    global ARGS, DB
    ap = argparse.ArgumentParser(description="Outlier Radar")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--share", action="store_true", help="listen on your network so others can view (read-only)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-scan", action="store_true", help="serve existing data without scanning")
    ARGS = ap.parse_args()
    DB = db_connect()
    with DB_LOCK:  # an earlier version stored banner images as avatars; clear those so they get re-fetched
        DB.execute("UPDATE channels SET avatar=NULL WHERE avatar LIKE '%fcrop%' OR avatar LIKE '%=w%'")
        DB.commit()
    rescore_all()
    if not CONFIG_PATH.exists():
        save_config(load_config())
    if not args_ytdlp_ok():
        print("yt-dlp was not found. Install it with:  python -m pip install -U yt-dlp")
        sys.exit(1)
    threading.Thread(target=avatar_worker, daemon=True).start()
    if not ARGS.no_scan:
        threading.Thread(target=scanner_loop, daemon=True).start()
    else:
        STATUS.update(state="idle")
    host = "0.0.0.0" if ARGS.share else "127.0.0.1"
    srv = ThreadingHTTPServer((host, ARGS.port), Handler)
    url = f"http://localhost:{ARGS.port}"
    print(f"Outlier Radar running at {url}" + ("  (sharing on your network, read-only for others)" if ARGS.share else ""))
    if not ARGS.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def args_ytdlp_ok():
    try:
        p = subprocess.run(ytdlp_base() + ["--version"], capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode == 0
    except Exception:
        return False


if __name__ == "__main__":
    main()
