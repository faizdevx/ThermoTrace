**To:** (the address given on the portal / organiser)
**Subject:** PS 26162 - ThermoTrace: AI-based detection and classification of industrial fires and persistent thermal sources (NTRO)

Respected Sir / Madam,

Please find our submission for **Problem Statement 26162 - AI-Based Detection and Classification of Industrial Fires and Persistent Thermal Sources Using NASA FIRMS, OSM & Satellite Data** (National Technical Research Organisation, Theme: Disaster Management).

**Project:** ThermoTrace
**Source code and documentation:** https://github.com/faizdevx/ThermoTrace
**Demo video (2 min):** <paste the unlisted YouTube link here>
**Dataset:** NASA FIRMS VIIRS (S-NPP, NOAA-20/21) - https://firms.modaps.eosdis.nasa.gov/map ; OpenStreetMap, WRI Global Power Plant Database, VIIRS Nightfire flare sites, ESA WorldCover 2021, Copernicus Sentinel-2 and USGS Landsat (all open).

**How it meets the two expected deliverables**

1. *Classification and segregation of industrial fires from forest fires and other natural fires.* Every FIRMS heat spot is grouped into a source, matched to 12,762 mapped facilities (refineries, power plants, steel, mines, cement, chemical works, flares), compared with that site's own 365-day thermal history, and classified as persistent industrial heat, possible industrial fire, agricultural burning, forest/vegetation fire, or unmapped persistent source. A gradient-boosted model gives a calibrated second opinion with reasons in words; the rule classifier is kept as baseline. Alerts fire on abnormal heat at industrial sites and are verified with Sentinel-2 / Landsat imagery (burn index, SWIR hot pixels, surface temperature).
2. *GIS-based storage and visualisation as an overlay over maps.* PostGIS stores detections, sources, facilities, baselines, alerts and labels. The web map (Leaflet) overlays classified sources on a heat-density grid, facility layer and state boundaries, with a dashboard, alert inbox, analyst Confirm / Reject / Unsure loop, PDF incident reports, replay of past incidents, accounts with roles and an audit log, and an offline mode with a self-hosted vector basemap.

**Measured results (stated with their limits)**
- On held-out regions and months the model reaches macro-F1 0.993 against 0.809 for the rules; the labels are rule-derived, so this is agreement with the labelling rules. A model that sees only the thermal behaviour scores 0.633.
- Independent check on 34 documented Indian industrial fires (2016-2025): the satellite saw heat for 20; when replayed through the live pipeline 22 events have a detection and 5 raise an alert. Refineries and other always-hot sites hide a fire inside their normal flaring at 375 m resolution; this is reported, not hidden (docs/EVALUATION.md, docs/REPLAY.md).
- Alerts reach the dashboard about 1-3 minutes after FIRMS publishes; the satellite data itself is about 3 hours behind the overpass.

**Attached**
1. ThermoTrace_deck.pdf - 6-slide presentation (problem, data, model, accuracy, live demo, impact)
2. thermotrace_overview.mp4 - 2-minute product tour
3. sample_incident_report.pdf - PDF incident report generated from the live system
4. SUMMARY.pdf / SUMMARY.md - one-page mapping to the problem statement
5. screenshots (national view, alert inbox, dashboard, mobile)

The longer replay video (4 min, five scenarios) and all documents are in the repository (docs/demo, docs/EVALUATION.md, docs/REPLAY.md, docs/USER_GUIDE.md, docs/SECURITY.md, docs/OFFLINE.md).

**Status, honestly:** the system runs end-to-end on our stack with real FIRMS data and passes its automated tests (350+ data/pipeline, 80+ API, 70+ frontend). A public hosted URL is not yet live; deployment scripts (HTTPS, read-only demo mode, backups) are ready and were checked on a local production-style stack. Telegram/email delivery was tested against local fakes, not a real bot.

Contact: <your name>, <your phone>, <your email>

Thanking you,
<your name / team>
