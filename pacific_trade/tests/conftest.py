import pandas as pd
import pytest

from pacific_trade import crosswalk


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PACIFIC_TRADE_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    monkeypatch.delenv("COMTRADE_API_KEY", raising=False)


@pytest.fixture
def ports():
    return crosswalk.load_ports()


def track(mmsi, start, steps, lat, lon, sog, draft=10.0, imo=None, vessel_type=70):
    """Build an AIS track: `steps` points every 10 minutes."""
    t = pd.date_range(start, periods=steps, freq="10min", tz="UTC")
    return pd.DataFrame({
        "mmsi": pd.array([mmsi] * steps, dtype="Int64"), "base_date_time": t,
        "latitude": lat, "longitude": lon, "sog": sog, "draft": draft,
        "imo": pd.array([imo] * steps, dtype="Int64"), "vessel_name": "X", "call_sign": "C",
        "vessel_type": pd.array([vessel_type] * steps, dtype="Int64"), "length": 200.0, "width": 30.0,
    })


def census_rows(rows):
    """rows: (PORT, time, I_COMMODITY, CTY_CODE, GEN, VES, VES_WGT, AIR)."""
    from pacific_trade import census
    df = pd.DataFrame(rows, columns=["PORT", "time", "I_COMMODITY", "CTY_CODE", "GEN_VAL_MO",
                                     "VES_VAL_MO", "VES_WGT_MO", "AIR_VAL_MO"])
    df["PORT_NAME"] = df["PORT"]
    df["CTY_NAME"] = df["CTY_CODE"]
    df["CNT_VAL_MO"] = df["VES_VAL_MO"] * 0.5
    df["CNT_WGT_MO"] = df["VES_WGT_MO"] * 0.5
    df["AIR_WGT_MO"] = 1.0
    return census.tidy(df.astype({c: str for c in df.columns}))
