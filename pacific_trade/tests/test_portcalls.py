import numpy as np
import pandas as pd

from pacific_trade import portcalls
from conftest import track

HNL = (21.307, -157.867)


def test_call_detected_with_anchorage_wait(ports):
    # 3 h waiting ~15 km off Honolulu, 1 h transit, 10 h moored, 1 h leaving.
    wait = track(1, "2026-06-01 00:00", 18, HNL[0] - 0.13, HNL[1], 0.2)
    approach = track(1, "2026-06-01 03:00", 6, np.linspace(HNL[0] - 0.1, HNL[0], 6), HNL[1], 8.0, draft=11.0)
    berth = track(1, "2026-06-01 04:00", 60, HNL[0], HNL[1], 0.0, draft=np.linspace(11, 9, 60))
    leave = track(1, "2026-06-01 14:00", 6, np.linspace(HNL[0], HNL[0] - 0.3, 6), HNL[1], 10.0, draft=9.0)
    # A ship that just transits the fence at speed: no call.
    transit = track(2, "2026-06-01 06:00", 12, HNL[0], np.linspace(HNL[1] - 0.05, HNL[1] + 0.05, 12), 12.0)
    pts = pd.concat([wait, approach, berth, leave, transit], ignore_index=True)

    calls = portcalls.detect_calls(pts, ports)
    assert len(calls) == 1
    c = calls.iloc[0]
    assert c["mmsi"] == 1 and c["port_key"] == "3201" and c["geofence_id"] == "USHNL"
    assert 9.5 <= c["dwell_h"] <= 10.5
    assert 2.5 <= c["anchorage_wait_h"] <= 3.0
    assert c["arr_draft"] > c["dep_draft"]


def test_gap_splits_visits(ports):
    a = track(1, "2026-06-01 00:00", 18, HNL[0], HNL[1], 0.0)
    b = track(1, "2026-06-01 12:00", 18, HNL[0], HNL[1], 0.0)   # 9 h silence > 6 h max gap
    calls = portcalls.detect_calls(pd.concat([a, b], ignore_index=True), ports, min_dwell_h=2)
    assert len(calls) == 2


def test_la_long_beach_nearest_centre(ports):
    lb = track(5, "2026-06-01", 24, 33.754, -118.216, 0.0)
    calls = portcalls.detect_calls(lb, ports)
    assert calls["port_key"].tolist() == ["2709"]


def test_port_month_stats(ports):
    pts = pd.concat([track(1, "2026-06-01", 24, HNL[0], HNL[1], 0.0),
                     track(2, "2026-06-10", 30, HNL[0], HNL[1], 0.0, vessel_type=80)], ignore_index=True)
    calls = portcalls.detect_calls(pts, ports)
    from pacific_trade import ais
    pm = portcalls.port_month_stats(calls, ais.vessel_registry(pts))
    row = pm.set_index(["port_key", "month"]).loc[("3201", "2026-06")]
    assert row["n_calls"] == 2 and row["n_cargo_calls"] == 1 and row["n_tanker_calls"] == 1
