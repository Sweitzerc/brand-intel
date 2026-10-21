"""
Core building blocks shared by every module.

The framework follows the same shape as Bellingcat's auto-archiver:
small, swappable modules wired into a pipeline.

    COLLECT  (collectors/)    -> list[Item]   public posts, headlines, filings
    ANALYZE  (analyzers/)     -> enrich Items  sentiment, topics, hashtags
    INVESTIGATE (investigators/)               one-off lookups: site networks,
                                               lookalike domains, evidence archiving
    STORE    (storage.py)     -> SQLite        history for trends + evidence log
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import requests
import yaml

log = logging.getLogger("brand_intel")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

# Every outbound request goes through one session so timeouts and the
# User-Agent are consistent. SEC EDGAR requires a UA with contact info;
# set `contact_email` in config.yaml to supply it.
_session: requests.Session | None = None
TIMEOUT = 15


def http() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        contact = load_config().get("contact_email") or "no-contact-set"
        _session.headers["User-Agent"] = f"BrandIntel/1.0 (open-source research; {contact})"
    return _session


@dataclass
class Item:
    """One piece of public content about the brand, from any source."""

    title: str
    url: str
    source: str            # publisher / subreddit / site
    platform: str          # "news", "reddit", "hackernews", ...
    published: str = ""    # ISO-ish date string when known
    text: str = ""         # body / snippet when available
    engagement: int = 0    # upvotes, points, comments - whatever the platform gives
    score: float | None = None   # sentiment, -1..+1
    topics: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def full_text(self) -> str:
        return f"{self.title}. {self.text}".strip(". ")


@lru_cache(maxsize=None)
def load_config(path: Path = CONFIG_PATH) -> dict:
    return (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
