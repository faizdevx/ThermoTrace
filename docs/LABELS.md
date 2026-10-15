# Labelled evaluation set (`labels-v1`)

**What it is:** 3,281 labels - 3,247 weak rule labels over three classes plus 34 verified industrial-fire incidents -
with a region-and-time split, evidence per label and a checksummed manifest. It is the ground the classifier (rules or
trained model) is scored on. **What it is not:** human-annotated ground truth for the weak classes.

Regenerate: `cd industrial-data && python -m src.labels.cli build` (seeded, deterministic). Verify the files against the
manifest: `python -m src.labels.cli verify`. Files: `industrial-data/data_labels/labels_v1/` (parquet, csv.gz,
`manifest_v1.json`) and the PostGIS table `labels` (migration 007).

## Unit

One label = one **1 km cell (EPSG:6933) x 7-day window** that holds at least one non-low-confidence VIIRS detection
(S-NPP and NOAA-20, SP preferred over NRT, one physical detection counted once). Windows start on 2025-10-01, where both
satellites are in the archive. This is the same notion of "a source in a window" the live pipeline scores.
Incidents are separate units (event date -1 / +2 days, circle of the curated location).

## Classes and rules

| Class | Rule | Inputs the rule reads | Evidence | Train / test |
|---|---|---|---|---|
| `industrial_persistent` | cell within 1 km of a WRI GPPD / GEM / flare-site facility **and** that facility was active on >= 20 % of the 365 baseline days | distance to authoritative facility, facility baseline active fraction | the facility's source (WRI GPPD or World Bank / VIIRS Nightfire) | 610 / 103 |
| `agricultural_burning` | WorldCover cropland >= 70 %; Punjab, Haryana, or Uttar Pradesh west of 79.5 E; window in Oct-Nov or Apr-May; no facility of any source within 3 km; cell active on < 5 % of baseline days | cropland fraction, state, month, distance to any facility, cell baseline | ESA WorldCover 2021 | 804 / 750 |
| `vegetation_fire` | WorldCover tree + shrub + mangrove >= 70 %; no facility within 3 km; cell active on < 5 % of baseline days | woody fraction, distance to any facility, cell baseline | ESA WorldCover 2021 | 812 / 168 |
| `industrial_fire` | documented incident: the source text names event, place and date and the location is resolved to an OSM object | event date, curated location | the incident's source URL | - / 34 (test only) |

A unit that satisfies two rules would be dropped, not guessed (0 such units occurred).

## Split: by region and by time, never random

* `train` = held-in regions **and** window before 2026-04-01.
* `test` = held-out regions **and** window from 2026-04-01.
* Held-out regions (chosen greedily so every class has train and test support): Andhra Pradesh, Chhattisgarh,
  Maharashtra, Punjab.
* Units that are held-out-before-cutoff or held-in-after-cutoff are not part of the set, so no region or period sits on
  both sides. Incidents are test-only: 34 events cannot train anything and they are independent of the rule inputs.

## Leakage policy

The rules read facility proximity, land cover, season, region and 365-day activity - the same things a classifier uses.
Scoring a classifier on these labels with those inputs measures **agreement with the labelling rules**, not accuracy
against the real cause. Therefore:

1. Every class lists its rule inputs (`manifest.rules.<class>.inputs`; `rules.forbidden_inputs(cls)`).
2. Any score against `industrial_persistent`, `agricultural_burning` and `vegetation_fire` must be reported next to an
   ablation that removes those inputs ("blind" model: thermal behaviour only).
3. The only labels independent of the rule inputs are the 34 incidents; their recall is the honest number for
   "does it catch a real industrial fire".

## Incidents (34 verified of 40 in the workbook)

An incident is **verified** when (1) the source text names the event, the place and the date, and (2) its location is
resolved to an auditable OSM object. Source texts were read with a script, plus a real browser for pages behind a
JavaScript check, plus a rendered PDF page; their sha256 are in `data_labels/evidence_index.json` (the page texts
themselves are not stored: publisher copyright).

| Location tier | Meaning | Count |
|---|---|---|
| `facility` | the incident's own plant as a named OSM object | 12 |
| `industrial_area` | OSM estate polygon, named estate place node, or the spine road of a named estate | 11 |
| `locality` | neighbourhood / village / town named in the source, as an OSM place node (uncertainty = its extent) | 11 |

Not verified (6): IN-018 (no OSM object for the plant), IN-022 and IN-025 (PNGRB register names no site), IN-030
(village not named), IN-033 (Jhadmajri not found), IN-035 (Pawane MIDC not found). IN-039 and IN-040 (Reuters is
behind a bot wall) are verified through Deccan Herald reports of the same events; the workbook URL is kept as
`source_url_original`. IN-039's plant is two OSM works 3.3 km apart, so its circle covers both.

## Known deviations from the plan

* **`vegetation_fire` is not confirmed by MODIS MCD64A1 burned area.** The product needs an Earthdata login and the
  University of Maryland mirror is not reachable (FTP blocked, certificate error). The label is "dense woody cover, no
  facility, episodic" - weaker than planned, and `detail.confirmation` says so on every row.
* **Global Energy Monitor trackers are not loaded** (their download form is behind a bot check), so
  `industrial_persistent` rests on WRI GPPD thermal plants and World Bank / VIIRS Nightfire flare sites: 36 facilities,
  433 power-plant windows and 244 flare windows. Steel, cement, refineries and mines are under-represented.
* `industrial_persistent` test support is small (103 windows) and `agricultural_burning` test is a single state
  (Punjab).
* Land cover for the agricultural and vegetation rules was sampled for a seeded subset of candidate cells (8,000),
  not every cell.
* Weak labels use activity over the 365-day baseline (2025-10-01 .. 2026-09-30), which overlaps the windows; it is a
  property of the place, not of the window.

## Licences

FIRMS detections: NASA open data. ESA WorldCover 2021: CC BY 4.0. WRI GPPD and World Bank GFMR flare sites: CC BY 4.0.
`distance_to_any_facility_m` is derived from OpenStreetMap (ODbL 1.0): treat that column as an ODbL-derived value.
Incident source URLs belong to their publishers; only the URL, a hash and a derived location are kept.
