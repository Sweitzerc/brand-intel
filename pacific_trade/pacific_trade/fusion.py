"""Build the fused fact table.

Grain: Census port × month × HS6 × origin country, with
  * Census values (general, vessel, containerised, air value; vessel/air weight)
  * AIS port × month call statistics (joined on Census port code via the crosswalk)
  * Comtrade context for (origin country, HS6, year): the origin's share of world
    exports and the global exporter HHI for that HS6.

The AIS join is port × month only; nothing here says which ship carried which goods.
"""

from __future__ import annotations

import pandas as pd

from .config import commodity_group


def build_fact(census: pd.DataFrame, port_month: pd.DataFrame | None, countries: pd.DataFrame,
               exporter_share: pd.DataFrame | None = None,
               global_hhi: pd.DataFrame | None = None) -> pd.DataFrame:
    keep = ["PORT", "PORT_NAME", "month", "hs6", "CTY_CODE", "CTY_NAME", "GEN_VAL_MO", "VES_VAL_MO",
            "VES_WGT_MO", "CNT_VAL_MO", "CNT_WGT_MO", "AIR_VAL_MO", "AIR_WGT_MO"]
    f = census[census["is_country"]][[c for c in keep if c in census.columns]].copy()
    f["commodity_group"] = f["hs6"].map(commodity_group)
    f["year"] = f["month"].str[:4].astype(int)

    f = f.merge(countries[["cty_code", "iso3", "m49_comtrade"]], left_on="CTY_CODE", right_on="cty_code",
                how="left").drop(columns="cty_code")

    if port_month is not None and not port_month.empty:
        pm = port_month.rename(columns={c: f"ais_{c}" for c in port_month.columns if c not in ("port_key", "month")})
        f = f.merge(pm, left_on=["PORT", "month"], right_on=["port_key", "month"], how="left").drop(columns="port_key")

    if exporter_share is not None and not exporter_share.empty:
        es = exporter_share.assign(hs6=exporter_share["cmdCode"].astype(str), year=exporter_share["refYear"].astype(int))
        f = f.merge(es[["hs6", "year", "m49_comtrade", "world_export_share", "origin_world_exports_usd"]],
                    on=["hs6", "year", "m49_comtrade"], how="left")

    if global_hhi is not None and not global_hhi.empty:
        g = global_hhi.assign(hs6=global_hhi["cmdCode"].astype(str), year=global_hhi["refYear"].astype(int))
        g = g[["hs6", "year", "hhi", "top_exporter_m49", "top_share"]].rename(
            columns={"hhi": "global_exporter_hhi", "top_share": "global_top_exporter_share"})
        f = f.merge(g, on=["hs6", "year"], how="left")

    return f.sort_values(["month", "PORT", "hs6", "CTY_CODE"]).reset_index(drop=True)
