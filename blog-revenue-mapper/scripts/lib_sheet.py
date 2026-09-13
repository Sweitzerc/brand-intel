"""Read the Canes Galore reports sheet.

Two sources, same output:

  api     Google Sheets API with the service account from the existing
          canes-galore-scripts GCP project. This is what runs on Chris's
          machine and in the Monday cadence.
  export  A markdown export of the whole spreadsheet saved to
          data/raw/sheet_export.md. Used when API credentials are not
          available. Same rows, same columns.

Tabs are located by HEADER SIGNATURE rather than by tab name. The export
format does not carry tab names at all, and the Apps Script has renamed tabs
before, so matching on the columns we actually need is the stable option.
"""

from __future__ import annotations

import csv
import os
import re
from typing import Dict, List

# A tab is identified by a set of columns that must all be present.
TAB_SIGNATURES: Dict[str, set] = {
    "ga_landing": {"landing_page", "sessions", "add_to_carts", "purchases", "revenue"},
    "gsc_queries": {"query", "clicks", "impressions", "position"},
    "gsc_pages": {"page", "clicks", "impressions", "position"},
    "scorecard": {"metric", "this_period", "prev_period"},
    # Written by apps_script/gsc_query_page.gs. The only source that says
    # which queries belong to which page.
    "gsc_query_page": {"query", "page", "clicks", "impressions", "position"},
}


def _clean(cell: str) -> str:
    # The markdown export escapes underscores and minus signs.
    return cell.replace("\\_", "_").replace("\\-", "-").strip()


def _is_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    body = stripped.replace("|", "").replace(" ", "")
    return bool(body) and set(body) <= set(":-")


def _split_row(line: str) -> List[str]:
    return [_clean(c) for c in line.strip().strip("|").split("|")]


def read_export(path: str) -> Dict[str, List[dict]]:
    """Parse every markdown table in the export and label them by signature."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    tables: List[List[dict]] = []
    i = 0
    while i < len(lines):
        if not _is_separator(lines[i]) or i + 1 >= len(lines):
            i += 1
            continue
        header = _split_row(lines[i + 1])
        rows: List[dict] = []
        j = i + 2
        while j < len(lines) and lines[j].strip().startswith("|"):
            cells = _split_row(lines[j])
            if len(cells) < len(header):
                cells += [""] * (len(header) - len(cells))
            rows.append(dict(zip(header, cells[: len(header)])))
            j += 1
        if header and rows:
            tables.append(rows)
        i = j

    out: Dict[str, List[dict]] = {}
    for rows in tables:
        cols = set(rows[0].keys())
        # Most specific signature first, so a query x page table is not
        # claimed by the plain gsc_queries signature, which it contains.
        for name, signature in sorted(
            TAB_SIGNATURES.items(), key=lambda kv: -len(kv[1])
        ):
            if name not in out and signature <= cols:
                out[name] = rows
                break
    return out


def read_xlsx(path: str) -> Dict[str, List[dict]]:
    """Parse a full .xlsx export. Tabs are matched by header signature.

    Use this in preference to read_export. Drive's markdown rendering caps
    its output, so a large tab comes back quietly truncated; the .xlsx
    carries every row.
    """
    import openpyxl  # type: ignore

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: Dict[str, List[dict]] = {}

    for name in book.sheetnames:
        raw = [
            row for row in book[name].iter_rows(values_only=True)
            if any(cell is not None and str(cell).strip() for cell in row)
        ]
        if len(raw) < 2:
            continue
        header = [_clean(str(c)) if c is not None else "" for c in raw[0]]
        cols = {h for h in header if h}
        rows = [
            dict(zip(header, ["" if c is None else c for c in row]))
            for row in raw[1:]
        ]
        for tab, signature in sorted(TAB_SIGNATURES.items(), key=lambda kv: -len(kv[1])):
            if tab not in out and signature <= cols:
                out[tab] = rows
                break
    return out


def read_api(sheet_id: str, tabs: Dict[str, str]) -> Dict[str, List[dict]]:
    """Read the tabs over the Sheets API (read-only scope)."""
    from google.oauth2 import service_account  # type: ignore
    from googleapiclient.discovery import build  # type: ignore

    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set. There is no known "
            "service account key for this project - the Monday Apps Script "
            "runs as the sheet owner over OAuth, not as a service account. "
            "Use --source export with a saved spreadsheet export instead."
        )
    creds = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    api = build("sheets", "v4", credentials=creds, cache_discovery=False)

    out: Dict[str, List[dict]] = {}
    for name, tab in tabs.items():
        values = (
            api.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=f"'{tab}'")
            .execute()
            .get("values", [])
        )
        if not values:
            continue
        header = [_clean(c) for c in values[0]]
        rows = []
        for raw in values[1:]:
            padded = list(raw) + [""] * (len(header) - len(raw))
            rows.append(dict(zip(header, padded[: len(header)])))
        out[name] = rows
    return out


def write_tab(rows: List[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def read_tab(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def num(value, default: float = 0.0) -> float:
    """Parse a sheet cell as a number. Sheets emits '', '(not set)', '1,234'."""
    if value is None:
        return default
    text = str(value).strip().replace(",", "").replace("%", "").replace("$", "")
    if not text or text in {"(not set)", "-", "—"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default
