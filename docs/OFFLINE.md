# Offline mode

With `OFFLINE_MODE=true` the stack makes **no outbound connection except to the NASA FIRMS hosts**. Everything else it needs is on disk:
the basemap, the fonts, the land-cover and satellite-scene caches, the models and the facility database. The one dependency that cannot be
cached is the heat data itself: FIRMS delivers new detections every few hours; without FIRMS the stack keeps serving its last data and says so.

## What is local

| Needs the internet when online | Offline replacement |
|---|---|
| Basemap tiles (OpenStreetMap volunteers' servers) | **PMTiles** vector basemap, one file served by this stack (`data/basemap/india.pmtiles`, India z0-12 from the Protomaps OpenStreetMap build, 692 MB), drawn in the browser by `protomaps-leaflet`; range requests, no tile server |
| Fonts (Google Fonts) | IBM Plex Sans bundled with the frontend (`@fontsource`) |
| ESA WorldCover (COGs over HTTPS) | the land-cover cache (`landcover_cache.json`, one entry per ~200 m cell); `python -m src.thermal.prefetch landcover` fills it while online |
| Sentinel-2 / Landsat scenes (STAC + COGs) | the imagery cache (one JSON with PNG chips per source and day, written by the worker for alerts and candidates); `prefetch imagery` fills it for existing alerts |
| geoBoundaries (India and state polygons) | cached by `prefetch boundaries` |
| Map tiles for PDF reports | drawn from the same PMTiles file with PIL (`report.basemap_pmtiles`) |
| Facilities, baseline, models, labels | already local (PostGIS, parquet, joblib) |

Build the basemap once, on a machine with internet, and copy it over: `python deploy/make_basemap.py` (`--maxzoom 10` for 130 MB). The script
downloads the official `pmtiles` tool and checks its SHA-256 against the digest GitHub publishes before running it.

## How it is enforced

`src/netguard.py`, installed at start-up by the thermal worker and the API, patches Python's `socket`: resolving or connecting to anything
that is not a FIRMS host, `localhost`, a single-label docker name (`db`) or a private address raises `OfflineViolation` (an `OSError`) and is
counted. `GET /api/system/offline` (and the worker's status) shows the allowed hosts and what was refused. Native libraries do not go through
Python sockets (GDAL reading WorldCover or Sentinel over HTTP, libpq), so the code that would use GDAL asks `netguard.enabled()` first and uses
its cache or says "offline"; libpq only talks to the database container.

Offline behaviour, by design: uncached land-cover cells stay *unknown* for that run (the classifier then says "land cover unavailable") and are
not written to the cache as if they were an answer; imagery is served from the cache and an alert without a cached scene says "Offline mode: no
scene is cached"; nothing is computed and nothing is invented.

## Evidence (2026-10-16, a second local stack with the production and offline overlays)

* **Browser with every outside name unresolvable** (`--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE localhost"`, `industrial-data/scripts/verify_offline_ui.py`): sign-in page, map with the vector basemap, dashboard and admin tab all rendered; the page made 59 requests, **all to localhost, none failed**. Screenshots: `docs/images/offline_map.png`, `login_page.png`, `admin_panel.png`.
* **A full worker cycle with `OFFLINE_MODE=true`**: FIRMS (S-NPP, NOAA-20, NOAA-21) fetched, 8,260 detections clustered and classified, 497 land-cover cells uncached and left unknown, imagery computed for none, result committed; the worker's own report: `external hosts reached {'firms.modaps.eosdis.nasa.gov': 1}, refused {}`.
* **The guard caught a real hidden dependency**: the first offline cycle on an empty volume tried to download the geoBoundaries polygons and was refused (`www.geoboundaries.org`). That is why `prefetch boundaries` exists and is in the list above.
* **PDF report offline**: map panels drawn from the local PMTiles file, imagery section says offline and shows the scene stored with the alert.

## Order of operations for an air-gapped site

1. Online machine: `docker compose up`, let the worker run for a while (it caches land cover and scenes for alerts).
2. `python -m src.thermal.prefetch boundaries`, `landcover` (add `--detections-days 30` for the recent cells), `imagery`.
3. `python deploy/make_basemap.py`; `python deploy/demo_bundle.py make` (or `pg_backup.py backup`).
4. Move images (`docker save`), `data/basemap`, the bundle and the caches (`thermaldata` volume) to the site; `docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.offline.yml up -d`.

## Not done / limits

* Only FIRMS is allowed. A site that cannot reach FIRMS at all needs a mirror of the FIRMS files on its internal network; pointing the worker at a mirror is not implemented (the base URL is a constant in `src/thermal/firms.py`), so today the allow-list (`OFFLINE_ALLOW_HOSTS`) is the only lever.
* The land-cover cache covers cells seen while online; a new fire in a never-seen cell has unknown land cover until the next online prefetch. The full prefetch (every facility, 12,762 points, plus 30 days of detections) was not run here: only the 3,073 current sources were, so 497 cells of the next cycle were unknown.
* The basemap is a snapshot of the Protomaps build of 2026-10-16 (zoom 12, India bounding box); street detail beyond zoom 12 is over-zoomed, not drawn.
* The guard is a defence in depth inside the processes, not a firewall. For a real air gap also block egress at the network (a docker network with `internal: true` plus a proxy that only allows the FIRMS hosts).
