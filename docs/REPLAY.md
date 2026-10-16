# Replay: archived events through the live pipeline

`python -m src.thermal.cli replay` feeds archived NASA FIRMS detections through the **same code path as the live worker**
on a virtual clock, so the map, the alerts and the server-sent events behave as if the event were happening now.
Demo video: [`docs/demo/thermotrace_replay_demo.mp4`](demo/thermotrace_replay_demo.mp4) (about 4 minutes, five scenarios,
recorded from the real dashboard by `industrial-data/scripts/record_replay_demo.py`).

## Run it

```bash
# once: create + migrate the replay database (<db>_replay, next to the live one; the live data is never touched)
cd industrial-data && python -m src.thermal.cli replay --init-db
# a curated scenario, 60 virtual seconds per real second
python -m src.thermal.cli replay --scenario vatva-gidc --speed 60x
# any verified incident (event date -2 d .. +2 d at its location), or any archive range / area
python -m src.thermal.cli replay --incident IN-034 --speed 600x
python -m src.thermal.cli replay --from 2026-05-08 --to 2026-05-10 --bbox 73.8,29.5,77.0,32.6 --speed 60x
python -m src.thermal.cli replay --list
```

To watch it: `docker compose --profile replay up -d api-replay` (the API on the replay database, port 8001) and the dashboard on
it, `VITE_PROXY_TARGET=http://127.0.0.1:8001 npm run dev`. The dashboard shows a **REPLAY** banner with the virtual clock;
alerts appear in the inbox as the clock reaches them. `--speed max` runs without waiting, `--no-db` classifies and evaluates
without a database, `--report file.json` writes the full result, `--imagery` attaches Sentinel-2 evidence to alert sources,
`--deliver` sends alerts to the configured Telegram / webhook / email channels (off by default: replaying 2020 must not
message anyone).

## What is real and what is simulated

| | |
|---|---|
| Detections | real archived VIIRS (S-NPP + NOAA-20, standard-processing archive), unaltered |
| Availability | each detection becomes visible `--latency-hours` (default 3, the FIRMS lag) after its acquisition time |
| One cycle | one tick of `--step-minutes` of virtual time: cluster, facility match, land cover, rule + model classification, baseline z-score, PostGIS commit with the NOTIFY the API turns into an SSE event, alert rules with 6 h deduplication |
| Baseline | built **only from detections before the replay starts** (365-day window ending 14 days before the start), so nothing the replay shows was known in advance |
| Facilities, land cover, model | *today's* OSM / WRI / flare-site facility map, ESA WorldCover 2021, and the current model. A 2020 event is therefore judged with a 2026 facility map: a plant that did not exist then may be matched, one that was unmapped then may be missing |
| Imagery | real Sentinel-2 / Landsat scenes of the event dates (`--imagery`) |

## Scenarios

Selection: the three verified incidents below are the ones with the strongest FIRMS signal that the pipeline **also** gets
right (a class of "possible industrial fire" and an alert) - curated, as asked, so the next section shows the whole population
they were picked from. The flare and the Punjab day are *negative* cases: the right answer is a class and silence.

| id | what | result |
|---|---|---|
| `vatva-gidc` | Vatva GIDC chemical-estate fire, Gujarat, 2020-12-09 (IN-024). 62 MW, the strongest detection among the verified incidents | `industrial_fire_candidate`; 1 alert, **high**, at the first visible detection (2020-12-09 00:00 UTC), trigger P(fire) 95 % and +82.7 sd over the facility baseline |
| `barauni-refinery` | Barauni refinery effluent-treatment fire, Bihar, 2024-02-10 (IN-034). 6.5 MW, refinery (severity raised one level) | `industrial_fire_candidate`; 3 **high** alerts: 2024-02-10 11:00, 2024-02-10 23:00 and 2024-02-11 11:00 UTC (the same facility re-alerts each 6 h window while it keeps showing heat) |
| `amudan-chemicals` | Amudan Chemicals fire, Maharashtra, 2024-05-23 (IN-036). 8.6 MW, a single detection | `industrial_fire_candidate`; 1 alert, **watch** |
| `nahorkatiya-flare` | Oil India's Nahorkatiya gas flare, Assam, 2026-09-01..07: routine, always hot | `industrial_persistent` (one cycle on 09-07 showed `industrial_fire_candidate` for a few detections, then settled back); **0 alerts** |
| `punjab-burning-day` | Punjab crop-residue burning peak, 2026-05-08..10 (2,426 detections on the peak day, 6,746 sources) | 95 % `agricultural_burning`; **1 alert** (watch: an 8-day, 25 MW persistent source at a mapped quarry inside farmland, +5.5 sd) - see below |

"Correct class" for an incident means the class the analyst *saw when the alert fired*, not the best class at any moment of the
replay. The model's second opinion is shown next to the rule class (`model_class`); for these three incidents it says
`industrial_persistent`, because the trained model has no fire class (see `docs/EVALUATION.md`).

## The honest hit rate behind the curated incidents

`industrial-data/scripts/replay_batch.py` replays **every** verified incident (34) the same way
([`industrial-data/replay/incident_replays.json`](../industrial-data/replay/incident_replays.json)):

* 12 have no detection at all within the replay window (nothing to replay: a 375 m satellite did not see them);
* 22 have at least one detection; 16 of those are classed industrial at some point of the replay, **5 raise an alert** (IN-005 Panipat naphtha cracker,
  IN-006 Novaphene, IN-024 Vatva, IN-034 Barauni, IN-036 Amudan), and all 5 are industrial classes;
* the 17 without an alert: **7 are routinely hot refineries and gas plants** (Paradip, Barauni 2018, Panipat 2019, Haldia 2019
  and 2021, Hazira, Chennai: class `industrial_persistent`, power never abnormal against their own baseline - at 375 m a fire
  inside a refinery looks like its normal flaring); **4 are fire-candidates too weak to alert** (3.3, 2.8, 0.6 and 0.5 MW);
  **4 are not at a mapped facility** (3 classed `other`, 1 `agricultural_burning`, including the 54 MW miss below); **2 have no
  source near the located point** at all.

So the curated demo shows what the system can do when the signal is there, not that it catches industrial fires in general:
**5 of 22 detected incidents alerted (23 %), 5 of 34 verified incidents overall (15 %)**.

### A miss worth looking at: Jindal Poly Films (IN-029, 2023-01-01, 54 MW)

The second-strongest detection of all. The replay classes it `other` and raises no alert: the heat is 1 km north of the located
point, the nearest mapped facility is a quarry that does not contain it, the land cover is not crop or forest, and the source is
new, so there is no baseline. Neither alert rule can fire without a matched facility or a high fire-candidate score. A rule
"large new source of unknown cause" would catch it, but that rule was not added after seeing this event: it would be fitted to
the one case, and it belongs with the next round of labelled data.

## What the replays changed in the alert rules

The first replay of the Punjab peak raised **58 alerts**: brick kilns, landfills and quarries that sit inside farmland, seen
only by day, one day long, at 5-30 MW over fields that were being burned that week. Those are crop fires at a point that
happens to carry an OSM facility tag. The alert rule now drops sources whose surroundings are at least 60 % cropland **and**
that were seen almost only by day (night share below 20 %), `ALERT_VETO_CROP_FRACTION` / `ALERT_VETO_NIGHT_FRACTION`
(`industrial-data/src/alerts/rules.py`). With the veto the same replay raises 1 alert, and the three incident alerts, the
incident batch (5 alerts) and the flare (0) are unchanged. This is a fix made **after** seeing the failure on that scenario,
so Punjab is no longer an independent test of the alert rule; it is a regression test of it. (The Punjab region is held out
from the trained model; the flare's region, Assam, is in its training regions, with later dates.)

## Other things the replays showed

* A **persistent 25 MW source at a mapped quarry in the middle of the Punjab peak** still alerts: eight days of detections,
  28 % at night, +5.5 sd over that cell's own history. It may well be real industrial heat or a burning dump; the alert is
  doing its job and the analyst decides.
* **Re-alerting**: one real event keeps its facility "hot" for days, so each 6 h window produces a new alert (Barauni: 3 in two
  days). That is the specified deduplication, not a bug, but an analyst will want "same incident" grouping; not built.
* **z-scores explode on quiet places** (+82.7 sd for Vatva): the baseline standard deviation is floored at 1 MW and the estate
  was silent. The size of the number means "never before", not "82 times worse".
* **Imagery of the events**: Sentinel-2 chips exist for Vatva (scene 2020-12-13, dNBR p95 +0.02: a building fire leaves no
  burn scar, as in the README findings) and Barauni (dNBR p95 +0.42, a vegetation change around the refinery that cannot be attributed to the fire); Amudan had no scene under
  30 % cloud within 5 days.

## Limits

Five scenarios and 22 incident replays are an illustration, not a benchmark; the facility map is 2026's; the model and the
land-cover layer are fixed; incident locations are partly coarse (see `docs/LABELS.md`); the "expected" results were written
down before each run but the incident selection and the Punjab veto were made after looking at results, as said above.
