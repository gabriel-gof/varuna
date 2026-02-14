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

Production:
```bash
cd /Users/gabriel/Documents/varuna
docker compose -f docker-compose.prod.yml up -d --build
```

If service names appear stale in Docker UI, recreate stack:
```bash
docker compose -f docker-compose.dev.yml down
docker compose -f docker-compose.dev.yml up -d --build --force-recreate
```

## Common Recovery Steps
Topology endpoint fails with DB column errors (example: missing `snmp_reachable` or `is_active`):
```bash
cd /Users/gabriel/Documents/varuna
backend/venv/bin/python backend/manage.py migrate
```

Recent schema additions include OLT interval fields such as `power_interval_seconds`, so migrations are mandatory before opening topology/settings.

If running with Docker, also recreate the stack so containers pick up new code and schema:
```bash
docker compose -f docker-compose.dev.yml down
docker compose -f docker-compose.dev.yml up -d --build --force-recreate
```

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

## Validation
Backend tests:
```bash
backend/venv/bin/python backend/manage.py test dashboard -v 2
```

Frontend build check:
```bash
cd /Users/gabriel/Documents/varuna/frontend
npm run build
```
