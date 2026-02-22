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
- `status`: status OID, `status_map`, and optional SNMP pacing overrides (`get_chunk_size`, retry/backoff, timeout, call budget multiplier, per-PON pause).
- `power`: OIDs/suffix for RX reads plus optional SNMP pacing overrides (`get_chunk_size`, retry/backoff, timeout, call budget multiplier, per-PON pause, bounded online retry pass).

Default seed migrations:
- `topology.0002_seed_zte_vendor_profile`: baseline `ZTE / C300`.
- `topology.0006_seed_vsol_like_gpon8p_vendor_profile`: `VSOL LIKE / GPON 8P` (white-label family) with ONU-only RX power (`power.onu_rx_oid`, no `power.olt_rx_oid`).
- `topology.0008_tune_vsol_like_collection_settings`: conservative status/power pacing defaults for `VSOL LIKE / GPON 8P` to reduce OLT load and improve completion reliability on large batches.
- `topology.0009_fix_vsol_like_status_map_phase_state`: adjusts VSOL-like phase-state mapping so observed OLT values map correctly (`1/2 -> link_loss`, `4/5 -> dying_gasp`, `3 -> online`), avoiding false `unknown` status for LOS/DyingGasp ONUs.
- `topology.0010_set_immediate_discovery_deactivation`: sets seeded profile discovery policy to deactivate missing ONUs immediately (`disable_lost_after_minutes=0`) while keeping inactive-history retention.

Parser supports:
- regex-based index extraction,
- explicit part-position mapping,
- fixed index values (for single-slot models, e.g. `fixed.slot_id=1`),
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

Default seeded profile policy (`ZTE / C300` and `VSOL LIKE / GPON 8P`):
- Disable lost resources after `0` minutes (immediate deactivation from active topology).
- Delete inactive lost ONUs after `10080` minutes (7 days).

## Polling Rules
- Status polling uses the same OLT-safe SNMP strategy as power collection:
  - short SNMP GET transport (`timeout≈1.8s`, `retries=0`);
  - chunk retry plus recursive chunk split fallback;
  - per-OID retry when a chunk returns partial varbinds;
  - paced PON batching with per-OLT call budget.
- Status runtime pacing is vendor-tunable through `oid_templates.status` (chunk size, timeout, retries/backoff, call-budget multiplier, inter-PON pause), enabling conservative profiles for sensitive OLTs.
- Missing status for one ONU in a partial snapshot: preserve last known ONU status/log state (do not force `unknown` on transient SNMP gaps).
- Full SNMP status failure for OLT: mark OLT unreachable and stop status mutation.
- Offline/online transitions create/close `ONULog` correctly.
- Disconnection timestamp reliability contract:
  - on a proven `online -> offline` transition, polling stores a disconnection window in `ONULog`:
    - `disconnect_window_start` = previous trusted poll timestamp,
    - `disconnect_window_end` = current poll timestamp (first observed offline);
  - if previous poll trust is not available (no prior trusted snapshot), the window fields remain empty.
- Polling command output now includes `failed_chunks` and `missing_preserved` counters for operational visibility.

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
- `refresh_power`: requires `supports_power_monitoring` and `power.onu_rx_oid`.
- `refresh_power` (bulk/all OLTs) applies the same preflight per OLT and skips invalid OLTs with explicit status/details.

If prerequisites are missing, API returns `400` with explicit `detail` and `missing_templates` (when applicable).

Background queue contract for OLT-scoped manual actions:
- `POST /api/olts/{id}/run_discovery/`, `POST /api/olts/{id}/run_polling/`, and `POST /api/olts/{id}/refresh_power/` accept optional payload `{"background": true}`.
- With `background=true`, API returns `202 Accepted` immediately with:
  - `status=accepted` when queued.
  - `status=already_running` when any maintenance action is already in-flight for the same OLT.
- Background execution is serialized per OLT (single-flight across discovery/polling/power) to avoid concurrent SNMP load spikes on the same device.
- Commands run in backend daemon threads and clear in-flight flags when finished (or on exception).
- Without `background=true`, actions keep synchronous behavior and return completion payloads (`200` or `500`) as before.

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
- per-ONU disconnection window fields:
  - `disconnect_window_start`
  - `disconnect_window_end`
- per-OLT power capability:
  - `supports_olt_rx_power` (`true` only when vendor template has `power.olt_rx_oid`)

These fields are used by the frontend for stale-data validation and interval-driven refresh behavior.

Power refresh contract:
- Power readings displayed in topology are read from Redis cache (no per-PON live SNMP read required in normal view flow).
- `POST /api/olts/{id}/refresh_power/` refreshes one OLT cache snapshot and updates `last_power_at`/`next_power_at`.
- `POST /api/olts/refresh_power/` executes a full batch refresh across active OLTs and updates schedule fields per OLT.
- Power collection is status-driven:
  - if usable status snapshot is missing (`last_poll_at` absent or ONUs only `unknown`), backend runs `poll_onu_status` before collecting power;
  - only ONUs with `status=online` are queried for SNMP power;
  - ONUs `offline`/`unknown` are intentionally skipped and returned with empty power values plus `skipped_reason`.
- Power refresh responses expose collection accounting:
  - single OLT: `count`, `attempted_count`, `skipped_not_online_count`, `skipped_offline_count`, `skipped_unknown_count`, `collected_count`;
  - bulk all OLTs: `total_onu_count`, `total_attempted_count`, `total_skipped_not_online_count`, `total_skipped_offline_count`, `total_skipped_unknown_count`, `total_collected_count`.
- Power SNMP collection is resilient for large OLT batches:
  - power reads use short SNMP transport requests (`timeout≈1.8s`, `retries=0`) with application-level retry/fallback control to avoid long stalls;
  - ONUs are collected in paced PON batches (ordered by slot/pon) to reduce burst load on large OLTs;
  - chunk requests are retried on timeout/no-response;
  - failed chunks are split recursively into smaller requests;
  - missing varbinds from partial SNMP responses are retried per OID.
  - online ONUs that still return empty readings after the main pass get a bounded targeted retry pass for higher completion reliability.
  - per-OLT SNMP call budget is derived from OLT size (`estimated_calls * multiplier`) instead of a fixed low cap, so large OLTs (e.g. 4k ONUs) are still fully attempted.
- Power runtime pacing is vendor-tunable through `oid_templates.power` (chunk size, timeout, retries/backoff, call-budget multiplier, inter-PON pause, retry cap), allowing slower but safer collection profiles when OLT load protection is preferred over speed.
- Power cache TTL is interval-aware per OLT (`max(POWER_CACHE_TTL, power_interval_seconds * 2, 300)`), preventing early expiry during long full-OLT collections.
  This reduces partial-power gaps where a full OLT run timed out while single-PON refresh succeeded.
- OLT RX is optional by vendor:
  - when `power.olt_rx_oid` is absent, backend collects only ONU RX;
  - `olt_rx_power` is returned as `null` and no OLT RX SNMP requests are executed;
  - ONU RX parser supports both legacy integer formats and string values like `-27.214(dBm)`.

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
