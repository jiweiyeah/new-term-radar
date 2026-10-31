"""Evidence beside the keyword table: is anyone building on a new name yet, and what do buyers of a
paying product search for besides its name.

For the strongest new names of the day (`names`, five by default):
- new sites: for the first `whois_names` names, domains carrying the name registered in the last
  `whois_days` days, from DataForSEO's whois overview through Monid (about $0.12
  a name: the query.domain method of watching who registers on a new word); for the rest, HTTPS
  certificates issued in the last week (crt.sh, free; it rate-limits hard, so one try, and a miss
  is a gap, not a zero). People register sites on a name before search engines list them;
- Wikipedia: views of the name's article over the last seven days against the seven before (free);
- Google's Knowledge Graph type for the name, from Trends suggestions (free): "AI agent" means
  Google already knows what it is;
- Discord: servers about it listed on Disboard, found through Google (DataForSEO, about $0.002; Disboard
  answers bots with 403).

For the paying products of ⑥ and ⑧ (`products`): "<product> alternative", "vs", "free", "pricing",
"review", sized in one Google Ads call (about $0.09). Searches for a paid product's alternatives
are a market someone already proved.
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from datetime import date, timedelta

from . import UA, dfs, net, usecases

TIMEOUT = 20
WHOIS = dfs.WHOIS
TRENDS = "https://trends.google.com/trends/api/autocomplete/{}?hl=en-US&tz=0"
TAILS = ("{} alternative", "{} alternatives", "{} vs", "{} free", "{} pricing", "{} review")
DISBOARD_PAGE = "https://disboard.org/en/servers"
DISBOARD_SEARCH = "https://disboard.org/search"
CSRF = re.compile(r'name="_csrf"\s+value="([^"]+)"')
SERVER = re.compile(r"/server/(\d+)")            # one server's page


def _get(url: str) -> bytes:
    with net.fetch(urllib.request.Request(url, headers={"User-Agent": UA}), TIMEOUT) as res:
        return res.read()


def new_sites(name: str, today: date) -> tuple[int, list[str]]:
    """Domains carrying the name whose certificates were issued in the last seven days (crt.sh)."""
    token = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(token) < 5:                                   # "jev" would match half the internet
        return 0, []
    url = "https://crt.sh/?" + urllib.parse.urlencode({"q": f"%{token}%", "output": "json", "exclude": "expired"})
    rows = json.loads(_get(url) or b"[]")          # one try: a 429 or a timeout is a gap in the evidence
    since = today - timedelta(days=7)
    domains = {r.get("common_name", "").lower().lstrip("*.") for r in rows
               if str(r.get("not_before", ""))[:10] >= since.isoformat() and token in r.get("common_name", "").lower()}
    return len(domains), sorted(domains)[:3]


def registered(name: str, today: date, days: int) -> tuple[int, list[str], int]:
    """(domains carrying the name registered in the last `days` days, the newest three, cost), from whois."""
    token = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(token) < 5:
        return 0, [], 0
    since = (today - timedelta(days=days)).isoformat() + " 00:00:00 +00:00"
    result, cost = dfs.result(WHOIS, {"limit": 3, "order_by": ["created_datetime,desc"],
                                      "filters": [["domain", "like", f"%{token}%"], "and", ["created_datetime", ">", since]]})
    return int(result.get("total_count") or 0), [i["domain"] for i in result.get("items") or []], cost


def wikipedia(name: str, today: date) -> tuple[str, int, int]:
    """(article, views in the last seven days, views in the seven before); ("", 0, 0) without an article."""
    found = json.loads(_get("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "opensearch", "search": name, "limit": 1, "namespace": 0, "format": "json"})))
    titles = found[1] if len(found) > 1 else []
    if not titles or re.sub(r"\W", "", titles[0].lower()) != re.sub(r"\W", "", name.lower()):
        return "", 0, 0
    end, start = today - timedelta(days=1), today - timedelta(days=14)
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
           f"{urllib.parse.quote(titles[0].replace(' ', '_'), safe='')}/daily/{start:%Y%m%d}/{end:%Y%m%d}")
    views = [i["views"] for i in json.loads(_get(url)).get("items", [])]
    return titles[0], sum(views[-7:]), sum(views[:-7][-7:])


# Knowledge Graph types a product can have. Anything else under the same title is someone else's: "Gemini 4"
# was only a 1965 NASA flight (and a car repair shop in Sofia) on 2026-10-15, not Google's model.
PRODUCT_TYPES = re.compile(r"\b(software|app|application|website|web site|company|corporation|ai|chatbot|ai agent|platform|"
                           r"ai model|language model|programming language|framework|library|video game|operating system|"
                           r"startup|browser|extension|search engine|social network)s?\b", re.I)   # "repair" holds no "ai" word


def entity(name: str) -> dict:
    """{"type": the product's Knowledge Graph type} when Google knows it as a product; {"taken": the other
    thing's type} when the name belongs to something else; {} when Google knows nothing by that name."""
    raw = _get(TRENDS.format(urllib.parse.quote(name))).decode("utf-8", "replace")
    topics = json.loads(raw[raw.index("{"):]).get("default", {}).get("topics", [])
    plain = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
    same = [x.get("type") or "" for x in topics if plain(x.get("title") or "") == plain(name)]
    product = next((tp for tp in same if PRODUCT_TYPES.search(tp) or tp == "Topic"), "")    # "Topic": known, not typed yet
    return {"type": product} if product else {"taken": same[0]} if same else {}


def count_servers(body: str) -> int:
    """How many servers a Disboard answer holds: one page of them, 24 at most."""
    return len(set(SERVER.findall(body)))


def discord(name: str) -> int:
    """How many Discord servers Disboard lists for the name (free).

    Disboard's own search: it is a form, so its page is asked for the token the form needs
    and the name is posted to it. The answer holds one page of servers (24), so the count
    is "at least this many".
    """
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    page = opener.open(urllib.request.Request(DISBOARD_PAGE, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}),
                        timeout=TIMEOUT).read().decode("utf-8", "replace")
    token = CSRF.search(page)
    if not token:
        raise ValueError("Disboard's page carries no token for its search")
    form = urllib.parse.urlencode({"_csrf": token.group(1), "SearchForm[keyword]": name}).encode()
    req = urllib.request.Request(DISBOARD_SEARCH, data=form, method="POST", headers={
        "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Language": "en-US,en;q=0.9", "Referer": DISBOARD_PAGE})
    body = opener.open(req, timeout=TIMEOUT).read().decode("utf-8", "replace")
    return count_servers(body)


def evidence(c: dict, names: list[str], today: date | None = None) -> dict:
    today = today or date.today()
    rows, cost, errors = [], 0, []
    for n, name in enumerate(names[:c["names"]]):
        row: dict = {"name": name}
        if n < c["whois_names"]:
            try:
                count, examples, spent = registered(name, today, c["whois_days"])
                row["sites"], row["sites_from"], cost = (count, examples), "whois", cost + spent
            except dfs.DfsError as exc:
                errors.append(f"whois {name}: {str(exc)[:80]}")
        for key, fn in (("sites", lambda: new_sites(name, today)), ("wiki", lambda: wikipedia(name, today)),
                        ("entity", lambda: entity(name))):
            if key in row:
                continue
            try:
                row[key] = fn()
            except (OSError, ValueError, KeyError, IndexError) as exc:
                errors.append(f"{key} {name}: {str(exc)[:80]}")
        try:
            row["discord"] = discord(name)         # free: Disboard's own search
        except (OSError, ValueError) as exc:
            errors.append(f"discord {name}: {str(exc)[:80]}")
        rows.append(row)
    return {"rows": rows, "errors": errors, "cost_micros": cost}


def tails(c: dict, products: list[str]) -> dict:
    """Searches around the paying products: alternatives, comparisons, free versions, prices."""
    phrases = [p.format(name) for name in dict.fromkeys(products[:c["products"]]) for p in TAILS if 2 <= len(name) <= 40]
    if not phrases:
        return {"rows": [], "errors": [], "cost_micros": 0}
    try:
        vol, cost = usecases.volumes(phrases, c["location_code"])
    except dfs.DfsError as exc:        # a failure leaves the section empty, not the report
        return {"rows": [], "errors": [f"volumes: {str(exc)[:120]}"], "cost_micros": 0}
    rows = sorted(({"phrase": p, "volume": (vol.get(p) or {}).get("volume") or 0} for p in phrases), key=lambda r: -r["volume"])
    return {"rows": [r for r in rows if r["volume"] >= c["min_volume"]], "errors": [], "cost_micros": cost}
