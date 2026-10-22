"""
Site network: which websites share the same tracking IDs?

Adapted from bellingcat/wayback-google-analytics. Sites run by the same
operator often reuse one Google Analytics / Tag Manager / ad / pixel ID.
We pull IDs from each site's live page *and* from yearly Wayback Machine
snapshots, then show which IDs link two or more domains - useful for
spotting copycat stores or a competitor's network of sites.
"""

import re
from collections import defaultdict

from ..core import TIMEOUT, http

PATTERNS = {
    "Universal Analytics": re.compile(r"\bUA-\d{4,10}-\d{1,4}\b"),
    "GA4": re.compile(r"\bG-[A-Z0-9]{8,12}\b"),
    "Tag Manager": re.compile(r"\bGTM-[A-Z0-9]{4,9}\b"),
    "Google Ads": re.compile(r"\bAW-\d{6,12}\b"),
    "Meta Pixel": re.compile(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{10,20})['\"]"),
    "AdSense": re.compile(r"\bca-pub-\d{10,20}\b"),
}


def extract_ids(html: str) -> dict[str, str]:
    """Return {tracking_id: kind} found in a page."""
    found = {}
    for kind, rx in PATTERNS.items():
        for m in rx.finditer(html):
            found[m.group(1) if rx.groups else m.group(0)] = kind
    return found


def snapshots(domain: str, per_year: bool = True, limit: int = 8) -> list[str]:
    """Wayback Machine snapshot timestamps for a domain (one per year)."""
    params = {"url": domain, "output": "json", "filter": "statuscode:200", "fl": "timestamp"}
    if per_year:
        params["collapse"] = "timestamp:4"
    resp = http().get("https://web.archive.org/cdx/search/cdx", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    rows = resp.json()[1:] if resp.text.strip() else []
    return [r[0] for r in rows][-limit:]


def fetch(url: str) -> str:
    resp = http().get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scan_domain(domain: str, history: bool = True) -> tuple[list[tuple], list[str]]:
    """Rows of (domain, id, kind, seen_where) plus any errors."""
    rows, errors = [], []
    targets = [("live", f"https://{domain}")]
    if history:
        try:
            # "id_" asks Wayback for the original page without its toolbar.
            targets += [(ts[:4], f"https://web.archive.org/web/{ts}id_/{domain}")
                        for ts in snapshots(domain)]
        except Exception as exc:
            errors.append(f"{domain} history: {exc}")
    for where, url in targets:
        try:
            for tid, kind in extract_ids(fetch(url)).items():
                rows.append((domain, tid, kind, where))
        except Exception as exc:
            errors.append(f"{domain} {where}: {exc}")
    return rows, errors


def shared_ids(rows) -> list[tuple[str, str, str]]:
    """IDs that appear on more than one domain -> (id, kind, domains)."""
    by_id, kinds = defaultdict(set), {}
    for domain, tid, kind, _ in rows:
        by_id[tid].add(domain)
        kinds[tid] = kind
    return [(tid, kinds[tid], ", ".join(sorted(d))) for tid, d in by_id.items() if len(d) > 1]


def merge_sightings(rows) -> list[tuple[str, str, str, str]]:
    """Collapse to one row per (domain, id) listing every year it was seen."""
    seen = defaultdict(list)
    kinds = {}
    for domain, tid, kind, where in rows:
        seen[(domain, tid)].append(where)
        kinds[tid] = kind
    out = []
    for (domain, tid), wheres in seen.items():
        years = sorted({w for w in wheres if w != "live"})
        when = ", ".join((["live"] if "live" in wheres else []) + years)
        out.append((domain, tid, kinds[tid], when))
    return sorted(out)
