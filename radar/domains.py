"""The name a domain carries.

The radar reads a site's name from its domain in two places: the Ads Transparency Center is
asked for a site under its name (a brand is found by its name, not by its address), and the
reports name a site by its name too. The suffix is no part of it, and neither is "www.".
"""
from __future__ import annotations

import re

GENERIC = ("blog", "www", "shop", "store", "news", "my", "go", "get", "app", "help", "support",
           "en", "us", "uk", "de", "fr", "jp", "m")


def name_of(domain: str) -> str:
    """The name a domain carries: "vogue.com" -> "vogue", "vogue.com.tw" -> "vogue",
    "www.vogue.com" -> "vogue", "ai-tools.co.uk" -> "ai-tools", "blog.shopify.com" -> "shopify"."""
    labels = [x for x in re.sub(r"^www\.", "", domain.lower()).split(".") if x]
    if not labels:
        return domain.lower()
    if len(labels) > 1 and labels[0] in GENERIC:
        labels = labels[1:]
    return labels[0]
