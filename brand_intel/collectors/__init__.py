"""
Collectors pull public content about a query and return `Item`s.

Each collector is a plain function `collect(query, limit) -> list[Item]`
registered in COLLECTORS. A failing source logs a warning and returns []
so one blocked site never breaks a scan. Add a source by writing a module
with a `collect` function and adding it here.
"""

from . import hackernews, news, reddit

COLLECTORS = {
    "Google News": news.collect,
    "Reddit": reddit.collect,
    "Hacker News": hackernews.collect,
}


def collect_all(query: str, sources: list[str], limit: int = 25):
    items, errors = [], []
    for name in sources:
        try:
            items.extend(COLLECTORS[name](query, limit))
        except Exception as exc:  # network blocks, rate limits, format changes
            errors.append(f"{name}: {exc}")
    return items, errors
