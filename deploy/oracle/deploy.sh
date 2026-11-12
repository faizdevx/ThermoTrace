#!/usr/bin/env bash
# Build and start (or update) the public ThermoTrace demo on the VM, then check it from the outside.
#   bash deploy/oracle/deploy.sh             # first deploy or update
#   bash deploy/oracle/deploy.sh --restore   # also load ./demo-bundle into the database (empties those tables)
set -euo pipefail
cd "$(dirname "$0")/../.."

[ -f .env ] || { echo "missing .env: cp .env.example .env and fill it in" >&2; exit 1; }
set -a; source .env; set +a
: "${DOMAIN:?set DOMAIN in .env, for example thermotrace.duckdns.org}"
if [ "${POSTGRES_PASSWORD:-}" = "change-me-please" ] || [ -z "${POSTGRES_PASSWORD:-}" ]; then
  echo "set a real POSTGRES_PASSWORD in .env" >&2; exit 1
fi
if [ "$(uname -m)" = "aarch64" ] && [ -z "${POSTGIS_IMAGE:-}" ]; then
  echo "ARM machine: set POSTGIS_IMAGE=imresamu/postgis:16-3.4 in .env (postgis/postgis has no arm64 image)" >&2; exit 1
fi

export DOMAIN
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

echo "== database and migrations"
$COMPOSE up -d db
$COMPOSE run --rm migrate

if [ "${1:-}" = "--restore" ]; then
  [ -d demo-bundle ] || { echo "no ./demo-bundle: copy it from the machine that has the data (scp -r demo-bundle ...)" >&2; exit 1; }
  echo "== loading demo-bundle"
  DEMO_COMPOSE_ARGS="-f docker-compose.yml -f docker-compose.prod.yml" \
    python3 deploy/demo_bundle.py restore --bundle demo-bundle --db "${POSTGRES_DB:-thermotrace}"
fi

echo "== build and start everything"
$COMPOSE up -d --build

echo "== waiting for https://$DOMAIN (certificate can take a minute on first start)"
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "https://$DOMAIN/api/health"; then break; fi
  sleep 5
done

fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1 ($3)"; else echo "FAIL  $1: expected $2, got $3"; fail=1; fi; }
check "home page"              200 "$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/")"
check "api health"             200 "$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/api/health")"
check "dashboard"              200 "$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/api/dashboard")"
check "writes refused (demo)"  403 "$(curl -s -o /dev/null -w '%{http_code}' -X POST "https://$DOMAIN/api/alerts/1/decision" -H 'Content-Type: application/json' -d '{}')"
$COMPOSE ps
exit $fail
