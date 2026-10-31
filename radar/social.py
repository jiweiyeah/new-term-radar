"""Where people ask for things in their own words: Reddit posts and YouTube comments, through Monid.

reddit(): Monid's AnyAPI Reddit search, about $0.0004 a query, with title, text, score, comments,
subreddit, link and creation time. youtube_comments(): the top-level comments of a video through
Monid's AnyAPI (YouTube), about $0.0012 a page of 20. Both are read for research: quote them, do
not republish them.
"""
from __future__ import annotations

from . import monid

REDDIT_TOOL = "/reddit.search"
COMMENTS_TOOL = "/youtube.video_comments"


def _reddit_posts(rows: list[dict]) -> list[dict]:
    """The search's posts, the way the radar reads them."""
    posts = []
    for p in rows:
        if p.get("nsfw") or p.get("over_18"):
            continue
        posts.append({"id": p.get("id") or p.get("post_id") or "", "title": " ".join(str(p.get("title") or "").split()),
                      "text": " ".join(str(p.get("selftext") or "").split())[:600], "score": int(p.get("score") or 0),
                      "comments": int(p.get("numComments") or p.get("num_comments") or 0),
                      "subreddit": p.get("subreddit") or "",
                      "url": "https://www.reddit.com" + (p.get("permalink") or ""),
                      "created": float(p.get("createdUtc") or p.get("created_utc") or 0)})
    return posts


def reddit(query: str, time: str = "week", sort: str = "top", limit: int = 25) -> tuple[list[dict], int]:
    rows, cost = monid.anyapi_rows(REDDIT_TOOL, {"query": query, "sort": sort, "timeframe": time})
    return _reddit_posts(rows)[:limit], cost


def youtube_comments(video_id: str) -> tuple[list[dict], int]:
    rows, cost = monid.anyapi_rows(COMMENTS_TOOL, {"url": f"https://www.youtube.com/watch?v={video_id}"})
    return [{"text": " ".join(str(c.get("text") or c.get("content") or "").split()), "likes": int(c.get("likes") or 0)}
            for c in rows], cost
