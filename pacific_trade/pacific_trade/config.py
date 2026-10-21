"""Scope constants from the data-source recon (2026-10-19).

Everything that encodes a scoping decision lives here so it can be cited and
changed in one place.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---- Commodities ----------------------------------------------------------
# HS 8541-8544: semiconductors / ICs / insulated wire & cable.
SEMI_HS4 = ["8541", "8542", "8543", "8544"]
# HS 2609-2621 + 2846: critical-mineral ores, concentrates, rare-earth compounds.
MINERAL_HS4 = [str(c) for c in range(2609, 2622)] + ["2846"]
ALL_HS4 = SEMI_HS4 + MINERAL_HS4


def commodity_group(hs: str) -> str:
    """Map any HS code (2-10 digits) to 'semiconductor', 'mineral' or 'other'."""
    hs4 = str(hs)[:4]
    if hs4 in SEMI_HS4:
        return "semiconductor"
    if hs4 in MINERAL_HS4:
        return "mineral"
    return "other"


# ---- AIS --------------------------------------------------------------------
AIS_CSV2_URL = "https://noaaocm.blob.core.windows.net/ais/csv2/csv{yyyy}/ais-{yyyy}-{mm}-{dd}.csv.zst"
AIS_LEGACY_URL = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{yyyy}/AIS_{yyyy}_{mm}_{dd}.zip"
# csv2 (2025+) is the only path that serves 2025 onward; legacy zips serve <=2024.
AIS_CSV2_FIRST_YEAR = 2025

# Stream-filter boxes: (lon_min, lon_max, lat_min, lat_max).
PACIFIC_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "hawaii": (-161.0, -154.0, 18.0, 23.0),
    "west_coast": (-126.0, -116.0, 32.0, 49.5),
    "guam_cnmi": (144.4, 146.1, 13.0, 15.4),
}

# AIS ship-type codes kept for the commodity story: 70-79 cargo, 80-89 tanker.
CARGO_TANKER_TYPES = range(70, 90)
# Allocation only uses dry cargo (ores and chips don't ride tankers).
ALLOCATION_TYPES = range(70, 80)

# Port-call detection defaults.
MOORED_SOG_KN = 0.5          # sog at or below this counts as dwelling
MIN_DWELL_H = 2.0            # minimum dwell to count as a call
MAX_GAP_H = 6.0              # a gap longer than this splits a visit

# ---- API endpoints ----------------------------------------------------------
CENSUS_BASE = "https://api.census.gov/data/timeseries/intltrade/imports"
COMTRADE_DATA = "https://comtradeapi.un.org/data/v1/get/C/{freq}/HS"
COMTRADE_PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/{freq}/HS"
COMTRADE_AVAIL = "https://comtradeapi.un.org/public/v1/getDA/C/{freq}/HS"
WPI_URL = "https://msi.nga.mil/api/publications/download?type=view&key=16920959/SFH00000/UpdatedPub150.csv"

COMTRADE_USA = 842  # M49 code Comtrade uses for the United States

# ---- Paths & keys -------------------------------------------------------------
PKG_ROOT = Path(__file__).resolve().parent.parent
CROSSWALK_DIR = PKG_ROOT / "crosswalks"


def data_dir() -> Path:
    d = Path(os.environ.get("PACIFIC_TRADE_DATA", PKG_ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def require_env(name: str, signup_url: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"{name} is not set. Get a free key at {signup_url} and export {name}=...")
    return val
