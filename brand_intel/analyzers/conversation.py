"""
Conversation map: hashtags and terms that travel with the brand.

Inspired by bellingcat/tiktok-hashtag-analysis, which counts hashtag
co-occurrence to reveal the communities around a topic. The same
counting works on any text we collect.
"""

import re
from collections import Counter
from itertools import combinations

STOPWORDS = set("""
a an and are as at be been but by can did do does for from had has have he her his
how i if in into is it its just me my no not of on or our out over she so than that
the their them then there these they this to too up us was we were what when which
who why will with would you your about after all also any more new now one only some
get got via says said vs amp https http www com
""".split())

HASHTAG = re.compile(r"#(\w{2,})")
WORD = re.compile(r"[a-zA-Z][a-zA-Z'-]{2,}")


def terms(text: str, exclude: set[str]) -> set[str]:
    tags = {"#" + t.lower() for t in HASHTAG.findall(text)}
    words = {w.lower().strip("'-") for w in WORD.findall(HASHTAG.sub(" ", text))}
    return tags | {w for w in words if w not in STOPWORDS and w not in exclude}


def analyze(items, query: str, top: int = 25):
    """Return (top terms, top co-occurring pairs) across all items."""
    exclude = {w.lower() for w in WORD.findall(query)}
    counts, pairs = Counter(), Counter()
    for item in items:
        t = terms(item.full_text, exclude)
        counts.update(t)
        pairs.update(combinations(sorted(t), 2))
    top_terms = [(t, n) for t, n in counts.most_common(top) if n > 1] or counts.most_common(top)
    top_pairs = [(f"{a} + {b}", n) for (a, b), n in pairs.most_common(top) if n > 1]
    return top_terms, top_pairs
