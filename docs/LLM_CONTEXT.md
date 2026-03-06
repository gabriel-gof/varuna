# LLM Context Guide

## What Varuna Is
Varuna is an OLT/ONU monitoring platform focused on topology-first operational visibility, not dashboard-heavy analytics.

## Agent Ownership Split
- `Codex` is responsible for backend/infrastructure/runtime work (Django API, scheduler, Zabbix integration, compose/env, operations docs).
- `Opus` is responsible for frontend/UI/UX work (layout, navigation flow, search UX, responsive behavior, visual interactions).
- `Opus` must not change backend/infrastructure files (`backend/`, `docker*`, runtime env, migrations, backend APIs).
- If a request is primarily frontend behavior/design, route it to Opus and document backend impacts only when needed.

## Current Product Decisions
- No dashboard tab in current scope.
- Primary views: topology, power report, alarm history, settings. Settings is a dedicated nav tab (visible for admin/operator only).
- Per-tab search: Topology has inline search (client-side via `useUniversalSearch` hook), Power Report has text filter (filters rows by name/serial), Alarm History has API-based search (debounced `alarm-clients` endpoint). Each tab manages its own search state independently. Power Report → Topology drill-through uses `ponHighlightTarget` state in App.jsx.
- There is no global/universal search input in the navbar header; search is local to each tab.
- Unreachable OLTs must be visually gray.
- Backend domain app is `topology`; do not reintroduce backend `dashboard` naming.
- Backend is currently single-tenant at application level.
- Multi-client strategy is deployment-level isolation (one Varuna app stack per client), not shared-db tables/multitenancy.
- Recommended practical production topology on one VM:
  - shared infra stack with `pg-varuna` (all Varuna logical DBs), `pg-zabbix`, `zabbix-server`, `zabbix-web`,
  - per-client app stacks with `frontend` + `backend` + `redis`.
- Production Zabbix security rule: keep a dedicated API account (`varuna_api`) for Varuna and a separate personal admin/operator account for UI; never use default credentials.
- Role-based access: `admin` (full including settings/maintenance), `operator` (read-only topology and monitoring), `viewer` (read-only). Enforce via `can_modify_settings()` on backend, `canManageSettings` on frontend.
- PON descriptions are admin-managed metadata: editable by `admin`, read-only for `operator`/`viewer`, and must persist across discovery refreshes.
- Discovery, polling, power collection, and reachability checks are scheduled by the backend `run_scheduler` command. The frontend does not submit automatic maintenance; it relies on backend scheduling and provides manual trigger buttons.
- Backend now persists power history snapshots in `ONUPowerSample` and exposes report APIs for the new tabs:
  - `GET /api/onu/power-report/`
  - `GET /api/onu/alarm-clients/`
  - `GET /api/onu/{id}/alarm-history/`
- Alarm and power history retention is controlled by `prune_history` + settings (`POWER_HISTORY_RETENTION_DAYS`, `ALARM_HISTORY_RETENTION_DAYS`, `HISTORY_PRUNE_INTERVAL_SECONDS`).
- Backend container runtime starts scheduler automatically when `ENABLE_SCHEDULER=1` (enabled in current dev/prod env templates).
- Manual settings maintenance actions use a persistent `MaintenanceJob` queue (PostgreSQL), not volatile in-memory flags.
- Background discovery/polling jobs have runtime timeouts (`MAINTENANCE_*_TIMEOUT_SECONDS`) and stale running jobs are auto-failed to prevent permanent queue lock when collector/integration settings are wrong.
- Collection is Zabbix-only: discovery/status/power are read via Zabbix API item keys.
- Product version source-of-truth is root `VERSION`; frontend version labels are injected from this file through `__APP_VERSION__` (no hardcoded UI version text).
- Varuna owns Zabbix host runtime lifecycle for OLTs: create/update syncs host group/tags/interface macros, missing hosts are auto-created, and OLT delete attempts `host.delete`.
- Host group for managed OLT hosts is instance-configurable (`ZABBIX_HOST_GROUP_NAME`) so each Varuna instance can keep its own client namespace on a shared Zabbix server.
- Host names in Zabbix can be instance-prefixed with `ZABBIX_HOST_NAME_PREFIX` to avoid collisions across multiple Varuna instances sharing one Zabbix server.
- Zabbix host tags are lowercase; Huawei/Fiberhome `UNIFICADO` model is normalized to tag `model=unified` for English consistency.
- Vendor templates must define `oid_templates.zabbix` keys (`discovery_item_key`, status/reason key patterns, power key patterns).
- ONU item prototypes should carry `slot={#SLOT}` and `pon={#PON}` tags so operators can filter item data by slot/PON directly in Zabbix.
- Template-first hygiene is mandatory: serial/status/power normalization belongs to Zabbix template preprocessing. Frontend must not implement vendor-specific data repair.
- Manual/scoped refresh can request immediate Zabbix item execution; this is capped by `ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS` (default `512`) to avoid overload on global runs.
- Reachability is sentinel-only: shared template `Varuna SNMP Availability` provides `varunaSnmpAvailability` (sysName.0) at 30s cadence, and backend uses that item as the single source of truth for OLT SNMP availability.
- Sentinel checks are fail-closed: item must be present, enabled, supported, and fresh (`ZABBIX_AVAILABILITY_STALE_SECONDS`).
- If sentinel clock is stale, backend may force one immediate sentinel execution and re-check once to speed recovery after connectivity return.
- Frontend recovery rule is stricter than reachability: an OLT only leaves gray after fresh ONU status polling (`last_poll_at` inside stale window); sentinel reachability alone does not make OLT active/green.
- Scheduler reachability cadence default is `COLLECTOR_CHECK_SECONDS=30`.
- Upstream-forced refresh prefers post-refresh clocks, but recent pre-refresh clocks are accepted when still inside stale-age policy; backend returns `503` only for unreachability or fully stale/empty status payloads.
- Topology-heavy API reads (`/api/olts/`, `/api/olts/?include_topology=true`, `/api/olts/{id}/topology/`) use Redis response cache with short TTLs and runtime invalidation.
- Topology list/detail payloads expose SNMP health metadata used by frontend gray-state logic (`snmp_reachable`, `last_snmp_check_at`, `snmp_failure_count`, `last_snmp_error`).
- ONU batch status/power endpoints default to snapshot mode (`refresh=false` unless explicitly provided), so opening/refreshing topology panels does not implicitly trigger upstream collection.
- `snmp_check` endpoint name is kept for compatibility, but behavior is Zabbix-only.

## Core Data/Behavior Rules
- `ONU` is scoped to `OLT`; SNMP index uniqueness is `(olt, snmp_index)`.
- `ONU.is_active` defines whether an ONU is part of current topology.
- OLT removal is lifecycle-based (`is_active=False`) rather than immediate hard delete.
- Discovery follows lost-resource retention windows (`disable_lost_after_minutes`, `delete_lost_after_minutes`).
- Polling should avoid false offline alarms during transient collector gaps.
- Settings actions validate vendor capabilities/OID templates before executing discovery/polling/power commands.
- Background maintenance responses include durable job metadata and progress; frontend polls `GET /api/olts/{id}/maintenance_status/`.
- OLT freshness is interval-driven (`polling_interval_seconds`); stale topology must be rendered gray.
- Documentation must be updated on every code change (see `/Users/gabriel/Documents/varuna/AGENTS.md`).
- For multi-client hosting on one machine, isolate by app stack, logical DB, Redis, and credentials per client.
- Production compose supports instance-level isolation knobs: `VARUNA_ENV_FILE`, `VARUNA_FRONTEND_BIND_IP`, `VARUNA_FRONTEND_HTTP_HOST_PORT`, `VARUNA_BACKEND_BIND_IP`, `VARUNA_BACKEND_HOST_PORT`, `VARUNA_POSTGRES_HOST`, `VARUNA_TLS_CERTS_DIR`.
- Production compose sets `BACKEND_BEHIND_FRONTEND_PROXY=1` so backend serves internal HTTP API for frontend `/api` proxying.
- Production backend runtime is Gunicorn on internal port `80`; frontend serves Django `/static` from shared volume.
- Production runtime expects forwarded proto propagation (`X-Forwarded-Proto`) from host ingress through frontend to backend for Django HTTPS/security middleware correctness.

## Where to Read First
1. `docs/ARCHITECTURE.md`
2. `docs/BACKEND.md`
3. `docs/FRONTEND.md`
4. `backend/topology/models/models.py`
5. `backend/topology/management/commands/discover_onus.py`
6. `backend/topology/management/commands/poll_onu_status.py`
7. `backend/topology/management/commands/run_scheduler.py`
8. `backend/topology/api/auth_utils.py`

## Safe Extension Pattern
- Add vendor support by extending `VendorProfile.oid_templates` and validating index/status parsing.
- Keep canonical statuses: `online`, `offline`, `unknown`.
- Add tests for new vendor mapping before rollout.
- Do not add implicit multi-tenant behavior; if tenancy is required, plan it explicitly as a separate architecture change.

## Vendor-Specific Structural Features
- **Indexing: `pon_resolve: interface_map`** — Huawei ONU index still uses `{pon_ifindex}.{onu_id}` semantics and is parsed through vendor profile metadata.
- **Status reason mapping** — Fiberhome reason can come directly from status value (`link_loss` / `dying_gasp`) while Huawei can use separate reason item keys.
- **Power normalization in templates** — Zabbix templates normalize vendor raw values before Varuna reads them.
- **Power validity contract** — accepted optical RX values are strictly `-40 dBm < value < 0 dBm`; template-level preprocessing enforces this first, and backend `normalize_power_value` mirrors it as a defensive guard.
- **Serial normalization in templates** — discovery preprocessing must sanitize malformed serial payloads (comma/punctuation artifacts) before Varuna consumes LLD rows.
- Current vendor profiles: ZTE C300, VSOL LIKE GPON 8P, Huawei MA5680T (seed migration `0012`), Fiberhome AN5516 (seed migration `0013`).
- Zabbix template set in repo root includes: `snmp-avail-template.yaml`, `huawei-template.yaml`, `fiberhome-template.yaml`, `zte-template.yaml`, `vsol-like-template.yaml`.
- **Fiberhome AN5516** — enterprise OID prefix `1.3.6.1.4.1.5875`, flat integer SNMP index (not dotted), slot/pon resolved from separate OID columns (`onu_slot_oid`/`onu_pon_oid` via `index_from: oid_columns`), onu_id extracted from byte2 of flat index, no ONU name OID (serial-only identification, `onu_name_oid` is empty), both ONU Rx and OLT Rx power via `hundredths_dbm`, OLT Rx uses `{pon_base}.{onu_id}` index format via `olt_rx_index_formula: fiberhome_pon_onu`.
