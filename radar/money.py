"""Signal ⑩: who already earns, and who just started paying for attention (weekly).

- Portfolios: a site that earns from ads names its AdSense publisher ID in its ads.txt, and the
  same ID on other domains is its owner's other sites (two calls a site, about $0.0048). One proven
  tool site leads to the rest of its owner's sites, each a niche someone already makes money in:
  one AI tools directory's ID also ran a coloring-page site and a song-meaning site. The sites asked
  about are the tool sites already on page one for the day's terms worth acting on (strongest first),
  then the paying products of ⑥
  and ⑧, at most `portfolio_sites`. Brand-new sites rarely show an AdSense ID yet: on 2026-10-15
  ⑥'s and ⑧'s twelve gave none.
- New advertisers: who runs Google ads for each of `ad_terms` (the ads on the page one, read
  from DataForSEO). An advertiser not seen before on a term has just decided the term is worth
  money. Only the portfolios above need a Monid key.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import adsense, dfs, domains, google, monid, sellers, snap
from .i18n import t


def _advertisers(keyword: str) -> tuple[list[str], int]:
    """The domains the Ads Transparency Center names for one keyword, and the cost."""
    out, cost = monid.run("dataforseo", "/serp/google-ads-advertisers", body={"keyword": keyword})
    return adsense.advertisers(out), cost


def sisters(domain: str) -> tuple[str, list[str], int, int]:
    """(publisher ID, the other domains on it, the pool it was read from, cost).

    The other domains are the ones whose ads name this domain (Google's Ads Transparency
    Center, through Monid) and whose ads.txt carries the same publisher ID. The Center is
    asked under the site's own name and under its own domain: the name brings the brand's
    family, the domain brings the near-misses on it. ("", [], 0, cost) when the site shows no
    AdSense ID.
    """
    keys = list(dict.fromkeys([domains.name_of(domain), domain]))
    with ThreadPoolExecutor(len(keys)) as pool:
        answers = list(pool.map(_advertisers, keys))
    cands = [c for names, _ in answers for c in names]
    cands = [c for c in dict.fromkeys(cands) if c.lower() != domain.lower()]
    pub, others = adsense.shared(domain, cands)
    return pub, others, len(cands), sum(spent for _, spent in answers)


def is_network(others: list[str], c: dict) -> bool:
    """One publisher ID on more than `max_sisters` of the domains that name the site is a
    network's, not a person's.

    The old reading asked Google for every domain on an ID and counted them (2026-10-15:
    tomshardware.com's ID held 34 Future plc titles, spiceworks.com's hundreds of wikis and
    papers). No such list is public now: the domains come from the Ads Transparency Center,
    which names about ten of them a question, and a shared ID (Google's own, an agency's) is
    on most of those while a person's is on one or two. So the bar is a low one now: six of
    the domains that name a site on one ID is a network, not a person with six niches.
    """
    return len(others) > c["max_sisters"]


def portfolios(c: dict, sites: list[str], seen: dict[str, str], today: str) -> tuple[list[dict], int, list[str]]:
    rows, cost, errors = [], 0, []
    for site in list(dict.fromkeys(sites))[:c["portfolio_sites"]]:
        try:
            pub, others, pool, spent = sisters(site)
        except (monid.MonidError, adsense.AdsenseError) as exc:
            errors.append(f"adsense {site}: {exc}")
            continue
        cost += spent
        if is_network(others, c):
            errors.append(f"adsense {site}: {pub} is on {len(others)} of the {pool} domains that name it, a network")
            continue
        fresh = [d for d in others if f"pub:{d}" not in seen]
        for d in fresh:
            seen[f"pub:{d}"] = today
        if fresh:
            rows.append({"site": site, "pub": pub, "sisters": fresh})
    return rows, cost, errors


def new_advertisers(c: dict, seen: dict[str, str], today: str) -> tuple[list[dict], int, list[str]]:
    rows, cost, errors = [], 0, []
    for term in c["ad_terms"]:
        try:
            out, spent = google.serp(term)
        except dfs.DfsError as exc:
            errors.append(f"ads {term}: {exc}")
            continue
        cost += spent
        names = google.advertisers(out["items"])
        first = not any(k.startswith(f"ad:{term}:") for k in seen)
        fresh = [n for n in names if f"ad:{term}:{n.lower()}" not in seen]
        for n in fresh:
            seen[f"ad:{term}:{n.lower()}"] = today
        if fresh and not first:
            rows.append({"term": term, "advertisers": fresh})
    return rows, cost, errors


def money(cfg: dict, state: Path, sites: list[str], today: date | None = None) -> dict:
    """`sites`: the paying products' domains of this week's ⑥ and ⑧."""
    c = cfg["money"]
    today_s = (today or date.today()).isoformat()
    path = state / "money.json.gz"
    seen: dict[str, str] = snap.load(path) or {}
    # The portfolios need a Monid key; the advertisers come from the ads on the page one,
    # so they work without one.
    folios, cost, errors = portfolios(c, sites, seen, today_s) if monid.key() else ([], 0, [])
    ads, spent, failed = new_advertisers(c, seen, today_s)
    snap.save(path, seen)
    terms = [{"term": sellers.term_of(d, ""), "recent": 1, "about": (f"https://{d}/", ""),
              "examples": [t("money_sister_ex", site=r["site"], pub=r["pub"])], "note": t("note_sister", site=r["site"])}
             for r in folios for d in r["sisters"]]
    terms += [{"term": n.lower(), "recent": 1, "examples": [t("money_ad_ex", term=r["term"])], "note": t("note_advertiser", term=r["term"])}
              for r in ads for n in r["advertisers"]]       # the domains running the ads
    return {"no_key": not monid.key(), "portfolios": folios, "advertisers": ads, "terms": terms,
            "errors": errors + failed, "cost_micros": cost + spent}
