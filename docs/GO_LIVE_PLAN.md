# ThermoTrace - Go-live plan (phases 10-15)

Phases 0-9 of [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) are built and tested locally. This plan is what stands between "works on my machine" and
"runs by itself on the internet, with real users, and we can say how well". Priority tags: **MUST** (do not go live without), **SHOULD** (do
in the first weeks live), **COULD** (only when there is a reason). Effort is working days for one person who knows the code; "you" = needs
an account, a decision or a credential only you have.

**Honest starting point.** Built: pipeline, model, alerts, replay, dashboard, PDF, auth/roles/audit, offline mode, backup scripts. Never done: a
deploy on a real VM, a real Telegram message, a load test, uptime monitoring, a restore on another machine, self-sign-up, anything run for
more than a day unattended. The plan below is mostly about those.

---

## Phase 10 - Go live (MUST, ~3 days + waiting time)

| # | Task | Done when | Who |
|---|---|---|---|
| 10.1 | Pick the host: Oracle Always Free (check the current ARM limit on Oracle's page) with a EUR 6/month Hetzner box as the fallback; 2 vCPU, 4-8 GB, 40 GB disk; firewall: 22 (key only), 80, 443 | VM reachable by SSH with a key, no password login, only 22/80/443 open | you |
| 10.2 | Domain: a free subdomain (DuckDNS) or your own; DNS A record to the VM | `dig` returns the VM address | you |
| 10.3 | Deploy with `docker-compose.prod.yml`, `AUTH_MODE=local`, `DEMO_READONLY=false`, `DOMAIN=...`, strong `POSTGRES_PASSWORD`; create the first admin with the CLI | `https://domain/` shows the sign-in page with a valid public certificate; wrong password gives 401, no session gives 401 | me, with your VM |
| 10.4 | Load the data: `demo_bundle.py restore` (facilities, boundaries, baseline parquet, current sources) **or** build it with a FIRMS key (facilities + 365-day baseline: ~2-3 h) | dashboard shows sources, 12,762 facilities, a baseline z-score on a persistent source | me |
| 10.5 | Real Telegram: create the bot with BotFather, set `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`, run `python -m src.alerts test` | the test alert arrives in the chat within one worker cycle; a failed send is visible on the alert | you + me |
| 10.6 | Worker unattended for 72 h with `restart: unless-stopped`; check no memory growth, no crash loops, no gaps | `docker stats` flat; `thermal_runs` has a row per FIRMS batch for 3 days; no 10-minute-or-longer error streak in the logs | me |
| 10.7 | Backups on: systemd timer from `deploy/`, `BACKUP_PASSPHRASE` stored in two places, **offsite copy** (a second free bucket / your PC with `rsync`), one real **restore drill onto another machine** | `pg_backup.py verify` passes nightly; a restored copy serves the dashboard | me + you |
| 10.8 | Uptime and stall alerts (see 11.1) | you get a message when the site or the data feed stops | me + you |
| 10.9 | Legal page: attributions and licences (FIRMS, OpenStreetMap ODbL, WorldCover CC BY, Copernicus, USGS, Protomaps), "not a confirmed fire" disclaimer, privacy note (what is logged, how long), contact | linked from the footer and the sign-in page | me, you review |

**Gate:** all of 10.x done and a written note of what failed during the 72 hours.

## Phase 11 - Run it unattended (MUST for 11.1-11.4, SHOULD for the rest, ~3 days)

| # | Task | Done when |
|---|---|---|
| 11.1 **MUST** | **Health and freshness monitoring**: an external check (UptimeRobot / Better Stack free tier) on `/api/health` and a new `/api/health/data` that returns 503 when the newest FIRMS batch is older than 6 h or the last worker cycle older than 30 min; the worker also posts to Telegram when it fails 3 cycles in a row | stopping the worker for 40 minutes produces a message |
| 11.2 **MUST** | **Retention**: purge `thermal_detections` older than the baseline needs, `sessions` expired, imagery cache older than 30 days, Docker log rotation (done), a disk-usage alert at 80% | disk use flat over a week; a documented one-line "how big will it get" |
| 11.3 **MUST** | **Baseline refresh** every night (the materialised view / parquet), because "today vs the last 365 days" goes stale | `baseline_window_end` moves daily |
| 11.4 **MUST** | **Gap handling**: on start, if the last cycle is older than the FIRMS file window, say so on the dashboard ("data missing between A and B") and, with a FIRMS key, backfill from the area API | a stopped-and-restarted worker shows the gap or fills it |
| 11.5 SHOULD | **Self-sign-up** (viewer role only): email verification link, per-address and per-email rate limits, honeypot, a switch to turn it off, admin approval option | a new user can register, verify and read; cannot decide or download until promoted |
| 11.6 SHOULD | Password reset by email, session list ("sign out everywhere"), forced password change at first login (the flag exists, the screen does not) | tested by a second person |
| 11.7 SHOULD | Error tracking (Sentry free tier or a log file you actually read) for the API, worker and browser | a deliberately thrown error shows up within a minute |
| 11.8 SHOULD | `docker compose` update procedure: `git pull`, build, migrate, restart with health gating, rollback to the previous image tag | a documented, rehearsed 5-minute upgrade |
| 11.9 COULD | Staging copy of the stack on the same VM (second compose project, other ports) | releases are tried on staging first |

## Phase 12 - Many users (SHOULD, ~3 days)

Today: one API process, in-memory rate limits, PDF and imagery computed inside the request, no load test. Measure first, then fix only what the numbers show.

| # | Task | Done when |
|---|---|---|
| 12.1 | **Load test** (k6 or Locust) against staging: 50 / 200 / 500 concurrent readers on map + dashboard + SSE, plus 5 analysts downloading PDFs | a table: requests/s, p95 latency, CPU/RAM per level, first failure point; written into the README |
| 12.2 | Cache the heavy reads: `GET /thermal/sources` and `/dashboard` per data revision at the edge (nginx `proxy_cache` keyed by revision) and compress | the 200-reader test shows the database near idle |
| 12.3 | **Queue for PDFs and imagery**: request returns "preparing" and the result arrives by SSE; at most N running at once; cached by alert id and revision | 20 simultaneous report clicks do not stall the page loads |
| 12.4 | Run 2-3 API workers (gunicorn/uvicorn workers) and move the rate-limit counters into the database or Redis so limits hold across workers | the same request budget applies whichever worker answers |
| 12.5 | SSE: cap connections per user, heartbeat, reconnect with `Last-Event-ID` tested through Caddy and nginx | 500 open streams stay under the RAM budget |
| 12.6 | Database: indexes checked with `EXPLAIN` on the slowest 5 queries, `thermal_detections` partitioned by month or pruned, `shared_buffers` sized to the VM | slowest query under 200 ms at 10x data |

## Phase 13 - Alert quality and learning (SHOULD, ~4 days, can start after 10)

The replay found: 5 of 22 detected incidents alert; big unmapped sources are missed; one real event re-alerts every 6 h.

| # | Task | Done when |
|---|---|---|
| 13.1 | Rule for **large new sources with no mapped facility** (for example above 30 MW, new in the baseline, not cropland by day), as its own alert type "unknown industrial / unexplained" | the 54 MW Jindal Poly Films replay alerts, and the Punjab replay still raises at most 3 |
| 13.2 | **Same-incident grouping**: one alert thread per facility and event, "still active" updates instead of a new alert every 6 h, closed after 24 h quiet | the Barauni replay shows one incident, not three alerts |
| 13.3 | **Alert quality report** from real decisions: share confirmed / rejected / unsure per rule, per facility kind, per week; shown in the dashboard | a number that says how many alerts analysts agree with |
| 13.4 | **Retrain loop**: nightly job that exports `analyst-v1` labels, retrains, evaluates on the frozen `labels-v1` test set and refuses to promote a model that is worse; model version recorded on each alert | a promoted model only when it beats the current one; rollback by version |
| 13.5 | Per-facility overrides: mute a known flare or kiln for N days, raise the threshold for one site, with an audit entry | an analyst can silence a site without a code change |
| 13.6 | Imagery at alert time for **every** alert (priority queue, retries when cloud clears, "re-check in 5 days") | an alert without a scene gets a scheduled re-check |
| 13.7 | More verified incidents: add 30-50 documented events (the detection rate and the 5/22 number need a bigger sample) | `EVALUATION.md` and `REPLAY.md` recomputed with the larger set |
| 13.8 | Global Energy Monitor trackers (needs a person for the download form), MODIS burned-area confirmation if obtainable | facility coverage and vegetation labels improved, or documented as unobtainable |

## Phase 14 - Finalise (MUST, ~2 days)

| # | Task | Done when |
|---|---|---|
| 14.1 | **Acceptance run**: a checklist executed on the live URL by someone who did not build it - sign up, sign in, read an alert, confirm it, download the PDF, see the Telegram message, sign out, use it on a phone | every line ticked, defects fixed or listed |
| 14.2 | Dependency and security pass: `npm audit`, `pip-audit`, container image scan (Trivy), headers (securityheaders.com), TLS grade (SSL Labs), OWASP ZAP baseline scan against staging | no high findings open, the rest written down |
| 14.3 | Runbook: "site down", "no new data", "disk full", "restore from backup", "forgot admin password", "rotate a leaked key" - each as numbered steps | someone else follows each once |
| 14.4 | SLO written down and measured for two weeks: availability (e.g. 99%), data freshness (newest detection under 6 h old, 95% of the time), alert delay (under 15 min after the FIRMS file) | a measured table, not a promise |
| 14.5 | Release `v1.0`: tag, changelog, README status table rewritten as "what works, what is limited", the demo video re-recorded on the live site | the repository tells the same story as the running system |
| 14.6 | Freeze: no feature work for 3 days while only defects are fixed | |

---

## Phase 15 - MLOps (SHOULD, ~5 days; 15.1-15.4 before anyone relies on the model, the rest after weeks of live data)

**What exists.** A reproducible training and evaluation script (`scripts/evaluate.py`), one feature definition shared by training and the live pipeline
(`src/ml/features.py`), a versioned and checksummed label set (`labels-v1`, manifest with sha256, region + time split, leakage policy), a model
version on every source and alert, `models/metrics.json` registered in `thermal_model`, bootstrap confidence intervals, a blind-model ablation, calibration,
and the analyst-decision loop writing `analyst-v1` labels. A sklearn version pin and a version warning exist.

**What does not exist.** Nothing watches the model once it is live; nothing retrains it; the model is a file committed to git, not a registry entry; the
training run is not tied to the exact data and code that produced it; there is no gate that stops a worse model from shipping.

| # | Task | Done when |
|---|---|---|
| 15.1 **MUST** | **Model registry in PostGIS** (not a new service): table `model_versions` with artifact path + sha256, training data manifest hash (`labels-v1`, analyst labels cut-off), git commit, feature list hash, metrics, status `candidate / champion / retired`, who promoted it and when; the worker loads the `champion` by hash and refuses a file whose hash differs | a source's `model_version` resolves to one registry row; swapping the file by hand makes the worker fall back to the rules and log why |
| 15.2 **MUST** | **Promotion gate**: `python -m src.ml.promote CANDIDATE` runs the frozen `labels-v1` test split and the incident check, and promotes only if macro-F1 is not worse than the champion beyond its confidence interval **and** incident recall at the fixed false-alarm rate does not drop; the decision and numbers are written to the registry and the audit log; one-command **rollback** to the previous champion | a deliberately degraded model is refused; rollback restores the old version in under a minute |
| 15.3 **MUST** | **Feature and data validation** at serving time: schema, ranges and missing-share per feature checked on every cycle (for example land cover unknown for 17% of sources offline); a cycle with a broken feed (empty FIRMS file, all FRP zero, a feature missing) is flagged and the model output is marked "degraded" instead of used silently | tests with injected bad frames; the dashboard shows "model degraded: reason" |
| 15.4 **MUST** | **Training/serving skew check**: the same sampled sources scored by the training code path and by the live path must agree (tolerance), run in CI and nightly | a changed feature in one path fails the check |
| 15.5 SHOULD | **Drift monitoring**: per-feature population stability index and class mix of live predictions vs the training window, weekly; alert when the share of `industrial_*` predictions or `frp_z` distribution moves beyond a bound (monsoon, a new FIRMS product, a satellite change such as a new NOAA instrument) | a weekly drift table on the dashboard; a Telegram message on a breach |
| 15.6 SHOULD | **Live performance from analyst decisions**: per model version and per rule, confirmed / rejected / unsure counts, precision of alerts, time to decision; a sliding 30-day window with confidence intervals; sample-size warnings (a few decisions prove nothing) | the model card shows live precision next to the held-out score |
| 15.7 SHOULD | **Automated retraining pipeline**: a scheduled job (nightly or weekly) that snapshots analyst labels, retrains, evaluates, registers a *candidate* and runs the gate; a human promotes (no auto-promote at first) | a candidate appears with a report; promotion takes one command and one audit row |
| 15.8 SHOULD | **Experiment tracking**: every training/evaluation run writes params, data hashes, metrics and plots to a run folder and a `model_runs` table (no MLflow server needed on a free VM; MLflow only if a team needs a UI) | any past number in `EVALUATION.md` can be reproduced from its run id |
| 15.9 SHOULD | **Label hygiene**: analyst labels are reviewed (two analysts or a second look on disagreements), conflicting labels for one source flagged, `unsure` excluded from training, a cap per facility so one noisy site does not dominate; label noise estimate reported | a label quality table next to the data card |
| 15.10 COULD | **Shadow mode** for a candidate: it scores live sources next to the champion for a week without affecting alerts; disagreement cases listed for review | a shadow report before promotion |
| 15.11 COULD | **Model card and data card** generated from the registry (intended use, training data, metrics with CIs, known failure cases from `REPLAY.md`, ethical limits) | one page per model version, linked from the dashboard |
| 15.12 COULD | **Better model, honestly measured**: add imagery and time-series features only if they beat the champion on the independent incident set (the 180-unit test said imagery did not help), try gradient boosting with monotone constraints, per-region calibration | changes are kept only when the promotion gate says so |

**Two traps to avoid.** (1) The held-out score is agreement with rule-derived labels (macro-F1 0.993): do not use it alone to promote a model; the incident check and
live analyst precision are the independent evidence. (2) Retraining on analyst labels creates a feedback loop (the system only shows analysts what it already
flagged): keep a random sample of *unflagged* sources for review, otherwise recall can never be measured.

**Free-tier footprint.** Everything above is Python scripts, PostGIS tables and the existing worker, so it fits the same free VM; no extra servers. Calendar: 15.1-15.4 in
week 2-3 of the go-live calendar, 15.5-15.9 once two or three weeks of live data and decisions exist, 15.10-15.12 only when there is a reason.

---

## Order and calendar (one person, realistic)

| Week | Work |
|---|---|
| 1 | 10.1-10.9, start the 72-hour run; 11.1-11.4 while it runs |
| 2 | 11.5-11.8, 12.1-12.3; alert quality 13.1-13.2 in parallel if a second person exists |
| 3 | 12.4-12.6, 13.3-13.6, 15.1-15.4 (registry, gate, validation, skew), staging; two weeks of SLO measurement start |
| 4 | 14.1-14.6, more incidents 13.7, release; then 15.5-15.9 as live data accumulates |

## What "real-time" will and will not mean on the live site (say this on the About page)

* Heat is seen **about 3 hours after the satellite pass**, a few times a day: an alert is **1-3 minutes after FIRMS publishes**, not after the fire starts.
* Imagery depends on cloud and a 5-day revisit; PDFs are made when you ask (a few seconds, up to 30 s the first time).
* An alert is "abnormal heat at an industrial site", not a confirmed fire.

## Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Free host changes or reclaims the VM (Oracle limits changed in 2026) | outage | EUR 6 fallback, backups offsite, deploy script rehearsed |
| FIRMS outage or key limits | no new data | gap banner (11.4), freshness alert (11.1), MAP_KEY backfill |
| Alert fatigue | analysts stop looking | 13.2 grouping, 13.3 quality numbers, 13.5 mutes |
| Open sign-up abused | spam accounts, load | viewer-only, email verification, rate limits, kill switch (11.5) |
| Heavy endpoints under crowd | slow site | queue (12.3), edge cache (12.2), load numbers (12.1) |
| Public OSM tile servers | blocked for heavy use | PMTiles basemap on the VM (already built) |
| Credentials leaked | account or bot takeover | `.env` outside git, rotation runbook (14.3), audit log |

## Definition of done for "live"

1. A public HTTPS address that stayed up for two weeks with measured availability and freshness.
2. A real Telegram alert received from real FIRMS data, and one analyst decision recorded in `labels`.
3. A restore of last night's backup onto a second machine.
4. A load-test table in the README with the first failure point.
5. The README, `EVALUATION.md` and `REPLAY.md` still match what the live system does.
