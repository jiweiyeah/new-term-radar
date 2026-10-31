"""Signal ⑨: what was just listed where people launch, rank and talk about things.

Each feed reads one list and the radar keeps what it has not seen before (state/feeds.json.gz);
the first run of a feed only takes its snapshot. Names go to the leads like any other signal.

Free:
- Product Hunt's Atom feed of featured launches;
- Show HN on Hacker News (Algolia's API), posts with `hn_points` or more;
- OpenRouter's model list and Civitai's newest checkpoints: new model names;
- the App Store's top-grossing apps per genre (Apple's RSS; the legacy feed, which has grossing
  and genres, first, the marketing-tools one when it fails);
- new entries in awesome lists on GitHub (`awesome`: raw README URLs).

Paid, small:
- `google_new`: pages Google indexed in the last week under a path (DataForSEO through Monid, about
  $0.02 a path): Figma community plugins, There's An AI For That tool pages,
  Chrome Web Store items. The sites themselves are not touched: Figma answers bots with 403 and
  There's An AI For That with a Cloudflare challenge.
- `trending`: trending searches on Douyin and TikTok, trending TikTok hashtags and Reddit's
  trending searches (TikHub through Monid, $0.001 each), kept only when they name a tool, an
  effect, a template or AI.
- `rising`: the root-word method. For each seed ("ai generator", "ai detector", "ai converter" ...)
  the searches Google Trends calls rising next to it this month (DataForSEO through Monid, about
  $0.011 a seed).
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from . import UA, dfs, google, monid, net, snap
from .i18n import t

TIMEOUT = 20
WEEK = "tbs=qdr:w"     # Google's own filter: the pages it indexed in the last week
# "AI换脸" has no word boundary between I and 换, so the Latin words are fenced by letters, not by \b
TOOLISH = re.compile(r"(?<![a-z])(ai|app|tool|generator|filter|effect|template|prompt|editor|maker|bot|gpt|agent|plugin|extension|"
                     r"converter|translator|avatar)s?(?![a-z])|特效|模板|滤镜|工具|生成|剪辑|一键|智能|插件", re.I)
LABEL = {"producthunt": "Product Hunt", "hn": "Show HN", "openrouter": "OpenRouter", "civitai": "Civitai", "appstore": "App Store",
         "awesome": "awesome list", "google_new": "Google, new this week", "trending": "trending", "rising": "rising with"}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, application/xml;q=0.9, */*;q=0.8"})
    with net.fetch(req, TIMEOUT) as res:
        return res.read()


def recent(stamp: str, days: int = 2) -> bool:
    """Whether an ISO date or datetime falls in the last `days` days."""
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).days < days


def clean(text: str, n: int = 200) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())[:n]


def producthunt(c: dict) -> tuple[list[dict], int]:
    xml = get("https://www.producthunt.com/feed").decode("utf-8", "replace")
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        title = re.search(r"<title>(.*?)</title>", entry, re.S)
        link = re.search(r'<link[^>]+href="([^"]+)"', entry)
        content = re.search(r"<content[^>]*>(.*?)</content>", entry, re.S)
        published = re.search(r"<published>(.*?)</published>", entry)
        if title and link:
            out.append({"key": link.group(1), "name": clean(title.group(1), 80), "url": link.group(1),
                        "about": clean(html.unescape(content.group(1)) if content else ""), "fresh": recent(published.group(1) if published else "")})
    return out, 0


def show_hn(c: dict) -> tuple[list[dict], int]:
    since = int(time.time()) - 2 * 86400
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&hitsPerPage=100&numericFilters="
           f"created_at_i>{since},points>={c['hn_points']}")
    out = []
    for h in json.loads(get(url)).get("hits", []):
        m = re.match(r"show hn:\s*(.+?)(?:\s+[–—-]\s+|:\s+|$)", h.get("title") or "", re.I)
        if m:
            out.append({"key": h["objectID"], "name": m.group(1).strip()[:80], "url": h.get("url") or
                        f"https://news.ycombinator.com/item?id={h['objectID']}", "about": clean(h.get("title") or ""),
                        "score": h.get("points") or 0, "fresh": True})          # the query asks for the last two days
    return out, 0


def openrouter(c: dict) -> tuple[list[dict], int]:
    since = time.time() - 7 * 86400
    models = json.loads(get("https://openrouter.ai/api/v1/models")).get("data", [])
    return [{"key": m["id"], "name": (m.get("name") or m["id"]).split(": ", 1)[-1][:80], "url": f"https://openrouter.ai/{m['id']}",
             "about": clean(m.get("description") or ""), "fresh": True} for m in models if (m.get("created") or 0) >= since], 0


def civitai(c: dict) -> tuple[list[dict], int]:
    data = json.loads(get("https://civitai.com/api/v1/models?sort=Newest&types=Checkpoint&limit=50"))
    return [{"key": str(m["id"]), "name": m["name"][:80], "url": f"https://civitai.com/models/{m['id']}",
             "about": clean(m.get("description") or ""),
             "fresh": recent(((m.get("modelVersions") or [{}])[0] or {}).get("publishedAt") or "")} for m in data.get("items", [])], 0


def appstore(c: dict) -> tuple[list[dict], int]:
    out = []
    for genre in c["app_genres"]:
        try:
            feed = json.loads(get(f"https://itunes.apple.com/us/rss/topgrossingapplications/limit=50/genre={genre}/json"))["feed"]
            for e in feed.get("entry", []):
                out.append({"key": e["id"]["attributes"]["im:id"], "name": e["im:name"]["label"][:80],
                            "url": e["id"]["label"], "about": clean(e.get("summary", {}).get("label", ""))})
        except (OSError, ValueError, KeyError):
            data = json.loads(get("https://rss.marketingtools.apple.com/api/v2/us/apps/top-free/50/apps.json"))
            out += [{"key": a["id"], "name": a["name"][:80], "url": a["url"], "about": ""} for a in data["feed"]["results"]]
            break
    return out, 0


ENTRY = re.compile(r"^\s*[-*]\s+\[([^\]]{2,80})\]\((https?://[^)\s]+)\)\s*[-–—:]?\s*(.*)$", re.M)


def awesome(c: dict) -> tuple[list[dict], int]:
    out = []
    for url in c["awesome"]:
        text = get(url).decode("utf-8", "replace")
        out += [{"key": link, "name": clean(name, 80), "url": link, "about": clean(desc)} for name, link, desc in ENTRY.findall(text)]
    return out, 0


class PartialFailure(RuntimeError):
    """A reading that failed in part: the rows it did get, and the calls that failed."""

    def __init__(self, rows: list[dict], cost: int, errors: list[str]):
        super().__init__("; ".join(errors))
        self.rows, self.cost, self.errors = rows, cost, errors


def _serp(path: str) -> tuple[list[dict], int]:
    """The pages Google has under a path, the ones of the last week first.

    The week filter is Google's own (`search_param`); an endpoint that refuses it answers
    the plain search instead, and the pages it lists are what there is.
    """
    body = {"keyword": f"site:{path}", "location_code": 2840, "language_code": "en",
            "device": "desktop", "depth": 100}
    try:
        return dfs.rows(dfs.SERP, {**body, "search_param": WEEK})
    except dfs.DfsError:
        return dfs.rows(dfs.SERP, body)


def google_new(c: dict) -> tuple[list[dict], int]:
    """Pages Google indexed in the last seven days under each path."""
    out, cost, errors = [], 0, []
    for path in c["google_new"]:
        try:
            rows, spent = _serp(path)
        except dfs.DfsError as exc:
            errors.append(f"{path}: {exc}")
            continue
        cost += spent
        for i in rows:
            if i.get("type") == "organic" and i.get("url"):
                name = re.split(r"\s+[|–—-]\s+", i.get("title") or "")[0]
                out.append({"key": i["url"], "name": clean(name, 80), "url": i["url"], "about": clean(i.get("description") or ""),
                            "where": path, "fresh": True})                   # indexed in the last seven days
    if errors:
        raise PartialFailure(out, cost, errors)
    return out, cost


def _words(payload: dict, keys: tuple[str, ...], field: str) -> list[str]:
    """The words a TikHub answer holds under `keys`, in the field it names them by."""
    rows: list = []
    for k in keys:
        rows += payload.get(k) or (payload.get("data") or {}).get(k) or []
    return [str(r.get(field) or "").strip() for r in rows if isinstance(r, dict)]


TRENDING = {
    "douyin": ("/api/v1/douyin/app/v3/fetch_hot_search_list", {},
               lambda d: _words(d, ("word_list", "trending_list"), "word")),
    "tiktok": ("/api/v1/tiktok/web/fetch_trending_searchwords", {},
               lambda d: _words(d, ("trending_search_words",), "trendingSearchWord")),
    "tiktok_tags": ("/api/v1/tiktok/ads/get_trends_hashtag_list", {"country_code": "US", "time_range": 7, "limit": 50},
                    lambda d: _words(d, ("items",), "hashtagName")),
    "reddit": ("/api/v1/reddit/app/fetch_trending_searches", {},
               lambda d: _words(d, ("trendingQueries",), "displayQuery")),
}


def trending(c: dict) -> tuple[list[dict], int]:
    """Trending searches and hashtags: TikHub through Monid, about $0.001 a list."""
    out, cost, errors = [], 0, []
    for k in c["trending"]:
        endpoint, params, words_of = TRENDING[k]
        try:
            answer, spent = monid.run("tikhub", endpoint, query=params)
        except monid.MonidError as exc:
            errors.append(f"{k}: {exc}")
            continue
        cost += spent
        payload = answer.get("data") if isinstance(answer, dict) and isinstance(answer.get("data"), dict) else answer
        words = words_of(payload) if isinstance(payload, dict) else []
        out += [{"key": f"{k}:{w}", "name": w[:80], "url": "", "about": "", "where": k, "fresh": True} for w in words if w and TOOLISH.search(w)]
    if errors:
        raise PartialFailure(out, cost, errors)
    return out, cost


def rising(c: dict) -> tuple[list[dict], int]:
    """The rising searches next to each root word ("ai generator", "ai detector" ...):
    Google Trends' related queries for each seed, read through DataForSEO and Monid
    (radar/google.py). A seed whose call failed simply brings nothing."""
    out, cost = [], 0
    groups, spent, failed = google.related_queries(c["seeds"], c.get("related_geo", "US"))
    cost += spent
    for seed in c["seeds"]:
        head = [w for w in seed.split() if w != "ai"] or seed.split()
        # Trends answered "ai generator" with "best pizza near me" and "how to bake a cake" on 2026-10-15: a rising
        # search that does not share the seed's word is the day's general news, not something people build tools for
        out += [{"key": q, "name": q, "url": "", "about": "", "where": seed, "fresh": True} for q in groups.get(seed, {}).get("rising", [])
                if q != seed and any(w in q.split() for w in head)]
    if failed:
        raise PartialFailure(out, cost, failed)
    return out, cost


FEEDS = {"producthunt": producthunt, "hn": show_hn, "openrouter": openrouter, "civitai": civitai, "appstore": appstore,
         "awesome": awesome, "google_new": google_new, "trending": trending, "rising": rising}


def feeds(cfg: dict, state: Path, today: date | None = None, weekly: bool = False) -> dict:
    """Every enabled feed; the `weekly` ones (paid searches) only on `weekday` or when asked."""
    c = cfg["feeds"]
    today = today or date.today()
    weekly = weekly or today.weekday() == c["weekday"]
    path = state / "feeds.json.gz"
    seen: dict[str, str] = snap.load(path) or {}
    out = {"read": {}, "new": {}, "first_run": [], "errors": [], "cost_micros": 0, "terms": []}
    for name in c["enabled"]:
        if name in c["weekly"] and not weekly:
            continue
        try:
            items, cost = FEEDS[name](c)
        except PartialFailure as exc:
            items, cost = exc.rows, exc.cost       # what came in is kept; the failures are named
            out["errors"] += [f"{name}: {e[:120]}" for e in exc.errors]
        except (OSError, ValueError, KeyError, TypeError, dfs.DfsError, monid.MonidError) as exc:
            out["errors"].append(f"{name}: {str(exc)[:120]}")
            continue
        out["cost_micros"] += cost
        out["read"][name] = len(items)
        first = not any(k.startswith(name + ":") for k in seen)
        fresh = [i for i in items if f"{name}:{i['key']}" not in seen]
        for i in fresh:
            seen[f"{name}:{i['key']}"] = today.isoformat()
        if first:               # the snapshot is taken; what the source itself dates as recent is news already
            out["first_run"].append(name)
            fresh = [i for i in fresh if i.get("fresh")]
            if not fresh:
                continue
        fresh = sorted(fresh, key=lambda i: -(i.get("score") or 0))[:c["max_per_feed"]]
        out["new"][name] = fresh
        out["terms"] += [{"term": i["name"].lower(), "recent": 1, "about": (i["url"], i["about"]) if i["url"] else None,
                          "examples": [f"{LABEL[name]}{' · ' + i['where'] if i.get('where') else ''}: {i['name']}"],
                          "note": t("note_feed", where=LABEL[name])} for i in fresh]
    snap.save(path, seen)
    out["terms"] = [{k: v for k, v in r.items() if v is not None} for r in out["terms"]]
    return out
