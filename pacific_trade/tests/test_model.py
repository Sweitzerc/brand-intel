import pandas as pd
import pytest

from pacific_trade import ais, allocation, crosswalk, fusion, metrics, portcalls
from conftest import census_rows, track

HNL = (21.307, -157.867)

CENSUS = census_rows([
    ("3201", "2026-06", "854231", "5830", 600, 500, 1000, 100),
    ("3201", "2026-06", "854231", "5800", 400, 300, 600, 100),
    ("3201", "2026-06", "854231", "-", 1000, 800, 1600, 200),        # total row: excluded
    ("2709", "2026-06", "284690", "5700", 2000, 2000, 50000, 0),
    ("2709", "2026-06", "284690", "6999", 10, 10, 10, 0),            # unmapped country code
])


def test_hhi():
    assert metrics.hhi(pd.Series([1.0])) == 10_000
    assert metrics.hhi(pd.Series([1.0, 1.0])) == pytest.approx(5_000)


def test_origin_and_port_hhi():
    o = metrics.origin_hhi(CENSUS).set_index(["PORT", "hs6"])
    r = o.loc[("3201", "854231")]
    assert r["n"] == 2 and r["top_origin"] == "5830"
    assert r["hhi"] == pytest.approx(10_000 * ((5 / 8) ** 2 + (3 / 8) ** 2))
    p = metrics.port_hhi(CENSUS).set_index("hs6")
    assert p.loc["284690", "top_port"] == "2709"


def test_mode_split():
    m = metrics.mode_split(CENSUS).set_index(["PORT", "hs6"])
    assert m.loc[("3201", "854231"), "vessel_share"] == pytest.approx(0.8)


def test_allocation_conserves_tonnage(ports):
    pts = pd.concat([track(1, "2026-06-02", 30, HNL[0], HNL[1], 0.0, imo=9000001),
                     track(2, "2026-06-12", 30, HNL[0], HNL[1], 0.0, imo=9000002),
                     track(3, "2026-06-20", 30, HNL[0], HNL[1], 0.0, vessel_type=80)], ignore_index=True)
    pts.loc[pts["mmsi"] == 2, "length"] = 100.0      # half the size of vessel 1
    reg = ais.vessel_registry(pts)
    calls = portcalls.detect_calls(pts, ports)
    edges, unalloc = allocation.allocate(CENSUS, calls, reg)
    hnl = edges[edges["port_key"] == "3201"]
    assert set(hnl["vessel_key"]) == {"IMO9000001", "IMO9000002"}       # tanker excluded
    assert hnl["est_wgt_kg"].sum() == pytest.approx(1600)
    w = hnl.set_index("vessel_key")["est_wgt_kg"]
    assert w["IMO9000001"] == pytest.approx(2 * w["IMO9000002"])
    assert hnl["estimated"].all()
    assert set(unalloc["port_key"]) == {"2709"}                        # no Long Beach calls in AIS
    spof = allocation.vessel_spof(edges).iloc[0]
    assert spof["top_vessel"] == "IMO9000001" and spof["top_vessel_share"] == pytest.approx(2 / 3)


def test_build_fact_joins():
    countries = crosswalk.load_countries()
    pm = pd.DataFrame({"port_key": ["3201"], "month": ["2026-06"], "n_calls": [7], "dwell_mean_h": [20.0]})
    world = pd.DataFrame({"cmdCode": ["854231"] * 2, "refYear": [2026] * 2, "reporterCode": [490, 410],
                          "primaryValue": [75.0, 25.0], "isAggregate": [False, False]})
    f = fusion.build_fact(CENSUS, pm, countries, metrics.exporter_share(world), metrics.global_exporter_hhi(world))
    tw = f[(f["PORT"] == "3201") & (f["CTY_CODE"] == "5830")].iloc[0]
    assert tw["iso3"] == "TWN" and tw["m49_comtrade"] == 490
    assert tw["ais_n_calls"] == 7 and tw["world_export_share"] == pytest.approx(0.75)
    assert tw["global_exporter_hhi"] == pytest.approx(6250)
    assert tw["commodity_group"] == "semiconductor"
    assert "-" not in set(f["CTY_CODE"])
    assert crosswalk.unmapped_countries(f["CTY_CODE"], countries) == ["6999"]


def test_wpi_enrichment(tmp_path, ports):
    p = tmp_path / "wpi.csv"
    p.write_text("World Port Index Number,Main Port Name,UN/LOCODE,Latitude,Longitude\n"
                 "99999,Honolulu,US HNL,21.31,-157.87\n")
    e = crosswalk.enrich_ports_from_wpi(ports, p).set_index("geofence_id")
    assert e.loc["USHNL", "wpi_number"] == "99999" and e.loc["USHNL", "lat"] == 21.31
    assert pd.isna(e.loc["USITO", "wpi_number"])
