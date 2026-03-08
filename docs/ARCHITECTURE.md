# Varuna Architecture

## Goals
- Monitor many OLTs from multiple vendors using one coherent topology-first UX.
- Keep backend logic vendor-extensible and failure-tolerant.
- Preserve a simple runtime footprint: `frontend`, `backend`, `varuna-db`, `redis` (plus optional Zabbix services when enabled).
- Keep tenant isolation operationally simple by scaling with stack-level boundaries.

## Runtime Services
- `frontend`: React + Vite (dev) or Nginx static app (prod).
  - frontend UI version labels are injected via `__APP_VERSION__`, sourced from root `VERSION` (dev/prod parity).
  - in dev compose, root `VERSION` is mounted to frontend container at `/app/VERSION`.
- `backend`: Django + DRF API, discovery/polling orchestration, built-in scheduler (`run_scheduler`).
  - Production compose runs backend with Gunicorn on internal port `80`.
  - Scheduler startup is controlled by `ENABLE_SCHEDULER=1` at backend container boot.
  - Optional auth bootstrap at container boot: when `VARUNA_AUTH_BOOTSTRAP=1`, backend entrypoint runs `ensure_auth_user` using `VARUNA_AUTH_*` envs before serving traffic.
- `varuna-db`: PostgreSQL source of truth.
- `redis`: infrastructure service kept in the default stack; used for per-OLT topology structure cache only. Live status, scoped power reads, Power Report, and Alarm History do not depend on Redis read-path caching.
- Optional collector stack (enabled in current dev compose):
  - `zabbix-db`
  - `zabbix-server`
  - `zabbix-web`
  - `zabbix-hardening` (one-shot post-start policy enforcer for Zabbix users)
  - `zabbix-agent` (agent2 sidecar for Zabbix server self-monitoring)
  - slow-OLT tolerance is configured via `ZBX_TIMEOUT` (current default: `10`).
  - unreachable/recovery convergence is tuned via:
    - `ZBX_UNREACHABLEDELAY` (default `5`)
    - `ZBX_UNAVAILABLEDELAY` (default `15`)
    - `ZBX_UNREACHABLEPERIOD` (default `30`)
  - high-volume defaults are pre-tuned in compose/env (baseline: 4 CPU / 16 GB / SSD):
    - Zabbix server worker/cache knobs (`ZBX_STARTPOLLERS=30`, `ZBX_STARTSNMPPOLLERS=80`, `ZBX_STARTPREPROCESSORS=12`, `ZBX_STARTLLDPROCESSORS=4`, `ZBX_CACHESIZE=1G`);
    - configuration cache uses Zabbix server native env `ZBX_CACHESIZE` (not `ZBX_CACHE_SIZE`);
    - Zabbix PostgreSQL knobs (`ZBX_PG_*`) applied at container start (`shared_buffers=2GB`, `synchronous_commit=off`, `wal_buffers=64MB`, `checkpoint_timeout=15min`, `effective_io_concurrency=200`, WAL/checkpoint settings).

Backend collection runtime:
- Default path is Zabbix API item keys.
- Exception: `FIT / FNCS4000` uses a direct backend Telnet collector because the device does not expose an equivalent Zabbix/SNMP runtime path for the required topology/status/power workflow.

Container/runtime health:
- Backend exposes a public liveness endpoint at `GET /api/healthz/` (`{"status":"ok"}`).
- Compose and image healthchecks should probe `/api/healthz/` instead of authenticated API roots to avoid false `unhealthy` states.
- Production ingress assumes host TLS termination and forwarded-proto propagation (`X-Forwarded-Proto`) from host proxy -> frontend Nginx -> backend Django.

## Tenancy and Isolation Strategy
- Current backend/API/data model are single-tenant at application level.
- Multi-client deployment direction is stack-per-client application isolation, not shared-db tables/multitenancy.
- Isolation boundary for each client:
  - dedicated `frontend`, `backend`, `redis` containers
  - dedicated Compose project namespace (`docker compose -p ...`)
  - dedicated Varuna logical database (`POSTGRES_DB`) and credentials
  - dedicated Redis credentials/namespace (or dedicated Redis container, per operator policy)
  - dedicated Zabbix host-group namespace (`ZABBIX_HOST_GROUP_NAME`, title case, for example `Varuna/Gabisat`, `Varuna/Vianet`, `Varuna/Pontal`)
- Recommended shared infrastructure on one VM:
  - shared `pg-varuna` PostgreSQL container for all Varuna logical DBs,
  - separate shared `pg-zabbix` PostgreSQL container for Zabbix only,
  - shared `zabbix-server` + `zabbix-web`,
  - shared `zabbix-agent` (agent2) for Zabbix server self-monitoring,
  - shared `zabbix-hardening` one-shot service that enforces runtime user policy on every infra `compose up`.
- Zabbix auth boundary:
  - Varuna integration should use a dedicated API user/token (`varuna_api`),
  - human debugging should use a separate operator/admin user,
  - never couple personal admin credentials to runtime integration envs.
- Shared infra user-hardening policy:
  - ensure operator user (`gabriel`) and integration user (`varuna`) exist,
  - remove bootstrap default user (`Admin`) after those users are ensured.
- Standalone mode (per-stack local `varuna-db`) is still supported for simpler single-instance deployments.

### Multi-Instance on One Host
- Running multiple Varuna instances on one machine is supported when each stack uses:
  - unique project name,
  - unique host port bindings (typically localhost-only bind),
  - isolated env vars/secrets.
- Current gabisat production hostname is `varuna.gabisat.com.br` (single canonical host, no secondary alias).
- Current demo instance hostname is `demo.varuna.network` (ports 18100/18101, project `varuna_demo`).
- `docker-compose.prod.yml` is instance-parameterized via:
  - `VARUNA_ENV_FILE` (env file injected into `backend` and standalone `varuna-db`),
  - `VARUNA_FRONTEND_BIND_IP`,
  - `VARUNA_FRONTEND_HTTP_HOST_PORT`,
  - `VARUNA_BACKEND_BIND_IP`,
  - `VARUNA_BACKEND_HOST_PORT`,
  - `VARUNA_POSTGRES_HOST`,
  - `VARUNA_TLS_CERTS_DIR`.
- Shared-infra compose files:
  - `docker-compose.infra.shared.yml`: shared `pg-varuna`, `pg-zabbix`, `zabbix-server`, `zabbix-web`.
  - `docker-compose.prod.shared-pg.yml`: per-client app stack that connects backend to shared `pg-varuna`.
- Production compose pins backend to internal API proxy mode with `BACKEND_BEHIND_FRONTEND_PROXY=1` so frontend `/api` proxying stays HTTP inside the stack and avoids backend HTTPS redirect loops.
- Frontend serves Django static assets from a shared `static` volume (`/var/www/static`) rather than proxying static requests back to backend.
- Resource limits are per-instance tunable via env (`VARUNA_DB_LIMIT_*`, `VARUNA_REDIS_LIMIT_*`, `VARUNA_BACKEND_LIMIT_*`, `VARUNA_FRONTEND_LIMIT_*`).
- Each instance should run with both:
  - a dedicated compose project namespace (`docker compose -p varuna_<client> ...`),
  - a dedicated env file passed with `--env-file` (and `VARUNA_ENV_FILE` pointing to that same file).
- Typical shared infrastructure components are:
  - reverse proxy (host-level ingress),
  - shared role-based databases (`pg-varuna`, `pg-zabbix`) and Zabbix services,
  - host monitoring/log shipping.
- In shared mode, backend joins shared `varuna-data` network for database/Zabbix access; public traffic remains only through frontend ingress.

### Dev Port Mapping
- Frontend (Vite): `http://localhost:4000`
- Backend API: `http://localhost:8000/api/`

## Naming Conventions
- PostgreSQL database names are environment-specific `varuna_*` (`POSTGRES_DB`).
- Backend monitoring domain is the Django app `topology`.
- The legacy backend label/name `dashboard` is not part of the active architecture.

## Why No Separate Collection Worker Container (Now)
A dedicated collection worker container is not required for the current scale because:
- discovery/polling/power are already isolated by management commands,
- logs are explicit and can be filtered by command context,
- operational complexity stays low with one backend service.

In multi-client deployments this remains true per client stack by default. Dedicated workers are still an optional scale optimization when one client's OLT volume requires it.

When to split into dedicated `discovery` and `poller` workers:
- command runtimes start overlapping significantly,
- API latency degrades during polling windows,
- or you need independent autoscaling by job type.

## Data Model Highlights
- `VendorProfile`: vendor/model OID templates and capabilities. Seeded profiles: ZTE C300, ZTE C600, VSOL LIKE GPON 8P, Huawei UNIFICADO, Fiberhome UNIFICADO, FIT FNCS4000.
- `OLT`: scheduler intervals and runtime reachability state fields (`collector_*` canonically, with `snmp_*` API aliases kept for compatibility). `blade_ips` JSONField supports multi-blade chassis (e.g. FIT FNCS4000 with 3 blades); `get_blade_ips()` returns the list or falls back to `[ip_address]`.
- `OLTSlot` and `OLTPON`: discovered topology map. Empty PONs (0 active ONUs) and empty slots are excluded from topology structure payloads.
- `ONU`: per-OLT endpoint with active/inactive lifecycle and status.
- `ONULog`: offline event history and disconnect reasons.
- `ONUPowerSample`: persisted ONU power snapshots used by Power Report and Alarm History trend APIs.
- `MaintenanceJob`: persistent OLT-scoped maintenance queue/progress state for manual discovery/polling/power actions.

## Key Backend Flows
### 1. Discovery (`discover_onus`)
- Reads vendor templates from `VendorProfile.oid_templates`.
- Uses the configured collector:
  - Zabbix vendors read `oid_templates.zabbix.discovery_item_key`.
  - FIT `FNCS4000` reads configured EPON interfaces (`0/1..0/4` by default) over Telnet with `show onu info epon 0/x all`. Multi-blade chassis iterate over `blade_ips`; only **authorized** ONUs are kept, so only blades/PONs that return authorized ONUs are materialized in topology.
- Manual discovery can request immediate upstream execution before read (`--refresh-upstream`).
- Discovers ONUs and topology links.
- Upserts ONUs as active.
- Applies immediate missing-resource deactivation policy:
  - `deactivate_missing=true`;
  - `disable_lost_after_minutes=0` (missing ONUs/PONs/slots leave active topology immediately);
  - optional hard delete after `delete_lost_after_minutes` (default retention: 7 days).
- Updates OLT discovery health and collector reachability.

### 2. Polling (`poll_onu_status`)
- Polls ONU status via the configured collector:
  - Zabbix vendors use per-ONU status/reason item keys (`oid_templates.zabbix.status_item_key_pattern` / `reason_item_key_pattern`).
  - FIT `FNCS4000` reads `show onu info epon 0/x all` per blade and maps `Up -> online`, `Down -> offline/unknown`. Parser accepts field variants both with and without the `Uptime` column because firmware output differs across blades.
- Manual/scoped polling can request immediate upstream execution before read (`--refresh-upstream`).
- Maps source values to canonical status/reason.
- Tracks online/offline transitions with `ONULog`.
- Marks missing statuses as `unknown` without generating false offline alarms.
- Marks OLT unreachable on collector transport failure; an empty FIT status snapshot after successful Telnet login is treated as a polling failure without flipping collector reachability to false.

### 3. OLT <-> Zabbix Runtime Sync
- On OLT create/update, Varuna synchronizes Zabbix host runtime (group, tags, interface macro refs, macro values).
- Host naming can be namespaced per Varuna instance with `ZABBIX_HOST_NAME_PREFIX` (`<prefix><OLT.name>`) to avoid collisions on shared Zabbix servers.
- If host is missing in Zabbix, Varuna auto-creates it and links the vendor template plus shared `Varuna SNMP Availability` template when available.
- On Varuna OLT delete, backend attempts Zabbix `host.delete` for the resolved host.

## Unreachable OLT Behavior
- Backend persists collector availability (`collector_reachable`, `last_collector_check_at`, `collector_failure_count`, `last_collector_error`).
- The backend scheduler runs reachability checks **before** dispatching any collection jobs (every `30s` by default via `COLLECTOR_CHECK_SECONDS`).
- Reachability checks are due-aware per OLT and run at fixed cadence (no runtime exponential backoff delay).
- Reachability source is collector-mode aware:
  - `zabbix`: host/interface availability plus sentinel/status freshness.
  - interface backoff signals (`errors_from`, `disable_until`) are treated as early unreachable signals before `available=2` convergence.
  - if template key `varunaSnmpAvailability` exists (`zabbix.availability_item_key`), its freshness is validated first for fast gray/green transitions.
- Polling, discovery, and power collection skip OLTs with `collector_reachable=False` and `collector_failure_count >= 2`.
- Frontend derives OLT health from backend reachability + freshness (`collector_reachable` with legacy `snmp_reachable` fallback and `last_poll_at` stale window) and renders OLT gray immediately when reachability is `false`.
- ONU state is preserved during hard collector outages to avoid false state corruption.

## Manual Maintenance Queue
- Manual settings actions (`run_discovery`, `run_polling`, `refresh_power` with `background=true`) enqueue `MaintenanceJob` rows in PostgreSQL.
- Queue is serialized per OLT (single active job across discovery/polling/power) to prevent concurrent collection bursts against the same OLT.
- A backend in-process runner claims queued jobs with row locking and updates progress/status (`queued -> running -> completed|failed|canceled`).
- Discovery/polling queue workers execute commands with hard runtime timeouts; stale `running` jobs beyond timeout are auto-failed to unblock the OLT queue.
- Frontend polls `GET /api/olts/{id}/maintenance_status/` for durable progress, so in-flight visibility does not depend on in-memory API view state.
- `collector_check` is the canonical API action name; legacy `snmp_check` remains as a compatibility alias and both dispatch through the collector-aware implementation.

## Role-Based Access Control
- Users have roles (`admin`, `operator`, `viewer`) via `UserProfile.role`.
- `admin` has full read/write access including settings, OLT management, and maintenance actions.
- `operator` cannot access settings, but can edit PON descriptions and trigger scoped live status/power refresh from topology view.
- `viewer` is read-only (no settings, no PON description edits, no live refresh).
- Role resolution: superuser → admin; profile role if valid; fallback → viewer.
- Permission enforcement at API level splits between `can_modify_settings()` (admin-only settings/OLT maintenance) and `can_operate_topology()` (admin/operator PON description edits and live topology refresh); `VendorProfileViewSet` is read-only for all users.
- Frontend hides settings tab for non-admin users via `canManageSettings` and gates PON sidebar edit/refresh controls via `canOperateTopology`.

## Background Collection Scheduling
- The `run_scheduler` management command is the primary scheduler. It runs as a long-lived background process alongside the Django server and dispatches polling, discovery, power collection, reachability checks, and periodic history pruning on configurable tick intervals.
- Discovery and polling commands support due-awareness: they skip OLTs that are not yet due based on `next_*_at` timestamps or computed intervals.
- Discovery and polling commands can be capped per run (`--max-olts`) so large fleets can be processed in controlled batches (oldest due first).
- Scheduler supports per-tick OLT caps (`max-poll`, `max-discovery`, `max-power`) for load shaping during high-scale deployments.
- `--force` flag bypasses due checks for manual/emergency runs.
- Polling command enforces a runtime budget (`max_runtime_seconds`, default 180s) to prevent long-running jobs.
- Power service persists fresh samples to PostgreSQL for topology/report/history surfaces.
- FIT `FNCS4000` power collection is constrained by the device CLI:
  - only online ONUs are queried;
  - only ONU IDs `<= 64` support `show onu optical-ddm`;
  - only ONU RX is collected; OLT RX is unsupported.

## Performance Decisions
- Removed global `ONU.snmp_index` uniqueness in favor of per-OLT uniqueness (`(olt, snmp_index)`), enabling multi-vendor/multi-OLT scale.
- Active-only topology/status queries to avoid stale-record inflation.
- `poll_onu_status` refactored to avoid per-ONU log queries.
- Redis invalidation switched from `KEYS` to `SCAN` pattern deletion.
- Topology serializers now use annotated counts where available.
- Topology-heavy API reads use a hybrid model:
  - `GET /api/olts/` is a direct PostgreSQL read and reuses denormalized `cached_*` counters for OLT summaries.
  - `GET /api/olts/?include_topology=true` and `GET /api/olts/{id}/topology/` reuse per-OLT Redis structure cache entries for static inventory only, then overlay live `ONU` + active `ONULog` data from PostgreSQL.
  - full-tree power is intentionally not loaded during topology reads; scoped power endpoints read persisted `ONUPowerSample` snapshots on demand.
- Frontend enforces stale-data gray state using per-OLT `polling_interval_seconds` and synchronizes health colors between topology and settings views.
- Stale tolerance enforces a 10-minute minimum window so short polling intervals don't cause premature gray state.
