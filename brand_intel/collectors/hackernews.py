"""Hacker News stories and comments via the free Algolia search API."""

from ..core import TIMEOUT, Item, http


def parse(payload: dict, limit: int) -> list[Item]:
    items = []
    for hit in payload.get("hits", [])[:limit]:
        title = hit.get("title") or hit.get("story_title") or ""
        body = hit.get("comment_text") or hit.get("story_text") or ""
        items.append(Item(
            title=title,
            url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            source="Hacker News",
            platform="hackernews",
            published=hit.get("created_at", ""),
            text=body[:1000],
            engagement=int(hit.get("points") or 0) + int(hit.get("num_comments") or 0),
        ))
    return items


def collect(query: str, limit: int = 25) -> list[Item]:
    resp = http().get(
        "https://hn.algolia.com/api/v1/search_by_date",
        params={"query": f'"{query}"', "hitsPerPage": limit, "tags": "(story,comment)"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return parse(resp.json(), limit)
