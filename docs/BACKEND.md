# Backend Guide

## Stack
- Django + DRF
- PostgreSQL
- Redis
- pureSNMP (`puresnmp`)

## Naming and Boundaries
- Project database is `varuna_*` (`POSTGRES_DB` controls environment-specific name).
- Backend monitoring domain app is `topology` (models, migrations, API routes).
- `dashboard` is not a backend app/module in current architecture.
- Backend runtime is currently single-tenant (no tenant_id partitioning across topology models/APIs).
- Multi-client isolation strategy is stack-level deployment (one backend+db+redis per client), not shared-database tenancy.

## Backend Layout
- `backend/topology/models/models.py`: domain models.
- `backend/topology/api/views.py`: REST endpoints/actions.
- `backend/topology/api/serializers.py`: API serialization.
- `backend/topology/api/auth_views.py`: auth endpoints (login, logout, me, change-password).
- `backend/topology/api/auth_utils.py`: role resolution and permission helpers.
- `backend/topology/services/snmp_service.py`: SNMP transport.
- `backend/topology/services/vendor_profile.py`: vendor index/status parsing helpers.
- `backend/topology/services/olt_health_service.py`: OLT SNMP health persistence.
- `backend/topology/services/maintenance_runtime.py`: shared maintenance runtime helpers (status snapshot pre-checks + power collection payloads).
- `backend/topology/services/maintenance_job_service.py`: persistent OLT maintenance queue/runner and progress lifecycle.
- `backend/topology/services/topology_counter_service.py`: denormalized topology counter rebuild service (OLT/slot/PON totals and online/offline counts).
- `backend/topology/management/commands/discover_onus.py`: topology discovery.
- `backend/topology/management/commands/poll_onu_status.py`: status polling.
- `backend/topology/management/commands/ensure_auth_user.py`: auth user bootstrap.
- `backend/topology/management/commands/run_scheduler.py`: long-lived scheduler for periodic polling, discovery, power collection, and SNMP checks.

## Vendor Extensibility Contract
Vendor behavior is controlled by `VendorProfile.oid_templates`:
- `indexing`: how SNMP index maps to `(slot_id, pon_id, onu_id)`.
- `discovery`: OIDs for name/serial/status, stale-deactivation policy, and walk pacing/safety settings.
  - `pause_between_walks_seconds` (default `0.5`, range `0.0-5.0`): delay between the main discovery walks (name, serial, status) to reduce burst SNMP load on the OLT.
  - `walk_timeout_seconds` (default `30`, range `5-120`): per-request timeout for SNMP walk operations during discovery. Walks use a generous timeout (separate from the short 2s GET timeout) because slow OLTs may need several seconds per bulk batch. Healthy OLTs respond in <100ms (zero impact); slow OLTs with 3-5s responses complete fine; dead OLTs timeout after one request and existing `mark_olt_unreachable` handles it.
  - `min_safe_ratio` (default `0.3`, range `0.0-1.0`): minimum ratio of discovered ONUs to existing active ONUs. If the walk returns fewer ONUs than `active_count * min_safe_ratio`, deactivation is skipped and a critical log is emitted. Guard only applies when `active_count > 0` (first discovery always proceeds). ONU upserts still run.
  - Parse-skip safety uses the same `min_safe_ratio`: if many SNMP indices are returned but only a low number are parseable (`parse_onu_index` success), deactivation is also skipped. This prevents mass false removal when indexing parse fails for a large portion of a discovery snapshot.
- `status`: status OID, `status_map`, and optional SNMP pacing overrides (`get_chunk_size`, retry/backoff, timeout, call budget multiplier, per-PON pause). Optional `disconnect_reason_oid` and `disconnect_reason_map` enable a second-pass fetch of disconnect reasons for offline ONUs (used by Huawei where status and disconnect cause are separate OIDs).
- `power`: OIDs/suffix for RX reads plus optional SNMP pacing overrides (`get_chunk_size`, retry/backoff, timeout, call budget multiplier, per-PON pause, bounded online retry pass). Optional `onu_rx_formula` and `olt_rx_formula` select named formulas from the power formula registry (e.g. `hundredths_dbm`, `huawei_olt_rx`); defaults to ZTE normalization when absent.

Default seed migrations:
- `topology.0002_seed_zte_vendor_profile`: baseline `ZTE / C300`.
- `topology.0006_seed_vsol_like_gpon8p_vendor_profile`: `VSOL LIKE / GPON 8P` (white-label family) with ONU-only RX power (`power.onu_rx_oid`, no `power.olt_rx_oid`).
- `topology.0008_tune_vsol_like_collection_settings`: conservative status/power pacing defaults for `VSOL LIKE / GPON 8P` to reduce OLT load and improve completion reliability on large batches.
- `topology.0009_fix_vsol_like_status_map_phase_state`: adjusts VSOL-like phase-state mapping so observed OLT values map correctly (`1/2 -> link_loss`, `4/5 -> dying_gasp`, `3 -> online`), avoiding false `unknown` status for LOS/DyingGasp ONUs.
- `topology.0010_set_immediate_discovery_deactivation`: sets seeded profile discovery policy to deactivate missing ONUs immediately (`disable_lost_after_minutes=0`) while keeping inactive-history retention.
- `topology.0011_set_global_immediate_discovery_deactivation`: normalizes discovery policy for all vendor profiles to immediate missing-ONU deactivation.
- `topology.0012_seed_huawei_vendor_profile`: `Huawei / MA5680T` with interface-map indexing, split disconnect reason OID, and config-driven power formulas.
- `topology.0013_seed_fiberhome_vendor_profile`: initial `Fiberhome / AN5516` seed (superseded by `0014`).
- `topology.0014_update_fiberhome_oid_columns`: updates Fiberhome to flat integer SNMP index with OID-column-based slot/pon resolution (`onu_slot_oid`/`onu_pon_oid`), byte2 onu_id extraction, enterprise OID prefix `1.3.6.1.4.1.5875`, ONU Rx/OLT Rx power using `hundredths_dbm`, and OLT Rx index translation via `olt_rx_index_formula: fiberhome_pon_onu`.

Parser supports:
- regex-based index extraction,
- explicit part-position mapping,
- fixed index values (for single-slot models, e.g. `fixed.slot_id=1`),
- legacy ZTE fallback (`pon_numeric.onu_id` with `0x11rrsspp`),
- `pon_resolve: interface_map` — resolves `pon_numeric` (opaque ifIndex) to slot/pon via a PON interface name map built during discovery (used by Huawei, where ONU SNMP index is `{pon_ifindex}.{onu_id}`),
- `index_from: oid_columns` — slot/pon resolved from separate SNMP OID columns (`discovery.onu_slot_oid`/`discovery.onu_pon_oid`), onu_id extracted from flat integer index via configurable method (`onu_id_extract: byte2`). Used by Fiberhome where the SNMP index is a flat integer with byte layout `[slot_enc, pon_enc, onu_id, 0]`.

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
- polling command,
- `run_scheduler` periodic SNMP checks.

`snmp_check` is maintenance-aware: if a background job (discovery/polling/power) is in-flight for the OLT when the SNMP check times out, the check returns `reachable: true, busy: true` instead of marking the OLT unreachable. This prevents false gray-state on slower OLTs (e.g. VSOL-like) whose SNMP agent cannot serve concurrent requests during heavy power collection.

## Cached Topology Counters
To remove repeated heavy aggregate queries from the configuration/topology APIs, topology counters are persisted on:
- `OLT`: `cached_slot_count`, `cached_pon_count`, `cached_onu_count`, `cached_online_count`, `cached_offline_count`, `cached_counts_at`.
- `OLTSlot`: `cached_pon_count`, `cached_onu_count`, `cached_online_count`, `cached_offline_count`.
- `OLTPON`: `cached_onu_count`, `cached_online_count`, `cached_offline_count`.

Counter lifecycle contract:
- Migration `0017_backfill_topology_cached_counts` backfills existing runtime data.
- Discovery and polling commands rebuild counters at the end of each successful non-dry-run OLT pass.
- API serializers read cached counters first and safely fall back to live counts when cache fields are null.

This keeps API responses consistent while making `/api/olts/` and `include_topology=true` reads cheaper under high ONU volume.

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

## SNMP Walk Safety
SNMP transport is implemented with `puresnmp`:
- SNMP `v2c` uses `bulkwalk` for discovery-scale table reads.
- SNMP `v1` falls back to `walk` (no bulk requests).
- Transport timeout/retry values are injected per request via the UDP sender wrapper.

SNMP walks include a configurable iteration cap (`max_walk_rows`, default `20000`). If a walk exceeds this limit, it stops early and logs a warning. This prevents infinite loops from buggy OLT firmware returning cyclic or unbounded OID trees.

Walk operations use a dedicated timeout (default 30s, `retries=0`) separate from the short GET timeout (2s, `retries=1`). This prevents walk timeouts on slow OLTs (e.g. MAXPRINT) where string-valued OID walks take 3-5s per bulk batch. The walk timeout is configurable per vendor via `discovery.walk_timeout_seconds`.

SNMP transport logs apply per-error throttling (default 30s window per OLT/error signature) to reduce log flood during sustained outages while keeping recurring failures visible.

## ONU Lifecycle
`ONU.is_active` is used to keep history without polluting live topology.
- Seen in discovery: `is_active=True`.
- Missing in discovery (when enabled): deactivated immediately from active topology (`disable_lost_after_minutes` is forced to `0` by discovery runtime policy).
- `deactivate_missing` remains enabled.
- `delete_lost_after_minutes` remains optional hard-delete for already inactive ONUs.
- Serial normalization: `_normalize_serial` forces all serials to uppercase and strips sentinel values (`N/A`, `NA`, `NONE`, `NULL`, `--`, `-`) to empty string. This ensures consistent display (no mixed-case hex) and prevents firmware-specific placeholder strings from being stored as real serials. Combined with serial preservation, an ONU returning `"N/A"` keeps its previously discovered real serial.
- Discovery serial safety: when a discovery run receives partial/empty serial rows (SNMP walk timeout gaps), existing ONU serial values are preserved instead of being overwritten with blank strings.
- Ghost index filtering: SNMP indices where both name and serial are empty/whitespace are filtered out before the `min_safe_ratio` check. This prevents ghost SNMP entries (deregistered ONUs that still appear in walks with empty fields) from inflating the discovered count or being created as phantom ONUs.
- Discovery DB operations use bulk create/update for ONU upserts to reduce query overhead on large OLTs.
- Discovery creates `ONULog` entries for offline ONUs whose `status_map` provides a disconnect reason (e.g. FiberHome maps status codes directly to `link_loss`/`dying_gasp`). This ensures the topology API returns the correct `disconnect_reason` on first discovery without waiting for a polling cycle. Existing open logs are not duplicated.
- PON interface discovery respects `slot_from`/`pon_from` from indexing config (consistent with `parse_onu_index`).

Default global policy (any OLT/vendor profile):
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
- Polling command accepts optional `--max-olts <N>` to cap due OLTs processed in one run (oldest due first).

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
- `run_discovery`: requires `supports_onu_discovery` and `discovery.onu_serial_oid` (`discovery.onu_name_oid` is optional — Fiberhome has no ONU name OID).
- `run_polling`: requires `supports_onu_status` and `status.onu_status_oid`.
- `refresh_power`: requires `supports_power_monitoring` and `power.onu_rx_oid`.
- `refresh_power` (bulk/all OLTs) applies the same preflight per OLT and skips invalid OLTs with explicit status/details.

If prerequisites are missing, API returns `400` with explicit `detail` and `missing_templates` (when applicable).

Background queue contract for OLT-scoped manual actions:
- `POST /api/olts/{id}/run_discovery/`, `POST /api/olts/{id}/run_polling/`, and `POST /api/olts/{id}/refresh_power/` accept optional payload `{"background": true}`.
- With `background=true`, API returns `202 Accepted` immediately with:
  - `status=accepted` when queued.
  - `status=already_running` when any maintenance action is already in-flight for the same OLT.
- Response payload now includes `job` with persistent metadata (`id`, `kind`, `status`, `progress`, `detail`, timestamps).
- Background execution is serialized per OLT by database constraint/queue policy (`MaintenanceJob` with one active job per OLT across discovery/polling/power).
- Queue state is persistent in PostgreSQL (migration `0015_maintenancejob_and_more`) and survives process restarts.
- Runner behavior:
  - `enqueue_job()` creates a queued row and ensures a background runner is alive.
  - runner claims queued jobs with row locking and marks `status=running`.
  - completion/failure writes terminal status plus output/error, with `progress=100`.
- `GET /api/olts/{id}/maintenance_status/` returns active/latest job state for frontend progress polling.
- Without `background=true`, actions keep synchronous behavior and return completion payloads (`200` or `500`) as before.

## Authentication
API uses Django REST Framework `TokenAuthentication`. All endpoints require authentication by default (`DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]`).

Auth endpoints (all under `/api/`):
- `POST /api/auth/login/` — accepts `{username, password}`, returns `{token, user: {id, username, role, can_modify_settings}}`. Public (AllowAny).
- `POST /api/auth/logout/` — deletes the user's token. Requires auth.
- `GET /api/auth/me/` — returns `{id, username, role, can_modify_settings}` for the authenticated user.
- `POST /api/auth/change-password/` — accepts `{current_password, new_password}`, validates current password, enforces Django password policy, rotates token. Returns new `{token}`.

Frontend stores the token in `localStorage` as `auth_token` and sends it as `Authorization: Token <key>` on every request via an Axios interceptor. On 401 responses, the interceptor clears the stored token and the app returns to the login screen.

Auth views: `backend/topology/api/auth_views.py`.
Auth helpers: `backend/topology/api/auth_utils.py` (`resolve_user_role`, `can_modify_settings`).
URL routing: `backend/topology/urls.py` (auth paths registered before API includes).

### Role-Based Access Control
Users have roles via `UserProfile.role`: `admin`, `operator`, `viewer`.
- `admin` and `operator`: full read/write access to settings, maintenance actions, and power refresh.
- `viewer`: read-only access to topology/status/power data; cannot create/update/delete OLTs, run maintenance actions, refresh power, or patch PON descriptions.

Role resolution (`resolve_user_role`):
1. Superusers always resolve to `admin`.
2. Users with a `UserProfile` use their stored role.
3. Users without a profile default to `viewer`.

Permission enforcement:
- `VendorProfileViewSet` is `ReadOnlyModelViewSet` (no create/update/delete).
- `OLTViewSet` guards `create`, `update`, `destroy`, and all maintenance actions (`run_discovery`, `run_polling`, `snmp_check`, `refresh_power`, `refresh_power_all`) with `can_modify_settings`.
- ONU power refresh (single and batch) requires `can_modify_settings`.
- PON `partial_update` (description editing) requires `can_modify_settings`.
- Read operations (list, retrieve, topology) remain accessible to all authenticated users.

### Auth Bootstrap
Bootstrap command: `backend/topology/management/commands/ensure_auth_user.py`

```bash
# Docker
docker compose -f docker-compose.dev.yml exec backend python manage.py ensure_auth_user \
  --username admin --password changeme --role admin --superuser

# Local
backend/venv/bin/python backend/manage.py ensure_auth_user \
  --username admin --password changeme --role admin --superuser
```

Flags: `--username`, `--password`, `--role` (admin/operator/viewer), `--superuser`, `--force-password`.
Environment variable fallbacks: `VARUNA_AUTH_USERNAME`, `VARUNA_AUTH_PASSWORD`, `VARUNA_AUTH_ROLE`.

## API Notes
Main endpoints:
- `GET /api/healthz/` (public container health endpoint, returns `{"status":"ok"}`)
- `GET /api/olts/`
- `GET /api/olts/?include_topology=true`
- `GET /api/olts/{id}/topology/`
- `POST /api/olts/{id}/run_discovery/`
- `POST /api/olts/{id}/run_polling/`
- `POST /api/olts/{id}/snmp_check/`
- `POST /api/olts/{id}/refresh_power/`
- `GET /api/olts/{id}/maintenance_status/`
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
- Status and power cache writes use Redis pipelines (`set_many_onu_status`/`set_many_onu_power`) to batch all per-OLT entries into a single pipeline execution, reducing Redis round-trips.
- Power cache TTL is interval-aware per OLT (`max(POWER_CACHE_TTL, power_interval_seconds * 2, 300)`), preventing early expiry during long full-OLT collections.
  This reduces partial-power gaps where a full OLT run timed out while single-PON refresh succeeded.
- OLT RX is optional by vendor:
  - when `power.olt_rx_oid` is absent, backend collects only ONU RX;
  - `olt_rx_power` is returned as `null` and no OLT RX SNMP requests are executed;
  - ONU RX parser supports both legacy integer formats and string values like `-27.214(dBm)`.

## Polling Atomicity (Huawei)
When `disconnect_reason_oid` is configured (Huawei), both status and disconnect reason are collected before any writes. Cache and DB writes include all ONU data in single atomic operations. The serializer also ensures offline ONUs without an active `ONULog` return `disconnect_reason='unknown'` instead of `null`, preventing the frontend from showing a bare "Offline" label.

## Backend Scheduler
The `run_scheduler` management command (`backend/topology/management/commands/run_scheduler.py`) is a long-lived process that periodically dispatches:
- **SNMP reachability checks** (run first): every `--snmp-check-seconds` (default 180s), queries `sysDescr.0` per OLT and calls `mark_olt_reachable`/`mark_olt_unreachable`.
  - checks are due-aware per OLT (`last_snmp_check_at`) with adaptive exponential backoff for unreachable OLTs (`snmp_reachable=False`) capped by `--snmp-check-max-backoff-seconds` (default 1800s);
  - scheduler emits per-cycle summary (`checked`, `skipped_not_due`, `reachable`, `unreachable`, elapsed time).
- **Polling**: `call_command('poll_onu_status')` — respects per-OLT `_is_due()` logic; skips OLTs with `snmp_reachable=False` and `snmp_failure_count >= 2`; supports scheduler cap `--max-poll-olts-per-tick`.
- **Discovery**: `call_command('discover_onus')` — respects per-OLT `_is_due()` logic; skips OLTs with `snmp_reachable=False` and `snmp_failure_count >= 2`; supports scheduler cap `--max-discovery-olts-per-tick`.
- **Power collection**: checks `next_power_at` per OLT and collects via `power_service` for due OLTs; skips SNMP-unreachable OLTs; supports scheduler cap `--max-power-olts-per-tick`.

Arguments:
- `--tick-seconds` (default 30)
- `--snmp-check-seconds` (default 180)
- `--snmp-check-max-backoff-seconds` (default 1800)
- optional per-tick caps: `--max-poll-olts-per-tick`, `--max-discovery-olts-per-tick`, `--max-power-olts-per-tick`

Scheduler writes operational timing lines to stdout for each cycle (`poll_onu_status`, `discover_onus`, SNMP summary, power summary) so Docker logs can be used directly for tuning.
Each tick calls `close_old_connections()` and wraps work in try/except for resilience.

**SNMP-first design**: The scheduler checks SNMP reachability before dispatching any collection jobs. This prevents wasted time and log spam from unreachable OLTs. When an OLT comes back online, the next SNMP check (every 180s) detects it and re-enables collection automatically.

In Docker dev, the scheduler runs as a background process alongside the Django runserver:
```bash
python manage.py run_scheduler &
python manage.py runserver 0.0.0.0:8000
```

## Background Collection Scheduling
Discovery and polling commands support due-awareness scheduling:
- Each command has a `_is_due(olt, now)` method that checks `next_discovery_at`/`next_poll_at` or computes due time from `last_discovery_at`/`last_poll_at` + interval.
- When run without `--force` and no specific `--olt-id`, commands filter to only due OLTs.
- Optional `--max-olts` cap limits how many due OLTs are processed in one command run (oldest due first).
- `--force` bypasses due checks and processes all active OLTs.
- The polling command includes a `max_runtime_seconds` budget (default 180s, configurable 30-1800s via `SystemSettings.MAX_POLL_RUNTIME_SECONDS`). If the budget is exhausted mid-run, remaining OLTs are skipped.

Run background collection via Docker:
```bash
# Discovery
docker compose -f docker-compose.dev.yml exec backend python manage.py discover_onus

# Polling
docker compose -f docker-compose.dev.yml exec backend python manage.py poll_onu_status

# Force all (ignores due checks)
docker compose -f docker-compose.dev.yml exec backend python manage.py poll_onu_status --force
```

## Power Service Resilience
Power collection (`backend/topology/services/power_service.py`) includes:
- Pre-fetches cached power via `cache_service.get_many_onu_power()` to reduce per-ONU Redis lookups.
- Skips cache writes for empty reads (`read_at is None`), preventing overwriting good cached data with empty SNMP responses.
- Retains last known cached power values when a forced refresh fails to produce readings: if the new pass returns empty but the cache had valid data, the cached snapshot is preserved instead of being cleared.

## Test Coverage
Current tests validate:
- vendor index/status mapping behavior,
- discovery stale deactivation,
- discovery partial walk guard (skips deactivation when walk returns too few ONUs),
- discovery total index-parse failure guard (when all SNMP indices fail `parse_onu_index`, deactivation is skipped, `discovery_healthy` is set to `False`, and OLT stays `snmp_reachable` since SNMP itself worked),
- polling unreachable handling,
- polling online/offline transition logs,
- settings API validation guardrails,
- soft OLT deactivation lifecycle,
- action preflight capability/template checks,
- SNMP walk iteration cap (`max_walk_rows`),
- SNMP walk timeout parameter passthrough and defaults,
- discovery ghost index filtering (empty name+serial excluded),
- discovery default `min_safe_ratio` (0.3),
- discovery `walk_timeout_seconds` vendor config integration,
- serial normalization (uppercase, sentinel stripping, vendor prefix handling, empty preservation),
- cached power retention on failed forced refresh,
- reader/viewer role permission enforcement (read allowed, write/actions denied),
- authentication API contract (login payload, invalid creds, me, logout, change-password, token rotation),
- `ensure_auth_user` management command (create with profile, superuser promotion, force-password),
- polling command scheduling (due-only, force overrides, runtime budget stops),
- polling command `--max-olts` cap (oldest due first),
- discovery command scheduling (due-only, force overrides),
- discovery command `--max-olts` cap (oldest due first),
- Huawei index parsing (`pon_resolve: interface_map`, unknown ifindex, backward compat with ZTE, empty/missing pon_map),
- disconnect reason mapping (`map_disconnect_reason` for dying_gasp, link_loss, unknown, None),
- power formula registry (hundredths_dbm, huawei_olt_rx, resolve by name/default/unknown),
- Huawei power collection end-to-end (mock SNMP with raw values, correct dBm conversion),
- polling disconnect reason second-pass (fetched for offline only, skipped for online, absent for ZTE),
- scheduler power due logic (`_is_power_due`),
- scheduler SNMP check reachable/unreachable paths,
- scheduler SNMP check backoff due logic (`_is_snmp_check_due`),
- scheduler dispatches polling and discovery commands,
- serializer returns `unknown` disconnect reason for offline ONUs without active log,
- Fiberhome OID-column index parsing (`index_from: oid_columns` with `column_map` and byte2 onu_id extraction), status mapping (0-3), unmapped status defaults, nameless discovery (empty `onu_name_oid`), OLT Rx index translation (`olt_rx_index_formula: fiberhome_pon_onu`), and total index-parse failure guard (all-skipped preserves existing ONUs).

File: `backend/topology/tests.py`
