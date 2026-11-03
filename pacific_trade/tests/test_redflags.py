import pandas as pd
import pytest

from pacific_trade import ais, crosswalk
from pacific_trade.redflags import Context, entity_risk, run_all, sanctions
from pacific_trade.redflags import forced_labor, tbml
from conftest import census_rows, track

COUNTRIES = crosswalk.load_countries()


def ctx(**kw):
    return Context(countries=COUNTRIES, **kw)


def months(n):
    return [f"2026-{m:02d}" for m in range(1, n + 1)]


# ---------------------------------------------------------------- identifiers
def test_imo_check_digit_and_extraction():
    assert sanctions.imo_valid("9074729") and not sanctions.imo_valid("9074728")
    assert sanctions.extract_imos("Vessel Registration Identification IMO 9074729; MMSI 123456789") == ["9074729"]
    assert sanctions.mid_valid(367000001) and not sanctions.mid_valid(111000001) and not sanctions.mid_valid(12345)
    assert sanctions.norm_name("Acme Shipping Co., Ltd.") == "ACME SHIPPING"


def test_ofac_loader(tmp_path):
    p = tmp_path / "sdn.csv"
    p.write_text('1,"DEMO TANKER","vessel","DEMO-PROG",-0- ,"ABCD","Crude Oil Tanker",-0- ,-0- ,"Panama",-0- ,"Vessel Registration Identification IMO 9074729."\n'
                 '2,"DEMO TRADING CO., LTD.","-0- ","DEMO-PROG",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n')
    wl = sanctions.load_ofac_sdn(p)
    assert wl.loc[0, "schema"] == "Vessel" and wl.loc[0, "imo"] == "9074729"
    assert wl.loc[1, "name_norm"] == "DEMO TRADING"


def test_screen_names_exact_and_fuzzy(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text("name,aliases\nDemo Trading Company Limited,Demo Trade HK\nOther Firm,\n")
    wl = sanctions.load_custom(p, "custom")
    hits = sanctions.screen_names(pd.DataFrame({"name": ["DEMO TRADING CO LTD", "Demo Tradng", "Unrelated Inc"]}), wl, threshold=0.85)
    by = hits.set_index("query")["match_score"]
    assert by["DEMO TRADING CO LTD"] == 1.0 and 0.85 <= by["Demo Tradng"] < 1 and "Unrelated Inc" not in by


# ---------------------------------------------------------------- TBML
def test_price_outlier_flags_the_odd_flow():
    rows = [("2709", m, "261000", "5700", 100_000, 100_000, 100_000, 0) for m in months(6)]
    rows += [("2704", m, "261000", "6021", 120_000, 120_000, 110_000, 0) for m in months(6)]
    rows += [("3201", "2026-06", "261000", "5880", 900_000, 900_000, 1_000, 0)]   # $900/kg vs ~$1/kg
    f = tbml.price_outliers(ctx(census=census_rows(rows)))
    assert len(f) == 1 and f.loc[0, "entity_id"] == "3201|5880|261000"
    assert "over-priced" in f.loc[0, "summary"] and f.loc[0, "severity"] == "high"


def test_mirror_gap():
    us = pd.DataFrame({"cmdCode": ["854231", "854231"], "period": ["2025", "2025"], "partnerCode": [156, 410],
                       "primaryValue": [30e6, 10.5e6]})
    px = pd.DataFrame({"cmdCode": ["854231", "854231"], "period": ["2025", "2025"], "reporterCode": [156, 410],
                       "primaryValue": [10e6, 10e6]})
    f = tbml.mirror_gaps(ctx(us_imports=us, partner_exports=px))
    assert f["entity_id"].tolist() == ["M49:156"] and "ratio 3.00" in f.loc[0, "evidence"]


def test_imports_exceed_origin_exports_skips_non_reporters():
    rows = [("2709", "2025-03", "284690", "5700", 5e6, 5e6, 1e5, 0),     # China: US imports 5M
            ("2709", "2025-03", "284690", "5830", 5e6, 5e6, 1e5, 0)]     # Taiwan: not a reporter
    world = pd.DataFrame({"cmdCode": ["284690", "854231"], "refYear": [2025, 2025], "reporterCode": [156, 156],
                          "partnerCode": [0, 0], "primaryValue": [2e6, 9e9]})
    f = tbml.imports_exceed_exports(ctx(census=census_rows(rows), world_exports=world))
    assert f["entity_id"].tolist() == ["M49:156"] and f.loc[0, "score"] > 0.6


def test_origin_surge():
    rows = []
    for i, m in enumerate(months(6)):
        late = i >= 3
        rows.append(("2709", m, "854231", "5830", 10e6, 10e6, 1e3, 0))
        rows.append(("2709", m, "854231", "5530", 8e6 if late else 0.2e6, 8e6 if late else 0.2e6, 1e3, 0))  # Laos
    world = pd.DataFrame({"cmdCode": ["854231"] * 2, "refYear": [2025] * 2, "reporterCode": [490, 418],
                          "primaryValue": [99e9, 1e6], "isAggregate": [False, False]})
    f = tbml.origin_surge(ctx(census=census_rows(rows), world_exports=world))
    assert f["entity_id"].tolist() == ["M49:418"] and "minor world exporter" in f.loc[0, "summary"]


# ---------------------------------------------------------------- sanctions
def _points():
    a = track(367000001, "2026-06-01", 12, 21.30, -157.86, 0.0, imo=9074729)
    a["vessel_name"] = "DEMO ONE"
    b = track(367000001, "2026-06-03", 12, 21.30, -157.86, 0.0, imo=9074732)   # same MMSI, second IMO
    b["vessel_name"] = "DEMO TWO"
    c = track(412000002, "2026-06-01", 3, 21.30, -157.86, 0.0, imo=9074729)    # same IMO, Chinese MID
    c["vessel_name"] = "DEMO THREE"
    return ais.normalise(pd.concat([a, b, c], ignore_index=True).astype({"imo": "Int64"}))


def test_identity_flags():
    ident = sanctions.identity_history(_points())
    f = sanctions.identity_flags(ctx(identity=ident)).set_index("entity_id")
    r = f.loc["IMO9074729"]
    assert "Flag (MMSI country prefix) changed" in r["summary"]
    assert "MMSI broadcast more than one IMO" in r["summary"]
    assert "IMO fails check digit" in f.loc["IMO9074732", "summary"]


def test_vessel_match_by_imo_and_name(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text("name,schema,imo\nLISTED TANKER,Vessel,9074729\nDEMO TWO,Vessel,\n")
    wl = sanctions.load_custom(p, "test-list")
    reg = pd.DataFrame({"mmsi": [1, 2, 3], "imo": pd.array([9074729, None, None], dtype="Int64"),
                        "vessel_name": ["SOMETHING", "DEMO TWO", "CLEAN"], "vessel_key": ["IMO9074729", "MMSI2", "MMSI3"]})
    f = sanctions.vessel_matches(ctx(registry=reg, watchlists=wl)).set_index("entity_id")
    assert f.loc["IMO9074729", "score"] == 0.95 and f.loc["MMSI2", "score"] == 0.5 and "MMSI3" not in f.index


def test_dark_gap_inside_coverage(ports):
    hnl = track(5, "2026-06-01 00:00", 6, 21.30, -157.86, 0.0)
    later = track(5, "2026-06-02 12:00", 6, 21.30, -158.05, 0.0)       # 35 h later, ~20 km west (Barbers Point)
    pts = pd.concat([hnl, later], ignore_index=True)
    g = sanctions.dark_gaps(pts, ports)
    assert len(g) == 1 and 34 < g.loc[0, "gap_h"] < 36 and g.loc[0, "moved_km"] > 5
    reg = pd.DataFrame({"mmsi": [5], "vessel_key": ["MMSI5"], "vessel_name": ["X"]})
    f = sanctions.dark_flags(ctx(gaps=g, registry=reg))
    assert f.loc[0, "entity_id"] == "MMSI5" and 0.5 < f.loc[0, "score"] < 0.75


def test_country_risk_flags():
    rows = [("2709", "2026-01", "261000", "5570", 2e6, 2e6, 1e5, 0)]
    risk = pd.DataFrame({"iso3": ["MYS"], "comprehensive_sanctions": ["0"], "fatf_status": ["grey"],
                         "basel_aml_score": ["5"], "tip_tier": ["3"], "as_of": ["TEST"]})
    c = ctx(census=census_rows(rows), country_risk=risk)
    assert sanctions.country_flags(c).loc[0, "score"] == 0.45
    assert forced_labor.tip_tier(c).loc[0, "score"] == 0.35


# ---------------------------------------------------------------- forced labour
def test_dol_goods():
    rows = [("2709", "2026-01", "854231", "5570", 3e6, 1e5, 1e2, 2.9e6),     # Malaysia ICs
            ("2709", "2026-01", "854231", "5880", 3e6, 1e5, 1e2, 2.9e6)]     # Japan: not listed
    dol = pd.read_csv(crosswalk.CROSSWALK_DIR / "dol_goods_hs.csv", dtype=str)
    f = forced_labor.dol_goods(ctx(census=census_rows(rows), dol_goods=dol))
    assert f["entity_id"].tolist() == ["MYS|854231"] and f.loc[0, "score"] == pytest.approx(0.7)


# ---------------------------------------------------------------- framework
def test_entity_risk_combines_and_dedupes():
    flags = pd.DataFrame({"typology_id": ["A", "B", "A"], "area": ["tbml"] * 3, "entity_type": ["x"] * 3,
                          "entity_id": ["e1"] * 3, "entity_label": ["E1"] * 3, "score": [0.5, 0.5, 0.4]})
    r = entity_risk(flags).iloc[0]
    assert r["risk"] == pytest.approx(0.75) and r["n_typologies"] == 2 and r["n_flags"] == 3


def test_run_all_isolates_errors_and_skips():
    bad = pd.DataFrame({"is_country": [True], "nonsense": [1]})
    flags, log = run_all(ctx(census=bad))
    st = log.set_index("typology_id")["status"]
    assert st["TBML-PRICE"].startswith("error") and st["TBML-MIRROR"].startswith("skipped")
    assert len(log) == 10 and flags.empty
