"""Render the build outputs as one self-contained, interactive HTML dashboard.

The page has no external dependencies (inline CSS/JS/SVG), so it opens offline,
can be emailed, or can be hosted as a static page. Filters re-aggregate the
embedded tables in the browser; every chart has a table view.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

TEMPLATE = Path(__file__).with_name("dashboard_template.html")

HS4_LABELS = {
    "8541": "Semiconductor devices, LEDs, PV cells", "8542": "Integrated circuits",
    "8543": "Electrical machines n.e.s.", "8544": "Insulated wire & cable",
    "2609": "Tin ores", "2610": "Chromium ores", "2611": "Tungsten ores", "2612": "Uranium/thorium ores",
    "2613": "Molybdenum ores", "2614": "Titanium ores", "2615": "Nb/Ta/V/Zr ores", "2616": "Precious-metal ores",
    "2617": "Other ores (incl. antimony)", "2618": "Granulated slag", "2619": "Slag, dross, scalings",
    "2620": "Metal-bearing ash & residues", "2621": "Other slag & ash", "2846": "Rare-earth compounds",
}


def _cols(df: pd.DataFrame | None, cols: list[str], rename: dict | None = None, digits: int | None = None) -> dict:
    """Column-oriented JSON-safe payload: {col: [values...]}."""
    if df is None or df.empty:
        return {(rename or {}).get(c, c): [] for c in cols}
    out = {}
    for c in cols:
        s = df[c] if c in df else pd.Series([None] * len(df))
        if pd.api.types.is_float_dtype(s) and digits is not None:
            s = s.round(digits)
        vals = s.astype(object).where(s.notna(), None).tolist()
        vals = [v.item() if isinstance(v, np.generic) else v for v in vals]
        out[(rename or {}).get(c, c)] = vals
    return out


def payload(tables: dict[str, pd.DataFrame], ports: pd.DataFrame, countries: pd.DataFrame, *,
            method: str, demo: bool) -> dict:
    fact = tables["fact_port_month_hs6_origin"]
    pm = tables.get("port_month_ais")
    edges = tables.get("est_vessel_edges")
    if edges is not None and not edges.empty:
        edges = edges.groupby(["vessel_key", "port_key", "month", "hs6"], as_index=False)[
            ["est_val_usd", "est_wgt_kg"]].sum()
    expo = tables.get("est_vessel_exposure")
    unalloc = tables.get("est_unallocated")

    port_meta = ports.dropna(subset=["schedule_d"]).drop_duplicates("schedule_d")
    names = {str(r.m49_comtrade): r.name for r in countries.itertuples()}
    names["842"] = "United States"
    vessels = {}
    if expo is not None and not expo.empty:
        for r in expo.itertuples():
            vessels[r.vessel_key] = {"name": getattr(r, "vessel_name", None) or r.vessel_key,
                                     "type": None if pd.isna(getattr(r, "vessel_type", np.nan)) else int(r.vessel_type)}
    return {
        "meta": {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                 "demo": demo, "method": method},
        "fact": _cols(fact, ["PORT", "month", "hs6", "CTY_CODE", "CTY_NAME", "commodity_group", "GEN_VAL_MO",
                             "VES_VAL_MO", "VES_WGT_MO", "AIR_VAL_MO", "CNT_VAL_MO"],
                      {"PORT": "port", "CTY_CODE": "cty", "CTY_NAME": "ctyName", "commodity_group": "group",
                       "GEN_VAL_MO": "gen", "VES_VAL_MO": "ves", "VES_WGT_MO": "vesKg", "AIR_VAL_MO": "air",
                       "CNT_VAL_MO": "cnt"}, digits=0),
        "ports": {r.schedule_d: {"name": r.port_name.title(), "region": r.region} for r in port_meta.itertuples()},
        "ais": _cols(pm, ["port_key", "month", "n_calls", "n_cargo_calls", "n_vessels", "dwell_mean_h",
                          "anchorage_wait_mean_h"],
                     {"port_key": "port", "n_calls": "calls", "n_cargo_calls": "cargoCalls", "n_vessels": "vessels",
                      "dwell_mean_h": "dwell", "anchorage_wait_mean_h": "wait"}, digits=2),
        "global": _cols(tables.get("global_exporter_hhi"), ["cmdCode", "refYear", "hhi", "n", "top_exporter_m49",
                                                           "top_share"],
                        {"cmdCode": "hs6", "refYear": "year", "top_exporter_m49": "top", "top_share": "topShare"},
                        digits=4),
        "edges": _cols(edges, ["vessel_key", "port_key", "month", "hs6", "est_val_usd", "est_wgt_kg"],
                       {"vessel_key": "v", "port_key": "port", "est_val_usd": "val", "est_wgt_kg": "kg"}, digits=0),
        "unalloc": _cols(unalloc, ["port_key", "month", "hs6", "VES_VAL_MO"],
                         {"port_key": "port", "VES_VAL_MO": "val"}, digits=0),
        "vessels": vessels,
        "m49": names,
        "hs4": HS4_LABELS,
    }


def render(tables: dict[str, pd.DataFrame], ports: pd.DataFrame, countries: pd.DataFrame, *,
           method: str = "capacity", demo: bool = False) -> str:
    data = json.dumps(payload(tables, ports, countries, method=method, demo=demo), separators=(",", ":"))
    data = data.replace("</", "<\\/")  # keep the JSON from closing its <script> tag
    return TEMPLATE.read_text().replace("/*__DATA__*/{}", data)
