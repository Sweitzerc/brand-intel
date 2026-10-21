"""Census International Trade API client (imports/porths and imports/hs).

* ``porths``: port of unlading × HS2/4/6 × country of origin × month, with vessel,
  containerised and air value/weight. This is the core trade table.
* ``hs``: customs district × up to HS10 × country × month, for sub-HS6 splits.

A key is mandatory (keyless calls 302 to missing_key.html). Responses are cached
on disk by query (without the key) so re-runs cost nothing.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd

from . import http
from .config import CENSUS_BASE, data_dir, require_env

PORTHS_VARS = ["PORT", "PORT_NAME", "I_COMMODITY", "I_COMMODITY_SDESC", "CTY_CODE", "CTY_NAME",
               "GEN_VAL_MO", "VES_VAL_MO", "VES_WGT_MO", "CNT_VAL_MO", "CNT_WGT_MO",
               "AIR_VAL_MO", "AIR_WGT_MO"]
HS_VARS = ["DISTRICT", "DIST_NAME", "I_COMMODITY", "I_COMMODITY_SDESC", "CTY_CODE", "CTY_NAME",
           "GEN_VAL_MO", "CON_VAL_MO", "GEN_CIF_MO", "VES_VAL_MO", "VES_WGT_MO", "CNT_VAL_MO",
           "AIR_VAL_MO", "AIR_WGT_MO", "GEN_QY1_MO", "UNIT_QY1"]
NUMERIC_SUFFIXES = ("_VAL_MO", "_WGT_MO", "_CIF_MO", "_QY1_MO", "_CHA_MO")


class CensusError(RuntimeError):
    pass


def _key() -> str:
    return require_env("CENSUS_API_KEY", "https://api.census.gov/data/key_signup.html")


def time_predicate(start: str, end: str | None = None) -> str:
    """'2026-06' or 'from 2025-01 to 2026-06'."""
    return start if not end or end == start else f"from {start} to {end}"


def query(endpoint: str, get_vars: Sequence[str], predicates: dict[str, str | list[str]],
          *, key: str | None = None, use_cache: bool = True) -> pd.DataFrame:
    """Run one Census query and return a raw (string-typed) DataFrame.

    List-valued predicates are sent as repeated parameters, which the API ORs.
    """
    if len(get_vars) > 50:
        raise ValueError("Census allows at most 50 variables per query")
    url = f"{CENSUS_BASE}/{endpoint}"
    params: dict = {"get": ",".join(get_vars), **predicates}

    def fetch():
        r = http.get(url, params={**params, "key": key or _key()}, allow_redirects=False)
        if r.status_code in (301, 302) and "missing_key" in r.headers.get("Location", ""):
            raise CensusError("Census rejected the request for a missing/invalid key (302 missing_key)")
        if r.status_code == 204:
            return []
        if r.status_code != 200:
            raise CensusError(f"Census {r.status_code}: {r.text[:400]}")
        return r.json()

    payload = http.cached_json(data_dir() / "cache" / "census", url, params, fetch) if use_cache else fetch()
    if not payload:
        return pd.DataFrame(columns=list(get_vars))
    header, *rows = payload
    df = pd.DataFrame(rows, columns=header)
    # Predicate variables are echoed back; drop exact duplicate columns.
    return df.loc[:, ~df.columns.duplicated()]


def tidy(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if c.endswith(NUMERIC_SUFFIXES):
            out[c] = pd.to_numeric(out[c], errors="coerce")
    if "time" in out:
        out["month"] = out["time"].astype(str).str[:7]
    if "I_COMMODITY" in out:
        out["hs"] = out["I_COMMODITY"].astype(str).str.strip()
        out["hs6"] = out["hs"].str[:6]
    if "CTY_CODE" in out:
        code = out["CTY_CODE"].astype(str).str.strip()
        out["CTY_CODE"] = code
        # Individual countries are 4-digit numeric codes 1000-7999; '-' is the total and
        # other codes (e.g. 0003, 1XXX) are regional/aggregate groupings.
        out["is_country"] = code.str.fullmatch(r"[1-7]\d{3}") & ~code.str.endswith("XXX")
    return out


def pull_porths(start: str, end: str | None = None, *, hs_prefixes: Iterable[str],
                ports: Sequence[str] | None = None, comm_lvl: str = "HS6",
                wildcard: bool = True) -> pd.DataFrame:
    """Port × HS6 × country × month for the given HS prefixes (e.g. ['8541', '2846']).

    ``wildcard=True`` uses ``I_COMMODITY=8541*`` (one call per prefix). The recon could
    not confirm wildcard support without a key; if it is rejected, pass
    ``wildcard=False`` to pull all HS6 lines per port and filter locally.
    """
    t = time_predicate(start, end)
    base = {"time": t, "COMM_LVL": comm_lvl}
    if ports:
        base["PORT"] = list(ports)
    frames = []
    if wildcard:
        for p in hs_prefixes:
            frames.append(query("porths", PORTHS_VARS, {**base, "I_COMMODITY": f"{p}*"}))
    else:
        if not ports:
            raise ValueError("wildcard=False needs an explicit port list to keep the pull small")
        prefixes = tuple(hs_prefixes)
        for port in ports:
            df = query("porths", PORTHS_VARS, {**base, "PORT": port})
            frames.append(df[df["I_COMMODITY"].astype(str).str.startswith(prefixes)])
    return tidy(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame()


def pull_hs10(start: str, end: str | None = None, *, hs_prefixes: Iterable[str],
              districts: Sequence[str] | None = None) -> pd.DataFrame:
    """District × HS10 × country × month (use when HS6 is too coarse, e.g. 2846 splits)."""
    base = {"time": time_predicate(start, end), "COMM_LVL": "HS10"}
    if districts:
        base["DISTRICT"] = list(districts)
    frames = [query("hs", HS_VARS, {**base, "I_COMMODITY": f"{p}*"}) for p in hs_prefixes]
    return tidy(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame()
