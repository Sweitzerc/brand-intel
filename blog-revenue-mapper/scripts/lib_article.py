"""Extract the product-path signals from a blog article's body HTML.

`has_product_link` in the spec turned out to be too blunt for the real
articles. Three different things all look like "a product link":

  cg-shop-block   The product module this project inserts. An unambiguous,
                  machine-written marker. This is the real has-a-block signal.
  /products/...   A direct link to a product page. A genuine path to buy.
  /collections/.. A link to a collection. A path to buy, one step longer.
  /search?q=...   A link to a site search. This is NOT a product path — it
                  drops the reader on a results page. Several articles are
                  full of these and would otherwise score as "already linked".

So the extractor reports all four separately and `score.py` decides.
"""

from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from typing import Dict, List

SHOP_BLOCK_MARKER = "cg-shop-block"

HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
PARA_RE = re.compile(r"<p[\s>]", re.I)
TAG_RE = re.compile(r"<[^>]+>")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def _path_of(href: str) -> str:
    """Reduce an href to a site-relative path, lowercased."""
    href = href.strip()
    href = re.sub(r"^https?://(www\.)?canesgalore\.com", "", href, flags=re.I)
    return href.lower()


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def visible_text(html: str) -> str:
    parser = _Text()
    try:
        parser.feed(html)
    except Exception:
        return TAG_RE.sub(" ", html)
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def extract(handle: str, html: str) -> Dict:
    """Return the per-article signals that score.py consumes."""
    hrefs = [_path_of(h) for h in HREF_RE.findall(html)]

    product_links = sorted({h for h in hrefs if h.startswith("/products/")})
    collection_links = sorted({h for h in hrefs if h.startswith("/collections/")})
    search_links = sorted({h for h in hrefs if h.startswith("/search")})

    body_only = COMMENT_RE.sub(" ", html)
    text = visible_text(body_only)

    return {
        "handle": handle,
        "has_shop_block": SHOP_BLOCK_MARKER in html,
        "product_links": product_links,
        "product_link_count": len(product_links),
        "collection_links": collection_links,
        "collection_link_count": len(collection_links),
        "search_link_count": len(search_links),
        "paragraph_count": len(PARA_RE.findall(html)),
        "word_count": len(text.split()),
        "bytes": len(html),
    }


def signals_path(data_dir: str, handle: str) -> str:
    return os.path.join(data_dir, "articles", f"{handle}.signals.json")


def html_path(data_dir: str, handle: str) -> str:
    return os.path.join(data_dir, "articles", f"{handle}.html")


def extract_to_disk(data_dir: str, handle: str) -> Dict:
    """Read the cached HTML for one article and write its signals JSON."""
    src = html_path(data_dir, handle)
    with open(src, encoding="utf-8") as fh:
        html = fh.read()
    signals = extract(handle, html)
    dest = signals_path(data_dir, handle)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(signals, fh, indent=2, sort_keys=True)
    return signals


def load_signals(data_dir: str) -> Dict[str, Dict]:
    folder = os.path.join(data_dir, "articles")
    out: Dict[str, Dict] = {}
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".signals.json"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            record = json.load(fh)
        out[record["handle"]] = record
    return out


if __name__ == "__main__":
    import sys

    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    for arg in sys.argv[1:]:
        handle = arg[:-5] if arg.endswith(".html") else arg
        print(json.dumps(extract_to_disk(data_dir, handle), sort_keys=True))
