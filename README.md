# ThermoTrace

<p align="center">
<pre>
 _____ _                             _____                   
|_   _| |__   ___ _ __ _ __ ___   __|_   _| __ __ _  ___ ___ 
  | | | '_ \ / _ \ '__| '_ ` _ \ / _ \| || '__/ _` |/ __/ _ \
  | | | | | |  __/ |  | | | | | | (_) | || | | (_| | (_|  __/
  |_| |_| |_|\___|_|  |_| |_| |_|\___/|_||_|  \__,_|\___\___|
</pre>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-Programming-3776AB?logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/FastAPI-WebFramework-009688?logo=fastapi&logoColor=white">
  <img src="https://img.shields.io/badge/PostgreSQL-Database-4169E1?logo=postgresql&logoColor=white">
  <img src="https://img.shields.io/badge/PostGIS-Geospatial-5B8C85?logo=postgresql&logoColor=white">
  <img src="https://img.shields.io/badge/GeoPandas-Geospatial-139C5A?logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/Shapely-Geometry-3776AB?logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/Rasterio-RasterProcessing-3776AB?logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/PyProj-CRS-3776AB?logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/Pandas-DataProcessing-150458?logo=pandas&logoColor=white">
  <img src="https://img.shields.io/badge/NumPy-ScientificComputing-013243?logo=numpy&logoColor=white">
  <img src="https://img.shields.io/badge/Scikit--learn-MachineLearning-F7931E?logo=scikit-learn&logoColor=white">
  <img src="https://img.shields.io/badge/SciPy-ScientificComputing-8CAAE6?logo=scipy&logoColor=white">
  <img src="https://img.shields.io/badge/OpenStreetMap-OSM-7EBC6F?logo=openstreetmap&logoColor=white">
  <img src="https://img.shields.io/badge/NASA%20FIRMS-ThermalDetection-E03C31?logo=nasa&logoColor=white">
  <img src="https://img.shields.io/badge/Sentinel--2-RemoteSensing-003247?logo=esa&logoColor=white">
  <img src="https://img.shields.io/badge/WorldCover-LandCover-003247?logo=esa&logoColor=white">
  <img src="https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=black">
  <img src="https://img.shields.io/badge/TypeScript-Frontend-3178C6?logo=typescript&logoColor=white">
  <img src="https://img.shields.io/badge/MapLibre-GISMapping-396CB2?logo=maplibre&logoColor=white">
  <img src="https://img.shields.io/badge/Plotly-DataVisualization-3F4F75?logo=plotly&logoColor=white">
  <img src="https://img.shields.io/badge/Redis-Cache-DC382D?logo=redis&logoColor=white">
  <img src="https://img.shields.io/badge/Celery-TaskQueue-37814A?logo=celery&logoColor=white">
  <img src="https://img.shields.io/badge/Docker-Containerization-2496ED?logo=docker&logoColor=white">
</p>





# India Industrial Intelligence Feature 

A Web GIS application for exploring, filtering, and analysing industrial sites
across India, built on top of the `industrial-data` geospatial pipeline.

> **Current milestone** — the web application runs against a synthetic
> 30-site dataset.  The backend is architected to switch to live PostGIS data
> (populated by the `industrial-data` pipeline) by setting one environment
> variable.  No other code change is required.

---

## Repository layout

```
Remote-sensing-OSM/
├── industrial-data/          # geospatial data pipeline (Phases 1–13 complete)
│   ├── docker-compose.yml    # PostGIS 16-3.4 container
│   ├── sql/                  # schema.sql · indexes.sql · functions.sql
│   ├── run_pipeline.py       # Unified end-to-end pipeline runner
│   ├── scripts/              # individual stage scripts
│   ├── src/                  # Python pipeline source (OSM, cleaning, matching, export)
│   └── requirements.txt
│
└── industrial-gis/           # Web GIS application (this milestone)
    ├── backend/              # FastAPI + SQLAlchemy
    │   ├── app/
    │   │   ├── main.py       # ASGI app, CORS from env
    │   │   ├── config.py     # Settings singleton (DATA_MODE, DATABASE_URL, ALLOWED_ORIGINS)
    │   │   ├── db.py         # SQLAlchemy engine + session factory
    │   │   ├── api/routes.py # All /api/* endpoints
    │   │   └── services/
    │   │       ├── catalog.py         # Synthetic dataset + dispatcher
    │   │       └── postgis_repo.py    # PostGIS queries (ST_Intersects, ST_MakeEnvelope)
    │   ├── tests/
    │   │   ├── test_health.py
    │   │   └── test_endpoints.py
    │   └── requirements.txt
    │
    └── frontend/             # React 18 + TypeScript + Vite + Leaflet
        ├── src/
        │   ├── App.tsx              # Central state, health probe, offline banner
        │   ├── components/
        │   │   ├── MapView.tsx      # Leaflet map, MarkerCluster, bbox emission
        │   │   ├── Header.tsx       # Search + filters + active-filter pills
        │   │   ├── StatisticsCards.tsx
        │   │   ├── IndustryDetails.tsx
        │   │   ├── AnalyticsPanel.tsx
        │   │   ├── LoadingState.tsx
        │   │   └── ErrorState.tsx
        │   ├── services/api.ts      # Central API client (VITE_API_BASE_URL)
        │   ├── types/industrial.ts  # All TypeScript interfaces
        │   └── hooks/useDebounce.ts
        └── vite.config.ts           # Proxy: /api → http://127.0.0.1:8000
```

---

## Architecture

```
Browser (http://localhost:5173)
  │
  │  React 18 + TypeScript + Vite
  │  Leaflet 1.9 + leaflet.markercluster
  │
  ├── /api/*  (proxied by Vite dev server)
  │              ↓
  │         FastAPI  (http://localhost:8000)
  │         uvicorn · Python 3.11+
  │              ↓
  │    DATA_MODE=synthetic  →  in-memory 30-site dataset
  │    DATA_MODE=postgis    →  industrial_sites (PostGIS)
  │                                   ↑
  │                        populated by industrial-data pipeline
  │
  └── PostGIS 16-3.4 (docker-compose in industrial-data/)
        database: industrial_data
        tables:   industrial_sites · osm_industries
                  industrial_entity_matches · states · districts
```

---

## Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| Python | 3.11 | 3.13 confirmed working |
| Node.js | 18 | 20+ recommended |
| npm | 9 | bundled with Node |
| Docker | 24 | only needed for PostGIS mode |

---

## 1 — Development setup (synthetic mode — no database required)

### 1.1 Backend

```powershell
# From the repository root
cd industrial-gis

# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# Install dependencies
pip install -r backend/requirements.txt

# Start the API server (DATA_MODE defaults to 'synthetic')
python -m uvicorn backend.app.main:app --reload --port 8000 --host 127.0.0.1
```

The server logs `data_mode=synthetic` on startup.

### 1.2 Frontend

```powershell
# In a second terminal
cd industrial-gis/frontend

npm install
npm run dev
```

### 1.3 Open the application

| URL | Purpose |
|---|---|
| http://localhost:5173 | Web GIS application |
| http://localhost:8000/api/health | Backend health check |
| http://localhost:8000/api/docs | Interactive API documentation (Swagger UI) |

---

## 2 — PostGIS mode (live data from the pipeline)

### 2.1 Start the PostGIS container

The Docker Compose file is in `industrial-data/`.

```powershell
cd industrial-data

# Copy the example env file and adjust if needed
copy .env.example .env

# Start PostGIS 16-3.4 (service name: postgis, container: industrial-data-postgis)
docker compose up -d

# Verify it is healthy
docker compose ps
```

Default credentials (from `.env.example`):

| Setting | Default |
|---|---|
| `POSTGRES_DB` | `industrial_data` |
| `POSTGRES_USER` | `industrial` |
| `POSTGRES_PASSWORD` | `industrial` |
| `POSTGRES_PORT` | `5432` |

The SQL scripts in `industrial-data/sql/` are mounted as
`/docker-entrypoint-initdb.d` and run automatically on first container start.
They create the PostGIS extension, all tables, indexes, and spatial helper
functions — **do not run them manually**.

### 2.2 Run the data pipeline

```powershell
# Still inside industrial-data/
pip install -r requirements.txt

# Phase 1 — boundaries (India / states / districts)
python scripts/run_boundaries.py

# Phase 2 — OSM extraction (one district at a time)
python scripts/run_osm_district.py --district-name "Ahmedabad" --dry-run

# Phase 3 — government dataset ingestion
python scripts/run_government_ingest.py --input path/to/source.csv \
    --table-name government_industries_source_a \
    --source-id-column source_id --name-column name \
    --industry-type-column industry_type --dry-run

# Phase 4 — entity resolution → populates industrial_sites
python scripts/run_entity_resolution.py \
    --government-table government_industries_source_a --dry-run
```

Remove `--dry-run` to write to the database.

### 2.3 Switch the backend to PostGIS

```powershell
cd industrial-gis

# Create backend/.env from the example
copy backend\.env.example backend\.env
```

Edit `backend/.env`:

```dotenv
DATA_MODE=postgis
DATABASE_URL=postgresql+psycopg2://industrial:industrial@localhost:5432/industrial_data
ALLOWED_ORIGINS=http://localhost:5173
```

Restart the backend — the startup log will show `data_mode=postgis` and the
status bar badge in the UI will change from **🔬 Synthetic** to **🗄 PostGIS**.

---

## 3 — Environment variables

### Backend (`industrial-gis/backend/.env`)

| Variable | Default | Required |
|---|---|---|
| `DATA_MODE` | `synthetic` | No |
| `DATABASE_URL` | `postgresql+psycopg2://postgres:postgres@localhost:5432/industrial_gis` | Only in `postgis` mode |
| `ALLOWED_ORIGINS` | `*` (all origins) | No — restrict in production |

See [`backend/.env.example`](industrial-gis/backend/.env.example) for the full reference.

### Frontend (`industrial-gis/frontend/.env.local`)

| Variable | Default | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | `/api` (Vite proxy) | Override to connect directly to the backend |

See [`frontend/.env.example`](industrial-gis/frontend/.env.example) for usage.

---

## 4 — API reference

All endpoints are prefixed with `/api`.  Interactive docs at
`http://localhost:8000/api/docs`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | `{"status":"ok","data_mode":"synthetic"\|"postgis"}` |
| `GET` | `/api/industries` | GeoJSON FeatureCollection with optional filters |
| `GET` | `/api/industries/search` | Full-text search (requires `?q=`) |
| `GET` | `/api/industries/{site_id}` | Single site GeoJSON Feature |
| `GET` | `/api/statistics` | Aggregate counts by state, type, confidence |
| `GET` | `/api/filters/options` | Dropdown values for all filter fields |
| `GET` | `/api/boundaries/{level}` | Boundary GeoJSON (`india` / `states` / `districts`) |

### Bbox parameter

All list endpoints accept `?bbox=west,south,east,north` (EPSG:4326).
In PostGIS mode this uses `ST_Intersects + ST_MakeEnvelope` against the
GIST-indexed `geometry` column.  In synthetic mode it filters the in-memory
dataset geometrically.

---

## 5 — Running tests

### Backend

```powershell
cd industrial-gis
python -m pytest backend/tests/ -v
```

Current result: **32/32 passed** (no database required — all tests run against
`DATA_MODE=synthetic`).

### Frontend — TypeScript compilation

```powershell
cd industrial-gis/frontend
npx tsc --noEmit
```

Current result: **0 errors**.

### Frontend — production build

```powershell
cd industrial-gis/frontend
npm run build
```

Current result: **built in ~1.6 s, 369 kB JS bundle**.

---

## 6 — Feature status

### Completed (this milestone)

| Feature | Status |
|---|---|
| Interactive Leaflet map centred on India | ✅ |
| CartoDB Positron basemap | ✅ |
| MarkerCluster (colour-coded by density) | ✅ |
| Industry-type emoji markers | ✅ |
| Confidence legend (bottom-left overlay) | ✅ |
| Click marker → IndustryDetails side panel | ✅ |
| Match-score animated bar | ✅ |
| OSM / Government provenance links | ✅ |
| Debounced auto-search (420 ms) | ✅ |
| State / District / Industry type / Status filters | ✅ |
| District cascade (filtered by selected state) | ✅ |
| Active filter pills with individual clear buttons | ✅ |
| Statistics cards (6 metrics with hover animation) | ✅ |
| Analytics panel (Sites by State / Industry bar charts) | ✅ |
| Bbox-based map loading (debounced 650 ms on moveend) | ✅ |
| Backend health probe + offline banner + Retry | ✅ |
| Dynamic data-mode badge (Synthetic / PostGIS) | ✅ |
| Zero-results overlay | ✅ |
| CORS from `ALLOWED_ORIGINS` env var | ✅ |
| `DATA_MODE` dispatcher (synthetic ↔ PostGIS, no code change) | ✅ |
| PostGIS repository with ST_Intersects + ST_MakeEnvelope | ✅ |
| TypeScript strict mode, 0 errors | ✅ |
| 32 backend pytest tests | ✅ |
| Vite production build | ✅ |

### Remaining work (future phases)

| Item | Notes |
|---|---|
| Populate `industrial_sites` with real OSM + government data | Run the `industrial-data` pipeline phases 2–4 |
| State / district boundary GeoJSON layers on map | `/api/boundaries/*` endpoints exist; add Leaflet GeoJSON layer |
| Confidence-level filter dropdown | Backend already supports `?confidence=` |
| Pagination / virtual loading for >500 sites | Add `limit` / `offset` or cursor to `/api/industries` |
| GeoJSON / CSV export of filtered results | New endpoint or client-side blob download |
| Production deployment | Nginx reverse proxy, gunicorn workers, static frontend build |
| Satellite / remote-sensing integration | Future phase |

---

## 7 — Current data source

> **The web application currently uses synthetic data.**
>
> The 30-site dataset in `backend/app/services/catalog.py` is a hand-crafted
> representative sample.  It is labelled `data_sources: ["openstreetmap", "government"]`
> for display purposes only — these records were **not** extracted from the real
> OSM database or any government registry.
>
> The `/api/health` response includes `"data_mode": "synthetic"` so this is
> always explicit.  The badge in the application UI also shows **🔬 Synthetic**.
>
> Switch to `DATA_MODE=postgis` after running the `industrial-data` pipeline to
> work with real data.

---

## 8 — Troubleshooting

**Backend won't start — `DATA_MODE` error**
: Set `DATA_MODE=synthetic` or `DATA_MODE=postgis` in your environment or
  `backend/.env`.  Any other value is rejected at startup.

**`DATABASE_URL is not configured`**
: This appears only in `postgis` mode.  Either set the variable or switch to
  `DATA_MODE=synthetic`.

**Frontend shows "Backend unavailable" banner**
: The backend is not running or not reachable on port 8000.  Check that
  `uvicorn` is running and the Vite proxy target matches (`http://127.0.0.1:8000`
  in `vite.config.ts`).

**Markers not visible on map**
: Open browser devtools → Network.  If `/api/industries` returns 0 features,
  check your bbox / filter settings or click **Reset**.

**PostGIS container not healthy**
: Run `docker compose logs postgis` inside `industrial-data/` and check for
  authentication or volume errors.

---

---

## 9 — Data Pipeline (`industrial-data`)

The `industrial-data` module is an end-to-end, production-grade geospatial data pipeline that populates the PostGIS `industrial_sites` table and generates the final master GeoJSON datasets consumed by the Web GIS application.

### Unified Pipeline Execution

The pipeline is fully orchestrated via a single command:

```powershell
# Run pipeline for a single state (OSM extraction, cleaning, matching, master generation)
python run_pipeline.py --state "Uttar Pradesh" --skip-government

# Run with refresh mode (re-extracts Overpass, preserves first_seen history, updates in-place)
python run_pipeline.py --state "Uttar Pradesh" --refresh --skip-government

# Dry-run mode (runs all processing in-memory without writing to database or disk)
python run_pipeline.py --state "Uttar Pradesh" --dry-run --skip-government

# With a configured government dataset
python run_pipeline.py --state "Uttar Pradesh" \
    --government-csv data/raw/up_industries.csv \
    --government-name-column "FacilityName" \
    --government-type-column "IndustryType"

# India-level mode (processes all 36 states and union territories sequentially with checkpoint resume)
python run_pipeline.py --state ALL --skip-government
```

### The 8 Pipeline Steps

```
[1/8] Loading State Boundary      → Loads boundary from PostGIS states table
[2/8] Loading District Boundaries  → Discovers all intersecting districts (e.g. 75 in UP)
[3/8] Fetching OSM Industrial Data → District-by-district Overpass API queries with checkpoints & rate-limiting
[4/8] Loading Government Data      → Dynamic source adapters with schema validation
[5/8] Cleaning Data                → Cross-district deduplication & taxonomy classification
[6/8] Matching Records             → Scalable candidate blocking (spatial radius + STRtree)
[7/8] Creating Master Dataset      → Merges automatic matches & preserves singletons
[8/8] Saving Results               → PostGIS transactional upsert & GeoJSON export
```

### Key Engineering Features Implemented

- **District-by-District Extraction**: Scalable Overpass API client that iterates district boundaries with exponential backoff, rate limiting, and `.complete` sentinel checkpoints.
- **Multipolygon Relation Support**: Reconstructs complex industrial estate polygons from OSM outer/inner member ways using Shapely `polygonize`.
- **Change Detection & Refresh Diff**: Tracks feature updates using `osm_version`, retains deleted features with `not_seen_on_refresh`, and preserves `first_seen` timestamps.
- **Reproducible Government Adapters**: Standardized adapter pattern (`src/government/sources/`) with validation error reporting and honest unavailable source handling.
- **Controlled Industry Taxonomy**: Maps raw strings into standardized industrial codes (`TEXTILE`, `CHEMICAL`, `STEEL_METAL`, `FOOD_PROCESSING`, etc.) via YAML taxonomy.
- **Scalable Candidate Blocking**: Replaces $O(N \times M)$ brute force matching with spatial radius STRtree blocking, reducing candidate pairs by up to 99%.
- **Robust Site Identity (Canonical Anchor)**: Derives UUIDs from the single most permanent source key. Adding or matching new records to an existing facility never changes its `site_id`, preventing foreign key drift.
- **Master Dataset Assembly**: Confirmed matches are clustered into master industrial sites; unmatched records are retained as singletons (`source_count = 1`).
- **GeoJSON Export & Validation**: Serializes all master sites to `data/processed/<State>/master_industrial_sites.geojson` with strict RFC 7946 validation.
- **Non-Destructive PostGIS Upsert**: Uses `merge_temporal_snapshots()` to update database records in-place without deleting existing rows.


