#!/usr/bin/env python3
"""Hosted scanner: refreshes data.json from the official YouTube Data API inside a daily quota budget.

Runs on a schedule (GitHub Actions) with YT_API_KEY in the environment. Standard library only.
Each run: (optionally) one discovery search, then re-checks the most overdue channels, then writes data.json.

State (history, quota counters, candidates) is kept in two places: a private state folder that the workflow saves in the
Actions cache (--state), and the previous public data.json on the live site (--prev-url). The newer of the two is used.
If neither can be read the scanner refuses to run (exit code 2) instead of publishing a rebuilt file; the seed file is only
for setting up a brand-new site (--allow-seed, or ALLOW_SEED=1), and even then its channels are only candidates to check.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # works however Python is started (including isolated mode)
from yt_api import ApiError, QuotaExceeded, SearchQuotaExceeded, YouTube  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SEARCH_EVERY = int(os.environ.get("SEARCH_EVERY", "1"))          # a discovery search every N runs (searches are paced across the day)
PROBES_MAX = int(os.environ.get("PROBES_PER_RUN", "1000"))       # most channels re-checked in one run (about 2.2 units each)
DAILY_BUDGET = int(os.environ.get("DAILY_BUDGET", "8000"))        # of the 10,000 daily units; trends.py keeps to its own smaller allowance
SEARCH_DAILY_CAP = int(os.environ.get("SEARCH_DAILY_CAP", "45"))  # searches have their own bucket of 100 a day; trends.py uses up to 34 more
FEATURED_PER_RUN = int(os.environ.get("FEATURED_PER_RUN", "3"))  # top channels whose featured channels are looked up per run (1 unit each)
RUN_CAP = int(os.environ.get("RUN_BUDGET", "2400"))              # most units one run may spend, however few runs GitHub starts
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "1200"))         # stop re-checking after this long, so a run never hangs the schedule
WINDOW_DAYS = int(os.environ.get("SEARCH_WINDOW_DAYS", "30"))
KEEP_CHANNELS = int(os.environ.get("KEEP_CHANNELS", "1500"))
MAX_TRACKED = int(os.environ.get("MAX_TRACKED", "400"))          # channels visitors asked for (kept on top of KEEP_CHANNELS; checked daily like the rest)
HIST_POINTS = int(os.environ.get("HIST_POINTS", "48"))           # growth history kept per channel (at most one point per 6 hours)
# how long statistics (subscriber counts, views) may be kept: YouTube's policies allow 30 days, or up to 36 months (1095 days)
# once the "Analytics & Reporting" amendment has been accepted through the quota extension form (web/QUOTA_REQUEST.md)
STATS_DAYS = min(1095, int(os.environ.get("STATS_DAYS") or "30"))
RETAIN_DAYS = int(os.environ.get("RETAIN_DAYS", "30"))           # a channel the API could not refresh for this long is dropped
MAX_STATE_AGE = int(os.environ.get("MAX_STATE_AGE_DAYS", "7"))   # older saved state is treated as missing (refuse rather than roll back)
VH_DAYS, VH_POINTS = 10, 8                                       # views-over-time curve for Shorts younger than 10 days


def load_radar():
    spec = importlib.util.spec_from_file_location("radar", ROOT / "outlier-radar" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pacific_now(ts):
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.fromtimestamp(ts, ZoneInfo("America/Los_Angeles"))
    except Exception:
        return dt.datetime.fromtimestamp(ts - 8 * 3600, dt.timezone.utc)


def pacific_day(ts):
    return pacific_now(ts).strftime("%Y-%m-%d")


def day_fraction(ts):
    """How far through Google's quota day (Pacific time) we are, 0..1."""
    t = pacific_now(ts)
    return (t.hour * 3600 + t.minute * 60 + t.second) / 86400


def valid_data(d):
    return isinstance(d, dict) and isinstance(d.get("channels"), list) and isinstance(d.get("generated", 0), (int, float))


def load_prev(url, state_dir, seed, allow_seed, now):
    """The newest usable previous state: the saved state folder or the live site. Returns (data, source) or (None, reason)."""
    found = []
    if state_dir:
        p = Path(state_dir) / "data.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if valid_data(d):
                    found.append((d.get("generated") or 0, 1, d, "saved state"))
            except Exception as e:
                print(f"saved state unreadable ({type(e).__name__})", file=sys.stderr)
    if url:
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                d = json.load(r)
            if valid_data(d):
                found.append((d.get("generated") or 0, 0, d, "live site"))
        except Exception as e:
            print(f"could not read previous data from the site ({type(e).__name__})", file=sys.stderr)
    if found:
        gen, _, d, source = max(found, key=lambda x: (x[0], x[1]))
        if allow_seed or now - gen <= MAX_STATE_AGE * 86400:
            return d, source
        print(f"newest saved state is {(now - gen) / 86400:.1f} days old", file=sys.stderr)
    if allow_seed:   # a brand-new site: the seed's channels become candidates the API checks, nothing of theirs is published
        cands = {}
        if seed and Path(seed).exists():
            for c in json.loads(Path(seed).read_text(encoding="utf-8")).get("channels", []):
                if re.fullmatch(r"UC[\w-]{22}", c.get("ch_id", "")):
                    cands[c["ch_id"]] = {"ch_id": c["ch_id"], "name": c.get("name", ""), "hit_views": c.get("max_views", 0), "hit_title": "",
                                         "queries": list(c.get("queries", []))[:3]}
        return {"channels": [], "_state": {"pending": cands}}, "seed candidates"
    return None, "no saved state and the live site could not be read"


def is_legacy(rec):
    """A record from the old PC-app import (yt-dlp): no upload dates and never refreshed through the API."""
    return rec.get("src") != "api" and bool(rec.get("series")) and not any(s.get("p") for s in rec["series"])


def seconds_to_pacific_midnight(ts):
    t = pacific_now(ts)
    nxt = (t + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60.0, (nxt - t).total_seconds())


def run_budget(st, now):
    """Units this run may spend: what is left of today's budget, shared over the runs still expected today.
    GitHub starts scheduled runs far less often than asked, so the expected gap is learned from the last runs."""
    times = sorted(t for t in st.get("run_times", []) if now - t < 3 * 86400 and t < now)
    gaps = sorted(b - a for a, b in zip(times, times[1:]) if b - a > 60)
    gap = gaps[len(gaps) // 2] if gaps else 3 * 3600
    gap = min(max(gap, 600), 8 * 3600)
    runs_left = max(1.0, seconds_to_pacific_midnight(now) / gap)
    left = max(0, DAILY_BUDGET - st.get("quota_used", 0))
    return int(min(RUN_CAP, left / runs_left)), gap


def add_vh(item, prev_item, prev_ts, now):
    """Views over time for one young Short: [[time, views], ...], starting at [upload time, 0]; points at least 3 hours apart (plus the newest)."""
    if prev_item and prev_item.get("vh"):
        vh = [list(x) for x in prev_item["vh"]]
    else:
        vh = [[int(item["p"]), 0]] if item.get("p") else []
        if prev_item and prev_ts and (not vh or prev_ts > vh[-1][0]):
            vh.append([int(prev_ts), prev_item["v"]])
    point = [int(now), item["v"]]
    if len(vh) >= 2 and vh[-1][0] - vh[-2][0] < 3 * 3600:
        vh[-1] = point
    else:
        vh.append(point)
    return vh if len(vh) <= VH_POINTS else [vh[0]] + vh[-(VH_POINTS - 1):]   # keep the upload point and the newest ones


def merge_extra(path, pending, channels, st, log):
    """Channel ids found by the PC app (web/seed/extra.json) become candidates for the API to check, once per version of that file.
    Nothing the PC app measured is published: the scanner fetches every number again through the official API."""
    p = Path(path) if path else None
    if not p or not p.exists():
        return
    raw = p.read_bytes()
    import hashlib
    digest = hashlib.sha1(raw).hexdigest()[:16]
    if st.get("extra_hash") == digest:
        return
    added = 0
    for c in json.loads(raw.decode("utf-8")):
        cid = c.get("ch_id", "") if isinstance(c, dict) else ""
        if not re.fullmatch(r"UC[\w-]{22}", cid) or cid in channels or cid in pending:
            continue
        pending[cid] = {"ch_id": cid, "name": c.get("name", ""), "hit_views": c.get("max_views", 0), "hit_title": "", "queries": list(c.get("queries", []))[:3]}
        added += 1
    st["extra_hash"] = digest
    log.append(f"Queued {added} channels found by the PC scanner for checking")


def fetch_requested(log):
    """Channels visitors asked the site to track (Firestore collection track/*, readable by anyone). Empty when sign-in isn't set up."""
    project, key = os.environ.get("FIREBASE_PROJECT_ID", "").strip(), os.environ.get("FIREBASE_API_KEY", "").strip()
    if not project or not key:
        return []
    base = os.environ.get("FIRESTORE_BASE", "https://firestore.googleapis.com/v1")
    ids, page = [], ""
    for _ in range(5):
        url = f"{base}/projects/{project}/databases/(default)/documents/track?pageSize=300&mask.fieldPaths=t&key={key}" + (f"&pageToken={page}" if page else "")
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.load(r)
        except Exception as e:
            log.append(f"Could not read tracked channels ({type(e).__name__})")
            break
        ids += [d["name"].rsplit("/", 1)[-1] for d in data.get("documents", [])]
        page = data.get("nextPageToken", "")
        if not page:
            break
    return [i for i in ids if re.fullmatch(r"UC[\w-]{22}", i)]


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
    """Who to re-check this run: old imports first (so their numbers come from the API), then new candidates, then whatever is overdue."""
    out = []
    for ch_id, rec in channels.items():
        if st["failed"].get(ch_id, 0) >= 3:
            continue
        age = now - (rec.get("last_probe") or 0)
        if is_legacy(rec):
            out.append((-1, -age, ch_id))
            continue
        hot = (rec.get("score") or 0) >= 50 or ch_id in picks
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
    h = [list(x) for x in rec.get("hist") or []] if not is_legacy(rec) else []   # old imports start a fresh history from API numbers
    if not h and rec.get("last_probe") and rec.get("med_recent") is not None and not is_legacy(rec):   # the record we already had becomes the first point
        h.append([int(rec["last_probe"]), rec.get("subs", 0), int(rec["med_recent"]), rec.get("score", 0)])
    point = [int(now), subs, int(m["med_recent"]), m["score"]]
    if len(h) >= 2 and h[-1][0] - h[-2][0] < 6 * 3600:
        h[-1] = point
    else:
        h.append(point)
    return [x for x in h if now - x[0] < STATS_DAYS * 86400][-HIST_POINTS:]


def orientation_trusted(st):
    """Whether the player sizes the API returns tell vertical from horizontal: at least 80% of very short videos (60 s or less,
    nearly all real Shorts) must come back vertical before horizontal ones are treated as not-Shorts."""
    o = st.get("orient") or {}
    n, v = o.get("n", 0), o.get("v", 0)
    return n >= 200 and v / n >= 0.8


def note_orientation(st, vids):
    o = st.setdefault("orient", {"n": 0, "v": 0})
    for x in vids:
        if x.get("dur", 999) <= 60 and x.get("o"):
            o["n"] += 1
            o["v"] += x["o"] == "v"
    if o["n"] > 20000:   # keep it a recent picture
        o["n"], o["v"] = o["n"] // 2, o["v"] // 2


def is_short(v, orient_ok):
    return v["dur"] <= 180 and not (orient_ok and v.get("o") == "h")


def discover(yt, st, queries, channels, pending, now, log, orient_ok):
    rot = rotation(queries)
    q = rot[st["cursor"] % len(rot)]
    st["cursor"] += 1
    text = q["q"]
    ids = yt.search_videos(text, now - WINDOW_DAYS * 86400, q.get("lang"), q.get("region"))
    vids = yt.videos(ids, player=True)
    rows = [v for v in vids.values() if is_short(v, orient_ok) and v["ch_id"]]
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


def probe(radar, yt, ch_id, rec, info, now, first_seen, st, orient_ok):
    """Re-score one channel from its newest uploads. Returns the new channel dict, or None if it has too few Shorts."""
    ids = yt.recent_upload_ids(ch_id, 50)
    vids = yt.videos(ids, player=True)
    note_orientation(st, vids.values())
    shorts = [vids[i] for i in ids if i in vids and is_short(vids[i], orient_ok)][:24]
    series = [{"id": v["id"], "t": v["t"], "v": v["v"], "d": v["dur"], **({"p": round(v["p"])} if v.get("p") else {}),
               **({"c": v["c"]} if v.get("c") is not None else {}), **({"l": v["l"]} if v.get("l") is not None else {}),
               **({"o": v["o"]} if v.get("o") else {}), **({"k": 1} if v.get("k") else {})} for v in shorts]
    subs = info["subs"] if info else rec.get("subs", 0)
    m = radar.compute_metrics(series, subs, now)
    if not m:
        return None
    legacy = is_legacy(rec)
    prev = {} if legacy else {s["id"]: s for s in rec.get("series", [])}
    prev_ts = None if legacy else rec.get("last_probe")
    if prev_ts and now - prev_ts >= 1800:
        h = (now - prev_ts) / 3600
        for s in series:
            if s["id"] in prev:
                s["g"], s["h"] = max(0, s["v"] - prev[s["id"]]["v"]), round(h, 1)
    for s in series:
        if s.get("p") and now - s["p"] < VH_DAYS * 86400:
            s["vh"] = add_vh(s, prev.get(s["id"]), prev_ts, now)
    descr = (info or {}).get("descr") or rec.get("descr", "")
    lang = radar.detect_lang([s["t"] for s in series] + [descr])
    created = (info or {}).get("created") or rec.get("created")
    return {
        "ch_id": ch_id, "name": (info or {}).get("name") or rec.get("name"), "handle": (info or {}).get("handle") or rec.get("handle", ""),
        "subs": subs, "n": m["n"], "med_recent": m["med_recent"], "med_all": m["med_all"], "max_views": m["max_views"],
        "share_100k": m["share_100k"], "share_1m": m["share_1m"], "trend": m["trend"], "peak_vs_med": m["peak_vs_med"], "ratio": m["ratio"],
        "score": m["score"], "tags": m["tags"], "base_n": m["base_n"], "settled_n": m["settled_n"], "mom_n": m["mom_n"], "top_share": m["top_share"],
        "lang": lang, "english": lang in ("en", "und"), "queries": rec.get("queries", []),
        "series": series, "watch": False, "last_probe": now, "descr": descr, "src": "api",
        "avatar_url": (info or {}).get("avatar") or rec.get("avatar_url", ""), "hit_views": rec.get("hit_views", 0), "pick": None,
        "hist": add_hist(rec, now, subs, m), "found": int(first_seen.get(ch_id) or now),
        **({"created": int(created)} if created else {}), **({"avatar_ok": rec["avatar_ok"]} if "avatar_ok" in rec else {}),
    }


def write_json(path, obj, ensure_ascii=True):
    """Writes through a temporary file, so a crash never leaves half a file behind for the cache or the site."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=ensure_ascii), encoding="utf-8")
    tmp.replace(path)


def write_lite(data, path):
    """The hottest channels only, for a fast first screen (sync_avatars.py rewrites it with profile-picture flags)."""
    lite = {k: v for k, v in data.items() if k not in ("channels", "_state", "queries")}
    lite["channels"] = [{k: v for k, v in c.items() if k not in ("hist", "descr")} for c in sorted(data.get("channels", []), key=lambda c: -(c.get("score") or 0))[:300]]
    write_json(path, lite)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev-url", default=os.environ.get("PREV_DATA_URL", ""))
    ap.add_argument("--state", default=os.environ.get("STATE_DIR", ""), help="private folder kept between runs (the workflow caches it)")
    ap.add_argument("--allow-seed", action="store_true", default=os.environ.get("ALLOW_SEED", "").strip().lower() in ("1", "true", "yes"),
                    help="set up a brand-new site from the seed's channel list when no previous state exists")
    ap.add_argument("--seed", default=str(ROOT / "web" / "seed" / "data.json"))
    ap.add_argument("--queries", default=str(ROOT / "web" / "queries.json"))
    ap.add_argument("--picks", default=str(ROOT / "outlier-radar" / "picks.json"))
    ap.add_argument("--extra", default=str(ROOT / "web" / "seed" / "extra.json"))
    ap.add_argument("--out", default=str(ROOT / "_site" / "data.json"))
    args = ap.parse_args()

    started = time.time()
    now = time.time() + float(os.environ.get("SCAN_TIME_OFFSET", "0"))   # the offset exists only so selftest.py can simulate later runs
    radar = load_radar()
    prev, source = load_prev(args.prev_url, args.state, args.seed, args.allow_seed, now)
    if prev is None:
        print(f"REFUSING TO RUN: {source}. Nothing was written, so the site keeps its last good data. "
              "For a brand-new site run the workflow once with 'bootstrap' ticked (or set ALLOW_SEED=1).", file=sys.stderr)
        sys.exit(2)
    channels = {c["ch_id"]: c for c in prev.get("channels", []) if isinstance(c, dict) and c.get("ch_id")}
    st = prev.get("_state") or {}
    day = pacific_day(now)
    if source == "live site" and not prev.get("_state"):   # the public file has no counters: assume the day's share of the budget is spent
        st.update(quota_day=day, quota_used=int(DAILY_BUDGET * day_fraction(now)), searches_used=int(SEARCH_DAILY_CAP * day_fraction(now)))
        print("no saved counters: estimating today's quota use from the time of day", file=sys.stderr)
    pending = st.get("pending", {})
    st.update(pending=pending, cursor=st.get("cursor", 0), runs=st.get("runs", 0) + 1, failed=st.get("failed", {}), qstats=st.get("qstats", {}))
    if st.get("quota_day") != day:
        st.update(quota_day=day, quota_used=0, searches_used=0)
    st.setdefault("searches_used", 0)
    first_seen = st.setdefault("first_seen", {})
    for ch_id, rec in channels.items():   # first-seen dates never reset, even when a channel drops off the list and comes back
        first_seen.setdefault(ch_id, int(rec.get("found") or rec.get("last_probe") or now))
    queries = [q if isinstance(q, dict) else {"q": q} for q in json.loads(Path(args.queries).read_text(encoding="utf-8"))]
    try:
        picks = {p["ch_id"]: p for p in json.loads(Path(args.picks).read_text(encoding="utf-8"))}
    except Exception:
        picks = {}

    log = list((prev.get("status") or {}).get("log", []))[-18:]
    merge_extra(args.extra, pending, channels, st, log)
    fresh_requests = []
    if now - st.get("tracked_at", 0) >= 3600:   # the visitor list is read once an hour, which keeps Firestore reads low
        fresh_requests = fetch_requested(log)
        st["tracked_at"] = int(now)
    tracked = set(sorted(set(st.get("tracked", [])) | set(fresh_requests))[:MAX_TRACKED])   # asked for by visitors: always scanned, never dropped for rank
    for ch_id in tracked:
        if ch_id not in channels and ch_id not in pending and st["failed"].get(ch_id, 0) < 3:
            pending[ch_id] = {"ch_id": ch_id, "name": "", "hit_views": 10 ** 12, "hit_title": "", "queries": [], "requested": True}
    stamp = time.strftime("%H:%M", time.gmtime(now)) + " UTC"
    errors = 0
    key = os.environ.get("YT_API_KEY", "")
    budget, gap = run_budget(st, now)
    st["run_times"] = (st.get("run_times", []) + [int(now)])[-24:]
    orient_ok = orientation_trusted(st)
    fatal = None
    removed = 0
    if not key:
        log.append(f"{stamp}  No YT_API_KEY set; showing the last saved data")
        fatal = "YT_API_KEY missing"
    elif budget < 5:
        log.append(f"{stamp}  Daily scan budget used ({st.get('quota_used', 0)}/{DAILY_BUDGET} units); waiting for tomorrow's reset")
    else:
        yt = YouTube(key, budget)
        try:
            paced = min(SEARCH_DAILY_CAP, int(SEARCH_DAILY_CAP * day_fraction(now)) + 2)   # spread the day's searches out
            if queries and st["runs"] % SEARCH_EVERY == 0 and budget >= 60 and st["searches_used"] < paced and st.get("search_out") != day:
                try:
                    discover(yt, st, queries, channels, pending, now, log, orient_ok)
                except SearchQuotaExceeded:   # the 100-a-day search bucket is empty; re-checking channels still works
                    st["search_out"] = day
                    log.append(f"{stamp}  Searches used up for today; re-checking channels anyway")
            limit = min(PROBES_MAX, max(0, int((budget - 8) / 2.2)))
            targets = pick_targets(channels, pending, st, picks, now, limit)
            infos = yt.channels(targets) if targets else {}
            done = 0
            for ch_id in targets:
                if time.time() - started > RUN_SECONDS:
                    log.append(f"{stamp}  Stopped re-checking after {RUN_SECONDS // 60} minutes; the rest wait for the next run")
                    break
                rec = channels.get(ch_id) or pending.get(ch_id) or {}
                try:
                    new = probe(radar, yt, ch_id, rec, infos.get(ch_id), now, first_seen, st, orient_ok)
                except QuotaExceeded:
                    raise
                except ApiError as e:
                    errors += 1
                    new = False
                    log.append(f"{stamp}  Skipped a channel: {e}")
                if not new:
                    st["failed"][ch_id] = st["failed"].get(ch_id, 0) + 1
                    give_up = new is None or st["failed"][ch_id] >= 3 or (ch_id in channels and is_legacy(channels[ch_id]) and st["failed"][ch_id] >= 2)
                    if give_up:
                        pending.pop(ch_id, None)
                        if channels.pop(ch_id, None) is not None:
                            removed += 1
                    continue
                st["failed"].pop(ch_id, None)
                first_seen.setdefault(ch_id, int(now))
                channels[ch_id] = new
                pending.pop(ch_id, None)
                done += 1
            crawl_featured(yt, st, channels, pending, now, log, FEATURED_PER_RUN)
            log.append(f"{stamp}  Re-checked {done}/{len(targets)} channels using {yt.used} of {budget} units")
        except QuotaExceeded as e:
            log.append(f"{stamp}  Stopped early: {e}")
        except ApiError as e:
            errors += 1
            fatal = str(e)
            log.append(f"{stamp}  YouTube API problem: {e}")
        st["quota_used"] = st.get("quota_used", 0) + yt.used
        st["searches_used"] += yt.searches

    for ch_id in [c for c, rec in channels.items() if now - (rec.get("last_probe") or 0) > RETAIN_DAYS * 86400]:   # not refreshable for a month
        del channels[ch_id]
        removed += 1
    if removed:
        log.append(f"{stamp}  Removed {removed} channels the API could no longer refresh")
    for ch_id, ch in channels.items():
        ch["pick"] = picks.get(ch_id)
    ranked = sorted(channels.values(), key=lambda c: -(c.get("score") or 0))[:KEEP_CHANNELS]
    keep = {c["ch_id"] for c in ranked}
    ranked += sorted((channels[i] for i in tracked if i in channels and i not in keep), key=lambda c: -(c.get("score") or 0))
    keep |= tracked
    public = [c for c in ranked if not is_legacy(c)]   # old imports stay private until the API has refreshed them
    keep_ids = {c["ch_id"] for c in ranked} | set(pending)
    first_seen = {k: v for k, v in first_seen.items() if k in keep_ids or now - v < RETAIN_DAYS * 86400}   # channels that left: kept no longer than YouTube's 30 days
    if len(first_seen) > 20000:
        first_seen = dict(sorted(first_seen.items(), key=lambda kv: -kv[1])[:20000])
    fresh24 = sum(1 for c in public if now - (c.get("last_probe") or 0) < 86400)
    o = st.get("orient") or {}
    status = {"state": "idle", "phase": "", "done": 0, "total": 0, "cycles": st["runs"], "errors": errors, "cycle_started": now,
              "last_cycle_end": now, "next_scan": None, "message": log[-1] if log else "", "tracked": len(channels) + len(pending),
              "scored": len(public), "log": log[-25:], "checked_24h": fresh24, "run_gap_h": round(gap / 3600, 1),
              "quota": {"used_today": st.get("quota_used", 0), "scanner_cap": DAILY_BUDGET, "searches_today": st["searches_used"],
                        "search_cap": SEARCH_DAILY_CAP, "day": day, "run_budget": budget}}
    state = {**{k: st[k] for k in ("quota_day", "quota_used", "searches_used", "runs", "cursor", "qstats")},
             "extra_hash": st.get("extra_hash", ""), "featured": {k: v for k, v in st.get("featured", {}).items() if now - v < 8 * 86400},
             "pending": dict(list(pending.items())[:3000]), "tracked": sorted(tracked)[:3000], "tracked_at": st.get("tracked_at", 0),
             "failed": {k: v for k, v in st["failed"].items() if k in keep_ids}, "run_times": st["run_times"], "first_seen": first_seen,
             "orient": o, "search_out": st.get("search_out", "")}
    queries_out = [{"query": k, "runs": v["runs"], "last_run": v.get("last_run"), "last_rows": v.get("last_rows", 0), "total_rows": v.get("total_rows", 0)}
                   for k, v in sorted(st["qstats"].items())]
    data = {"generated": now, "readonly": True, "web": True, "status": status, "channels": public, "queries": queries_out, "config": {},
            "orient": {"trusted": orientation_trusted(st), "n": o.get("n", 0), "vertical": round(o["v"] / o["n"], 3) if o.get("n") else None}}
    if args.state:   # the full record, legacy imports and counters included, for the next run only
        write_json(Path(args.state) / "data.json", {**data, "channels": ranked, "_state": state})
    out = Path(args.out)
    write_json(out, data)
    write_lite(data, out.parent / "data-lite.json")
    write_json(out.parent / "version.json", {"generated": now})
    print(f"state from {source}; {len(public)} channels published ({len(ranked) - len(public)} old imports waiting for a check), {len(pending)} pending; "
          f"quota used today {st.get('quota_used', 0)} (this run's budget {budget}, typical gap {gap / 3600:.1f} h); wrote {out}")
    if fatal:
        print(f"WARNING: {fatal}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
