#!/usr/bin/env python3
"""Stage 2 - Score. Read-only. Produces out/candidates.csv.

For every blog URL with traffic, joins GA landing-page behaviour to GSC
search performance to the article's own link structure, and ranks the posts
where real buying-intent traffic currently has no path to a product.

    python3 scripts/score.py
    python3 scripts/score.py --top 20 --show

Output columns are suffixed _28d, not _90d. The reports sheet is a trailing
28-day window with a 3-day GSC lag; see config.yaml: window.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from typing import Dict, List, Optional
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lib_article  # noqa: E402
import lib_attribute  # noqa: E402
import lib_intent  # noqa: E402
import lib_sheet  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# Approximate organic click-through rate by rank position. Used to discount a
# query's impressions when weighting intent: a query the page ranks 6th for
# says far more about what that page is for than one it ranks 40th for, even
# when the 40th has more impressions. Weighting by impressions alone lets a
# huge, barely-ranking head term drown out the queries that actually describe
# the page.
POSITION_CTR = {
    1: 0.280, 2: 0.150, 3: 0.110, 4: 0.080, 5: 0.060,
    6: 0.050, 7: 0.040, 8: 0.033, 9: 0.028, 10: 0.025,
}
POSITION_CTR_FLOOR = 0.002


def position_weight(position: float) -> float:
    """Expected CTR at this rank. Beyond position 10 it decays smoothly."""
    if position is None or position <= 0:
        # No position reported - treat as mid-page rather than discarding it.
        return POSITION_CTR[10]
    rank = int(round(position))
    if rank in POSITION_CTR:
        return POSITION_CTR[rank]
    if rank < 1:
        return POSITION_CTR[1]
    return max(POSITION_CTR[10] * (10.0 / rank), POSITION_CTR_FLOOR)


def load_config() -> dict:
    import yaml  # type: ignore

    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def blog_handle_from(value: str, prefix: str) -> Optional[str]:
    """Pull the article handle out of a GA path or a GSC absolute URL.

    GA_Landing gives paths ("/blogs/news/x"); GSC_Pages gives absolute URLs
    ("https://www.canesgalore.com/blogs/news/x"). Both reduce to the handle.
    """
    if not value:
        return None
    parsed = urlparse(value.strip())
    path = parsed.path or ""
    if not path.startswith(prefix):
        return None
    return path[len(prefix):].strip("/") or None


class HandleResolver:
    """Map a possibly-truncated handle onto the real article handle.

    GA4 truncates long page paths, so `/blogs/news/the-ultimate-blackthorn-
    walking-stick-guide-history-benefits-and-choosing-your-perf` is the same
    page as the article whose handle ends `...your-perfect-stick`. Exact match
    wins; otherwise a prefix match is accepted only when it is unambiguous.
    """

    def __init__(self, handles: List[str]) -> None:
        self.handles = sorted(set(handles))
        self.exact = set(self.handles)
        self.unresolved: List[str] = []
        self.ambiguous: List[str] = []

    def resolve(self, handle: str) -> Optional[str]:
        if not handle:
            return None
        if handle in self.exact:
            return handle
        matches = [h for h in self.handles if h.startswith(handle)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            self.ambiguous.append(handle)
            return None
        self.unresolved.append(handle)
        return None


def window_end(config: dict) -> str:
    """Last day of the reporting window, preferring the Scorecard tab."""
    scorecard = lib_sheet.read_tab(os.path.join(DATA, "tabs", "scorecard.csv"))
    period = next((r.get("this_period", "") for r in scorecard if r.get("metric") == "period"), "")
    match = re.findall(r"\d{4}-\d{2}-\d{2}", period)
    if len(match) >= 2:
        return match[-1]
    return str(config["window"].get("ends", ""))


def load_query_page(prefix: str, resolve) -> Optional[Dict[str, List[dict]]]:
    """The real query-by-page join, when scripts/lib_gsc.py has produced it.

    When this file exists there is no estimation: every query row names the
    page it appeared on. Falling back to lib_attribute is only for when it
    does not.
    """
    path = os.path.join(DATA, "tabs", "gsc_query_page.csv")
    rows = lib_sheet.read_tab(path)
    if not rows:
        return None
    by_page: Dict[str, List[dict]] = {}
    for record in rows:
        handle = resolve(blog_handle_from(record.get("page", ""), prefix))
        if not handle:
            continue
        by_page.setdefault(handle, []).append(record)
    return by_page


def load_shopify_sessions(window: str, resolve, prefix: str) -> Dict[str, dict]:
    """Shopify's own session funnel by landing page, if collected."""
    path = os.path.join(DATA, "tabs", f"shopify_sessions_{window}.csv")
    out: Dict[str, dict] = {}
    for record in lib_sheet.read_tab(path):
        handle = resolve(blog_handle_from(record.get("landing_page_path", ""), prefix))
        if not handle:
            continue
        out[handle] = {
            "sessions": lib_sheet.num(record.get("sessions")),
            "add_to_carts": lib_sheet.num(record.get("sessions_with_cart_additions")),
            "reached_checkout": lib_sheet.num(record.get("sessions_that_reached_checkout")),
            "completed_checkout": lib_sheet.num(record.get("sessions_that_completed_checkout")),
        }
    return out


def load_article_index() -> Dict[str, dict]:
    path = os.path.join(DATA, "articles_index.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {row["handle"]: row for row in json.load(fh)}


def striking_distance(record: dict, low: float, high: float) -> bool:
    """Reuse the sheet's own bucket; fall back to the position range."""
    bucket = (record.get("bucket") or "").strip().lower()
    if bucket:
        return bucket == "striking_distance"
    position = lib_sheet.num(record.get("position"))
    return low <= position <= high


def build_rows(config: dict) -> Dict[str, object]:
    prefix = config["store"]["blog_path_prefix"]
    window = config["window"]["label"]
    scoring = config["scoring"]

    ga = lib_sheet.read_tab(os.path.join(DATA, "tabs", "ga_landing.csv"))
    pages = lib_sheet.read_tab(os.path.join(DATA, "tabs", "gsc_pages.csv"))
    queries = lib_sheet.read_tab(os.path.join(DATA, "tabs", "gsc_queries.csv"))
    signals = lib_article.load_signals(DATA)
    index = load_article_index()

    known = list(index) or list(signals)
    resolver = HandleResolver(known) if known else None

    def canonical(handle: Optional[str]) -> Optional[str]:
        if handle is None:
            return None
        return resolver.resolve(handle) if resolver else handle

    rows: Dict[str, dict] = {}

    # GA landing-page behaviour: sessions, add-to-carts, revenue.
    for record in ga:
        handle = canonical(blog_handle_from(record.get("landing_page", ""), prefix))
        if not handle:
            continue
        row = rows.setdefault(handle, {"handle": handle})
        row.update(
            {
                "sessions": lib_sheet.num(record.get("sessions")),
                "add_to_carts": lib_sheet.num(record.get("add_to_carts")),
                "purchases": lib_sheet.num(record.get("purchases")),
                "revenue": lib_sheet.num(record.get("revenue")),
                "atc_rate": lib_sheet.num(record.get("atc_rate")),
                "prev_sessions": lib_sheet.num(record.get("prev_sessions")),
                "sessions_change_pct": lib_sheet.num(record.get("sessions_change_pct")),
            }
        )

    # Shopify landing pages. The 90-day pull covers blog URLs that never
    # appear in the 28-day GA export, so it seeds rows of its own rather than
    # only decorating rows GA already found.
    for window_label in ("90d", "28d"):
        path = os.path.join(DATA, "tabs", f"shopify_sessions_{window_label}.csv")
        for record in lib_sheet.read_tab(path):
            handle = canonical(blog_handle_from(record.get("landing_page_path", ""), prefix))
            if handle:
                rows.setdefault(handle, {"handle": handle})

    # GSC page-level search performance.
    for record in pages:
        handle = canonical(blog_handle_from(record.get("page", ""), prefix))
        if not handle:
            continue
        row = rows.setdefault(handle, {"handle": handle})
        row.update(
            {
                "clicks": lib_sheet.num(record.get("clicks")),
                "impressions": lib_sheet.num(record.get("impressions")),
                "avg_position": lib_sheet.num(record.get("position")),
                "ctr": lib_sheet.num(record.get("ctr")),
            }
        )

    # Attribute queries to pages. See lib_attribute for why this is estimated.
    vocabulary = {
        handle: lib_attribute.page_vocabulary(handle, index.get(handle, {}).get("title", ""))
        for handle in rows
    }
    query_records = [q for q in queries if (q.get("query") or "").strip()]
    joined = load_query_page(prefix, canonical)
    if joined is not None:
        attributed = {handle: joined.get(handle, []) for handle in rows}
        join_kind = "measured"
        query_records = [
            {"query": r.get("query", ""), "impressions": r.get("impressions"),
             "clicks": r.get("clicks"), "position": r.get("position")}
            for rows_for_page in joined.values() for r in rows_for_page
        ]
    else:
        attributed = lib_attribute.attribute(query_records, vocabulary)
        join_kind = "attributed"

    # Site-level prior. When too few queries can be attributed to a page, a
    # measured score of 0.0 would say "this page has no buying intent", which
    # the data does not support - it says we could not tell. The prior is the
    # impression-weighted mean intent across every query the site ranks for.
    prior_weight = prior_impressions = 0.0
    for record in query_records:
        _, _, weight = lib_intent.classify(record["query"])
        impressions = max(lib_sheet.num(record.get("impressions")), 1.0)
        evidence = impressions * position_weight(lib_sheet.num(record.get("position")))
        prior_weight += weight * evidence
        prior_impressions += evidence
    site_prior = round(prior_weight / prior_impressions, 3) if prior_impressions else 0.0

    low = scoring["striking_distance_min_position"]
    high = scoring["striking_distance_max_position"]
    closes = window_end(config)
    shopify = {w: load_shopify_sessions(w, canonical, prefix) for w in ("90d", "28d")}

    for handle, row in rows.items():
        hits = attributed.get(handle, [])
        weight_total = 0.0
        impression_total = 0.0
        scored = []
        for hit in hits:
            label, reason, weight = lib_intent.classify(hit["query"])
            impressions = max(lib_sheet.num(hit.get("impressions")), 1.0)
            position = lib_sheet.num(hit.get("position"))
            # Weight = impressions discounted by rank. Expected clicks, near
            # enough, which is the right measure of how much this query tells
            # us about the page.
            evidence = impressions * position_weight(position)
            weight_total += weight * evidence
            impression_total += evidence
            scored.append(
                {
                    "query": hit["query"],
                    "label": label,
                    "reason": reason,
                    "weight": weight,
                    "impressions": impressions,
                    "position": position,
                    "evidence": evidence,
                    "clicks": lib_sheet.num(hit.get("clicks")),
                    "striking": striking_distance(hit, low, high),
                }
            )
        scored.sort(key=lambda item: -item["evidence"])

        row["intent_query_support"] = len(scored)
        measured = round(weight_total / impression_total, 3) if impression_total else None
        if measured is not None and len(scored) >= scoring["min_intent_query_support"]:
            row["buying_intent_score"] = measured
            row["intent_source"] = join_kind
        else:
            row["buying_intent_score"] = site_prior
            row["intent_source"] = "site_prior"
        row["measured_intent_score"] = measured if measured is not None else ""
        row["striking_distance_queries"] = sum(1 for item in scored if item["striking"])
        row["top_queries"] = [item["query"] for item in scored[:5]]
        row["query_impressions"] = sum(item["impressions"] for item in scored)
        row["top_query_intent"] = max((item["weight"] for item in scored), default=0.0)

        signal = signals.get(handle, {})
        row["has_shop_block"] = bool(signal.get("has_shop_block"))
        row["product_link_count"] = int(signal.get("product_link_count", 0))
        row["collection_link_count"] = int(signal.get("collection_link_count", 0))
        row["search_link_count"] = int(signal.get("search_link_count", 0))
        row["paragraph_count"] = int(signal.get("paragraph_count", 0))
        row["word_count"] = int(signal.get("word_count", 0))
        row["signals_cached"] = handle in signals

        # A link to /search?q= is not a path to a product; it is a path to a
        # results page, and never counts.
        row["has_product_link"] = bool(
            row["has_shop_block"]
            or row["product_link_count"] > 0
            or row["collection_link_count"] > 0
        )
        # What the candidate filter treats as "already handled". See
        # config.yaml: scoring.existing_path_signal.
        if scoring.get("existing_path_signal", "shop_block") == "shop_block":
            row["has_existing_path"] = row["has_shop_block"]
        else:
            row["has_existing_path"] = row["has_product_link"]

        article = index.get(handle, {})
        row["title"] = article.get("title", "")
        row["is_published"] = article.get("isPublished")
        row["updated_at"] = (article.get("updatedAt") or "")[:10]
        # An article edited after the window closed is NOT measured by these
        # numbers. Seven posts received their shop block days after this
        # window ended, so their add-to-cart figures describe the page as it
        # was BEFORE the block existed. Reading them as block performance is
        # the single easiest mistake to make with this file.
        row["edited_after_window"] = bool(
            row["updated_at"] and closes and row["updated_at"] > closes
        )
        row["url"] = f"https://{config['store']['domain']}{prefix}{handle}"

        for key in ("sessions", "add_to_carts", "purchases", "revenue", "clicks",
                    "impressions", "avg_position", "prev_sessions"):
            row.setdefault(key, 0.0)

        for window_label in ("90d", "28d"):
            funnel = shopify[window_label].get(handle, {})
            for metric, value in funnel.items():
                row[f"{metric}_{window_label}"] = value
            for metric in ("sessions", "add_to_carts", "reached_checkout", "completed_checkout"):
                row.setdefault(f"{metric}_{window_label}", "")

        # Rank on the widest window we actually have.
        wide = row.get("sessions_90d")
        row["ranking_window"] = "90d" if isinstance(wide, float) and wide > 0 else "28d"
        row["ranking_sessions"] = wide if row["ranking_window"] == "90d" else row["sessions"]
        row["opportunity_score"] = round(row["ranking_sessions"] * row["buying_intent_score"], 1)
        row["well_supported"] = row["intent_source"] != "site_prior"

    return {
        "rows": rows,
        "site_prior": site_prior,
        "join_kind": join_kind,
        "resolver": resolver,
        "attributed": attributed,
        "query_count": len(query_records),
    }


def baseline(config: dict) -> dict:
    """Phase 0 must leave Phase 1 something to beat. See spec section 6."""
    ga = lib_sheet.read_tab(os.path.join(DATA, "tabs", "ga_landing.csv"))
    prefix = config["store"]["blog_path_prefix"]

    blog = [r for r in ga if (r.get("landing_page") or "").startswith(prefix)]
    site_sessions = sum(lib_sheet.num(r.get("sessions")) for r in ga)
    sessions = sum(lib_sheet.num(r.get("sessions")) for r in blog)
    carts = sum(lib_sheet.num(r.get("add_to_carts")) for r in blog)
    purchases = sum(lib_sheet.num(r.get("purchases")) for r in blog)
    revenue = sum(lib_sheet.num(r.get("revenue")) for r in blog)

    scorecard = lib_sheet.read_tab(os.path.join(DATA, "tabs", "scorecard.csv"))
    period = next((r.get("this_period") for r in scorecard if r.get("metric") == "period"), "")

    return {
        "period": period,
        "window_days": config["window"]["days"],
        "blog_landing_pages": len(blog),
        "blog_sessions": sessions,
        "blog_add_to_carts": carts,
        "blog_purchases": purchases,
        "blog_revenue": revenue,
        "blog_atc_rate_pct": round(100 * carts / sessions, 3) if sessions else 0.0,
        "blog_conv_rate_pct": round(100 * purchases / sessions, 3) if sessions else 0.0,
        "blog_share_of_sessions_pct": round(100 * sessions / site_sessions, 1) if site_sessions else 0.0,
        "all_landing_sessions": site_sessions,
        "note": (
            "Primary metric for Phase 1 is blog_atc_rate_pct. Re-run score.py "
            "after the blocks ship and compare against this file."
        ),
    }


COLUMNS = [
    "rank", "handle", "title", "url",
    "ranking_window", "ranking_sessions",
    "sessions_90d", "add_to_carts_90d", "reached_checkout_90d", "completed_checkout_90d",
    "ga_sessions_28d", "ga_add_to_carts_28d", "purchases_28d", "revenue_28d",
    "clicks_28d", "impressions_28d", "avg_position",
    "buying_intent_score", "intent_source", "measured_intent_score",
    "intent_query_support", "well_supported", "top_query_intent",
    "striking_distance_queries", "opportunity_score",
    "has_product_link", "has_shop_block", "has_existing_path",
    "updated_at", "edited_after_window",
    "product_link_count", "collection_link_count", "search_link_count",
    "paragraph_count", "word_count", "is_published", "signals_cached",
    "top_queries",
]


def to_output(row: dict, rank: int) -> dict:
    return {
        "rank": rank,
        "handle": row["handle"],
        "title": row.get("title", ""),
        "url": row["url"],
        "ranking_window": row["ranking_window"],
        "ranking_sessions": int(row["ranking_sessions"]),
        "sessions_90d": row.get("sessions_90d", ""),
        "add_to_carts_90d": row.get("add_to_carts_90d", ""),
        "reached_checkout_90d": row.get("reached_checkout_90d", ""),
        "completed_checkout_90d": row.get("completed_checkout_90d", ""),
        "ga_sessions_28d": int(row["sessions"]),
        "ga_add_to_carts_28d": int(row["add_to_carts"]),
        "purchases_28d": int(row["purchases"]),
        "revenue_28d": row["revenue"],
        "clicks_28d": int(row["clicks"]),
        "impressions_28d": int(row["impressions"]),
        "avg_position": row["avg_position"],
        "buying_intent_score": row["buying_intent_score"],
        "intent_source": row["intent_source"],
        "measured_intent_score": row["measured_intent_score"],
        "intent_query_support": row["intent_query_support"],
        "well_supported": row["well_supported"],
        "top_query_intent": row["top_query_intent"],
        "striking_distance_queries": row["striking_distance_queries"],
        "opportunity_score": row["opportunity_score"],
        "has_product_link": row["has_product_link"],
        "has_shop_block": row["has_shop_block"],
        "has_existing_path": row["has_existing_path"],
        "updated_at": row["updated_at"],
        "edited_after_window": row["edited_after_window"],
        "product_link_count": row["product_link_count"],
        "collection_link_count": row["collection_link_count"],
        "search_link_count": row["search_link_count"],
        "paragraph_count": row["paragraph_count"],
        "word_count": row["word_count"],
        "is_published": row.get("is_published"),
        "signals_cached": row["signals_cached"],
        "top_queries": " | ".join(row.get("top_queries", [])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2 - score. Read-only.")
    parser.add_argument("--top", type=int, default=None, help="how many rows to print")
    parser.add_argument("--show", action="store_true", help="print the ranked table")
    parser.add_argument("--all", action="store_true",
                        help="write every blog URL, not just the unlinked ones")
    args = parser.parse_args()

    config = load_config()
    built = build_rows(config)
    rows: Dict[str, dict] = built["rows"]
    resolver: HandleResolver = built["resolver"]

    if not rows:
        raise SystemExit("no blog rows found - run collect.py first")

    min_sessions = config["scoring"]["min_sessions"]
    candidates = [
        row for row in rows.values()
        if max(row["sessions"], row["ranking_sessions"]) >= min_sessions
        and (args.all or not row["has_existing_path"])
    ]
    candidates.sort(key=lambda row: (-row["opportunity_score"], -row["ranking_sessions"]))

    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    out_path = os.path.join(ROOT, config["output"]["candidates_csv"])
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for rank, row in enumerate(candidates, start=1):
            writer.writerow(to_output(row, rank))

    edited = sorted(
        (h for h, r in rows.items() if r["edited_after_window"] and r["sessions"] >= min_sessions),
        key=lambda h: -rows[h]["sessions"],
    )
    base = baseline(config)
    base["window_ends"] = window_end(config)
    base["articles_edited_after_window"] = edited
    base["sessions_on_articles_edited_after_window"] = sum(rows[h]["sessions"] for h in edited)
    with open(os.path.join(ROOT, config["output"]["baseline_json"]), "w", encoding="utf-8") as fh:
        json.dump(base, fh, indent=2)

    # Only warn about pages above the traffic threshold. collect.py caches
    # every article, but a partial cache is normal while it is running, and
    # the long tail below min_sessions never reaches candidates.csv anyway.
    missing_signals = sorted(
        h for h, r in rows.items()
        if not r["signals_cached"] and max(r["sessions"], r["ranking_sessions"]) >= min_sessions
    )

    windows = {row["ranking_window"] for row in candidates}
    print(f"query->page join      {built['join_kind']}"
          f"{'  (estimated - see lib_attribute.py)' if built['join_kind'] == 'attributed' else ''}")
    print(f"ranking window        {'/'.join(sorted(windows)) or 'n/a'}")
    print(f"site intent prior     {built['site_prior']}")
    print(f"blog URLs scored      {len(rows)}")
    print(f"candidates written    {len(candidates)}  -> {config['output']['candidates_csv']}")
    print(f"blog sessions ({config['window']['label']})   {base['blog_sessions']:.0f}"
          f"  ATC {base['blog_add_to_carts']:.0f}"
          f"  rate {base['blog_atc_rate_pct']}%"
          f"  revenue ${base['blog_revenue']:.2f}")
    if missing_signals:
        print(f"WARNING no cached article HTML for {len(missing_signals)} URLs "
              f"above {min_sessions} sessions - has_product_link is unknown "
              f"for these, so they may be listed as candidates in error:")
        for handle in missing_signals:
            print(f"MISSING_HTML\t{handle}")
    if edited:
        touched = sum(rows[h]["sessions"] for h in edited)
        print()
        print(f"NOTE  {len(edited)} scored articles were edited after the window "
              f"closed ({base['window_ends']}), covering {touched:.0f} sessions "
              f"({100 * touched / base['blog_sessions']:.0f}% of blog traffic).")
        print("      Their add-to-cart figures describe the page BEFORE that edit.")
        print("      They are a clean pre-treatment baseline, not a measured result.")
        print()
    if resolver and resolver.unresolved:
        print(f"WARNING {len(resolver.unresolved)} paths did not match any article handle")
    if resolver and resolver.ambiguous:
        print(f"WARNING {len(resolver.ambiguous)} truncated paths matched more than one article")

    if args.show or args.top:
        limit = args.top or config["scoring"]["top_n"]
        print()
        header = (f"{'#':>2}  {'sess':>5} {'intent':>7} {'sup':>4} {'sd':>3} "
                  f"{'opp':>7}  {'link':>4}  handle    (~ = intent from site prior)")
        print(header)
        print("-" * len(header))
        for rank, row in enumerate(candidates[:limit], start=1):
            mark = " " if row["well_supported"] else "~"
            print(f"{rank:>2}  {row['ranking_sessions']:>5.0f} {row['buying_intent_score']:>6.2f}{mark}"
                  f"{row['intent_query_support']:>4} {row['striking_distance_queries']:>3} "
                  f"{row['opportunity_score']:>7.1f}  "
                  f"{'yes' if row['has_product_link'] else 'NO':>4}  {row['handle'][:62]}")


if __name__ == "__main__":
    main()
