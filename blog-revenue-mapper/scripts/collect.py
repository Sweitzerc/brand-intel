#!/usr/bin/env python3
"""Stage 1 - Collect. Read-only. Writes nothing to the store.

Pulls the four report tabs, the product catalog and every blog article's body
HTML into data/, then stops. Everything is cached; a re-run reuses what is on
disk unless --refresh is passed.

    python3 scripts/collect.py                    # cached, no refetch
    python3 scripts/collect.py --refresh          # refetch everything
    python3 scripts/collect.py --source export    # parse a saved sheet export
    python3 scripts/collect.py --skip-products    # tabs + articles only

Credentials, all read-only:
    GOOGLE_APPLICATION_CREDENTIALS  service account key, if one exists. None is
                                    known to; prefer --source export.
    SHOPIFY_STORE_DOMAIN            e.g. canes-galore.myshopify.com
    SHOPIFY_ADMIN_TOKEN             Admin API access token
    SHOPIFY_API_VERSION             optional, defaults below
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lib_article  # noqa: E402
import lib_sheet  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DEFAULT_API_VERSION = "2025-07"

PRODUCTS_QUERY = """
query Products($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      handle
      title
      status
      productType
      tags
      totalInventory
      publishedAt
      priceRangeV2 { minVariantPrice { amount currencyCode } }
      collections(first: 20) { nodes { handle title } }
      variants(first: 50) {
        nodes {
          id
          sku
          price
          inventoryQuantity
          inventoryItem {
            inventoryLevels(first: 10) {
              nodes { quantities(names: ["available"]) { name quantity } location { name } }
            }
          }
        }
      }
    }
  }
}
"""

ARTICLES_QUERY = """
query Articles($first: Int!, $after: String) {
  articles(first: $first, after: $after, sortKey: PUBLISHED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      handle
      title
      isPublished
      publishedAt
      updatedAt
      tags
      body
      blog { handle }
    }
  }
}
"""


def load_config() -> dict:
    import yaml  # type: ignore

    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------
# Shopify Admin API (read-only)
# --------------------------------------------------------------------------
def shopify_graphql(query: str, variables: dict) -> dict:
    import requests  # type: ignore

    domain = os.environ.get("SHOPIFY_STORE_DOMAIN")
    token = os.environ.get("SHOPIFY_ADMIN_TOKEN")
    if not domain or not token:
        raise RuntimeError(
            "SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_TOKEN must be set to pull "
            "products or articles. Use --skip-products / --skip-articles to "
            "run on the cache alone."
        )
    version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
    url = f"https://{domain}/admin/api/{version}/graphql.json"

    for attempt in range(5):
        response = requests.post(
            url,
            headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
            json={"query": query, "variables": variables},
            timeout=60,
        )
        if response.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            # Throttled errors are worth retrying; everything else is a bug.
            text = json.dumps(payload["errors"])
            if "THROTTLED" in text.upper() and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Shopify GraphQL error: {text}")
        return payload["data"]
    raise RuntimeError("Shopify GraphQL kept throttling after 5 attempts")


def paginate(query: str, root: str, page_size: int = 50) -> List[dict]:
    nodes: List[dict] = []
    cursor = None
    while True:
        data = shopify_graphql(query, {"first": page_size, "after": cursor})
        block = data[root]
        nodes.extend(block["nodes"])
        if not block["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = block["pageInfo"]["endCursor"]


def flatten_product(node: dict, sellable_location: str, ignore: List[str]) -> dict:
    """Reduce a product to the fields stages 3-4 actually check.

    Inventory is counted only at the sellable location. "Missouri Returns" is
    a returns address, not stock, and counting it would pass dead products
    through the stage 4 inventory check.
    """
    variants = node.get("variants", {}).get("nodes", [])
    sellable = 0
    for variant in variants:
        levels = (
            variant.get("inventoryItem", {})
            .get("inventoryLevels", {})
            .get("nodes", [])
        )
        for level in levels:
            name = (level.get("location") or {}).get("name", "")
            if name in ignore or name != sellable_location:
                continue
            for quantity in level.get("quantities") or []:
                if quantity.get("name") == "available":
                    sellable += int(quantity.get("quantity") or 0)

    price_block = (node.get("priceRangeV2") or {}).get("minVariantPrice") or {}
    return {
        "gid": node["id"],
        "handle": node["handle"],
        "title": node["title"],
        "status": node.get("status"),
        "publishedAt": node.get("publishedAt"),
        "price": price_block.get("amount"),
        "currency": price_block.get("currencyCode"),
        "totalInventory": node.get("totalInventory"),
        "inventoryQuantity": sellable,
        "locationName": sellable_location,
        "productType": node.get("productType"),
        "tags": node.get("tags", []),
        "collections": [c["handle"] for c in node.get("collections", {}).get("nodes", [])],
        "skus": [v.get("sku") for v in variants],
        "variant_count": len(variants),
    }


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------
def collect_tabs(config: dict, source: str, refresh: bool) -> None:
    target = os.path.join(DATA, "tabs")
    existing = [f for f in os.listdir(target) if f.endswith(".csv")] if os.path.isdir(target) else []
    if existing and not refresh:
        print(f"tabs      cached ({len(existing)} files)")
        return

    if source == "export":
        path = os.path.join(DATA, "raw", "sheet_export.md")
        if not os.path.exists(path):
            raise SystemExit(f"missing {path} - export the sheet there, or use --source api")
        tabs = lib_sheet.read_export(path)
    else:
        tabs = lib_sheet.read_api(config["sheet"]["id"], config["sheet"]["tabs"])

    missing = set(lib_sheet.TAB_SIGNATURES) - set(tabs)
    if missing:
        raise SystemExit(f"could not locate tabs: {sorted(missing)}")

    for name, rows in tabs.items():
        lib_sheet.write_tab(rows, os.path.join(target, f"{name}.csv"))
        print(f"tabs      {name}: {len(rows)} rows")


def collect_products(config: dict, refresh: bool) -> None:
    path = os.path.join(DATA, "products.json")
    if os.path.exists(path) and not refresh:
        print(f"products  cached ({len(json.load(open(path)))} products)")
        return
    nodes = paginate(PRODUCTS_QUERY, "products")
    store = config["store"]
    products = [
        flatten_product(node, store["sellable_location"], store.get("ignore_locations", []))
        for node in nodes
    ]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(products, fh, indent=2)
    print(f"products  {len(products)} written")


def collect_articles(config: dict, refresh: bool) -> None:
    target = os.path.join(DATA, "articles")
    os.makedirs(target, exist_ok=True)
    cached = [f for f in os.listdir(target) if f.endswith(".html")]
    if cached and not refresh:
        print(f"articles  cached ({len(cached)} files)")
        rebuild_signals()
        return

    blog_handle = config["store"]["blog_handle"]
    nodes = paginate(ARTICLES_QUERY, "articles")
    index = []
    for node in nodes:
        if node.get("blog", {}).get("handle") != blog_handle:
            continue
        handle = node["handle"]
        with open(lib_article.html_path(DATA, handle), "w", encoding="utf-8") as fh:
            fh.write(node.get("body") or "")
        index.append(
            {
                "gid": node["id"],
                "handle": handle,
                "title": node["title"],
                "isPublished": node.get("isPublished"),
                "publishedAt": node.get("publishedAt"),
                "updatedAt": node.get("updatedAt"),
                "tags": node.get("tags", []),
            }
        )
    with open(os.path.join(DATA, "articles_index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)
    print(f"articles  {len(index)} written")
    rebuild_signals()


SESSIONS_QUERY = (
    "FROM sessions "
    "SHOW sessions, sessions_with_cart_additions, "
    "sessions_that_reached_checkout, sessions_that_completed_checkout "
    "GROUP BY landing_page_path "
    "SINCE -{days}d UNTIL today "
    "ORDER BY sessions DESC LIMIT 1000"
)


def collect_sessions(config: dict, refresh: bool) -> None:
    """Session funnel by landing page, straight from the store.

    This is the 90-day backfill. The reports sheet only carries a trailing
    28 days because that is the range the Apps Script asks for, not because
    the history is missing. Pulling 90 days here surfaced 100 blog landing
    pages against the 38 the 28-day GA export showed.

    Shopify sessions and GA4 sessions are different measurement systems and
    will not agree. Both windows are collected so like is compared with like.
    """
    import csv as _csv

    for days, label in ((config["backfill"]["days"], "90d"), (28, "28d")):
        path = os.path.join(DATA, "tabs", f"shopify_sessions_{label}.csv")
        if os.path.exists(path) and not refresh:
            print(f"sessions  cached ({label})")
            continue
        # Field names verified against the live schema: parseErrors is a
        # plain [String!]! and the rows live in tableData.rows as JSON.
        data = shopify_graphql(
            "query Q($q: String!) { shopifyqlQuery(query: $q) { "
            "__typename parseErrors "
            "tableData { columns { name dataType } rows } } }",
            {"q": SESSIONS_QUERY.format(days=days)},
        )
        block = data["shopifyqlQuery"]
        if block.get("parseErrors"):
            raise RuntimeError(f"ShopifyQL parse error: {block['parseErrors']}")
        table = block.get("tableData") or {}
        header = [c["name"] for c in table.get("columns", [])]
        rows = table.get("rows") or []
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = _csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)
        print(f"sessions  {label}: {len(rows)} rows")


def rebuild_signals() -> None:
    """Derive the link signals from every cached article body."""
    folder = os.path.join(DATA, "articles")
    if not os.path.isdir(folder):
        return
    count = 0
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".html"):
            continue
        lib_article.extract_to_disk(DATA, name[:-5])
        count += 1
    print(f"signals   {count} extracted")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 1 - collect. Read-only.")
    parser.add_argument("--source", choices=["api", "export"], default="api",
                        help="where the sheet comes from (default: api)")
    parser.add_argument("--refresh", action="store_true", help="refetch instead of using the cache")
    parser.add_argument("--skip-products", action="store_true")
    parser.add_argument("--skip-sessions", action="store_true",
                        help="skip the ShopifyQL session backfill")
    parser.add_argument("--skip-articles", action="store_true")
    parser.add_argument("--signals-only", action="store_true",
                        help="re-derive article signals from cached HTML and exit")
    args = parser.parse_args()

    for folder in ("raw", "tabs", "articles"):
        os.makedirs(os.path.join(DATA, folder), exist_ok=True)

    if args.signals_only:
        rebuild_signals()
        return

    config = load_config()
    collect_tabs(config, args.source, args.refresh)
    if not args.skip_products:
        collect_products(config, args.refresh)
    else:
        print("products  skipped")
    if not args.skip_sessions:
        collect_sessions(config, args.refresh)
    else:
        print("sessions  skipped")
    if not args.skip_articles:
        collect_articles(config, args.refresh)
    else:
        print("articles  skipped")
        rebuild_signals()


if __name__ == "__main__":
    main()
