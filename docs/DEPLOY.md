# Hosted demo on one VM

**Status: not deployed.** Deploying needs a VM, a domain and credentials that only you have, so there is no public URL yet.
Everything below was exercised on this machine as a second, isolated stack (`docker compose -p ttprod ...`, Caddy with its internal
CA on `https://localhost:8443`): HTTPS, read-only refusal, rate limits, the data restore and a phone-width screenshot. What a real
VM adds is DNS and a public Let's Encrypt certificate, which Caddy requests by itself once the name resolves.

## What the demo is

| Piece | How |
|---|---|
| One VM, docker compose | `docker-compose.yml` + `docker-compose.prod.yml` (db, migrate, api, thermal worker, frontend, caddy) |
| HTTPS | Caddy (`deploy/Caddyfile`): automatic Let's Encrypt for `DOMAIN`, HSTS, `nosniff`, `X-Frame-Options`; only ports 80/443 are published, the database, API and worker are internal |
| Read-only demo user | `DEMO_READONLY=true` on the API: every `POST/PUT/DELETE` answers **403** whatever tokens are sent; `ANALYST_TOKEN` and `ADMIN_TOKEN` are empty; the inbox shows "public read-only demo" instead of the decision form |
| Abuse limits | nginx in the frontend container: 20 requests/s per client for the API, **6 per minute** for the endpoints that do 20-30 s of work (Sentinel-2 / Landsat chips, PDF reports); excess gets 429 |
| Live data | the thermal worker keeps polling FIRMS, so the demo is live, not a recording |
| Phone | responsive layout; checked at 390 x 844 with no sideways scroll (`industrial-data/scripts/capture_mobile.py`) |

## Steps

Needs a VM with Docker (2 vCPU, 4 GB RAM, 20 GB disk is enough: the lean bundle is about 50 MB; the full database with 1.9 M archived
detections is not needed for the demo) and a DNS record for the domain pointing at it.

```bash
git clone https://github.com/faizdevx/ThermoTrace && cd ThermoTrace
cp .env.example .env        # set POSTGRES_PASSWORD (URL-safe) and optionally FIRMS_MAP_KEY; leave ANALYST_TOKEN / ADMIN_TOKEN empty
export DOMAIN=demo.example.org
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE up -d db migrate                                   # empty schema (migrations 001-010)
# on the machine that has the data:  python deploy/demo_bundle.py make --out demo-bundle ; scp -r demo-bundle vm:ThermoTrace/
DEMO_COMPOSE_ARGS="-f docker-compose.yml -f docker-compose.prod.yml" python deploy/demo_bundle.py restore --bundle demo-bundle --db thermotrace
$COMPOSE up -d --build                                      # api, worker, frontend, caddy
```

`demo_bundle.py make` writes a gzip CSV per table (boundaries, the merged facility database, current thermal sources with their last
21 days of detections, alerts and decisions, labels, the model record) plus the baseline parquet; `restore` refuses to run without an
explicit `--db`, because it empties those tables.

Check from outside: `curl -I https://$DOMAIN/`, `curl https://$DOMAIN/api/dashboard`, and
`curl -X POST https://$DOMAIN/api/alerts/1/decision -d '{}'` must answer 403.

## Measured locally (stack `ttprod`, 2026-10-16)

* `GET /api/health` 200 in 0.2 s, `/api/dashboard` 200 in 0.07 s through Caddy + nginx; response headers `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`, no `Server`.
* `POST /api/alerts/1/decision` -> 403 "read-only public demo".
* 10 quick requests to an imagery URL: five pass, then 429.
* The first boot of an empty PostGIS container made `migrate` fail ("connection refused": the init server listens on the socket only); the database health check now probes TCP.

## Not done / limits

* No VM, no domain, no certificate from a public CA, no uptime or load testing beyond the checks above.
* Map tiles come from OpenStreetMap's volunteer servers (and PDF reports fetch tiles server-side). That is fine for a demo and not for traffic: set `VITE_TILE_URL` (build arg for the frontend) and `REPORT_TILE_URL` to a tile provider you may use.
* The worker needs outbound internet (FIRMS, ESA WorldCover, Sentinel-2 / Landsat catalogues); without it the demo shows its last data and says so.
* The bundle is a snapshot; regenerate it with `make` when you want the demo's history refreshed. Alerts raised on the VM after that are the VM's own.
