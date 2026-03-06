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

Core app services in compose:
- `frontend`
- `backend`
- `varuna-db`
- `redis`

Development service URLs:
- Frontend: `http://localhost:4000`
- Backend API: `http://localhost:8000/api/`
- Backend health endpoint: `http://localhost:8000/api/healthz/`
- Zabbix Web: `http://localhost:8080`

Version label contract:
- Product version source of truth is root `VERSION`.
- Frontend resolves `__APP_VERSION__` from `APP_VERSION` env or `VERSION` file (`/app/VERSION` in dev compose, mounted from repo root).
- After changing root `VERSION` in dev compose, recreate/restart `frontend` so Vite reloads config and applies the new version label.

Development default app login (from `docker/dev.env` bootstrap):
- user: `admin`
- password: `admin`

Backend collection contract:
- `ENABLE_SCHEDULER=1` must stay enabled in runtime env so discovery, polling, collector checks, power collection, and history pruning run without any frontend session.
- Collections are shared backend state for all users; frontend reads live API responses backed by current DB/Zabbix data and does not need to trigger manual collection for normal topology usage.
- Redis remains part of the default stack because topology structure cache is enabled for slow-changing inventory. Runtime status, Power Report, Alarm History, and scoped power snapshot reads do not require Redis to return current data.
- Zabbix integration requires:
  - `ZABBIX_API_URL`
  - either `ZABBIX_API_TOKEN` or (`ZABBIX_USERNAME` + `ZABBIX_PASSWORD`)
  - optional `ZABBIX_HOST_NAME_BY_OLT_JSON` for OLT->host alias mapping.
  - optional `TOPOLOGY_STRUCTURE_CACHE_TTL` (default `43200`) for per-OLT topology structure cache lifetime in Redis.
  - optional `ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS` (default `512`) to cap immediate `task.create` executions; explicit manual refresh paths can bypass via `--force-upstream`.
  - optional `ZABBIX_AVAILABILITY_INTERVAL_SECONDS` (default `30`) used by host macro `{$VARUNA.AVAILABILITY_INTERVAL}` for SNMP sentinel polling cadence.
  - optional `ZABBIX_AVAILABILITY_STALE_SECONDS` (default `45`) for sentinel freshness threshold in Varuna reachability checks.
  - optional `ZABBIX_REFRESH_CLOCK_GRACE_SECONDS` (default `15`) for upstream-refresh clock validation window.
  - optional `ZABBIX_REFRESH_UPSTREAM_WAIT_SECONDS` (default `12`) for short wait time while polling fresh upstream status clocks.
  - optional `ZABBIX_REFRESH_UPSTREAM_WAIT_STEP_SECONDS` (default `2`) for fetch retry step during that wait window.
  - optional `ZABBIX_DISCOVERY_REFRESH_WAIT_SECONDS` (default `15`) for short wait time while reading ONU discovery rows after an upstream execute request.
  - optional `ZABBIX_DISCOVERY_REFRESH_WAIT_STEP_SECONDS` (default `2`) for discovery fetch retry step during that wait window.
  - optional `ZABBIX_DISCONNECT_HISTORY_MAX_ITEMS` (default `512`) to cap per-run history lookups for offline transition validation.
  - optional `ZABBIX_DISCONNECT_WINDOW_MARGIN_SECONDS` (default `90`) as trust margin for `online -> offline` timestamp window validation.
  - optional `ZABBIX_STATUS_STALE_MARGIN_SECONDS` (default `90`) as stale-sample safety margin for status freshness checks.
  - optional `ZABBIX_HOST_GROUP_NAME` (default `OLT`) to place OLT hosts into a client-specific Zabbix host group (recommended Title Case with spaces/slashes, for example `Varuna/GabSAT`, `Varuna/VNET`, `Varuna/Local`).
  - optional `ZABBIX_HOST_GROUP_LEGACY_NAMES` (default `OLT,OLTs`) to define old group names that should be removed from managed hosts during sync.
  - optional `ZABBIX_HOST_NAME_PREFIX` (default empty) to namespace host names per client instance (for example `GabSAT-`, producing `GabSAT-OLT-BSJ-01`).
  - optional `COLLECTOR_CHECK_SECONDS` (default `30`) for scheduler reachability cadence.
  - optional `COLLECTOR_CHECK_MAX_BACKOFF_SECONDS` (default `1800`, compatibility knob).

## Zabbix Dev Setup
Current dev compose includes Zabbix `7.0 LTS` services:
- `zabbix-db` (PostgreSQL)
- `zabbix-server`
- `zabbix-web`
- `zabbix-agent` (agent2 sidecar used for local self-monitoring checks)
- default server timeout is tuned to `ZBX_TIMEOUT=10` for slow OLT SNMP walks.
- SNMP unreachable convergence is tuned for faster gray/recovery transitions:
  - `ZBX_UNREACHABLEDELAY=5`
  - `ZBX_UNAVAILABLEDELAY=15`
  - `ZBX_UNREACHABLEPERIOD=30`
- dev cache sizing is tuned with `ZBX_CACHESIZE=1G` to reduce configuration-cache pressure with large ONU inventories.
- dev server concurrency/caches are tuned for larger ONU fleets:
  - `ZBX_STARTPOLLERS=30`
  - `ZBX_STARTPOLLERSUNREACHABLE=15`
  - `ZBX_STARTSNMPPOLLERS=80`
  - `ZBX_STARTPREPROCESSORS=12`
  - `ZBX_STARTLLDPROCESSORS=4`
  - `ZBX_HISTORYCACHESIZE=256M`
  - `ZBX_HISTORYINDEXCACHESIZE=64M`
  - `ZBX_VALUECACHESIZE=256M`
  - `ZBX_TRENDCACHESIZE=32M`
- dev `zabbix-db` container uses PostgreSQL tuning for write-heavy history workloads (`shared_buffers=2GB`, `effective_cache_size=6GB`, `work_mem=4MB`, `maintenance_work_mem=512MB`, `max_wal_size=4GB`, `min_wal_size=1GB`, `wal_buffers=64MB`, `synchronous_commit=off`, `checkpoint_completion_target=0.9`, `checkpoint_timeout=15min`, `effective_io_concurrency=200`).

Default dev login:
- user: `Admin`
- password: `zabbix`

Template import workflow:
- Open `http://localhost:8080` in a browser.
- Import vendor templates from repo root:
  - `snmp-avail-template.yaml`
  - `huawei-template.yaml`
  - `fiberhome-template.yaml`
  - `zte-template.yaml`
  - `vsol-like-template.yaml`
- Template naming convention (human-facing): Title Case with preserved acronyms and spaces (no underscores/hyphens).
  - `OLT Huawei Unified`
  - `OLT Fiberhome Unified`
  - `OLT ZTE C300`
  - `OLT ZTE C600`
  - `OLT VSOL GPON 8P`
- Varuna controls Zabbix collection cadence through host macros pushed on OLT create/update:
  - `{$VARUNA.DISCOVERY_INTERVAL}`
  - `{$VARUNA.STATUS_INTERVAL}`
  - `{$VARUNA.POWER_INTERVAL}`
  - `{$VARUNA.AVAILABILITY_INTERVAL}`
  - `{$VARUNA.HISTORY_DAYS}`
  - `{$VARUNA.SNMP_IP}`
  - `{$VARUNA.SNMP_PORT}`
  - `{$VARUNA.SNMP_COMMUNITY}`
- Sentinel SNMP availability lives in shared template `Varuna SNMP Availability` (`varunaSnmpAvailability`, `sysName.0`) driven by `{$VARUNA.AVAILABILITY_INTERVAL}` for fast reachability flips.
- Power preprocessors in both templates discard sentinel optical values (`0 dBm` and `-40 dBm`) before history write, so frontend no longer needs client-side sentinel filtering.
- ZTE C300/C600 note:
  - power preprocessors use vendor-specific raw conversion formulas (ONU Rx register conversion + OLT Rx thousandths conversion) instead of generic `/10` or `/100` scaling.
  - invalid raw sentinel readings (`-80000`, `65535`, empty/non-numeric) are normalized to an out-of-range fallback (`-80`) to avoid `Not supported` item state storms on hosts with many offline ONUs.
  - Varuna backend power normalization discards out-of-range values, so UI/history stays clean while Zabbix items remain supported.
  - `OLT ZTE C600` uses a different status code map from `OLT ZTE C300`; live validation on `192.168.7.151` (`sysName=ZTE-PONTAL`) confirmed `3/4 -> online`, `2 -> link_loss`, `5 -> dying_gasp`, `7 -> offline`, with `1 -> link_loss` retained as a compatible LOS-class fallback.
  - The C600 ONU name OID is still `1.3.6.1.4.1.3902.1082.500.10.2.3.3.1.2`; if the OLT returns `""`, treat the ONU as nameless rather than substituting numeric placeholders.
  - C600 serial payloads may include numeric prefixes such as `1,<serial>`; template preprocessing should strip the prefix before Varuna discovery/polling runs.
- Template default macro values are bootstrap-only (`5m`, `1m`, `5m`, `30s`, `7d`) and are overridden per OLT by Varuna settings.
- Varuna ONU LLD rules in both Huawei and Fiberhome templates are configured with immediate lost-resource cleanup (`Delete lost resources = Immediately`) so stale ONU item prototypes are removed as soon as discovery no longer returns them.
- On OLT create/update, Varuna also synchronizes Zabbix host runtime fields:
  - auto-create missing Zabbix host with vendor template + shared `Varuna SNMP Availability` linkage when possible,
  - host group membership to `ZABBIX_HOST_GROUP_NAME` (legacy names from `ZABBIX_HOST_GROUP_LEGACY_NAMES` are migrated automatically),
  - host technical/visible names using optional prefix (`ZABBIX_HOST_NAME_PREFIX + OLT.name`),
  - host tags (`source=varuna`, `vendor`, `model`) from Varuna vendor profile in lowercase values,
  - SNMP interface `ip/port/community` references to `{$VARUNA.SNMP_IP}` / `{$VARUNA.SNMP_PORT}` / `{$VARUNA.SNMP_COMMUNITY}`,
  - fallback creation of sentinel item `varunaSnmpAvailability` on host when missing,
  - SNMP runtime macro values (`IP/port/community`) from OLT settings.
- On OLT delete in Varuna, backend attempts `host.delete` in Zabbix for the resolved host.
- Zabbix host resolution is self-healing across host recreation: stale cached host IDs are validated and re-resolved by host name/IP automatically.
- Huawei and Fiberhome vendor profiles are standardized to model `UNIFICADO`; host tag `model` is synced as lowercase `unified` (English normalization in Zabbix).
- Trends are disabled in Varuna templates (`trends=0`) to reduce Zabbix storage overhead.
- Item history in Varuna templates is driven by `{$VARUNA.HISTORY_DAYS}` (default `7d`) for ONU status and power metrics.
- ONU item prototypes include `slot` and `pon` tags (`slot={#SLOT}`, `pon={#PON}`) for direct slot/PON filtering in Zabbix item views.
- Self-monitoring (dev and production):
  - `Zabbix server` host agent interface must target `zabbix-agent:10050` (DNS), not `127.0.0.1:10050` inside `zabbix-server`.
  - Required templates on `Zabbix server` host: `Zabbix server health` (internal checks) + `Zabbix agent` (passive agent).
  - `zabbix-agent` (agent2) service must be running in the same compose stack and network as `zabbix-server`.

Production:
```bash
cd /Users/gabriel/Documents/varuna
docker compose -p varuna --env-file docker/prod.env -f docker-compose.prod.yml up -d --build
```

Production (shared Postgres/Zabbix infra mode):
```bash
cd /Users/gabriel/Documents/varuna
docker compose -p varuna --env-file docker/prod.env \
  -f docker-compose.prod.shared-pg.yml up -d --build
```

Production ingress contract:
- host-level reverse proxy terminates TLS and forwards `X-Forwarded-Proto` to frontend,
- frontend forwards that header to backend `/api`,
- backend trusts `X-Forwarded-Proto=https` for `SECURE_SSL_REDIRECT` and secure-cookie behavior.

## Production Zabbix Access Policy
If `zabbix-web` is exposed for operator debugging, keep this mandatory policy:
- expose only over HTTPS (`443`) behind a reverse proxy;
- use strong credentials only (long random passwords/tokens, never default `Admin/zabbix`);
- keep two separate users:
  - `varuna_api` for Varuna integration (`ZABBIX_USERNAME`/`ZABBIX_PASSWORD` or `ZABBIX_API_TOKEN`);
  - personal operator/admin user (for example `gabriel`) for manual Zabbix UI access;
- do not use personal admin credentials in Varuna env files;
- keep `zabbix-web` bound to private/localhost interfaces when possible, or restrict with IP allowlist/VPN.
- in shared infra, `zabbix-hardening` enforces user policy on every `docker compose ... up`:
  - ensures `gabriel` and `varuna` users exist (passwords from `docker/infra.shared.env` / runtime env file),
  - removes default bootstrap `Admin` user when `ZABBIX_REMOVE_BOOTSTRAP_ADMIN=1`.

## Multi-Instance Production (Per Client)
Use one production Compose stack per client when serving different OLT fleets.

Core rules:
- one project name per client (`-p varuna_<client>`),
- one env file per client (logical DB name, credentials, secrets, hostnames, and compose overrides),
- one localhost frontend/backend bind per client (`VARUNA_*_BIND_IP=127.0.0.1`),
- one frontend host port per client (and optional backend host port),
- one dedicated Varuna logical database per client inside shared `pg-varuna`,
- one dedicated Redis per client stack (recommended practical isolation).

Shared vs per-instance responsibility:
- Shared once per host:
  - `pg-varuna` (single PostgreSQL service, multiple Varuna logical DBs),
  - `pg-zabbix`,
  - `zabbix-server`,
  - `zabbix-web` (optional external exposure for operators).
- Per Varuna instance (one stack per client):
  - `frontend`,
  - `backend`,
  - `redis`.

Per-instance mandatory identity in shared Zabbix:
- `ZABBIX_HOST_GROUP_NAME` must be unique per client namespace (for example `Varuna/GabSAT`, `Varuna/VNET`).
- `ZABBIX_HOST_NAME_PREFIX` must be unique per client (for example `GabSAT-`, `VNET-`) to avoid host name collisions.
- Keep a dedicated API user for Varuna (for example `varuna_api`) and a separate personal/admin user for manual Zabbix UI access.

Recommended practical deployment on one VM:
- shared infra stack (run once):
  - `pg-varuna` (single PostgreSQL container for all Varuna logical DBs),
  - `pg-zabbix` (separate PostgreSQL container only for Zabbix),
  - `zabbix-server` + `zabbix-web`.
- per-client application stack:
  - `frontend` + `backend` + `redis`.

`docker-compose.prod.yml` reads these instance-specific compose variables:
- `VARUNA_ENV_FILE`
- `VARUNA_FRONTEND_BIND_IP`
- `VARUNA_FRONTEND_HTTP_HOST_PORT`
- `VARUNA_BACKEND_BIND_IP`
- `VARUNA_BACKEND_HOST_PORT`
- `VARUNA_POSTGRES_HOST`
- `VARUNA_TLS_CERTS_DIR`
- `VARUNA_DB_LIMIT_*`, `VARUNA_REDIS_LIMIT_*`, `VARUNA_BACKEND_LIMIT_*`, `VARUNA_FRONTEND_LIMIT_*`
- `VARUNA_GUNICORN_WORKERS`, `VARUNA_GUNICORN_THREADS`, `VARUNA_GUNICORN_TIMEOUT_SECONDS`

Shared-mode compose files:
- `docker-compose.infra.shared.yml`: starts shared `pg-varuna`, `pg-zabbix`, `zabbix-server`, `zabbix-web`.
  - also includes one-shot `zabbix-hardening` to enforce secure user policy after startup.
- `docker-compose.prod.shared-pg.yml`: per-client app stack using shared `pg-varuna` over external network `varuna-data`.
- Shared infra tuning knobs live in `docker/infra.shared.env`:
  - Zabbix server worker/cache knobs (`ZBX_START*`, `ZBX_*CACHESIZE`, `ZBX_TIMEOUT`, `ZBX_UNREACHABLEDELAY`, `ZBX_UNAVAILABLEDELAY`, `ZBX_UNREACHABLEPERIOD`, housekeeping knobs),
  - Zabbix PostgreSQL knobs (`ZBX_PG_*`) applied directly by `pg-zabbix` container command,
  - production baseline is tuned for 4 CPU / 16 GB RAM / SSD with ~141 Zabbix worker processes and `synchronous_commit=off` for maximum write throughput.

Production backend mode:
- `BACKEND_BEHIND_FRONTEND_PROXY=1` is set in compose so backend serves API over internal HTTP (no 80->443 redirect loop when frontend proxies `/api`).
- backend runtime command is Gunicorn on internal port `80`,
- frontend serves `/static` from shared volume `/var/www/static`.

Bring up shared infra (once per host):
```bash
cd /Users/gabriel/Documents/varuna
cp docker/infra.shared.env /etc/varuna/infra.shared.env
# Edit secrets and ports in /etc/varuna/infra.shared.env
# Required for automatic Zabbix user hardening:
# - ZABBIX_OPERATOR_PASSWORD
# - ZABBIX_VARUNA_PASSWORD

docker compose --env-file /etc/varuna/infra.shared.env -f docker-compose.infra.shared.yml up -d
```

Create per-client logical database in shared `pg-varuna`:
```bash
docker compose --env-file /etc/varuna/infra.shared.env -f docker-compose.infra.shared.yml exec -T pg-varuna psql -U postgres -d postgres -c \"CREATE USER varuna_client_b WITH PASSWORD 'CHANGE-THIS-PASSWORD';\"
docker compose --env-file /etc/varuna/infra.shared.env -f docker-compose.infra.shared.yml exec -T pg-varuna psql -U postgres -d postgres -c \"CREATE DATABASE varuna_client_b OWNER varuna_client_b;\"
```

Bring up a client app stack:
```bash
cd /Users/gabriel/Documents/varuna
cp docker/prod.env docker/prod.client-b.env
```

Edit `docker/prod.client-b.env`:
- `VARUNA_ENV_FILE=docker/prod.client-b.env`
- unique host ports (for example `VARUNA_FRONTEND_HTTP_HOST_PORT=18080`, `VARUNA_BACKEND_HOST_PORT=18081`)
- unique DB identity (`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`)
- `VARUNA_POSTGRES_HOST=pg-varuna`
- per-instance hostnames (`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SERVER_NAME`, `SERVER_ALIASES`)
- TLS mount base when needed (`VARUNA_TLS_CERTS_DIR`)
- Zabbix API settings (`ZABBIX_API_URL=http://zabbix-web:8080/api_jsonrpc.php`)

Start instance:
```bash
cd /Users/gabriel/Documents/varuna
docker compose -p varuna_client_b --env-file docker/prod.client-b.env \
  -f docker-compose.prod.shared-pg.yml up -d --build
```

Example: `gabisat` instance on the same server (secrets outside repo):
```bash
cd /Users/gabriel/Documents/varuna
# prepare secure runtime env (chmod 600) at /etc/varuna/prod.gabisat.env
# keep binds local:
# - VARUNA_FRONTEND_BIND_IP=127.0.0.1
# - VARUNA_BACKEND_BIND_IP=127.0.0.1
# - VARUNA_POSTGRES_HOST=pg-varuna
# and set domain:
# - ALLOWED_HOSTS=varuna.gabisat.com.br
# - CSRF_TRUSTED_ORIGINS=https://varuna.gabisat.com.br
# - SERVER_NAME=varuna.gabisat.com.br

docker compose -p varuna_gabisat --env-file /etc/varuna/prod.gabisat.env \
  -f docker-compose.prod.shared-pg.yml up -d --build
```

Example: `demo` instance (`demo.varuna.network`, ports 18100/18101):
```bash
cd /Users/gabriel/Documents/varuna
docker compose -p varuna_demo --env-file docker/prod.demo.env \
  -f docker-compose.prod.shared-pg.yml up -d --build
```

Daily operations for one instance:
```bash
# logs
docker compose -p varuna_client_b --env-file docker/prod.client-b.env \
  -f docker-compose.prod.shared-pg.yml logs -f

# restart / recreate after env edits
docker compose -p varuna_client_b --env-file docker/prod.client-b.env \
  -f docker-compose.prod.shared-pg.yml up -d --build --force-recreate

# stop
docker compose -p varuna_client_b --env-file docker/prod.client-b.env \
  -f docker-compose.prod.shared-pg.yml down
```

Operational recommendations:
- keep shared `pg-varuna`/`pg-zabbix` host ports bound to localhost or private network only,
- keep per-client `redis` unexposed and route only HTTP through a reverse proxy,
- stagger discovery/polling intervals across clients to avoid synchronized SNMP bursts,
- apply CPU/memory limits on both shared infra and per-client stacks so one tenant cannot starve others,
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
Development compose can bootstrap app auth user automatically at container startup when enabled:
- `VARUNA_AUTH_BOOTSTRAP=1`
- `VARUNA_AUTH_USERNAME=admin`
- `VARUNA_AUTH_PASSWORD=admin`
- `VARUNA_AUTH_ROLE=admin`
- `VARUNA_AUTH_SUPERUSER=1`
- `VARUNA_AUTH_FORCE_PASSWORD=1`

Manual bootstrap/update:

Docker:
```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py ensure_auth_user \
  --username admin --password admin --role admin --superuser --force-password
```

Local:
```bash
backend/venv/bin/python backend/manage.py ensure_auth_user \
  --username admin --password admin --role admin --superuser --force-password
```

Use `--force-password` to update an existing user's password. Environment variable fallbacks: `VARUNA_AUTH_USERNAME`, `VARUNA_AUTH_PASSWORD`, `VARUNA_AUTH_ROLE`.

Roles: `admin` (full access, including Settings and OLT maintenance), `operator` (no Settings tab, but can edit PON descriptions and trigger scoped topology status/power refresh), `viewer` (read-only).

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
The `run_scheduler` management command runs as a background process when backend env sets `ENABLE_SCHEDULER=1`. Current dev/prod env templates enable this by default. It automatically dispatches polling, discovery, power collection, collector reachability checks, and history prune cycles.

```bash
# Scheduler starts automatically when ENABLE_SCHEDULER=1.
# To run an extra manual instance (debug only):
docker compose -f docker-compose.dev.yml exec backend python manage.py run_scheduler

# With custom intervals:
docker compose -f docker-compose.dev.yml exec backend python manage.py run_scheduler --tick-seconds 60 --collector-check-seconds 300

# With collector-check cadence and per-tick OLT batch limits:
docker compose -f docker-compose.dev.yml exec backend python manage.py run_scheduler \
  --collector-check-seconds 30 \
  --collector-check-max-backoff-seconds 1800 \
  --history-prune-seconds 21600 \
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

# Manual immediate polling for one OLT, bypassing upstream execution cap
docker compose -f docker-compose.dev.yml exec backend python manage.py poll_onu_status \
  --olt-id 1 --force --refresh-upstream --force-upstream
```

The polling command enforces a `max_runtime_seconds` budget (default 180s) to prevent long-running jobs from blocking subsequent cycles. Configure via `SystemSettings.MAX_POLL_RUNTIME_SECONDS` (range 30-1800s).

Collector check behavior:
- Reachable and unreachable OLTs are checked on the base `--collector-check-seconds` cadence (no runtime backoff delay).
- `--collector-check-max-backoff-seconds` is still accepted for compatibility, but current runtime cadence does not apply exponential backoff.
- Reachability is sentinel-only: Varuna uses `varunaSnmpAvailability` (or vendor `zabbix.availability_item_key`) as the single source of truth.
- Sentinel must be present, enabled, supported, and fresh (`ZABBIX_AVAILABILITY_STALE_SECONDS`).
- On stale sentinel clock, Varuna may force one immediate item execution (`task.create`) and re-check once before declaring unreachable.
- Manual/scoped refresh with `--refresh-upstream` first waits for post-refresh clocks, but can accept pre-refresh clocks that are still inside freshness policy; `503` is reserved for collector unreachability or fully stale/empty status reads.
- Scheduler logs include collector summary lines (`checked`, `skipped_not_due`, `reachable`, `unreachable`, elapsed) for tuning verification.

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
