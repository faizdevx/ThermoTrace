# User guide: finding fires and their industrial link

ThermoTrace answers three questions about every heat spot a satellite sees, in this order:

1. **Is it a fire or just hot ground?** NASA's VIIRS satellites see heat 375 m wide, three or four times a day.
2. **Is an industrial site involved?** Each spot is matched to 12,762 mapped facilities (power plants, mines, refineries, steel, cement, chemical works, kilns, gas flares).
3. **Is it abnormal for that place?** A coal mine or a flare is hot every night. The app compares each spot with that site's own last 365 days.

Open <http://localhost:8080> (`docker compose up -d`). With sign-in enabled you get a login page first.

## 5-minute walkthrough

**1. National view (Map tab).** Coloured dots are *industrial* sources: red = persistent industrial heat, orange = possible industrial fire / abnormal event, purple = persistent heat with no mapped facility. The faint squares are crop and forest fires, shown as a density grid so they do not bury the industrial ones. Zoom in (zoom 8 and up) and every source becomes a dot. Facilities are off by default; tick *Facilities* under Layers to see where plants are.

**2. Look at the alerts.** The red **Alerts (n open)** button opens the inbox. An alert means one of two things, and says which:
* *P(industrial fire)* is high: short-lived heat at a mapped facility, the pattern of an incident, or
* the site's heat is **3 standard deviations above its own baseline** ("+4.2 sd vs own baseline").

Severity follows power (watch < 40 MW <= high < 100 MW <= critical) and goes up one level at refineries, LNG, chemical and fuel sites. One alert per facility per 6 hours, so you are not spammed.

**3. Open an alert.** You see the site name and kind, peak power, the **reasons in words** ("lies on a mapped steel plant", "only 1 day of detections", "this facility type is usually not thermally active"), and a timeline: first detection, peak, latest, imagery, delivery, decisions.

**4. Check the evidence.** *Show on map + satellite imagery* opens Sentinel-2 chips (true colour and SWIR, where flames glow orange) from before and after, a **dNBR** burn index, SWIR hot-pixel count and a Landsat temperature check. Read it like this:
* high dNBR / burn scar -> vegetation or coal burnt (crop, forest, mine fire);
* hot SWIR pixels, no scar -> something is burning or very hot right now (furnace, flare, slag);
* **low dNBR proves nothing** about a fire inside a building: a plant fire leaves no scar at 10-20 m. No clear scene (cloud, monsoon) is reported as "could not confirm or exclude".

**5. Decide.** Analysts press **Confirm fire / Reject / Unsure** and write a note. The decision is saved with your name in the audit log and as a label for retraining the model. **Download PDF report** makes a one-page-ish report (maps, imagery, reasons, timeline) to hand to a supervisor.

## Reading one source (click any dot)

| Panel | What it tells you |
|---|---|
| Class and confidence | the explanation the system finds most likely, with the competing ones |
| Nearest mapped facility | name, kind, distance, and whether the heat is *inside* its match radius ("at" the facility) |
| Trained model (second opinion) | the machine-learned class with its own reasons; it can disagree with the rules, which is a reason to look closer |
| Compared with its own history | how often this place is hot, typical power, and today's power in standard deviations. **Industrial relation in one line**: a place that is *always* hot is routine; the same place at +4 sd is news |
| Land cover | crop / forest / built-up around the source. Heat in farmland by day is crop burning, even if a landfill or kiln is mapped nearby (such sources are never alerted) |

## The Dashboard tab

Alerts today and open, number of sources and the industrial share, the class mix over time, the top states, and the **model card**: how well the model matches held-out data, with the caveat below.

## How much to trust it

* A detection says *a place was hot*, not *a plant is on fire*. Always check imagery and ground reports.
* Of 34 documented Indian industrial fires, the satellite saw heat for 20; replayed through the live pipeline, 5 raise an alert. Refineries and other always-hot sites hide a fire inside their normal flaring. Small fires fall between satellite passes.
* The headline model score (macro-F1 0.993) is agreement with the labelling rules, not field accuracy ([docs/EVALUATION.md](EVALUATION.md)).
* Data lags the satellite pass by about 3 hours. Replay mode ([docs/REPLAY.md](REPLAY.md)) shows real past events through the same pipeline.

## Typical jobs

* **"Is something burning at industrial site X?"** Search the map to X, click its dot or facility, read *Compared with its own history* and the imagery.
* **"What is new tonight?"** Open the inbox, sort by severity, work from the top.
* **"Is this just crop burning?"** Look at land cover and the night share: day-only heat over fields is agricultural and will not alert.
* **"Brief my team."** Download the PDF report of the alert; use the Dashboard for the overall picture.

Sign-in, roles and the audit log: [SECURITY.md](SECURITY.md). Running without internet: [OFFLINE.md](OFFLINE.md).
