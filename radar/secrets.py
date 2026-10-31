"""Where the radar's API keys come from.

Every secret lives in the environment: MONID_API_KEY (the paid readings), AISA_API_KEY (the
second door for Google's own numbers, comma-separated for several), OPENAI_API_KEY (the
model gateway), TRUSTMRR_API_KEY (optional), GITHUB_TOKEN (optional, only raises the rate
limit) and GSC_TOKEN_JSON (optional, for CI). A missing key turns its own signal off; the
others keep working.

The .env file beside config.json may hold them too, in plain KEY=value lines, without
executing shell code. Exported environment variables win, even empty ones.
"""
from __future__ import annotations

import os
from pathlib import Path

# The keys the radar reads from the environment.
DATA_KEYS = (
    "AISA_API_KEY",
    "MONID_API_KEY",
    "OPENAI_API_KEY",
    "TRUSTMRR_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GSC_TOKEN_JSON",
)

# The settings that are not secrets but are read the same way.
SETTINGS = ("OPENAI_BASE_URL",)


def read_env(path: Path) -> dict[str, str]:
    """The KEY=value settings of a .env file, without executing shell code.

    Blank lines and comments are ignored; export and surrounding quotes are allowed.
    A line without "=" is skipped. The first setting of a key wins, as in the
    notification loader that read .env before this one did.
    """
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or key in out:
            continue
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        out[key] = value
    return out


def load_env(path: Path, wanted: tuple[str, ...] | None = None) -> None:
    """Put the wanted settings of a .env file into the environment.

    Explicit environment settings (even empty ones) always take precedence, so a
    shell export overrides the file and an exported empty value disables a key.
    """
    for key, value in read_env(path).items():
        if key in (DATA_KEYS + SETTINGS if wanted is None else wanted):
            os.environ.setdefault(key, value)
