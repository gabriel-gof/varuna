# Operations Guide

## Local Development
Backend:
```bash
cd /Users/gabriel/Documents/varuna
backend/venv/bin/python backend/manage.py migrate
backend/venv/bin/python backend/manage.py runserver 0.0.0.0:8000
```

Frontend:
```bash
cd /Users/gabriel/Documents/varuna/frontend
npm install
npm run dev
```

## Docker Compose
Development:
```bash
cd /Users/gabriel/Documents/varuna
docker compose -f docker-compose.dev.yml up -d --build
```

Development service URLs:
- Frontend: `http://localhost:4000`
- Backend API: `http://localhost:8000/api/`

Production:
```bash
cd /Users/gabriel/Documents/varuna
docker compose -f docker-compose.prod.yml up -d --build
```

## Multi-Instance Direction (Per Client)
Use one Compose stack per client when serving different OLT fleets.
This is a deployment direction guide; current dev default remains the single-stack `docker-compose.dev.yml` flow.

Core rules:
- one project name per client (`-p varuna_<client>`),
- one env file per client (DB name, credentials, secrets),
- one port mapping set per client for frontend/backend,
- one dedicated PostgreSQL/Redis data set per client.

Example (same host):
```bash
cd /Users/gabriel/Documents/varuna
docker compose -p varuna_client_a --env-file docker/client-a.env -f docker-compose.dev.yml up -d --build
docker compose -p varuna_client_b --env-file docker/client-b.env -f docker-compose.dev.yml -f docker-compose.client-b.dev.yml up -d --build
```

Typical client override file (`docker-compose.client-b.dev.yml`) should only remap host ports:
```yaml
services:
  frontend:
    ports:
      - "4100:4000"
  backend:
    ports:
      - "8100:8000"
  db:
    ports:
      - "5433:5432"
  redis:
    ports:
      - "6380:6379"
```

Operational recommendations:
- in production, keep `db`/`redis` unexposed and route only HTTP through a reverse proxy,
- stagger discovery/polling intervals across clients to avoid synchronized SNMP bursts,
- apply CPU/memory limits per stack so one tenant cannot starve others,
- back up each client database independently.

If service names appear stale in Docker UI, recreate stack:
```bash
docker compose -f docker-compose.dev.yml down
docker compose -f docker-compose.dev.yml up -d --build --force-recreate
```

## Common Recovery Steps
If this workspace still has pre-refactor DB state from legacy backend migration labels, reset DB first.

Docker reset:
```bash
cd /Users/gabriel/Documents/varuna
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d --build
```

Local SQLite reset:
```bash
cd /Users/gabriel/Documents/varuna
rm -f backend/db.sqlite3 backend/varuna_dev
backend/venv/bin/python backend/manage.py migrate
```

Topology endpoint fails with DB column errors (example: missing `snmp_reachable` or `is_active`):
```bash
cd /Users/gabriel/Documents/varuna
backend/venv/bin/python backend/manage.py migrate
```

Recent schema additions include OLT interval fields such as `power_interval_seconds`, so migrations are mandatory before opening topology/settings.
Recent schema additions also include persistent maintenance queue tracking (`topology_maintenancejob`, migration `0015_maintenancejob_and_more`), so applying migrations is mandatory before using background discovery/polling/power actions.

If running with Docker, also recreate the stack so containers pick up new code and schema:
```bash
docker compose -f docker-compose.dev.yml down
docker compose -f docker-compose.dev.yml up -d --build --force-recreate
```

## Authentication Bootstrap
Create the initial admin user before first login:

Docker:
```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py ensure_auth_user \
  --username admin --password changeme --role admin --superuser
```

Local:
```bash
backend/venv/bin/python backend/manage.py ensure_auth_user \
  --username admin --password changeme --role admin --superuser
```

Use `--force-password` to update an existing user's password. Environment variable fallbacks: `VARUNA_AUTH_USERNAME`, `VARUNA_AUTH_PASSWORD`, `VARUNA_AUTH_ROLE`.

Roles: `admin` (full access), `operator` (full access), `viewer` (read-only).

## Manual Jobs
Run discovery for all eligible OLTs:
```bash
backend/venv/bin/python backend/manage.py discover_onus
```

Run polling for all eligible OLTs:
```bash
backend/venv/bin/python backend/manage.py poll_onu_status
```

Run one OLT only:
```bash
backend/venv/bin/python backend/manage.py discover_onus --olt-id <ID>
backend/venv/bin/python backend/manage.py poll_onu_status --olt-id <ID>
```

Run due OLTs in capped batches (oldest due first):
```bash
backend/venv/bin/python backend/manage.py discover_onus --max-olts 10
backend/venv/bin/python backend/manage.py poll_onu_status --max-olts 20
```

Force run (bypass due checks):
```bash
backend/venv/bin/python backend/manage.py discover_onus --force
backend/venv/bin/python backend/manage.py poll_onu_status --force
```

## Background Collection (Scheduler)
In Docker dev mode, the `run_scheduler` management command runs as a background process alongside the Django server. It automatically dispatches polling, discovery, power collection, and SNMP reachability checks.

```bash
# Scheduler starts automatically in docker-compose.dev.yml
# To run manually:
docker compose -f docker-compose.dev.yml exec backend python manage.py run_scheduler

# With custom intervals:
docker compose -f docker-compose.dev.yml exec backend python manage.py run_scheduler --tick-seconds 60 --snmp-check-seconds 300

# With SNMP backoff cap and per-tick OLT batch limits:
docker compose -f docker-compose.dev.yml exec backend python manage.py run_scheduler \
  --snmp-check-seconds 180 \
  --snmp-check-max-backoff-seconds 1800 \
  --max-poll-olts-per-tick 20 \
  --max-discovery-olts-per-tick 10 \
  --max-power-olts-per-tick 10
```

Monitor scheduler logs:
```bash
docker compose -f docker-compose.dev.yml logs -f backend | grep scheduler
```

Manual one-off collection (e.g. from host cron or debugging):
```bash
# Discovery (runs only due OLTs)
docker compose -f docker-compose.dev.yml exec backend python manage.py discover_onus

# Polling (runs only due OLTs, respects runtime budget)
docker compose -f docker-compose.dev.yml exec backend python manage.py poll_onu_status
```

The polling command enforces a `max_runtime_seconds` budget (default 180s) to prevent long-running jobs from blocking subsequent cycles. Configure via `SystemSettings.MAX_POLL_RUNTIME_SECONDS` (range 30-1800s).

SNMP check behavior is adaptive:
- Reachable OLTs are checked on the base `--snmp-check-seconds` cadence.
- Repeatedly unreachable OLTs are checked less frequently (exponential backoff) up to `--snmp-check-max-backoff-seconds`.
- Scheduler logs include SNMP summary lines (`checked`, `skipped_not_due`, `reachable`, `unreachable`, elapsed) for tuning verification.

## Manual Maintenance Queue Observability
Background actions triggered from Settings (`background=true`) are persisted in `MaintenanceJob` and can be observed via API:

```bash
curl -H "Authorization: Token <token>" \
  http://localhost:8000/api/olts/<OLT_ID>/maintenance_status/
```

Response includes active/latest job metadata:
- `status`: `queued`, `running`, `completed`, `failed`, `canceled`
- `progress`: `0..100`
- `detail`, `output`, `error`

If queued jobs are present, backend API calls that touch maintenance status (`maintenance_status`, `snmp_check`) automatically ensure the in-process runner is active.

## Validation
Backend tests:
```bash
backend/venv/bin/python backend/manage.py test topology -v 2
```

Frontend build check:
```bash
cd /Users/gabriel/Documents/varuna/frontend
npm run build
```
