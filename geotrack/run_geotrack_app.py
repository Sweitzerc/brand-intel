#!/usr/bin/env python3
"""
GeoTrack COP — near-real-time logistics & package tracking server.

FastAPI + WebSocket live dashboard, with an optional PostgreSQL/PostGIS
backend (asyncpg). Without DATABASE_URL it runs entirely in memory.

Quickstart (in-memory):
    pip install -r geotrack/requirements.txt
    python geotrack/run_geotrack_app.py

Quickstart (PostGIS):
    docker compose -f geotrack/docker-compose.yml up -d
    export DATABASE_URL="postgresql://postgres:password@localhost:5432/logistics_db"
    python geotrack/run_geotrack_app.py

Environment variables:
    DATABASE_URL         asyncpg DSN; omit for in-memory mode
    HOST / PORT          bind address (default 127.0.0.1:8000)
    GEOTRACK_SIMULATION  "0" disables the 2-second simulated telemetry loop
    GEOTRACK_NO_BROWSER  "1" skips opening a browser tab on start
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import random
import threading
import webbrowser
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

try:  # Optional PostgreSQL / PostGIS async driver
    import asyncpg
except ImportError:  # pragma: no cover - depends on environment
    asyncpg = None

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 1. STATE MACHINE, ROLES & GEOFENCES
# ---------------------------------------------------------------------------
PackageStatus = Literal[
    "CREATED",
    "PICKED_UP",
    "IN_TRANSIT",
    "AT_HUB",
    "HELD_AT_HUB",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
    "EXCEPTION_RETURN",
]
ALL_STATUSES: List[str] = list(PackageStatus.__args__)  # type: ignore[attr-defined]

RoleType = Literal["LOGISTICS_MANAGER", "OPERATOR", "SYSTEM_AUTOMATION"]
HumanRole = Literal["LOGISTICS_MANAGER", "OPERATOR"]

VALID_TRANSITIONS: Dict[str, List[str]] = {
    "CREATED": ["PICKED_UP", "AT_HUB", "HELD_AT_HUB"],
    "PICKED_UP": ["IN_TRANSIT", "AT_HUB", "HELD_AT_HUB"],
    "IN_TRANSIT": ["AT_HUB", "OUT_FOR_DELIVERY", "HELD_AT_HUB"],
    "AT_HUB": ["IN_TRANSIT", "OUT_FOR_DELIVERY", "HELD_AT_HUB"],
    "HELD_AT_HUB": ["IN_TRANSIT", "AT_HUB", "OUT_FOR_DELIVERY"],
    "OUT_FOR_DELIVERY": ["DELIVERED", "HELD_AT_HUB"],
    "DELIVERED": [],
    "EXCEPTION_RETURN": ["AT_HUB"],
}

# A logistics manager may force any non-delivered package into these states.
MANAGER_OVERRIDE_TARGETS = ("HELD_AT_HUB", "AT_HUB", "EXCEPTION_RETURN")
MOVING_STATUSES = ("IN_TRANSIT", "OUT_FOR_DELIVERY")

ACTOR_CODES = {
    "LOGISTICS_MANAGER": "USR-4410-MGR",
    "OPERATOR": "USR-1029-OPR",
    "SYSTEM_AUTOMATION": "SYS-GEOFENCE-01",
}

GEOFENCE_HUBS = [
    {
        "code": "HUB-HNL-01",
        "name": "Honolulu Port Primary Hub",
        "type": "SORTING_HUB",
        "lat": 21.3069,
        "lng": -157.8583,
        "radius_km": 2.2,
    },
    {
        "code": "HUB-PEARL-02",
        "name": "Pearl City Regional Depot",
        "type": "REGIONAL_DEPOT",
        "lat": 21.3890,
        "lng": -157.9410,
        "radius_km": 2.0,
    },
    {
        "code": "ZONE-KAILUA-03",
        "name": "Windward Delivery Terminal",
        "type": "CUSTOMER_DELIVERY_ZONE",
        "lat": 21.3972,
        "lng": -157.7394,
        "radius_km": 2.5,
    },
]
HUBS_BY_CODE = {h["code"]: h for h in GEOFENCE_HUBS}

# Delivery corridor the simulator drives along: 0% = Honolulu, 50% = Pearl City,
# 100% = Kailua.
ROUTE_WAYPOINTS = [HUBS_BY_CODE[c] for c in ("HUB-HNL-01", "HUB-PEARL-02", "ZONE-KAILUA-03")]

SIM_TICK_SECONDS = 2.0
SIM_PROGRESS_STEP = 1
# A real device ping takes precedence over the simulator for this long.
EXTERNAL_PING_HOLD_SECONDS = 30.0


def transition_error(current: str, target: str, role: str) -> Optional[str]:
    """Return a human-readable reason the transition is illegal, or None."""
    if current == "DELIVERED":
        return "is already DELIVERED and locked."
    if target == current:
        return f"is already {current}."
    if target in VALID_TRANSITIONS.get(current, []):
        return None
    if role == "LOGISTICS_MANAGER" and target in MANAGER_OVERRIDE_TARGETS:
        return None
    return f"cannot transition directly from {current} to {target}."


# ---------------------------------------------------------------------------
# 2. PYDANTIC PAYLOADS
# ---------------------------------------------------------------------------
class CoordinatesModel(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)
    altitude_m: Optional[float] = 12.0
    accuracy_m: Optional[float] = 4.0


class TelemetryPingPayload(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=64)
    package_ids: List[str]
    timestamp_utc: Optional[datetime] = None
    coordinates: CoordinatesModel
    speed_kph: float = Field(default=60.0, ge=0.0, le=999.0)
    heading_deg: float = Field(default=45.0, ge=0.0, le=360.0)
    battery_pct: int = Field(default=85, ge=0, le=100)
    network_type: str = Field(default="5G", max_length=16)


class CreateShipmentPayload(BaseModel):
    sender: str = Field(..., min_length=1, max_length=150)
    recipient: str = Field(..., min_length=1, max_length=150)
    priority: Literal["STANDARD", "EXPRESS", "CRITICAL"] = "EXPRESS"
    weight_kg: float = Field(default=15.0, gt=0, le=99999)
    actor_role: HumanRole = "OPERATOR"


class CustodyUpdatePayload(BaseModel):
    actor_role: HumanRole
    new_status: PackageStatus
    reason_code: str = Field(default="ROUTINE_SCAN", min_length=1, max_length=64)
    notes: str = Field(default="", max_length=2000)


# ---------------------------------------------------------------------------
# 3. HELPERS: TIME, HASH CHAIN, GEOMETRY
# ---------------------------------------------------------------------------
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def compute_event_hash(prev_hash: Optional[str], tracking_code: str, evt: dict) -> str:
    """SHA-256 over the event and the previous event's hash (tamper-evident chain)."""
    raw = "|".join(
        str(x)
        for x in (
            prev_hash or "GENESIS",
            tracking_code,
            evt["eventType"],
            evt["previousStatus"],
            evt["newStatus"],
            evt["actor"],
            evt["reasonCode"],
            evt["notes"],
            evt["timestamp"],
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_chain(pkg: dict) -> dict:
    """Recompute every hash oldest -> newest and report the first mismatch."""
    prev = None
    for idx, evt in enumerate(reversed(pkg["events"])):
        expected = compute_event_hash(prev, pkg["trackingCode"], evt)
        if expected != evt["hash"]:
            return {"valid": False, "brokenAtEventId": evt["id"], "eventIndex": idx}
        prev = evt["hash"]
    return {"valid": True, "eventCount": len(pkg["events"]), "headHash": prev}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dl)
    return round((math.degrees(math.atan2(x, y)) + 360) % 360, 1)


def route_position(progress_pct: float) -> dict:
    """Point on the corridor for a progress %, plus the heading of that leg."""
    p = max(0.0, min(100.0, float(progress_pct)))
    a, b, r = (
        (ROUTE_WAYPOINTS[0], ROUTE_WAYPOINTS[1], p / 50)
        if p <= 50
        else (ROUTE_WAYPOINTS[1], ROUTE_WAYPOINTS[2], (p - 50) / 50)
    )
    return {
        "lat": a["lat"] + (b["lat"] - a["lat"]) * r,
        "lng": a["lng"] + (b["lng"] - a["lng"]) * r,
        "heading": bearing_deg(a["lat"], a["lng"], b["lat"], b["lng"]),
    }


LEG_KM = [
    haversine_km(a["lat"], a["lng"], b["lat"], b["lng"])
    for a, b in zip(ROUTE_WAYPOINTS, ROUTE_WAYPOINTS[1:])
]


def remaining_route_km(progress_pct: float) -> float:
    p = max(0.0, min(100.0, float(progress_pct)))
    if p <= 50:
        return LEG_KM[0] * (50 - p) / 50 + LEG_KM[1]
    return LEG_KM[1] * (100 - p) / 50


def eta_minutes(progress_pct: float, speed_kph: float) -> int:
    km = remaining_route_km(progress_pct)
    if km <= 0:
        return 0
    return max(1, round(km / max(speed_kph, 1.0) * 60))


def nearest_route_progress(lat: float, lng: float) -> int:
    """Progress % of the corridor point closest to (lat, lng)."""
    return min(
        range(101),
        key=lambda p: haversine_km(lat, lng, *(lambda q: (q["lat"], q["lng"]))(route_position(p))),
    )


def geofences_containing_memory(lat: float, lng: float) -> List[str]:
    return [
        h["code"]
        for h in GEOFENCE_HUBS
        if haversine_km(lat, lng, h["lat"], h["lng"]) <= h["radius_km"]
    ]


# ---------------------------------------------------------------------------
# 4. IN-MEMORY HOT STATE (always the source for the live UI)
# ---------------------------------------------------------------------------
def _seed_event(code, minutes_ago, role, event_type, prev, new, reason, notes):
    return {
        "minutes_ago": minutes_ago,
        "actor": ACTOR_CODES[role],
        "role": role,
        "eventType": event_type,
        "previousStatus": prev,
        "newStatus": new,
        "reasonCode": reason,
        "notes": notes,
    }


SEED_PACKAGES = [
    {
        "trackingCode": "PKG-99201",
        "sender": "Pacific Medical Supply",
        "recipient": "Tripler Logistics Wing",
        "originHub": "HUB-HNL-01",
        "destinationHub": "ZONE-KAILUA-03",
        "status": "IN_TRANSIT",
        "priority": "CRITICAL",
        "weightKg": 18.4,
        "vehicleId": "VEH-8842-ALPHA",
        "speedKph": 68.5,
        "batteryPct": 86,
        "networkType": "5G",
        "etaMinutes": 24,
        "routeProgressPct": 30,
        "seed_events": [
            _seed_event("PKG-99201", 40, "OPERATOR", "BARCODE_INGEST", None, "CREATED",
                        "MANIFEST_UPLOAD", "Initial manifest verified at Honolulu Port."),
            _seed_event("PKG-99201", 30, "OPERATOR", "CUSTODY_SCAN", "CREATED", "PICKED_UP",
                        "ROUTINE_SCAN", "Picked up by VEH-8842-ALPHA."),
            _seed_event("PKG-99201", 25, "OPERATOR", "CUSTODY_SCAN", "PICKED_UP", "IN_TRANSIT",
                        "DEPARTED_ORIGIN", "Departed Honolulu Port toward Pearl City."),
        ],
    },
    {
        "trackingCode": "PKG-99202",
        "sender": "AeroTech Avionics",
        "recipient": "Pearl Harbor Hangar 4",
        "originHub": "HUB-HNL-01",
        "destinationHub": "HUB-PEARL-02",
        "status": "HELD_AT_HUB",
        "priority": "EXPRESS",
        "weightKg": 54.0,
        "vehicleId": "VEH-3109-BRAVO",
        "speedKph": 0.0,
        "batteryPct": 94,
        "networkType": "LTE",
        "etaMinutes": 95,
        "routeProgressPct": 50,
        "seed_events": [
            _seed_event("PKG-99202", 120, "OPERATOR", "BARCODE_INGEST", None, "CREATED",
                        "MANIFEST_UPLOAD", "Manifest received from AeroTech."),
            _seed_event("PKG-99202", 110, "OPERATOR", "CUSTODY_SCAN", "CREATED", "PICKED_UP",
                        "ROUTINE_SCAN", "Picked up by VEH-3109-BRAVO."),
            _seed_event("PKG-99202", 100, "OPERATOR", "CUSTODY_SCAN", "PICKED_UP", "IN_TRANSIT",
                        "DEPARTED_ORIGIN", "Departed Honolulu Port."),
            _seed_event("PKG-99202", 60, "LOGISTICS_MANAGER", "STATUS_OVERRIDE", "IN_TRANSIT",
                        "HELD_AT_HUB", "WEATHER_DELAY",
                        "H-1 Westbound lane closure; holding at Pearl City Depot."),
        ],
    },
    {
        "trackingCode": "PKG-99208",
        "sender": "Island Cold Chain Co.",
        "recipient": "Kailua Regional Clinic",
        "originHub": "HUB-PEARL-02",
        "destinationHub": "ZONE-KAILUA-03",
        "status": "OUT_FOR_DELIVERY",
        "priority": "STANDARD",
        "weightKg": 11.2,
        "vehicleId": "VEH-7714-CHARLIE",
        "speedKph": 49.2,
        "batteryPct": 71,
        "networkType": "5G",
        "etaMinutes": 9,
        "routeProgressPct": 81,
        "seed_events": [
            _seed_event("PKG-99208", 90, "OPERATOR", "BARCODE_INGEST", None, "CREATED",
                        "MANIFEST_UPLOAD", "Cold-chain manifest verified at Pearl City."),
            _seed_event("PKG-99208", 80, "OPERATOR", "CUSTODY_SCAN", "CREATED", "AT_HUB",
                        "ROUTINE_SCAN", "Inducted at Pearl City Regional Depot."),
            _seed_event("PKG-99208", 70, "OPERATOR", "CUSTODY_SCAN", "AT_HUB", "IN_TRANSIT",
                        "DEPARTED_ORIGIN", "Loaded onto VEH-7714-CHARLIE."),
            _seed_event("PKG-99208", 15, "LOGISTICS_MANAGER", "STATUS_OVERRIDE", "IN_TRANSIT",
                        "OUT_FOR_DELIVERY", "ROUTINE_SCAN", "Released for final-mile delivery."),
        ],
    },
]

PACKAGES_STATE: Dict[str, dict] = {}
SIMULATION_CONFIG = {
    "live_enabled": True,
    "kalman_snap": True,
    "storage_engine": "IN_MEMORY_FALLBACK",
}
STATE_LOCK = asyncio.Lock()


def new_event(pkg: dict, role: str, event_type: str, prev: Optional[str], new: str,
              reason: str, notes: str, when: Optional[datetime] = None) -> dict:
    ts = iso(when or utc_now())
    evt = {
        "id": "evt-" + hashlib.sha1(f"{pkg['trackingCode']}{ts}{random.random()}".encode()).hexdigest()[:12],
        "timestamp": ts,
        "actor": ACTOR_CODES[role],
        "role": role,
        "eventType": event_type,
        "previousStatus": prev,
        "newStatus": new,
        "reasonCode": reason,
        "notes": notes,
        "lat": pkg["coordinates"]["lat"],
        "lng": pkg["coordinates"]["lng"],
    }
    prev_hash = pkg["events"][0]["hash"] if pkg["events"] else None
    evt["hash"] = compute_event_hash(prev_hash, pkg["trackingCode"], evt)
    return evt


def build_seed_state() -> Dict[str, dict]:
    now = utc_now()
    state: Dict[str, dict] = {}
    for seed in copy.deepcopy(SEED_PACKAGES):
        events = seed.pop("seed_events")
        pos = route_position(seed["routeProgressPct"])
        pkg = {
            **seed,
            "coordinates": {"lat": round(pos["lat"], 5), "lng": round(pos["lng"], 5)},
            "headingDeg": pos["heading"],
            "etaMinutes": (
                eta_minutes(seed["routeProgressPct"], seed["speedKph"])
                if seed["status"] in MOVING_STATUSES else seed["etaMinutes"]
            ),
            "lastPingUtc": iso(now),
            "currentGeofences": geofences_containing_memory(pos["lat"], pos["lng"]),
            "events": [],
        }
        for e in events:
            when = datetime.fromtimestamp(now.timestamp() - e["minutes_ago"] * 60, timezone.utc)
            evt = new_event(pkg, e["role"], e["eventType"], e["previousStatus"], e["newStatus"],
                            e["reasonCode"], e["notes"], when)
            pkg["events"].insert(0, evt)
        state[pkg["trackingCode"]] = pkg
    return state


# Per-package wall-clock time until which the simulator leaves it alone
# because a real device is reporting for it. Not part of the public payload.
EXTERNAL_HOLD_UNTIL: Dict[str, float] = {}


def reset_state() -> None:
    PACKAGES_STATE.clear()
    PACKAGES_STATE.update(build_seed_state())
    EXTERNAL_HOLD_UNTIL.clear()
    SIMULATION_CONFIG.update(live_enabled=True, kalman_snap=True, storage_engine="IN_MEMORY_FALLBACK")


reset_state()


def snapshot() -> dict:
    return {
        "type": "COP_SNAPSHOT",
        "packages": list(PACKAGES_STATE.values()),
        "geofences": GEOFENCE_HUBS,
        "route": [{"code": w["code"], "lat": w["lat"], "lng": w["lng"]} for w in ROUTE_WAYPOINTS],
        "config": SIMULATION_CONFIG,
        "transitions": VALID_TRANSITIONS,
        "managerOverrides": list(MANAGER_OVERRIDE_TARGETS),
        "serverTime": iso(utc_now()),
    }


# ---------------------------------------------------------------------------
# 5. POSTGRESQL + POSTGIS REPOSITORY
# ---------------------------------------------------------------------------
class TransitionRejected(Exception):
    """The database refused a status change (state-machine trigger)."""


def _trigger_sql() -> str:
    """PL/pgSQL state-machine guard generated from VALID_TRANSITIONS so the
    database and the Python service can never disagree."""
    branches = []
    for status, targets in VALID_TRANSITIONS.items():
        allowed = sorted(set(targets) | set(MANAGER_OVERRIDE_TARGETS)) if status != "DELIVERED" else []
        allowed_sql = ", ".join(f"'{t}'" for t in allowed) or "NULL"
        branches.append(
            f"    ELSIF OLD.current_status = '{status}' AND NEW.current_status NOT IN ({allowed_sql}) THEN\n"
            f"        RAISE EXCEPTION 'Illegal state transition from {status} to %', NEW.current_status;"
        )
    body = "\n".join(branches).replace("    ELSIF", "    IF", 1)
    return f"""
CREATE OR REPLACE FUNCTION validate_package_status_transition()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.current_status = 'DELIVERED' THEN
        RAISE EXCEPTION 'Package % is already DELIVERED and locked.', OLD.tracking_code;
    END IF;
{body}
    END IF;
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_package_state ON packages;
CREATE TRIGGER trg_enforce_package_state
BEFORE UPDATE OF current_status ON packages
FOR EACH ROW
WHEN (OLD.current_status IS DISTINCT FROM NEW.current_status)
EXECUTE FUNCTION validate_package_status_transition();
"""


BOOTSTRAP_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS logistics_geofences (
    geofence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hub_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(150) NOT NULL,
    zone_type VARCHAR(64) NOT NULL,
    boundary GEOMETRY(Polygon, 4326) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_geofences_boundary_gist
    ON logistics_geofences USING GIST (boundary);

CREATE TABLE IF NOT EXISTS transport_assets (
    asset_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_code VARCHAR(64) UNIQUE NOT NULL,
    carrier_name VARCHAR(100) NOT NULL DEFAULT 'GeoTrack Fleet',
    last_known_location GEOMETRY(Point, 4326),
    last_speed_kph NUMERIC(6,2),
    last_heading_deg NUMERIC(5,2),
    last_battery_pct SMALLINT CHECK (last_battery_pct BETWEEN 0 AND 100),
    last_network_type VARCHAR(16),
    last_ping_utc TIMESTAMPTZ,
    is_online BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_assets_location_gist
    ON transport_assets USING GIST (last_known_location);

CREATE TABLE IF NOT EXISTS packages (
    package_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tracking_code VARCHAR(32) UNIQUE NOT NULL,
    sender_name VARCHAR(150) NOT NULL,
    recipient_name VARCHAR(150) NOT NULL,
    origin_hub VARCHAR(32) NOT NULL,
    destination_hub VARCHAR(32) NOT NULL,
    current_status VARCHAR(32) NOT NULL DEFAULT 'CREATED',
    priority_tier VARCHAR(20) NOT NULL DEFAULT 'EXPRESS',
    weight_kg NUMERIC(10,2) NOT NULL CHECK (weight_kg > 0),
    vehicle_id VARCHAR(64) NOT NULL,
    current_location GEOMETRY(Point, 4326) NOT NULL,
    speed_kph NUMERIC(6,2) NOT NULL DEFAULT 0.0,
    heading_deg NUMERIC(5,2) NOT NULL DEFAULT 0.0,
    battery_pct SMALLINT NOT NULL DEFAULT 100,
    network_type VARCHAR(16) NOT NULL DEFAULT '5G',
    eta_minutes INTEGER NOT NULL DEFAULT 45,
    route_progress_pct INTEGER NOT NULL DEFAULT 0,
    last_ping_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_packages_location_gist ON packages USING GIST (current_location);
CREATE INDEX IF NOT EXISTS idx_packages_status ON packages (current_status);

CREATE TABLE IF NOT EXISTS package_custody_events (
    seq BIGSERIAL UNIQUE,
    event_id VARCHAR(40) PRIMARY KEY,
    tracking_code VARCHAR(32) NOT NULL REFERENCES packages(tracking_code) ON DELETE CASCADE,
    actor_code VARCHAR(64) NOT NULL,
    actor_role VARCHAR(32) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    previous_status VARCHAR(32),
    new_status VARCHAR(32) NOT NULL,
    reason_code VARCHAR(64) NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    signature_hash CHAR(64) NOT NULL,
    event_location GEOMETRY(Point, 4326),
    recorded_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_custody_package_seq
    ON package_custody_events (tracking_code, seq DESC);

-- Custody log is append-only: block UPDATE/DELETE of existing rows.
CREATE OR REPLACE FUNCTION forbid_custody_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'package_custody_events is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_custody_append_only ON package_custody_events;
CREATE TRIGGER trg_custody_append_only
BEFORE UPDATE ON package_custody_events
FOR EACH ROW EXECUTE FUNCTION forbid_custody_mutation();

CREATE TABLE IF NOT EXISTS telemetry_breadcrumbs (
    ping_id BIGSERIAL PRIMARY KEY,
    device_id VARCHAR(64) NOT NULL,
    tracking_code VARCHAR(32),
    timestamp_utc TIMESTAMPTZ NOT NULL,
    ingested_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_coordinates GEOMETRY(PointZ, 4326) NOT NULL,
    accuracy_m NUMERIC(6,2),
    speed_kph NUMERIC(6,2),
    heading_deg NUMERIC(5,2),
    battery_pct SMALLINT,
    network_type VARCHAR(16)
);
CREATE INDEX IF NOT EXISTS idx_breadcrumbs_geom_gist
    ON telemetry_breadcrumbs USING GIST (raw_coordinates);
CREATE INDEX IF NOT EXISTS idx_breadcrumbs_code_time
    ON telemetry_breadcrumbs (tracking_code, timestamp_utc DESC);
"""

INSERT_EVENT_SQL = """
INSERT INTO package_custody_events (
    event_id, tracking_code, actor_code, actor_role, event_type, previous_status,
    new_status, reason_code, notes, signature_hash, event_location, recorded_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
          ST_SetSRID(ST_MakePoint($11, $12), 4326), $13);
"""


class MemoryRepository:
    """No-op persistence; geofences evaluated with haversine distance."""

    engine = "IN_MEMORY_FALLBACK"

    async def close(self):
        pass

    async def insert_package(self, pkg: dict):
        pass

    async def apply_transition(self, pkg: dict, fields: dict, evt: dict):
        pass

    async def record_telemetry(self, pkg: dict, device_id: str, ts: datetime, lat: float,
                               lng: float, alt: float, accuracy: Optional[float]) -> List[str]:
        return geofences_containing_memory(lat, lng)

    async def geofences_at(self, lat: float, lng: float) -> List[str]:
        return geofences_containing_memory(lat, lng)

    async def packages_in_bbox(self, min_lng, min_lat, max_lng, max_lat) -> List[str]:
        return [
            p["trackingCode"]
            for p in PACKAGES_STATE.values()
            if p["status"] != "DELIVERED"
            and min_lng <= p["coordinates"]["lng"] <= max_lng
            and min_lat <= p["coordinates"]["lat"] <= max_lat
        ]

    async def breadcrumbs(self, code: str, limit: int) -> List[dict]:
        return []


class PostGISRepository(MemoryRepository):
    engine = "POSTGRESQL_POSTGIS_LIVE"

    def __init__(self, pool):
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "PostGISRepository":
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
        repo = cls(pool)
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Serialize concurrent bootstraps of the same database.
                await conn.execute("SELECT pg_advisory_xact_lock(424242);")
                await conn.execute(BOOTSTRAP_SQL)
                await conn.execute(_trigger_sql())
                await repo._seed_geofences(conn)
        return repo

    async def close(self):
        await self.pool.close()

    async def _seed_geofences(self, conn):
        # Circular buffers in metres, computed on the geography type.
        for hub in GEOFENCE_HUBS:
            await conn.execute(
                """
                INSERT INTO logistics_geofences (hub_code, name, zone_type, boundary)
                VALUES ($1, $2, $3,
                        ST_Buffer(ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography, $6, 32)::geometry)
                ON CONFLICT (hub_code) DO NOTHING;
                """,
                hub["code"], hub["name"], hub["type"], hub["lng"], hub["lat"],
                hub["radius_km"] * 1000.0,
            )

    async def load_or_seed(self) -> None:
        """Load packages from the database; seed it from memory if empty."""
        async with self.pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM packages;")
            if count == 0:
                for pkg in PACKAGES_STATE.values():
                    await self._insert_package(conn, pkg, pkg["events"])
                return
            rows = await conn.fetch(
                """
                SELECT *, ST_Y(current_location) AS lat, ST_X(current_location) AS lng
                FROM packages ORDER BY created_at, tracking_code;
                """
            )
            events = await conn.fetch(
                """
                SELECT *, ST_Y(event_location) AS lat, ST_X(event_location) AS lng
                FROM package_custody_events ORDER BY seq DESC;
                """
            )
        by_code: Dict[str, List[dict]] = {}
        for e in events:
            by_code.setdefault(e["tracking_code"], []).append(
                {
                    "id": e["event_id"],
                    "timestamp": iso(e["recorded_at"]),
                    "actor": e["actor_code"],
                    "role": e["actor_role"],
                    "eventType": e["event_type"],
                    "previousStatus": e["previous_status"],
                    "newStatus": e["new_status"],
                    "reasonCode": e["reason_code"],
                    "notes": e["notes"],
                    "lat": e["lat"],
                    "lng": e["lng"],
                    "hash": e["signature_hash"],
                }
            )
        PACKAGES_STATE.clear()
        for r in rows:
            PACKAGES_STATE[r["tracking_code"]] = {
                "trackingCode": r["tracking_code"],
                "sender": r["sender_name"],
                "recipient": r["recipient_name"],
                "originHub": r["origin_hub"],
                "destinationHub": r["destination_hub"],
                "status": r["current_status"],
                "priority": r["priority_tier"],
                "weightKg": float(r["weight_kg"]),
                "vehicleId": r["vehicle_id"],
                "coordinates": {"lat": r["lat"], "lng": r["lng"]},
                "speedKph": float(r["speed_kph"]),
                "headingDeg": float(r["heading_deg"]),
                "batteryPct": r["battery_pct"],
                "networkType": r["network_type"],
                "etaMinutes": r["eta_minutes"],
                "routeProgressPct": r["route_progress_pct"],
                "lastPingUtc": iso(r["last_ping_utc"]),
                "currentGeofences": [],
                "events": by_code.get(r["tracking_code"], []),
            }

    async def _insert_event(self, conn, code: str, evt: dict):
        await conn.execute(
            INSERT_EVENT_SQL,
            evt["id"], code, evt["actor"], evt["role"], evt["eventType"], evt["previousStatus"],
            evt["newStatus"], evt["reasonCode"], evt["notes"], evt["hash"], evt["lng"], evt["lat"],
            parse_iso(evt["timestamp"]),
        )

    async def _insert_package(self, conn, pkg: dict, events: List[dict]):
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO packages (
                    tracking_code, sender_name, recipient_name, origin_hub, destination_hub,
                    current_status, priority_tier, weight_kg, vehicle_id, current_location,
                    speed_kph, heading_deg, battery_pct, network_type, eta_minutes,
                    route_progress_pct, last_ping_utc
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                          ST_SetSRID(ST_MakePoint($10, $11), 4326),
                          $12, $13, $14, $15, $16, $17, $18);
                """,
                pkg["trackingCode"], pkg["sender"], pkg["recipient"], pkg["originHub"],
                pkg["destinationHub"], pkg["status"], pkg["priority"], pkg["weightKg"],
                pkg["vehicleId"], pkg["coordinates"]["lng"], pkg["coordinates"]["lat"],
                pkg["speedKph"], pkg["headingDeg"], pkg["batteryPct"], pkg["networkType"],
                pkg["etaMinutes"], pkg["routeProgressPct"], parse_iso(pkg["lastPingUtc"]),
            )
            for evt in reversed(events):  # oldest first so seq order matches the chain
                await self._insert_event(conn, pkg["trackingCode"], evt)

    async def insert_package(self, pkg: dict):
        async with self.pool.acquire() as conn:
            await self._insert_package(conn, pkg, pkg["events"])

    async def apply_transition(self, pkg: dict, fields: dict, evt: dict):
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        UPDATE packages
                        SET current_status = $2, speed_kph = $3, route_progress_pct = $4,
                            eta_minutes = $5, updated_at = NOW()
                        WHERE tracking_code = $1;
                        """,
                        pkg["trackingCode"], fields["status"], fields["speedKph"],
                        fields["routeProgressPct"], fields["etaMinutes"],
                    )
                    await self._insert_event(conn, pkg["trackingCode"], evt)
        except asyncpg.PostgresError as exc:
            raise TransitionRejected(str(exc)) from exc

    async def record_telemetry(self, pkg, device_id, ts, lat, lng, alt, accuracy):
        """Write a 3D PointZ breadcrumb, update hot state if the ping is newer,
        and return the geofences that cover the point (ST_Covers)."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO telemetry_breadcrumbs (
                        device_id, tracking_code, timestamp_utc, raw_coordinates, accuracy_m,
                        speed_kph, heading_deg, battery_pct, network_type
                    ) VALUES ($1, $2, $3, ST_SetSRID(ST_MakePoint($4, $5, $6), 4326),
                              $7, $8, $9, $10, $11);
                    """,
                    device_id, pkg["trackingCode"], ts, lng, lat, alt, accuracy,
                    pkg["speedKph"], pkg["headingDeg"], pkg["batteryPct"], pkg["networkType"],
                )
                await conn.execute(
                    """
                    INSERT INTO transport_assets (
                        device_code, last_known_location, last_speed_kph, last_heading_deg,
                        last_battery_pct, last_network_type, last_ping_utc
                    ) VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326), $4, $5, $6, $7, $8)
                    ON CONFLICT (device_code) DO UPDATE SET
                        last_known_location = EXCLUDED.last_known_location,
                        last_speed_kph = EXCLUDED.last_speed_kph,
                        last_heading_deg = EXCLUDED.last_heading_deg,
                        last_battery_pct = EXCLUDED.last_battery_pct,
                        last_network_type = EXCLUDED.last_network_type,
                        last_ping_utc = EXCLUDED.last_ping_utc,
                        is_online = TRUE,
                        updated_at = NOW()
                    WHERE transport_assets.last_ping_utc IS NULL
                       OR EXCLUDED.last_ping_utc >= transport_assets.last_ping_utc;
                    """,
                    device_id, lng, lat, pkg["speedKph"], pkg["headingDeg"], pkg["batteryPct"],
                    pkg["networkType"], ts,
                )
                await conn.execute(
                    """
                    UPDATE packages
                    SET current_location = ST_SetSRID(ST_MakePoint($2, $3), 4326),
                        speed_kph = $4, heading_deg = $5, battery_pct = $6, network_type = $7,
                        eta_minutes = $8, route_progress_pct = $9,
                        last_ping_utc = $10, updated_at = NOW()
                    WHERE tracking_code = $1 AND last_ping_utc <= $10;
                    """,
                    pkg["trackingCode"], lng, lat, pkg["speedKph"], pkg["headingDeg"],
                    pkg["batteryPct"], pkg["networkType"], pkg["etaMinutes"],
                    pkg["routeProgressPct"], ts,
                )
                rows = await conn.fetch(
                    """
                    SELECT hub_code FROM logistics_geofences
                    WHERE active AND ST_Covers(boundary, ST_SetSRID(ST_MakePoint($1, $2), 4326))
                    ORDER BY hub_code;
                    """,
                    lng, lat,
                )
        return [r["hub_code"] for r in rows]

    async def geofences_at(self, lat, lng):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT hub_code FROM logistics_geofences
                WHERE active AND ST_Covers(boundary, ST_SetSRID(ST_MakePoint($1, $2), 4326))
                ORDER BY hub_code;
                """,
                lng, lat,
            )
        return [r["hub_code"] for r in rows]

    async def packages_in_bbox(self, min_lng, min_lat, max_lng, max_lat):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT tracking_code FROM packages
                WHERE current_status <> 'DELIVERED'
                  AND current_location && ST_MakeEnvelope($1, $2, $3, $4, 4326)
                ORDER BY tracking_code;
                """,
                min_lng, min_lat, max_lng, max_lat,
            )
        return [r["tracking_code"] for r in rows]

    async def breadcrumbs(self, code, limit):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT timestamp_utc, ST_Y(raw_coordinates) AS lat, ST_X(raw_coordinates) AS lng,
                       ST_Z(raw_coordinates) AS alt, speed_kph, device_id
                FROM telemetry_breadcrumbs WHERE tracking_code = $1
                ORDER BY timestamp_utc DESC, ping_id DESC LIMIT $2;
                """,
                code, limit,
            )
        return [
            {
                "timestampUtc": iso(r["timestamp_utc"]),
                "lat": r["lat"],
                "lng": r["lng"],
                "altitudeM": r["alt"],
                "speedKph": float(r["speed_kph"]) if r["speed_kph"] is not None else None,
                "deviceId": r["device_id"],
            }
            for r in rows
        ]


repo: MemoryRepository = MemoryRepository()


# ---------------------------------------------------------------------------
# 6. DOMAIN OPERATIONS (shared by REST, telemetry and simulator)
# ---------------------------------------------------------------------------
async def commit_transition(pkg: dict, target: str, role: str, event_type: str,
                            reason: str, notes: str) -> dict:
    """Validate, persist (DB first), then update memory. Caller holds STATE_LOCK."""
    current = pkg["status"]
    err = transition_error(current, target, role)
    if err:
        raise TransitionRejected(f"State Machine Violation: Package {pkg['trackingCode']} {err}")

    fields = {
        "status": target,
        "speedKph": pkg["speedKph"] if target in MOVING_STATUSES else 0.0,
        "routeProgressPct": 100 if target == "DELIVERED" else pkg["routeProgressPct"],
        "etaMinutes": 0 if target == "DELIVERED" else pkg["etaMinutes"],
    }
    if target in MOVING_STATUSES and fields["speedKph"] == 0.0:
        fields["speedKph"] = 62.4
    evt = new_event(pkg, role, event_type, current, target, reason,
                    notes or f"Transitioned {current} -> {target} by {ACTOR_CODES[role]}.")
    await repo.apply_transition(pkg, fields, evt)
    pkg.update(fields)
    pkg["events"].insert(0, evt)
    return evt


async def apply_position(pkg: dict, device_id: str, ts: datetime, lat: float, lng: float,
                         alt: float = 12.0, accuracy: Optional[float] = None) -> List[str]:
    """Store a position for a package and run geofence automation. Caller holds STATE_LOCK."""
    pkg["coordinates"] = {"lat": round(lat, 6), "lng": round(lng, 6)}
    pkg["lastPingUtc"] = iso(ts)
    fences = await repo.record_telemetry(pkg, device_id, ts, lat, lng, alt, accuracy)
    pkg["currentGeofences"] = fences
    await run_geofence_automation(pkg, fences)
    return fences


async def run_geofence_automation(pkg: dict, fences: List[str]) -> None:
    dest = pkg["destinationHub"]
    in_delivery_zone = any(
        HUBS_BY_CODE.get(c, {}).get("type") == "CUSTOMER_DELIVERY_ZONE" for c in fences
    )
    try:
        if pkg["status"] == "IN_TRANSIT" and in_delivery_zone:
            zone = next(c for c in fences if HUBS_BY_CODE[c]["type"] == "CUSTOMER_DELIVERY_ZONE")
            await commit_transition(
                pkg, "OUT_FOR_DELIVERY", "SYSTEM_AUTOMATION", "GEOFENCE_ENTRY",
                "ENTERED_DELIVERY_SECTOR", f"Crossed {HUBS_BY_CODE[zone]['name']} boundary polygon.",
            )
        elif (
            pkg["status"] == "OUT_FOR_DELIVERY"
            and pkg["routeProgressPct"] >= 100
            and dest in fences
        ):
            await commit_transition(
                pkg, "DELIVERED", "SYSTEM_AUTOMATION", "GEOFENCE_ARRIVAL_LOCK",
                "DESTINATION_POLYGON_MATCH",
                f"Auto-verified arrival inside {HUBS_BY_CODE[dest]['name']}.",
            )
    except TransitionRejected as exc:
        print(f"⚠️  Geofence automation rejected for {pkg['trackingCode']}: {exc}")


async def simulation_tick() -> None:
    """Advance every moving package along the corridor by one step."""
    if not SIMULATION_CONFIG["live_enabled"]:
        return
    jitter = 0.0002 if SIMULATION_CONFIG["kalman_snap"] else 0.0018
    now = utc_now()
    async with STATE_LOCK:
        for pkg in list(PACKAGES_STATE.values()):
            if pkg["status"] not in MOVING_STATUSES:
                pkg["speedKph"] = 0.0
                continue
            if EXTERNAL_HOLD_UNTIL.get(pkg["trackingCode"], 0.0) > now.timestamp():
                continue  # a real device is reporting for this package
            pkg["routeProgressPct"] = min(100, pkg["routeProgressPct"] + SIM_PROGRESS_STEP)
            pkg["speedKph"] = round(58.0 + random.random() * 14.0, 1)
            pkg["etaMinutes"] = eta_minutes(pkg["routeProgressPct"], pkg["speedKph"])
            pos = route_position(pkg["routeProgressPct"])
            pkg["headingDeg"] = pos["heading"]
            lat = pos["lat"] + (random.random() - 0.5) * jitter
            lng = pos["lng"] + (random.random() - 0.5) * jitter
            try:
                await apply_position(pkg, pkg["vehicleId"], now, lat, lng)
            except Exception as exc:  # keep the loop alive if the DB blips
                print(f"⚠️  Telemetry write failed for {pkg['trackingCode']}: {exc}")
    await ws_manager.broadcast_state()


async def telemetry_simulation_loop():
    while True:
        await asyncio.sleep(SIM_TICK_SECONDS)
        try:
            await simulation_tick()
        except Exception as exc:  # pragma: no cover - defensive
            print(f"⚠️  Simulation tick failed: {exc}")


# ---------------------------------------------------------------------------
# 7. WEBSOCKET MANAGER
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_state(self):
        if not self.active_connections:
            return
        payload = json.dumps(snapshot())
        for connection in list(self.active_connections):
            try:
                await connection.send_text(payload)
            except Exception:
                self.disconnect(connection)


ws_manager = ConnectionManager()


# ---------------------------------------------------------------------------
# 8. APP LIFESPAN
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global repo, STATE_LOCK
    STATE_LOCK = asyncio.Lock()  # bind to this server's event loop
    reset_state()
    repo = MemoryRepository()
    dsn = os.getenv("DATABASE_URL")
    if dsn and asyncpg is None:
        print("⚠️  DATABASE_URL is set but asyncpg is not installed; running in memory.")
    elif dsn:
        pg = None
        try:
            pg = await PostGISRepository.connect(dsn)
            await pg.load_or_seed()
            for pkg in PACKAGES_STATE.values():
                c = pkg["coordinates"]
                pkg["currentGeofences"] = await pg.geofences_at(c["lat"], c["lng"])
            repo = pg
            print("✅  Connected to PostgreSQL/PostGIS; schema and triggers verified.")
        except Exception as exc:
            print(f"⚠️  Could not use PostgreSQL ({exc}). Falling back to in-memory store.")
            if pg is not None:
                await pg.close()
            reset_state()
    SIMULATION_CONFIG["storage_engine"] = repo.engine

    sim_task = None
    if os.getenv("GEOTRACK_SIMULATION", "1") != "0":
        sim_task = asyncio.create_task(telemetry_simulation_loop())
    try:
        yield
    finally:
        if sim_task:
            sim_task.cancel()
        await repo.close()


app = FastAPI(
    title="GeoTrack COP Logistics & PostGIS Telemetry API",
    version="2.1.0",
    description="Near-real-time GPS telemetry ingestion, PostGIS ST_Covers geofencing, "
    "and state-machine chain-of-custody tracking.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# 9. REST + WEBSOCKET ENDPOINTS
# ---------------------------------------------------------------------------
def get_pkg_or_404(code: str) -> dict:
    pkg = PACKAGES_STATE.get(code)
    if not pkg:
        raise HTTPException(status_code=404, detail=f"Package {code} not found.")
    return pkg


@app.get("/api/v1/health")
async def health():
    return {"ok": True, "storage_engine": SIMULATION_CONFIG["storage_engine"]}


@app.get("/api/v1/state")
async def get_cop_state():
    snap = snapshot()
    snap.pop("type")
    return snap


@app.post("/api/v1/config/toggle")
async def toggle_simulation_config(field: Literal["live_enabled", "kalman_snap"]):
    SIMULATION_CONFIG[field] = not SIMULATION_CONFIG[field]
    await ws_manager.broadcast_state()
    return SIMULATION_CONFIG


@app.get("/api/v1/shipments/{tracking_code}")
async def get_shipment(tracking_code: str):
    return get_pkg_or_404(tracking_code)


@app.post("/api/v1/shipments", status_code=201)
async def ingest_shipment(payload: CreateShipmentPayload):
    async with STATE_LOCK:
        code = f"PKG-{random.randint(10000, 99999)}"
        while code in PACKAGES_STATE:
            code = f"PKG-{random.randint(10000, 99999)}"
        origin = HUBS_BY_CODE["HUB-HNL-01"]
        pkg = {
            "trackingCode": code,
            "sender": payload.sender.strip(),
            "recipient": payload.recipient.strip(),
            "originHub": "HUB-HNL-01",
            "destinationHub": "ZONE-KAILUA-03",
            "status": "CREATED",
            "priority": payload.priority,
            "weightKg": payload.weight_kg,
            "vehicleId": f"VEH-{random.randint(1000, 9999)}-DELTA",
            "coordinates": {"lat": origin["lat"], "lng": origin["lng"]},
            "speedKph": 0.0,
            "headingDeg": 0.0,
            "batteryPct": 100,
            "networkType": "5G",
            "etaMinutes": eta_minutes(0, 60.0),
            "routeProgressPct": 0,
            "lastPingUtc": iso(utc_now()),
            "currentGeofences": ["HUB-HNL-01"],
            "events": [],
        }
        if not pkg["sender"] or not pkg["recipient"]:
            raise HTTPException(status_code=422, detail="Sender and recipient are required.")
        pkg["events"].insert(0, new_event(
            pkg, payload.actor_role, "MANIFEST_INGESTED", None, "CREATED", "INITIAL_SCAN",
            f"Ingested via {payload.actor_role} portal ({payload.weight_kg} kg).",
        ))
        await repo.insert_package(pkg)
        PACKAGES_STATE[code] = pkg
    await ws_manager.broadcast_state()
    return {"status": "ok", "package": pkg}


@app.post("/api/v1/shipments/{tracking_code}/events")
async def update_custody_status(tracking_code: str, payload: CustodyUpdatePayload):
    async with STATE_LOCK:
        pkg = get_pkg_or_404(tracking_code)
        event_type = (
            "STATUS_OVERRIDE" if payload.actor_role == "LOGISTICS_MANAGER" else "CUSTODY_HANDOFF_SCAN"
        )
        try:
            await commit_transition(pkg, payload.new_status, payload.actor_role, event_type,
                                    payload.reason_code.strip(), payload.notes.strip())
        except TransitionRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    await ws_manager.broadcast_state()
    return {"status": "ok", "package": pkg}


@app.get("/api/v1/shipments/{tracking_code}/verify")
async def verify_custody_chain(tracking_code: str):
    return {"trackingCode": tracking_code, **verify_chain(get_pkg_or_404(tracking_code))}


@app.get("/api/v1/shipments/{tracking_code}/breadcrumbs")
async def get_breadcrumbs(tracking_code: str, limit: int = Query(100, ge=1, le=5000)):
    get_pkg_or_404(tracking_code)
    return {
        "trackingCode": tracking_code,
        "storage_engine": SIMULATION_CONFIG["storage_engine"],
        "breadcrumbs": await repo.breadcrumbs(tracking_code, limit),
    }


@app.post("/api/v1/telemetry/ping")
async def ingest_gps_ping(payload: TelemetryPingPayload):
    """Ingest an external IoT/mobile GPS ping, persist it and evaluate geofences.
    Pings older than the package's last known ping are ignored (out-of-order guard)."""
    ts = payload.timestamp_utc or utc_now()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    updated, stale, unknown = [], [], []
    fences: List[str] = []
    async with STATE_LOCK:
        for code in payload.package_ids:
            pkg = PACKAGES_STATE.get(code)
            if not pkg:
                unknown.append(code)
                continue
            if ts < parse_iso(pkg["lastPingUtc"]):
                stale.append(code)
                continue
            pkg["speedKph"] = payload.speed_kph
            pkg["headingDeg"] = payload.heading_deg
            pkg["batteryPct"] = payload.battery_pct
            pkg["networkType"] = payload.network_type
            pkg["routeProgressPct"] = nearest_route_progress(
                payload.coordinates.lat, payload.coordinates.lng)
            if pkg["status"] in MOVING_STATUSES:
                pkg["etaMinutes"] = eta_minutes(pkg["routeProgressPct"], payload.speed_kph or 60.0)
            fences = await apply_position(
                pkg, payload.device_id, ts, payload.coordinates.lat, payload.coordinates.lng,
                payload.coordinates.altitude_m or 12.0, payload.coordinates.accuracy_m,
            )
            EXTERNAL_HOLD_UNTIL[code] = utc_now().timestamp() + EXTERNAL_PING_HOLD_SECONDS
            updated.append(code)
    if updated:
        await ws_manager.broadcast_state()
    return {
        "ingested": bool(updated),
        "storage_engine": SIMULATION_CONFIG["storage_engine"],
        "updated_packages": updated,
        "stale_packages": stale,
        "unknown_packages": unknown,
        "geofences_intersected": fences,
    }


@app.get("/api/v1/geofences/check")
async def check_geofences(lat: float = Query(..., ge=-90, le=90),
                          lng: float = Query(..., ge=-180, le=180)):
    codes = await repo.geofences_at(lat, lng)
    return {"lat": lat, "lng": lng, "geofences": [HUBS_BY_CODE[c] for c in codes if c in HUBS_BY_CODE]}


@app.get("/api/v1/packages/in-bbox")
async def packages_in_bbox(min_lng: float, min_lat: float, max_lng: float, max_lat: float):
    if min_lng > max_lng or min_lat > max_lat:
        raise HTTPException(status_code=422, detail="min values must not exceed max values.")
    codes = await repo.packages_in_bbox(min_lng, min_lat, max_lng, max_lat)
    return {"packages": [PACKAGES_STATE[c] for c in codes if c in PACKAGES_STATE]}


@app.websocket("/ws/cop")
async def websocket_cop_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        await websocket.send_text(json.dumps(snapshot()))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket)


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return HTMLResponse(content=(HERE / "dashboard.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    print(f"\n  🚀 GeoTrack COP starting at {url}")
    print(f"  📡 OpenAPI / Swagger docs at {url}/docs\n")
    if os.getenv("GEOTRACK_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
