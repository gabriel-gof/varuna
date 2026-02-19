# Backend Guide

## Stack
- Django + DRF
- PostgreSQL
- Redis
- PySNMP

## Naming and Boundaries
- Project database is `varuna_*` (`POSTGRES_DB` controls environment-specific name).
- Backend monitoring domain app is `topology` (models, migrations, API routes).
- `dashboard` is not a backend app/module in current architecture.

## Backend Layout
- `backend/topology/models/models.py`: domain models.
- `backend/topology/api/views.py`: REST endpoints/actions.
- `backend/topology/api/serializers.py`: API serialization.
- `backend/topology/services/snmp_service.py`: SNMP transport.
- `backend/topology/services/vendor_profile.py`: vendor index/status parsing helpers.
- `backend/topology/services/olt_health_service.py`: OLT SNMP health persistence.
- `backend/topology/management/commands/discover_onus.py`: topology discovery.
- `backend/topology/management/commands/poll_onu_status.py`: status polling.

## Vendor Extensibility Contract
Vendor behavior is controlled by `VendorProfile.oid_templates`:
- `indexing`: how SNMP index maps to `(slot_id, pon_id, onu_id)`.
- `discovery`: OIDs for name/serial/status and stale-deactivation policy.
- `status`: status OID, chunk size, and `status_map`.
- `power`: OIDs/suffix for RX reads.

Default seed migration (`topology.0002_seed_zte_vendor_profile`) creates baseline ZTE C300 templates and thresholds.

Parser supports:
- regex-based index extraction,
- explicit part-position mapping,
- legacy ZTE fallback (`pon_numeric.onu_id` with `0x11rrsspp`).

## OLT Availability State
`OLT` now tracks runtime connectivity:
- `snmp_reachable`
- `last_snmp_check_at`
- `last_snmp_error`
- `snmp_failure_count`
- `polling_interval_seconds`
- `power_interval_seconds`
- `discovery_interval_minutes`
- `last_power_at`
- `next_power_at`

Updated from:
- `snmp_check` API action,
- discovery command,
- polling command.

## Settings API Guardrails
The OLT configuration API now enforces strict runtime-safe validation:
- `protocol` must be `snmp`.
- `snmp_version` must be `v2c` (v3 credentials are not represented yet in the data model).
- `snmp_port` must be in `[1, 65535]`.
- `name` and `snmp_community` are normalized and cannot be empty.
- Intervals must be positive and bounded:
  - `discovery_interval_minutes` <= `10080` (7 days)
  - `polling_interval_seconds` <= `604800` (7 days)
  - `power_interval_seconds` <= `604800` (7 days)

Create semantics were also hardened:
- Creating an OLT with the same name as an inactive OLT reactivates that record instead of failing or creating duplicates.
- Reactivation resets runtime health/scheduling fields so discovery/polling restarts from a clean state.

## ONU Lifecycle
`ONU.is_active` is used to keep history without polluting live topology.
- Seen in discovery: `is_active=True`.
- Missing in discovery (when enabled): managed by retention policy:
  - `disable_lost_after_minutes`: grace before deactivation.
  - `delete_lost_after_minutes`: optional hard-delete for already inactive ONUs.
  - `deactivate_missing`: lifecycle on/off switch.

Default ZTE profile policy:
- Disable lost resources after `60` minutes.
- Delete inactive lost ONUs after `10080` minutes (7 days).

## Polling Rules
- Missing status for one ONU: mark ONU `unknown`, do not create false offline event.
- Full SNMP status failure for OLT: mark OLT unreachable and stop status mutation.
- Offline/online transitions create/close `ONULog` correctly.

## OLT Deletion Contract
`DELETE /api/olts/{id}/` is a soft-deactivation flow:
- OLT is marked `is_active=False`.
- Discovery/polling are disabled.
- Related active slots/PONs/ONUs are deactivated.
- Active ONU offline logs are closed (`offline_until` set).
- Redis cache for that OLT is invalidated.

This preserves topology/history data while removing the OLT from active runtime views.

## Action Preflight Validation
Settings actions now validate vendor capability/template prerequisites before running commands:
- `run_discovery`: requires `supports_onu_discovery` and discovery OIDs (`discovery.onu_name_oid`, `discovery.onu_serial_oid`).
- `run_polling`: requires `supports_onu_status` and `status.onu_status_oid`.
- `refresh_power`: requires `supports_power_monitoring` and power OIDs (`power.onu_rx_oid`, `power.olt_rx_oid`).
- `refresh_power` (bulk/all OLTs) applies the same preflight per OLT and skips invalid OLTs with explicit status/details.

If prerequisites are missing, API returns `400` with explicit `detail` and `missing_templates` (when applicable).

## API Notes
Main endpoints:
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

`GET /api/olts/?include_topology=true` now also returns:
- `discovery_interval_minutes`
- `polling_interval_seconds`
- `power_interval_seconds`
- `last_power_at`
- `next_power_at`

These fields are used by the frontend for stale-data validation and interval-driven refresh behavior.

Power refresh contract:
- Power readings displayed in topology are read from Redis cache (no per-PON live SNMP read required in normal view flow).
- `POST /api/olts/{id}/refresh_power/` refreshes one OLT cache snapshot and updates `last_power_at`/`next_power_at`.
- `POST /api/olts/refresh_power/` executes a full batch refresh across active OLTs and updates schedule fields per OLT.

## Test Coverage
Current tests validate:
- vendor index/status mapping behavior,
- discovery stale deactivation,
- polling unreachable handling,
- polling online/offline transition logs,
- settings API validation guardrails,
- soft OLT deactivation lifecycle,
- action preflight capability/template checks.

File: `backend/topology/tests.py`
