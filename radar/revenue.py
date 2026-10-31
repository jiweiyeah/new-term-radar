"""Signal ⑥: products people pay for, rising (TrustMRR).

A product that makes money proves the demand behind it. TrustMRR lists startups whose revenue
is read from their payment provider (Stripe, LemonSqueezy, Polar, Creem, Paddle and others).
With a TrustMRR API key this reads, once a week by default (revenue.weekday, 0 = Monday):

- the fastest growing over the last 30 days, among those with at least `min_revenue_usd` in that time;
- the most recently listed, which mostly earn next to nothing yet: they are read to record where
  they start;

and keeps what it saw, so a product that made nothing last time and earns past the floor now is
called out first (the habit: "zero last month, or surging: look at those first"). Each product becomes a lead, with its own description handed to the keyword table as
evidence: the table then reads the job people pay for ("ai headshot generator"), not only the
brand. A run is `pages_growth + pages_new` requests; a standard key allows 10 a minute.

TrustMRR's API policy forbids bulk republication and public SEO pages built from its data: the
leads are for your own research, do not publish the list. Key: TRUSTMRR_API_KEY.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from . import UA, net, snap
from .i18n import t

API = "https://trustmrr.com/api/v1/startups"
LEGAL = re.compile(r",?\s*\b(inc|llc|l\.l\.c|ltd|limited|gmbh|corp|corporation|pte|s\.a|s\.l)\b\.?", re.I)
HUGE = 10                   # growth over 1,000% means it started from almost nothing: say that instead
PAUSE = 6.5                 # seconds between requests: 10 a minute on a standard key
PER_PAGE = 10               # the API's maximum


def key() -> str:
    return os.environ.get("TRUSTMRR_API_KEY", "").strip()


def fetch(sort: str, page: int, c: dict) -> list[dict]:
    params = {"limit": PER_PAGE, "page": page, "sort": sort}
    if sort == "growth-desc":
        params["minRevenue"] = int(c["min_revenue_usd"] * 100)        # the API counts cents
    if c.get("category"):
        params["category"] = c["category"]
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}", headers={
        "Authorization": f"Bearer {key()}", "User-Agent": UA, "Accept": "application/json"})
    with net.fetch(req, 40) as res:
        return json.load(res).get("data") or []


def read(c: dict) -> dict[str, dict]:
    """{slug: product} from the fastest growing and the newest listings, each tagged with how it came."""
    got: dict[str, dict] = {}
    asked = 0
    for sort, pages in (("growth-desc", c["pages_growth"]), ("listed-desc", c["pages_new"])):
        for page in range(1, pages + 1):
            if asked:
                time.sleep(PAUSE)
            rows = fetch(sort, page, c)
            asked += 1
            for r in rows:
                got.setdefault(r["slug"], dict(r, came=[]))["came"].append(sort)
            if len(rows) < PER_PAGE:
                break
    return got


def term_of(p: dict) -> str:
    """The product's name; for a company's legal name ("Talent Tech Global Limited"), its site's name."""
    name = " ".join((p.get("name") or "").split())
    site = re.sub(r"^https?://(www\.)?", "", p.get("website") or "").split("/")[0]
    if LEGAL.search(name) and site and "." in site and not site.endswith(("apple.com", "google.com", "trustmrr.com")):
        return site.rsplit(".", 1)[0]
    return name.lower()


def growth_text(g: float | None) -> str:
    return t("dash") if g is None else t("revenue_near_zero") if g >= HUGE else f"{g:+.0%}"


def cents(p: dict) -> int:
    return int((p.get("revenue") or {}).get("last30Days") or 0)


def products(cfg: dict, state: Path, today: date | None = None) -> dict:
    c = cfg["revenue"]
    if not key():
        return {"skipped": True, "no_key": True}
    today = today or date.today()
    path = state / "revenue.json.gz"
    seen: dict[str, dict] = snap.load(path) or {}
    first = not seen
    got = read(c)
    floor = int(c["min_revenue_usd"] * 100)
    rows = []
    for slug, p in got.items():
        earned, before = cents(p), seen.get(slug)
        from_zero = before is not None and before["cents"] == 0 and earned >= floor
        if earned < floor or (before is not None and not from_zero):
            continue                       # too small, or already reported
        if p.get("stealthMode") or not p.get("website") or (p.get("name") or "").lower() == "anonymous startup":
            continue                       # a hidden listing names nothing to search for
        rows.append({"slug": slug, "name": p.get("name") or slug, "website": p.get("website") or "",
                     "about": " ".join((p.get("description") or "").split())[:200], "category": p.get("category") or "",
                     "usd": round(earned / 100), "growth": p.get("growth30d"), "came": p["came"], "from_zero": from_zero})
    for slug, p in got.items():
        seen[slug] = {"cents": cents(p), "day": today.isoformat()}
    if got:
        snap.save(path, seen)
    zero = [r for r in rows if r["from_zero"]]
    growing = sorted((r for r in rows if not r["from_zero"] and "growth-desc" in r["came"]), key=lambda r: -(r["growth"] or 0))
    listed = [r for r in rows if not r["from_zero"] and "growth-desc" not in r["came"]]
    listed = listed[:max(1, c["max_leads"] // 2)]           # room kept for new listings that already earn
    rows = (zero + growing[:max(0, c["max_leads"] - len(zero) - len(listed))] + listed)[:c["max_leads"]]
    for r in rows:
        r["term"], r["growth_text"] = term_of(got[r["slug"]]), growth_text(r["growth"])
    terms = [{"term": r["term"], "recent": 1, "about": (r["website"] or "TrustMRR", r["about"]),
              "examples": [f"{r['website']} · ${r['usd']:,} in 30 days · {r['growth_text']}"],
              "note": t("note_revenue_zero" if r["from_zero"] else "note_revenue", usd=f"{r['usd']:,}", growth=r["growth_text"])}
             for r in rows if r["term"]]
    return {"read": len(got), "first_run": first,
            "products": rows, "terms": terms, "cost_micros": 0}
