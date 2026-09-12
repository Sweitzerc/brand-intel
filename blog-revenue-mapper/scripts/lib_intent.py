"""Classify a search query as commercial, informational or navigational.

Rules first. The rules below settle the large majority of the 275 queries in
the sheet; whatever is left is reported as `ambiguous` so a model pass can
handle only that remainder rather than the whole list.

Every query this site ranks for is, broadly, commercial: 267 of the 275
queries in the sheet mention a product noun. A commercial/not-commercial
boolean therefore has no variance and cannot rank anything. So each label
also carries a WEIGHT expressing how close the query sits to a purchase:

  1.00  condition + product noun + modifier. "best walking cane for
        arthritis" names a use case AND a selection criterion. The reader has
        already decided to buy and is choosing between options. This is the
        top tier by construction.
  0.90  an explicit buying term with a product noun - best, buy, review, vs
  0.80  condition + product noun, no modifier. "cane for arthritis"
  0.70  a sizing question about a product. Someone measuring their wrist is
        shopping, not browsing
  0.50  a bare product noun - shopping behaviour, but unqualified
  0.30  ambiguous
  0.10  informational
  0.00  navigational

`buying_intent_score` is a weighted mean of these, where each query's weight
is its impressions discounted by rank position - see POSITION_WEIGHT in
score.py. A query sitting at position 40 carries far less signal about what
this page is for than one at position 6.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

BRAND_TOKENS = {"canes galore", "canesgalore", "cane galore", "canes-galore"}

COMMERCIAL_WORDS = {
    "best", "buy", "shop", "sale", "cheap", "review", "reviews", "top",
    "vs", "versus", "price", "cost", "for sale", "where to buy", "near me",
    "deal", "deals", "discount", "brand", "brands", "quality", "premium",
    "luxury", "custom", "handmade", "designer",
}

# A product noun. "cane for arthritis" is commercial; "arthritis" alone is not.
PRODUCT_NOUNS = {
    "cane", "canes", "stick", "sticks", "walking stick", "walking sticks",
    "walking cane", "walking canes", "staff", "staffs", "pole", "poles",
    "crutch", "crutches", "walker", "walkers", "tip", "tips", "ferrule",
    "handle", "handles", "shillelagh", "blackthorn", "umbrella",
    # Common misspellings that appear in the live query data.
    "cain", "cains", "walking cain", "walking cains", "walkingstick",
    "walkingsticks", "sheleighly", "shillelaghs",
}

# Condition / use-case words. Only commercial when paired with a product noun.
CONDITION_WORDS = {
    "arthritis", "parkinsons", "parkinson", "balance", "stability", "seniors",
    "senior", "elderly", "knee", "hip", "back", "injury", "recovery",
    "surgery", "neuropathy", "ms", "stroke", "sciatica", "vertigo",
    "tremor", "disability", "mobility", "bariatric", "heavy duty", "obese",
}

INFORMATIONAL_PREFIXES = ("how to", "what is", "what are", "why", "when to", "can you", "do i", "should i")
INFORMATIONAL_WORDS = {
    "how", "what", "why", "causes", "cause", "exercises", "exercise",
    "history", "meaning", "definition", "symptoms", "difference",
    "benefits", "instructions", "guide",
}

PRICE_RE = re.compile(r"(\$|\bunder\s+\d|\bover\s+\d|\d+\s*dollar)")
SIZE_RE = re.compile(
    r"\b(what size|which size|how tall|what height|how long|size chart|"
    r"height chart|measure|measurement|sizing|fit|\d+\s*(inch|in|\"))\b"
)

COMMERCIAL = "commercial"
INFORMATIONAL = "informational"
NAVIGATIONAL = "navigational"
AMBIGUOUS = "ambiguous"

# How close each rule sits to a purchase. Keyed by the reason a rule fired.
WEIGHTS = {
    "condition + modifier": 1.0,
    "buying term": 0.9,
    "condition": 0.8,
    "sizing": 0.7,
    "bare product noun": 0.5,
    "ambiguous": 0.3,
    "informational": 0.1,
    "navigational": 0.0,
}

# A modifier is a selection criterion: it says the reader is comparing
# options rather than asking what the thing is.
MODIFIER_WORDS = {
    "best", "top", "review", "reviews", "vs", "versus", "compare",
    "comparison", "cheap", "cheapest", "affordable", "premium", "luxury",
    "quality", "recommended", "good", "strongest", "lightest", "sturdiest",
    "most", "rated", "ranked", "buy", "where to buy", "for sale", "near me",
}


def _has_product_noun(text: str) -> bool:
    return any(re.search(rf"\b{re.escape(noun)}\b", text) for noun in PRODUCT_NOUNS)


def classify(query: str) -> Tuple[str, str, float]:
    """Return (label, reason, weight). Reason names the rule that fired."""
    text = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not text:
        return AMBIGUOUS, "empty", WEIGHTS["ambiguous"]

    # Navigational: the query is only the brand.
    stripped = text.replace(".com", "").replace("www ", "").strip()
    if stripped in BRAND_TOKENS:
        return NAVIGATIONAL, "brand only", WEIGHTS["navigational"]
    is_brand = any(token in text for token in BRAND_TOKENS)

    has_noun = _has_product_noun(text)
    words = set(re.findall(r"[a-z']+", text))

    condition_hits = sorted(c for c in CONDITION_WORDS if re.search(rf"\b{re.escape(c)}\b", text))
    modifier_hits = sorted(m for m in MODIFIER_WORDS if re.search(rf"\b{re.escape(m)}\b", text))
    buying_hits = sorted(w for w in COMMERCIAL_WORDS if re.search(rf"\b{re.escape(w)}\b", text))

    # Top tier: a use case AND a selection criterion, against a product.
    # "best walking cane for arthritis" - decided to buy, choosing between.
    if condition_hits and has_noun and modifier_hits:
        return (
            COMMERCIAL,
            f"condition + product noun + modifier: {condition_hits[0]} + {modifier_hits[0]}",
            WEIGHTS["condition + modifier"],
        )

    # An explicit buying term. Worth more when it qualifies a product noun.
    if buying_hits:
        return COMMERCIAL, f"buying term: {buying_hits[0]}", WEIGHTS["buying term"]
    if PRICE_RE.search(text):
        return COMMERCIAL, "buying term: price token", WEIGHTS["buying term"]
    if is_brand and has_noun:
        return COMMERCIAL, "buying term: brand + product noun", WEIGHTS["buying term"]

    # A use case against a product, with no selection criterion.
    if condition_hits and has_noun:
        return COMMERCIAL, f"condition + product noun: {condition_hits[0]}", WEIGHTS["condition"]

    if SIZE_RE.search(text) and has_noun:
        return COMMERCIAL, "sizing question about a product", WEIGHTS["sizing"]

    # Informational signals.
    if text.startswith(INFORMATIONAL_PREFIXES):
        return INFORMATIONAL, "informational prefix", WEIGHTS["informational"]
    info_hits = sorted(words & INFORMATIONAL_WORDS)
    if info_hits:
        return INFORMATIONAL, f"informational term: {info_hits[0]}", WEIGHTS["informational"]

    if is_brand:
        return NAVIGATIONAL, "brand mention", WEIGHTS["navigational"]

    # A bare product noun ("walking canes") is shopping behaviour, but it is
    # weaker than an explicit buying term, so it is called out separately.
    if has_noun:
        return COMMERCIAL, "bare product noun", WEIGHTS["bare product noun"]

    return AMBIGUOUS, "no rule matched", WEIGHTS["ambiguous"]


def classify_all(queries: List[str]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for query in queries:
        label, reason, weight = classify(query)
        out[query] = {"label": label, "reason": reason, "weight": weight}
    return out
