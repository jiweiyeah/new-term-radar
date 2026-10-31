"""DataForSEO's own numbers, through Monid first and AIsa second.

The readings Google does not publish: a search's first page, the Trends series and the
related queries, the monthly searches of a keyword, the difficulty of a keyword, a site's
registration, the words a site ranks for, the pages that link to it.

Monid is asked first. It resells DataForSEO at DataForSEO's price and its answers come
back in DataForSEO's own shape, so the radar reads them the way it read AIsa's. AIsa is
the second door: when Monid's answer does not come back (no key, a run that failed), the
same reading is asked of AIsa, and the answer is the same shape either way. A reading
that fails on both doors is named, not swallowed.

One call is one endpoint of Monid's `dataforseo` provider, or one AIsa tool (the router's
name for it, or the REST passthrough's path for the ones the router's schema refuses).
"""
from __future__ import annotations

from . import aisa, monid

PROVIDER = "dataforseo"

SERP = "/serp/google-organic"                     # the first page of a search, with the ads on it
TRENDS = "/keywords/google-trends-explore"        # the interest over time and the related queries
VOLUME = "/keywords/google-ads-search-volume"     # the monthly searches of a list of keywords
DIFFICULTY = "/labs/bulk-keyword-difficulty"      # the difficulty of a list of keywords
OVERVIEW = "/labs/keyword-overview"               # volume, CPC, difficulty and intent in one call
WHOIS = "/domain/whois"                           # the registration dates, by filters
RANKED = "/labs/ranked-keywords"                  # the words a site ranks for
BACKLINKS = "/backlinks/backlinks"                # the pages that link to a site
REFERRING = "/backlinks/referring-domains"        # the domains that link to a site
ADS = "/serp/google-ads-advertisers"              # the advertisers of a name (Google's Ads Transparency Center)
AI_MODE = "/serp/google-ai-mode"                  # Google AI Mode's answer and the sources it cites
CHATGPT = "/ai/chatgpt-response"                  # ChatGPT's answer to a question

# The same reading through AIsa: the tool router's name for it, and the REST passthrough's
# path for the ones the router's schema refuses (the whois and the ranked keywords).
AISA_TOOL = {
    SERP: "post_dataforseo_serp_google_organic_live",
    VOLUME: "post_dataforseo_keywords_gads_search_volume_live",
    DIFFICULTY: "post_dataforseo_labs_google_keyword_overview_live",
    OVERVIEW: "post_dataforseo_labs_google_keyword_overview_live",
    BACKLINKS: "post_dataforseo_backlinks_backlinks_live",
    REFERRING: "post_dataforseo_backlinks_referring_domains_live",
}
AISA_REST = {
    TRENDS: "keywords_data/google_trends/explore/live",
    WHOIS: "domain_analytics/whois/overview/live",
    RANKED: "dataforseo_labs/google/ranked_keywords/live",
    AI_MODE: "serp/google_ai_mode/live",
    CHATGPT: "llm_chatgpt/chat_gpt/live",
}


class DfsError(RuntimeError):
    """A reading that came back from neither door."""


def keyed() -> bool:
    """Whether a door is open at all: Monid's key or AIsa's.

    Without one, the readings are not even tried: a reading that cannot be bought is left
    out, not failed."""
    return bool(monid.key() or aisa.has_key())


def _aisa(endpoint: str, body: dict) -> tuple[dict | list, int]:
    """AIsa's answer to the same reading, in the same shape."""
    tool = AISA_TOOL.get(endpoint)
    if tool:
        data, cost = aisa.call([{"call_id": "d", "tool": tool, "arguments": {"body": [body]}}])
        payload = data.get("d") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            payload = next((v for v in (data or {}).values() if isinstance(v, dict)), {})
        task = ((payload.get("tasks") or [{}])[0])
        if task.get("status_code") not in (None, 20000):
            raise DfsError(f"DataForSEO answered {task.get('status_code')}: "
                           f"{str(task.get('status_message') or task)[:160]}")
        # The result is a list: one object where the endpoint answers with a result object
        # (the first page of a search), one object a keyword where it answers that way (the
        # searches of a keyword, its difficulty). dfs.result() takes the first, dfs.rows()
        # takes them all.
        return (task.get("result") or [{}]), cost
    path = AISA_REST.get(endpoint)
    if path:
        return aisa.rest(path, body)
    raise DfsError(f"no AIsa tool answers {endpoint}")


def _raw(endpoint: str, body: dict) -> tuple[dict | list, int]:
    """The raw answer, from whichever door answered."""
    try:
        return monid.run(PROVIDER, endpoint, body=body)
    except monid.MonidTimeout as first:
        # The run was created and may already have been billed: the second door must not
        # buy the same reading again.
        raise DfsError(f"Monid: {first}") from first
    except monid.MonidError as first:
        try:
            return _aisa(endpoint, body)
        except (aisa.AisaError, DfsError) as second:
            raise DfsError(f"Monid: {first}; AIsa: {second}") from second


def result(endpoint: str, body: dict) -> tuple[dict, int]:
    """The endpoint's one result object and the cost in micro-dollars.

    Monid is asked first; AIsa answers when Monid does not. Both failures are named.
    """
    out, cost = _raw(endpoint, body)
    if isinstance(out, list):
        return (out[0] if out and isinstance(out[0], dict) else {}), cost
    return (out if isinstance(out, dict) else {}), cost


def rows(endpoint: str, body: dict) -> tuple[list[dict], int]:
    """The endpoint's rows and the cost in micro-dollars.

    The rows sit under the first result object ("items"), or under "keywords" where the
    endpoint answers with the keyword list, or are the answer itself (the Ads volume
    answers with one object a keyword).
    """
    out, cost = _raw(endpoint, body)
    first = out[0] if isinstance(out, list) and out else out
    if not isinstance(first, dict):
        return [], cost
    items = first.get("items")
    if not isinstance(items, list):
        items = first.get("keywords")
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict) and i], cost
    if isinstance(out, list):
        return [i for i in out if isinstance(i, dict) and i], cost
    return [], cost


def many(endpoint: str, bodies: list[dict]) -> tuple[list[dict | None], int, list[str]]:
    """Several calls, one after the other: the answers (None where a call failed), the
    cost, and the failures. One bad call does not stop the rest."""
    out, cost, errors = [], 0, []
    for i, body in enumerate(bodies):
        try:
            res, spent = result(endpoint, body)
        except DfsError as exc:
            out.append(None)
            errors.append(f"call {i}: {exc}")
            continue
        out.append(res)
        cost += spent
    return out, cost, errors
