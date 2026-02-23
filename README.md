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

## Naming
- Database (PostgreSQL) can be `varuna_dev` / `varuna_prod` (configured by `POSTGRES_DB`).
- Backend monitoring domain app is `topology` (no `dashboard` backend app/module).

## Quick Start
### Backend
```bash
cd /home/gabriel/varuna
backend/venv/bin/python backend/manage.py migrate
backend/venv/bin/python backend/manage.py runserver 0.0.0.0:8000
```

### Frontend
```bash
cd /home/gabriel/varuna/frontend
npm install
npm run dev
```

### Docker (Dev)
```bash
cd /home/gabriel/varuna
docker compose -f docker-compose.dev.yml up -d --build
```

Dev URLs:
- Frontend: http://localhost:4000
- Backend API: http://localhost:8000/api/

## Authentication Bootstrap
API access requires authentication by default. Create users before opening the frontend:

```bash
cd /home/gabriel/varuna/backend
. .venv/bin/activate
python manage.py ensure_auth_user --username gabriel --password 'CHANGE-THIS' --role admin --superuser
python manage.py ensure_auth_user --username gabisat --password 'CHANGE-THIS-READER' --role viewer
```

Role behavior:
- `admin` / `operator`: can access Topology + Settings tabs and execute maintenance/configuration actions.
- `viewer`: topology read-only; no Settings tab and no API permission for maintenance/configuration actions.

Rotate password later:

```bash
python manage.py ensure_auth_user --username gabriel --password 'NEW-STRONG-PASSWORD' --role admin --force-password
```

Disable old default admin account (recommended):

```bash
python manage.py shell -c "from django.contrib.auth.models import User; User.objects.filter(username='admin').update(is_active=False)"
```

## Background Collection
Status/discovery collection is backend-scheduled and must not depend on active UI sessions.

In production, host timers should run:
- `manage.py poll_onu_status`
- `manage.py discover_onus`

Commands are due-aware by OLT interval (`next_poll_at`/`next_discovery_at`) when executed without `--force`.

## Migration Reset (Hard Refactor)
Backend migrations were reset to a clean `topology` app history.

If you had an older local DB created with legacy migration labels, reset local DB state before running:
```bash
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d --build
```

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

## Documentation
- `docs/ARCHITECTURE.md`
- `docs/BACKEND.md`
- `docs/FRONTEND.md`
- `docs/OPERATIONS.md`
- `docs/LLM_CONTEXT.md`

## Validation
```bash
backend/venv/bin/python backend/manage.py test topology -v 2
cd /home/gabriel/varuna/frontend && npm run build
```
