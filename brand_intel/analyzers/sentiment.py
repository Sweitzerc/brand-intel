"""
Tone scoring: each Item gets a signed score from -1 (negative) to +1 (positive).

Uses a Hugging Face sentiment model when available, otherwise a small
word list so the app works without downloading anything.
"""

import re
from functools import lru_cache

from . import ai_enabled

MODEL = "distilbert-base-uncased-finetuned-sst-2-english"

POSITIVE = set("""
love loved great excellent amazing beautiful best perfect happy recommend sturdy
elegant stylish helpful comfortable quality favorite gorgeous fantastic wonderful
reliable durable lightweight awesome pleased impressed thanks win wins award
""".split())
NEGATIVE = set("""
broke broken bad worst terrible awful cheap flimsy scam fake counterfeit late lost
rude refund return disappointed poor horrible hate useless complaint lawsuit
recall injury injured fraud slow damaged defective unsafe problem issue
""".split())


@lru_cache(maxsize=1)
def _model():
    from transformers import pipeline
    return pipeline("sentiment-analysis", model=MODEL)


def lexicon_score(text: str) -> float:
    words = re.findall(r"[a-z']+", text.lower())
    pos = sum(w in POSITIVE for w in words)
    neg = sum(w in NEGATIVE for w in words)
    if pos == neg:
        return 0.0
    return round((pos - neg) / (pos + neg), 2)


def score_texts(texts: list[str]) -> list[float]:
    if not texts:
        return []
    if not ai_enabled():
        return [lexicon_score(t) for t in texts]
    results = _model()([t[:512] for t in texts], truncation=True)
    return [round(r["score"] if r["label"] == "POSITIVE" else -r["score"], 2) for r in results]


def analyze(items):
    for item, s in zip(items, score_texts([i.full_text for i in items])):
        item.score = s
    return items


def mood(avg: float) -> str:
    return "positive" if avg > 0.15 else "negative" if avg < -0.15 else "mixed"
