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
- Primary views: topology, power report, alarm history, settings. Settings is a dedicated nav tab visible only for `admin`.
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
- Role-based access: `admin` (full including settings/maintenance), `operator` (no settings, but can edit PON descriptions and trigger scoped live status/power refresh), `viewer` (read-only). Enforce via `can_modify_settings()` / `can_operate_topology()` on backend and `canManageSettings` / `canOperateTopology` on frontend.
- PON descriptions are operator-managed metadata: editable by `admin` and `operator`, read-only for `viewer`, and must persist across discovery refreshes.
- Discovery, polling, power collection, and reachability checks are scheduled by the backend `run_scheduler` command. The frontend does not submit automatic maintenance; it relies on backend scheduling and provides manual trigger buttons.
- Backend now persists power history snapshots in `ONUPowerSample` and exposes report APIs for the new tabs:
  - `GET /api/onu/power-report/`
  - `GET /api/onu/alarm-clients/`
  - `GET /api/onu/{id}/alarm-history/`
- Alarm and power history retention is controlled by `prune_history` + settings (`POWER_HISTORY_RETENTION_DAYS`, `ALARM_HISTORY_RETENTION_DAYS`, `HISTORY_PRUNE_INTERVAL_SECONDS`).
- Backend container runtime starts scheduler automatically when `ENABLE_SCHEDULER=1` (enabled in current dev/prod env templates).
- Manual settings maintenance actions use a persistent `MaintenanceJob` queue (PostgreSQL), not volatile in-memory flags.
- Background discovery/polling jobs have runtime timeouts (`MAINTENANCE_*_TIMEOUT_SECONDS`) and stale running jobs are auto-failed to prevent permanent queue lock when collector/integration settings are wrong.
- Background discovery/polling jobs also inspect resulting OLT health after command execution and are marked `failed` when the collector run actually failed, instead of reporting `completed` with failure text only in stdout.
- Default collection path is Zabbix API item keys.
- Exception: `FIT / FNCS4000` uses a direct backend Telnet collector (`fit_telnet`) for discovery/status/power because there is no equivalent Zabbix/SNMP runtime path for that device.
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
- Topology-heavy API reads use hybrid caching: per-OLT Redis entries store only static topology structure (OLT/slot/PON/ONU identity fields), while live status/disconnect fields are overlaid from PostgreSQL on every read.
- Topology list/detail no longer load full-tree power snapshots. Power fields stay in the payload for compatibility but may be `null` until the frontend requests scoped `batch-power refresh=false` for the selected PON.
- `GET /api/onu/{id}/power/` and `POST /api/onu/batch-power/` with `refresh=false` read the latest persisted `ONUPowerSample`; `refresh=true` still runs live Zabbix collection and persists the result.
- Topology list/detail payloads expose collector health metadata used by frontend gray-state logic (`collector_reachable`, `last_collector_check_at`, `collector_failure_count`, `last_collector_error`). Legacy `snmp_*` aliases remain in the payload for compatibility.
- ONU batch status/power endpoints default to snapshot mode (`refresh=false` unless explicitly provided), so opening/refreshing topology panels does not implicitly trigger upstream collection.
- `collector_check` is the canonical reachability action. `snmp_check` remains as a compatibility alias, and both are collector-aware (Zabbix sentinel or FIT Telnet login).
- FIT discovery keeps only authorized ONUs (`Active` column from `show onu info`); unauthorized rows must not keep topology branches active.
- Topology color contract is strict: any ONU that is not `online` counts as offline for PON/slot/OLT color decisions, even when its operator-facing bucket is `unknown`.
- The purple `unknown` counter is informational only; it does not suppress red/yellow escalation when a whole PON is down.

## Core Data/Behavior Rules
- `ONU` is scoped to `OLT`; SNMP index uniqueness is `(olt, snmp_index)`.
- `ONU.is_active` defines whether an ONU is part of current topology.
- OLT removal is lifecycle-based (`is_active=False`) rather than immediate hard delete.
- Discovery is immediate for missing resources in active topology (`deactivate_missing=true`, `disable_lost_after_minutes=0`), with optional hard-delete retention (`delete_lost_after_minutes`).
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
- **Operator-facing ONU statuses** — frontend status badges intentionally expose only `online`, `link_loss`, `dying_gasp`, and `unknown`; backend `offline` rows with no known disconnect reason are shown as `unknown`.
- **Power normalization in templates** — Zabbix templates normalize vendor raw values before Varuna reads them.
- **Power validity contract** — accepted optical RX values are strictly `-40 dBm < value < 0 dBm`; template-level preprocessing enforces this first, and backend `normalize_power_value` mirrors it as a defensive guard.
- **Serial normalization in templates** — discovery preprocessing must sanitize malformed serial payloads (comma/punctuation artifacts) before Varuna consumes LLD rows.
- Current vendor profiles: ZTE C300, ZTE C600, VSOL LIKE GPON 8P, Huawei MA5680T (seed migration `0012`), Fiberhome AN5516 (seed migration `0013`), FIT FNCS4000 (seed migration `0030`).
- Zabbix template set in `zabbix-templates/` includes: `snmp-avail-template.yaml`, `huawei-template.yaml`, `fiberhome-template.yaml`, `zte-template.yaml`, `vsol-like-template.yaml`.
- `zte-template.yaml` now exports two Varuna templates: `OLT ZTE C300` and `OLT ZTE C600`.
- **ZTE C600 live mapping** — validation on `192.168.7.151` (`sysName=ZTE-PONTAL`) plus CLI `show gpon onu state` output showed `3/4 -> online`, `2 -> link_loss`, `5 -> dying_gasp`, `7 -> offline`; keep `1 -> link_loss` only as a compatibility fallback for unseen LOS-class rows.
- **ZTE C600 ONU names** — the correct ONU name OID is still `.1.3.6.1.4.1.3902.1082.500.10.2.3.3.1.2`, but nameless ONUs legitimately return `""`; do not invent numeric placeholder names from serial prefixes.
- **ZTE C600 serial cleanup** — comma-prefixed serial payloads such as `1,DD72E68F39E5` must be normalized to the serial token in template preprocessing and backend fallback parsing.
- **Fiberhome AN5516** — enterprise OID prefix `1.3.6.1.4.1.5875`, flat integer SNMP index (not dotted), slot/pon resolved from separate OID columns (`onu_slot_oid`/`onu_pon_oid` via `index_from: oid_columns`), onu_id extracted from byte2 of flat index, no ONU name OID (serial-only identification, `onu_name_oid` is empty), both ONU Rx and OLT Rx power via `hundredths_dbm`, OLT Rx uses `{pon_base}.{onu_id}` index format via `olt_rx_index_formula: fiberhome_pon_onu`.
- **FIT FNCS4000** — direct Telnet collector, configured EPON interfaces `0/1..0/4` by default, discovery runs `show onu info epon 0/x all` on all configured interfaces, while routine status polling scopes `show onu info` reads to PONs that currently have active ONUs in Varuna. Telnet login lands on `EPON>` and must be escalated with `enable`, long `show onu info` output paginates with `--- Enter Key To Continue ----`, identity by `OLT + slot_id + PON + ONU ID`, name from CLI when present, no serial contract, power via `show onu optical-ddm epon 0/x <onu_id>` for online ONUs only, ONU IDs above `64` skipped for power, OLT RX unsupported, disconnect reason remains `unknown`. Multi-blade chassis support via `OLT.blade_ips` JSONField: each blade IP maps to a slot; discovery/status/power open per-blade Telnet sessions. FIT `show onu info` rows may arrive with or without an `Uptime` column depending on blade firmware, and the parser must accept both. FIT failures must preserve blade IP context in error text (`Blade <ip>: ...`). `snmp_index` format: `"{slot_id}/{interface}:{onu_id}"`. Single-blade OLTs fall back to `[ip_address]`. Discovery only materializes blades/PONs that currently have ONUs.
- **Empty PON hiding** — topology structure builder skips PONs with 0 active ONUs and slots with 0 non-empty PONs. Applies generically to all OLT types, including FIT multi-blade chassis. Cache invalidation is natural via `discovery_signature`.
