# LLM Context Guide

## What Varuna Is
Varuna is an OLT/ONU monitoring platform focused on topology-first operational visibility, not dashboard-heavy analytics.

## Current Product Decisions
- No dashboard tab in current scope.
- Primary views: topology + settings (settings hidden for viewer role).
- Unreachable OLTs must be visually gray.
- Backend domain app is `topology`; do not reintroduce backend `dashboard` naming.
- Backend is currently single-tenant at application level.
- Multi-client strategy is deployment-level isolation (one Varuna stack per client), not shared-db tenancy.
- Role-based access: `admin`/`operator` (full), `viewer` (read-only). Enforce via `can_modify_settings()` on backend, `canManageSettings` on frontend.
- Frontend auto-maintenance is disabled by default (`FRONTEND_AUTO_MAINTENANCE_ENABLED = false`); manual actions and resume-on-focus refreshes are active.

## Core Data/Behavior Rules
- `ONU` is scoped to `OLT`; SNMP index uniqueness is `(olt, snmp_index)`.
- `ONU.is_active` defines whether an ONU is part of current topology.
- OLT removal is lifecycle-based (`is_active=False`) rather than immediate hard delete.
- Discovery follows lost-resource retention windows (`disable_lost_after_minutes`, `delete_lost_after_minutes`).
- Polling should avoid false offline alarms during transient SNMP gaps.
- Settings actions validate vendor capabilities/OID templates before executing discovery/polling/power commands.
- OLT freshness is interval-driven (`polling_interval_seconds`); stale topology must be rendered gray.
- Documentation must be updated on every code change (see `/Users/gabriel/Documents/varuna/AGENTS.md`).
- For multi-client hosting on one machine, isolate by container stack, DB, Redis, and credentials per client.

## Where to Read First
1. `docs/ARCHITECTURE.md`
2. `docs/BACKEND.md`
3. `docs/FRONTEND.md`
4. `backend/topology/models/models.py`
5. `backend/topology/management/commands/discover_onus.py`
6. `backend/topology/management/commands/poll_onu_status.py`
7. `backend/topology/api/auth_utils.py`

## Safe Extension Pattern
- Add vendor support by extending `VendorProfile.oid_templates` and validating index/status parsing.
- Keep canonical statuses: `online`, `offline`, `unknown`.
- Add tests for new vendor mapping before rollout.
- Do not add implicit multi-tenant behavior; if tenancy is required, plan it explicitly as a separate architecture change.

## Vendor-Specific Structural Features
- **Indexing: `pon_resolve: interface_map`** — Huawei ONU SNMP index is `{pon_ifindex}.{onu_id}` where `pon_ifindex` is opaque. Discovery builds a `pon_map` (ifIndex → slot/pon) from the PON interface walk and passes it to `parse_onu_index`.
- **Disconnect reason second-pass** — when `status.disconnect_reason_oid` is configured, polling fetches disconnect cause in a second SNMP pass for offline ONUs only. Maps raw codes via `status.disconnect_reason_map`.
- **Config-driven power formulas** — `power.onu_rx_formula` and `power.olt_rx_formula` select named functions from `POWER_FORMULA_REGISTRY` in `power_service.py`. Available: `zte_onu_rx`, `zte_olt_rx`, `hundredths_dbm`, `huawei_olt_rx`, `dbm_string`. Defaults to ZTE normalization when absent.
- Current vendor profiles: ZTE C300, VSOL LIKE GPON 8P, Huawei MA5680T (seed migration `0012`).
