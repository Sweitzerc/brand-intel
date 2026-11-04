"""
Evidence locker: preserve a page before it disappears.

A lightweight take on bellingcat/auto-archiver. For each URL we
  1) ask the Wayback Machine to save a public copy, and
  2) keep our own record: timestamp, page title, and a SHA-256 hash of
     the content we fetched, so you can later show it hasn't changed.
Use it for counterfeit listings, fake reviews, copied product photos.
"""

import hashlib
import re
from datetime import datetime, timezone

from ..core import http

TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def archive(url: str) -> dict:
    record = {
        "url": url,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "title": "", "sha256": "", "wayback_url": "", "note": "",
    }
    try:
        resp = http().get(url, timeout=20)
        record["sha256"] = hashlib.sha256(resp.content).hexdigest()
        m = TITLE.search(resp.text)
        record["title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:200] if m else ""
    except Exception as exc:
        record["note"] = f"fetch failed: {exc}"
    try:
        save = http().get(f"https://web.archive.org/save/{url}", timeout=60)
        loc = save.headers.get("Content-Location") or ""
        record["wayback_url"] = f"https://web.archive.org{loc}" if loc else save.url
    except Exception as exc:
        record["note"] = (record["note"] + "; " if record["note"] else "") + f"wayback failed: {exc}"
    return record
