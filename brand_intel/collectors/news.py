"""Google News RSS search - free, no API key."""

from urllib.parse import quote_plus

import feedparser

from ..core import TIMEOUT, Item, http


def parse(feed_bytes: bytes, limit: int) -> list[Item]:
    feed = feedparser.parse(feed_bytes)
    items = []
    for entry in feed.entries[:limit]:
        items.append(Item(
            title=entry.get("title", ""),
            url=entry.get("link", ""),
            source=entry.get("source", {}).get("title", ""),
            platform="news",
            published=entry.get("published", ""),
        ))
    return items


def collect(query: str, limit: int = 25) -> list[Item]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    resp = http().get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse(resp.content, limit)
