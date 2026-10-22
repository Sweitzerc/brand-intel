"""
Reddit keyword search via the public JSON endpoint.

Adapted from the idea behind bellingcat/reddit-post-scraping-tool:
find posts that mention a keyword, keep the engagement numbers.
"""

from datetime import datetime, timezone

from ..core import TIMEOUT, Item, http


def parse(payload: dict, limit: int) -> list[Item]:
    items = []
    for child in payload.get("data", {}).get("children", [])[:limit]:
        d = child.get("data", {})
        created = d.get("created_utc")
        items.append(Item(
            title=d.get("title", ""),
            url="https://www.reddit.com" + d.get("permalink", ""),
            source="r/" + d.get("subreddit", ""),
            platform="reddit",
            published=datetime.fromtimestamp(created, timezone.utc).isoformat() if created else "",
            text=(d.get("selftext") or "")[:1000],
            engagement=int(d.get("score", 0)) + int(d.get("num_comments", 0)),
        ))
    return items


def collect(query: str, limit: int = 25) -> list[Item]:
    resp = http().get(
        "https://www.reddit.com/search.json",
        params={"q": f'"{query}"', "sort": "new", "limit": limit},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return parse(resp.json(), limit)
