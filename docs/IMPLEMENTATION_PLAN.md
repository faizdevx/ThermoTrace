# ThermoTrace - Final Implementation Plan

**Goal:** win the NTRO problem statement *"AI-Based Detection and Classification of Industrial Fires and Persistent Thermal Sources Using NASA FIRMS, OSM & Satellite Data"*.

**Required deliverables (from the statement)**
1. Classify and separate industrial fires from forest and other natural fires.
2. GIS-based storage and visualisation of the output as a map overlay.

**Current state:** FIRMS ingestion, clustering, OSM facility matching, WorldCover land cover, PostGIS storage, live map and SSE updates already work. Missing: a trained model, validation, satellite imagery, a demonstrated industrial-fire catch, alert delivery, and a demo judges can open.

Phases are in priority order. **MUST** items decide the result; **SHOULD** items strengthen it; **COULD** items are polish.

---

## Phase 0 - Clean foundation (MUST, ~1 day)

| Task | Where | Done when |
|---|---|---|
| Fix the frontend Docker build (`.dockerignore` already added; verify `npm ci` + `vite build` in image) | `docker/frontend.Dockerfile` | `docker compose build frontend` succeeds |
| Get CI green on GitHub (API, PostGIS, frontend jobs) | `.github/workflows/ci.yml` | all 3 jobs green on `main` |
| Re-run PostGIS API suite to completion and fix failures | `industrial-gis/backend/tests/postgis` | 0 failures |
| Decide on the 15 legacy pipeline test failures: fix or delete the dead batch code they cover | `industrial-data/tests` | `pytest industrial-data/tests` green |
| Move the thermal facility index into PostGIS (one table `facilities`) instead of a side GeoJSON | new migration `005_facilities.sql`, `src/thermal/sites.py` | thermal matching reads only PostGIS |
| Drop the North-East OSM site layer from the main story (keep code) or hide it behind a toggle | `App.tsx` | default map shows thermal layer + facilities only |
| Remove README badges for tech not used (Redis, Celery, MapLibre, Plotly) - **team decision**, the banner itself stays | `README.md` | every badge matches the code |

---

## Phase 1 - Authoritative facility database (MUST, ~1.5 days)

OSM alone is too incomplete for Indian industry. Merge citable sources into `facilities`, keeping `source`, `source_id`, licence and `kind`.

| Source | Gives | Access |
|---|---|---|
| WRI Global Power Plant Database | thermal plants with fuel + capacity | free CSV (CC BY 4.0) |
| Global Energy Monitor trackers (steel, coal plants, coal mines, oil & gas) | plant locations + status | free download after form (check licence per tracker) |
| VIIRS Nightfire (VNF) gas-flare sites, Earth Observation Group | persistent flare locations | free account required |
| OpenStreetMap (existing Overpass cache) | refineries, cement, kilns, landfills, quarries | ODbL |

- Deduplicate across sources by distance (≤ 500 m) + kind; keep every source id (`facility_sources` table).
- Store polygons where OSM has them; points elsewhere with a kind-based radius (as now).
- **Done when:** every one of these is in PostGIS with provenance, and the facilities layer shows on the map with source attribution.

---

## Phase 2 - Long baseline from the FIRMS archive (MUST, ~1 day)

Separating an always-on flare or kiln from a new fire needs months of history, not 7 days.

- Get a free FIRMS `MAP_KEY`. Pull 12 months of VIIRS (S-NPP + NOAA-20) for India through the area API in day-range chunks, respecting the documented request limits. Use the archive download request for older standard-processing data if needed.
- Load into `thermal_detections` (already exists) with a `product` column (NRT vs archive).
- Compute per-facility and per-1 km-cell baselines in SQL (materialised view `thermal_baseline`):
  - active-day fraction over 365 days, monthly profile, median and 95th-percentile FRP, typical night share.
- **Done when:** every current source shows "normally active X% of days, typical FRP Y MW", and a z-score of today versus its own baseline.

---

## Phase 3 - Labelled evaluation set (MUST, ~1.5 days) - the single most important phase

Without this there is no accuracy number, and judges will ask for one.

**Label sources (weak but defensible):**

| Class | Label rule |
|---|---|
| `industrial_persistent` | within 1 km of a GPPD/GEM/VNF facility AND active on ≥ 20% of baseline days |
| `agricultural_burning` | WorldCover cropland ≥ 70%, Punjab/Haryana/western UP, Oct-Nov or Apr-May, no facility within 3 km |
| `vegetation_fire` | WorldCover tree/shrub ≥ 70%, inside a forest area, confirmed by MODIS burned area (MCD64A1) |
| `industrial_fire` (incident) | **hand-curated list** of documented industrial fire events (date, facility, coordinates, news/official source URL); target 30-50 events |

- Store in `labels(source_id or detection cluster, label, label_source, evidence_url, labelled_by)`.
- **Rules:** label features must not be classifier inputs in the same form (avoid leakage). Split train/test **by region and by time**, never randomly.
- Curated incidents must each cite a real public source. No invented events.
- **Done when:** ≥ 2,000 weak labels across 4 classes + ≥ 30 verified incidents, versioned in `data/labels/` (not in git if licence forbids) with a checksum.

---

## Phase 4 - Real ML model + measured accuracy (MUST, ~2 days)

- **Model:** gradient-boosted trees (LightGBM or scikit-learn `HistGradientBoostingClassifier`) on per-source features:
  - persistence (days, active fraction vs baseline), night share, FRP stats, FRP z-score vs baseline, footprint, VIIRS `bright_ti4 - bright_ti5`, scan/track pixel size
  - facility: kind, distance, source count
  - land cover fractions, month, latitude band
  - later, Sentinel-2 indices from Phase 5
- **Outputs:** class probabilities (calibrated with isotonic regression on validation data), SHAP top-5 reasons in plain language (replace the hand-written reason text), and a model version stored with each prediction.
- **Keep the rule engine** as baseline and fallback; show both in the evaluation.
- **Evaluation report**, auto-generated (`scripts/evaluate.py` produces `docs/EVALUATION.md` plus charts):
  - per-class precision/recall/F1, macro-F1, confusion matrix, rules vs model
  - industrial-incident recall at a fixed false-alarm rate per day
  - results on held-out regions and held-out months
- New API field `model_version`, plus `/api/thermal/model` returning metrics.
- **Done when:** the model beats the rules baseline on held-out data and the numbers are in the README. If it doesn't, say so and ship the rules.

---

## Phase 5 - Satellite imagery verification (MUST, ~1.5 days)

The statement explicitly asks for satellite imagery.

- Query a free Sentinel-2 L2A STAC catalogue (Element84 Earth Search or Microsoft Planetary Computer) for the clearest scene within ±5 days of the detection, with cloud cover under 30%.
- Read a ~3 × 3 km window from the COGs (rasterio, already a dependency).
- Render two chips per source, true colour and SWIR false colour (B12/B11/B8A, where active flames and flares glow), with a before/after pair where available.
- Compute NBR / dNBR for burn-scar confirmation and SWIR hot-pixel count. Feed both as features into the Phase 4 model.
- Cache chips in object storage or on disk; serve at `/api/thermal/sources/{id}/imagery`.
- Optional: Landsat 8/9 TIRS for an independent thermal check.
- **Done when:** clicking any alert shows dated imagery chips with scene id + cloud %, and dNBR appears in the reasons.

---

## Phase 6 - Industrial-fire alerting + analyst loop (MUST, ~1.5 days)

- **Alert rule:** P(industrial_fire) ≥ threshold, OR a source at a known facility with FRP z-score ≥ 3 versus its own baseline. Severity comes from FRP and facility kind (refinery/LNG/chemical rank higher).
- **Delivery:** webhook + Telegram bot + email (SMTP). Credentials come from env, never committed. Deduplicate per facility per 6 h.
- **Analyst UI:**
  - alert inbox with Confirm / Reject / Unsure buttons and a note field
  - every decision is written to `labels` (closes the loop; feeds retraining)
- **Incident timeline:** first detection, peak FRP, last detection, imagery, analyst decision.
- **Done when:** a test alert reaches Telegram within one worker cycle and an analyst decision appears in `labels`.

---

## Phase 7 - Prove it on a real event (MUST, ~0.5 day)

- **Replay mode:** `python -m src.thermal.cli replay --from <date> --to <date> --speed 60x` feeds archived FIRMS for a documented incident through the live pipeline. The map, alerts and SSE behave as if it were happening now.
- Pick 2-3 curated incidents from the Phase 3 list plus one gas-flare site and one Punjab burning day.
- **Done when:** the replay produces the correct class and an alert for each, recorded in the demo video.

---

## Phase 8 - Judge-facing product (SHOULD, ~1.5 days)

| Task | Done when |
|---|---|
| Declutter the map: zoom-dependent rendering (heat-density grid at low zoom, points at high zoom), facilities as a separate toggle | national view readable at a glance |
| Dashboard tab: alerts today, class mix over time, top states, model metrics card | one screen tells the story |
| Downloadable incident report (PDF) per alert: map, imagery, reasons, timeline | one click from the alert |
| Hosted demo on one VM (docker compose, HTTPS, read-only demo user) | public URL works on a phone |
| 2-minute demo video + 6-slide deck (problem, data, model, accuracy, live demo, impact) | uploaded and linked in README |

---

## Phase 9 - NTRO-grade deployment (COULD, ~1 day)

- Auth (OIDC or local accounts), roles (viewer/analyst/admin), audit log of analyst decisions and exports.
- Offline mode: self-hosted vector basemap (PMTiles) instead of public OSM tiles, cached WorldCover and Sentinel chips, no outbound calls except FIRMS.
- Rate limiting, request logging, backup/restore script for PostGIS.

---

## Schedule (2-3 people, ~12 working days)

| Days | Person A (data/ML) | Person B (backend) | Person C (frontend/demo) |
|---|---|---|---|
| 1 | Phase 0 tests/CI | Phase 0 Docker/facilities table | Phase 0 map cleanup |
| 2-3 | Phase 1 facility merge | Phase 2 archive pull + baseline | Phase 8 map declutter |
| 4-5 | Phase 3 labels + curated incidents | Phase 5 STAC imagery service | Phase 6 alert inbox UI |
| 6-7 | Phase 4 model + evaluation | Phase 6 alert delivery | Phase 5 imagery panel |
| 8 | Phase 4 tuning, SHAP reasons | Phase 7 replay mode | Phase 8 dashboard |
| 9-10 | Phase 7 validation on events | Phase 8 hosting | Phase 8 video + deck |
| 11-12 | buffer / Phase 9 | buffer / Phase 9 | rehearsal |

If time is cut, keep Phases 0, 3, 4, 5, 7 and the hosted demo. Those answer "is it AI", "how accurate", "uses imagery" and "does it catch a real fire".

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Too few verified industrial-fire incidents | combine news-sourced incidents with FRP-anomaly events at known facilities; report incident recall separately with its sample size |
| Weak labels leak into features | build labels from sources/thresholds not used as model inputs; region + time hold-out |
| Cloudy Sentinel-2 in monsoon | widen date window, fall back to Landsat, show "no clear scene" honestly |
| FIRMS API limits / key quota | cache everything in PostGIS, pull archive once, NRT only incrementally |
| GEM / VNF licence terms | store source + licence per record; do not redistribute raw files in git |
| Docker disk pressure on the dev laptop | keep the dev DB on a non-system drive; host the demo on a VM |
| Model does not beat rules | ship rules + publish the comparison; honesty scores better than an unbacked "AI" claim |

---

## Definition of done (what judges will see)

1. Live map of India with classified thermal sources, facilities, and alerts updating without reload.
2. An evaluation report with real precision/recall per class and incident recall, rules vs model.
3. Every alert carries Sentinel-2 imagery, a timeline, a calibrated probability and plain-language reasons.
4. A replayed real industrial fire caught and alerted, shown in the video.
5. A public demo URL, a 2-minute video, a short deck, and green CI.
