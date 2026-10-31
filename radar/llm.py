"""The model door: an OpenAI-compatible gateway, Monid's chat second.

The radar asks a model to judge names, screen keyword candidates, read what people
complain about, sort a seller's sites by kind. Those calls go to an OpenAI-compatible
gateway (OPENAI_BASE_URL, OPENAI_API_KEY): the OpenAI SDK when it is installed, the
standard library's urllib when it is not, so the package still runs on the standard
library alone. AIsa is not a model door: it stays the second door for Google's own
numbers (radar/dfs.py).

The gateway names its models with the maker in front ("openai/gpt-5.4-mini"), so the name
the config asks for is not always the name the gateway knows. The gateway's own model
list says which name it has; the name that answered is remembered for the run, so the
report can name it.

Monid answers when the gateway does not: its own chat is the second door, one paid call.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request

from . import UA, monid, net

BASE = os.environ.get("OPENAI_BASE_URL", "https://api.y-api.bestvirtualgoods.com/v1").rstrip("/")
TIMEOUT = 120            # seconds one answer may take
TRIES = 3                # how many times a connection that never left is asked again
PAUSE = 0.5              # seconds between two tries
# The models to fall back on, in order, when the gateway does not carry the one the config
# asks for. The cheap ones first: the radar's model calls judge lists, they do not write.
DEFAULT_CHAIN = ("openai/gpt-5.4-mini", "anthropic/claude-haiku-4.5", "google/gemini-3.1-flash-lite",
                 "deepseek/deepseek-v4-flash", "z-ai/glm-5.3-flash")
# The model Monid's own chat is asked for when the gateway does not answer: the one it
# carries by default.
MONID_DEFAULT = "gpt-4o-mini"

_answered = ""           # the model that answered last, for the report
_models: list[str] | None = None       # the gateway's model list, read once per run


class LlmError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, body: str = ""):
        super().__init__(message)
        self.code = code
        self.body = body


def key() -> str:
    """The model key; empty when there is none."""
    return os.environ.get("OPENAI_API_KEY", "").strip()


def has_key() -> bool:
    """Whether a model key is set at all."""
    return bool(key())


def answered_model() -> str:
    """The model that answered last; empty when none did."""
    return _answered


def models() -> list[str]:
    """The gateway's own model list, read once per run.

    An empty list means the list could not be read: the names are then tried as they are,
    and the list is read again next time (a broken handshake once is not a broken list)."""
    global _models
    if _models is None:
        found = _read_models()
        if found:
            _models = found
    return _models or []


def _read_models() -> list[str]:
    req = urllib.request.Request(f"{BASE}/models", headers={
        "Authorization": f"Bearer {key()}", "User-Agent": UA, "x-source": "new-term-radar"})
    try:
        with net.fetch(req, timeout=30) as res:
            body = json.load(res)
    except (OSError, ValueError):
        return []
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    return sorted(str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id"))


def candidates(model: str) -> list[str]:
    """The names to try, in order.

    The gateway names its models with the maker in front, so the name the config asks for
    ("gpt-5.4-mini") is not always the name the gateway has ("openai/gpt-5.4-mini"). Its
    own list says which name it knows; when that list cannot be read, the name asked for
    is tried as it is, and the default chain behind it."""
    known = models()
    out: list[str] = []
    tail = model.rsplit("/", 1)[-1]
    if "/" in model:
        out.append(model)
    else:
        out += [m for m in known if m == model]
        out += [m for m in known if m != model and m.rsplit("/", 1)[-1] == tail][:1]
        if not out:
            out.append(model)
    for m in DEFAULT_CHAIN:
        if m not in out and (not known or m in known):
            out.append(m)
    return out


def chat(model: str, system: str, user: str, max_tokens: int = 1500) -> str:
    """One model answer: the gateway first, Monid's chat second.

    Both failures are named. The model that answered is remembered, so the report can
    name it."""
    global _answered
    if has_key():
        try:
            answer, name = _gateway(model, system, user, max_tokens)
        except LlmError as first:
            return _monid(model, system, user, max_tokens, first)
        _answered = name
        return answer
    return _monid(model, system, user, max_tokens, LlmError("no model key: set OPENAI_API_KEY"))


def _monid(model: str, system: str, user: str, max_tokens: int, first: LlmError) -> str:
    """Monid's chat, the second door. Its own models are named differently, so the model
    it is asked for is one it carries."""
    global _answered
    name = model.rsplit("/", 1)[-1]
    if name not in monid.CHAT_MODELS:
        name = MONID_DEFAULT
    try:
        answer = monid.chat(name, system, user, max_tokens=max_tokens)
    except monid.MonidError as second:
        raise LlmError(f"the gateway: {first}; Monid: {second}") from second
    _answered = name
    return answer


def _gateway(model: str, system: str, user: str, max_tokens: int) -> tuple[str, str]:
    """The gateway's answer and the model that gave it.

    A name the gateway does not carry is not the end of the road: the next name in the
    list is asked, until the default chain runs out."""
    last = ""
    for name in candidates(model):
        try:
            return _ask(name, system, user, max_tokens), name
        except LlmError as exc:
            if not _no_such_model(exc):
                raise
            last = str(exc)
    raise LlmError(f"the gateway does not carry {model}: {last}")


def _ask(model: str, system: str, user: str, max_tokens: int) -> str:
    """One answer from the gateway: the OpenAI SDK when it is installed, urllib when not."""
    sdk = _sdk()
    if sdk is None:
        return _ask_http(model, system, user, max_tokens)
    return _ask_sdk(sdk, model, system, user, max_tokens)


def _sdk():
    """The OpenAI SDK, when it is installed."""
    try:
        import openai
    except ImportError:
        return None
    return openai


def _ask_sdk(sdk, model: str, system: str, user: str, max_tokens: int) -> str:
    """One answer through the SDK. A connection that never left is asked again; an answer
    that broke halfway is not, because it may already have been billed."""
    last: Exception | None = None
    for attempt in range(TRIES):
        try:
            client = sdk.OpenAI(base_url=BASE, api_key=key(), timeout=TIMEOUT, max_retries=0)
            res = client.chat.completions.create(
                model=model, temperature=0, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            return _content(res)
        except sdk.APIConnectionError as exc:
            if not _undelivered(exc):
                raise LlmError(f"the gateway's answer did not arrive: {exc}") from exc
            last = exc
        except sdk.APIStatusError as exc:
            body = _text(exc.body)
            raise LlmError(f"HTTP {exc.status_code}: {_why(body)}", exc.status_code, body) from exc
        except LlmError:
            raise
        except Exception as exc:                      # the SDK's own shapes
            raise LlmError(f"{type(exc).__name__}: {exc}") from exc
        if attempt + 1 < TRIES:
            time.sleep(PAUSE * (attempt + 1))
    raise LlmError(f"the gateway did not answer: {last}")


def _ask_http(model: str, system: str, user: str, max_tokens: int) -> str:
    """One answer through the standard library: the same protocol, no SDK needed."""
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key()}",
                                          "User-Agent": UA, "x-source": "new-term-radar"})
    try:
        with net.fetch(req, timeout=TIMEOUT) as res:
            out = json.load(res)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise LlmError(f"HTTP {exc.code}: {_why(raw)}", exc.code, raw) from exc
    except (OSError, ValueError) as exc:
        raise LlmError(f"{type(exc).__name__}: {exc}") from exc
    if isinstance(out, dict) and out.get("error"):
        raw = json.dumps(out)
        raise LlmError(f"the gateway refused: {_why(raw)}", None, raw)
    try:
        return out["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError(f"unexpected chat answer: {str(out)[:160]}") from exc


def _content(res) -> str:
    """The answer's text out of the SDK's own object."""
    try:
        return res.choices[0].message.content or ""
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise LlmError(f"unexpected chat answer: {str(res)[:160]}") from exc


def _undelivered(exc: BaseException) -> bool:
    """Whether the request never left: the connection broke before it was sent.

    An answer that breaks halfway through may already have been billed, so it is not
    asked again."""
    cause = exc.__cause__ or exc
    seen = [cause, getattr(cause, "__cause__", None), getattr(cause, "__context__", None)]
    for c in seen:
        if c is None:
            continue
        if isinstance(c, (ConnectionRefusedError, socket.gaierror)):
            return True
        if type(c).__name__ in ("ConnectError", "ConnectTimeout"):
            return True
    return False


def _no_such_model(exc: LlmError) -> bool:
    """Whether the gateway refused the model's name: it does not carry that model."""
    text = f"{exc.body} {exc}".lower()
    return any(s in text for s in ("model_not_found", "no available channel", "model not found", "unknown model"))


def _text(body) -> str:
    return body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")


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
