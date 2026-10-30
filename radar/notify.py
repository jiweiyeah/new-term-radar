"""Optional report notifications: Resend email and Feishu custom-bot webhook.

Environment: RESEND_API_KEY, EMAIL_FROM (an address on a domain you verified in Resend),
RADAR_EMAIL_TO (comma-separated for several), FEISHU_WEBHOOK.
Missing mail settings skip email; a configured webhook enables Feishu independently.
Notification settings may also live in .env beside config.json; exported values win.
Only email needs the `markdown` package (pip install markdown).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import UA

# Inline styles: Gmail drops a <style> block that sits outside <head>, which leaves
# tables unbordered with centred headers. Every tag carries its own.
FONT = "-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif"
INLINE = {
    "h2": "font-size:18px;margin:0 0 12px;color:#1f2328",
    "h3": "font-size:15px;margin:24px 0 8px;color:#1f2328",
    "p": "margin:8px 0",
    "table": "border-collapse:collapse;margin:8px 0;width:100%",
    "th": "border:1px solid #d0d7de;padding:6px 10px;font-size:13px;background:#f6f8fa;text-align:left;white-space:nowrap",
    "td": "border:1px solid #d0d7de;padding:6px 10px;font-size:13px;vertical-align:top",
    "ul": "padding-left:0;margin:8px 0;list-style:none",
    "li": "margin:0 0 10px;padding:8px 12px;border-left:3px solid #d0d7de;background:#f6f8fa;font-size:13px",
    "a": "color:#0969da",
}
TAG = re.compile(r"<(" + "|".join(INLINE) + r")(\s[^>]*)?>")
_ADDRESS = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[a-z]{2,}$", re.I)
ENV_KEYS = ("FEISHU_WEBHOOK", "RESEND_API_KEY", "EMAIL_FROM", "RADAR_EMAIL_TO")
FEISHU_MAX_BYTES = 18_000   # leave room below the custom bot's 20 KB request limit


def load_env(path: Path) -> None:
    """Read notification KEY=value settings only, without executing shell code.

    Blank lines and comments are ignored; export and surrounding quotes are allowed.
    Explicit environment settings (even empty ones) always take precedence.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if sep and key in ENV_KEYS:
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            os.environ.setdefault(key, value)


def _inline(m: re.Match) -> str:
    attrs = m.group(2) or ""
    if m.group(1) == "td" and 'style="text-align' in attrs:   # markdown's own column alignment
        return f"<td{attrs}>"
    return f'<{m.group(1)} style="{INLINE[m.group(1)]}"{attrs}>'


def html(md_text: str) -> str:
    import markdown
    body = TAG.sub(_inline, markdown.markdown(md_text, extensions=["tables"]))
    return f'<div style="font:14px/1.6 {FONT};color:#1f2328;max-width:900px">{body}</div>'


def recipients(raw: str) -> list[str]:
    """RADAR_EMAIL_TO as people type it: commas (also full-width), semicolons or spaces
    between addresses; quotes and angle brackets around them are dropped."""
    parts = re.split(r"[,，;；\s]+", raw.strip().strip("\"'"))
    return [p.strip("\"'<>") for p in parts if p.strip("\"'<>")]


def send(subject: str, md_text: str) -> str:
    to = recipients(os.environ.get("RADAR_EMAIL_TO", ""))
    if not to:
        return "mail skipped: RADAR_EMAIL_TO not set"
    missing = [key for key in ("RESEND_API_KEY", "EMAIL_FROM") if not os.environ.get(key, "").strip()]
    if missing:
        return f"mail skipped: {', '.join(missing)} not set"
    bad = [i + 1 for i, a in enumerate(to) if not _ADDRESS.match(a)]
    if bad:   # the address itself stays out of the log
        raise SystemExit(f"RADAR_EMAIL_TO: {len(to)} address(es), number {bad} is not an email address")
    body = json.dumps({"from": os.environ["EMAIL_FROM"], "to": to, "subject": subject, "html": html(md_text)}).encode()
    # Resend sits behind Cloudflare, which answers Python's default User-Agent with 403 "error code: 1010".
    req = urllib.request.Request("https://api.resend.com/emails", data=body, method="POST", headers={
        "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}", "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return f"mail sent ({res.status})"
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Resend {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc


def _feishu_body(text: str) -> bytes:
    # Plain text keeps the complete Markdown report, including tables and source URLs.
    return json.dumps({"msg_type": "text", "content": {"text": text}}, ensure_ascii=False).encode("utf-8")


def feishu_messages(subject: str, md_text: str) -> list[bytes]:
    """Keep every report character, splitting on newlines where possible.

    Measure the serialized JSON, not characters: Chinese, emoji and JSON escapes all
    count toward the webhook's request size. Each part repeats the title and its number.
    """
    body = _feishu_body(f"{subject}\n\n{md_text}")
    if len(body) <= FEISHU_MAX_BYTES:
        return [body]
    header = f"{subject} (999999/999999)\n\n"   # reserve more digits than a report needs
    if len(_feishu_body(header + md_text[:1])) > FEISHU_MAX_BYTES:
        raise ValueError("Feishu report title is too long")
    chunks = []
    remaining = md_text
    while remaining:
        low, high = 1, len(remaining)
        while low < high:
            middle = (low + high + 1) // 2
            if len(_feishu_body(header + remaining[:middle])) <= FEISHU_MAX_BYTES:
                low = middle
            else:
                high = middle - 1
        end = low
        if end < len(remaining):
            newline = remaining.rfind("\n", 0, end) + 1
            if newline >= end // 2 and newline:
                end = newline
        chunks.append(remaining[:end])
        remaining = remaining[end:]
    return [_feishu_body(f"{subject} ({i}/{len(chunks)})\n\n{chunk}") for i, chunk in enumerate(chunks, 1)]


def send_feishu(subject: str, md_text: str) -> str:
    webhook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        return "feishu skipped: FEISHU_WEBHOOK not set"
    # This URL is a credential. Never include it (or network error details) in logs.
    target = urlsplit(webhook)
    if (target.scheme != "https" or target.netloc != "open.feishu.cn"
            or not target.path.startswith("/open-apis/bot/v2/hook/")
            or not target.path.removeprefix("/open-apis/bot/v2/hook/")
            or target.query or target.fragment):
        return "feishu failed: FEISHU_WEBHOOK must be a Feishu custom-bot HTTPS URL"
    try:
        messages = feishu_messages(subject, md_text)
    except ValueError:
        return "feishu failed: report title is too long"
    for i, body in enumerate(messages, 1):
        if i > 1:
            time.sleep(1)   # space out long reports rather than burst all parts
        req = urllib.request.Request(webhook, data=body, method="POST", headers={
            "Content-Type": "application/json; charset=utf-8", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                result = json.loads(res.read())
            if not isinstance(result, dict):
                return f"feishu failed ({i}/{len(messages)}): invalid response"
            code = result.get("code", result.get("StatusCode"))
            if type(code) is not int or code != 0:
                # Response messages can echo credentials or report data; log only an integer code.
                status = str(code) if type(code) is int else "unknown"
                return f"feishu failed ({i}/{len(messages)}): code {status}"
        except urllib.error.HTTPError as exc:
            return f"feishu failed ({i}/{len(messages)}): HTTP {exc.code}"
        except (urllib.error.URLError, OSError):
            return f"feishu failed ({i}/{len(messages)}): network error"
        except (ValueError, UnicodeError):
            return f"feishu failed ({i}/{len(messages)}): invalid response"
    return f"feishu sent ({len(messages)} message(s))"
