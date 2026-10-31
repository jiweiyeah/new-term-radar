"""The Chrome Web Store, read the way the store's own pages read it.

The store's pages hold no review text: the page only says which internal call to make, and
the id of that call rotates. So one GET of the page tells how to ask for the reviews (the
id of the reviews call, the session id and the build id), and one POST per extension and
star level brings them back. Free and keyless, like the page itself.

A review comes back as a row: its id, its author, its stars, its text, when it was written.
The store's own sort is the newest first; `stars` asks for one star level only, which is
how the low-star complaints the radar wants are picked out (an extension with 540 one-star
reviews and 10,470 in all).

Which extensions carry a keyword is asked of the store too: its search page holds its own
links to the extensions it shows, so one GET names them (a search engine's index of the
store is thin and slow to answer).
"""
from __future__ import annotations

import json
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import UA, net

PAGE = "https://chromewebstore.google.com/detail/x/%s/reviews?hl=en"
SEARCH = "https://chromewebstore.google.com/search/%s"
CALLS = "https://chromewebstore.google.com%s/data/batchexecute"
TIMEOUT = 45
RECENT = 2                 # the store's sort order: the newest first
REQUESTS = re.compile(r"AF_dataServiceRequests\s*=\s*(\{.*?\})\s*;", re.S)
RPC_IDS = re.compile(r"'ds:\d+'\s*:\s*\{id:'([^']+)'")
DETAIL = re.compile(r'href="\./detail/[^"]*?/([a-p]{32})"')     # the store's own link to an extension


class CwsError(RuntimeError):
    pass


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        with net.fetch(req, TIMEOUT) as res:
            return res.read().decode("utf-8", "replace")
    except (urllib.error.HTTPError, OSError, ValueError) as exc:
        raise CwsError(f"{type(exc).__name__}: {exc}") from exc


def _post(url: str, form: dict) -> str:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST", headers={
        "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Referer": "https://chromewebstore.google.com/"})
    try:
        with net.fetch(req, TIMEOUT) as res:
            return res.read().decode("utf-8", "replace")
    except (urllib.error.HTTPError, OSError, ValueError) as exc:
        raise CwsError(f"{type(exc).__name__}: {exc}") from exc


def _global_data(html: str) -> dict:
    """The page's global data: the path of the store's own calls and the ids they need."""
    idx = html.find("WIZ_global_data")
    if idx < 0:
        raise CwsError("the store's page carries no global data")
    start = html.find("{", html.find("=", idx))
    depth, inside, escaped = 0, False, False
    for pos, ch in enumerate(html[start:], start):
        if inside:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                inside = False
            continue
        if ch == '"':
            inside = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:pos + 1])
                except ValueError as exc:
                    raise CwsError(f"the store's global data is not JSON: {exc}") from exc
    raise CwsError("the store's global data is not closed")


def _rpcids(html: str) -> list[str]:
    """The ids of the calls the page names, the last one first: the reviews call is the
    one the reviews page ends with, and the ids rotate, so they are read and never written
    down."""
    found = REQUESTS.search(html)
    if not found:
        raise CwsError("the store's page names no call")
    ids = list(reversed(RPC_IDS.findall(found.group(1))))
    if not ids:
        raise CwsError("the store's page names no call id")
    return ids


def _rows(text: str, rpcid: str) -> list[list]:
    """The review rows out of the store's answer."""
    parts = text.split("\n", 2)
    if len(parts) < 3:
        raise CwsError("the store's answer is not a batch")
    try:
        batch = json.loads(parts[2])
    except ValueError as exc:
        raise CwsError(f"the store's answer is not JSON: {exc}") from exc
    for row in batch:
        if isinstance(row, list) and len(row) > 2 and row[0] == "wrb.fr" and row[1] == rpcid:
            if not row[2]:
                raise CwsError("the store's answer holds no reviews")
            try:
                answer = json.loads(row[2])
            except ValueError as exc:
                raise CwsError(f"the store's reviews are not JSON: {exc}") from exc
            return answer[1] if isinstance(answer, list) and len(answer) > 1 else []
    raise CwsError("the store's answer holds no reviews call")


def _day(stamp) -> str:
    """The day a review was written, from the store's [seconds, nanoseconds] stamp."""
    if not isinstance(stamp, (list, tuple)) or not stamp:
        return ""
    try:
        return datetime.fromtimestamp(int(stamp[0]), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def search(keyword: str, limit: int = 10) -> list[str]:
    """The extensions the store lists for a keyword: its own search, free.

    One GET: the search page holds the store's own links to the extensions it shows, so
    the ids are read off it. No key, and no search engine's idea of what the store holds.
    """
    html = _get(SEARCH % urllib.parse.quote(keyword))
    found: list[str] = []
    for m in DETAIL.finditer(html):
        if m.group(1) not in found:
            found.append(m.group(1))
        if len(found) >= limit:
            break
    return found


def page(extension_id: str) -> dict:
    """The extension's page as the store's own handles: where its calls live and which ids
    they carry. One GET, and then as many calls as the star levels asked for."""
    html = _get(PAGE % extension_id)
    wiz = _global_data(html)
    return {"id": extension_id, "path": wiz.get("Im6cmf", ""), "sid": str(wiz.get("FdrFJe") or ""),
            "bl": wiz.get("cfb2h") or "", "rpcids": _rpcids(html)}


def reviews(handle: dict, stars: int | None = None, limit: int = 100) -> list[dict]:
    """The extension's reviews: [{"rating", "text", "date"}], the newest first.

    `stars` asks for one star level only (1 to 5); None asks for all of them. `limit` is how
    many of that level to take: the store's call returns up to a thousand in one answer.
    """
    args = [handle["id"], [limit], RECENT, stars, None, None, 0]
    last = ""
    for rpcid in handle["rpcids"]:
        params = {"rpcids": rpcid, "f.sid": handle["sid"], "bl": handle["bl"],
                  "hl": "en", "soc-app": "1", "soc-platform": "1", "soc-device": "1",
                  "_reqid": str(random.randint(1000, 9999))}
        url = f"{CALLS % handle['path']}?{urllib.parse.urlencode(params)}"
        form = {"f.req": json.dumps([[[rpcid, json.dumps(args), None, "generic"]]])}
        try:
            rows = _rows(_post(url, form), rpcid)
        except CwsError as exc:
            last = str(exc)
            continue
        out = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 5 or not isinstance(row[2], int):
                continue
            text = str(row[3] or "").strip()
            if text:
                out.append({"rating": row[2], "text": text, "date": _day(row[4])})
        return out
    if last:
        raise CwsError(last)
    return []
