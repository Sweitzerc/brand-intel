"""Turn pulled inputs into the output tables (shared by `build` and `demo`)."""

from __future__ import annotations

import pandas as pd

from . import allocation, fusion, metrics, redflags


def build_tables(cen: pd.DataFrame, countries: pd.DataFrame, *, port_month=None, calls=None, registry=None,
                 world=None, us_imports=None, census_national=None, partner_exports=None, identity=None, gaps=None,
                 watchlists=None, reference: dict | None = None, redflag_params: dict | None = None,
                 method: str = "capacity") -> dict[str, pd.DataFrame]:
    es = metrics.exporter_share(world) if world is not None else None
    gh = metrics.global_exporter_hhi(world) if world is not None else None
    tables = {
        "fact_port_month_hs6_origin": fusion.build_fact(cen, port_month, countries, es, gh),
        "origin_hhi": metrics.origin_hhi(cen),
        "port_hhi": metrics.port_hhi(cen),
        "mode_split": metrics.mode_split(cen),
    }
    if port_month is not None:
        tables["port_month_ais"] = port_month
    if gh is not None:
        tables["global_exporter_hhi"] = gh
    if us_imports is not None and census_national is not None:
        tables["census_vs_comtrade"] = metrics.census_vs_comtrade(census_national, us_imports, countries)
    if calls is not None and registry is not None:
        edges, unalloc = allocation.allocate(cen, calls, registry, method=method)
        tables.update({"est_vessel_edges": edges, "est_unallocated": unalloc,
                       "est_vessel_spof": allocation.vessel_spof(edges),
                       "est_vessel_exposure": _with_names(allocation.vessel_exposure(edges), registry)})

    ref = reference if reference is not None else redflags.load_reference()
    ctx = redflags.Context(census=cen, countries=countries, world_exports=world, us_imports=us_imports,
                           partner_exports=partner_exports, registry=registry, identity=identity, gaps=gaps,
                           watchlists=watchlists, dol_goods=ref.get("dol_goods"), country_risk=ref.get("country_risk"),
                           params=redflag_params or {})
    flags, runlog = redflags.run_all(ctx)
    tables.update({"red_flags": flags, "red_flag_entities": redflags.entity_risk(flags),
                   "red_flag_runlog": runlog, "typology_library": redflags.library()})
    return tables


def _with_names(exposure: pd.DataFrame, registry: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in ("vessel_key", "vessel_name", "vessel_type") if c in registry]
    names = registry[cols].drop_duplicates("vessel_key")
    return exposure.merge(names, on="vessel_key", how="left")
