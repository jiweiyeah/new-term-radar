"""A domain's registration date, from the registry's own RDAP answer.

RDAP is the registry's own answer, free and keyless: 200 with the domain's events (the
registration among them), 404 when the name is not registered. rdap.org sends the question
to the registry that holds the suffix, so one address serves every suffix.

The date says how old a site is, which the traffic reading needs: a site that started
last month is not the "new tool" the radar looks for.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import UA, net

RDAP = "https://rdap.org/domain/"
TIMEOUT = 20


def registered(domain: str) -> str:
    """The day the domain was registered (YYYY-MM-DD); "" when the registry says nothing."""
    req = urllib.request.Request(RDAP + domain, headers={"User-Agent": UA, "Accept": "application/rdap+json"})
    try:
        with net.fetch(req, TIMEOUT) as res:
            body = json.load(res)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ""
        raise RuntimeError(f"RDAP answered {exc.code} for {domain}") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"RDAP for {domain}: {type(exc).__name__}: {exc}") from exc
    for event in body.get("events") or []:
        if event.get("eventAction") == "registration":
            return str(event.get("eventDate") or "")[:10]
    return ""
