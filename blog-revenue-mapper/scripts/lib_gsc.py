"""Pull Search Console data directly, with BOTH the query and page dimensions.

This is the fix for the gap documented in lib_attribute.py. The Monday Apps
Script exports queries and pages as two independent top-N lists over a
trailing 28 days, so there is no way to say which queries belong to which
page. The Search Console API will return them together, and retains roughly
16 months of history, so the 90-day window the build spec assumed is
available - it was never a data limitation, only the date range the script
asked for.

Requires a service account with Search Console read access (the existing
canes-galore-scripts account, added as a user on the property) pointed at by
GOOGLE_APPLICATION_CREDENTIALS, or an OAuth token in GSC_ACCESS_TOKEN.

    python3 scripts/lib_gsc.py --days 90 --out data/tabs/gsc_query_page.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from typing import Dict, Iterable, List

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
PAGE_SIZE = 25000


def _session():
    """An authorised requests session, service account or bearer token."""
    import requests  # type: ignore

    token = os.environ.get("GSC_ACCESS_TOKEN")
    if token:
        session = requests.Session()
        session.headers["Authorization"] = f"Bearer {token}"
        session._identity = "GSC_ACCESS_TOKEN (bearer token)"  # type: ignore[attr-defined]
        return session

    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        raise RuntimeError(
            "No Search Console credentials. Set GSC_ACCESS_TOKEN to an OAuth "
            f"token with {SCOPE}, or GOOGLE_APPLICATION_CREDENTIALS to a "
            "service account key that has been added as a user on the "
            "property. Easier: run apps_script/gsc_query_page.gs from the "
            "sheet instead, which needs no credentials at all."
        )
    from google.auth.transport.requests import AuthorizedSession  # type: ignore
    from google.oauth2 import service_account  # type: ignore

    creds = service_account.Credentials.from_service_account_file(key_path, scopes=[SCOPE])
    session = AuthorizedSession(creds)
    session._identity = f"service account {creds.service_account_email}"  # type: ignore[attr-defined]
    return session


def list_sites() -> List[dict]:
    """Every property this identity can read, with its exact siteUrl."""
    session = _session()
    response = session.get("https://searchconsole.googleapis.com/webmasters/v3/sites", timeout=60)
    response.raise_for_status()
    return response.json().get("siteEntry", [])


def fetch(
    site: str,
    start: str,
    end: str,
    dimensions: Iterable[str] = ("query", "page"),
    row_limit: int = PAGE_SIZE,
) -> List[dict]:
    """Every row for the window, paginated. Search type: web."""
    from urllib.parse import quote

    session = _session()
    url = ENDPOINT.format(site=quote(site, safe=""))
    dimensions = list(dimensions)
    # Say who we are and what we are asking for BEFORE the request. A 403
    # here is ambiguous between "wrong siteUrl string" and "this identity
    # cannot read the property", and the body does not distinguish them.
    print(f"authenticating as     {getattr(session, '_identity', 'unknown')}")
    print(f"siteUrl               {site}")
    print(f"window                {start} to {end}")

    rows: List[dict] = []
    start_row = 0
    while True:
        body = {
            "startDate": start,
            "endDate": end,
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": start_row,
            "type": "web",
            # Ask for every row rather than Google's default aggregation, so
            # page-level totals are not silently collapsed.
            "dataState": "final",
        }
        response = session.post(url, json=body, timeout=120)
        if response.status_code == 403:
            raise SystemExit(
                f"403 for siteUrl {site!r}.\n"
                "Either the string is not EXACTLY the property as Search "
                "Console stores it, or this identity cannot read it. Run "
                "--list-sites to see the exact strings. A URL-prefix property "
                "looks like https://www.canesgalore.com/ and a domain "
                "property like sc-domain:canesgalore.com; the two are not "
                f"interchangeable.\nBody: {response.text[:400]}"
            )
        response.raise_for_status()
        block = response.json().get("rows", [])
        for row in block:
            record = dict(zip(dimensions, row["keys"]))
            record.update(
                {
                    "clicks": row.get("clicks", 0),
                    "impressions": row.get("impressions", 0),
                    "ctr": round(100 * row.get("ctr", 0.0), 4),
                    "position": round(row.get("position", 0.0), 2),
                }
            )
            rows.append(record)
        if len(block) < row_limit:
            return rows
        start_row += row_limit


def write(rows: List[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull GSC query x page rows.")
    parser.add_argument("--site", default="https://www.canesgalore.com/",
                        help="Search Console property, exactly as it appears there")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--lag", type=int, default=3, help="skip the most recent N days")
    parser.add_argument("--out", default="data/tabs/gsc_query_page.csv")
    parser.add_argument("--dimensions", default="query,page")
    parser.add_argument("--list-sites", action="store_true",
                        help="print every readable property and exit")
    args = parser.parse_args()

    if args.list_sites:
        entries = list_sites()
        if not entries:
            raise SystemExit("No Search Console properties readable by this identity.")
        print("Copy the exact siteUrl you want into --site:")
        for entry in entries:
            print(f"  {entry['siteUrl']}   ({entry.get('permissionLevel', '?')})")
        return

    end = dt.date.today() - dt.timedelta(days=args.lag)
    start = end - dt.timedelta(days=args.days - 1)
    rows = fetch(args.site, start.isoformat(), end.isoformat(),
                 args.dimensions.split(","))
    write(rows, args.out)
    pages = len({r.get("page") for r in rows})
    print(f"{len(rows)} rows over {start} to {end} across {pages} pages -> {args.out}")


if __name__ == "__main__":
    main()
