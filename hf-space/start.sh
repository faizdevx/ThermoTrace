#!/usr/bin/env bash
# Entry point of the Hugging Face Space: database, demo data, API, FIRMS worker, nginx on :7860. Any process exiting stops the
# container, and the Space restarts it.
set -euo pipefail

export PGDATA=/tmp/pgdata PGHOST=127.0.0.1 PGUSER=thermotrace PGDATABASE=thermotrace
export DATABASE_URL=postgresql+psycopg2://thermotrace@127.0.0.1:5432/thermotrace
export DATA_MODE=postgis DEMO_READONLY=true AUTH_MODE=off TRUSTED_PROXY_HOPS=${TRUSTED_PROXY_HOPS:-1}
export THERMAL_DATA_DIR=/data/thermal/live THERMAL_IMAGERY_DIR=/data/thermal/imagery FACILITIES_DIR=/data/thermal/facilities
export THERMAL_BASELINE_DIR=/data/baseline THERMAL_SITES_FROM_DB=1
export THERMAL_FACILITIES_GEOJSON=/data/thermal/facilities/facilities.geojson THERMAL_SITES_GEOJSON=/data/thermal/sites_india.geojson
export THERMAL_INTERVAL_SECONDS=${THERMAL_INTERVAL_SECONDS:-600} THERMAL_IMAGERY_PER_CYCLE=${THERMAL_IMAGERY_PER_CYCLE:-2}

echo "== postgres"
# trust auth is safe here: the server listens on 127.0.0.1 inside the container only
initdb -D "$PGDATA" -U thermotrace --auth=trust >/dev/null
pg_ctl -D "$PGDATA" -l /tmp/pg.log -w start \
  -o "-c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp -c shared_buffers=1GB -c max_wal_size=2GB"
createdb thermotrace

echo "== schema and demo data"
cd /src/industrial-data
python -m src.sync.cli migrate
python /src/hf-space/restore.py /bundle
cp /bundle/baseline/thermal_baseline.parquet /data/baseline/ 2>/dev/null || echo "no baseline parquet in the bundle"

echo "== services"
(cd /src/industrial-gis && exec uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --proxy-headers) &
(cd /src/industrial-data && exec python -m src.thermal.cli run) &
nginx -c /src/hf-space/nginx.conf -g 'daemon off;' &

wait -n
echo "a service exited; stopping so the Space restarts" >&2
exit 1
