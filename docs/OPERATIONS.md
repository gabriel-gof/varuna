# Operations Guide

## Local Development
Backend:
```bash
cd /home/gabriel/varuna
backend/venv/bin/python backend/manage.py migrate
backend/venv/bin/python backend/manage.py runserver 0.0.0.0:8000
```

Frontend:
```bash
cd /home/gabriel/varuna/frontend
npm install
npm run dev
```

## Docker Compose
Development:
```bash
cd /home/gabriel/varuna
docker compose -f docker-compose.dev.yml up -d --build
```

Development service URLs:
- Frontend: `http://localhost:4000`
- Backend API: `http://localhost:8000/api/`

Production:
```bash
cd /home/gabriel/varuna
docker compose -f docker-compose.prod.yml up -d --build
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
cd /home/gabriel/varuna
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d --build
```

Local SQLite reset:
```bash
cd /home/gabriel/varuna
rm -f backend/db.sqlite3 backend/varuna_dev
backend/venv/bin/python backend/manage.py migrate
```

Topology endpoint fails with DB column errors (example: missing `snmp_reachable` or `is_active`):
```bash
cd /home/gabriel/varuna
backend/venv/bin/python backend/manage.py migrate
```

Recent schema additions include OLT interval fields such as `power_interval_seconds`, so migrations are mandatory before opening topology/settings.

If running with Docker, also recreate the stack so containers pick up new code and schema:
```bash
docker compose -f docker-compose.dev.yml down
docker compose -f docker-compose.dev.yml up -d --build --force-recreate
```

## Manual Jobs
Bootstrap auth user (required when API auth is enabled):
```bash
cd /home/gabriel/varuna/backend
. .venv/bin/activate
python manage.py ensure_auth_user --username gabriel --password 'CHANGE-THIS' --role admin --superuser
python manage.py ensure_auth_user --username gabisat --password 'CHANGE-THIS-READER' --role viewer
```

Rotate existing user password:
```bash
cd /home/gabriel/varuna/backend
. .venv/bin/activate
python manage.py ensure_auth_user --username gabriel --password 'NEW-STRONG-PASSWORD' --role admin --force-password
```

Environment-driven bootstrap (for automation):
```bash
VARUNA_AUTH_USERNAME=gabriel \
VARUNA_AUTH_PASSWORD='CHANGE-THIS' \
VARUNA_AUTH_ROLE=admin \
VARUNA_AUTH_SUPERUSER=1 \
python manage.py ensure_auth_user
```

Disable legacy default login name (recommended):
```bash
python manage.py shell -c "from django.contrib.auth.models import User; User.objects.filter(username='admin').update(is_active=False)"
```

## Backend Scheduler (No-Login Collection)
Status/discovery collection must run even when nobody is logged into the UI.

Production uses host timers that invoke management commands:
- `varuna-polling.timer` -> `manage.py poll_onu_status`
- `varuna-discovery.timer` -> `manage.py discover_onus`

Recommended checks:
```bash
systemctl status varuna-polling.timer --no-pager
systemctl status varuna-discovery.timer --no-pager
systemctl list-timers --all | grep -E 'varuna-(polling|discovery)'
```

## Security Verification
Confirm only intended public surface is exposed:
```bash
ss -ltn
```

Expected for Varuna stack:
- Public: `:80` and `:443` (Apache virtual hosts)
- Local-only: `127.0.0.1:18000` (Gunicorn), `127.0.0.1:5432` (PostgreSQL), `127.0.0.1:6379` (Redis)

Check API rejects unauthenticated access:
```bash
curl -i -sS https://varuna-gabisat.templa.tech/api/auth/me/
```

Expected response: `401 Unauthorized`.

Frontend cache safety:
- Varuna Apache vhost sets no-cache headers for HTML responses (`Cache-Control: no-store, no-cache`) so browsers always fetch the latest `index.html` and do not keep stale role/UI logic.

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
backend/venv/bin/python backend/manage.py test topology -v 2
```

Frontend build check:
```bash
cd /home/gabriel/varuna/frontend
npm run build
```
