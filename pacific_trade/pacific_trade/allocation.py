"""MODELLED vessel attribution: spread Census port-month vessel tonnage over calling ships.

No source in scope links a vessel to a commodity. This module produces
*estimates* by allocating each port × month × HS6 ``VES_WGT_MO`` / ``VES_VAL_MO``
across the dry-cargo calls at that port in that month, in proportion to a
capacity proxy. Every output row carries ``estimated=True`` and the method name;
present the results as an allocation model, never as observed cargo.

Methods
  capacity    length × width × arrival draft (hull-volume proxy)
  draft_drop  length × width × max(arrival draft − departure draft, 0); calls with
              no usable draft change fall back to capacity × the port-month median
              drop ratio. AIS draft is crew-entered and often stale, so treat this
              as a sensitivity check rather than the default.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ALLOCATION_TYPES
from .metrics import hhi

METHODS = ("capacity", "draft_drop")


def _proxy(calls: pd.DataFrame, method: str) -> pd.Series:
    c = calls.copy()
    grp = c.groupby(["port_key", "month"])
    for col in ("length", "width", "arr_draft"):
        c[col] = c[col].fillna(grp[col].transform("median")).fillna(c[col].median())
    capacity = c["length"] * c["width"] * c["arr_draft"]
    if method == "capacity":
        return capacity
    drop = (c["arr_draft"] - c["dep_draft"]).clip(lower=0)
    ratio = (drop / c["arr_draft"]).where(drop > 0)
    fallback = ratio.groupby([c["port_key"], c["month"]]).transform("median").fillna(0)
    return (c["length"] * c["width"] * drop).where(drop > 0, capacity * fallback)


def allocate(census: pd.DataFrame, calls: pd.DataFrame, registry: pd.DataFrame, *,
             method: str = "capacity", vessel_types=ALLOCATION_TYPES) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (edges, unallocated).

    edges: vessel_key × port × month × HS6 with est_wgt_kg / est_val_usd.
    unallocated: port-month-HS6 tonnage with no eligible call to carry it.
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")

    cen = census[census["is_country"]].groupby(["PORT", "month", "hs6"], as_index=False)[
        ["VES_WGT_MO", "VES_VAL_MO"]].sum()
    cen = cen[(cen["VES_WGT_MO"] > 0) | (cen["VES_VAL_MO"] > 0)].rename(columns={"PORT": "port_key"})

    c = calls.assign(month=calls["arrival"].dt.strftime("%Y-%m"))
    c = c.merge(registry[["mmsi", "vessel_key", "vessel_type", "length", "width"]], on="mmsi", how="left")
    c = c[c["vessel_type"].isin(list(vessel_types))].copy()
    c["proxy"] = _proxy(c, method).fillna(0.0)
    c = c[c["proxy"] > 0]
    c["weight"] = c["proxy"] / c.groupby(["port_key", "month"])["proxy"].transform("sum")

    m = cen.merge(c[["port_key", "month", "vessel_key", "mmsi", "arrival", "weight"]],
                  on=["port_key", "month"], how="left")
    unallocated = m[m["weight"].isna()][["port_key", "month", "hs6", "VES_WGT_MO", "VES_VAL_MO"]]
    edges = m.dropna(subset=["weight"]).copy()
    edges["est_wgt_kg"] = edges["VES_WGT_MO"] * edges["weight"]
    edges["est_val_usd"] = edges["VES_VAL_MO"] * edges["weight"]
    edges["method"] = method
    edges["estimated"] = True
    edges = edges[["vessel_key", "mmsi", "port_key", "month", "hs6", "arrival", "weight",
                   "est_wgt_kg", "est_val_usd", "method", "estimated"]]
    return edges.reset_index(drop=True), unallocated.reset_index(drop=True)


def vessel_spof(edges: pd.DataFrame) -> pd.DataFrame:
    """Per port × month × HS6: how much estimated tonnage rides on the single largest vessel."""
    if edges.empty:
        return pd.DataFrame(columns=["port_key", "month", "hs6", "n_vessels", "vessel_hhi",
                                     "top_vessel", "top_vessel_share", "estimated"])
    v = edges.groupby(["port_key", "month", "hs6", "vessel_key"], as_index=False)["est_wgt_kg"].sum()

    def agg(g):
        top = g.loc[g["est_wgt_kg"].idxmax()]
        tot = g["est_wgt_kg"].sum()
        return pd.Series({"n_vessels": len(g), "vessel_hhi": hhi(g["est_wgt_kg"]),
                          "top_vessel": top["vessel_key"],
                          "top_vessel_share": top["est_wgt_kg"] / tot if tot else np.nan})

    out = v.groupby(["port_key", "month", "hs6"]).apply(agg, include_groups=False).reset_index()
    out["estimated"] = True
    return out


def vessel_exposure(edges: pd.DataFrame) -> pd.DataFrame:
    """Per vessel: estimated value carried across all ports/HS6, ranked."""
    e = edges.groupby("vessel_key").agg(est_val_usd=("est_val_usd", "sum"), est_wgt_kg=("est_wgt_kg", "sum"),
                                        n_calls=("arrival", "nunique"), n_ports=("port_key", "nunique"),
                                        n_hs6=("hs6", "nunique")).reset_index()
    e["share_of_total_val"] = e["est_val_usd"] / e["est_val_usd"].sum()
    e["estimated"] = True
    return e.sort_values("est_val_usd", ascending=False).reset_index(drop=True)
