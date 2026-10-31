"""Client for Monid (https://monid.ai/tools), the gateway that answers what Google does not.

Four readings the radar takes from it:
- a site's Domain Rating and its rank (Ahrefs), about $0.0013 a site;
- a site's traffic (Similarweb): the monthly visits, where they come from and how much of
  them comes from AI answers, about $0.0017 a site;
- the advertisers of a name (Google's Ads Transparency Center), about $0.0012 a name;
- Google's own numbers (DataForSEO, radar/dfs.py): a search's first page, the Trends series
  and the related queries, the monthly searches of a keyword, the difficulty of a keyword.

Three more, through the `anyapi` tools Monid carries: X's posts and X's search, Reddit's
search and YouTube's comments (radar/social.py, radar/sources.py, radar/asks.py). And the
judge's chat answer (radar/judge.py), through Monid's own OpenAI-compatible proxy: that
chat is the second door behind the model gateway (radar/llm.py).

A run is asynchronous: POST /v1/run hands back a run id, and the answer arrives when the
run is done, so every call polls GET /v1/runs/<id> until the run ends. The cost comes back
with the run, in dollars; the radar counts micro-dollars.

The key is MONID_API_KEY. Without it the signals that need it turn themselves off.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from . import UA, net

BASE = os.environ.get("MONID_BASE_URL", "https://api.monid.ai")
TIMEOUT = 60
POLL = 2                # seconds between two polls of a run
LIMIT = 180             # seconds a run may take in all, polls and waits together
DONE = ("COMPLETED", "FAILED", "ERROR", "CANCELLED", "BLOCKED")
ANYAPI = "anyapi"       # the provider that carries X, Reddit and YouTube
CHAT_PROVIDER = "blockrun.ai"
CHAT_ENDPOINT = "/api/v1/chat/completions"
CHAT_MODELS = ("gpt-4o-mini", "gpt-4.1-mini", "gemini-2.5-flash", "deepseek-chat", "claude-sonnet-4-6")


class MonidError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class MonidTimeout(MonidError):
    """A run that was created but whose answer never came: it may already have been
    billed, so the second door must not buy the same reading again."""


def key() -> str:
    """The Monid key; empty when there is none."""
    return os.environ.get("MONID_API_KEY", "").strip()


def _request(url: str, body: dict | None = None, method: str = "POST") -> dict:
    k = key()
    if not k:
        raise MonidError("no Monid key: set MONID_API_KEY")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {k}",
        "User-Agent": UA, "x-source": "new-term-radar"})
    try:
        with net.fetch(req, timeout=TIMEOUT) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        raise MonidError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}", exc.code) from exc
    except (OSError, ValueError) as exc:
        raise MonidError(f"{type(exc).__name__}: {exc}") from exc


def run(provider: str, endpoint: str, body: dict | None = None, query: dict | None = None) -> tuple[dict | list, int]:
    """Run one call and wait for its answer: (the answer, cost in micro-USD).

    The call's own parameters go in `body` (what it posts) or `query` (what it puts in the
    address), the way the endpoint's schema asks for them.
    """
    inp: dict = {}
    if body is not None:
        inp["body"] = body
    if query is not None:
        inp["queryParams"] = query
    started = _request(f"{BASE}/v1/run", {"provider": provider, "endpoint": endpoint, "input": inp})
    run_id = started.get("runId") or started.get("id")
    if not run_id:
        raise MonidError(f"no run id in the answer: {str(started)[:160]}")
    state = {}
    deadline = time.monotonic() + LIMIT
    while time.monotonic() < deadline:
        state = _request(f"{BASE}/v1/runs/{run_id}", method="GET")
        if state.get("status") in DONE:
            break
        time.sleep(POLL)
    else:
        raise MonidTimeout(f"the run did not finish in {LIMIT} s")
    spent = state.get("cost")
    value = spent.get("value") if isinstance(spent, dict) else spent
    try:
        cost = int(round(float(value or 0) * 1e6))
    except (TypeError, ValueError):
        cost = 0
    if state.get("status") != "COMPLETED":
        err = (state.get("providerResponse") or {}).get("error") or {}
        # Only the reason: the whole answer can echo the request (the keyword, the prompt),
        # and that would travel into the report and out by mail.
        detail = str(err.get("error") or state.get("reason") or state.get("error") or "")[:200]
        raise MonidError(f"{state.get('status')}: {detail or 'no reason given'}")
    output = state.get("output")
    if not isinstance(output, (dict, list)):
        raise MonidError(f"unexpected answer shape: {type(output).__name__}")
    return output, cost


def data(provider: str, endpoint: str, body: dict | None = None, query: dict | None = None) -> tuple[dict, int]:
    """run() with the answer's own `data` object: (it, cost in micro-USD)."""
    out, cost = run(provider, endpoint, body=body, query=query)
    if isinstance(out, dict) and isinstance(out.get("data"), dict):
        return out["data"], cost
    return {}, cost


def anyapi(endpoint: str, body: dict) -> tuple[dict, int]:
    """One call to an `anyapi` tool (X's posts, X's search, Reddit's search, YouTube's
    comments): its data object and the cost in micro-USD."""
    return data(ANYAPI, endpoint, body=body)


def anyapi_rows(endpoint: str, body: dict) -> tuple[list[dict], int]:
    """The rows of an `anyapi` tool call: X's posts, Reddit's posts, YouTube's comments."""
    data_out, cost = anyapi(endpoint, body)
    rows = data_out.get("items") or data_out.get("posts") or data_out.get("comments") or []
    return (list(rows) if isinstance(rows, list) else []), cost


def chat(model: str, system: str, user: str, max_tokens: int = 1500) -> str:
    """One chat answer (Monid's OpenAI-compatible proxy): the model's text.

    The models it carries are named in CHAT_MODELS; a model it does not carry is named
    in the failure, so the config can be corrected. This door does not answer for the
    gateway (radar/llm.py): that one asks Monid second."""
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": user}]}
    out, _ = run(CHAT_PROVIDER, CHAT_ENDPOINT, body=body)
    try:
        return out["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise MonidError(f"unexpected chat answer: {str(out)[:160]}") from exc

