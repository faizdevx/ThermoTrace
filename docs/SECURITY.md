# Security: accounts, roles, audit, rate limits, logs, backups

What is built, how to switch it on, and what was and was not tested. Offline operation is in [OFFLINE.md](OFFLINE.md), the hosted
demo in [DEPLOY.md](DEPLOY.md).

## Authentication modes (`AUTH_MODE`)

| Mode | Who is who | Use |
|---|---|---|
| `off` (default) | nobody: reading is open, writes need the legacy `ANALYST_TOKEN` / `ADMIN_TOKEN`, `DEMO_READONLY=true` refuses all writes | development, the public read-only demo |
| `local` | accounts in PostGIS, sign-in with a password, an `HttpOnly; SameSite=Strict; Secure` cookie session | a deployment without an identity provider |
| `proxy` | identity from headers set by an OIDC reverse proxy, trusted only together with a shared secret | a deployment behind the organisation's single sign-on |

Everything under `/api` except `/api/health` and the sign-in endpoints answers 401 without a session (`local`/`proxy`), including the
event stream. State-changing requests in `local` mode must carry `X-Requested-With: ThermoTrace` (CSRF protection on top of
`SameSite=Strict`).

### Local accounts

* Passwords: scrypt (N=2^15, r=8, p=1), 12 characters minimum, not containing the username, not repetitive, a short common-password list. An unknown username costs the same time as a wrong password.
* Five wrong passwords lock the account for 15 minutes (answer 423); every failure is audited. Sign-in is also rate limited per address.
* Sessions: the cookie value is random (256 bit) and only its SHA-256 is stored. 60 minutes idle and 12 hours absolute by default (`SESSION_IDLE_MINUTES`, `SESSION_MAX_HOURS`). Disabling a user, changing their role or password, or signing out ends their sessions at once.
* The last active admin cannot be disabled or demoted.
* First admin and recovery: `docker compose exec api python -m backend.app.users create --username NAME --role admin` (password from a prompt or `THERMOTRACE_PASSWORD`). Also `list`, `set-password`, `set-role`, `disable`, `enable`, `verify-audit`. Changes made there are audited as actor `cli`.

### Behind an OIDC proxy (`AUTH_MODE=proxy`)

The API does not speak OIDC itself. Put an OIDC-aware proxy (oauth2-proxy, an API gateway, Keycloak's gatekeeper) in front that authenticates the
user and forwards `X-Auth-Request-User` and `X-Auth-Request-Groups`; the API accepts those headers only when the request also carries
`X-Auth-Proxy-Secret` equal to `AUTH_PROXY_SECRET`, so a client that reaches the API directly cannot claim an identity. Groups map to roles:
`AUTH_PROXY_ADMIN_GROUP` (default `thermotrace-admin`) is admin, `AUTH_PROXY_ANALYST_GROUP` is analyst, anyone else authenticated is viewer.
`deploy/Caddyfile.oidc.example` shows the pattern with Caddy `forward_auth` (it also deletes client-supplied identity headers).
**Tested here with forged-header and secret tests and a header-driven request test; not tested against a real identity provider.**

## Roles

| | viewer | analyst | admin |
|---|:-:|:-:|:-:|
| map, dashboard, alerts, imagery, facilities | yes | yes | yes |
| Confirm / Reject / Unsure (written to `labels`) | | yes | yes |
| CSV / GeoJSON downloads, PDF incident reports | | yes | yes |
| accounts, audit log, sync trigger | | | yes |

The analyst name on a decision is the signed-in user; the request body cannot name someone else. The UI hides what a role cannot do; the
API refuses it (403) and records the refusal as `access.denied`.

## Audit log

`audit_log` is append-only and hash-chained: each row stores the SHA-256 of the previous row's hash plus its own canonical content (time,
actor, role, action, target, detail, address). Written actions: sign-in (ok / failed with reason), sign-out, password changes, account
creation and changes, every analyst decision (together with the decision, its note and the label id, **in the same database transaction**: both
commit or neither), every export and PDF report (written before the first byte is sent; with filters and row count), sync triggers and
denied requests. A database trigger refuses `UPDATE`, `DELETE` and `TRUNCATE`. A database owner can switch the trigger off, so the chain is the
real guarantee: `GET /api/admin/audit/verify` (button in the Admin tab) and `python -m backend.app.users verify-audit` recompute it and
report the first row that does not match. To make tampering with the *tail* detectable too, copy the printed `head` hash somewhere the database
admin cannot write (a ticket, a signed mail) on a schedule.

## Rate limiting and request logging

* Edge (nginx): 20 requests/s per client for `/api`, 6 per minute for imagery and PDF endpoints (429).
* Application (per process, per address): 600 requests/min overall, 12/min for imagery, PDF and exports, 10/min for sign-in (`RATE_*`). Retry-After is sent.
* Every API request is one JSON line on stdout (`thermotrace.access`): request id (also returned as `X-Request-ID`), address, user and role, method, path (no query string), status, duration. nginx writes its own JSON line. Docker's `json-file` driver rotates (20 MB x 10, `docker-compose.prod.yml`). Behind proxies set `TRUSTED_PROXY_HOPS` (2 for caddy -> nginx -> api) so the logged address is the client's and a forged `X-Forwarded-For` is ignored.

## Backups

`python deploy/pg_backup.py backup|verify|restore|list` (see its docstring): custom-format `pg_dump` of the whole database, SHA-256 + manifest with row
counts, optional AES-256 encryption (`--passphrase-env`), retention, `verify` restores into a scratch database and compares counts, `restore`
refuses the live database without `--into-live`. `deploy/thermotrace-backup.service` / `.timer` run it nightly. Exercised against a real stack:
backup -> encrypted file -> verify (11 tables matched) -> tampered copy refused (checksum) -> wrong passphrase refused -> live restore refused.
Not in the backup: `.env`, the cache volumes, the basemap file.

## Tested

14 integration tests against PostGIS (sign-in, cookie flags, lockout, CSRF header, viewer/analyst/admin matrix, the decision and its audit row,
export and report auditing, user administration and last-admin rule, session end on disable/logout/idle, audit verification and tamper detection,
sign-in rate limit, proxy trust, `off` mode unchanged), 6 unit tests, 8 frontend tests. Run live on the local production stack: 401 without a
session, sign-in with a secure cookie, 403 for CSRF-less writes and for role violations, an audited export, an intact chain.

## Not done / limits

* No real identity provider was available: the proxy mode is tested with simulated headers only.
* No multi-factor authentication, no password-reset mail, no per-user rate limits across several API processes (the limiter is in memory; run one API process or rely on the edge limits).
* The audit log protects against silent edits, not against an administrator who deletes the whole database; keep the head hash elsewhere.
* No penetration test, no dependency audit beyond what `npm audit` / `pip` report; secrets live in `.env` and the process environment, not in a vault.
* `DEMO_READONLY` and `AUTH_MODE=off` leave reading open to anyone who can reach the server.
