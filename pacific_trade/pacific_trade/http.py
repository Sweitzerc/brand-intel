"""Small HTTP helpers: retries with backoff, on-disk response cache, streamed downloads."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import requests

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "pacific-trade/0.1 (research pipeline)"

RETRY_STATUS = {429, 500, 502, 503, 504}


def get(url: str, *, params=None, headers=None, timeout=120, retries=4, stream=False,
        allow_redirects=True) -> requests.Response:
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            r = SESSION.get(url, params=params, headers=headers, timeout=timeout, stream=stream,
                            allow_redirects=allow_redirects)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == retries:
                raise
        else:
            if r.status_code not in RETRY_STATUS or attempt == retries:
                return r
        time.sleep(delay)
        delay *= 2
    raise AssertionError("unreachable")


def cache_key(url: str, params: dict | None) -> str:
    # Keys never enter the cache key, so rotating a key does not invalidate the cache.
    clean = {k: v for k, v in sorted((params or {}).items()) if k not in {"key", "subscription-key"}}
    return hashlib.sha256(json.dumps([url, clean], sort_keys=True).encode()).hexdigest()[:24]


def cached_json(cache_dir: Path, url: str, params: dict | None, fetch) -> object:
    """Return cached JSON for (url, params) or call ``fetch()`` and store its result."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{cache_key(url, params)}.json"
    if path.exists():
        return json.loads(path.read_text())
    payload = fetch()
    path.write_text(json.dumps(payload))
    return payload


def download(url: str, dest: Path, chunk=1 << 20) -> Path:
    """Stream ``url`` to ``dest`` atomically; skip if already present."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for block in r.iter_content(chunk):
                f.write(block)
    tmp.replace(dest)
    return dest
