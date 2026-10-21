"""Port-call detection from AIS points and port × month aggregation.

A *visit* is a contiguous run of a vessel's points inside one geofence, split
whenever the vessel leaves the fence or goes silent for more than ``max_gap_h``.
A visit becomes a *call* when its dwell (time at ``sog <= moored_sog``) reaches
``min_dwell_h``. Calls carry arrival/departure draft so that draft change can
serve as a discharge proxy in allocation.

Anchorage wait: slow time spent in the ring between the berth geofence and
``anchorage_km`` from the port centre during the ``wait_window_h`` before
arrival. This is the only "route deviation" signal the coastal-only AIS supports.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import MAX_GAP_H, MIN_DWELL_H, MOORED_SOG_KN
from .crosswalk import assign_geofence


def _segment(points: pd.DataFrame, fence: pd.Series, max_gap_h: float) -> pd.DataFrame:
    df = points.assign(geofence_id=fence).sort_values(["mmsi", "base_date_time"], kind="stable")
    new_vessel = df["mmsi"].ne(df["mmsi"].shift())
    gap_h = df["base_date_time"].diff().dt.total_seconds() / 3600
    fence_key = df["geofence_id"].fillna("__out__")
    # Comparisons against shift() yield NA on the first row; NA there means "new visit".
    changed = (fence_key.ne(fence_key.shift()).fillna(True) | new_vessel.fillna(True)
               | (gap_h > max_gap_h).fillna(False))
    df["visit_id"] = changed.to_numpy(dtype=bool).cumsum()
    # Time until the next point of the same visit (the last point contributes nothing).
    nxt = df.groupby("visit_id")["base_date_time"].shift(-1)
    df["dt_h"] = ((nxt - df["base_date_time"]).dt.total_seconds() / 3600).fillna(0.0).clip(upper=max_gap_h)
    return df


def detect_calls(points: pd.DataFrame, ports: pd.DataFrame, *, moored_sog: float = MOORED_SOG_KN,
                 min_dwell_h: float = MIN_DWELL_H, max_gap_h: float = MAX_GAP_H,
                 anchorage_km: float = 25.0, wait_window_h: float = 72.0) -> pd.DataFrame:
    """Return one row per detected port call."""
    cols = ["mmsi", "geofence_id", "arrival", "departure", "duration_h", "dwell_h", "n_points",
            "arr_draft", "dep_draft", "censored"]
    if points.empty:
        return pd.DataFrame(columns=cols)

    fence = assign_geofence(points["latitude"], points["longitude"], ports)
    df = _segment(points, fence, max_gap_h)
    inside = df[df["geofence_id"].notna()].copy()
    inside["slow_h"] = np.where(inside["sog"].fillna(np.inf) <= moored_sog, inside["dt_h"], 0.0)

    def first_valid(s):
        s = s.dropna()
        return s.iloc[0] if len(s) else np.nan

    def last_valid(s):
        s = s.dropna()
        return s.iloc[-1] if len(s) else np.nan

    g = inside.groupby("visit_id", sort=False)
    calls = g.agg(mmsi=("mmsi", "first"), geofence_id=("geofence_id", "first"),
                  arrival=("base_date_time", "min"), departure=("base_date_time", "max"),
                  dwell_h=("slow_h", "sum"), n_points=("mmsi", "size"),
                  arr_draft=("draft", first_valid), dep_draft=("draft", last_valid)).reset_index(drop=True)
    calls["duration_h"] = (calls["departure"] - calls["arrival"]).dt.total_seconds() / 3600
    calls = calls[calls["dwell_h"] >= min_dwell_h].copy()

    t0, t1 = points["base_date_time"].min(), points["base_date_time"].max()
    edge = pd.Timedelta(hours=max_gap_h)
    calls["censored"] = (calls["arrival"] <= t0 + edge) | (calls["departure"] >= t1 - edge)

    calls["anchorage_wait_h"] = _anchorage_wait(df, calls, ports, moored_sog, anchorage_km, wait_window_h)
    meta = ports[["geofence_id", "schedule_d", "port_name", "region", "census_covered"]]
    calls = calls.merge(meta, on="geofence_id", how="left")
    calls["port_key"] = calls["schedule_d"].fillna(calls["geofence_id"])
    return calls.sort_values(["arrival", "mmsi"]).reset_index(drop=True)


def _anchorage_wait(df, calls, ports, moored_sog, anchorage_km, wait_window_h) -> pd.Series:
    if calls.empty:
        return pd.Series(dtype=float)
    ring_ports = ports.assign(radius_km=anchorage_km)
    out = df[df["geofence_id"].isna()]
    slow = out[out["sog"].fillna(np.inf) <= moored_sog]
    if slow.empty:
        return pd.Series(0.0, index=calls.index)
    zone = assign_geofence(slow["latitude"], slow["longitude"], ring_ports)
    slow = slow.assign(zone=zone).dropna(subset=["zone"])
    if slow.empty:
        return pd.Series(0.0, index=calls.index)

    # Attach each slow anchorage point to the same vessel's next call at that port.
    c = calls[["mmsi", "geofence_id", "arrival"]].reset_index().rename(columns={"index": "call_idx"})
    c = c.sort_values("arrival")
    s = slow[["mmsi", "zone", "base_date_time", "dt_h"]].rename(columns={"zone": "geofence_id"})
    s = s.sort_values("base_date_time")
    m = pd.merge_asof(s, c, left_on="base_date_time", right_on="arrival", by=["mmsi", "geofence_id"],
                      direction="forward", tolerance=pd.Timedelta(hours=wait_window_h))
    waits = m.dropna(subset=["call_idx"]).groupby("call_idx")["dt_h"].sum()
    return waits.reindex(calls.index, fill_value=0.0).astype(float).values


def port_month_stats(calls: pd.DataFrame, registry: pd.DataFrame | None = None) -> pd.DataFrame:
    """Aggregate calls to port × month (month of arrival, UTC)."""
    c = calls.copy()
    c["month"] = c["arrival"].dt.strftime("%Y-%m")
    if registry is not None:
        c = c.merge(registry[["mmsi", "vessel_type", "vessel_key"]], on="mmsi", how="left")
    else:
        c["vessel_key"] = "MMSI" + c["mmsi"].astype("string")
    g = c.groupby(["port_key", "month"])
    out = g.agg(n_calls=("mmsi", "size"), n_vessels=("vessel_key", "nunique"),
                dwell_mean_h=("dwell_h", "mean"), dwell_var_h2=("dwell_h", "var"),
                dwell_median_h=("dwell_h", "median"), dwell_p90_h=("dwell_h", lambda s: s.quantile(0.9)),
                anchorage_wait_mean_h=("anchorage_wait_h", "mean"),
                n_censored=("censored", "sum")).reset_index()
    if "vessel_type" in c:
        cargo = c[c["vessel_type"].between(70, 79)].groupby(["port_key", "month"]).size()
        tank = c[c["vessel_type"].between(80, 89)].groupby(["port_key", "month"]).size()
        out = out.merge(cargo.rename("n_cargo_calls").reset_index(), how="left", on=["port_key", "month"])
        out = out.merge(tank.rename("n_tanker_calls").reset_index(), how="left", on=["port_key", "month"])
        out[["n_cargo_calls", "n_tanker_calls"]] = out[["n_cargo_calls", "n_tanker_calls"]].fillna(0).astype(int)
    return out
