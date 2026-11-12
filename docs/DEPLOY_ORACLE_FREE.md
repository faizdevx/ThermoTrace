# Free public deployment: Oracle Cloud Always Free + DuckDNS

Goal: ThermoTrace live at `https://<name>.duckdns.org`, updating itself from NASA FIRMS every 10 minutes, for zero cost.

**Status: plan and scripts ready, not yet deployed.** It needs an Oracle account and a DuckDNS token that only the owner can create.

## What runs where

```
Browser ──HTTPS──> Caddy (Let's Encrypt) ──> nginx (app + rate limits) ──> API ──> PostGIS
                                                    worker: FIRMS every 10 min -> classify -> alerts
DuckDNS  <── this VM's public IP, refreshed every 5 min
```

One Oracle VM runs the existing Docker stack (`docker-compose.yml` + `docker-compose.prod.yml`). Only ports 22, 80 and 443 are open.
The public site is read-only (`DEMO_READONLY=true`): anyone can look, nobody can change data.

| Piece | Free offer used | Notes |
|---|---|---|
| Server | Oracle Always Free, Ampere A1 (ARM64), Ubuntu 24.04 | Use the largest shape the console marks *Always Free eligible*; 2 OCPU / 12 GB is plenty, 4 OCPU / 24 GB if offered |
| Disk | Always Free block volume (up to 200 GB total) | 50-100 GB boot volume |
| Public IP | Reserved public IP (free while attached) | Keeps the address across restarts |
| Name | DuckDNS subdomain | Free, needs a GitHub / Google sign-in |
| HTTPS | Let's Encrypt via Caddy | Automatic, renews itself |
| Fire data | NASA FIRMS public CSV or a free MAP_KEY | Already used by the worker |

## Phase 0 - accounts (owner, about 30 min)

1. **Oracle Cloud**: sign up at https://www.oracle.com/cloud/free/. A card is needed for identity checks only; Always Free resources are not charged.
   Pick the **home region** carefully, it cannot be changed: one close to India with A1 capacity (Mumbai or Hyderabad; if A1 is always
   "out of capacity" there, a less busy region such as Singapore works too).
2. **DuckDNS**: sign in at https://www.duckdns.org, create a subdomain (for example `thermotrace`), copy the **token**. Keep the token secret.
3. **NASA FIRMS key** (optional): https://firms.modaps.eosdis.nasa.gov/api/map_key/.

Done when: you can log in to both consoles and have the subdomain and token.

## Phase 1 - create the VM (owner, about 20 min)

In the Oracle console: *Compute > Instances > Create instance*.

1. Image: **Canonical Ubuntu 24.04** (aarch64).
2. Shape: *Change shape > Ampere > VM.Standard.A1.Flex*, choose OCPUs and memory within the Always Free limit shown.
3. Networking: create a new VCN with a public subnet; **assign a public IPv4 address**.
4. SSH keys: upload your public key (or download the generated pair and keep it safe).
5. Boot volume: 50-100 GB.
6. Create. If it fails with *Out of capacity*, try another availability domain, fewer OCPUs, or retry later (capacity frees up during the day).
7. Make the IP permanent: *Instance > Attached VNICs > IPv4 addresses > Edit > Reserved public IP*.

Open the ports in Oracle's network firewall: *Networking > Virtual cloud networks > your VCN > Security Lists > Default* >
*Add Ingress Rules*: source `0.0.0.0/0`, TCP, destination port `80`; again for `443`.

Done when: `ssh ubuntu@<IP>` works.

## Phase 2 - prepare the VM (about 10 min)

```bash
ssh ubuntu@<IP>
git clone -b deployment https://github.com/faizdevx/ThermoTrace && cd ThermoTrace
bash deploy/oracle/setup_vm.sh       # updates, Docker, opens 80/443 in iptables, 2 GB swap, auto security updates
exit                                  # log in again so the docker group applies
```

Oracle's Ubuntu image blocks everything but SSH in `iptables`; the script opens 80 and 443 and saves the rule. Both firewalls (the
Security List in Phase 1 and `iptables` here) must allow the ports, otherwise the certificate request times out.

Done when: `docker run --rm hello-world` works without sudo.

## Phase 3 - DuckDNS name (about 5 min)

```bash
sudo mkdir -p /etc/thermotrace /opt/thermotrace
printf 'DUCKDNS_SUBDOMAIN=thermotrace\nDUCKDNS_TOKEN=<your token>\n' | sudo tee /etc/thermotrace/duckdns.env >/dev/null
sudo chmod 600 /etc/thermotrace/duckdns.env
sudo cp deploy/duckdns/duckdns-update.sh /opt/thermotrace/ && sudo chmod 755 /opt/thermotrace/duckdns-update.sh
sudo cp deploy/duckdns/duckdns-update.service deploy/duckdns/duckdns-update.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now duckdns-update.timer
sudo systemctl start duckdns-update.service && journalctl -u duckdns-update -n 3
```

Done when: `getent hosts thermotrace.duckdns.org` prints the VM's IP.

## Phase 4 - configuration (about 5 min)

```bash
cp .env.example .env
nano .env
```

Set at least:

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD` | long random, letters and digits only (`openssl rand -hex 24`) |
| `DOMAIN` | `thermotrace.duckdns.org` |
| `POSTGIS_IMAGE` | `imresamu/postgis:16-3.4` (ARM build; the default image is amd64 only) |
| `PG_SHARED_BUFFERS` | `2GB` |
| `FIRMS_MAP_KEY` | your key, or leave empty for the public CSV |
| `ALERT_BASE_URL` | `https://thermotrace.duckdns.org` |

Leave `ANALYST_TOKEN`, `ADMIN_TOKEN` empty and `AUTH_MODE=off` for the public read-only demo.

## Phase 5 - data and first start (about 20-30 min, mostly the ARM image build)

The database content (facilities, boundaries, 365-day baseline, recent sources, model record) is not in git. Copy the demo bundle from
the PC that has it:

```bash
# on the PC
scp -r D:/ThermoTrace/demo-bundle ubuntu@<IP>:ThermoTrace/
```

Then on the VM:

```bash
bash deploy/oracle/deploy.sh --restore
```

The script checks `.env`, starts PostGIS, runs migrations, loads the bundle, builds and starts every service, waits for HTTPS and
checks four things from outside: home page 200, `/api/health` 200, `/api/dashboard` 200, and a write request refused with 403.

No bundle at hand? `python deploy/demo_bundle.py make --out demo-bundle` rebuilds it on any machine with the local database; without
one the site still starts and fills itself from FIRMS within a cycle, but the facility layer and the baseline are empty until the
facility import has run.

Done when: the script ends with four `ok` lines and the site opens in a browser with a padlock.

## Phase 6 - keep it running (about 15 min, once)

1. **Survive reboots**: every service has `restart: unless-stopped` and Docker starts at boot. Test once with `sudo reboot`.
2. **Uptime alerts**: free UptimeRobot monitor on `https://thermotrace.duckdns.org/api/health`, 5-minute interval, email on failure.
3. **Backups**: set `BACKUP_PASSPHRASE` in `.env`, install `deploy/thermotrace-backup.service` and `.timer` (nightly
   `pg_dump`, encrypted). Copy backups off the VM now and then (Oracle Object Storage has a free 20 GB tier, or `scp` to your PC).
4. **Updates**: `git pull && bash deploy/oracle/deploy.sh`.
5. **Idle reclaim**: Oracle may reclaim an Always Free instance that stays almost idle for 7 days (CPU, network and memory all
   below about 20 %). The 10-minute FIRMS worker keeps some load but may not be enough. Check the console's notices; upgrading the
   account to *Pay As You Go* removes idle reclaim and keeps Always Free resources free (set a budget alert of 1 rupee to be safe).

## Phase 7 - later, when real users come

| Need | Change |
|---|---|
| Named users, analyst decisions | `DEMO_READONLY=false`, `AUTH_MODE=local`, create the admin: `docker compose exec api python -m backend.app.users create --username <name> --role admin` (docs/SECURITY.md) |
| Alerts to a phone | Telegram bot: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| More traffic | `VITE_TILE_URL` / `REPORT_TILE_URL` to a tile provider you may use (OpenStreetMap's servers are for light use only) |
| Own domain | add an A record to the reserved IP, set `DOMAIN`, rerun `deploy.sh` |

## Risks and answers

| Risk | Answer |
|---|---|
| A1 "out of capacity" when creating the VM | other availability domain, smaller shape, retry at a different hour, or a less busy home region (chosen at sign-up) |
| Certificate not issued | both firewalls must allow 80/443; DuckDNS must resolve to the VM; Let's Encrypt allows only a few failures per hour, so fix and wait |
| An image without an ARM build | db uses `POSTGIS_IMAGE`; api, worker and frontend are built on the VM from `python`, `debian`, `node`, `nginx`, `caddy` images, all multi-arch; the Python wheels used (geopandas, rasterio, pyarrow, scikit-learn, psycopg2-binary) publish aarch64 builds |
| Idle reclaim | Phase 6 step 5 |
| VM lost | nightly encrypted backups off the box; rebuild with Phases 1-5 and `deploy/pg_backup.py restore` |
| Secrets leak | `.env` and `/etc/thermotrace/duckdns.env` stay on the VM (mode 600), never in git; public site is read-only |

## Time and cost

About 2 hours the first time, most of it waiting for capacity and the image build. Cost: 0, as long as the VM stays inside the Always
Free limits shown in the console.

## Definition of done

- `https://<name>.duckdns.org` opens with a valid certificate, Map, 3D Globe and Dashboard tabs work.
- The status badge shows new FIRMS data within 3-4 hours of a satellite pass, without anyone touching the server.
- A write request returns 403.
- The site comes back by itself after `sudo reboot`.
- UptimeRobot and the nightly backup are on.
