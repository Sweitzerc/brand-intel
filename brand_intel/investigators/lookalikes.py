"""
Name variants and lookalike domains.

Inspired by bellingcat/name-variant-search and alias-generator, which
expand a person's name into the spellings people actually use. For a
brand that means: spacing/hyphen variants to search for mentions, and
typo / homoglyph domains that copycats or scammers might register.
"""

import re
import socket
from concurrent.futures import ThreadPoolExecutor

HOMOGLYPHS = {"o": "0", "l": "1", "i": "1", "e": "3", "a": "4", "s": "5", "g": "9"}
QWERTY_NEIGHBORS = {
    "a": "qs", "b": "vn", "c": "xv", "d": "sf", "e": "wr", "f": "dg", "g": "fh",
    "h": "gj", "i": "uo", "j": "hk", "k": "jl", "l": "k", "m": "n", "n": "bm",
    "o": "ip", "p": "o", "q": "w", "r": "et", "s": "ad", "t": "ry", "u": "yi",
    "v": "cb", "w": "qe", "x": "zc", "y": "tu", "z": "x",
}


def search_variants(brand: str) -> list[str]:
    """Ways people write the brand name in posts, hashtags and handles."""
    words = brand.split()
    joined = "".join(words)
    out = [
        brand, brand.lower(), joined, joined.lower(), "-".join(words).lower(),
        "_".join(words).lower(), "#" + joined.lower(), "@" + joined.lower(),
    ]
    # Singular/plural slips: "Canes Galore" is often written "Cane Galore".
    for i, w in enumerate(words):
        alt = w[:-1] if w.lower().endswith("s") else w + "s"
        out.append(" ".join(words[:i] + [alt] + words[i + 1:]))
    out.append(brand + "'s")
    return list(dict.fromkeys(out))


def typo_labels(name: str) -> dict[str, str]:
    """Typosquat candidates for a bare domain label -> technique that made it."""
    name = re.sub(r"[^a-z0-9-]", "", name.lower())
    found: dict[str, str] = {}

    def add(label, how):
        if label and label != name and label not in found:
            found[label] = how

    for i in range(len(name)):
        add(name[:i] + name[i + 1:], "missing letter")
        add(name[:i] + name[i] + name[i:], "doubled letter")
        if i < len(name) - 1:
            add(name[:i] + name[i + 1] + name[i] + name[i + 2:], "swapped letters")
        for n in QWERTY_NEIGHBORS.get(name[i], ""):
            add(name[:i] + n + name[i + 1:], "keyboard typo")
        if name[i] in HOMOGLYPHS:
            add(name[:i] + HOMOGLYPHS[name[i]] + name[i + 1:], "lookalike character")
    for affix in ("shop", "store", "official", "outlet", "usa", "sale"):
        add(name + affix, "added word")
        add(name + "-" + affix, "added word")
    return found


def candidate_domains(domain: str, tlds: list[str]) -> list[tuple[str, str]]:
    label, _, own_tld = domain.lower().partition(".")
    pairs = [(f"{label}.{t}", "other TLD") for t in tlds if t != own_tld]
    pairs += [(f"{lbl}.{own_tld or 'com'}", how) for lbl, how in typo_labels(label).items()]
    return pairs


def resolves(domain: str) -> str:
    try:
        return socket.gethostbyname(domain)
    except OSError:
        return ""


def check_domains(domain: str, tlds: list[str], workers: int = 32):
    """Return rows of (domain, technique, IP or '') - registered ones first."""
    pairs = candidate_domains(domain, tlds)
    with ThreadPoolExecutor(workers) as pool:
        ips = list(pool.map(resolves, [d for d, _ in pairs]))
    rows = [(d, how, ip) for (d, how), ip in zip(pairs, ips)]
    return sorted(rows, key=lambda r: (r[2] == "", r[0]))
