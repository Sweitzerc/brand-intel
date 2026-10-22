"""Concentration and exposure metrics feasible at the grains these sources provide.

HHI is reported on the 0-10,000 scale (US DOJ convention: >2,500 = highly concentrated).
"""

from __future__ import annotations

import pandas as pd


def hhi(values: pd.Series) -> float:
    total = values.clip(lower=0).sum()
    if not total:
        return float("nan")
    s = values.clip(lower=0) / total
    return float((s ** 2).sum() * 10_000)


def concentration(df: pd.DataFrame, by: list[str], over: str, value: str) -> pd.DataFrame:
    """HHI, member count, top member and its share of ``value`` across ``over`` within ``by``."""
    d = df.groupby(by + [over], as_index=False)[value].sum()
    d = d[d[value] > 0]
    if d.empty:
        return pd.DataFrame(columns=by + ["hhi", "n", "top", "top_share", "total"])
    g = d.groupby(by)[value]
    d = d.assign(total=g.transform("sum"))
    d["share"] = d[value] / d["total"]
    out = d.assign(sq=d["share"] ** 2).groupby(by).agg(hhi=("sq", "sum"), n=(over, "size"), total=("total", "first"))
    out["hhi"] *= 10_000
    top = d.loc[d.groupby(by)[value].idxmax()].set_index(by)
    out["top"], out["top_share"] = top[over], top["share"]
    return out[["hhi", "n", "top", "top_share", "total"]].reset_index()


def origin_hhi(census: pd.DataFrame, value="VES_VAL_MO", per_month=False) -> pd.DataFrame:
    """Supplier-country concentration of a port's imports of each HS6."""
    c = census[census["is_country"]]
    by = ["PORT", "hs6"] + (["month"] if per_month else [])
    return concentration(c, by, "CTY_CODE", value).rename(columns={"top": "top_origin"})


def port_hhi(census: pd.DataFrame, value="VES_VAL_MO", per_month=False) -> pd.DataFrame:
    """How concentrated US vessel imports of each HS6 are across ports of unlading."""
    c = census[census["is_country"]]
    by = ["hs6"] + (["month"] if per_month else [])
    return concentration(c, by, "PORT", value).rename(columns={"top": "top_port"})


def mode_split(census: pd.DataFrame) -> pd.DataFrame:
    """Vessel vs air vs containerised share of value per port × HS6.

    Answers recon risk #2: if semiconductors mostly fly, the AIS story only fits
    cable (8544) and the minerals.
    """
    c = census[census["is_country"]]
    g = c.groupby(["PORT", "hs6"], as_index=False)[["GEN_VAL_MO", "VES_VAL_MO", "AIR_VAL_MO",
                                                     "CNT_VAL_MO", "VES_WGT_MO"]].sum()
    tot = g["GEN_VAL_MO"].where(g["GEN_VAL_MO"] > 0)
    g["vessel_share"] = g["VES_VAL_MO"] / tot
    g["air_share"] = g["AIR_VAL_MO"] / tot
    g["containerised_share_of_vessel"] = g["CNT_VAL_MO"] / g["VES_VAL_MO"].where(g["VES_VAL_MO"] > 0)
    g["usd_per_kg_vessel"] = g["VES_VAL_MO"] / g["VES_WGT_MO"].where(g["VES_WGT_MO"] > 0)
    return g


def global_exporter_hhi(world_exports: pd.DataFrame) -> pd.DataFrame:
    """Concentration of world exports by reporter for each HS code and year (Comtrade)."""
    d = world_exports
    if "isAggregate" in d:
        d = d[~d["isAggregate"].astype(bool)]
    out = concentration(d, ["cmdCode", "refYear"], "reporterCode", "primaryValue")
    return out.rename(columns={"top": "top_exporter_m49", "total": "world_exports_usd"})


def exporter_share(world_exports: pd.DataFrame) -> pd.DataFrame:
    """Each reporter's share of world exports per HS code and year."""
    d = world_exports.groupby(["cmdCode", "refYear", "reporterCode"], as_index=False)["primaryValue"].sum()
    d["world_export_share"] = d["primaryValue"] / d.groupby(["cmdCode", "refYear"])["primaryValue"].transform("sum")
    return d.rename(columns={"reporterCode": "m49_comtrade", "primaryValue": "origin_world_exports_usd"})


def census_vs_comtrade(census_national: pd.DataFrame, us_imports: pd.DataFrame,
                       countries: pd.DataFrame) -> pd.DataFrame:
    """Compare Census general imports with Comtrade US-reported imports by HS6 × origin × month.

    Only meaningful when ``census_national`` covers all ports (no PORT filter).
    """
    c = census_national[census_national["is_country"]]
    c = c.groupby(["hs6", "CTY_CODE", "month"], as_index=False)["GEN_VAL_MO"].sum()
    c = c.merge(countries[["cty_code", "m49_comtrade"]], left_on="CTY_CODE", right_on="cty_code", how="left")
    u = us_imports.assign(month=us_imports["period"].astype(str).str[:4] + "-" + us_imports["period"].astype(str).str[4:6],
                          hs6=us_imports["cmdCode"].astype(str))
    u = u.groupby(["hs6", "partnerCode", "month"], as_index=False)["primaryValue"].sum()
    m = c.merge(u, left_on=["hs6", "m49_comtrade", "month"], right_on=["hs6", "partnerCode", "month"], how="outer")
    m["ratio_census_to_comtrade"] = m["GEN_VAL_MO"] / m["primaryValue"]
    return m
