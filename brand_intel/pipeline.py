"""COLLECT -> ANALYZE -> STORE for one brand scan. Usable without the UI:

    from brand_intel.pipeline import run_scan
    scan = run_scan("Canes Galore", ["Google News", "Reddit"])
"""

from dataclasses import dataclass, field

from . import storage
from .analyzers import conversation, sentiment, topics
from .collectors import collect_all
from .core import Item, load_config


@dataclass
class Scan:
    query: str
    items: list[Item]
    errors: list[str] = field(default_factory=list)

    @property
    def avg(self) -> float:
        scored = [i.score for i in self.items if i.score is not None]
        return round(sum(scored) / len(scored), 2) if scored else 0.0

    def by_source(self) -> list[tuple[str, int, float]]:
        groups: dict[str, list[float]] = {}
        for i in self.items:
            groups.setdefault(i.platform, []).append(i.score or 0.0)
        return [(p, len(v), round(sum(v) / len(v), 2)) for p, v in groups.items()]

    def topics(self):
        return topics.breakdown(self.items)

    def conversation(self):
        return conversation.analyze(self.items, self.query)


def run_scan(query: str, sources: list[str], limit: int = 25, save: bool = True) -> Scan:
    items, errors = collect_all(query, sources, limit)
    # The same story often appears in several feeds - keep the first copy.
    seen, unique = set(), []
    for i in items:
        key = i.title.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(i)
    sentiment.analyze(unique)
    topics.analyze(unique, load_config().get("topics", {}))
    scan = Scan(query, unique, errors)
    if save and unique:
        storage.save_scan(query, sources, unique, scan.avg)
    return scan
