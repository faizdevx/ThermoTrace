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
  <img src="https://img.shields.io/badge/OpenStreetMap-OSM-7EBC6F?logo=openstreetmap&logoColor=white">
  <img src="https://img.shields.io/badge/NASA%20FIRMS-ThermalDetection-E03C31?logo=nasa&logoColor=white">
  <img src="https://img.shields.io/badge/Sentinel--2-RemoteSensing-003247?logo=esa&logoColor=white">
  <img src="https://img.shields.io/badge/WorldCover-LandCover-003247?logo=esa&logoColor=white">
  <img src="https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=black">
  <img src="https://img.shields.io/badge/Leaflet-GISMapping-199900?logo=leaflet&logoColor=white">
  <img src="https://img.shields.io/badge/TypeScript-Frontend-3178C6?logo=typescript&logoColor=white">
  <img src="https://img.shields.io/badge/Docker-Containerization-2496ED?logo=docker&logoColor=white">
</p>

# ThermoTrace - India industrial intelligence + thermal-source classification

ThermoTrace is a Web GIS that does two things on one map:

1. **Industrial sites** - industrial facilities mapped in OpenStreetMap, stored in PostGIS and kept in sync by a background worker (state/district assignment, filters, search, exports, live updates).
2. **Thermal sources** - NASA FIRMS (VIIRS 375 m) heat detections grouped into persistent *sources* and classified as **industrial vs. agricultural vs. forest fire**, using the facility map and ESA WorldCover land cover, with a plain-language explanation for every decision.

> FIRMS shows *where heat was seen*, not *why*. ThermoTrace scores competing explanations from physical evidence (persistence, night activity, radiative power, footprint, nearby facility, land cover, season). The weights are **expert-set priors, not trained on labelled ground truth**, and the output is a decision-support hint to be confirmed with imagery - never an official determination.

---

## Dashboard

![ThermoTrace dashboard: thermal sources by class over the industrial-sites map](docs/images/dashboard.png)

_Live PostGIS-backed dashboard captured 2026-10-10: 2,794 OSM industrial sites (north-east pilot) and 3,159 classified FIRMS thermal sources from 12,676 VIIRS detections, 2 alerts, newest detection ~6 h old. Filled circles are thermal sources (size = radiative power, colour = class, dark ring = alert); numbered discs are clusters; the left panel filters by class, window, minimum power and alerts, and exports CSV/GeoJSON._

![Sentinel-2 true-colour and SWIR chips for a persistent industrial thermal source](docs/images/sentinel2_chip.png)

_Clicking a thermal source shows a Sentinel-2 chip pair from the clearest scene within 5 days (here a coal mine in Odisha: the SWIR composite shows glowing, burning coal seams)._

## Findings

All figures are produced by `industrial-data/scripts/make_figures.py` from the real result files. Read the caveats: labels are weak and rule-derived, incident locations are partly coarse, and one snapshot of one week is not a trend.

**1. A trained model fixes the rules' main weakness.** On held-out states and months the gradient-boosted model reaches macro-F1 0.87 against 0.66 for the rules. The rules call 459 of 827 persistent industrial windows "fire candidate" (recall 0.42) and send 142 of 421 persistent unmapped sources to "other" (recall 0.36); the model recovers them (recall 0.83 and 0.88) at the price of slightly lower agricultural recall (0.87 vs 0.96).

![Per-class F1 for rules, model and thermal-only features](docs/images/f1_by_class.png)
![Confusion matrices, rules vs model](docs/images/confusion_matrices.png)

**2. Context, not satellite behaviour alone, separates the classes.** Thermal-behaviour features alone (persistence, night share, power, footprint) score macro-F1 0.34; adding facility proximity and land cover gives 0.87. FIRMS tells you where heat is, the facility map and land cover tell you why. This is also a caution: those context features define the labels, so the score is agreement with the labelling rules.

**3. Radiative power does not separate industrial from agricultural fires.** Median peak power per source is 3.1 MW for persistent industrial, 3.2 MW for crop burning and 3.4 MW for vegetation fire; night share and persistence do the separating (median night share 1.0 for the industrial classes, 0.0 for crop and forest fires).

![Thermal source classes, power and persistence](docs/images/thermal_overview.png)

**4. Persistent "industrial" heat in this pilot is mostly coal.** Of 152 persistent industrial sources in the 2026-10-02 to 2026-10-08 run, 86 sit on OSM mining/quarry features and 42 on thermal power plants (only 2 refineries, 2 steel plants, 3 cement works). By state: Jharkhand 33, Odisha 28, Chhattisgarh 16, Maharashtra 14, Rajasthan 10. Burning coal seams and overburden dumps are a heat source a facility list alone would not explain.

**5. Satellite evidence for documented incidents is thin and depends on location quality.** Of 40 documented industrial fires, 17 could not be located well enough to test. Of the 23 tested, 9 show an anomalous VIIRS detection, 6 sit on a site that is routinely hot (a refinery fire is not separable from baseline at 375 m), and 8 show nothing. The two strongest signals (Vatva GIDC 62 MW, Igatpuri/Jindal Poly Films 54 MW) are industrial-estate or town-level locations; facility-level matches are mostly under 7 MW. The bottleneck is verified facility coordinates, not the satellite archive.

![Incident matching results](docs/images/incident_matching.png)

**6. Honest limits of the numbers.** One train/test split, one seed, S-NPP only, no confidence intervals; "industrial fire candidate" means an episodic detection at a facility, not a confirmed fire; the trained model is not yet in the live pipeline.

Regenerate: `python industrial-data/scripts/train_classifier.py && python industrial-data/scripts/make_figures.py` (matplotlib is only needed for the figures); the dashboard screenshot comes from `industrial-data/scripts/capture_dashboard.py` against a running app.

## What is implemented (and verified)

| Area | Status |
|---|---|
| FIRMS ingestion (S-NPP, NOAA-20, NOAA-21 VIIRS NRT; public CSV or `MAP_KEY` API) | working, run against real data |
| Clustering into thermal sources, facility matching, land-cover sampling, explainable classifier | working, 24 unit tests |
| Thermal layer on the map, class filters, alert flag, source detail panel with reasons + detection timeline, Sentinel-2 true-colour/SWIR chip (clearest scene within 5 days, < 30 % cloud, `/api/thermal/sources/{id}/imagery`), CSV/GeoJSON export | working (frontend tests + browser check of the map layer) |
| Snapshot **and** PostGIS storage (`thermal_sources`, `thermal_detections`, `thermal_runs`) | PostGIS path run against a real PostGIS container |
| OSM industrial sites: baseline import + incremental replication (osm2pgsql) with durable checkpoint, locking, backoff, revisions | 23 lifecycle tests; baseline imported (below); catching up to the live diff feed **not observed to completion** |
| REST API, SSE change notifications with polling fallback, live-status panel | tests below |
| Government sources (India Industrial Land Bank, ICLP, NCoG) | **not integrated** - see [Data sources](#data-sources) |

### Measured results (2026-10-09, real data, not fabricated)

* **Industrial sites (pilot)**: 2,794 active OSM industrial features from the Geofabrik *North-Eastern Zone* extract (Assam 2,160, Tripura 230, Arunachal 77, Sikkim 63, Meghalaya 60, Manipur 39, Nagaland 28, Mizoram 26, West Bengal 6; 105 fall outside the geoBoundaries state polygons and show as *Unassigned*). This is a regional pilot, **not national coverage**.
* **Thermal run** (VIIRS, 2026-10-02 → 2026-10-08, India): 14,437 detections in the South-Asia files → 11,114 inside India → **2,823 thermal sources**, matched against 19,497 thermally relevant OSM facilities (23,014 fetched, India-wide):

  | Class | Sources |
  |---|---|
  | Agricultural burning | 1,983 |
  | Forest / vegetation fire | 367 |
  | Persistent industrial thermal source | 152 |
  | Possible industrial fire / abnormal event | 126 |
  | Persistent, no mapped facility | 106 |
  | Other | 89 |

  The persistent-industrial list is plausible: it is dominated by Odisha/Jharkhand coal mines, plus e.g. Tata Steel Meramandali, Jindal Raigarh and Jojobera power stations - night-time detections on 7 of 7 days. The rule-based classes were then checked against the incident list and a trained model (below).
* **Documented industrial-fire incidents vs NASA FIRMS** (40 public-source incidents, 2016-2025, matched against the VIIRS S-NPP/NOAA-20 archive; script `industrial-data/scripts/match_incidents.py`). Event window = date -1 / +2 days, baseline = 60 to 8 days before, radius = location uncertainty + 0.5 km, "routine" = active on >= 30 % of baseline days. Query parameters are stored with the results.

  | Outcome | Incidents |
  |---|---|
  | Not assessable (no verified facility/area location) | 17 |
  | Anomalous VIIRS detection near the incident (quiet baseline) | 9 |
  | No detection | 8 |
  | Detection but the site is routinely hot (cannot attribute) | 6 |

  The 9 anomalous matches: Jamnagar FCCU flash fire 2016-11-24 (1 det., 3.3 MW), Panipat Naphtha Cracker 2018-01-22 (3, 6.0 MW), Mathura refinery 2020-01-07 (1, 0.6 MW), Vatva GIDC 2020-12-09 (8, 62.4 MW), Numaligarh hydrocracker 2023-05-29 (1, 0.5 MW), Barauni ETP 2024-02-10 (4, 6.5 MW), plus three locality-level matches (Bengaluru 2020-11-10, Kundli 2022-04-17, Igatpuri/Jindal Poly Films 2023-01-01: 17 det., 54.1 MW). **Read with care:** facility-level matches are mostly under 7 MW, near the VIIRS detection floor; locality-level matches use a 5 km centroid, so nearby unrelated heat can match; a match is *a* thermal anomaly at the right place and time, not proof of the reported fire; no detection does not mean no fire (375 m pixels, cloud, small fires between passes). Only 11 of 40 incident locations are facility-level (OSM named objects), 4 industrial-estate level, 8 locality, 17 unlocated.
* **Trained classifier vs rules** (`docs/EVALUATION.md`, `industrial-data/scripts/train_classifier.py`). Gradient-boosted trees (scikit-learn `HistGradientBoostingClassifier`) on 7-day source windows from the S-NPP archive (2025-07 to 2026-10, 562k detections in India). Strict hold-out: test = 11 held-out states AND windows from 2026-04-01; train = other states and earlier windows (5,212 train / 2,007 test windows).

  | | macro-F1 | accuracy |
  |---|---|---|
  | Rule classifier | 0.656 | 0.607 |
  | Gradient-boosted model | **0.870** | **0.877** |
  | Gradient-boosted, thermal-behaviour features only | 0.338 | 0.444 |

  Per-class F1 rules -> model: industrial persistent 0.58 -> 0.89, industrial fire candidate 0.45 -> 0.71, persistent no mapped facility 0.51 -> 0.90, agricultural burning 0.91 -> 0.93, vegetation fire 0.83 -> 0.93. **These labels are weak and rule-derived, not ground truth:** they come from each ~2 km cell's long-run activity over the archive, OSM facility proximity and WorldCover land cover, and the last two are also model inputs, so the scores measure agreement with the labelling rules on unseen regions/months, not real-world accuracy. The thermal-only score (0.33) shows satellite behaviour alone barely separates the classes. "Industrial fire candidate" means *episodic detection at a facility*, not a confirmed fire. One split, one seed, S-NPP only, no confidence intervals. The trained model is not yet wired into the live pipeline; the live app still uses the rule classifier.
* **Real-time chain, measured once on 2026-10-09 (single observation, not a benchmark):** the thermal worker polls FIRMS every 10 min and publishes only when new detections arrive. For a batch of 40 new detections the FIRMS file was published 07:45:42 UTC and committed to PostGIS at 07:48:57 (**195 s**); the `thermal` SSE event reached the API stream ~0.02 s after the commit. Worst case is the poll interval plus ~17 s of processing; the satellite-to-file delay (~3 h) is upstream and not under our control. The OSM worker checks Geofabrik every 5 min and was caught up (cursor = source head), but Geofabrik regional diffs are published about **once a day**, so a new-OSM-edit-to-screen lag of hours is inherent to that source; no OSM diff was applied during this check. Browser repaint delay was not measured.
* The lifecycle tests assert the mechanics (notification only after commit, checkpoint never advances on failure, unchanged data publishes nothing); they are not latency benchmarks.

---

## Facility database (OSM + WRI + Global Energy Monitor + VIIRS Nightfire)

Facilities are merged into one table with provenance: `facilities` (one row per physical facility) and
`facility_sources` (one row per source record: `source`, `source_id`, licence, attribution, distance to the anchor).
Records of a compatible kind within 500 m are one facility; authoritative sources (GEM, WRI) supply the name and
coordinates, OSM fills the rest. Nothing is downloaded by the code: each file is obtained under its own terms.

```bash
python -m src.facilities.cli build --osm data/thermal/sites_india.geojson     --wri data/facilities/raw/global_power_plant_database.csv     --gem data/facilities/raw/gem_coal_plants.xlsx:coal_plant --gem data/facilities/raw/gem_steel.xlsx:steel     --vnf data/facilities/raw/vnf_flares.csv --out data/facilities [--postgis]
```

Sources you leave out are skipped and the printed source counts say so. The thermal pipeline reads
`data/facilities/facilities.geojson` first (`THERMAL_FACILITIES_GEOJSON`), and `GET /api/facilities` serves it
with per-source licences; the map shows it as the "Facilities" layer with the attribution list.

| Source | Licence | Access |
|---|---|---|
| OpenStreetMap | ODbL-1.0 | existing Overpass cache |
| WRI Global Power Plant Database | CC BY 4.0 | free CSV |
| Global Energy Monitor trackers | CC BY 4.0 (check each tracker) | free download after a form |
| VIIRS Nightfire (EOG) | EOG terms | free account required |

## Architecture

```
NASA FIRMS CSV/API ──► thermal worker ──► snapshot (atomic files) ─┐
OSM facilities  ────►  (cluster, match,   PostGIS (thermal_*) ─────┼─► FastAPI ──SSE/REST──► React + Leaflet
ESA WorldCover  ────►   land cover,       NOTIFY after commit ─────┘
                        classify)
Geofabrik diffs ──► osm2pgsql (append) ──► sync worker ──► industrial_sites (PostGIS, revisioned)
geoBoundaries   ──► states / districts
```

* `industrial-data/` - Python pipeline: `src/sync/` (OSM replication worker), `src/thermal/` (FIRMS classifier), SQL migrations in `sql/migrations/`, legacy batch pipeline (boundaries, entity matching).
* `industrial-gis/backend/` - FastAPI + SQLAlchemy; `industrial-gis/frontend/` - React 18, TypeScript, Vite, Leaflet.
* `docker/`, `docker-compose.yml` - PostGIS, migrations, API, OSM worker, thermal worker, nginx frontend.

## How thermal classification works

1. **Detections → sources.** Detections within ~1 km (DBSCAN, haversine) over the analysis window (up to 14 days; history is kept) form a *source*.
2. **Evidence per source:** days detected, night fraction, radiative power (FRP, MW), footprint (m), distance to the nearest mapped facility inside its matching radius (+500 m for VIIRS geolocation), facility type prior (refinery/steel/power/cement/kiln/mine/landfill/…), ESA WorldCover fractions (crop/vegetation/built/bare) in a 150 m window, month (residue-burning season).
3. **Scoring.** Each of six classes accumulates weighted evidence (`src/thermal/classifier.py`, `WEIGHTS`); a softmax gives *relative* shares. The top class, the margin to the runner-up and the human-readable reasons are stored. Crop/forest classes require land-cover evidence - without it a source stays `other`.
4. **Alerts.** `industrial_fire_candidate` with ≥ 8 MW seen in the last 24 h → `watch`/`high` (≥ 40 MW)/`critical` (≥ 100 MW); or a persistent industrial source whose recent power is ≥ 3× its own mean and ≥ 30 MW.

Classes: `industrial_persistent`, `industrial_fire_candidate`, `agricultural_burning`, `vegetation_fire`, `unmapped_persistent`, `other`.

## Data sources

| Source | Use | Licence / terms | Notes |
|---|---|---|---|
| NASA FIRMS (LANCE) VIIRS NRT | thermal detections | open, attribute NASA FIRMS | host is `firms.modaps.eosdis.nasa.gov` (note *modaps*); ~3 h latency |
| OpenStreetMap via Geofabrik / Overpass | facilities, industrial sites | ODbL 1.0 © OSM contributors | regional diffs are published ~daily; one cached bounded Overpass query for the thermal facility list |
| geoBoundaries gbOpen (IND ADM0/1/2) | states/districts | CC BY 4.0 | |
| ESA WorldCover 2021 v200 | land cover | CC BY 4.0 | read from public COGs |
| India Industrial Land Bank, Industrial Corridor Land Platform, NCoG / UP Industrial Parks GIS | - | - | **Not integrated.** Hosts did not resolve / connect from the build environment on 2026-10-09 and no authorised export was established. They are registered as `not_configured` and never presented as live. |

OSM-derived records are **not** government-verified; the UI says so. Basemap tiles come from `tile.openstreetmap.org` (light use; set `VITE_TILE_URL` for production).

## Quick start

Requirements: Docker with Compose v2 (or Python 3.12 + Node 22 + PostGIS), ~10 GB free disk for the pilot import.

```bash
cp .env.example .env                      # set POSTGRES_PASSWORD (URL-safe)
docker compose up -d --build db migrate api worker thermal frontend
docker compose --profile bootstrap run --rm bootstrap          # one-time OSM baseline (OSM_REGION in .env)
docker compose --profile bootstrap run --rm thermal-bootstrap  # one-time India-wide facility cache for thermal matching
# UI: http://localhost:8080   API docs: http://localhost:8000/api/docs
```

Standalone thermal analysis, no database:

```bash
cd industrial-data
pip install -r requirements.txt
python scripts/fetch_thermal_sites.py --out ../data/thermal/sites_india.geojson   # one bounded Overpass query
THERMAL_SITES_GEOJSON=../data/thermal/sites_india.geojson \
  python -m src.thermal.cli refresh --out ../data/thermal/live                   # writes the snapshot the API serves
```

Development without Docker for the app: run PostGIS yourself, then `uvicorn backend.app.main:app` from `industrial-gis/` (`DATABASE_URL`, `DATA_MODE=postgis`) and `npm run dev` in `industrial-gis/frontend`. `DATA_MODE=synthetic` serves ~30 invented sites for offline UI tests only and the UI labels it *DEMO*.

## API (all under `/api`)

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness, database readiness, dataset counts, data revision (reported separately) |
| `GET /industries`, `/industries/search`, `/industries/{id}`, `/industries/export` | sites; filters `bbox,state,district,industry_type,status,confidence,q`, `limit/offset`; export streams **all** matches |
| `GET /statistics`, `/filters/options`, `/boundaries/{level}` | counts over the filter scope; cascading options; simplified boundaries |
| `GET /thermal/sources`, `/thermal/sources/{id}`, `/thermal/stats`, `/thermal/export`, `/thermal/classes`, `/thermal/status` | thermal layer; filters `thermal_class,bbox,state,site_kind,min_frp,alerts_only,hours` |
| `GET /sync/status`, `/sync/history` | per-source freshness (`live/syncing/delayed/offline/not_configured/awaiting_setup`) |
| `GET /events` | Server-Sent Events: `hello`, `revision`, `sync`, `thermal`; the UI falls back to 10 s polling |
| `POST /admin/sync/trigger` | asks the worker to sync now; needs `ADMIN_TOKEN` (`X-Admin-Token`), disabled when unset |

"Live" never means "the API answers": it means checked recently, caught up with the source, and within the source's freshness target (36 h for daily OSM diffs; FIRMS checked every 10 min with newest data < 12 h old). `/sync/status` and the status panel list OSM and NASA FIRMS side by side with last check, last data change and last commit as separate times.

## Tests

```bash
cd industrial-gis && DATA_MODE=synthetic python -m pytest backend/tests          # API + thermal contracts, no DB
cd industrial-data && python -m pytest tests/thermal                              # classifier/pipeline, no DB/network
TEST_DATABASE_URL=postgresql://user:pw@localhost:5432/postgres python -m pytest tests/sync   # needs PostGIS
cd industrial-gis/frontend && npx tsc --noEmit && npm test && npm run build
```

Results on the development machine (2026-10-09): backend `54 passed, 30 skipped` (the 30 PostGIS tests need a database); `tests/thermal` 24 passed; `tests/sync` 23 passed; frontend `tsc` clean, vitest 27 passed. The PostGIS API contract suite (`backend/tests/postgis`) was run once against a real PostGIS (2 of its 3 first tests passed before one expected count was corrected) and **not re-run to completion**; CI runs it. The legacy batch-pipeline suite (`industrial-data/tests`, excluding `sync`/`thermal`) has **15 failing tests** that predate this work and were not fixed. `docker compose build frontend` was not verified.

## Configuration

See `.env.example`. Key variables: `POSTGRES_*`, `ALLOWED_ORIGINS`, `ADMIN_TOKEN`, `OSM_REGION`, `OSM_POLL_INTERVAL_SECONDS`, `OSM_FRESHNESS_TARGET_SECONDS`, `FIRMS_MAP_KEY` (optional), `THERMAL_INTERVAL_SECONDS` (default 600), `THERMAL_WINDOW_DAYS`, `VITE_TILE_URL`. Never commit `.env`.

## Known limitations

* The live classifier is rule-based with expert priors. A trained model beats it on a held-out split, but against **weak rule-derived labels, not ground truth**; incident evidence covers only 23 of 40 documented incidents (17 could not be located) and is mostly near the detection floor. Agricultural vs. other low-power vegetation fires and industrial vs. unmapped industry depend on OSM completeness.
* VIIRS is 375 m and ~3 h behind real time; a short-lived industrial event can fall between overpasses. Persistence needs history: the first run only has the 7 days FIRMS publishes (history accumulates afterwards).
* Facility matching uses a type-based radius around a representative point in standalone mode; large plants can be missed or over-matched. In PostGIS mode polygon equivalent radius is used.
* OSM sites cover only the imported region; thermal facility matching is India-wide. 105 pilot sites sit outside geoBoundaries' state polygons (neighbouring countries / border simplification).
* OSM industrial tags describe land use or features, not confirmed operation; `status` is `mapped` unless disused/abandoned/construction is tagged.
* Regional OSM diffs are published about daily, so freshness of the sites layer is bounded by the source, not the worker.
* Government land-bank sources are not integrated (see above).
* Docker Desktop needs the C: drive free-space headroom while importing; the pilot database is ~2 GB, a larger zone needs proportionally more.

## Licence and attribution

Code: see `LICENSE`. Data: © OpenStreetMap contributors (ODbL 1.0); NASA FIRMS / LANCE; ESA WorldCover 2021 © ESA, CC BY 4.0; geoBoundaries CC BY 4.0; basemap tiles © OpenStreetMap contributors.
