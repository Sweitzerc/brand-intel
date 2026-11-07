"""Tests for the GeoTrack COP logistics server.

In-memory tests always run. PostGIS tests run when GEOTRACK_TEST_DATABASE_URL
points at a PostgreSQL database with the PostGIS extension available; the
GeoTrack tables in that database are dropped and recreated.
"""

import asyncio
import os

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

os.environ["GEOTRACK_SIMULATION"] = "0"  # tests drive ticks explicitly
from geotrack import run_geotrack_app as geo  # noqa: E402

KAILUA = geo.HUBS_BY_CODE["ZONE-KAILUA-03"]
TEST_DSN = os.getenv("GEOTRACK_TEST_DATABASE_URL")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(geo.app) as c:
        yield c


def tick(c, n=1):
    for _ in range(n):
        c.portal.call(geo.simulation_tick)


def mgr_event(c, code, status, role="LOGISTICS_MANAGER", reason="ROUTINE_SCAN"):
    return c.post(f"/api/v1/shipments/{code}/events",
                  json={"actor_role": role, "new_status": status, "reason_code": reason})


def create(c, **kw):
    body = {"sender": "Acme", "recipient": "Clinic", "priority": "EXPRESS", "weight_kg": 3.5,
            "actor_role": "OPERATOR", **kw}
    r = c.post("/api/v1/shipments", json=body)
    assert r.status_code == 201, r.text
    return r.json()["package"]


# --------------------------------------------------------------------- unit
def test_transition_rules():
    assert geo.transition_error("CREATED", "PICKED_UP", "OPERATOR") is None
    assert geo.transition_error("CREATED", "DELIVERED", "OPERATOR")
    assert geo.transition_error("IN_TRANSIT", "HELD_AT_HUB", "OPERATOR") is None
    assert geo.transition_error("OUT_FOR_DELIVERY", "AT_HUB", "OPERATOR")
    assert geo.transition_error("OUT_FOR_DELIVERY", "AT_HUB", "LOGISTICS_MANAGER") is None
    assert "locked" in geo.transition_error("DELIVERED", "AT_HUB", "LOGISTICS_MANAGER")
    assert geo.transition_error("AT_HUB", "AT_HUB", "LOGISTICS_MANAGER")


def test_route_position_endpoints_hit_hubs():
    start, mid, end = (geo.route_position(p) for p in (0, 50, 100))
    assert geo.geofences_containing_memory(start["lat"], start["lng"]) == ["HUB-HNL-01"]
    assert geo.geofences_containing_memory(mid["lat"], mid["lng"]) == ["HUB-PEARL-02"]
    assert geo.geofences_containing_memory(end["lat"], end["lng"]) == ["ZONE-KAILUA-03"]


def test_eta_tracks_remaining_distance():
    assert geo.eta_minutes(100, 60) == 0
    total_km = sum(geo.LEG_KM)
    assert geo.eta_minutes(0, 60) == round(total_km)
    assert geo.eta_minutes(75, 60) < geo.eta_minutes(25, 60) < geo.eta_minutes(0, 60)


def test_generated_trigger_sql_covers_every_status():
    sql = geo._trigger_sql()
    for status in geo.VALID_TRANSITIONS:
        assert f"OLD.current_status = '{status}'" in sql


# --------------------------------------------------------------- in-memory API
def test_state_and_dashboard(client):
    state = client.get("/api/v1/state").json()
    assert state["config"]["storage_engine"] == "IN_MEMORY_FALLBACK"
    assert {p["trackingCode"] for p in state["packages"]} == {"PKG-99201", "PKG-99202", "PKG-99208"}
    for p in state["packages"]:
        assert client.get(f"/api/v1/shipments/{p['trackingCode']}/verify").json()["valid"]
    page = client.get("/")
    assert page.status_code == 200 and "GeoTrack COP" in page.text


def test_create_and_lifecycle(client):
    pkg = create(client)
    code = pkg["trackingCode"]
    assert pkg["status"] == "CREATED" and len(pkg["events"]) == 1
    for status in ("PICKED_UP", "IN_TRANSIT"):
        assert mgr_event(client, code, status, role="OPERATOR").status_code == 200
    assert client.get(f"/api/v1/shipments/{code}").json()["status"] == "IN_TRANSIT"
    v = client.get(f"/api/v1/shipments/{code}/verify").json()
    assert v["valid"] and v["eventCount"] == 3


def test_create_rejects_blank_names(client):
    r = client.post("/api/v1/shipments", json={"sender": "  ", "recipient": "x"})
    assert r.status_code == 422


def test_state_machine_violations(client):
    r = mgr_event(client, "PKG-99201", "CREATED", role="OPERATOR")
    assert r.status_code == 400 and "State Machine Violation" in r.json()["detail"]
    # operators cannot use manager overrides
    assert mgr_event(client, "PKG-99208", "AT_HUB", role="OPERATOR").status_code == 400
    assert mgr_event(client, "PKG-99208", "AT_HUB").status_code == 200
    # the API never accepts SYSTEM_AUTOMATION from a client
    r = mgr_event(client, "PKG-99201", "AT_HUB", role="SYSTEM_AUTOMATION")
    assert r.status_code == 422
    assert mgr_event(client, "PKG-NOPE", "AT_HUB").status_code == 404


def test_delivered_is_locked(client):
    assert mgr_event(client, "PKG-99208", "DELIVERED", reason="PROOF_OF_DELIVERY").status_code == 200
    pkg = client.get("/api/v1/shipments/PKG-99208").json()
    assert pkg["routeProgressPct"] == 100 and pkg["speedKph"] == 0
    r = mgr_event(client, "PKG-99208", "HELD_AT_HUB")
    assert r.status_code == 400 and "locked" in r.json()["detail"]


def test_simulation_drives_geofence_automation(client):
    before = client.get("/api/v1/shipments/PKG-99201").json()
    tick(client)
    after = client.get("/api/v1/shipments/PKG-99201").json()
    assert after["routeProgressPct"] == before["routeProgressPct"] + geo.SIM_PROGRESS_STEP
    assert after["coordinates"] != before["coordinates"]
    held = client.get("/api/v1/shipments/PKG-99202").json()
    assert held["routeProgressPct"] == 50  # HELD_AT_HUB does not move

    tick(client, 100)
    pkg = client.get("/api/v1/shipments/PKG-99201").json()
    assert pkg["status"] == "DELIVERED"
    types = [e["eventType"] for e in pkg["events"]]
    assert types[:2] == ["GEOFENCE_ARRIVAL_LOCK", "GEOFENCE_ENTRY"]
    assert all(e["role"] == "SYSTEM_AUTOMATION" for e in pkg["events"][:2])
    assert client.get("/api/v1/shipments/PKG-99201/verify").json()["valid"]


def test_paused_simulation_does_not_move(client):
    client.post("/api/v1/config/toggle?field=live_enabled")
    before = client.get("/api/v1/shipments/PKG-99201").json()["routeProgressPct"]
    tick(client, 3)
    assert client.get("/api/v1/shipments/PKG-99201").json()["routeProgressPct"] == before


def test_telemetry_ping_enters_delivery_zone(client):
    r = client.post("/api/v1/telemetry/ping", json={
        "device_id": "VEH-8842-ALPHA", "package_ids": ["PKG-99201", "PKG-NOPE"],
        "coordinates": {"lat": KAILUA["lat"] + 0.005, "lng": KAILUA["lng"]},
        "speed_kph": 40, "heading_deg": 90, "battery_pct": 80, "network_type": "LTE",
    }).json()
    assert r["updated_packages"] == ["PKG-99201"] and r["unknown_packages"] == ["PKG-NOPE"]
    assert r["geofences_intersected"] == ["ZONE-KAILUA-03"]
    pkg = client.get("/api/v1/shipments/PKG-99201").json()
    assert pkg["status"] == "OUT_FOR_DELIVERY" and pkg["events"][0]["eventType"] == "GEOFENCE_ENTRY"
    assert pkg["routeProgressPct"] >= 90  # snapped to the corridor near Kailua

    # the simulator leaves an externally tracked package alone
    tick(client)
    assert client.get("/api/v1/shipments/PKG-99201").json()["coordinates"] == pkg["coordinates"]

    # out-of-order (older) pings are ignored
    r = client.post("/api/v1/telemetry/ping", json={
        "device_id": "VEH-8842-ALPHA", "package_ids": ["PKG-99201"],
        "timestamp_utc": "2020-01-01T00:00:00Z", "coordinates": {"lat": 21.0, "lng": -157.0},
    }).json()
    assert r["stale_packages"] == ["PKG-99201"] and not r["ingested"]


def test_geofence_check_and_bbox(client):
    hits = client.get("/api/v1/geofences/check", params={"lat": KAILUA["lat"], "lng": KAILUA["lng"]}).json()
    assert [g["code"] for g in hits["geofences"]] == ["ZONE-KAILUA-03"]
    miss = client.get("/api/v1/geofences/check", params={"lat": 21.0, "lng": -157.0}).json()
    assert miss["geofences"] == []
    pearl = geo.HUBS_BY_CODE["HUB-PEARL-02"]
    box = client.get("/api/v1/packages/in-bbox", params={
        "min_lng": pearl["lng"] - 0.01, "min_lat": pearl["lat"] - 0.01,
        "max_lng": pearl["lng"] + 0.01, "max_lat": pearl["lat"] + 0.01}).json()
    assert [p["trackingCode"] for p in box["packages"]] == ["PKG-99202"]


def test_tampering_is_detected(client):
    geo.PACKAGES_STATE["PKG-99202"]["events"][-1]["notes"] = "edited after the fact"
    v = client.get("/api/v1/shipments/PKG-99202/verify").json()
    assert v["valid"] is False and v["eventIndex"] == 0


def test_websocket_snapshot(client):
    with client.websocket_connect("/ws/cop") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "COP_SNAPSHOT" and len(msg["packages"]) == 3
        create(client)
        assert len(ws.receive_json()["packages"]) == 4


# ------------------------------------------------------------------- PostGIS
needs_db = pytest.mark.skipif(not TEST_DSN, reason="GEOTRACK_TEST_DATABASE_URL not set")


def _sql(query, *args):
    import asyncpg

    async def run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    return asyncio.run(run())


@pytest.fixture
def db_client(monkeypatch):
    _sql("""DROP TABLE IF EXISTS telemetry_breadcrumbs, package_custody_events, packages,
            transport_assets, logistics_geofences CASCADE;""")
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    with TestClient(geo.app) as c:
        yield c


@needs_db
def test_db_seeds_and_persists_across_restart(db_client):
    assert db_client.get("/api/v1/health").json()["storage_engine"] == "POSTGRESQL_POSTGIS_LIVE"
    code = create(db_client)["trackingCode"]
    assert mgr_event(db_client, code, "PICKED_UP", role="OPERATOR").status_code == 200
    db_client.__exit__(None, None, None)

    with TestClient(geo.app) as c2:  # fresh process state, same database
        pkg = c2.get(f"/api/v1/shipments/{code}").json()
        assert pkg["status"] == "PICKED_UP" and len(pkg["events"]) == 2
        assert pkg["currentGeofences"] == ["HUB-HNL-01"]  # recomputed with ST_Covers on load
        for code_ in ("PKG-99201", "PKG-99202", "PKG-99208", code):
            assert c2.get(f"/api/v1/shipments/{code_}/verify").json()["valid"], code_


@needs_db
def test_db_trigger_blocks_illegal_transition(db_client):
    import asyncpg

    with pytest.raises(asyncpg.RaiseError, match="Illegal state transition"):
        _sql("UPDATE packages SET current_status = 'DELIVERED' WHERE tracking_code = 'PKG-99202';")
    _sql("UPDATE packages SET current_status = 'DELIVERED' WHERE tracking_code = 'PKG-99208';")
    with pytest.raises(asyncpg.RaiseError, match="DELIVERED and locked"):
        _sql("UPDATE packages SET current_status = 'AT_HUB' WHERE tracking_code = 'PKG-99208';")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        _sql("UPDATE package_custody_events SET notes = 'x';")


@needs_db
def test_db_rejection_surfaces_as_400_and_memory_unchanged(db_client):
    # Simulate the DB disagreeing with memory: DB thinks the package is DELIVERED.
    _sql("UPDATE packages SET current_status = 'DELIVERED' WHERE tracking_code = 'PKG-99208';")
    r = mgr_event(db_client, "PKG-99208", "HELD_AT_HUB")
    assert r.status_code == 400 and "DELIVERED and locked" in r.json()["detail"]
    pkg = db_client.get("/api/v1/shipments/PKG-99208").json()
    assert pkg["status"] == "OUT_FOR_DELIVERY" and pkg["events"][0]["newStatus"] == "OUT_FOR_DELIVERY"


@needs_db
def test_db_telemetry_uses_st_covers_and_records_breadcrumbs(db_client):
    r = db_client.post("/api/v1/telemetry/ping", json={
        "device_id": "VEH-8842-ALPHA", "package_ids": ["PKG-99201"],
        "coordinates": {"lat": KAILUA["lat"], "lng": KAILUA["lng"] + 0.01, "altitude_m": 30},
    }).json()
    assert r["storage_engine"] == "POSTGRESQL_POSTGIS_LIVE"
    assert r["geofences_intersected"] == ["ZONE-KAILUA-03"]
    crumbs = db_client.get("/api/v1/shipments/PKG-99201/breadcrumbs").json()["breadcrumbs"]
    assert len(crumbs) == 1 and crumbs[0]["altitudeM"] == 30
    status = _sql("SELECT current_status FROM packages WHERE tracking_code = 'PKG-99201';")
    assert status[0]["current_status"] == "OUT_FOR_DELIVERY"
    asset = _sql("SELECT last_ping_utc FROM transport_assets WHERE device_code = 'VEH-8842-ALPHA';")
    assert asset and asset[0]["last_ping_utc"] is not None

    tick(db_client, 3)  # other moving packages write breadcrumbs too
    assert _sql("SELECT COUNT(*) AS n FROM telemetry_breadcrumbs;")[0]["n"] > 1
    box = db_client.get("/api/v1/packages/in-bbox", params={
        "min_lng": -158.0, "min_lat": 21.2, "max_lng": -157.6, "max_lat": 21.5}).json()
    assert {p["trackingCode"] for p in box["packages"]} >= {"PKG-99201", "PKG-99202"}


def test_db_unreachable_falls_back_to_memory(monkeypatch):
    if geo.asyncpg is None:
        pytest.skip("asyncpg not installed")
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/none")
    with TestClient(geo.app) as c:
        assert c.get("/api/v1/health").json()["storage_engine"] == "IN_MEMORY_FALLBACK"
        assert len(c.get("/api/v1/state").json()["packages"]) == 3
