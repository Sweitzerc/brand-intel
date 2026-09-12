"""Attribute GSC queries to blog pages.

THE GAP: the spec assumes the GSC_Queries tab carries a `page` column, so
that each page's buying intent is the share of ITS OWN queries that look
commercial. It does not. The Apps Script exports queries and pages as two
independent top-N lists:

    GSC_Queries  query, clicks, impressions, ctr, position, ..., bucket
    GSC_Pages    page,  clicks, impressions, ctr, position, ...

There is no join key between them. A real query-by-page join needs a GSC
export with both dimensions, which is a one-line change to the Apps Script.
Until that exists, this module estimates the mapping by matching query terms
against the page's slug and title, and every consumer of the estimate also
reports `intent_query_support` (how many queries backed it) so a page scored
off one weak match is visibly different from one scored off twelve.

Read the estimate as a ranking aid, not as a measurement.
"""

from __future__ import annotations

import re
from typing import Dict, List

STOPWORDS = {
    "a", "an", "the", "for", "to", "of", "and", "or", "my", "your", "you",
    "is", "are", "in", "on", "with", "at", "it", "its", "be", "do", "does",
    "i", "me", "we", "s", "that", "this", "can", "will", "from", "by",
}

# Terms that mean the same thing to a shopper and should match across the
# slug/query boundary.
SYNONYMS = {
    "canes": "cane",
    "sticks": "stick",
    "cain": "cane",
    "cains": "cane",
    "walkingstick": "stick",
    "walkingsticks": "stick",
    "poles": "pole",
    "womens": "women",
    "woman": "women",
    "mens": "men",
    "man": "men",
    "seniors": "senior",
    "elderly": "senior",
    "heights": "height",
    "measuring": "measure",
    "measurement": "measure",
    "measurements": "measure",
    "sizing": "size",
    "handles": "handle",
    "guides": "guide",
    "folding": "fold",
    "foldable": "fold",
    "collapsible": "fold",
    "hiking": "hike",
}

MIN_COVERAGE = 0.6
MIN_MATCHED_TOKENS = 2
MAX_PAGES_PER_QUERY = 3
# A token appearing in more than this share of blog pages is not distinctive.
# Nearly every slug on this site contains "walking", "cane" and "guide", so a
# query that matches only those matches everything and tells us nothing about
# which page it belongs to.
MAX_DOCUMENT_FREQUENCY = 0.30

# Tokens that can never be the distinguishing term, whatever their frequency.
# "stick" appears in only 18 of 92 slugs and so passes the rarity test, but it
# is a core product noun: "walking sticks for men" is not about the wood-canes
# post just because that slug happens to contain the word.
GENERIC_TOKENS = {
    "walking", "walk", "cane", "stick", "staff", "pole", "guide", "guides",
    "complete", "ultimate", "best", "top", "perfect", "choose", "choosing",
    "find", "finding", "your", "you", "how", "what", "why", "which", "right",
    "new", "buy", "buying", "art", "types", "type", "style", "styles",
}


def tokens(text: str) -> List[str]:
    raw = re.findall(r"[a-z0-9']+", (text or "").lower())
    out = []
    for token in raw:
        token = SYNONYMS.get(token, token)
        if token and token not in STOPWORDS and len(token) > 1:
            out.append(token)
    return out


def page_vocabulary(handle: str, title: str = "") -> set:
    return set(tokens(handle.replace("-", " "))) | set(tokens(title))


def distinctive_tokens(pages: Dict[str, set]) -> set:
    """Tokens rare enough across the blog to identify a particular page."""
    if not pages:
        return set()
    counts: Dict[str, int] = {}
    for vocabulary in pages.values():
        for token in vocabulary:
            counts[token] = counts.get(token, 0) + 1
    total = len(pages)
    return {
        t for t, c in counts.items()
        if c / total <= MAX_DOCUMENT_FREQUENCY and t not in GENERIC_TOKENS
    }


def attribute(
    queries: List[dict],
    pages: Dict[str, set],
) -> Dict[str, List[dict]]:
    """Map page handle -> list of {query, coverage, ...} attributed to it.

    `queries` are dicts with at least `query`; `pages` maps handle to its
    vocabulary set. A query is attributed to up to MAX_PAGES_PER_QUERY pages
    whose vocabulary covers enough of the query's terms AND shares at least
    one distinctive token with it.

    Without the distinctiveness rule, a generic query like "walking canes"
    matches every page on the blog and every page's intent score converges on
    the same number. A query too generic to place is attributed to nothing,
    which is the honest outcome: the sheet does not say where it landed.
    """
    by_page: Dict[str, List[dict]] = {handle: [] for handle in pages}
    distinctive = distinctive_tokens(pages)

    for record in queries:
        query_tokens = tokens(record.get("query", ""))
        if not query_tokens:
            continue
        unique = set(query_tokens)

        scored = []
        for handle, vocabulary in pages.items():
            matched = unique & vocabulary
            if len(matched) < MIN_MATCHED_TOKENS:
                continue
            if not (matched & distinctive):
                continue
            coverage = len(matched) / len(unique)
            if coverage >= MIN_COVERAGE:
                scored.append((coverage, len(matched), handle))

        if not scored:
            continue
        scored.sort(reverse=True)
        for coverage, matched, handle in scored[:MAX_PAGES_PER_QUERY]:
            item = dict(record)
            item["coverage"] = round(coverage, 3)
            item["matched_tokens"] = matched
            by_page[handle].append(item)

    return by_page
