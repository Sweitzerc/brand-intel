-- =============================================================================
-- GeoTrack COP — production PostgreSQL / PostGIS (/ TimescaleDB) schema
--
-- Normalized schema for deployments beyond the single-node demo tables that
-- run_geotrack_app.py bootstraps itself. Adds users/RBAC, enum types, asset
-- foreign keys, an optional TimescaleDB hypertable for breadcrumbs, the
-- state-machine trigger, and a telemetry -> hot-state sync trigger.
--
-- Apply to an empty database:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f geotrack/schema/production_schema.sql
--
-- The status-transition trigger mirrors VALID_TRANSITIONS + manager overrides
-- in run_geotrack_app.py (regenerate with: python -c "from geotrack import
-- run_geotrack_app as g; print(g._trigger_sql())").
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- TimescaleDB is optional: use it when the server has it installed.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
    ELSE
        RAISE NOTICE 'timescaledb not available; telemetry_breadcrumbs stays a plain table';
    END IF;
END $$;

-- 1. Enumerated types ---------------------------------------------------------
CREATE TYPE user_role_enum AS ENUM (
    'OPERATOR',           -- drivers, warehouse scanners, field couriers
    'LOGISTICS_MANAGER',  -- dispatchers, fleet managers, hub supervisors
    'RECIPIENT',          -- end customer viewing package progress
    'SYSTEM_AUTOMATION'   -- geofence engine, automated telemetry rules
);

CREATE TYPE package_status_enum AS ENUM (
    'CREATED', 'PICKED_UP', 'IN_TRANSIT', 'AT_HUB',
    'HELD_AT_HUB',        -- exception state (weather, customs, damage)
    'OUT_FOR_DELIVERY', 'DELIVERED', 'EXCEPTION_RETURN'
);

CREATE TYPE zone_type_enum AS ENUM (
    'SORTING_HUB', 'REGIONAL_DEPOT', 'RESTRICTED_CORRIDOR', 'CUSTOMER_DELIVERY_ZONE'
);

-- 2. Users & operators ------------------------------------------------------
CREATE TABLE users (
    user_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_code VARCHAR(32) UNIQUE NOT NULL,       -- e.g. 'USR-4410-MGR'
    full_name VARCHAR(120) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    role user_role_enum NOT NULL DEFAULT 'OPERATOR',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Hubs & geofence polygons ------------------------------------------------
CREATE TABLE logistics_geofences (
    geofence_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    hub_code VARCHAR(32) UNIQUE NOT NULL,            -- e.g. 'HUB-HNL-01'
    name VARCHAR(150) NOT NULL,
    zone_type zone_type_enum NOT NULL,
    boundary GEOMETRY(Polygon, 4326) NOT NULL,       -- WGS 84
    center_point GEOMETRY(Point, 4326) GENERATED ALWAYS AS (ST_Centroid(boundary)) STORED,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by UUID REFERENCES users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_geofences_boundary_gist ON logistics_geofences USING GIST (boundary);

-- 4. Vehicles & IoT telemetry devices ------------------------------------------
CREATE TABLE transport_assets (
    asset_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_code VARCHAR(64) UNIQUE NOT NULL,         -- e.g. 'VEH-8842-ALPHA'
    carrier_name VARCHAR(100) NOT NULL,
    assigned_operator_id UUID REFERENCES users(user_id),
    last_known_location GEOMETRY(Point, 4326),       -- hot state, newest ping wins
    last_speed_kph NUMERIC(6,2),
    last_heading_deg NUMERIC(5,2),
    last_battery_pct SMALLINT CHECK (last_battery_pct BETWEEN 0 AND 100),
    last_network_type VARCHAR(16),
    last_ping_utc TIMESTAMPTZ,
    is_online BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_assets_location_gist ON transport_assets USING GIST (last_known_location);
CREATE INDEX idx_assets_last_ping ON transport_assets (last_ping_utc DESC);

-- 5. Packages & manifests ----------------------------------------------------
CREATE TABLE packages (
    package_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tracking_code VARCHAR(32) UNIQUE NOT NULL,       -- e.g. 'PKG-99201'
    manifest_batch_id VARCHAR(64),
    sender_name VARCHAR(150) NOT NULL,
    recipient_name VARCHAR(150) NOT NULL,
    recipient_phone VARCHAR(32),
    origin_hub_id UUID REFERENCES logistics_geofences(geofence_id),
    destination_hub_id UUID REFERENCES logistics_geofences(geofence_id),
    destination_coords GEOMETRY(Point, 4326) NOT NULL,
    current_asset_id UUID REFERENCES transport_assets(asset_id) ON DELETE SET NULL,
    current_hub_id UUID REFERENCES logistics_geofences(geofence_id) ON DELETE SET NULL,
    current_status package_status_enum NOT NULL DEFAULT 'CREATED',
    current_location GEOMETRY(Point, 4326),          -- synced from vehicle or hub
    weight_kg NUMERIC(10,2) NOT NULL CHECK (weight_kg > 0),
    priority_tier VARCHAR(20) NOT NULL DEFAULT 'STANDARD'
        CHECK (priority_tier IN ('STANDARD', 'EXPRESS', 'CRITICAL')),
    estimated_delivery_utc TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_packages_status ON packages (current_status);
CREATE INDEX idx_packages_current_asset ON packages (current_asset_id);
CREATE INDEX idx_packages_location_gist ON packages USING GIST (current_location);

-- 6. Append-only chain-of-custody log ----------------------------------------
CREATE TABLE package_custody_events (
    seq BIGSERIAL UNIQUE,
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    package_id UUID NOT NULL REFERENCES packages(package_id) ON DELETE CASCADE,
    actor_id UUID REFERENCES users(user_id),
    actor_role user_role_enum NOT NULL,
    event_type VARCHAR(64) NOT NULL,                 -- 'CUSTODY_HANDOFF_SCAN', 'STATUS_OVERRIDE', 'GEOFENCE_ENTRY'
    previous_status package_status_enum,
    new_status package_status_enum NOT NULL,
    hub_id UUID REFERENCES logistics_geofences(geofence_id),
    asset_id UUID REFERENCES transport_assets(asset_id),
    event_location GEOMETRY(Point, 4326),
    reason_code VARCHAR(64),
    notes TEXT,
    signature_hash CHAR(64) NOT NULL,                -- SHA-256 over event + previous hash
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_custody_package_seq ON package_custody_events (package_id, seq DESC);

CREATE OR REPLACE FUNCTION forbid_custody_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'package_custody_events is append-only';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_custody_append_only
BEFORE UPDATE ON package_custody_events
FOR EACH ROW EXECUTE FUNCTION forbid_custody_mutation();

-- 7. GPS breadcrumbs (TimescaleDB hypertable when available) ------------------
CREATE TABLE telemetry_breadcrumbs (
    ping_id BIGSERIAL,
    asset_id UUID NOT NULL REFERENCES transport_assets(asset_id) ON DELETE CASCADE,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    ingested_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_coordinates GEOMETRY(PointZ, 4326) NOT NULL, -- lng, lat, altitude
    snapped_coordinates GEOMETRY(Point, 4326),       -- road-snapped (OSRM/Mapbox)
    accuracy_m NUMERIC(6,2),
    speed_kph NUMERIC(6,2),
    heading_deg NUMERIC(5,2),
    battery_pct SMALLINT,
    network_type VARCHAR(16),
    PRIMARY KEY (asset_id, timestamp_utc, ping_id)
);
CREATE INDEX idx_telemetry_spatial ON telemetry_breadcrumbs USING GIST (raw_coordinates);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable('telemetry_breadcrumbs', 'timestamp_utc',
                                  chunk_time_interval => INTERVAL '1 day');
    END IF;
END $$;

-- 8. Trigger: package status state machine -----------------------------------
CREATE OR REPLACE FUNCTION validate_package_status_transition()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.current_status = 'DELIVERED' THEN
        RAISE EXCEPTION 'Package % is already DELIVERED and locked.', OLD.tracking_code;
    END IF;
    IF OLD.current_status = 'CREATED' AND NEW.current_status NOT IN ('AT_HUB', 'EXCEPTION_RETURN', 'HELD_AT_HUB', 'PICKED_UP') THEN
        RAISE EXCEPTION 'Illegal state transition from CREATED to %', NEW.current_status;
    ELSIF OLD.current_status = 'PICKED_UP' AND NEW.current_status NOT IN ('AT_HUB', 'EXCEPTION_RETURN', 'HELD_AT_HUB', 'IN_TRANSIT') THEN
        RAISE EXCEPTION 'Illegal state transition from PICKED_UP to %', NEW.current_status;
    ELSIF OLD.current_status = 'IN_TRANSIT' AND NEW.current_status NOT IN ('AT_HUB', 'EXCEPTION_RETURN', 'HELD_AT_HUB', 'OUT_FOR_DELIVERY') THEN
        RAISE EXCEPTION 'Illegal state transition from IN_TRANSIT to %', NEW.current_status;
    ELSIF OLD.current_status = 'AT_HUB' AND NEW.current_status NOT IN ('AT_HUB', 'EXCEPTION_RETURN', 'HELD_AT_HUB', 'IN_TRANSIT', 'OUT_FOR_DELIVERY') THEN
        RAISE EXCEPTION 'Illegal state transition from AT_HUB to %', NEW.current_status;
    ELSIF OLD.current_status = 'HELD_AT_HUB' AND NEW.current_status NOT IN ('AT_HUB', 'EXCEPTION_RETURN', 'HELD_AT_HUB', 'IN_TRANSIT', 'OUT_FOR_DELIVERY') THEN
        RAISE EXCEPTION 'Illegal state transition from HELD_AT_HUB to %', NEW.current_status;
    ELSIF OLD.current_status = 'OUT_FOR_DELIVERY' AND NEW.current_status NOT IN ('AT_HUB', 'DELIVERED', 'EXCEPTION_RETURN', 'HELD_AT_HUB') THEN
        RAISE EXCEPTION 'Illegal state transition from OUT_FOR_DELIVERY to %', NEW.current_status;
    ELSIF OLD.current_status = 'DELIVERED' AND NEW.current_status NOT IN (NULL) THEN
        RAISE EXCEPTION 'Illegal state transition from DELIVERED to %', NEW.current_status;
    ELSIF OLD.current_status = 'EXCEPTION_RETURN' AND NEW.current_status NOT IN ('AT_HUB', 'EXCEPTION_RETURN', 'HELD_AT_HUB') THEN
        RAISE EXCEPTION 'Illegal state transition from EXCEPTION_RETURN to %', NEW.current_status;
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

-- 9. Trigger: newest telemetry updates asset + aboard packages ---------------
-- Out-of-order cellular packets never overwrite a newer live position.
CREATE OR REPLACE FUNCTION sync_latest_telemetry_state()
RETURNS TRIGGER AS $$
DECLARE
    v_last_ping TIMESTAMPTZ;
    v_point GEOMETRY(Point, 4326);
BEGIN
    v_point := COALESCE(NEW.snapped_coordinates, ST_Force2D(NEW.raw_coordinates));

    SELECT last_ping_utc INTO v_last_ping FROM transport_assets WHERE asset_id = NEW.asset_id;

    IF v_last_ping IS NULL OR NEW.timestamp_utc >= v_last_ping THEN
        UPDATE transport_assets
        SET last_known_location = v_point,
            last_speed_kph = NEW.speed_kph,
            last_heading_deg = NEW.heading_deg,
            last_battery_pct = NEW.battery_pct,
            last_network_type = NEW.network_type,
            last_ping_utc = NEW.timestamp_utc,
            is_online = TRUE,
            updated_at = NOW()
        WHERE asset_id = NEW.asset_id;

        UPDATE packages
        SET current_location = v_point, updated_at = NOW()
        WHERE current_asset_id = NEW.asset_id
          AND current_status IN ('PICKED_UP', 'IN_TRANSIT', 'OUT_FOR_DELIVERY');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_telemetry_to_assets
AFTER INSERT ON telemetry_breadcrumbs
FOR EACH ROW EXECUTE FUNCTION sync_latest_telemetry_state();

-- =============================================================================
-- Reference queries for the API layer
-- =============================================================================
-- A) Map viewport: active packages inside the current screen bounds
--    ($1 min_lng, $2 min_lat, $3 max_lng, $4 max_lat)
-- SELECT p.tracking_code, p.current_status, p.priority_tier, a.device_code AS vehicle_id,
--        a.last_speed_kph, a.last_heading_deg,
--        ST_Y(p.current_location) AS latitude, ST_X(p.current_location) AS longitude
-- FROM packages p
-- LEFT JOIN transport_assets a ON p.current_asset_id = a.asset_id
-- WHERE p.current_status <> 'DELIVERED'
--   AND p.current_location && ST_MakeEnvelope($1, $2, $3, $4, 4326);
--
-- B) Geofence hit test for an incoming GPS point ($1 lng, $2 lat).
--    ST_Covers (not ST_Contains) so a point exactly on the boundary counts.
-- SELECT geofence_id, hub_code, name, zone_type
-- FROM logistics_geofences
-- WHERE active AND ST_Covers(boundary, ST_SetSRID(ST_MakePoint($1, $2), 4326));
