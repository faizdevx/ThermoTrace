# ThermoTrace - PS 26162 (NTRO) - one-page summary

**Problem.** NASA FIRMS shows where heat was seen, not why. NTRO needs industrial fires and persistent industrial heat separated from forest fires, crop burning and other natural heat, with GIS storage and map overlays.

**Solution.** FIRMS VIIRS detections -> clustered sources -> matched to 12,762 facilities (OSM + WRI + flare sites) and ESA WorldCover land cover -> compared with the site's own 365-day baseline -> rule classifier + trained model (second opinion, reasons in words) -> alert rule -> Sentinel-2 / Landsat evidence -> analyst Confirm / Reject / Unsure -> PostGIS + web map.

| Expected deliverable | Where it is |
|---|---|
| i. Classify and segregate industrial fires from forest and other natural fires | 5 classes (persistent industrial, possible industrial fire, agricultural burning, forest/vegetation fire, unmapped persistent); alerts for abnormal industrial heat; cropland-by-day veto; model + rules; docs/EVALUATION.md |
| ii. GIS storage and visualisation as map overlay | PostGIS tables; Leaflet map with heat-density grid, source points, facility and boundary layers, dashboard, alert inbox, PDF incident report, offline vector basemap |

**Measured, with limits.** Macro-F1 0.993 (model) vs 0.809 (rules) on held-out regions and months - agreement with rule-derived labels, not field accuracy; 0.633 for a model with thermal behaviour only. 34 documented industrial fires: satellite saw 20; replayed through the live pipeline 22 have a detection, 5 raise an alert. Alerts 1-3 minutes after FIRMS publishes (data itself ~3 h after overpass).

**Extras.** Replay of real past incidents (Vatva GIDC, Barauni refinery, Amudan Chemicals, a gas flare, a Punjab crop-burning peak); accounts, roles and a hash-chained audit log; offline mode; backups.

**Not done.** Public hosted URL; real identity provider; real Telegram test; load test.

Code: https://github.com/faizdevx/ThermoTrace - Dataset: https://firms.modaps.eosdis.nasa.gov/map
