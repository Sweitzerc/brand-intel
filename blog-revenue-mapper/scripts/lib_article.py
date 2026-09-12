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
  /collections    The bare collection INDEX, with no handle. Also not a
                  product path: it is a list of lists. Counted separately as
                  an index link so it is visible rather than silently
                  dropped, which is what used to happen.

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


# The articles were hand-edited over several years and link to the store in
# every shape: with and without a scheme, with and without www, and with at
# least one typo'd domain (canegalore.com). All of them are internal links.
SITE_HOST_RE = re.compile(
    r"^(?:https?:)?/{0,2}(?:www\.)?canes?galore\.com",
    re.I,
)


def _path_of(href: str) -> str:
    """Reduce an href to a site-relative path, lowercased."""
    href = href.strip()
    stripped = SITE_HOST_RE.sub("", href)
    if stripped != href and not stripped.startswith("/"):
        stripped = "/" + stripped
    return stripped.lower()


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

    def _bare(path: str, root: str) -> bool:
        """True for the index itself (/collections), not a member (/x/y)."""
        stem = path.split("?")[0].rstrip("/")
        return stem == root

    product_links = sorted({h for h in hrefs if h.startswith("/products/")})
    collection_links = sorted({h for h in hrefs if h.startswith("/collections/")})
    search_links = sorted({h for h in hrefs if h.startswith("/search")})
    index_links = sorted({
        h for h in hrefs
        if _bare(h, "/collections") or _bare(h, "/products")
    })

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
        "index_links": index_links,
        "index_link_count": len(index_links),
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
