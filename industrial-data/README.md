# Industrial Data

Phase 1 of an India-wide industrial geospatial master dataset pipeline.

This repository currently implements the boundary-processing foundation only:

- GeoBoundaries-based India, state, and district boundary ingestion
- geometry validation and repair
- CRS normalization to EPSG:4326
- PostGIS schema creation and indexing for boundary tables
- synthetic pytest coverage for parsing, repair, CRS conversion, and duplicate removal

## Files created for Phase 1

- [config/config.yaml](config/config.yaml)
- [docker-compose.yml](docker-compose.yml)
- [requirements.txt](requirements.txt)
- [.env.example](.env.example)
- [sql/schema.sql](sql/schema.sql)
- [sql/indexes.sql](sql/indexes.sql)
- [sql/functions.sql](sql/functions.sql)
- [src/boundaries/config.py](src/boundaries/config.py)
- [src/boundaries/source.py](src/boundaries/source.py)
- [src/boundaries/processing.py](src/boundaries/processing.py)
- [src/boundaries/storage.py](src/boundaries/storage.py)
- [src/database/connection.py](src/database/connection.py)
- [scripts/run_boundaries.py](scripts/run_boundaries.py)
- [tests/test_boundaries_phase1.py](tests/test_boundaries_phase1.py)

## What each file does

- [config/config.yaml](config/config.yaml) stores CRS settings, database env keys, logging settings, and the GeoBoundaries boundary-level mapping.
- [docker-compose.yml](docker-compose.yml) starts PostgreSQL with PostGIS and initializes the SQL scripts on first boot.
- [sql/schema.sql](sql/schema.sql) creates the `india_boundary`, `states`, and `districts` tables.
- [sql/indexes.sql](sql/indexes.sql) adds GiST and B-tree indexes for boundary queries.
- [sql/functions.sql](sql/functions.sql) adds a reusable PostGIS geometry repair helper.
- [src/boundaries/source.py](src/boundaries/source.py) fetches GeoBoundaries metadata, downloads GeoJSON, and converts it to a GeoDataFrame.
- [src/boundaries/processing.py](src/boundaries/processing.py) validates, repairs, normalizes, deduplicates, and standardizes the boundary rows.
- [src/boundaries/storage.py](src/boundaries/storage.py) bootstraps the database and writes cleaned GeoDataFrames into PostGIS.
- [src/database/connection.py](src/database/connection.py) reads the database URL from the environment and creates the SQLAlchemy engine.
- [scripts/run_boundaries.py](scripts/run_boundaries.py) is the Phase 1 runner.
- [tests/test_boundaries_phase1.py](tests/test_boundaries_phase1.py) covers repair, duplicate handling, CRS conversion, and GeoJSON parsing.

## Installation

1. Copy [.env.example](.env.example) to `.env` and set `DATABASE_URL` if needed.
2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

3. Start PostGIS:

```bash
docker compose up -d
```

## Database setup

The first database startup runs the SQL scripts in [sql/](sql/). The schema enables PostGIS and creates the boundary tables plus indexes.

## Configuration

Key settings live in [config/config.yaml](config/config.yaml):

- CRS storage/interchange settings
- GeoBoundaries API base URL and dataset type
- level-to-table mappings for India, states, and districts
- validation flags for geometry repair and duplicate removal

Sensitive values must come from environment variables, especially `DATABASE_URL`.

## Running Phase 1

Run all boundary levels:

```bash
python scripts/run_boundaries.py
```

Run only the district layer:

```bash
python scripts/run_boundaries.py --level districts
```

Dry run without writing to PostGIS:

```bash
python scripts/run_boundaries.py --level districts --dry-run
```

## Expected output

The script prints a JSON summary with one entry per processed level, including total rows, valid rows, repaired rows, rejected rows, and duplicates removed.

## Testing

Run the Phase 1 tests:

```bash
pytest -q
```

## Troubleshooting

- If the database URL is missing, set `DATABASE_URL` in your environment or `.env`.
- If GeoBoundaries changes its metadata shape or download payload, update the parsing logic in [src/boundaries/source.py](src/boundaries/source.py).
- If a boundary geometry cannot be repaired, the row is rejected and recorded in the run summary.

## Data provenance

Each boundary row keeps source metadata, source IDs, and timestamps. The pipeline does not overwrite source identity fields during cleaning.

## Known limitations

- The GeoBoundaries metadata structure is assumed to expose a GeoJSON download URL in a current-release response.
- Source dates are only populated when the upstream metadata provides an exact date-like value.
- External boundary downloads are not executed during tests; the test suite uses synthetic GeoJSON.

## Roadmap

Phase 2 will add the OSM extraction pipeline once Phase 1 is confirmed.

## Phase 2 status

The repository now also includes a one-district OSM extraction pipeline based on curated industrial tags, Overpass query construction, response parsing, geometry cleanup, duplicate detection, and PostGIS storage in `osm_industries`.

Suggested run command for the district pipeline:

```bash
python scripts/run_osm_district.py --district-geojson path/to/test_district.geojson --dry-run
```

If you already loaded boundaries into PostGIS, you can use:

```bash
python scripts/run_osm_district.py --district-name "Your District" --dry-run
```

## Phase 3 status

The repository now also includes a modular government-ingestion framework that can read tabular datasets, inspect and standardize their schema, detect coordinate columns, build geometries, normalize common fields, preserve raw records, and write each source to its own PostGIS table.

Suggested run command for a synthetic or real source file:

```bash
python scripts/run_government_ingest.py --input path/to/source_a.csv --table-name government_industries_source_a --source-id-column source_id --name-column name --industry-type-column industry_type --address-column address --state-column state --district-column district --establishment-status-column establishment_status --establishment-date-column establishment_date --dry-run
```

## Phase 4 status

The repository now includes an entity-resolution layer that scores OSM and government records with configurable spatial, name, industry, and address weights; blocks impossible state/district mismatches; stores pairwise match evaluations; and builds a stable `industrial_sites` master table with provenance preserved.

Suggested run command:

```bash
python scripts/run_entity_resolution.py --government-table government_industries_source_a --dry-run
```

## Phase 5 status

The master dataset is now materialized in the PostGIS table `industrial_sites` with stable `site_id`, provenance-bearing source ID arrays, confidence fields, and review flags. Unmatched records remain present as singleton master rows for later analysis.

To build the master dataset without writing results:

```bash
python scripts/run_entity_resolution.py --government-table government_industries_source_a --dry-run
```

## Phase 6 status

Every pipeline execution now emits a JSON data-quality report at `exports/data_quality_report.json`. The report includes geometry validity, CRS consistency, duplicate rate, null and invalid coordinates, assignment coverage, matching rate, unmatched records, suspicious duplicate clusters, and records outside India.

The report is generated automatically at the end of the boundary, OSM, government, and entity-resolution runs.

## Phase 7 status

Temporal support is now explicit across the industrial data pipeline. The source, government, matching, and master tables now carry `extraction_date`, `first_seen`, `last_seen`, and `operational_status` fields where appropriate, and reruns merge those timestamps instead of discarding them.

This is the foundation for answering questions like "what changed at this industrial site, and when?" in later imagery-driven phases.

## Phase 8 status

The PostGIS layer now includes reusable spatial helper functions for distance calculations, district containment checks, candidate pair generation, and duplicate-cluster detection, along with constraints and supporting indexes for the master and match tables.