"""Word age: when did a term first draw searches?

A term nobody searched half a year ago has no settled page one yet: the difficulty scores
and site ratings that crowd it out are read from pages written for something else. For the
rows of the keyword table that would be acted on (prioritize and watch) this reads the
term's interest over the longest range the Trends source offers (DataForSEO Trends,
`past_5_years`, one point a week) and takes the first period with any interest as
`first_seen`. `is_new` = first_seen in the last `new_days` (180) days; competition.verdict
then reads its page one one step easier.

The last period of the series is the week still running, and the last two days of any
series are not settled (they read "0, 100" for unrelated terms): points are keyed on the
day their period ends and passed through review.settled(), so neither can make an old
term look new or a dead one look alive. A term whose interest is above zero from the first
point is older than the range: `since_start`, never new.

What it cannot tell: a term made of words people searched before ("nano banana" reads a
little interest in 2021) is dated by those older searches.

Cost seen in October 2026: one Trends call per row through Monid, $0.011.
"""
from __future__ import annotations

from datetime import date

from . import dfs, review


def series(result: dict) -> list[tuple[date, int, date]]:
    """(the day the period ends, value, the day it starts) from one Trends result, the graph item only."""
    items = result.get("items") or []
    graph = next((i for i in items if "graph" in str(i.get("type", ""))), items[0] if items else {})
    out = []
    for p in graph.get("data") or []:
        if p.get("missing_data"):
            continue
        try:
            start = date.fromisoformat(p["date_from"])
            out.append((date.fromisoformat(p.get("date_to") or p["date_from"]), int((p.get("values") or [0])[0] or 0), start))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda p: p[2])


def first_seen(points: list[tuple[date, int, date]], today: date, new_days: int = 180) -> dict:
    """{"first_seen", "is_new", "since_start"} from a series of (period end, value, period start)."""
    points = review.settled(points, today)
    lit = [p for p in points if p[1] > 0]
    if not lit:
        return {"first_seen": None, "is_new": False, "since_start": False, "no_data": True}
    first = lit[0][2]
    since_start = first == points[0][2]
    return {"first_seen": first.isoformat(), "since_start": since_start, "no_data": False,
            "is_new": not since_start and (today - first).days <= new_days}


def assess(rows: list[dict], cfg: dict, today: date) -> tuple[int, list[str]]:
    """Adds `age` and `is_new` to each row. (cost in micro-USD, errors)"""
    c = cfg["word_age"]
    if not c.get("enabled") or not rows:
        return 0, []
    cost, errors, last = 0, [], ""
    for r in rows:
        try:
            result, spent = dfs.result(dfs.TRENDS, {"keywords": [r["phrase"]], "time_range": c["time_range"]})
        except dfs.DfsError as exc:
            last = str(exc)
            errors.append(f"{r['phrase']} (word age)")
            continue
        cost += spent
        r["age"] = first_seen(series(result), today, c["new_days"])
        r["is_new"] = r["age"]["is_new"]
    if len(errors) == len(rows):
        errors.append(f"word age: {last}")
    return cost, errors
