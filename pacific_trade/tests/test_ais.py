import io

import pandas as pd
import zstandard

from pacific_trade import ais

LEGACY = """MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,VesselType,Status,Length,Width,Draft,Cargo,TransceiverClass
367000001,2023-07-15T00:00:01,21.30,-157.86,0.1,360.0,511,SHIP A,IMO9267209,WABC,70,5,200,30,11.2,70,A
367000002,2023-07-15T00:00:02,40.00,-70.00,12.0,90.0,90,SHIP B,,WXYZ,70,0,150,25,9.0,70,A
367000003,2023-07-15T00:00:03,21.31,-157.87,0.0,0.0,0,TUG,IMO0000000,WTUG,52,0,30,10,0,0,A
"""
CSV2 = """mmsi,base_date_time,longitude,latitude,sog,cog,heading,vessel_name,imo,call_sign,vessel_type,status,length,width,draft,cargo,transceiver
367000001,2026-06-15 00:00:01,-157.86,21.30,0.1,,,SHIP A,IMO9267209,WABC,70,5,200,30,11.2,70,A
367000004,2026-06-15 00:00:05,144.66,13.44,0.2,,,SHIP G,IMO9000001,WGUM,80,5,180,32,10.0,80,A
"""


def _read(text):
    return ais.normalise(pd.read_csv(io.StringIO(text), dtype=str))


def test_both_schemas_normalise_to_canonical():
    a, b = _read(LEGACY), _read(CSV2)
    assert list(a.columns) == list(b.columns) == ais.CANONICAL
    assert a.loc[0, "imo"] == 9267209 and b.loc[0, "imo"] == 9267209
    assert a.loc[0, "longitude"] == b.loc[0, "longitude"] == -157.86
    assert str(a["base_date_time"].dt.tz) == "UTC"


def test_legacy_sentinels_become_null():
    a = _read(LEGACY)
    assert pd.isna(a.loc[0, "heading"]) and pd.isna(a.loc[0, "cog"])
    assert pd.isna(a.loc[2, "imo"])        # IMO0000000 is not a real IMO
    assert pd.isna(a.loc[2, "draft"])      # draft 0 means unknown


def test_filter_keeps_pacific_cargo_only():
    out = ais.filter_chunk(_read(LEGACY))
    assert out["mmsi"].tolist() == [367000001]          # Atlantic ship and tug dropped
    assert out["region"].tolist() == ["hawaii"]
    g = ais.filter_chunk(_read(CSV2))
    assert set(g["region"]) == {"hawaii", "guam_cnmi"}


def test_zst_stream_roundtrip(tmp_path):
    p = tmp_path / "ais-2026-06-15.csv.zst"
    p.write_bytes(zstandard.ZstdCompressor().compress(CSV2.encode()))
    with ais.open_local(p) as fh:
        df = ais.filter_stream(fh)
    assert len(df) == 2


def test_day_url_switches_schema():
    import datetime as dt
    assert ais.day_url(dt.date(2026, 6, 15)).endswith("csv2026/ais-2026-06-15.csv.zst")
    assert ais.day_url(dt.date(2024, 7, 15)).endswith("2024/AIS_2024_07_15.zip")


def test_registry_resolves_imo_from_sparse_rows():
    df = _read(CSV2 + "367000001,2026-06-15 01:00:00,-157.86,21.30,0.1,,,SHIP A,,WABC,70,5,200,30,11.0,70,A\n")
    reg = ais.vessel_registry(df).set_index("mmsi")
    assert reg.loc[367000001, "vessel_key"] == "IMO9267209"
    assert not reg.loc[367000001, "imo_conflict"]
