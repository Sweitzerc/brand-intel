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
        return session

    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        raise RuntimeError(
            "Set GOOGLE_APPLICATION_CREDENTIALS to the canes-galore-scripts "
            "service account key, or GSC_ACCESS_TOKEN to an OAuth token with "
            f"{SCOPE}. The service account must be added as a user on the "
            "Search Console property."
        )
    from google.auth.transport.requests import AuthorizedSession  # type: ignore
    from google.oauth2 import service_account  # type: ignore

    creds = service_account.Credentials.from_service_account_file(key_path, scopes=[SCOPE])
    return AuthorizedSession(creds)


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
    args = parser.parse_args()

    end = dt.date.today() - dt.timedelta(days=args.lag)
    start = end - dt.timedelta(days=args.days - 1)
    rows = fetch(args.site, start.isoformat(), end.isoformat(),
                 args.dimensions.split(","))
    write(rows, args.out)
    pages = len({r.get("page") for r in rows})
    print(f"{len(rows)} rows over {start} to {end} across {pages} pages -> {args.out}")


if __name__ == "__main__":
    main()
