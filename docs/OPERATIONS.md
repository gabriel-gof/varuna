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
- Backend health endpoint: `http://localhost:8000/api/healthz/`

Backend collection contract:
- `ENABLE_SCHEDULER=1` must stay enabled in runtime env so discovery, polling, SNMP checks, power collection, and history pruning run without any frontend session.
- Collections are shared backend state for all users; frontend reads snapshots and does not need to trigger SNMP collection for normal topology usage.
- Recommended topology cache TTL for fast first load is `300s` (`OLT_TOPOLOGY_LIST_CACHE_TTL` and `OLT_TOPOLOGY_DETAIL_CACHE_TTL`).
- Backend SNMP transport uses `puresnmp`.

Production:
```bash
cd /Users/gabriel/Documents/varuna
docker compose -p varuna --env-file docker/prod.env -f docker-compose.prod.yml up -d --build
```

Production ingress contract:
- host-level reverse proxy terminates TLS and forwards `X-Forwarded-Proto` to frontend,
- frontend forwards that header to backend `/api`,
- backend trusts `X-Forwarded-Proto=https` for `SECURE_SSL_REDIRECT` and secure-cookie behavior.

## Multi-Instance Production (Per Client)
Use one production Compose stack per client when serving different OLT fleets.

Core rules:
- one project name per client (`-p varuna_<client>`),
- one env file per client (DB name, credentials, secrets, hostnames, and compose overrides),
- one localhost frontend/backend bind per client (`VARUNA_*_BIND_IP=127.0.0.1`),
- one frontend host port per client (and optional backend host port),
- one dedicated PostgreSQL/Redis data set per client.

`docker-compose.prod.yml` reads these instance-specific compose variables:
- `VARUNA_ENV_FILE`
- `VARUNA_FRONTEND_BIND_IP`
- `VARUNA_FRONTEND_HTTP_HOST_PORT`
- `VARUNA_BACKEND_BIND_IP`
- `VARUNA_BACKEND_HOST_PORT`
- `VARUNA_TLS_CERTS_DIR`
- `VARUNA_DB_LIMIT_*`, `VARUNA_REDIS_LIMIT_*`, `VARUNA_BACKEND_LIMIT_*`, `VARUNA_FRONTEND_LIMIT_*`
- `VARUNA_GUNICORN_WORKERS`, `VARUNA_GUNICORN_THREADS`, `VARUNA_GUNICORN_TIMEOUT_SECONDS`

Production backend mode:
- `BACKEND_BEHIND_FRONTEND_PROXY=1` is set in compose so backend serves API over internal HTTP (no 80->443 redirect loop when frontend proxies `/api`).
- backend runtime command is Gunicorn on internal port `80`,
- frontend serves `/static` from shared volume `/var/www/static`.

Bring up a second instance on the same host:
```bash
cd /Users/gabriel/Documents/varuna
cp docker/prod.env docker/prod.client-b.env
```

Edit `docker/prod.client-b.env`:
- `VARUNA_ENV_FILE=docker/prod.client-b.env`
- unique host ports (for example `VARUNA_FRONTEND_HTTP_HOST_PORT=18080`, `VARUNA_BACKEND_HOST_PORT=18081`)
- unique DB identity (`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`)
- per-instance hostnames (`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SERVER_NAME`, `SERVER_ALIASES`)
- TLS mount base when needed (`VARUNA_TLS_CERTS_DIR`)

Start instance:
```bash
cd /Users/gabriel/Documents/varuna
docker compose -p varuna_client_b --env-file docker/prod.client-b.env -f docker-compose.prod.yml up -d --build
```

Example: `gabisat` instance on the same server (secrets outside repo):
```bash
cd /Users/gabriel/Documents/varuna
# prepare secure runtime env (chmod 600) at /etc/varuna/prod.gabisat.env
# keep binds local:
# - VARUNA_FRONTEND_BIND_IP=127.0.0.1
# - VARUNA_BACKEND_BIND_IP=127.0.0.1
# and set domain:
# - ALLOWED_HOSTS=varuna.gabisat.com.br
# - CSRF_TRUSTED_ORIGINS=https://varuna.gabisat.com.br
# - SERVER_NAME=varuna.gabisat.com.br

docker compose -p varuna_gabisat --env-file /etc/varuna/prod.gabisat.env -f docker-compose.prod.yml up -d --build
```

Daily operations for one instance:
```bash
# logs
docker compose -p varuna_client_b --env-file docker/prod.client-b.env -f docker-compose.prod.yml logs -f

# restart / recreate after env edits
docker compose -p varuna_client_b --env-file docker/prod.client-b.env -f docker-compose.prod.yml up -d --build --force-recreate

# stop
docker compose -p varuna_client_b --env-file docker/prod.client-b.env -f docker-compose.prod.yml down
```

Operational recommendations:
- in production, keep `db`/`redis` unexposed and route only HTTP through a reverse proxy,
- stagger discovery/polling intervals across clients to avoid synchronized SNMP bursts,
- apply CPU/memory limits per stack so one tenant cannot starve others,
- back up each client database independently.

### HTTPS-Only Host Ingress (Gabisat)
Bring certificate and host Nginx online:
```bash
# issue cert (temporary HTTP challenge via standalone)
systemctl stop nginx
certbot certonly --standalone --preferred-challenges http \
  -d varuna.gabisat.com.br \
  --agree-tos --register-unsafely-without-email --non-interactive

# configure host Nginx 443-only reverse proxy -> localhost:18080
cat > /etc/nginx/sites-available/varuna.gabisat.com.br <<'EOF'
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name varuna.gabisat.com.br;

    ssl_certificate /etc/letsencrypt/live/varuna.gabisat.com.br/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/varuna.gabisat.com.br/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;
    ssl_session_tickets off;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    if ($host != "varuna.gabisat.com.br") { return 444; }

    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
# cleanup any previous hostname site files from sites-enabled/sites-available as needed
ln -sfn /etc/nginx/sites-available/varuna.gabisat.com.br /etc/nginx/sites-enabled/varuna.gabisat.com.br
nginx -t
systemctl start nginx
systemctl reload nginx
```

Validation:
```bash
curl -I https://varuna.gabisat.com.br
curl https://varuna.gabisat.com.br/api/healthz/
ss -tulpn | rg ':22|:80|:443|:18080|:18081'
```

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
Recent schema additions also include persisted power history (`topology_onupowersample`, migration `0018_onupowersample`) required for Power Report and Alarm History data endpoints.

If running with Docker, also recreate the stack so containers pick up new code and schema:
```bash
docker compose -f docker-compose.dev.yml down
docker compose -f docker-compose.dev.yml up -d --build --force-recreate
```

If backend appears `unhealthy`, verify health endpoint directly:
```bash
docker compose -f docker-compose.dev.yml exec backend \
  curl -fsS http://localhost:8000/api/healthz/
```
Expected response:
```json
{"status":"ok"}
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

Prune historical data manually:
```bash
backend/venv/bin/python backend/manage.py prune_history
```

## Background Collection (Scheduler)
The `run_scheduler` management command runs as a background process when backend env sets `ENABLE_SCHEDULER=1`. Current dev/prod env templates enable this by default. It automatically dispatches polling, discovery, power collection, SNMP reachability checks, and history prune cycles.

```bash
# Scheduler starts automatically when ENABLE_SCHEDULER=1.
# To run an extra manual instance (debug only):
docker compose -f docker-compose.dev.yml exec backend python manage.py run_scheduler

# With custom intervals:
docker compose -f docker-compose.dev.yml exec backend python manage.py run_scheduler --tick-seconds 60 --snmp-check-seconds 300

# With SNMP backoff cap and per-tick OLT batch limits:
docker compose -f docker-compose.dev.yml exec backend python manage.py run_scheduler \
  --snmp-check-seconds 180 \
  --snmp-check-max-backoff-seconds 1800 \
  --history-prune-seconds 21600 \
  --max-poll-olts-per-tick 20 \
  --max-discovery-olts-per-tick 10 \
  --max-power-olts-per-tick 10
```

Monitor scheduler logs:
```bash
docker compose -f docker-compose.dev.yml logs -f backend | grep scheduler
```

Topology API cache tuning (env):
- `OLT_LIST_CACHE_TTL`: base `/api/olts/` list cache.
- `OLT_TOPOLOGY_LIST_CACHE_TTL`: `/api/olts/?include_topology=true` cache.
- `OLT_TOPOLOGY_DETAIL_CACHE_TTL`: `/api/olts/{id}/topology/` cache.

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

History retention knobs:
- `POWER_HISTORY_RETENTION_DAYS` (default `30`)
- `ALARM_HISTORY_RETENTION_DAYS` (default `90`)
- `HISTORY_PRUNE_INTERVAL_SECONDS` (default `21600`)

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

Timeout safety knobs (optional env overrides):
- `MAINTENANCE_DISCOVERY_TIMEOUT_SECONDS` (default `1800`)
- `MAINTENANCE_POLLING_TIMEOUT_SECONDS` (default `1200`)
- `MAINTENANCE_POWER_TIMEOUT_SECONDS` (default `1800`)

When a background job exceeds its timeout window, it is marked `failed` with timeout detail/error so the same OLT can accept a new queued job instead of staying blocked in `running` forever.

## Topology Gray-State Soak (2h)
Use the soak checker to verify stale/unreachable behavior continuously from backend topology payloads.

Run (default 2 hours):
```bash
cd /Users/gabriel/Documents/varuna
python3 scripts/soak_topology_health.py \
  --base-url http://localhost:8000 \
  --username <user> \
  --password '<pass>' \
  --duration-seconds 7200 \
  --interval-seconds 30 \
  --detail-probe-seconds 300 \
  --run-id soak2h \
  --fail-on-anomaly
```

Outputs:
- line-by-line event log: `artifacts/soak/<run-id>.jsonl`
- final summary report: `artifacts/soak/<run-id>.summary.json`

Live follow:
```bash
tail -f artifacts/soak/<run-id>.jsonl
```

What it checks:
- topology list endpoint (`/api/olts/?include_topology=true`) every interval;
- expected health state using frontend stale-window logic (`gray` on `snmp_failure_count>=2` or stale polling age);
- consistency of SNMP health metadata between list and detail (`/api/olts/{id}/topology/`) on probe interval;
- state transitions and anomaly counts over the full soak duration.

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
