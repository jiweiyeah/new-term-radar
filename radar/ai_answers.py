"""What AI assistants recommend for a job people ask for (⑦), and which sites they cite.

For the ⑦ rows that have Google searches, up to `max_rows`, this asks each engine of `engines`
the job as a question (`prompt`, "What is the best tool to {job}?") and keeps who the answer
names and which sites it cites:

- chatgpt: the answer ChatGPT gives (DataForSEO's ChatGPT response, through Monid): it has no
  brand list of its own, so the names are the ones it opens its list items with ("1. **Notion**
  : …"), the way Google's answer writes them, and the sites it cites;
- google: Google's AI Mode for the same question: it has no brand list either, so the names
  are the bold ones that open its list items ("- **[Knowt AI PowerPoint Summarizer](…)** : …"),
  which is how it writes up the tools it recommends; and the sites its answer references. The
  names its references go by are not used: they include "Reddit".

An answer that names no tool is a gap a new tool can fill in AI search; the cited sites are the
pages AI search reads for the job, the ones to be listed on or compared against. One of your
sites (`yours.domains`, or the Search Console property) among them is marked. About $0.0008 an
answer for ChatGPT and $0.004 for Google's AI Mode (October 2026); off by default: set
`"enabled": true` to pay for it.
"""
from __future__ import annotations

import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import dfs, yours

PATHS = {"chatgpt": dfs.CHATGPT, "google": dfs.AI_MODE}
MODEL = "gpt-4o-mini"
TOKENS = 300
KEPT = 8
WORKERS = 3          # answers asked at once; each takes 10 to 60 seconds


def _host(domain: str | None) -> str:
    return re.sub(r"^www\.", "", (domain or "").lower())


def _site(url: str | None) -> str:
    """The host a link points at, the way a site is written."""
    if not url:
        return ""
    return _host(urllib.parse.urlsplit(url).netloc or url.split("/")[0])


def _unique(values) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))[:KEPT]


LISTED = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\*\*(.+?)\*\*", re.M)
BOLD = re.compile(r"\*\*(.+?)\*\*")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
LINK_URL = re.compile(r"\]\((https?://[^)\s]+)")


def listed_names(markdown: str) -> list[str]:
    """The bold names that open an answer's list items, links and code marks taken off."""
    return [LINK.sub(r"\1", m).strip(" :`") for m in LISTED.findall(markdown or "")]


def _text(result: dict) -> str:
    """The answer's own text: the sections it writes it in."""
    parts = []
    for item in result.get("items") or []:
        for section in item.get("sections") or []:
            if isinstance(section, dict) and section.get("text"):
                parts.append(section["text"])
    return "\n".join(parts)


def _cited(result: dict, text: str) -> list[str]:
    """The sites the answer cites: its source sections, its search results, its links."""
    hosts = []
    for item in result.get("items") or []:
        for section in item.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for row in section.get("items") or []:
                if isinstance(row, dict):
                    hosts.append(_host(row.get("domain")) or _site(row.get("url")))
    for row in result.get("search_results") or []:
        if isinstance(row, dict):
            hosts.append(_host(row.get("domain")) or _site(row.get("url")))
    hosts += [_site(url) for url in LINK_URL.findall(text)]
    return hosts


def read_chatgpt(result: dict) -> dict:
    """The names ChatGPT writes and the sites it cites.

    The answer carries no brand list of its own: the names are the ones it opens its list
    items with, the way Google's answer writes them. The answer that comes through AIsa's
    passthrough lists the brands and the sources under its own keys instead, so those are
    read too, when the answer has them."""
    text = _text(result)
    names = listed_names(text) or [LINK.sub(r"\1", m).strip(" :`") for m in BOLD.findall(text)]
    brands = [re.sub(r"[*_`\[\]\\]", "", b.get("markdown") or b.get("title") or "").strip()
              for b in result.get("brand_entities") or [] if isinstance(b, dict)]
    sources = [_host(s.get("domain")) for s in result.get("sources") or [] if isinstance(s, dict)]
    return {"names": _unique(names or brands), "sites": _unique(_cited(result, text) or sources)}


def read_google(result: dict) -> dict:
    """Google AI Mode's answer: the bold names that open its list items and the sites it
    references. The names its references go by are not used: they include "Reddit"."""
    refs, names, links = [], [], []
    for item in result.get("items") or []:
        markdown = item.get("markdown") or ""
        refs += item.get("references") or []
        names += listed_names(markdown)
        links += LINK_URL.findall(markdown)
        for element in item.get("items") or []:
            refs += element.get("references") or []
    sites = [_host(r.get("domain")) or _site(r.get("url")) for r in refs]
    sites += [_site(url) for url in links]
    return {"names": _unique(names), "sites": _unique(sites)}


READ = {"chatgpt": read_chatgpt, "google": read_google}


def _body(engine: str, prompt: str, c: dict) -> dict:
    """The call each engine takes: the question the way it asks it."""
    if engine == "chatgpt":
        return {"model_name": MODEL, "user_prompt": prompt, "max_output_tokens": TOKENS, "web_search": True}
    return {"keyword": prompt, "location_code": c["location_code"], "language_code": c["language_code"],
            "device": "desktop"}


def _ask(job: str, engine: str, c: dict) -> tuple[str, str, dict | None, int, str | None]:
    prompt = c["prompt"].format(job=job)
    try:
        result, cost = dfs.result(PATHS[engine], _body(engine, prompt, c))
    except dfs.DfsError as exc:
        return job, engine, None, 0, f"{job} ({engine}): {exc}"
    return job, engine, READ[engine](result), cost, None


def assess(rows: list[dict], cfg: dict) -> tuple[int, list[str]]:
    """Adds `ai` = {"prompt", "by": {engine: {"names", "sites"}}, "yours"} to the first `max_rows` rows. (cost, errors)"""
    c = cfg.get("ai_answers") or {}
    if not c.get("enabled") or not rows:
        return 0, []
    engines = [e for e in c["engines"] if e in PATHS]
    errors = [f"unknown engine: {e}" for e in c["engines"] if e not in PATHS]
    picked = {r["phrase"]: r for r in rows[:c["max_rows"]]}
    mine, cost = yours.own_domains(cfg), 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for job, engine, got, spent, error in pool.map(lambda p: _ask(*p, c), [(j, e) for j in picked for e in engines]):
            cost += spent
            if error:
                errors.append(error)
                continue
            ai = picked[job].setdefault("ai", {"prompt": c["prompt"].format(job=job), "by": {}, "yours": False})
            ai["by"][engine] = got
            ai["yours"] |= any(yours.is_mine(s, mine) for s in got["sites"]) if mine else False
    return cost, errors
