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
- `/Users/gabriel/Documents/varuna/docs/ARCHITECTURE.md`
- `/Users/gabriel/Documents/varuna/docs/BACKEND.md`
- `/Users/gabriel/Documents/varuna/docs/FRONTEND.md`
- `/Users/gabriel/Documents/varuna/docs/OPERATIONS.md`
- `/Users/gabriel/Documents/varuna/docs/LLM_CONTEXT.md`

## Validation
```bash
backend/venv/bin/python backend/manage.py test topology -v 2
cd /Users/gabriel/Documents/varuna/frontend && npm run build
```
