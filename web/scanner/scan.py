#!/usr/bin/env python3
"""Hosted scanner: refreshes data.json from the official YouTube Data API inside a daily quota budget.

Runs on a schedule (GitHub Actions) with YT_API_KEY in the environment. Standard library only.
Each run: (optionally) one discovery search, then re-checks the most overdue channels, then writes data.json.
The previous data.json (downloaded from the live site, or the seed file the first time) carries all state between runs.
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # works however Python is started (including isolated mode)
from yt_api import ApiError, QuotaExceeded, YouTube  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SEARCH_EVERY = int(os.environ.get("SEARCH_EVERY", "1"))          # a discovery search every N runs (searches are paced across the day)
PROBES_PER_RUN = int(os.environ.get("PROBES_PER_RUN", "40"))      # channels re-checked per run (about 2 units each)
DAILY_BUDGET = int(os.environ.get("DAILY_BUDGET", "8000"))        # of the 10,000 daily units; the rest is left for visitors' live searches
SEARCH_DAILY_CAP = int(os.environ.get("SEARCH_DAILY_CAP", "45"))  # Google allows 100 searches a day per project; the rest is left for visitors
FEATURED_PER_RUN = int(os.environ.get("FEATURED_PER_RUN", "3"))  # top channels whose featured channels are looked up per run (1 unit each)
RUN_BUDGET = int(os.environ.get("RUN_BUDGET", "400"))
WINDOW_DAYS = int(os.environ.get("SEARCH_WINDOW_DAYS", "30"))
KEEP_CHANNELS = int(os.environ.get("KEEP_CHANNELS", "1500"))
HIST_POINTS = int(os.environ.get("HIST_POINTS", "48"))         # growth history kept per channel (at most one point per 6 hours)


def load_radar():
    spec = importlib.util.spec_from_file_location("radar", ROOT / "outlier-radar" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pacific_day(ts):
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.fromtimestamp(ts, ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:
        return dt.datetime.fromtimestamp(ts - 8 * 3600, dt.timezone.utc).strftime("%Y-%m-%d")


def load_prev(url, seed):
    if url:
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                data = json.load(r)
            if isinstance(data, dict) and "channels" in data:
                return data, "live site"
        except Exception as e:
            print(f"could not read previous data from the site ({type(e).__name__}); falling back to the seed", file=sys.stderr)
    if seed and Path(seed).exists():
        return json.loads(Path(seed).read_text(encoding="utf-8")), "seed file"
    return {"channels": [], "_state": {}}, "empty"


def day_fraction(ts):
    """How far through Google's quota day (Pacific time) we are, 0..1."""
    try:
        from zoneinfo import ZoneInfo
        t = dt.datetime.fromtimestamp(ts, ZoneInfo("America/Los_Angeles"))
    except Exception:
        t = dt.datetime.fromtimestamp(ts - 8 * 3600, dt.timezone.utc)
    return (t.hour * 3600 + t.minute * 60 + t.second) / 86400


def merge_extra(path, channels, st, log):
    """Adds channels scored by the PC app (web/seed/extra.json) once per version of that file. The API re-checks them when due."""
    p = Path(path) if path else None
    if not p or not p.exists():
        return
    raw = p.read_bytes()
    digest = hashlib.sha1(raw).hexdigest()[:16]
    if st.get("extra_hash") == digest:
        return
    added = 0
    for c in json.loads(raw.decode("utf-8")):
        if not isinstance(c, dict) or c.get("ch_id") in channels or not re.fullmatch(r"UC[\w-]{22}", c.get("ch_id", "")) or len(c.get("series") or []) < 6:
            continue
        channels[c["ch_id"]] = dict(c, watch=False, pick=None)
        added += 1
    st["extra_hash"] = digest
    log.append(f"Added {added} channels found by the PC scanner")


def crawl_featured(yt, st, channels, pending, now, log, limit):
    """Looks up the channels top creators feature on their pages; unknown ones become candidates and inherit the source's topics."""
    done = st.setdefault("featured", {})
    picks = sorted((c for c in channels.values() if (c.get("score") or 0) >= 55 and now - done.get(c["ch_id"], 0) > 7 * 86400),
                   key=lambda c: -(c.get("score") or 0))[:limit]
    found = 0
    for c in picks:
        done[c["ch_id"]] = int(now)
        try:
            ids = yt.featured_channels(c["ch_id"])
        except ApiError:
            continue
        for fid in ids:
            if fid in channels or fid in pending or not re.fullmatch(r"UC[\w-]{22}", fid):
                continue
            pending[fid] = {"ch_id": fid, "name": "", "hit_views": 0, "hit_title": "", "queries": list(c.get("queries", []))[:3], "via": c["ch_id"]}
            found += 1
    if picks:
        log.append(f"Related channels: {found} new candidates from {len(picks)} top channels")


def pick_targets(channels, pending, st, picks, now, limit):
    out = []
    for ch_id, rec in channels.items():
        if st["failed"].get(ch_id, 0) >= 3:
            continue
        hot = (rec.get("score") or 0) >= 50 or ch_id in picks
        age = now - (rec.get("last_probe") or 0)
        need = 3 * 3600 if hot else 24 * 3600
        if age >= need:
            out.append((1 if hot else 2, -(age / need), ch_id))
    for ch_id, rec in pending.items():
        if st["failed"].get(ch_id, 0) < 3:
            out.append((0, -rec.get("hit_views", 0), ch_id))
    out.sort()
    return [c for *_, c in out[:limit]]


def rotation(queries):
    """Search order: every phrase once, then the ones weighted "w": 2 (or more) again, so they come up more often without repeating back to back."""
    top = max([int(q.get("w", 1)) for q in queries] or [1])
    return [q for k in range(1, top + 1) for q in queries if int(q.get("w", 1)) >= k]


def add_hist(rec, now, subs, m):
    """Growth history: [time, subscribers, typical views, score]; all but the newest point are at least 6 hours apart."""
    h = [list(x) for x in rec.get("hist") or []]
    if not h and rec.get("last_probe") and rec.get("med_recent") is not None:   # the record we already had becomes the first point
        h.append([int(rec["last_probe"]), rec.get("subs", 0), int(rec["med_recent"]), rec.get("score", 0)])
    point = [int(now), subs, int(m["med_recent"]), m["score"]]
    if len(h) >= 2 and h[-1][0] - h[-2][0] < 6 * 3600:
        h[-1] = point
    else:
        h.append(point)
    return h[-HIST_POINTS:]


def discover(yt, st, queries, channels, pending, now, log):
    rot = rotation(queries)
    q = rot[st["cursor"] % len(rot)]
    st["cursor"] += 1
    text = q["q"]
    ids = yt.search_videos(text, now - WINDOW_DAYS * 86400, q.get("lang"), q.get("region"))
    vids = yt.videos(ids)
    rows = [v for v in vids.values() if v["dur"] <= 180 and v["ch_id"]]
    best = {}
    for v in rows:
        b = best.setdefault(v["ch_id"], {"hit": 0, "title": "", "name": v["ch"]})
        if v["v"] > b["hit"]:
            b["hit"], b["title"] = v["v"], v["t"]
    for ch_id, b in best.items():
        if ch_id in channels:
            rec = channels[ch_id]
            rec["queries"] = sorted(set(rec.get("queries", [])) | {text})
            rec["hit_views"] = max(rec.get("hit_views", 0), b["hit"])
        else:
            rec = pending.setdefault(ch_id, {"ch_id": ch_id, "name": b["name"], "hit_views": 0, "hit_title": "", "queries": []})
            rec["queries"] = sorted(set(rec["queries"]) | {text})
            if b["hit"] > rec["hit_views"]:
                rec["hit_views"], rec["hit_title"] = b["hit"], b["title"]
    s = st["qstats"].setdefault(text, {"runs": 0, "total_rows": 0})
    s.update(runs=s["runs"] + 1, last_run=now, last_rows=len(rows), total_rows=s["total_rows"] + len(rows))
    log.append(f"Search '{text}': {len(rows)} short videos from {len(best)} channels")


def probe(radar, yt, ch_id, rec, info, now, is_new=False):
    """Re-score one channel from its newest uploads. Returns the new channel dict, or None if it has too few shorts."""
    ids = yt.recent_upload_ids(ch_id, 50)
    vids = yt.videos(ids)
    shorts = [vids[i] for i in ids if i in vids and vids[i]["dur"] <= 180][:24]
    series = [{"id": v["id"], "t": v["t"], "v": v["v"], "d": v["dur"], **({"p": round(v["p"])} if v.get("p") else {}),
               **({"c": v["c"]} if v.get("c") is not None else {}), **({"l": v["l"]} if v.get("l") is not None else {})} for v in shorts]
    subs = info["subs"] if info else rec.get("subs", 0)
    m = radar.compute_metrics(series, subs)
    if not m:
        return None
    prev = {s["id"]: s for s in rec.get("series", [])}
    prev_ts = rec.get("last_probe")
    if prev_ts and now - prev_ts >= 1800:
        h = (now - prev_ts) / 3600
        for s in series:
            if s["id"] in prev:
                s["g"], s["h"] = max(0, s["v"] - prev[s["id"]]["v"]), round(h, 1)
    descr = (info or {}).get("descr") or rec.get("descr", "")
    lang = radar.detect_lang([s["t"] for s in series] + [descr])
    return {
        "ch_id": ch_id, "name": (info or {}).get("name") or rec.get("name"), "handle": (info or {}).get("handle") or rec.get("handle", ""),
        "subs": subs, "n": m["n"], "med_recent": m["med_recent"], "med_all": m["med_all"], "max_views": m["max_views"],
        "share_100k": m["share_100k"], "share_1m": m["share_1m"], "trend": m["trend"], "peak_vs_med": m["peak_vs_med"], "ratio": m["ratio"],
        "score": m["score"], "tags": m["tags"], "lang": lang, "english": lang in ("en", "und"), "queries": rec.get("queries", []),
        "series": series, "watch": False, "last_probe": now, "descr": descr,
        "avatar_url": (info or {}).get("avatar") or rec.get("avatar_url", ""), "hit_views": rec.get("hit_views", 0), "pick": None,
        "hist": add_hist(rec, now, subs, m),
        **({"created": int((info or {}).get("created") or rec.get("created"))} if ((info or {}).get("created") or rec.get("created")) else {}), **({"found": rec.get("found") or int(now)} if (is_new or rec.get("found")) else {}),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev-url", default=os.environ.get("PREV_DATA_URL", ""))
    ap.add_argument("--seed", default=str(ROOT / "web" / "seed" / "data.json"))
    ap.add_argument("--queries", default=str(ROOT / "web" / "queries.json"))
    ap.add_argument("--picks", default=str(ROOT / "outlier-radar" / "picks.json"))
    ap.add_argument("--extra", default=str(ROOT / "web" / "seed" / "extra.json"))
    ap.add_argument("--out", default=str(ROOT / "_site" / "data.json"))
    args = ap.parse_args()

    now = time.time() + float(os.environ.get("SCAN_TIME_OFFSET", "0"))   # the offset exists only so selftest.py can simulate later runs
    radar = load_radar()
    prev, source = load_prev(args.prev_url, args.seed)
    channels = {c["ch_id"]: c for c in prev.get("channels", [])}
    st = prev.get("_state") or {}
    pending = st.get("pending", {})
    st.update(pending=pending, cursor=st.get("cursor", 0), runs=st.get("runs", 0) + 1, failed=st.get("failed", {}), qstats=st.get("qstats", {}))
    day = pacific_day(now)
    if st.get("quota_day") != day:
        st.update(quota_day=day, quota_used=0, searches_used=0)
    st.setdefault("searches_used", 0)
    queries = [q if isinstance(q, dict) else {"q": q} for q in json.loads(Path(args.queries).read_text(encoding="utf-8"))]
    try:
        picks = {p["ch_id"]: p for p in json.loads(Path(args.picks).read_text(encoding="utf-8"))}
    except Exception:
        picks = {}

    log = list((prev.get("status") or {}).get("log", []))[-18:]
    merge_extra(args.extra, channels, st, log)
    stamp = time.strftime("%H:%M", time.gmtime(now)) + " UTC"
    errors = 0
    key = os.environ.get("YT_API_KEY", "")
    budget = max(0, min(RUN_BUDGET, DAILY_BUDGET - st.get("quota_used", 0)))
    fatal = None
    if not key:
        log.append(f"{stamp}  No YT_API_KEY set; showing the last saved data")
        fatal = "YT_API_KEY missing"
    elif budget < 5:
        log.append(f"{stamp}  Daily scan budget used ({st.get('quota_used', 0)}/{DAILY_BUDGET} units); waiting for tomorrow's reset")
    else:
        yt = YouTube(key, budget)
        try:
            paced = min(SEARCH_DAILY_CAP, int(SEARCH_DAILY_CAP * day_fraction(now)) + 2)   # spread the day's searches out
            if queries and st["runs"] % SEARCH_EVERY == 0 and budget >= 60 and st["searches_used"] < paced:
                discover(yt, st, queries, channels, pending, now, log)
            targets = pick_targets(channels, pending, st, picks, now, PROBES_PER_RUN)
            infos = yt.channels(targets) if targets else {}
            done = 0
            for ch_id in targets:
                rec = channels.get(ch_id) or pending.get(ch_id) or {}
                try:
                    new = probe(radar, yt, ch_id, rec, infos.get(ch_id), now, is_new=ch_id not in channels)
                except QuotaExceeded:
                    raise
                except ApiError as e:
                    errors += 1
                    st["failed"][ch_id] = st["failed"].get(ch_id, 0) + 1
                    log.append(f"{stamp}  Skipped a channel: {e}")
                    continue
                if new is None:
                    st["failed"][ch_id] = st["failed"].get(ch_id, 0) + 1
                    pending.pop(ch_id, None) if st["failed"][ch_id] >= 3 else None
                    continue
                channels[ch_id] = new
                pending.pop(ch_id, None)
                done += 1
            crawl_featured(yt, st, channels, pending, now, log, FEATURED_PER_RUN)
            log.append(f"{stamp}  Re-checked {done}/{len(targets)} channels using {yt.used} units")
        except QuotaExceeded as e:
            log.append(f"{stamp}  Stopped early: {e}")
        except ApiError as e:
            errors += 1
            fatal = str(e)
            log.append(f"{stamp}  YouTube API problem: {e}")
        st["quota_used"] = st.get("quota_used", 0) + yt.used
        st["searches_used"] += yt.searches

    for ch_id, ch in channels.items():
        ch["pick"] = picks.get(ch_id)
    ranked = sorted(channels.values(), key=lambda c: -(c.get("score") or 0))[:KEEP_CHANNELS]
    keep = {c["ch_id"] for c in ranked}
    st["qstats"] = {k: v for k, v in st["qstats"].items()}
    data = {
        "generated": now, "readonly": True, "web": True,
        "status": {"state": "idle", "phase": "", "done": 0, "total": 0, "cycles": st["runs"], "errors": errors, "cycle_started": now,
                   "last_cycle_end": now, "next_scan": None, "message": log[-1] if log else "", "tracked": len(channels) + len(pending),
                   "scored": len(ranked), "log": log[-25:],
                   "quota": {"used_today": st.get("quota_used", 0), "scanner_cap": DAILY_BUDGET, "searches_today": st["searches_used"],
                             "search_cap": SEARCH_DAILY_CAP, "day": day}},
        "channels": ranked,
        "queries": [{"query": k, "runs": v["runs"], "last_run": v.get("last_run"), "last_rows": v.get("last_rows", 0), "total_rows": v.get("total_rows", 0)}
                    for k, v in sorted(st["qstats"].items())],
        "config": {},
        "_state": {**{k: st[k] for k in ("quota_day", "quota_used", "searches_used", "runs", "cursor", "failed", "qstats")},
                   "extra_hash": st.get("extra_hash", ""), "featured": {k: v for k, v in st.get("featured", {}).items() if now - v < 8 * 86400},
                   "pending": dict(list(pending.items())[:3000])},
    }
    # forget channels that fell off the ranked list (they would only be re-discovered by searches)
    data["_state"]["failed"] = {k: v for k, v in st["failed"].items() if k in keep or k in pending}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"state from {source}; {len(ranked)} channels, {len(pending)} pending; quota used today {st.get('quota_used', 0)}; wrote {out}")
    if fatal:
        print(f"WARNING: {fatal}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
