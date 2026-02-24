# Varuna Architecture

## Goals
- Monitor many OLTs from multiple vendors using one coherent topology-first UX.
- Keep backend logic vendor-extensible and failure-tolerant.
- Preserve a simple runtime footprint: `frontend`, `backend`, `db`, `redis`.
- Keep tenant isolation operationally simple by scaling with stack-level boundaries.

## Runtime Services
- `frontend`: React + Vite (dev) or Nginx static app (prod).
- `backend`: Django + DRF API, discovery/polling orchestration, built-in scheduler (`run_scheduler`).
  - Production compose runs backend with Gunicorn on internal port `80`.
- `db`: PostgreSQL source of truth.
- `redis`: low-latency status/power cache.

Backend SNMP runtime:
- Uses `puresnmp` as the SNMP transport client.
- Discovery prefers SNMP bulk-walk (`v2c`) and falls back to regular walk for `v1`.

Container/runtime health:
- Backend exposes a public liveness endpoint at `GET /api/healthz/` (`{"status":"ok"}`).
- Compose and image healthchecks should probe `/api/healthz/` instead of authenticated API roots to avoid false `unhealthy` states.
- Production ingress assumes host TLS termination and forwarded-proto propagation (`X-Forwarded-Proto`) from host proxy -> frontend Nginx -> backend Django.

## Tenancy and Isolation Strategy
- Current backend/API/data model are single-tenant at application level.
- Multi-client deployment direction is stack-per-client isolation, not shared-db multitenancy.
- Isolation boundary for each client:
  - dedicated `frontend`, `backend`, `db`, `redis` containers
  - dedicated Compose project namespace (`docker compose -p ...`)
  - dedicated DB/Redis credentials and persistent volumes

### Multi-Instance on One Host
- Running multiple Varuna instances on one machine is supported when each stack uses:
  - unique project name,
  - unique host port bindings (typically localhost-only bind),
  - isolated env vars/secrets.
- `docker-compose.prod.yml` is instance-parameterized via:
  - `VARUNA_ENV_FILE` (env file injected into `db` and `backend`),
  - `VARUNA_FRONTEND_BIND_IP`,
  - `VARUNA_FRONTEND_HTTP_HOST_PORT`,
  - `VARUNA_BACKEND_BIND_IP`,
  - `VARUNA_BACKEND_HOST_PORT`,
  - `VARUNA_TLS_CERTS_DIR`.
- Production compose pins backend to internal API proxy mode with `BACKEND_BEHIND_FRONTEND_PROXY=1` so frontend `/api` proxying stays HTTP inside the stack and avoids backend HTTPS redirect loops.
- Frontend serves Django static assets from a shared `static` volume (`/var/www/static`) rather than proxying static requests back to backend.
- Resource limits are per-instance tunable via env (`VARUNA_DB_LIMIT_*`, `VARUNA_REDIS_LIMIT_*`, `VARUNA_BACKEND_LIMIT_*`, `VARUNA_FRONTEND_LIMIT_*`).
- Each instance should run with both:
  - a dedicated compose project namespace (`docker compose -p varuna_<client> ...`),
  - a dedicated env file passed with `--env-file` (and `VARUNA_ENV_FILE` pointing to that same file).
- Typical shared infrastructure components are:
  - reverse proxy (host-level ingress),
  - host monitoring/log shipping.
- PostgreSQL and Redis should stay private to each stack network in production.

### Dev Port Mapping
- Frontend (Vite): `http://localhost:4000`
- Backend API: `http://localhost:8000/api/`

## Naming Conventions
- PostgreSQL database names are environment-specific `varuna_*` (`POSTGRES_DB`).
- Backend monitoring domain is the Django app `topology`.
- The legacy backend label/name `dashboard` is not part of the active architecture.

## Why No Separate SNMP Container (Now)
A dedicated SNMP worker container is not required for the current scale (5-6 OLTs, multiple cards/PONs) because:
- SNMP discovery/polling are already isolated by management commands.
- Logs are explicit and can be filtered by command context.
- Operational complexity stays low with one backend service.

In multi-client deployments this remains true per client stack by default. Dedicated worker containers are still an optional scale optimization when one client's OLT volume requires it.

When to split into dedicated `discovery` and `poller` workers:
- command runtimes start overlapping significantly,
- API latency degrades during polling windows,
- or you need independent autoscaling by job type.

## Data Model Highlights
- `VendorProfile`: vendor/model OID templates and capabilities. Seeded profiles: ZTE C300, VSOL LIKE GPON 8P, Huawei MA5680T.
- `OLT`: SNMP credentials, scheduler intervals, and runtime SNMP reachability state.
- `OLTSlot` and `OLTPON`: discovered topology map.
- `ONU`: per-OLT endpoint with active/inactive lifecycle and status.
- `ONULog`: offline event history and disconnect reasons.
- `MaintenanceJob`: persistent OLT-scoped maintenance queue/progress state for manual discovery/polling/power actions.

## Key Backend Flows
### 1. Discovery (`discover_onus`)
- Reads vendor OID templates.
- Discovers ONUs and topology links.
- Upserts ONUs as active.
- Applies Zabbix-style lost-resource lifecycle:
  - missing resources stay active during `disable_lost_after_minutes` grace,
  - then become inactive,
  - and can be hard-deleted after `delete_lost_after_minutes` (optional).
- Updates OLT discovery health and SNMP reachability.

### 2. Polling (`poll_onu_status`)
- Polls status OIDs in chunks.
- Maps vendor status codes to canonical status/reason.
- Optional second-pass: when `disconnect_reason_oid` is configured (e.g. Huawei), fetches disconnect cause only for offline ONUs and maps it via `disconnect_reason_map`.
- Tracks online/offline transitions with `ONULog`.
- Marks missing statuses as `unknown` without generating false offline alarms.
- Marks OLT unreachable when no SNMP status data is returned.

## Unreachable OLT Behavior
- Backend persists SNMP availability (`snmp_reachable`, `last_snmp_check_at`, `snmp_failure_count`, `last_snmp_error`).
- The backend scheduler runs SNMP reachability checks **before** dispatching any collection jobs (every 180s by default). This SNMP-first design prevents wasted time and log noise from unreachable OLTs.
- SNMP reachability checks are due-aware per OLT and use adaptive backoff for repeatedly unreachable OLTs (capped by scheduler config), reducing redundant timeout probes when fleets grow.
- Polling, discovery, and power collection skip OLTs with `snmp_reachable=False` and `snmp_failure_count >= 2`.
- Frontend derives OLT health from backend fields (`snmp_reachable`, `snmp_failure_count >= 2`) and renders unreachable OLT nodes as gray.
- ONU state is preserved during hard SNMP outages to avoid false state corruption.

## Manual Maintenance Queue
- Manual settings actions (`run_discovery`, `run_polling`, `refresh_power` with `background=true`) enqueue `MaintenanceJob` rows in PostgreSQL.
- Queue is serialized per OLT (single active job across discovery/polling/power) to prevent concurrent SNMP bursts against the same OLT.
- A backend in-process runner claims queued jobs with row locking and updates progress/status (`queued -> running -> completed|failed|canceled`).
- Frontend polls `GET /api/olts/{id}/maintenance_status/` for durable progress, so in-flight visibility does not depend on in-memory API view state.
- `snmp_check` "busy" behavior now derives from active `MaintenanceJob` rows, avoiding false unreachable markings while maintenance is running.

## Role-Based Access Control
- Users have roles (`admin`, `operator`, `viewer`) via `UserProfile.role`.
- `admin`/`operator` have full read/write access; `viewer` is read-only (no settings changes, no maintenance actions, no power refresh).
- Role resolution: superuser → admin; profile role if valid; fallback → viewer.
- Permission enforcement at API level via `can_modify_settings()` checks; `VendorProfileViewSet` is read-only for all users.
- Frontend hides settings tab and action buttons for viewers via `canManageSettings` derived state.

## Background Collection Scheduling
- The `run_scheduler` management command is the primary scheduler. It runs as a long-lived background process alongside the Django server and dispatches polling, discovery, power collection, and SNMP reachability checks on configurable tick intervals.
- Discovery and polling commands support due-awareness: they skip OLTs that are not yet due based on `next_*_at` timestamps or computed intervals.
- Discovery and polling commands can be capped per run (`--max-olts`) so large fleets can be processed in controlled batches (oldest due first).
- Scheduler supports per-tick OLT caps (`max-poll`, `max-discovery`, `max-power`) for load shaping during high-scale deployments.
- `--force` flag bypasses due checks for manual/emergency runs.
- Polling command enforces a runtime budget (`max_runtime_seconds`, default 180s) to prevent long-running jobs.
- Power service pre-fetches cached values, skips cache writes for empty reads, and retains cached snapshots when forced refresh fails.

## Performance Decisions
- Removed global `ONU.snmp_index` uniqueness in favor of per-OLT uniqueness (`(olt, snmp_index)`), enabling multi-vendor/multi-OLT scale.
- Active-only topology/status queries to avoid stale-record inflation.
- `poll_onu_status` refactored to avoid per-ONU log queries.
- Redis invalidation switched from `KEYS` to `SCAN` pattern deletion.
- Topology serializers now use annotated counts where available.
- Frontend enforces stale-data gray state using per-OLT `polling_interval_seconds` and synchronizes health colors between topology and settings views.
- Stale tolerance enforces a 10-minute minimum window so short polling intervals don't cause premature gray state.
