"""Forced labour in supply chains: the trade-data side of human trafficking.

Public trade data cannot see people. It can show which in-scope imports come
from origins and goods that the US Department of Labor lists as produced with
forced or child labour, and from countries the State Department ranks Tier 3 in
its Trafficking in Persons report. Company-level checks (the DHS UFLPA Entity
List) need entity names, which Census and Comtrade do not carry; use
``pacific_trade screen`` on importer or supplier lists for those.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .framework import Context, make_flags, typology

DOL = "US Department of Labor, List of Goods Produced by Child Labor or Forced Labor (TVPRA)"
TIP = "US Department of State, Trafficking in Persons Report (country tiers)"


@typology(
    id="FL-DOL-GOODS", area="forced_labor", name="Good and origin on the DOL forced/child labour list",
    question="Do in-scope imports come from a country listed by DOL for producing that good with forced or child labour?",
    method=("Matches Census origin and HS6 to crosswalks/dol_goods_hs.csv (good -> HS prefixes). Forced labour 0.6, "
            "child labour 0.45, both 0.65; +0.1 where the lane carried at least $1M."),
    sources=(DOL, "CBP Withhold Release Orders and UFLPA enforcement"),
    data_needed=("census_porths", "crosswalks/dol_goods_hs.csv"),
    innocent_explanations=("DOL lists a good-country pair, not every producer; many suppliers are clean",
                           "An HS prefix can cover more than the listed good"),
)
def dol_goods(ctx: Context) -> pd.DataFrame | None:
    if ctx.census is None or ctx.dol_goods is None or ctx.countries is None:
        return None
    rows = []
    for g in ctx.dol_goods.itertuples():
        for pre in str(g.hs_prefixes).split(";"):
            rows.append({"iso3": g.iso3, "prefix": pre.strip(), "good": g.good, "labor_type": g.labor_type})
    dol = pd.DataFrame(rows)
    c = ctx.census[ctx.census["is_country"]].merge(ctx.countries[["cty_code", "iso3", "name"]], left_on="CTY_CODE", right_on="cty_code")
    c = c.groupby(["iso3", "name", "hs6"], as_index=False)[["GEN_VAL_MO", "VES_VAL_MO"]].sum()
    hits = c.merge(dol, on="iso3")
    hits = hits[[h.startswith(p) for h, p in zip(hits["hs6"], hits["prefix"])]]
    hits = hits[hits["GEN_VAL_MO"] > 0].drop_duplicates(["iso3", "hs6"])
    if hits.empty:
        return hits
    base = hits["labor_type"].map({"forced": 0.6, "child": 0.45, "both": 0.65}).fillna(0.45)
    score = base + np.where(hits["GEN_VAL_MO"] >= 1_000_000, 0.1, 0)
    return make_flags("FL-DOL-GOODS", hits, entity_type="trade_lane", entity_id=lambda h: h["iso3"] + "|" + h["hs6"],
                      entity_label=lambda h: h["name"] + " · HS " + h["hs6"], period="all months", score=score,
                      summary=lambda h: h["good"] + " from " + h["name"] + " is DOL-listed (" + h["labor_type"] + " labour)",
                      evidence=lambda h: [f"${v:,.0f} imported (${s:,.0f} by vessel)" for v, s in zip(h["GEN_VAL_MO"], h["VES_VAL_MO"])],
                      caveats="Exposure, not a finding: ask importers for supply-chain tracing on these lanes.", hs6="hs6")


@typology(
    id="FL-TIP", area="forced_labor", name="Origin ranked Tier 3 for trafficking",
    question="Do in-scope goods come from a country the State Department ranks Tier 3?",
    method="Joins Census origins to tip_tier in crosswalks/country_risk.csv. Tier 3 scores 0.35.",
    sources=(TIP,),
    data_needed=("census_porths", "crosswalks/country_risk.csv (tip_tier)"),
    innocent_explanations=("Country tier says nothing about a given supplier or sector",),
)
def tip_tier(ctx: Context) -> pd.DataFrame | None:
    if ctx.census is None or ctx.country_risk is None or ctx.country_risk.empty:
        return None
    r = ctx.country_risk[ctx.country_risk["tip_tier"].astype(str).str.strip() == "3"]
    c = ctx.census[ctx.census["is_country"]].merge(ctx.countries[["cty_code", "iso3", "m49_comtrade", "name"]],
                                                    left_on="CTY_CODE", right_on="cty_code")
    c = c.groupby(["iso3", "m49_comtrade", "name"], as_index=False)["GEN_VAL_MO"].sum()
    hits = c.merge(r[["iso3", "as_of"]], on="iso3")
    return make_flags("FL-TIP", hits, entity_type="origin_country", entity_id=lambda h: "M49:" + h["m49_comtrade"].astype(str),
                      entity_label="name", period="all months", score=0.35,
                      summary="Origin ranked Tier 3 in the TIP report",
                      evidence=lambda h: [f"${v:,.0f} of in-scope imports; tier as of {a}" for v, a in zip(h["GEN_VAL_MO"], h["as_of"])],
                      caveats="Country-level context only.")
