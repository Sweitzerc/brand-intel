"""
SEC EDGAR full-text search.

Adapted from bellingcat/EDGAR. Search every public-company filing since
2001 for a phrase - handy for seeing which listed competitors, retailers
or suppliers mention your brand, product category or a rival.
"""

from ..core import TIMEOUT, http

FULLTEXT = "https://efts.sec.gov/LATEST/search-index"


def parse(payload: dict) -> list[tuple]:
    rows = []
    for hit in payload.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        adsh, _, filename = hit.get("_id", "").partition(":")
        cik = (src.get("ciks") or [""])[0].lstrip("0")
        link = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{filename}"
                if cik and adsh else "")
        rows.append((
            src.get("file_date", ""),
            ", ".join(src.get("display_names", [])),
            src.get("form", ""),
            link,
        ))
    return rows


def search(phrase: str, forms: str = "", start: str = "", end: str = "") -> list[tuple]:
    """Rows of (filed, company, form, link). Dates are YYYY-MM-DD."""
    params = {"q": f'"{phrase}"'}
    if forms:
        params["forms"] = forms
    if start or end:
        params.update(dateRange="custom", startdt=start or "2001-01-01", enddt=end or "2100-01-01")
    resp = http().get(FULLTEXT, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse(resp.json())
