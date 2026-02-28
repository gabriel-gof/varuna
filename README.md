# Varuna

Varuna is a topology-first FTTH monitoring platform for multi-vendor OLT environments.

## What It Does
- Discovers and maintains OLT → Slot → PON → ONU topology.
- Polls ONU status over SNMP with disconnect-reason mapping.
- Shows unreachable OLTs clearly (gray state in frontend).
- Caches hot status/power reads in Redis.

## Runtime Architecture
- `frontend`: React app (Vite dev / Nginx prod)
- `backend`: Django + DRF API and SNMP orchestration
- `db`: PostgreSQL
- `redis`: Redis

## Versioning
- Release version is tracked in the root `VERSION` file.
- Frontend footer version label is built from `frontend/package.json` (`__APP_VERSION__`).

Manual maintenance actions (discovery, polling, power refresh) are queued as persistent backend jobs with progress tracking (`MaintenanceJob`), so long-running operations are observable and resilient to transient API process restarts.
Backend scheduler (`run_scheduler`) is started at container boot when `ENABLE_SCHEDULER=1` (enabled by default in current dev/prod env templates), keeping discovery/polling/power collection backend-driven.
Topology-heavy API reads are served through short-lived Redis response cache to reduce initial topology load latency.

## Multi-Instance Production
- Current codebase is single-tenant at application level (no tenant isolation in backend models/API).
- Multi-client hosting is stack-level isolation: one `frontend + backend + db + redis` stack per client.
- `docker-compose.prod.yml` now supports per-instance overrides through env vars:
  - `VARUNA_ENV_FILE` (container env file used by `db` and `backend`)
  - `VARUNA_FRONTEND_BIND_IP`
  - `VARUNA_FRONTEND_HTTP_HOST_PORT`
  - `VARUNA_BACKEND_BIND_IP`
  - `VARUNA_BACKEND_HOST_PORT`
  - `VARUNA_TLS_CERTS_DIR`
- In production compose, backend runs in internal HTTP mode (`BACKEND_BEHIND_FRONTEND_PROXY=1`) so frontend proxying `/api` does not hit backend HTTPS redirects.
- Frontend Nginx preserves upstream `X-Forwarded-Proto` when proxying `/api`, and Django trusts it in production for secure redirect/cookie behavior behind host TLS termination.
- Production backend serves with Gunicorn (container command) and frontend serves Django `/static` from a shared volume.
- Always use a unique Compose project name (`-p varuna_<client>`) per instance.

Example (same host, second production instance):
```bash
cd /Users/gabriel/Documents/varuna
cp docker/prod.env docker/prod.client-b.env
# Edit docker/prod.client-b.env:
# - VARUNA_ENV_FILE=docker/prod.client-b.env
# - unique ports (for example 18080/18081)
# - unique DB credentials/name
# - domain/host settings (ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, SERVER_NAME, SERVER_ALIASES)

docker compose -p varuna_client_b --env-file docker/prod.client-b.env -f docker-compose.prod.yml up -d --build
```

For `gabisat`, a ready instance template is included at `docker/prod.gabisat.env`.
Production recommendation: keep real client secrets in root-owned files outside repo, for example `/etc/varuna/prod.<client>.env`.

Use one host-level reverse proxy/load balancer to route subdomains to each instance's localhost frontend host port, exposing only `443` publicly.

## Naming
- Database (PostgreSQL) can be `varuna_dev` / `varuna_prod` (configured by `POSTGRES_DB`).
- Backend monitoring domain app is `topology` (no `dashboard` backend app/module).

## Quick Start
### Backend
```bash
cd /Users/gabriel/Documents/varuna
backend/venv/bin/python backend/manage.py migrate
backend/venv/bin/python backend/manage.py runserver 0.0.0.0:8000
```

### Frontend
```bash
cd /Users/gabriel/Documents/varuna/frontend
npm install
npm run dev
```

### Docker (Dev)
```bash
cd /Users/gabriel/Documents/varuna
docker compose -f docker-compose.dev.yml up -d --build
```

Dev URLs:
- Frontend: http://localhost:4000
- Backend API: http://localhost:8000/api/

## Migration Reset (Hard Refactor)
Backend migrations were reset to a clean `topology` app history.

If you had an older local DB created with legacy migration labels, reset local DB state before running:
```bash
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d --build
```

## Authentication Bootstrap
Create the initial admin user (Docker):
```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py ensure_auth_user --username admin --password changeme --role admin --superuser
```

Local:
```bash
backend/venv/bin/python backend/manage.py ensure_auth_user --username admin --password changeme --role admin --superuser
```

The command also reads `VARUNA_AUTH_USERNAME`, `VARUNA_AUTH_PASSWORD`, `VARUNA_AUTH_ROLE` environment variables as defaults. Use `--force-password` to update an existing user's password.

Roles: `admin` (full access), `operator` (full access), `viewer` (read-only, no settings changes or maintenance actions).

## Main API Endpoints
- `GET /api/olts/`
- `GET /api/olts/?include_topology=true`
- `GET /api/olts/{id}/topology/`
- `POST /api/olts/{id}/run_discovery/`
- `POST /api/olts/{id}/run_polling/`
- `POST /api/olts/{id}/snmp_check/`
- `POST /api/olts/{id}/refresh_power/`
- `GET /api/olts/{id}/maintenance_status/`
- `POST /api/olts/refresh_power/`
- `GET /api/onu/`
- `GET /api/onu/{id}/power/`
- `POST /api/onu/batch-power/`
- `POST /api/auth/login/`
- `POST /api/auth/logout/`
- `GET /api/auth/me/`
- `POST /api/auth/change-password/`

## Documentation
- `docs/ARCHITECTURE.md`
- `docs/BACKEND.md`
- `docs/FRONTEND.md`
- `docs/OPERATIONS.md`
- `docs/LLM_CONTEXT.md`

## Validation
```bash
backend/venv/bin/python backend/manage.py test topology -v 2
cd /Users/gabriel/Documents/varuna/frontend && npm run build
```
