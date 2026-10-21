"""Port and country crosswalks.

Ports:     Census Schedule D (4-char) -> UN/LOCODE -> WPI number -> lat/lon -> geofence.
Countries: Census Schedule C CTY_CODE -> ISO -> Comtrade M49.

Both seed tables in ``crosswalks/`` are hand-built and carry a ``verified`` flag.
``enrich_ports_from_wpi`` replaces seed coordinates with World Port Index values
and records the WPI number; the country table should be checked against the
Schedule C "By ISO code" list before results are published.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import CROSSWALK_DIR

EARTH_RADIUS_KM = 6371.0088


def load_ports(path: Path | None = None) -> pd.DataFrame:
    df = pd.read_csv(path or CROSSWALK_DIR / "ports.csv", dtype={"schedule_d": "string", "district": "string",
                                                                    "unlocode": "string", "wpi_number": "string"})
    df["census_covered"] = df["census_covered"].astype(bool)
    df["verified"] = df["verified"].astype(bool)
    # Geofence id: one row per physical harbour; several may share a Census port.
    df["geofence_id"] = df["unlocode"].fillna("SD" + df["schedule_d"].fillna("NA"))
    return df


def load_countries(path: Path | None = None) -> pd.DataFrame:
    return pd.read_csv(path or CROSSWALK_DIR / "countries.csv",
                       dtype={"cty_code": "string", "iso2": "string", "iso3": "string"})


def _norm_locode(s: pd.Series) -> pd.Series:
    return s.astype("string").str.replace(r"\s+", "", regex=True).str.upper()


def enrich_ports_from_wpi(ports: pd.DataFrame, wpi_csv: Path) -> pd.DataFrame:
    """Overwrite lat/lon with World Port Index (NGA Pub 150) values matched on UN/LOCODE."""
    wpi = pd.read_csv(wpi_csv, dtype=str)
    need = {"World Port Index Number", "UN/LOCODE", "Latitude", "Longitude"}
    missing = need - set(wpi.columns)
    if missing:
        raise ValueError(f"WPI file is missing columns {sorted(missing)}")
    wpi = wpi.assign(locode=_norm_locode(wpi["UN/LOCODE"])).dropna(subset=["locode"])
    wpi = wpi.drop_duplicates("locode").set_index("locode")

    out = ports.copy()
    key = _norm_locode(out["unlocode"])
    hit = key.isin(wpi.index)
    out.loc[hit, "wpi_number"] = wpi.loc[key[hit], "World Port Index Number"].to_numpy()
    out.loc[hit, "lat"] = pd.to_numeric(wpi.loc[key[hit], "Latitude"]).to_numpy()
    out.loc[hit, "lon"] = pd.to_numeric(wpi.loc[key[hit], "Longitude"]).to_numpy()
    return out


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def assign_geofence(lat: pd.Series, lon: pd.Series, ports: pd.DataFrame) -> pd.Series:
    """Return the geofence_id of the nearest port whose radius contains each point (else <NA>).

    Nearest-centre wins where circles overlap (e.g. Los Angeles / Long Beach).
    """
    lat_a = lat.to_numpy(dtype=float)[:, None]
    lon_a = lon.to_numpy(dtype=float)[:, None]
    d = haversine_km(lat_a, lon_a, ports["lat"].to_numpy()[None, :], ports["lon"].to_numpy()[None, :])
    d = np.where(d <= ports["radius_km"].to_numpy()[None, :], d, np.inf)
    best = d.argmin(axis=1)
    inside = np.isfinite(d[np.arange(len(best)), best])
    ids = ports["geofence_id"].to_numpy()[best]
    return pd.Series(np.where(inside, ids, None), index=lat.index, dtype="string")


def unmapped_countries(cty_codes: pd.Series, countries: pd.DataFrame) -> list[str]:
    """Country codes present in Census pulls with no M49 mapping yet (add them to countries.csv)."""
    seen = set(cty_codes.dropna().astype(str))
    return sorted(seen - set(countries["cty_code"]))
