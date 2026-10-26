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
SKIP_WIKI = re.compile(r"^(Main_Page$|หน้าหลัก$|Special:|Wikipedia:|File:|Portal:|Help:|Category:|Template:|Talk:|User:|"
                       r"พิเศษ:|วิกิพีเดีย:|ไฟล์:|สถานีย่อย:|หมวดหมู่:|แม่แบบ:|-$|Search$|Cleopatra$|\.)", re.I)
ADULT = re.compile(r"xxx|porn|xvideos|xnxx|onlyfans|hentai|nude|เย็ด|โป๊|ควย|^หี$", re.I)   # bots and adult pages crowd these lists


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
    args = ap.parse_args()
    t = now()
    prev = load_prev(os.environ.get("PREV_TRENDS_URL", ""))
    yt_prev, wiki_prev = prev.get("yt") or {}, prev.get("wiki") or {}
    seen = {k: v for k, v in (prev.get("seen") or {}).items() if isinstance(v, (int, float)) and t - v < SEEN_DAYS * 86400}
    out = {"generated": int(t), "yt": yt_prev, "wiki": wiki_prev}

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
    print(f"trends: {charts} YouTube charts, Wikipedia {', '.join(k for k in WIKIS if k in out['wiki']) or 'none'}")


if __name__ == "__main__":
    main()
