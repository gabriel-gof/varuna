# Varuna Architecture

## Goals
- Monitor many OLTs from multiple vendors using one coherent topology-first UX.
- Keep backend logic vendor-extensible and failure-tolerant.
- Preserve a simple runtime footprint: `frontend`, `backend`, `db`, `redis`.

## Runtime Services
- `frontend`: React + Vite (dev) or Nginx static app (prod).
- `backend`: Django + DRF API, discovery and polling orchestration.
- `db`: PostgreSQL source of truth.
- `redis`: low-latency status/power cache.

## Why No Separate SNMP Container (Now)
A dedicated SNMP worker container is not required for the current scale (5-6 OLTs, multiple cards/PONs) because:
- SNMP discovery/polling are already isolated by management commands.
- Logs are explicit and can be filtered by command context.
- Operational complexity stays low with one backend service.

When to split into dedicated `discovery` and `poller` workers:
- command runtimes start overlapping significantly,
- API latency degrades during polling windows,
- or you need independent autoscaling by job type.

## Data Model Highlights
- `VendorProfile`: vendor/model OID templates and capabilities.
- `OLT`: SNMP credentials, scheduler intervals, and runtime SNMP reachability state.
- `OLTSlot` and `OLTPON`: discovered topology map.
- `ONU`: per-OLT endpoint with active/inactive lifecycle and status.
- `ONULog`: offline event history and disconnect reasons.

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
- Tracks online/offline transitions with `ONULog`.
- Marks missing statuses as `unknown` without generating false offline alarms.
- Marks OLT unreachable when no SNMP status data is returned.

## Unreachable OLT Behavior
- Backend persists SNMP availability (`snmp_reachable`, `last_snmp_check_at`, `snmp_failure_count`, `last_snmp_error`).
- Frontend also runs `snmp_check` and renders unreachable OLT nodes as gray.
- ONU state is preserved during hard SNMP outages to avoid false state corruption.

## Performance Decisions
- Removed global `ONU.snmp_index` uniqueness in favor of per-OLT uniqueness (`(olt, snmp_index)`), enabling multi-vendor/multi-OLT scale.
- Active-only topology/status queries to avoid stale-record inflation.
- `poll_onu_status` refactored to avoid per-ONU log queries.
- Redis invalidation switched from `KEYS` to `SCAN` pattern deletion.
- Topology serializers now use annotated counts where available.
- Frontend enforces stale-data gray state using per-OLT `polling_interval_seconds` and synchronizes health colors between topology and settings views.
