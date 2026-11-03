"""Trade-based money laundering (TBML) checks.

Mis-pricing, mirror-trade gaps, and origin patterns that suggest goods are
being re-routed or mis-described. All of these work at trade-flow or country
level: Census and Comtrade carry no importer, exporter or invoice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .framework import Context, make_flags, typology

FATF_TBML = "FATF, Trade-Based Money Laundering (2006) and FATF/Egmont TBML Risk Indicators (2021)"
GFI = "Global Financial Integrity, trade mis-invoicing methodology (mirror-trade gaps)"
FINCEN = "FinCEN advisories on TBML red flags"


def _m49_names(ctx: Context) -> dict:
    names = {int(r.m49_comtrade): r.name for r in ctx.countries.itertuples()} if ctx.countries is not None else {}
    names[842] = "United States"
    return names


def _label_flow(df: pd.DataFrame) -> pd.Series:
    port = df.get("PORT_NAME", df["PORT"]).astype(str).str.title()
    cty = df.get("CTY_NAME", df["CTY_CODE"]).astype(str).str.title()
    return port + " ← " + cty + " · HS " + df["hs6"].astype(str)


# --------------------------------------------------------------------------------------
@typology(
    id="TBML-PRICE", area="tbml", name="Unit-price outlier",
    question="Is the declared value per kg of this flow far from what other flows of the same HS6 declare?",
    method=("Robust z-score of log(vessel value / vessel weight) within each HS6, using median and MAD (floored at "
            "0.1) across all ports, origins and months, on flows of at least 100 kg. Flags |z| >= 3.5 on flows worth "
            "at least min_value. Over-pricing an "
            "import moves value out of the country; under-pricing moves it in or cuts duty."),
    sources=(FATF_TBML, FINCEN),
    data_needed=("census_porths (VES_VAL_MO, VES_WGT_MO)",),
    innocent_explanations=("An HS6 code covers products with very different prices (e.g. bare die vs packaged chips)",
                           "Weight data entry errors or unit mistakes",
                           "Census disclosure rules move weight to another port",
                           "Samples, prototypes and one-off high-spec shipments"),
)
def price_outliers(ctx: Context) -> pd.DataFrame | None:
    if ctx.census is None:
        return None
    d = ctx.census[ctx.census["is_country"]].copy()
    d = d[(d["VES_VAL_MO"] > 0) & (d["VES_WGT_MO"] > 0)]
    d["usd_kg"] = d["VES_VAL_MO"] / d["VES_WGT_MO"]
    d["lp"] = np.log(d["usd_kg"])
    g = d.groupby("hs6")["lp"]
    d["med"], d["n"] = g.transform("median"), g.transform("size")
    d["mad"] = (d["lp"] - d["med"]).abs().groupby(d["hs6"]).transform("median")
    # Floor the spread so near-identical prices (or rounding in tiny weights) can't produce huge z-scores.
    d["mad"] = d["mad"].clip(lower=ctx.p("price_mad_floor", 0.1))
    d = d[(d["n"] >= ctx.p("price_min_obs", 8)) & (d["VES_WGT_MO"] >= ctx.p("price_min_kg", 100))]
    d["z"] = 0.6745 * (d["lp"] - d["med"]) / d["mad"]
    hit = d[(d["z"].abs() >= ctx.p("price_z", 3.5)) & (d["VES_VAL_MO"] >= ctx.p("min_value", 50_000))].copy()
    if hit.empty:
        return hit
    hit["median_usd_kg"] = np.exp(hit["med"])
    hit["dir"] = np.where(hit["z"] > 0, "over-priced", "under-priced")
    score = (0.4 + 0.45 * np.minimum(1, (hit["z"].abs() - 3.5) / 6.5)
             + np.where(hit["VES_VAL_MO"] >= 1_000_000, 0.1, 0))
    return make_flags(
        "TBML-PRICE", hit, entity_type="trade_flow",
        entity_id=lambda h: h["PORT"] + "|" + h["CTY_CODE"] + "|" + h["hs6"], entity_label=_label_flow,
        period="month", score=score,
        summary=lambda h: h["dir"] + " vs other HS " + h["hs6"] + " flows",
        evidence=lambda h: [f"${u:,.2f}/kg vs HS6 median ${m:,.2f}/kg (robust z {z:+.1f}, n={n}); vessel value ${v:,.0f}"
                            for u, m, z, n, v in zip(h["usd_kg"], h["median_usd_kg"], h["z"], h["n"], h["VES_VAL_MO"])],
        caveats="Price spread within one HS6 can be wide; check HS10 detail (census-pull --hs10) before escalating.",
        hs6="hs6", port="PORT")


# --------------------------------------------------------------------------------------
@typology(
    id="TBML-MIRROR", area="tbml", name="Mirror-trade gap",
    question="Does the value the US says it imported from a partner match what that partner says it exported to the US?",
    method=("Ratio of US-reported imports (Comtrade, reporter 842, flow M) to the partner's reported exports to the "
            "US (flow X), per HS code and period. Expected about 1.0-1.1 because imports include freight and insurance. "
            "Flags ratios above 1.5 or below 0.67 where either side is at least min_mirror_value."),
    sources=(GFI, FATF_TBML),
    data_needed=("comtrade_us_imports", "comtrade_partner_exports"),
    innocent_explanations=("Country of origin (US side) vs country of consignment (partner side): goods routed via a "
                           "third country appear on different lines", "Timing: shipments straddling a period end",
                           "CIF vs FOB valuation and currency conversion", "Confidential lines suppressed by one side",
                           "Re-exports (e.g. via Hong Kong or Singapore) counted differently"),
)
def mirror_gaps(ctx: Context) -> pd.DataFrame | None:
    if ctx.us_imports is None or ctx.partner_exports is None:
        return None
    us = ctx.us_imports.groupby(["cmdCode", "period", "partnerCode"], as_index=False)["primaryValue"].sum()
    us = us.rename(columns={"partnerCode": "m49", "primaryValue": "us_imports"})
    px = ctx.partner_exports.groupby(["cmdCode", "period", "reporterCode"], as_index=False)["primaryValue"].sum()
    px = px.rename(columns={"reporterCode": "m49", "primaryValue": "partner_exports"})
    for f in (us, px):
        f["m49"] = f["m49"].astype(int)
        f["period"] = f["period"].astype(str)
        f["cmdCode"] = f["cmdCode"].astype(str)
    m = us.merge(px, on=["cmdCode", "period", "m49"], how="inner")
    m = m[(m[["us_imports", "partner_exports"]].max(axis=1) >= ctx.p("min_mirror_value", 1_000_000))]
    m = m[(m["us_imports"] > 0) & (m["partner_exports"] > 0)].copy()
    m["ratio"] = m["us_imports"] / m["partner_exports"]
    lr = np.log(m["ratio"]).abs()
    hit = m[lr >= np.log(ctx.p("mirror_ratio", 1.5))].copy()
    if hit.empty:
        return hit
    lr = np.log(hit["ratio"]).abs()
    score = 0.35 + 0.6 * np.minimum(1, (lr - np.log(1.5)) / (np.log(10) - np.log(1.5)))
    names = _m49_names(ctx)
    hit["name"] = hit["m49"].map(names).fillna("M49 " + hit["m49"].astype(str))
    return make_flags(
        "TBML-MIRROR", hit, entity_type="origin_country", entity_id=lambda h: "M49:" + h["m49"].astype(str),
        entity_label="name", period="period", score=score,
        summary=lambda h: np.where(h["ratio"] > 1, "US records more than partner reports exporting",
                                   "Partner reports exporting more than US records") + " · HS " + h["cmdCode"],
        evidence=lambda h: [f"US imports ${u:,.0f} vs partner exports ${p:,.0f} (ratio {r:.2f})"
                            for u, p, r in zip(h["us_imports"], h["partner_exports"], h["ratio"])],
        caveats="Mirror gaps are common and mostly innocent; persistent, one-directional gaps across periods matter most.",
        hs6="cmdCode")


# --------------------------------------------------------------------------------------
@typology(
    id="TBML-EXCEED", area="tbml", name="Imports exceed the origin's total exports",
    question="Did the US receive more of an HS6 'made in' a country than that country reports exporting to the whole world?",
    method=("Annual sum of Census general imports by country of origin and HS6 (crosswalk ports only, so an "
            "under-count) divided by that country's Comtrade exports to World. Flags ratio > exceed_ratio, and "
            "imports of at least min_mirror_value from a reporting country that reports no exports of that HS6."),
    sources=(FATF_TBML, GFI, "CBP guidance on transshipment and false country-of-origin claims"),
    data_needed=("census_porths", "comtrade_world_exports"),
    innocent_explanations=("Origin country does not report to Comtrade for that year or HS code",
                           "Exports recorded under a different HS6 by the origin's customs",
                           "Goods produced in a free-trade zone and recorded as re-exports",
                           "Valuation differences (customs value vs FOB)"),
)
def imports_exceed_exports(ctx: Context) -> pd.DataFrame | None:
    if ctx.census is None or ctx.world_exports is None or ctx.countries is None:
        return None
    c = ctx.census[ctx.census["is_country"]].assign(year=lambda d: d["month"].str[:4].astype(int))
    c = c.groupby(["CTY_CODE", "hs6", "year"], as_index=False)["GEN_VAL_MO"].sum()
    c = c.merge(ctx.countries[["cty_code", "m49_comtrade", "name"]], left_on="CTY_CODE", right_on="cty_code")
    w = ctx.world_exports.assign(cmdCode=lambda d: d["cmdCode"].astype(str), refYear=lambda d: d["refYear"].astype(int),
                                 reporterCode=lambda d: d["reporterCode"].astype(int))
    reporters = set(zip(w["reporterCode"], w["refYear"]))
    w = w.groupby(["cmdCode", "refYear", "reporterCode"], as_index=False)["primaryValue"].sum()
    m = c.merge(w, left_on=["hs6", "year", "m49_comtrade"], right_on=["cmdCode", "refYear", "reporterCode"], how="left")
    m["reports"] = [(int(a), int(b)) in reporters for a, b in zip(m["m49_comtrade"], m["year"])]
    m = m[m["reports"] & (m["GEN_VAL_MO"] >= ctx.p("min_mirror_value", 1_000_000))].copy()
    m["ratio"] = m["GEN_VAL_MO"] / m["primaryValue"]
    hit = m[m["primaryValue"].isna() | (m["primaryValue"] <= 0) | (m["ratio"] > ctx.p("exceed_ratio", 1.1))].copy()
    if hit.empty:
        return hit
    none = hit["primaryValue"].isna() | (hit["primaryValue"] <= 0)
    score = np.where(none, 0.4, 0.6 + 0.35 * np.minimum(1, np.log(hit["ratio"].clip(lower=1)) / np.log(5)))
    return make_flags(
        "TBML-EXCEED", hit, entity_type="origin_country", entity_id=lambda h: "M49:" + h["m49_comtrade"].astype(str),
        entity_label="name", period="year", score=score,
        summary=lambda h: np.where(none, "Origin reports no exports of HS ", "US imports exceed origin's world exports of HS ") + h["hs6"],
        evidence=lambda h: [f"US imports ${u:,.0f} vs origin's reported world exports " + ("none" if pd.isna(p) or p <= 0 else f"${p:,.0f} (ratio {u / p:.2f})")
                            for u, p in zip(h["GEN_VAL_MO"], h["primaryValue"])],
        caveats="A classic sign of false origin claims or transshipment, but check the origin's Comtrade coverage first.",
        hs6="hs6")


# --------------------------------------------------------------------------------------
@typology(
    id="TBML-SURGE", area="tbml", name="Origin-share surge",
    question="Did a country suddenly become a much larger supplier of an HS6, especially one it is not known to produce?",
    method=("Per HS6, each origin's share of US vessel value in the recent window (last surge_window months) vs the "
            "earlier baseline. Flags a rise of at least surge_pp percentage points and at least 3x the baseline share. "
            "Scores higher when the country's share of world exports (Comtrade) is under 2%, i.e. a minor producer."),
    sources=(FATF_TBML, "CBP and Commerce guidance on transshipment to evade duties and export controls"),
    data_needed=("census_porths", "comtrade_world_exports (optional, raises the score)"),
    innocent_explanations=("Real supply-chain relocation (e.g. new assembly plants after tariff changes)",
                           "A single large contract or a new factory coming online",
                           "Small baselines that make shares jumpy"),
)
def origin_surge(ctx: Context) -> pd.DataFrame | None:
    if ctx.census is None:
        return None
    c = ctx.census[ctx.census["is_country"]]
    months = sorted(c["month"].unique())
    win = ctx.p("surge_window", 3)
    if len(months) < win + 3:
        return pd.DataFrame()
    recent = set(months[-win:])
    c = c.assign(window=np.where(c["month"].isin(recent), "recent", "base"))
    v = c.groupby(["hs6", "CTY_CODE", "window"])["VES_VAL_MO"].sum().unstack("window", fill_value=0)
    v = v.reindex(columns=["base", "recent"], fill_value=0)
    share = v / v.groupby(level="hs6").transform("sum")
    d = pd.DataFrame({"base_share": share["base"], "recent_share": share["recent"], "recent_val": v["recent"]}).reset_index()
    d["delta"] = d["recent_share"] - d["base_share"]
    hit = d[(d["delta"] >= ctx.p("surge_pp", 0.15)) & (d["recent_share"] >= 3 * d["base_share"])
            & (d["recent_val"] >= ctx.p("min_mirror_value", 1_000_000))].copy()
    if hit.empty:
        return hit
    hit = hit.merge(ctx.countries[["cty_code", "m49_comtrade", "name"]], left_on="CTY_CODE", right_on="cty_code", how="left")
    hit["world_share"] = np.nan
    if ctx.world_exports is not None and not ctx.world_exports.empty:
        w = ctx.world_exports.assign(cmdCode=lambda x: x["cmdCode"].astype(str))
        latest = w["refYear"].max()
        w = w[w["refYear"] == latest].groupby(["cmdCode", "reporterCode"])["primaryValue"].sum()
        ws = (w / w.groupby(level="cmdCode").transform("sum")).rename("ws").reset_index()
        hit = hit.drop(columns="world_share").merge(ws, left_on=["hs6", "m49_comtrade"], right_on=["cmdCode", "reporterCode"],
                                                    how="left").rename(columns={"ws": "world_share"})
    minor = hit["world_share"].fillna(1) < 0.02
    score = 0.35 + 0.4 * np.minimum(1, (hit["delta"] - 0.15) / 0.45) + np.where(minor, 0.2, 0)
    period = f"{months[-win]}..{months[-1]}"
    return make_flags(
        "TBML-SURGE", hit, entity_type="origin_country",
        entity_id=lambda h: "M49:" + h["m49_comtrade"].astype("Int64").astype(str), entity_label=lambda h: h["name"].fillna(h["CTY_CODE"]),
        period=period, score=score,
        summary=lambda h: "Share of US vessel imports of HS " + h["hs6"] + " jumped" + np.where(minor, " (minor world exporter)", ""),
        evidence=lambda h: [f"{b:.0%} → {r:.0%} of vessel value; recent ${v:,.0f}" + ("" if pd.isna(ws) else f"; {ws:.1%} of world exports")
                            for b, r, v, ws in zip(h["base_share"], h["recent_share"], h["recent_val"], h["world_share"])],
        caveats="Supply chains do move; corroborate with the origin's production capacity before treating this as transshipment.",
        hs6="hs6")
