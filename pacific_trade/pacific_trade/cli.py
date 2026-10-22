"""Command-line entry point: ``python -m pacific_trade <command>``.

Typical run (one quarter, Hawaii + West Coast):

  python -m pacific_trade wpi-fetch
  python -m pacific_trade ais-fetch --start 2026-04-01 --end 2026-06-30 --workers 4
  python -m pacific_trade census-pull --start 2026-04 --end 2026-06
  python -m pacific_trade comtrade-pull --year 2025 --months 202604 202605 202606
  python -m pacific_trade portcalls --start 2026-04-01 --end 2026-06-30
  python -m pacific_trade build            # tables + report.md + dashboard.html
  python -m pacific_trade demo             # synthetic data, to preview the dashboard
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from . import ais, build, census, comtrade, crosswalk, dashboard, demo, http, portcalls, report
from .allocation import METHODS
from .config import ALL_HS4, WPI_URL, data_dir

log = logging.getLogger("pacific_trade")


def _out(name: str) -> Path:
    d = data_dir() / "out"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _ports() -> pd.DataFrame:
    ports = crosswalk.load_ports()
    wpi = data_dir() / "ref" / "wpi.csv"
    if wpi.exists():
        ports = crosswalk.enrich_ports_from_wpi(ports, wpi)
    else:
        log.warning("WPI not downloaded; using seed port coordinates (run `wpi-fetch`).")
    return ports


def cmd_wpi_fetch(a):
    dest = http.download(WPI_URL, data_dir() / "ref" / "wpi.csv")
    print(f"WPI -> {dest}")
    ports = crosswalk.enrich_ports_from_wpi(crosswalk.load_ports(), dest)
    print(ports[["schedule_d", "port_name", "unlocode", "wpi_number", "lat", "lon"]].to_string(index=False))


def cmd_ais_fetch(a):
    days = [a.start + dt.timedelta(days=i) for i in range((a.end - a.start).days + 1)]
    types = None if a.all_types else ais.CARGO_TANKER_TYPES
    failures = []
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(ais.fetch_day, d, vessel_types=types, overwrite=a.overwrite): d for d in days}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                print(f"{d}  -> {fut.result()}")
            except Exception as e:  # keep going; report at the end
                failures.append((d, e))
                print(f"{d}  FAILED: {e}", file=sys.stderr)
    if failures:
        sys.exit(f"{len(failures)} day(s) failed; re-run to retry (finished days are skipped).")


def cmd_census_pull(a):
    ports = None if a.national else sorted(_ports()["schedule_d"].dropna().unique())
    df = census.pull_porths(a.start, a.end, hs_prefixes=a.hs, ports=ports, wildcard=not a.no_wildcard)
    name = "census_porths_national.parquet" if a.national else "census_porths.parquet"
    df.to_parquet(_out(name), index=False)
    print(f"{len(df):,} rows -> {_out(name)}")
    missing = crosswalk.unmapped_countries(df.loc[df["is_country"], "CTY_CODE"], crosswalk.load_countries())
    if missing:
        print(f"Unmapped Census country codes (add to crosswalks/countries.csv): {', '.join(missing)}")
    if a.hs10:
        districts = sorted(_ports()["district"].dropna().unique())
        h = census.pull_hs10(a.start, a.end, hs_prefixes=a.hs, districts=districts)
        h.to_parquet(_out("census_hs10.parquet"), index=False)
        print(f"{len(h):,} HS10 rows -> {_out('census_hs10.parquet')}")


def cmd_comtrade_pull(a):
    cen = pd.read_parquet(_out("census_porths.parquet")) if _out("census_porths.parquet").exists() else None
    hs6 = sorted(cen["hs6"].unique()) if cen is not None else None
    cmd = hs6 or a.hs
    # Batch HS codes so the free tier's 500 calls/day goes a long way.
    world = pd.concat([comtrade.world_exports_by_reporter(a.year, cmd[i:i + 20]) for i in range(0, len(cmd), 20)],
                      ignore_index=True)
    world.to_parquet(_out("comtrade_world_exports.parquet"), index=False)
    print(f"{len(world):,} world-export rows -> {_out('comtrade_world_exports.parquet')}")
    if a.months:
        us = pd.concat([comtrade.us_imports_by_partner("M", cmd[i:i + 20], a.months) for i in range(0, len(cmd), 20)],
                       ignore_index=True)
        us.to_parquet(_out("comtrade_us_imports.parquet"), index=False)
        print(f"{len(us):,} US-import rows -> {_out('comtrade_us_imports.parquet')}")


def cmd_portcalls(a):
    pts = ais.load_range(a.start, a.end)
    reg = ais.vessel_registry(pts)
    calls = portcalls.detect_calls(pts, _ports(), moored_sog=a.moored_sog, min_dwell_h=a.min_dwell_h)
    pm = portcalls.port_month_stats(calls, reg)
    reg.to_parquet(_out("vessel_registry.parquet"), index=False)
    calls.to_parquet(_out("port_calls.parquet"), index=False)
    pm.to_parquet(_out("port_month_ais.parquet"), index=False)
    print(f"{len(pts):,} points, {len(reg):,} vessels, {len(calls):,} calls, {len(pm):,} port-months")


def _read_opt(name):
    p = _out(name)
    return pd.read_parquet(p) if p.exists() else None


def _write_outputs(tables, *, method, csv=False, demo=False):
    for name, df in tables.items():
        df.to_parquet(_out(f"{name}.parquet"), index=False)
        if csv:
            df.to_csv(_out(f"{name}.csv"), index=False)
    _out("report.md").write_text(report.render(tables, method=method))
    html = dashboard.render(tables, crosswalk.load_ports(), crosswalk.load_countries(), method=method, demo=demo)
    _out("dashboard.html").write_text(html)
    print(f"Wrote {len(tables)} tables, {_out('report.md')} and {_out('dashboard.html')}")


def cmd_build(a):
    cen = _read_opt("census_porths.parquet")
    if cen is None:
        sys.exit("census_porths.parquet missing: run `census-pull` first.")
    tables = build.build_tables(
        cen, crosswalk.load_countries(), port_month=_read_opt("port_month_ais.parquet"),
        calls=_read_opt("port_calls.parquet"), registry=_read_opt("vessel_registry.parquet"),
        world=_read_opt("comtrade_world_exports.parquet"), us_imports=_read_opt("comtrade_us_imports.parquet"),
        census_national=_read_opt("census_porths_national.parquet"), method=a.method)
    _write_outputs(tables, method=a.method, csv=a.csv)


def cmd_demo(a):
    tables = demo.demo_tables(seed=a.seed, method=a.method)
    _write_outputs(tables, method=a.method, demo=True)
    print("These are SYNTHETIC demo numbers for previewing the dashboard, not results.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="pacific_trade", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("wpi-fetch", help="Download World Port Index and enrich the port crosswalk")
    s.set_defaults(fn=cmd_wpi_fetch)

    s = sub.add_parser("ais-fetch", help="Download + Pacific-filter daily AIS files to Parquet")
    s.add_argument("--start", type=_date, required=True)
    s.add_argument("--end", type=_date, required=True)
    s.add_argument("--workers", type=int, default=2)
    s.add_argument("--all-types", action="store_true", help="Keep all vessel types, not just 70-89")
    s.add_argument("--overwrite", action="store_true")
    s.set_defaults(fn=cmd_ais_fetch)

    s = sub.add_parser("census-pull", help="Pull Census porths (port x HS6 x country x month)")
    s.add_argument("--start", required=True, help="YYYY-MM")
    s.add_argument("--end", help="YYYY-MM")
    s.add_argument("--hs", nargs="+", default=ALL_HS4)
    s.add_argument("--national", action="store_true", help="All US ports (for the Comtrade cross-check)")
    s.add_argument("--no-wildcard", action="store_true", help="Fallback if I_COMMODITY=8541* is rejected")
    s.add_argument("--hs10", action="store_true", help="Also pull district x HS10 from imports/hs")
    s.set_defaults(fn=cmd_census_pull)

    s = sub.add_parser("comtrade-pull", help="Pull Comtrade world exports (+ US monthly imports)")
    s.add_argument("--year", required=True)
    s.add_argument("--months", nargs="*", help="YYYYMM periods for US-reported monthly imports")
    s.add_argument("--hs", nargs="+", default=ALL_HS4)
    s.set_defaults(fn=cmd_comtrade_pull)

    s = sub.add_parser("portcalls", help="Detect port calls from filtered AIS and aggregate to port x month")
    s.add_argument("--start", type=_date, required=True)
    s.add_argument("--end", type=_date, required=True)
    s.add_argument("--moored-sog", type=float, default=portcalls.MOORED_SOG_KN)
    s.add_argument("--min-dwell-h", type=float, default=portcalls.MIN_DWELL_H)
    s.set_defaults(fn=cmd_portcalls)

    s = sub.add_parser("build", help="Fuse, compute metrics, run the allocation model, write report.md")
    s.add_argument("--method", choices=METHODS, default="capacity")
    s.add_argument("--csv", action="store_true", help="Also write CSV copies")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("demo", help="Write SYNTHETIC demo outputs (incl. dashboard.html) to preview the layout")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--method", choices=METHODS, default="capacity")
    s.set_defaults(fn=cmd_demo)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    a.fn(a)


if __name__ == "__main__":
    main()
