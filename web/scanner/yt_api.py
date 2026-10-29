"""Minimal YouTube Data API v3 client (standard library only) with quota accounting.

Google's published default: 100 search.list calls a day (a bucket of their own), plus 10,000 units a day for all other endpoints.
So a search costs nothing from the units budget here; the scanner caps its number of searches separately.
The API key is sent as a query parameter, so error messages here never include the request URL.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("YT_API_BASE", "https://www.googleapis.com/youtube/v3")
COST = {"search": 0}   # search.list has its own daily bucket of 100 calls


class QuotaExceeded(Exception):
    pass


class ApiError(Exception):
    pass


class SearchQuotaExceeded(ApiError):
    """The 100-a-day search.list bucket is empty. Every other method still works, so callers carry on without searching."""


def parse_duration(iso):
    """ISO 8601 duration like PT1M5S -> seconds."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 10 ** 6
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def orientation(player):
    """'v', 'h' or 's' from the embedded player's width and height, or None when YouTube didn't say."""
    try:
        w, h = int(player.get("embedWidth") or 0), int(player.get("embedHeight") or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return "v" if w < h * 0.9 else "h" if w > h * 1.1 else "s"


def iso_to_ts(s):
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


class YouTube:
    def __init__(self, key, budget):
        self.key, self.budget, self.used, self.searches = key, budget, 0, 0   # searches: calls to search.list (Google caps these separately)

    def call(self, path, **params):
        cost = COST.get(path, 1)
        if self.used + cost > self.budget:
            raise QuotaExceeded("this run's budget is used up")
        params["key"] = self.key
        url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
        last = "unknown"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=30) as r:
                    data = json.load(r)
                self.used += cost
                if path == "search":
                    self.searches += 1
                return data
            except urllib.error.HTTPError as e:
                try:
                    body = e.read().decode("utf-8", "replace")
                except Exception:
                    body = ""
                if e.code == 403 and "quota" in body.lower():
                    if path == "search":   # searches have their own bucket: only searching stops
                        raise SearchQuotaExceeded("YouTube says today's searches are used up")
                    self.used = self.budget
                    raise QuotaExceeded("YouTube says today's quota is used up")
                if e.code in (500, 502, 503) and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                reason = re.search(r'"message":\s*"([^"]{0,160})', body)
                raise ApiError(f"HTTP {e.code} on {path}: {reason.group(1) if reason else 'no detail'}")
            except urllib.error.URLError as e:
                last = str(getattr(e, "reason", e))[:80]
                time.sleep(2 * (attempt + 1))
        raise ApiError(f"network problem on {path}: {last}")

    # ---- helpers -------------------------------------------------------------------------------
    def search_videos(self, query, published_after_ts, language=None, region=None):
        """Most-viewed short videos for a phrase since a date. Returns list of video ids (100 units)."""
        import datetime as dt
        params = {"part": "id", "type": "video", "order": "viewCount", "videoDuration": "short", "maxResults": 50, "q": query,
                  "publishedAfter": dt.datetime.fromtimestamp(published_after_ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if language:
            params["relevanceLanguage"] = language
        if region:
            params["regionCode"] = region
        data = self.call("search", **params)
        return [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]

    def videos(self, ids, player=False):
        """Details for up to 50 video ids per call (1 unit each call, whatever parts are asked for).
        player=True also asks for the embedded player's size, which Google documents is returned when the video's
        aspect ratio is known; it becomes "o": "v" (vertical), "h" (horizontal) or "s" (square).
        "k" is YouTube's Made for Kids flag: the developer policies require looking it up for every video a site embeds."""
        out = {}
        ids = list(dict.fromkeys(ids))
        extra = {"part": "snippet,statistics,contentDetails,status,player", "maxHeight": 480} if player else {"part": "snippet,statistics,contentDetails,status"}
        for i in range(0, len(ids), 50):
            data = self.call("videos", id=",".join(ids[i:i + 50]), **extra)
            for it in data.get("items", []):
                st = it.get("statistics", {})
                if "viewCount" not in st:
                    continue
                out[it["id"]] = {
                    "id": it["id"], "t": it["snippet"].get("title", ""), "v": int(st["viewCount"]), "ch_id": it["snippet"].get("channelId"),
                    "ch": it["snippet"].get("channelTitle"), "dur": parse_duration(it.get("contentDetails", {}).get("duration")),
                    "p": iso_to_ts(it["snippet"].get("publishedAt", "")),
                    "l": int(st["likeCount"]) if "likeCount" in st else None, "c": int(st["commentCount"]) if "commentCount" in st else None,
                    "o": orientation(it.get("player") or {}), "k": bool((it.get("status") or {}).get("madeForKids")),
                }
        return out

    def channels(self, ids):
        out = {}
        ids = list(dict.fromkeys(ids))
        for i in range(0, len(ids), 50):
            data = self.call("channels", part="snippet,statistics", id=",".join(ids[i:i + 50]))
            for it in data.get("items", []):
                sn, st = it.get("snippet", {}), it.get("statistics", {})
                thumbs = sn.get("thumbnails", {})
                avatar = (thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {}).get("url", "")
                out[it["id"]] = {
                    "id": it["id"], "name": sn.get("title"), "handle": sn.get("customUrl") or "",
                    "subs": 0 if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
                    "descr": re.sub(r"\s+", " ", sn.get("description", "")).strip()[:400], "avatar": avatar,
                    "created": iso_to_ts(sn.get("publishedAt", "")),
                }
        return out

    def featured_channels(self, ch_id):
        """Channels a creator features on their own channel page (1 unit). Often similar channels in the same niche."""
        data = self.call("channelSections", part="contentDetails", channelId=ch_id)
        return [c for it in data.get("items", []) for c in (it.get("contentDetails") or {}).get("channels", []) if isinstance(c, str)]

    def recent_upload_ids(self, ch_id, n=50):
        """A channel's newest uploads (the uploads playlist id is the channel id with UC replaced by UU). 1 unit."""
        data = self.call("playlistItems", part="contentDetails", playlistId="UU" + ch_id[2:], maxResults=n)
        return [i["contentDetails"]["videoId"] for i in data.get("items", []) if i.get("contentDetails", {}).get("videoId")]
