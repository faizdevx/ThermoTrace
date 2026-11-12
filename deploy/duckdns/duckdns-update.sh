#!/usr/bin/env bash
# Point <DUCKDNS_SUBDOMAIN>.duckdns.org at this machine's current public IP.
# Reads DUCKDNS_SUBDOMAIN and DUCKDNS_TOKEN from /etc/thermotrace/duckdns.env (mode 600, never committed).
set -euo pipefail
ENV_FILE=${DUCKDNS_ENV_FILE:-/etc/thermotrace/duckdns.env}
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${DUCKDNS_SUBDOMAIN:?set DUCKDNS_SUBDOMAIN in $ENV_FILE}"
: "${DUCKDNS_TOKEN:?set DUCKDNS_TOKEN in $ENV_FILE}"
# empty ip= lets DuckDNS use the address the request comes from
answer=$(curl -fsS --max-time 20 "https://www.duckdns.org/update?domains=${DUCKDNS_SUBDOMAIN}&token=${DUCKDNS_TOKEN}&ip=")
if [ "$answer" != "OK" ]; then
  echo "duckdns update failed: $answer" >&2
  exit 1
fi
echo "duckdns: ${DUCKDNS_SUBDOMAIN}.duckdns.org updated"
