"""NOAA Marine Cadastre AIS: download, normalise both schemas, stream-filter to the Pacific.

* 2025+ files come from the csv2 blob as daily ``.csv.zst`` (~200-300 MB each);
  they are streamed straight from HTTP through a zstd decoder, never landing on disk.
* <=2024 files come from the legacy daily ``.zip`` path; zips need seeking, so they
  are downloaded to a temporary file first.

Output: one Parquet per day at ``{data}/ais/{yyyy}/{yyyy-mm-dd}.parquet`` in the
canonical (csv2, snake_case) schema plus a ``region`` column.
"""

from __future__ import annotations

import datetime as dt
import io
import tempfile
import zipfile
from pathlib import Path
from typing import IO, Iterable, Iterator

import numpy as np
import pandas as pd

from . import http
from .config import (AIS_CSV2_FIRST_YEAR, AIS_CSV2_URL, AIS_LEGACY_URL, CARGO_TANKER_TYPES,
                     PACIFIC_BBOXES, data_dir)

CANONICAL = ["mmsi", "base_date_time", "longitude", "latitude", "sog", "cog", "heading",
             "vessel_name", "imo", "call_sign", "vessel_type", "status", "length", "width",
             "draft", "cargo", "transceiver"]

LEGACY_RENAME = {
    "MMSI": "mmsi", "BaseDateTime": "base_date_time", "LAT": "latitude", "LON": "longitude",
    "SOG": "sog", "COG": "cog", "Heading": "heading", "VesselName": "vessel_name", "IMO": "imo",
    "CallSign": "call_sign", "VesselType": "vessel_type", "Status": "status", "Length": "length",
    "Width": "width", "Draft": "draft", "Cargo": "cargo", "TransceiverClass": "transceiver",
}

FLOAT_COLS = ["longitude", "latitude", "sog", "cog", "heading", "length", "width", "draft"]
INT_COLS = ["vessel_type", "status", "cargo"]


def day_url(day: dt.date) -> str:
    fmt = AIS_CSV2_URL if day.year >= AIS_CSV2_FIRST_YEAR else AIS_LEGACY_URL
    return fmt.format(yyyy=f"{day.year:04d}", mm=f"{day.month:02d}", dd=f"{day.day:02d}")


def normalise(chunk: pd.DataFrame) -> pd.DataFrame:
    """Bring a chunk from either schema into the canonical csv2 schema with clean types.

    Legacy sentinels (heading 511, sog 102.3, cog 360, vessel_type 0, zero dims) become null,
    matching what the 2025 reprocessing does; ``imo`` loses its ``IMO`` prefix and becomes Int64.
    """
    if "MMSI" in chunk.columns:
        chunk = chunk.rename(columns=LEGACY_RENAME)
    for c in CANONICAL:
        if c not in chunk.columns:
            chunk[c] = pd.NA
    df = chunk[CANONICAL].copy()

    df["mmsi"] = pd.to_numeric(df["mmsi"], errors="coerce").astype("Int64")
    df["base_date_time"] = pd.to_datetime(df["base_date_time"], utc=True, errors="coerce", format="mixed")
    for c in FLOAT_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in INT_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")

    df.loc[df["heading"] >= 511, "heading"] = np.nan
    df.loc[df["sog"] >= 102.2, "sog"] = np.nan
    df.loc[df["cog"] >= 360, "cog"] = np.nan
    df.loc[df["vessel_type"] == 0, "vessel_type"] = pd.NA
    for c in ("length", "width", "draft"):
        df.loc[df[c] <= 0, c] = np.nan

    imo = df["imo"].astype("string").str.upper().str.replace("IMO", "", regex=False).str.strip()
    imo = pd.to_numeric(imo, errors="coerce").astype("Int64")
    # Real IMO numbers are 7 digits; zeros and junk become null.
    df["imo"] = imo.where((imo >= 1_000_000) & (imo <= 9_999_999))

    for c in ("vessel_name", "call_sign", "transceiver"):
        df[c] = df[c].astype("string").str.strip().replace("", pd.NA)
    return df.dropna(subset=["mmsi", "base_date_time", "latitude", "longitude"])


def tag_region(df: pd.DataFrame, bboxes=PACIFIC_BBOXES) -> pd.Series:
    region = pd.Series(pd.NA, index=df.index, dtype="string")
    for name, (lon0, lon1, lat0, lat1) in bboxes.items():
        m = df["longitude"].between(lon0, lon1) & df["latitude"].between(lat0, lat1)
        region = region.mask(m & region.isna(), name)
    return region


def filter_chunk(df: pd.DataFrame, *, vessel_types: Iterable[int] | None = CARGO_TANKER_TYPES,
                 bboxes=PACIFIC_BBOXES) -> pd.DataFrame:
    df = df.assign(region=tag_region(df, bboxes))
    df = df[df["region"].notna()]
    if vessel_types is not None:
        df = df[df["vessel_type"].isin(list(vessel_types))]
    return df


def iter_csv_chunks(text: IO[str], chunksize: int = 1_000_000) -> Iterator[pd.DataFrame]:
    yield from pd.read_csv(text, chunksize=chunksize, dtype=str, keep_default_na=True)


def filter_stream(text: IO[str], **kw) -> pd.DataFrame:
    parts = [filter_chunk(normalise(c), **kw) for c in iter_csv_chunks(text)]
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame(columns=CANONICAL + ["region"])
    return pd.concat(parts, ignore_index=True)


def open_local(path: Path) -> IO[str]:
    """Open a local .csv / .csv.zst / .zip AIS file as a text stream."""
    name = path.name.lower()
    if name.endswith(".zst"):
        import zstandard
        raw = zstandard.ZstdDecompressor().stream_reader(open(path, "rb"))
        return io.TextIOWrapper(raw, encoding="utf-8", newline="")
    if name.endswith(".zip"):
        zf = zipfile.ZipFile(path)
        member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        return io.TextIOWrapper(zf.open(member), encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


def out_path(day: dt.date) -> Path:
    return data_dir() / "ais" / f"{day.year:04d}" / f"{day.isoformat()}.parquet"


def fetch_day(day: dt.date, *, vessel_types=CARGO_TANKER_TYPES, overwrite=False) -> Path:
    """Download one national daily file, keep the Pacific boxes, write Parquet."""
    dest = out_path(day)
    if dest.exists() and not overwrite:
        return dest
    url = day_url(day)
    if url.endswith(".zst"):
        import zstandard
        with http.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            r.raw.decode_content = True
            raw = zstandard.ZstdDecompressor().stream_reader(r.raw)
            df = filter_stream(io.TextIOWrapper(raw, encoding="utf-8", newline=""), vessel_types=vessel_types)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            zpath = http.download(url, Path(tmp) / Path(url).name)
            with open_local(zpath) as text:
                df = filter_stream(text, vessel_types=vessel_types)
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dest, index=False)
    return dest


def load_range(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Concatenate the filtered daily Parquet files for [start, end]."""
    files = [out_path(start + dt.timedelta(days=i)) for i in range((end - start).days + 1)]
    files = [f for f in files if f.exists()]
    if not files:
        raise FileNotFoundError(f"No filtered AIS days in {start}..{end}; run `ais-fetch` first.")
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)


def vessel_registry(points: pd.DataFrame) -> pd.DataFrame:
    """One row per MMSI with modal IMO / name / call sign / type / dimensions.

    ``imo`` is null on ~65% of raw rows, so the modal non-null value across the
    whole period is used to resolve identity. ``imo_conflict`` flags MMSIs that
    broadcast more than one IMO (re-flagging, spoofing, or data errors).
    """
    def mode(s: pd.Series):
        s = s.dropna()
        return s.mode().iloc[0] if len(s) else pd.NA

    g = points.groupby("mmsi", sort=False)
    reg = g.agg(imo=("imo", mode), vessel_name=("vessel_name", mode), call_sign=("call_sign", mode),
                vessel_type=("vessel_type", mode), length=("length", "median"), width=("width", "median"),
                max_draft=("draft", "max"), n_points=("mmsi", "size"),
                first_seen=("base_date_time", "min"), last_seen=("base_date_time", "max"))
    reg["imo_conflict"] = g["imo"].nunique() > 1
    reg = reg.reset_index()
    reg["imo"] = reg["imo"].astype("Int64")
    reg["vessel_type"] = reg["vessel_type"].astype("Int64")
    # A stable vessel key: IMO where known, else the MMSI.
    reg["vessel_key"] = np.where(reg["imo"].notna(), "IMO" + reg["imo"].astype("string"),
                                 "MMSI" + reg["mmsi"].astype("string"))
    return reg
