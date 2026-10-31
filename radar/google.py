"""Google's own numbers, through Monid's DataForSEO (and AIsa's, when Monid's answer does
not come back).

Google's own numbers, answered from the DataForSEO that Monid resells:
the first page of a search (its items, Google's own count of the
results, the ads on it), the daily Trends series of one country, and the related
queries. The readings that need more than Google are elsewhere: a domain rating and a
site's traffic from Monid (radar/monid.py), the other sites on an AdSense account from
the sites' own ads.txt (radar/adsense.py), and the Chrome Web Store's reviews from the
store's own call (radar/cws.py).

A call that fails is never fatal: the reading is left out and the terms that failed are
named, so one bad term does not empty a table.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import dfs

TRIES = 3                 # a search DataForSEO's own server failed on is asked again (it fails about half the time)
WORKERS = 4               # the Trends calls in flight at once
TRENDS_DAYS = 90          # the window the Trends call asks for: this long comes back by the day
TRENDS_PATH = dfs.AISA_REST[dfs.TRENDS]    # the same reading through AIsa, when Monid's does not come back
# Google's own location codes (the Trends endpoint's dictionary, the same one the Ads
# endpoint uses). The Trends endpoint refuses a location name together with a date range,
# so the code is what goes in.
LOCATIONS = {"US": 2840, "GB": 2826, "CA": 2124, "AU": 2036, "IN": 2356, "DE": 2276,
             "FR": 2250, "ES": 2724, "IT": 2380, "NL": 2528, "BR": 2076, "JP": 2392,
             "KR": 2410}


def location(geo: str) -> int | str:
    """The location code for a country code ("US" -> 2840). A place that is not a country
    code is passed on as the name it already is."""
    code = geo.strip().upper()
    return LOCATIONS.get(code, geo.strip())


def serp(keyword: str, location_code: int = 2840, language: str = "en",
         device: str = "desktop", depth: int = 10) -> tuple[dict, int]:
    """{"items": [...], "results_count": int|None} for a search, and the cost.

    `results_count` is Google's own count of the results, the number the `intitle:`
    reading wants.

    A search that DataForSEO's own server failed on is asked again (a quarter of the calls
    fail that way, and the same one fails again just as often), and a failure that is
    neither the server's fault nor the gateway's is raised, so the caller names it instead
    of reading a search that never happened as an empty one.
    """
    body = {"keyword": keyword, "location_code": location_code, "language_code": language,
            "device": device, "depth": depth}
    last = ""
    for _ in range(TRIES):
        try:
            result, cost = dfs.result(dfs.SERP, body)
        except dfs.DfsError as exc:
            last = str(exc)
            continue
        try:
            count = int(result.get("se_results_count"))
        except (TypeError, ValueError):
            count = None
        return {"items": result.get("items") or [], "results_count": count}, cost
    raise dfs.DfsError(last)


def organic(items: list[dict]) -> list[dict]:
    """The organic results on the page: [{"url", "title", "rank"}]."""
    return [{"url": i.get("url") or "", "title": i.get("title") or "", "rank": i.get("rank_group")}
            for i in items if i.get("type") == "organic"]


def advertisers(items: list[dict]) -> list[str]:
    """The domains running ads on the page: what a paid item names."""
    return list(dict.fromkeys(re.sub(r"^www\.", "", i.get("domain") or "") for i in items
                               if i.get("type") == "paid" and i.get("domain")))


def intitle_count(term: str, location_code: int = 2840, language: str = "en") -> tuple[int | None, int]:
    """How many pages Google holds with `term` in the title, and the cost.

    Google's own count of the results of an `intitle:` search. The count is Google's
    own, not a window's worth of new pages: a search for the last month would count
    only what appeared in it, this reads what Google says today.
    """
    out, cost = serp(f'intitle:"{term}"', location_code, language)
    return out["results_count"], cost


def _trends(term: str, geo: str, item_types: list[str],
            date_from: str | None = None, date_to: str | None = None) -> tuple[dict, int]:
    """One Trends Explore task: its result and the cost."""
    body = {"keywords": [term], "item_types": item_types}
    place = location(geo)
    if isinstance(place, int):
        body["location_code"] = place
    else:
        body["location_name"] = place
    if date_from:
        body["date_from"] = date_from
    if date_to:
        body["date_to"] = date_to
    return dfs.result(dfs.TRENDS, body)


def _queries(rows: list) -> list[str]:
    """The query names of a related-queries list, the way the radar reads them."""
    out = []
    for x in rows or []:
        name = x.get("query") if isinstance(x, dict) else x
        name = " ".join(str(name or "").lower().split())
        if name:
            out.append(name)
    return out


def _item(result: dict, kind: str) -> dict:
    """The Trends item of one kind, whichever door answered it: the type is
    "google_trends_graph" through Monid and "dataforseo_trends_graph" through AIsa."""
    return next((i for i in result.get("items") or [] if kind in str(i.get("type", ""))), {})


def related_queries(terms: list[str], geo: str = "US") -> tuple[dict[str, dict[str, list[str]]], int, list[str]]:
    """{term: {"rising": [...], "top": [...]}}: what people searched on Google together
    with each term. The cost, and the terms whose call failed."""
    def one(term: str):
        try:
            result, spent = _trends(term, geo, ["google_trends_queries_list"])
            data = _item(result, "queries").get("data") or {}
            if not isinstance(data, dict):
                data = {}
            return term, {k: _queries(data.get(k)) for k in ("rising", "top")}, spent, ""
        except dfs.DfsError as exc:
            return term, None, 0, f"{term}: {exc}"
    out, cost, errors = {}, 0, []
    with ThreadPoolExecutor(WORKERS) as pool:
        for term, groups, spent, error in pool.map(one, terms):
            if error:
                errors.append(error)
                continue
            cost += spent
            out[term] = groups
    return out, cost, errors


def daily_series(terms: list[str], geo: str = "US", today: date | None = None) -> tuple[dict[str, list], int, list[str]]:
    """{term: [(day, value)]}: the daily Trends series of one country, and the cost and
    the terms whose call failed.

    The series is read for the country the config names, so a rise there is visible
    where the worldwide series is flat. The last days are not settled by Google yet, so
    the reader drops them (radar/review.py).
    """
    today = today or date.today()
    window = {"date_from": (today - timedelta(days=TRENDS_DAYS)).isoformat(), "date_to": today.isoformat()}
    def one(term: str):
        try:
            result, spent = _trends(term, geo, ["google_trends_graph"], window["date_from"], window["date_to"])
            item = _item(result, "graph")
            points = []
            for p in item.get("data") or []:
                try:
                    points.append((date.fromisoformat(p["date_from"]), int((p.get("values") or [0])[0] or 0)))
                except (KeyError, TypeError, ValueError):
                    continue
            return term, points, spent, ""
        except dfs.DfsError as exc:
            return term, None, 0, f"{term}: {exc}"
    out, cost, errors = {}, 0, []
    with ThreadPoolExecutor(WORKERS) as pool:
        for term, points, spent, error in pool.map(one, terms):
            if error:
                errors.append(error)
                continue
            cost += spent
            out[term] = points
    return out, cost, errors
