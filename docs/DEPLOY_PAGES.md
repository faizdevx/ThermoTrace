# Free live site on GitHub Pages (no server, no card)

`https://faizdevx.github.io/ThermoTrace/` is the public, read-only ThermoTrace. Nothing runs between updates: the workflow
`.github/workflows/pages.yml` does the work on a GitHub Actions runner every 20 minutes.

```
pages-state branch ──load──> PostGIS (service container) ──FIRMS cycle──> classify, alerts ──save──> pages-state branch
                                       │
                                       └── real API ──crawl──> JSON files + static build of the app ──> GitHub Pages
```

* **State.** `pages-state` holds the demo bundle tables, the 365-day baseline and the worker's caches. Each run loads it, runs one
  `python -m src.thermal.cli refresh` and force-pushes the changed tables back as a single commit (`deploy/pages/state.py`).
* **Answers.** `deploy/pages/crawl.py` saves what the real API returns for every view the app needs; the app built with
  `VITE_STATIC_DATA=1` reads those files (`industrial-gis/frontend/src/services/staticApi.ts`) and applies the thermal filters itself.
* **If FIRMS is down** the run still publishes the last state; the status badge shows its age.

## Limits

* Read-only: no Confirm / Reject, no sign-in, no exports, no on-demand Sentinel-2 / Landsat imagery, no PDF reports, no district
  outlines, no OSM pilot layer.
* Freshness: every 20 minutes when GitHub runs the schedule on time (it can be late under load); FIRMS itself is ~3 h behind the pass.
* GitHub pauses scheduled workflows in a public repository after 60 days without repository activity; re-enable it in the Actions tab.

## Setup (once)

1. Settings > Pages > Source: **GitHub Actions**.
2. Optional: Settings > Secrets and variables > Actions > `FIRMS_MAP_KEY`.
3. Actions > *Live site (GitHub Pages)* > Run workflow.

To rebuild `pages-state` from scratch: `python deploy/demo_bundle.py make` on a machine with the full database, then commit the bundle
(plus `live/*.gz` caches, optional) as the only commit of an orphan `pages-state` branch.
