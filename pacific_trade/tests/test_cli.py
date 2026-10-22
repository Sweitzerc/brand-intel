import pandas as pd

from pacific_trade import ais, cli, config
from conftest import track
from test_model import CENSUS, HNL


def test_portcalls_and_build_end_to_end():
    import datetime as dt
    pts = ais.normalise(pd.concat([track(1, "2026-06-02", 30, HNL[0], HNL[1], 0.0, imo=9000001)]))
    pts["region"] = "hawaii"
    dest = ais.out_path(dt.date(2026, 6, 2))
    dest.parent.mkdir(parents=True)
    pts.to_parquet(dest)
    out = config.data_dir() / "out"
    out.mkdir(parents=True, exist_ok=True)
    CENSUS.to_parquet(out / "census_porths.parquet")

    cli.main(["portcalls", "--start", "2026-06-01", "--end", "2026-06-03"])
    cli.main(["build", "--csv"])
    report = (out / "report.md").read_text()
    assert "MODELLED" in report and "Does it move by sea?" in report
    edges = pd.read_parquet(out / "est_vessel_edges.parquet")
    assert edges["est_wgt_kg"].sum() == 1600
