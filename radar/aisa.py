"""Client for AIsa, the radar's second paid dependency: its tool router (X posts, Google
Trends and whois data through DataForSEO). Keys: AISA_API_KEY, comma-separated for
several, or the one the AIsa CLI stores when you log in. The keys are used in order:
always the first one not yet marked out of quota. A request refused with 401/402/403 (bad
key, no balance) bills nothing, so that key is marked dead and the same request is resent
on the next key. The marks live in the state directory (state/aisa_keys.json,
fingerprints only) so later runs skip those keys too. Any other failure is raised as is —
a failed batch is never retried.

An empty wallet is the account's, not the key's: it is named and raised, and the key stays
alive, so a top-up makes it work again without a manual reset.

AIsa is not a model door: the radar's model calls go to the OpenAI-compatible gateway
(radar/llm.py), Monid's chat second. AIsa is the second door for Google's own numbers
(radar/dfs.py): Monid is asked first there.

The tool router runs a batch's calls one by one, about 10 s each, so batches are kept to
BATCH calls and the timeout is long.

Prices seen in October 2026: an account's last 20 posts $0.003, one Trends task $0.0012,
one whois overview query about $0.13.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from . import UA, net

ROUTER = os.environ.get("AISA_ROUTER_BASE_URL", "https://tools.aisa.one") + "/v1/tool-router/aisa-batch-use"
# DataForSEO through AIsa's REST passthrough. The tool router checks arguments against a schema that types
# `filters` as objects, so DataForSEO's own filter syntax ([field, op, value] arrays joined by "and") is refused
# there (whois overview, backlinks; reported to AIsa on 2026-10-15). The passthrough takes it as DataForSEO does.
REST = os.environ.get("AISA_API_BASE_URL", "https://api.aisa.one") + "/apis/v1/dataforseo/"
MAX_CALLS = 20      # the router's own limit per batch
BATCH = 5           # what call_all sends at once, about 50 s a batch
TIMEOUT = 240


class AisaError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


DEAD_CODES = frozenset({401, 402, 403})   # key refused, nothing billed: mark it and move on
# A model that is not paid for is refused with the same 402 as an empty wallet, but the key
# itself is fine: marking it dead would take every other model down with it.
MODEL_COST = ("requires a paid balance", "model not found", "model is not available", "model not enabled")
# The wallet belongs to the account, not to the key: an empty wallet says nothing about the
# key, and a top-up must make the key work again without a manual reset. So a refusal of
# this kind is named and raised, and the key stays alive.
WALLET = ("insufficient wallet balance", "insufficient_wallet_balance", "recharge_required",
          "wallet balance is insufficient")
_ledger: Path | None = None               # where dead keys are remembered; set by set_key_ledger()
_dead_run: dict[str, set[str]] = {"data": set()}   # this run's marks, also kept without a ledger
_ledger_lock = threading.Lock()           # the signals read at once, and each may meet a dead key

# AIsa bills its tool router from one wallet, so a key out of balance cannot read anything
# else here: there is one scope.
SCOPES = ("data",)


def set_key_ledger(path: Path) -> None:
    """Remember dead keys in a file (called once per run with the state directory)."""
    global _ledger
    _ledger = path


def key(scope: str = "data") -> str:
    """The first AIsa key that is not marked out of quota for this scope."""
    return _live_keys(scope)[0]


def has_key() -> bool:
    """Whether there is a key at all (not whether it is alive: an empty wallet still counts)."""
    return bool(_keys())


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
    with _ledger_lock:            # several signals read at once, and each may meet a dead key
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


def _error_texts(body) -> str:
    """Every error message in an answer, as one text."""
    if not isinstance(body, dict):
        return ""
    errors = [r.get("error") for r in body.get("results", []) if isinstance(r, dict) and r.get("error")]
    if body.get("error"):
        errors.append(body["error"])
    return " ".join(str(e.get("message") or "") + str(e.get("code") or "") for e in errors if isinstance(e, dict))


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
    results = [r for r in body.get("results", []) if isinstance(r, dict)]
    if len(results) > 1 and any(not r.get("error") for r in results):
        return None           # the key worked for some calls of the batch: it is not refused
    texts = _error_texts(body)
    lowered = texts.lower()   # the gateways write the same words in different cases
    statuses = {e.get("status") for e in errors if isinstance(e, dict) and isinstance(e.get("status"), int)}
    if any(s in lowered for s in WALLET):
        return None                       # the account's wallet is empty, not this key
    if statuses and statuses <= set(DEAD_CODES):
        return min(statuses)
    if "invalid_api_key" in lowered:
        return 401
    if any(s in lowered for s in MODEL_COST):
        return None                       # one model is not paid for: the key still works
    if "insufficient_quota" in lowered:
        return 402
    return None


def _model_level(text: str) -> bool:
    """Whether a refusal is about one model that is not paid for, not about the key."""
    lowered = text.lower()
    return any(s in lowered for s in MODEL_COST)


def _wallet_level(text: str) -> bool:
    """Whether a refusal is about an empty wallet, which is the account's, not the key's."""
    lowered = text.lower()
    return any(s in lowered for s in WALLET)


def _attempt(url: str, body, timeout: int, keys: list[str], scope: str) -> dict:
    """One request on the first key; a refused one (401/402/403 in the status line or in
    the body, nothing billed) is marked dead for this scope and the same request resent on
    the next key in order. Other errors are raised as is."""
    for i, k in enumerate(keys):
        try:
            out = _send(url, body, timeout, k)
        except AisaError as exc:
            if exc.code not in DEAD_CODES or _model_level(str(exc)):
                raise
            if _wallet_level(str(exc)):
                raise AisaError(f"the AIsa wallet is empty: {exc}") from exc
            _mark_dead(k, scope)
            if i + 1 == len(keys):
                raise
            continue
        status = _refused_status(out)
        if status is None:
            if _wallet_level(_error_texts(out)):
                raise AisaError(f"the AIsa wallet is empty: {_error_texts(out)[:160]}")
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
        with net.fetch(req, timeout=timeout) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        raise AisaError(f"HTTP {exc.code}: {_why(exc.read().decode('utf-8', 'replace'))}", exc.code) from exc
    except (OSError, ValueError) as exc:
        raise AisaError(f"{type(exc).__name__}: {exc}") from exc


def _why(raw: str) -> str:
    """The reason out of an answer body, when the body carries one."""
    try:
        body = json.loads(raw)
    except ValueError:
        return " ".join(raw.split())[:200]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:200]
    return str(error or body)[:200]


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
