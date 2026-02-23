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

## Multi-Client Direction (Future-Ready)
- Current codebase is single-tenant at application level (no tenant isolation in backend models/API).
- Current dev default is still one local stack from `docker-compose.dev.yml`; this section defines expansion direction.
- Recommended growth path is one isolated Varuna stack per client:
  - `frontend + backend + db + redis` per client
  - isolated Docker Compose project name per client (`-p`)
  - isolated database/Redis data and credentials per client
- Multiple stacks can run on the same host when host port bindings are unique per stack.
- In production, place one reverse proxy in front and route by subdomain (`client-a.varuna.example`, `client-b.varuna.example`).
- Keep PostgreSQL/Redis internal-only for each stack (no public exposure unless explicitly required).

Example (same machine, two client stacks):
```bash
docker compose -p varuna_client_a -f docker-compose.dev.yml up -d --build
docker compose -p varuna_client_b -f docker-compose.dev.yml -f docker-compose.client-b.dev.yml up -d --build
```

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
