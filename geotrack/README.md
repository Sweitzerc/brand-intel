# GeoTrack COP — logistics & package tracking

A near-real-time package tracking server with a live map dashboard. It is built
with FastAPI and WebSockets, and it can store data in PostgreSQL/PostGIS.

![Dashboard](screenshot.png)

## Run it

```bash
pip install -r geotrack/requirements.txt
python geotrack/run_geotrack_app.py          # opens http://127.0.0.1:8000
```

Without a database the app keeps everything in memory, and the data resets when
it restarts. To store data in PostGIS:

```bash
docker compose -f geotrack/docker-compose.yml up -d
export DATABASE_URL="postgresql://postgres:password@localhost:5432/logistics_db"
python geotrack/run_geotrack_app.py
```

On first start the app creates its tables, triggers and geofences, and adds the
three demo packages. On later starts it loads packages and custody history from
the database. If the database can't be reached, it says so and falls back to
memory. The badge at the top of the dashboard shows which mode is active.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | unset | PostgreSQL DSN; unset means in-memory mode |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address (`0.0.0.0` to expose on your network) |
| `GEOTRACK_SIMULATION` | `1` | `0` turns off the simulated trucks |
| `GEOTRACK_NO_BROWSER` | unset | `1` skips opening a browser tab |

## What it does

- **Live map.** Packages, hub geofences and the delivery route
  (Honolulu → Pearl City → Kailua) are drawn to scale. Updates arrive over the
  `/ws/cop` WebSocket. If the WebSocket fails, the page falls back to polling
  every 2 seconds.
- **Simulated fleet.** Every 2 seconds, moving packages advance along the
  route. Each position is checked against the geofences (`ST_Covers` in
  PostGIS, a distance check in memory mode). A package that enters the Kailua
  delivery zone moves to `OUT_FOR_DELIVERY` automatically. It becomes
  `DELIVERED` when it reaches the destination.
- **Real GPS pings.** `POST /api/v1/telemetry/ping` accepts device positions.
  It ignores pings older than the last one received, and it pauses the
  simulator for that package for 30 seconds. To send a test ping, click
  anywhere on the map.
- **State machine.** Statuses can only change along allowed paths (see
  `VALID_TRANSITIONS`). Managers can also force a package into `HELD_AT_HUB`,
  `AT_HUB` or `EXCEPTION_RETURN`. Delivered packages are locked. In PostGIS
  mode a database trigger built from the same rules enforces them a second
  time. If the database rejects a change, the API returns HTTP 400 and the
  in-memory state stays as it was.
- **Tamper-evident custody log.** Each event is hashed with SHA-256 together
  with the hash of the event before it. `GET /api/v1/shipments/{code}/verify`
  recomputes the chain, and the dashboard's **Verify chain** button calls it.
  In PostGIS mode the events table also rejects updates to existing rows.

## API

Interactive docs are at `/docs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/state` | Full snapshot (packages, geofences, route, config) |
| POST | `/api/v1/shipments` | Create a package |
| GET | `/api/v1/shipments/{code}` | One package with its events |
| POST | `/api/v1/shipments/{code}/events` | Change status (custody scan or manager override) |
| GET | `/api/v1/shipments/{code}/verify` | Check the custody hash chain |
| GET | `/api/v1/shipments/{code}/breadcrumbs` | GPS history (PostGIS mode) |
| POST | `/api/v1/telemetry/ping` | Send a device GPS ping |
| GET | `/api/v1/geofences/check?lat=&lng=` | Which geofences cover a point |
| GET | `/api/v1/packages/in-bbox?min_lng=&min_lat=&max_lng=&max_lat=` | Active packages inside a map rectangle |
| POST | `/api/v1/config/toggle?field=live_enabled\|kalman_snap` | Pause the simulator / change GPS noise |
| WS | `/ws/cop` | Live snapshots |

Example ping:

```bash
curl -X POST localhost:8000/api/v1/telemetry/ping -H 'Content-Type: application/json' -d '{
  "device_id": "VEH-8842-ALPHA", "package_ids": ["PKG-99201"],
  "coordinates": {"lat": 21.3972, "lng": -157.7394, "altitude_m": 12}
}'
```

## Production schema

`schema/production_schema.sql` holds the fuller, normalized schema from the
design documents: users and roles, enum types, foreign keys to vehicles, a
TimescaleDB hypertable for breadcrumbs (used only if TimescaleDB is
installed), and a trigger that copies the newest GPS position onto each
vehicle. The demo server uses its own simpler tables. This file is the
starting point for a production deployment.

## Tests

```bash
pytest tests/test_geotrack.py                      # in-memory tests
GEOTRACK_TEST_DATABASE_URL=postgresql://... pytest tests/test_geotrack.py
```

The second command also runs the PostGIS tests. **They drop and recreate the
GeoTrack tables in the database you point them at**, so use a throwaway
database.

## Not included yet

- **Login.** The Manager/Operator toggle is a demo switch, and the API trusts
  the `actor_role` it receives. Put the app behind authentication before
  exposing it beyond your own machine.
- **Single process.** Live state is held in one server process, so run one
  instance at a time.
- **Fixed demo route.** Every package uses the same Honolulu → Pearl City →
  Kailua corridor. Per-package routes and road snapping are not implemented.
