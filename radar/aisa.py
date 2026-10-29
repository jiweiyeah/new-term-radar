"""Client for AIsa, the radar's one paid dependency: its tool router (X posts, Google
Trends and whois data through DataForSEO) and its OpenAI-compatible chat endpoint (the
name judge). Keys: AISA_API_KEY, comma-separated for several, or the one the AIsa CLI
stores when you log in. The keys are used in order: always the first one not yet marked
out of quota. A request refused with 401/402/403 (bad key, no balance) bills nothing, so
that key is marked dead and the same request is resent on the next key. The marks are per
scope (data vs chat: the router and the chat endpoint bill separate wallets, so a key out
of model balance can still read X posts) and live in the state directory
(state/aisa_keys.json, fingerprints only) so later runs skip those keys too. Any other
failure is raised as is — a failed batch is never retried.

The tool router runs a batch's calls one by one, about 10 s each, so batches are kept to
BATCH calls and the timeout is long.

Prices seen in October 2026: an account's last 20 posts $0.003, one Trends task $0.0012,
one whois overview query about $0.13.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import UA

ROUTER = os.environ.get("AISA_ROUTER_BASE_URL", "https://tools.aisa.one") + "/v1/tool-router/aisa-batch-use"
CHAT = os.environ.get("AISA_API_BASE_URL", "https://api.aisa.one") + "/v1/chat/completions"
# DataForSEO through AIsa's REST passthrough. The tool router checks arguments against a schema that types
# `filters` as objects, so DataForSEO's own filter syntax ([field, op, value] arrays joined by "and") is refused
# there (whois overview, backlinks; reported to AIsa on 2026-10-15). The passthrough takes it as DataForSEO does.
REST = os.environ.get("AISA_API_BASE_URL", "https://api.aisa.one") + "/apis/v1/dataforseo/"
MAX_CALLS = 20      # the router's own limit per batch
BATCH = 5           # what call_all sends at once, about 50 s a batch
TIMEOUT = 240
CHAT_TIMEOUT = 90


class AisaError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


DEAD_CODES = frozenset({401, 402, 403})   # key refused, nothing billed: mark it and move on
_ledger: Path | None = None               # where dead keys are remembered; set by set_key_ledger()
_dead_run: dict[str, set[str]] = {"data": set(), "chat": set()}   # this run's marks, also kept without a ledger

# AIsa bills the tool router and the chat endpoint from separate wallets, so a key out of
# model balance can still read X posts: the marks are per scope, never across both.
SCOPES = ("data", "chat")


def set_key_ledger(path: Path) -> None:
    """Remember dead keys in a file (called once per run with the state directory)."""
    global _ledger
    _ledger = path


def key(scope: str = "data") -> str:
    """The first AIsa key that is not marked out of quota for this scope."""
    return _live_keys(scope)[0]


def _keys() -> list[str]:
    """AISA_API_KEY (comma-separated for several), else the key the AIsa CLI stored at login (~/.aisa)."""
    raw = os.environ.get("AISA_API_KEY", "").strip()
    if raw:
        return [k.strip() for k in raw.split(",") if k.strip()]
    home = Path.home() / ".aisa"
    try:
        if (home / "key").exists():
            return [(home / "key").read_text().strip()]
        if (home / "tokens.json").exists():
            token = json.loads((home / "tokens.json").read_text()).get("accessToken", "")
            return [token] if token else []
    except (OSError, ValueError):
        pass
    return []


def _no_key() -> str:
    raise AisaError("no AIsa key: set AISA_API_KEY, or log in with the AIsa CLI (`aisa login`)")


def _live_keys(scope: str) -> list[str]:
    keys = _keys()
    if not keys:
        _no_key()
    dead = _dead(scope)
    live = [k for k in keys if _fingerprint(k) not in dead]
    if not live:
        where = f"delete {_ledger} to reset" if _ledger else "restart to reset"
        raise AisaError(f"all {len(keys)} AIsa key(s) are marked out of quota for {scope} calls (401/402/403); {where}")
    return live


def _fingerprint(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()[:16]


def _dead(scope: str) -> set[str]:
    dead = set(_dead_run[scope])
    if _ledger is not None:
        try:
            dead |= set(json.loads(_ledger.read_text()).get("dead", {}).get(scope, []))
        except (OSError, ValueError):
            pass
    return dead


def _mark_dead(k: str, scope: str) -> None:
    _dead_run[scope].add(_fingerprint(k))
    if _ledger is None:
        return
    try:
        dead = {s: sorted(set(_dead_run[s]) | _read_dead(s)) for s in SCOPES}
        _ledger.parent.mkdir(parents=True, exist_ok=True)
        _ledger.write_text(json.dumps({"dead": dead}))
    except (OSError, ValueError):
        pass   # a lost mark only means the dead key is tried once more next run


def _read_dead(scope: str) -> set[str]:
    if _ledger is None or not _ledger.exists():
        return set()
    try:
        return set(json.loads(_ledger.read_text()).get("dead", {}).get(scope, []))
    except (OSError, ValueError):
        return set()


def _post(url: str, body, timeout: int, scope: str = "data") -> dict:
    return _attempt(url, body, timeout, _live_keys(scope), scope)


def _refused_status(body) -> int | None:
    """The status when the body itself says the key was refused. The router reports an
    invalid or unbalanced key as per-call errors under HTTP 200 (status 401/402 inside
    each failed result), not as a 401; a key that works for some calls is not refused."""
    if not isinstance(body, dict):
        return None
    errors = [r.get("error") for r in body.get("results", []) if isinstance(r, dict) and r.get("error")]
    if body.get("error"):
        errors.append(body["error"])
    if not errors:
        return None
    texts = " ".join(str(e.get("message") or "") + str(e.get("code") or "") for e in errors if isinstance(e, dict))
    statuses = {e.get("status") for e in errors if isinstance(e, dict) and isinstance(e.get("status"), int)}
    if statuses and statuses <= set(DEAD_CODES):
        return min(statuses)
    if "invalid_api_key" in texts:
        return 401
    if any(s in texts for s in ("recharge_required", "insufficient wallet balance", "insufficient_quota")):
        return 402
    return None


def _attempt(url: str, body, timeout: int, keys: list[str], scope: str) -> dict:
    """One request on the first key; a refused one (401/402/403 in the status line or in
    the body, nothing billed) is marked dead for this scope and the same request resent on
    the next key in order. Other errors are raised as is."""
    for i, k in enumerate(keys):
        try:
            out = _send(url, body, timeout, k)
        except AisaError as exc:
            if exc.code not in DEAD_CODES:
                raise
            _mark_dead(k, scope)
            if i + 1 == len(keys):
                raise
            continue
        status = _refused_status(out)
        if status is None:
            return out
        _mark_dead(k, scope)
        if i + 1 == len(keys):
            raise AisaError(f"HTTP {status}: the key was refused: {str(out)[:160]}", status)
    raise AisaError("no key")


def _send(url: str, body, timeout: int, k: str) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {k}",
        "User-Agent": UA, "x-aisa-source": "new-term-radar"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        raise AisaError(f"HTTP {exc.code}: {' '.join(exc.read().decode('utf-8', 'replace').split())[:200]}", exc.code) from exc
    except (OSError, ValueError) as exc:
        raise AisaError(f"{type(exc).__name__}: {exc}") from exc


def call(calls: list[dict]) -> tuple[dict[str, dict], int]:
    """Run one batch of tool calls. Returns ({call_id: data}, cost in micro-USD). Calls
    that failed are left out of the dict; only a failed request raises."""
    if not calls:
        return {}, 0
    if len(calls) > MAX_CALLS:
        raise AisaError(f"{len(calls)} calls in one batch, the cap is {MAX_CALLS}")
    body = _post(ROUTER, {"calls": calls}, TIMEOUT)
    if "error" in body:
        raise AisaError(str(body["error"].get("message") or body["error"])[:200])
    out, cost = {}, 0
    for r in body.get("results", []):
        cost += int(r.get("customer_cost_micros_usd") or 0)
        if r.get("successful"):
            out[r["call_id"]] = r.get("data") or {}
    return out, cost


def call_all(calls: list[dict]) -> tuple[dict[str, dict], int]:
    """call() in batches of BATCH. A failed batch raises and stops the rest."""
    out, cost = {}, 0
    for i in range(0, len(calls), BATCH):
        data, c = call(calls[i:i + BATCH])
        out.update(data)
        cost += c
    return out, cost


def rest(path: str, task: dict) -> tuple[dict, int]:
    """One DataForSEO live task through the passthrough: (its first result, cost in micro-USD)."""
    body = _post(REST + path, [task], TIMEOUT)
    t = (body.get("tasks") or [{}])[0]
    if body.get("status_code") != 20000 or t.get("status_code") != 20000:
        raise AisaError(str(t.get("status_message") or body.get("status_message") or body)[:200])
    return (t.get("result") or [{}])[0] or {}, int(round(float(body.get("cost") or t.get("cost") or 0) * 1e6))


def chat(model: str, system: str, user: str, max_tokens: int = 1500) -> str:
    body = _post(CHAT, {"model": model, "temperature": 0, "max_tokens": max_tokens, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": user}]}, CHAT_TIMEOUT, scope="chat")
    try:
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise AisaError(f"unexpected chat answer: {str(body)[:160]}") from exc
