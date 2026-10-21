"""
Topic bucketing: what are people actually talking about?

Inspired by bellingcat/smart-image-sorter, which sorts images into
user-defined labels with zero-shot models. Here the same trick sorts
text: you define the topic labels in config.yaml, no training needed.
"""

from functools import lru_cache

from . import ai_enabled

MODEL = "typeform/distilbert-base-uncased-mnli"
THRESHOLD = 0.5


@lru_cache(maxsize=1)
def _model():
    from transformers import pipeline
    return pipeline("zero-shot-classification", model=MODEL)


def keyword_topics(text: str, topics: dict[str, list[str]]) -> list[str]:
    low = text.lower()
    return [label for label, words in topics.items() if any(w.lower() in low for w in words)]


def analyze(items, topics: dict[str, list[str]]):
    if not topics:
        return items
    labels = list(topics)
    use_ai = ai_enabled()
    for item in items:
        found = keyword_topics(item.full_text, topics)
        if use_ai and not found:
            res = _model()(item.full_text[:512], labels, multi_label=True)
            found = [l for l, s in zip(res["labels"], res["scores"]) if s >= THRESHOLD]
        item.topics = found
    return items


def breakdown(items) -> list[tuple[str, int, float]]:
    """(topic, mentions, average tone) sorted by mentions."""
    stats: dict[str, list[float]] = {}
    for item in items:
        for t in item.topics:
            stats.setdefault(t, []).append(item.score or 0.0)
    rows = [(t, len(v), round(sum(v) / len(v), 2)) for t, v in stats.items()]
    return sorted(rows, key=lambda r: -r[1])
