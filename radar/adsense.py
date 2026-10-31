"""A site's ad publisher IDs, and the other sites that share them.

ads.txt is the file a site publishes to say which networks may serve it; Google's AdSense
writes the site's publisher ID in it. The reverse is what the radar wants: one owner's
tools, one niche each (2026-10-15: one AI tools directory's ID also ran a coloring-page
site and a song-meaning site).

Google keeps no public list of the domains on an ID, so the other sites are found the other
way round: Ads Transparency Center (through Monid) says which domains run ads naming a
domain, and each of those domains' own ads.txt says whether the publisher ID is the same
one. A domain that only mentions the name in its ads (a squatter on the name, a rival
bidding on the brand) shares no ID and is dropped.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import UA, net

TIMEOUT = 20
WORKERS = 6             # the ads.txt files are fetched side by side
IDS = re.compile(r"^\s*google\.com\s*,\s*(pub-\d+)\s*,\s*(?:DIRECT|RESELLER)\s*,", re.M)


class AdsenseError(RuntimeError):
    pass


def ids(domain: str) -> set[str]:
    """The Google publisher IDs the domain's ads.txt names; none when it has no ads.txt."""
    req = urllib.request.Request(f"https://{domain}/ads.txt", headers={"User-Agent": UA})
    try:
        with net.fetch(req, TIMEOUT) as res:
            text = res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return set()
        raise AdsenseError(f"ads.txt of {domain}: HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise AdsenseError(f"ads.txt of {domain}: {type(exc).__name__}: {exc}") from exc
    return {m.group(1) for m in IDS.finditer(text)}


def advertisers(out) -> list[str]:
    """The domains the Ads Transparency Center names for one search."""
    names = []
    for result in out if isinstance(out, list) else [out]:
        if not isinstance(result, dict):
            continue
        for item in result.get("items") or []:
            name = (item or {}).get("domain") if isinstance(item, dict) else None
            if name and name not in names:
                names.append(name)
    return names


def _ids_of(domain: str) -> set[str]:
    """The publisher IDs of one domain; none when its ads.txt cannot be read."""
    try:
        return ids(domain)
    except AdsenseError:
        return set()


def shared(domain: str, candidates: list[str]) -> tuple[str, list[str]]:
    """(the publisher ID the domain shares with its candidates, the candidates on that ID).

    A site can carry several publisher IDs (one per country, one per network), and the
    candidates are split among them. The ID with the most candidates wins: it is the one
    the owner's other sites are on.
    """
    mine = ids(domain)
    if not mine:
        return "", []
    others = [c for c in candidates if c.lower() != domain.lower()]
    groups: dict[str, list[str]] = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        for name, common in pool.map(lambda n: (n, mine & _ids_of(n)), others):
            for pub in common:
                groups.setdefault(pub, []).append(name)
    if not groups:
        return "", []
    pub = max(groups, key=lambda p: (len(groups[p]), p))
    return pub, groups[pub]
