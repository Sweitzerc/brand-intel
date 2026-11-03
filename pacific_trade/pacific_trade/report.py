"""Render the build outputs as a short Markdown report."""

from __future__ import annotations

import pandas as pd

from .config import commodity_group


def _md(df: pd.DataFrame, n=10, floatfmt="{:,.2f}") -> str:
    if df is None or df.empty:
        return "_no rows_\n"
    d = df.head(n).copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else floatfmt.format(v))
    cols = [str(c) for c in d.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in d.astype(str).itertuples(index=False)]
    return "\n".join(lines) + "\n"


DEMO_BANNER = ("> **SYNTHETIC DEMO DATA.** Every number below is random and only shows the layout. "
               "Country, port and vessel names appear, but nothing here is a finding about them.")


def render(tables: dict[str, pd.DataFrame], method: str, demo: bool = False) -> str:
    fact = tables["fact_port_month_hs6_origin"]
    out = ["# Pacific semiconductor & critical-mineral shipments: build report", ""]
    if demo:
        out += [DEMO_BANNER, ""]
    if not fact.empty:
        out += [f"Months: {fact['month'].min()} to {fact['month'].max()} · ports: {fact['PORT'].nunique()} · "
                f"HS6 lines: {fact['hs6'].nunique()} · origin countries: {fact['CTY_CODE'].nunique()}", ""]
        unmapped = fact.loc[fact["m49_comtrade"].isna(), "CTY_CODE"].unique()
        if len(unmapped):
            out += [f"Origin codes with no Comtrade mapping: {', '.join(sorted(unmapped))}", ""]

    ms = tables["mode_split"].assign(group=lambda d: d["hs6"].map(commodity_group))
    by_group = ms.groupby("group")[["GEN_VAL_MO", "VES_VAL_MO", "AIR_VAL_MO"]].sum()
    by_group["vessel_share"] = by_group["VES_VAL_MO"] / by_group["GEN_VAL_MO"]
    by_group["air_share"] = by_group["AIR_VAL_MO"] / by_group["GEN_VAL_MO"]
    out += ["## Does it move by sea? (recon risk #2)", "",
            "Value share by mode, all selected ports. A low vessel share means AIS says little about that group.", "",
            _md(by_group.reset_index()), ""]

    out += ["## Most concentrated origin mix by port × HS6 (vessel value)", "",
            _md(tables["origin_hhi"].sort_values(["hhi", "total"], ascending=False)), ""]
    out += ["## Port concentration of US vessel imports by HS6", "",
            _md(tables["port_hhi"].sort_values("total", ascending=False)), ""]
    if "global_exporter_hhi" in tables:
        out += ["## Global exporter concentration (Comtrade)", "",
                _md(tables["global_exporter_hhi"].sort_values("hhi", ascending=False)), ""]
    if "red_flags" in tables:
        ents, log = tables["red_flag_entities"], tables["red_flag_runlog"]
        out += ["## Red flags (leads for review, not findings)", "",
                "Each flag matches a documented typology. Most have innocent explanations; see "
                "`typology_library` for each check's sources and caveats.", "",
                _md(log[["typology_id", "name", "status", "n_flags"]], n=50), "",
                "Highest-risk entities:", "",
                _md(ents[["entity_type", "entity_label", "risk", "severity", "typologies"]], n=15, floatfmt="{:.2f}"), ""]
    if "est_vessel_exposure" in tables:
        unalloc = tables["est_unallocated"]
        out += [f"## Vessel exposure: MODELLED ({method} allocation)", "",
                "These figures spread Census port-month vessel tonnage over calling dry-cargo ships in proportion "
                "to a size proxy. No source in scope says what any ship carried; treat these as estimates.", "",
                _md(tables["est_vessel_exposure"]), "",
                f"Port-month-HS6 cells with tonnage but no detected cargo call: {len(unalloc)} "
                f"(${unalloc['VES_VAL_MO'].sum():,.0f} vessel value left unallocated).", ""]
    return "\n".join(out)
