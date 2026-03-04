# Backend Guide

## Stack
- Django + DRF
- PostgreSQL
- Redis
- Collector backend:
  - `zabbix` mode only: Zabbix API (`api_jsonrpc.php`) is the single collection source.

## Naming and Boundaries
- Project database is `varuna_*` (`POSTGRES_DB` controls environment-specific name).
- Backend monitoring domain app is `topology` (models, migrations, API routes).
- `dashboard` is not a backend app/module in current architecture.
- Backend runtime is currently single-tenant (no tenant_id partitioning across topology models/APIs).
- Multi-client isolation strategy is stack-level app deployment (backend+redis per client with dedicated logical DB/credentials), not shared-table multitenancy.
- Recommended production infra is role-separated: shared `pg-varuna` for Varuna logical DBs and separate `pg-zabbix` for Zabbix.

## Production Container Mode
- In `docker-compose.prod.yml`, backend sets `BACKEND_BEHIND_FRONTEND_PROXY=1`.
- Production backend command uses Gunicorn:
  - `gunicorn varuna.wsgi:application --bind 0.0.0.0:80 ...`
- Frontend proxies `/api` and `/admin` to backend internal HTTP (`backend:80`).
- Django production settings trust `X-Forwarded-Proto=https` (`SECURE_PROXY_SSL_HEADER`) and `USE_X_FORWARDED_HOST=True` for correct secure redirect/cookie behavior behind host TLS termination.
- Entry-point still runs migration/static bootstrap before executing the runtime command.
- Apache templates remain in-repo for optional non-default runtimes, but default production stack path is Gunicorn behind frontend Nginx.

## Backend Layout
- `backend/topology/models/models.py`: domain models.
- `backend/topology/api/views.py`: REST endpoints/actions.
- `backend/topology/api/serializers.py`: API serialization.
- `backend/topology/api/auth_views.py`: auth endpoints (login, logout, me, change-password).
- `backend/topology/api/auth_utils.py`: role resolution and permission helpers.
- `backend/topology/services/zabbix_service.py`: Zabbix API integration (host lookup, item fetch, discovery/status/power reads).
- `backend/topology/services/vendor_profile.py`: vendor index/status parsing helpers.
- `backend/topology/services/olt_health_service.py`: OLT SNMP health persistence.
- `backend/topology/services/maintenance_runtime.py`: shared maintenance runtime helpers (status snapshot pre-checks + power collection payloads).
- `backend/topology/services/maintenance_job_service.py`: persistent OLT maintenance queue/runner and progress lifecycle.
- `backend/topology/services/history_service.py`: persistence helpers for ONU power history snapshots.
- `backend/topology/services/topology_counter_service.py`: denormalized topology counter rebuild service (OLT/slot/PON totals and online/offline counts).
- `backend/topology/management/commands/discover_onus.py`: topology discovery.
- `backend/topology/management/commands/poll_onu_status.py`: status polling.
- `backend/topology/management/commands/prune_history.py`: retention/prune command for alarm and power history.
- `backend/topology/management/commands/ensure_auth_user.py`: auth user bootstrap.
- `backend/topology/management/commands/normalize_serials.py`: one-time data cleanup that applies `_normalize_serial` to all active ONUs and bulk-updates any that changed (fixes Huawei hex-encoded serials stored before recovery logic existed). Supports `--olt-id` and `--dry-run`.
- `backend/topology/management/commands/run_scheduler.py`: long-lived scheduler for periodic polling, discovery, power collection, and collector reachability checks.

## Vendor Extensibility Contract
Vendor behavior is controlled by `VendorProfile.oid_templates`:
- `indexing`: how SNMP index maps to `(slot_id, pon_id, onu_id)`.
- `status`: canonical status mapping metadata (`status_map`) kept for compatibility.
- `power`: metadata used by report/signal contracts.
- `zabbix`: key patterns used by the runtime collector:
  - `discovery_item_key` (default `onuDiscovery`)
  - `availability_item_key` (default `varunaSnmpAvailability`)
  - `status_item_key_pattern` (default `onuStatusValue[{index}]`)
  - `reason_item_key_pattern` (default `onuDisconnectReason[{index}]`)
  - `onu_rx_item_key_pattern` / `olt_rx_item_key_pattern` (standardized to `onuRxPower[{index}]` and `oltRxPower[{index}]` for Huawei and Fiberhome templates)
  - OLT interval fields are pushed to host-level Zabbix macros on OLT create/update:
    - `{$VARUNA.DISCOVERY_INTERVAL}` = `discovery_interval_minutes * 60` (seconds)
    - `{$VARUNA.STATUS_INTERVAL}` = `polling_interval_seconds`
    - `{$VARUNA.POWER_INTERVAL}` = `power_interval_seconds`
    - `{$VARUNA.AVAILABILITY_INTERVAL}` = global sentinel polling interval (`ZABBIX_AVAILABILITY_INTERVAL_SECONDS`, default `30s`)
    - `{$VARUNA.HISTORY_DAYS}` = `history_days` with `d` suffix (for example `7d`)
  - OLT runtime connection fields are also synchronized from Varuna to Zabbix host runtime on OLT create/update:
    - if the Zabbix host is missing, Varuna auto-creates it and links the vendor template (`OLT Fiberhome Unified`, `OLT Huawei Unified`, `OLT ZTE C300`, `OLT VSOL GPON 8P`) plus shared sentinel template (`Varuna SNMP Availability`) with legacy-name fallback support.
    - host cache safety: if a cached Zabbix host id becomes stale (for example after host delete/recreate), runtime resolution validates cached hostid and automatically re-resolves by host name/IP before reading items.
    - Zabbix host technical name (`host`) and visible name (`name`) <- `OLT.name`
    - host group is synced to `ZABBIX_HOST_GROUP_NAME` (legacy groups from `ZABBIX_HOST_GROUP_LEGACY_NAMES` are removed during sync)
    - host tags are standardized and refreshed from Varuna:
      - `source=varuna`
      - `vendor` in lowercase (`fiberhome`, `huawei`, `zte`, `vsol like`, etc.)
      - `model` in lowercase (`unified`, `c300`, `gpon 8p`, etc.)
    - SNMP interface fields are macroized and enforced as:
    - interface `ip` -> `{$VARUNA.SNMP_IP}`
    - interface `port` -> `{$VARUNA.SNMP_PORT}`
    - interface `details.community` -> `{$VARUNA.SNMP_COMMUNITY}`
    - if missing on the host, a fallback SNMP sentinel item (`varunaSnmpAvailability`, `sysName.0`) is created automatically so reachability checks can use 30s freshness even before template re-import.
    - runtime macro values are synchronized from OLT settings:
      - `{$VARUNA.SNMP_IP}` <- `OLT.ip_address`
      - `{$VARUNA.SNMP_PORT}` <- `OLT.snmp_port`
      - `{$VARUNA.SNMP_COMMUNITY}` <- `OLT.snmp_community`
  - Huawei/Fiberhome templates consume those macros for discovery/status/power item delays and item history retention so cadence and retention are controlled from Varuna settings.
  - Template naming convention in Zabbix is Title Case with preserved acronyms and spaces (for example `OLT Huawei Unified`, `OLT Fiberhome Unified`, `OLT ZTE C300`, `OLT VSOL GPON 8P`).
  - Vendor model naming policy: active Huawei and Fiberhome profiles are standardized to `UNIFICADO` (migration `0021`) for Varuna UI compatibility, but exported to Zabbix host tag `model=unified` (English normalization).
  - Trends are disabled in Varuna templates (`trends: 0`) for status/power.
  - Power item preprocessing in Huawei/Fiberhome templates discards sentinel readings `0 dBm` and `-40 dBm` (plus out-of-range values), so invalid optical values do not enter new Zabbix history used by Varuna.
  - Backend applies a second guard (`normalize_power_value`) on Zabbix fetch, cache fallback, history persistence, and API serialization to prevent legacy sentinel rows from surfacing in UI responses.
  - History retention policy for template items is macroized (`{$VARUNA.HISTORY_DAYS}`), default `7d`.
  - Varuna persists power snapshots (`ONUPowerSample`) for report/history APIs.
  - discovery source supports both:
    - normal item key with `lastvalue`,
    - LLD/discovery-rule key (read via `discoveryrule.get` + latest `history.get` value by itemid).
  - when both sources above are empty/unavailable, discovery falls back to enumerating per-ONU status items (`status_item_key_pattern`) and reconstructs ONU identity from item key/index and item name (Huawei/Fiberhome patterns), so topology discovery can continue even when Zabbix does not expose LLD payload history.
  - Fiberhome fallback accepts both status item name formats seen in the field:
    - `ONU PON <slot>/<pon>/<onu> <serial>: Status`,
    - `ONU <slot>/<pon>/<onu> <serial>: Status` (no `PON` prefix),
    - `ONU {#PON} <serial>: Status` (serial-only name).
    For serial-only names, slot/pon/onu are decoded from the flat SNMP index bytes (`[slot*2, pon*8, onu_id, 0]`) before falling back to generic index parsing.
  - Huawei fallback accepts both `ONU <chassi>/<slot>/<pon>/<onu> ...` and `ONU <slot>/<pon>/<onu> ...` status name formats; when serial is embedded as `[SERIAL]`, it is extracted to `{#SERIAL}` (including hex-byte bracket format normalized to `0X...` for serial decoding in discovery).
  - Generic fallback (used by VSOL/ZTE-like templates) accepts status item names in the form `ONU <slot>/<pon>/<onu> <name> <serial>: Status` and extracts both `{#ONU_NAME}` and `{#SERIAL}` when LLD JSON history is unavailable.
  - ZTE C300 power conversion is template-specific (not generic float scaling):
    - `ONU Rx`: raw 16-bit register converted with vendor formula (`<=32767: raw*0.002-30`, `>32767: (raw-65535)*0.002-30`).
    - `OLT Rx`: raw thousandths-of-dBm converted by `/1000` with compatibility fallback when already in dBm.
    - invalid/sentinel raw values are mapped to out-of-range fallback (`-80`) in template preprocessing to keep items supported; Varuna backend range guards then discard them from API/runtime payloads.
  - Fiberhome can omit `reason_item_key_pattern` (empty string) because status values can directly encode offline reason (`link_loss` / `dying_gasp`) and Zabbix status parsing maps those to `status=offline` with canonical reason.

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
- `topology.0018_onupowersample`: adds persisted ONU power history table used by Power Report and Alarm History trend APIs.
- `topology.0019_add_zabbix_key_templates`: adds `oid_templates.zabbix` defaults for seeded vendors so discovery/status/power can be read from Zabbix item keys.
- `topology.0020_align_fiberhome_zabbix_keys`: aligns Fiberhome Zabbix key patterns with Huawei (`onuRxPower[{index}]`/`oltRxPower[{index}]`) and disables separate Fiberhome reason key (`reason_item_key_pattern=''`) because reason is derived from status value.
- `topology.0024_add_zabbix_availability_item_key`: adds `oid_templates.zabbix.availability_item_key` default (`varunaSnmpAvailability`) for sentinel-based reachability checks.
- `topology.0025_seed_zte_vsol_zabbix_profiles`: standardizes `ZTE/C300` and `VSOL LIKE/GPON 8P` profiles for Zabbix-native item keys and template linkage (`OLT ZTE C300`, `OLT VSOL GPON 8P`).
- `topology.0026_standardize_zabbix_template_names`: standardizes preferred Zabbix template names for Huawei/Fiberhome/ZTE/VSOL and keeps legacy aliases for compatibility.

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
- `history_days` (default 7, range 7–30): configurable history retention in days. It drives:
  - frontend Alarm History default window for ONUs of this OLT,
  - Zabbix host macro `{$VARUNA.HISTORY_DAYS}` used by template item `history`.
  Exposed by `OLTSerializer` (read/write), validated by `validate_history_days`, and included in `alarm-clients` response per row.

Updated from:
- `snmp_check` API action,
- discovery command,
- polling command,
- `run_scheduler` periodic collector checks.
- connectivity state is overlaid at response time on cached topology payloads (no topology-cache flush required for pure runtime SNMP reachability updates).

`snmp_check` is mode-aware:
- endpoint name is kept for compatibility;
- implementation is Zabbix-only and updates the same OLT health fields.

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

`include_topology=true` serialization path is optimized for high ONU counts:
- ONU nested payload generation is built in a single pass per ONU (`ONUNestedSerializer.to_representation`) instead of multiple per-field method calls.
- Vendor capability checks (for optional OLT RX power) are cached per OLT during serialization.
- This keeps refresh latency stable when topology payloads include thousands of ONUs.

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

## Collector Runtime
- Direct backend SNMP polling/walk code was removed from runtime services and commands.
- Discovery, status, and power refresh now run exclusively through Zabbix API reads.
- Manual and scoped refresh paths can request immediate Zabbix execution (`task.create`) before reading values.
- Discovery upstream refresh (`discover_onus --refresh-upstream`) uses a short retry window before failing empty:
  - `ZABBIX_DISCOVERY_REFRESH_WAIT_SECONDS` (default `15`)
  - `ZABBIX_DISCOVERY_REFRESH_WAIT_STEP_SECONDS` (default `2`)
  This reduces false "no ONUs discovered" results when Zabbix LLD item creation lags a few seconds after execution request.

## ONU Lifecycle
`ONU.is_active` is used to keep history without polluting live topology.
- Seen in discovery: `is_active=True`.
- Missing in discovery (when enabled): deactivated immediately from active topology (`disable_lost_after_minutes` is forced to `0` by discovery runtime policy).
- `deactivate_missing` remains enabled.
- `delete_lost_after_minutes` remains optional hard-delete for already inactive ONUs.
- Zabbix-side ONU LLD item prototypes are deleted immediately when no longer discovered (template `lifetime_type: DELETE_IMMEDIATELY`), preventing stale per-ONU item carry-over in collector reads.
- Serial normalization: `_normalize_serial` forces all serials to uppercase and strips sentinel values (`N/A`, `NA`, `NONE`, `NULL`, `--`, `-`) to empty string. This ensures consistent display (no mixed-case hex) and prevents firmware-specific placeholder strings from being stored as real serials. Combined with serial preservation, an ONU returning `"N/A"` keeps its previously discovered real serial.
- Serial normalization now handles comma-suffixed/multi-fragment payloads (common on some ZTE/VSOL returns). Discovery selects the fragment that matches a serial pattern (for example `TPLG-D22D7400,` -> `TPLGD22D7400`; `client.name,TPLG-D22D7400` -> `TPLGD22D7400`) instead of blindly keeping the right side of the comma.
- Mangled serial recovery: some OLTs return 8-byte raw serials (4 ASCII vendor + 4 binary) that get UTF-8 decoded into garbage text (e.g. `MONU&BY` instead of `MONU26425900`). `_normalize_serial` detects 5-8 char strings with a 4-letter vendor prefix and non-ASCII/non-alphanumeric suffix artifacts, re-encodes them to bytes (with null-byte padding to 8), and delegates to `_decode_hex_serial` for proper conversion. All-ASCII-alphanumeric and length >8 serials are never touched.
- Mangled serial recovery is resilient to non-latin-1 Unicode suffix characters (from UTF-8 decoded raw bytes): fallback UTF-8 re-encoding prevents normalization crashes and still recovers the serial when the byte payload maps to the Huawei vendor+hex format.
- SNMP byte parsing preserves exact byte payload on binary fallback (`0x...`), including trailing `00` bytes; this prevents Huawei serial truncation like `TPLGD22D74` (missing `00`) when OCTET STRING values are not UTF-8 text.
- Discovery serial safety: when a discovery run receives partial/empty serial rows (SNMP walk timeout gaps), existing ONU serial values are preserved instead of being overwritten with blank strings.
- Discovery serial self-healing on partial gaps: the preserved existing serial also passes through `_normalize_serial`, so legacy malformed values (for example Huawei mangled text like `MONU&BY`/`CMSZ; 0`) are repaired during discovery even when the current serial walk row is missing.
- Ghost index filtering: SNMP indices where both name and serial are empty/whitespace are filtered out before the `min_safe_ratio` check. This prevents ghost SNMP entries (deregistered ONUs that still appear in walks with empty fields) from inflating the discovered count or being created as phantom ONUs.
- Discovery DB operations use bulk create/update for ONU upserts to reduce query overhead on large OLTs.
- Discovery creates `ONULog` entries for offline ONUs whose `status_map` provides a disconnect reason (e.g. FiberHome maps status codes directly to `link_loss`/`dying_gasp`). This ensures the topology API returns the correct `disconnect_reason` on first discovery without waiting for a polling cycle. Existing open logs are not duplicated.
- PON interface discovery respects `slot_from`/`pon_from` from indexing config (consistent with `parse_onu_index`).
- PON descriptions are treated as operator-managed metadata. Discovery must not erase them.
- If discovery recreates a PON row (for example, slot identity/key drift between runs), the new row inherits prior manual description using historical matching (`pon_index`, `pon_key`, then `(slot_id, pon_id)`).

Default global policy (any OLT/vendor profile):
- Disable lost resources after `0` minutes (immediate deactivation from active topology).
- Delete inactive lost ONUs after `10080` minutes (7 days).

## Polling Rules
- Status polling reads per-ONU status/reason from Zabbix item keys (`oid_templates.zabbix.*`) and applies canonical ONU state/log transition logic.
- Missing status for one ONU in a partial snapshot: preserve last known ONU status/log state (do not force `unknown` on transient SNMP gaps).
- Full status read failure for OLT: mark OLT unreachable and stop status mutation.
- Scoped polling filters are supported (`slot`/`pon`/`onu_ids`): scoped runs update only selected ONUs and do not move OLT-wide polling schedule fields (`last_poll_at`/`next_poll_at`).
- Scoped/manual polling supports `--refresh-upstream` to request immediate Zabbix item execution before state read.
- Upstream execution safety cap: `ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS` (default `512`) avoids scheduling thousands of immediate item checks in one request.
- `poll_onu_status --force-upstream` bypasses that cap for explicit manual/scoped refresh runs when operators need immediate reconciliation.
- Settings-triggered OLT polling (`run_polling`, foreground/background) uses `--force-upstream` so manual refresh does not silently fall back to stale snapshots on large OLTs.
- Upstream-forced polling guard:
  - `--refresh-upstream` requests immediate Zabbix item execution and can wait briefly for newer clocks (`ZABBIX_REFRESH_UPSTREAM_WAIT_SECONDS`, default `12s`; `ZABBIX_REFRESH_UPSTREAM_WAIT_STEP_SECONDS`, default `2s`; with `ZABBIX_REFRESH_CLOCK_GRACE_SECONDS`, default `15s`);
  - if some items do not refresh inside that short wait, Varuna still accepts their latest values when they are inside the normal stale-age window (prevents false `unknown` spikes on large OLTs);
  - fail-closed behavior remains for real outages: unreachable collector (`check_olt_reachability`) or fully stale/empty status batch still aborts mutation.
- Stale status safety:
  - per-ONU samples older than `polling_interval_seconds * 3 + ZABBIX_STATUS_STALE_MARGIN_SECONDS` (default margin `90s`) are ignored and existing ONU state/log is preserved for that ONU.
  - when all returned samples are stale, polling marks the OLT unreachable and aborts mutation (no false mass state changes, no `last_poll_at` advance).
- Offline/online transitions create/close `ONULog` correctly.
- Disconnection timestamp reliability contract:
  - on a proven `online -> offline` transition, polling stores a disconnection window in `ONULog`:
    - `disconnect_window_start` = previous Zabbix status sample clock with `value=online`,
    - `disconnect_window_end` = current Zabbix status sample clock with `value=offline` (or reason-encoded offline).
  - trust rule is Zabbix-sample based (not local polling clock based):
    - previous sample must exist and be `online`,
    - previous clock must be older than current clock,
    - transition gap must be within `polling_interval_seconds * 2 + ZABBIX_DISCONNECT_WINDOW_MARGIN_SECONDS` (default margin `90s`).
  - if transition proof is unavailable, polling stores a detection-point window (`disconnect_window_start == disconnect_window_end == offline_since`) so UI can still show when Varuna first confirmed the ONU offline.
  - `offline_since`/`offline_until` use Zabbix status clocks when available (fallback to backend `now()` only when clock metadata is missing).
- Polling command output now includes `failed_chunks` and `missing_preserved` counters for operational visibility.
- Polling command accepts optional `--max-olts <N>` to cap due OLTs processed in one run (oldest due first).
- Successful/failed polling runs invalidate cached topology API payloads for that OLT (`cache_service.invalidate_topology_api_cache(olt.id)`) so frontend counters do not lag behind fresh status writes.

## OLT Deletion Contract
`DELETE /api/olts/{id}/` is a soft-deactivation flow:
- OLT is marked `is_active=False`.
- Discovery/polling are disabled.
- Related active slots/PONs/ONUs are deactivated.
- Active ONU offline logs are closed (`offline_until` set).
- Redis cache for that OLT is invalidated.
- Varuna also attempts to delete the resolved Zabbix host (`host.delete`) to keep Zabbix inventory aligned with active Varuna OLTs.

This preserves topology/history data while removing the OLT from active runtime views.

## Action Preflight Validation
Settings actions now validate vendor capability/template prerequisites before running commands:
- `run_discovery`: requires `supports_onu_discovery` and `zabbix.discovery_item_key`.
- `run_polling`: requires `supports_onu_status` and `zabbix.status_item_key_pattern`.
- `refresh_power`: requires `supports_power_monitoring` and `zabbix.onu_rx_item_key_pattern`.
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
- Timeout and stale-job safety:
  - discovery and polling background jobs run in subprocesses with hard timeouts (`MAINTENANCE_DISCOVERY_TIMEOUT_SECONDS`, `MAINTENANCE_POLLING_TIMEOUT_SECONDS`) so blocked SNMP calls cannot stall the maintenance runner forever.
  - active `running` jobs older than the configured timeout window are auto-expired as `failed` during enqueue/status checks, unblocking new jobs for the same OLT.
  - timeout failures set job detail/error to explicit timeout guidance so operators can fix SNMP parameters and retry.
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
Runtime policy is two-role (`admin`, `viewer`). The legacy `operator` value is still accepted for backward compatibility and behaves like `admin`.
- `admin` (and legacy `operator`): full read/write access to settings, maintenance actions, and power refresh.
- `viewer`: topology/runtime operator profile; cannot create/update/delete OLTs, run OLT maintenance actions, or patch PON descriptions.
- `viewer` can trigger ONU-scoped refresh endpoints (`batch-status`, `batch-power`, single ONU status/power refresh) for live status/power reads from topology view.

Role resolution (`resolve_user_role`):
1. Superusers always resolve to `admin`.
2. Users with a `UserProfile` use their stored role.
3. Users without a profile default to `viewer`.

Permission enforcement:
- `VendorProfileViewSet` is `ReadOnlyModelViewSet` (no create/update/delete).
- `OLTViewSet` guards `create`, `update`, `destroy`, and all maintenance actions (`run_discovery`, `run_polling`, `snmp_check`, `refresh_power`, `refresh_power_all`) with `can_modify_settings`.
- PON `partial_update` (description editing) requires `can_modify_settings`.
- Successful PON description patch invalidates topology API response cache for that OLT so refreshed topology reads return the updated description immediately.
- Read operations (list, retrieve, topology) remain accessible to all authenticated users.

### Auth Bootstrap
Bootstrap command: `backend/topology/management/commands/ensure_auth_user.py`

```bash
# Docker
docker compose -f docker-compose.dev.yml exec backend python manage.py ensure_auth_user \
  --username admin --password admin --role admin --superuser --force-password

# Local
backend/venv/bin/python backend/manage.py ensure_auth_user \
  --username admin --password admin --role admin --superuser --force-password
```

Flags: `--username`, `--password`, `--role` (`admin`/`viewer`; legacy `operator` still accepted), `--superuser`, `--force-password`.
Environment variable fallbacks: `VARUNA_AUTH_USERNAME`, `VARUNA_AUTH_PASSWORD`, `VARUNA_AUTH_ROLE`.

Container bootstrap support (`docker/entrypoint.sh`):
- if `VARUNA_AUTH_BOOTSTRAP=1`, entrypoint runs `ensure_auth_user` during startup using:
  - `VARUNA_AUTH_USERNAME`
  - `VARUNA_AUTH_PASSWORD`
  - `VARUNA_AUTH_ROLE` (default `admin`)
  - optional `VARUNA_AUTH_SUPERUSER`
  - optional `VARUNA_AUTH_FORCE_PASSWORD`
- when bootstrap is enabled and username/password are missing, startup fails fast.

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
- `POST /api/onu/{id}/refresh-status/`
- `GET /api/onu/{id}/power/`
- `POST /api/onu/batch-status/`
- `POST /api/onu/batch-power/`

## Topology API Response Cache
To reduce load time and repeated serialization cost for large topology trees:
- `GET /api/olts/` and `GET /api/olts/?include_topology=true` now support Redis-backed response cache keyed by request query signature.
- `GET /api/olts/{id}/topology/` also supports per-OLT response cache.
- Cache TTL settings:
  - `OLT_LIST_CACHE_TTL` (base list)
  - `OLT_TOPOLOGY_LIST_CACHE_TTL` (include_topology list)
  - `OLT_TOPOLOGY_DETAIL_CACHE_TTL` (single OLT topology endpoint)
- Cache invalidation is focused on topology structure changes:
  - discovery (`discover_onus`)
  - OLT create/update/delete settings actions
- Cache-hit freshness guard:
  - topology list/detail responses now overlay runtime OLT health/schedule fields from DB (`snmp_*`, polling/discovery/power timestamps and intervals) before returning payloads.
  - topology list/detail cache hits also overlay per-ONU status and power fields from Redis (`varuna:onu:*:status` and `varuna:onu:*:power`) so dynamic telemetry stays fresh without rebuilding full topology trees.
  - runtime ONU overlay is non-destructive for disconnection fields: empty cache values never erase existing serializer fallbacks (`offline_since` / disconnection window) from active logs.
  - this keeps topology caches hot for first-page loads while still reflecting backend runtime collection updates.

`GET /api/olts/?include_topology=true` now also returns:
- `discovery_interval_minutes`
- `polling_interval_seconds`
- `power_interval_seconds`
- `last_power_at`
- `next_power_at`
- SNMP health metadata required for gray-state derivation on topology surfaces:
  - `snmp_reachable`
  - `last_snmp_check_at`
  - `snmp_failure_count`
  - `last_snmp_error`
- per-ONU disconnection window fields:
  - `disconnect_window_start`
  - `disconnect_window_end`
- per-OLT power capability:
  - `supports_olt_rx_power` (`true` only when vendor template has `power.olt_rx_oid`)

These fields are used by the frontend for stale-data validation and interval-driven refresh behavior.

`GET /api/olts/{id}/topology/` includes the same SNMP health metadata under `olt`, so fallback detail fetches and list fetches remain behaviorally consistent.

Power refresh contract:
- Power readings displayed in topology are read from Redis cache (no per-PON live collector read required in normal view flow).
- `POST /api/olts/{id}/refresh_power/` refreshes one OLT cache snapshot and updates `last_power_at`/`next_power_at`.
- `POST /api/olts/refresh_power/` executes a full batch refresh across active OLTs and updates schedule fields per OLT.
- Power collection is status-driven:
  - if usable status snapshot is missing (`last_poll_at` absent, stale poll timestamp, `snmp_reachable=false`, or ONUs only `unknown`), backend runs `poll_onu_status` before collecting power;
  - only ONUs with `status=online` are queried for power through Zabbix item keys;
  - ONUs `offline`/`unknown` are intentionally skipped and returned with empty power values plus `skipped_reason`.
- Power refresh responses expose collection accounting:
  - single OLT: `count`, `attempted_count`, `skipped_not_online_count`, `skipped_offline_count`, `skipped_unknown_count`, `collected_count`;
  - bulk all OLTs: `total_onu_count`, `total_attempted_count`, `total_skipped_not_online_count`, `total_skipped_offline_count`, `total_skipped_unknown_count`, `total_collected_count`.
- Power collection reads key-patterned item values from Zabbix (`oid_templates.zabbix.onu_rx_item_key_pattern` / `olt_rx_item_key_pattern`).
- Stale power safety: samples older than `power_interval_seconds * 3 + ZABBIX_POWER_STALE_MARGIN_SECONDS` (default margin `90s`) are discarded for refresh responses.
- Cache fallback on forced refresh is mode-aware:
  - non-upstream refresh keeps previous cached values when no fresh sample arrives;
  - `refresh_upstream=true` does not fall back to cached stale values (manual scoped refresh must not look artificially fresh).
- Power refresh shares the same upstream execution cap (`ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS`) and supports forced bypass via `force_upstream=True` in maintenance runtime for explicit manual refresh flows.
- Power history persistence accepts older item clocks (up to 180 minutes) to account for template-driven cadence and avoid dropping valid readings.
- Fiberhome Zabbix template payloads are intentionally compacted (`by_onu` short codes and flat `power_data` values) to keep master JSON under Zabbix text item limits (65,535 bytes) on high-density OLTs.
- Status and power cache writes use Redis pipelines (`set_many_onu_status`/`set_many_onu_power`) to batch all per-OLT entries into a single pipeline execution, reducing Redis round-trips.
- Status cache TTL is interval-aware per OLT (`max(STATUS_CACHE_TTL, polling_interval_seconds * 2, 300)`), preventing status snapshots from expiring before the next scheduled polling cycle.
- Power cache TTL is interval-aware per OLT (`max(POWER_CACHE_TTL, power_interval_seconds * 2, 300)`), preventing early expiry during long full-OLT collections.
  This reduces partial-power gaps where a full OLT run timed out while single-PON refresh succeeded.
- Fresh power rows are persisted to PostgreSQL (`ONUPowerSample`) during scheduler/manual/scoped power refresh flows for report queries and trend charts.
- OLT RX is optional by vendor:
- when `power.olt_rx_oid` is absent, backend collects only ONU RX;
- `olt_rx_power` is returned as `null` and no OLT RX SNMP requests are executed;
- ONU RX parser supports both legacy integer formats and string values like `-27.214(dBm)`.

ONU batch refresh default behavior:
- `POST /api/onu/batch-status/` now defaults to cached DB/log snapshot reads (`refresh=false` unless explicitly set).
- `POST /api/onu/batch-power/` now defaults to cached power snapshot reads (`refresh=false` unless explicitly set).
- `GET /api/onu/{id}/power/` and `POST /api/onu/{id}/refresh-status/` also default to snapshot reads unless `refresh=true` is explicitly requested.
- This avoids accidental UI-coupled SNMP collection from panel refresh flows; explicit live collection remains available when `refresh=true` is sent by authenticated users.
- For `refresh=true` scoped reads, if collector connectivity is unavailable the API returns `503` with explicit `detail` instead of silently returning stale-as-fresh results.
- For `refresh=true` scoped reads with `refresh_upstream`, backend first tries to get fresh post-refresh clocks, but still accepts recent pre-refresh clocks inside stale-age policy; API remains fail-closed (`503`) for collector unreachability or fully stale/empty status reads.
  This applies to:
  - `POST /api/onu/batch-status/`
  - `POST /api/onu/batch-power/`
  - `GET /api/onu/{id}/power/`
  - `POST /api/onu/{id}/refresh-status/`

## Polling Atomicity (Huawei)
When `disconnect_reason_oid` is configured (Huawei), both status and disconnect reason are collected before any writes. Cache and DB writes include all ONU data in single atomic operations. The serializer also ensures offline ONUs without an active `ONULog` return `disconnect_reason='unknown'` instead of `null`, preventing the frontend from showing a bare "Offline" label.

## Backend Scheduler
The `run_scheduler` management command (`backend/topology/management/commands/run_scheduler.py`) is a long-lived process that periodically dispatches:
- **Collector reachability checks** (run first): every `--collector-check-seconds` (default `30s`), checks OLT availability from Zabbix host/interface freshness and calls `mark_olt_reachable`/`mark_olt_unreachable`.
  - checks are due-aware per OLT (`last_snmp_check_at`) and run with a fixed cadence to speed recovery detection when connectivity returns (no exponential backoff delay in runtime loop).
  - reachability logic uses SNMP interface availability plus collector item freshness:
    - if `zabbix.availability_item_key` exists (default `varunaSnmpAvailability`), its clock is validated against `ZABBIX_AVAILABILITY_STALE_SECONDS` (default `45s`);
    - if `zabbix.status_collection_key` exists, its `lastclock` is validated against freshness.
    - otherwise Varuna validates freshness using the newest per-ONU status item from `zabbix.status_item_key_pattern` prefix (for example `onuStatusValue[`), preventing false "reachable" when interfaces are stale but still reported as available.
  - freshness threshold for collector checks is aligned with stale-status policy: `max(polling_interval_seconds + 90s, 390s)`.
  - scheduler emits per-cycle summary (`checked`, `skipped_not_due`, `reachable`, `unreachable`, elapsed time).
- **Discovery**: `call_command('discover_onus')` — respects per-OLT `_is_due()` logic; skips OLTs with `snmp_reachable=False` and `snmp_failure_count >= 2`; supports scheduler cap `--max-discovery-olts-per-tick`.
- **Polling**: `call_command('poll_onu_status')` — runs after discovery in the same tick, respects per-OLT `_is_due()` logic, skips OLTs with `snmp_reachable=False` and `snmp_failure_count >= 2`; supports scheduler cap `--max-poll-olts-per-tick`.
- **Power collection**: checks `next_power_at` per OLT and collects via `power_service` for due OLTs; skips unreachable OLTs; supports scheduler cap `--max-power-olts-per-tick`.
- **History prune**: `call_command('prune_history')` on scheduler interval (`--history-prune-seconds`, default from `HISTORY_PRUNE_INTERVAL_SECONDS`) to enforce retention windows.

Arguments:
- `--tick-seconds` (default 30)
- `--collector-check-seconds` (default from `COLLECTOR_CHECK_SECONDS`, fallback `30`; legacy alias `--snmp-check-seconds`)
- `--collector-check-max-backoff-seconds` (default from `COLLECTOR_CHECK_MAX_BACKOFF_SECONDS`, fallback `1800`; legacy alias `--snmp-check-max-backoff-seconds`)
- `--history-prune-seconds` (default `HISTORY_PRUNE_INTERVAL_SECONDS`, 21600)
- optional per-tick caps: `--max-poll-olts-per-tick`, `--max-discovery-olts-per-tick`, `--max-power-olts-per-tick`

Scheduler writes operational timing lines to stdout for each cycle (`poll_onu_status`, `discover_onus`, collector summary, power summary) so Docker logs can be used directly for tuning.

Container startup contract:
- Backend container startup supports `ENABLE_SCHEDULER=1` to launch `python manage.py run_scheduler` in background before starting the main web process.
- This keeps discovery/polling/power/collector checks backend-managed in both dev and production runtime modes.
Each tick calls `close_old_connections()` and wraps work in try/except for resilience.

**Collector-first design**: The scheduler checks reachability before dispatching any collection jobs. This prevents wasted time and log spam from unreachable OLTs. When an OLT comes back online, the next check (every 30s by default) detects it and re-enables collection automatically.

In Docker dev, the scheduler runs as a background process alongside the Django runserver:
```bash
python manage.py run_scheduler &
python manage.py runserver 0.0.0.0:8000
```

## Background Collection Scheduling
Discovery and polling commands support due-awareness scheduling:
- Each command has a `_is_due(olt, now)` method that checks `next_discovery_at`/`next_poll_at` or computes due time from `last_discovery_at`/`last_poll_at` + interval.
- When run without `--force` and no specific `--olt-id`, commands filter to only due OLTs.
- On successful discovery, backend brings `next_poll_at` forward to `now` (when polling is enabled and next poll was in the future) so newly discovered/reactivated ONUs are polled immediately in the next scheduler pass.
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
- Retains last known cached power values when a forced refresh fails to produce readings only for non-upstream refreshes.
- For upstream-forced refreshes (`refresh_upstream=true`), stale/empty reads stay empty so operators can see that live collection did not succeed.

## History Retention and Reporting APIs
- Persisted power history model: `ONUPowerSample` (per-ONU `read_at`, RX values, and collection source).
- Retention settings:
  - `POWER_HISTORY_RETENTION_DAYS` (default `30`)
  - `ALARM_HISTORY_RETENTION_DAYS` (default `90`)
  - `HISTORY_PRUNE_INTERVAL_SECONDS` (default `21600`)
- Prune command:
  - `python manage.py prune_history`
  - removes `ONUPowerSample` rows older than power retention.
  - removes resolved `ONULog` rows (`offline_until` set) older than alarm retention.
  - keeps active alarms (`offline_until` null).
- Report endpoints:
  - `GET /api/onu/power-report/?search=<term>`: flattened power rows (latest persisted sample per active ONU), with optional backend search on ONU name/serial/OLT name.
    - if no persisted sample exists yet for an ONU, API falls back to runtime Redis power cache so Power tab can render current readings immediately.
    - row contract includes `status` (ONU runtime status), `power_interval_seconds` (OLT power cadence), and topology references (`slot_ref_id`, `pon_ref_id`) so frontend can derive stale/fresh power classes and drill into the exact topology path.
  - `GET /api/onu/alarm-clients/?search=<term>&limit=<n>`: lightweight searchable ONU suggestions. Each result includes `history_days` (from the ONU's OLT) so the frontend renders the correct rolling window without an extra OLT fetch.
  - `GET /api/onu/{id}/alarm-history/`: ONU event history + downsampled power trend points.
    - Optional `start_date` and `end_date` query params (ISO `YYYY-MM-DD`) restrict both alarm logs and power samples to the given date range. Start is clamped to max 365 days ago, end to today. Falls back to days-based `alarm_days`/`power_days` offsets on invalid or missing dates.
    - Source contract is now Zabbix-first:
      - status intervals are reconstructed from the ONU status item history timeline (`online -> offline -> online` transitions) using Zabbix item clocks;
      - `event_type` is inferred from status values (`link_loss`/`dying_gasp`) or from reason item history when needed;
      - when transition proof is unavailable, disconnection window collapses to the first observed offline sample (`disconnect_window_start == disconnect_window_end == start_at`);
      - power history is merged from ONU RX + OLT RX item histories and downsampled by `max_power_points`.
    - Fallback contract: when Zabbix timeline data is unavailable for the ONU, endpoint falls back to local `ONULog` + `ONUPowerSample`.
    - Response includes `source` with values:
      - `zabbix`: timeline derived directly from Zabbix history API;
      - `varuna`: local DB fallback.

## Test Coverage
Current tests validate:
- vendor index/status mapping behavior,
- discovery stale deactivation,
- discovery partial walk guard (skips deactivation when walk returns too few ONUs),
- discovery total index-parse failure guard (when all indices fail `parse_onu_index`, deactivation is skipped, `discovery_healthy` is set to `False`, and OLT stays `snmp_reachable` since collector read itself worked),
- polling unreachable handling,
- polling online/offline transition logs,
- settings API validation guardrails,
- soft OLT deactivation lifecycle,
- action preflight capability/template checks,
- discovery row iteration cap (`max_walk_rows`),
- discovery timeout parameter passthrough and defaults,
- discovery ghost index filtering (empty name+serial excluded),
- discovery default `min_safe_ratio` (0.3),
- discovery `walk_timeout_seconds` vendor config integration,
- serial normalization (uppercase, sentinel stripping, vendor prefix handling, empty preservation),
- cached power retention on failed forced refresh,
- topology cache-hit overlays for per-ONU status/power runtime fields (list and detail endpoints),
- interval-aware polling status cache TTL,
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
- Huawei power collection end-to-end (mock Zabbix values, correct dBm conversion),
- polling disconnect reason second-pass (fetched for offline only, skipped for online, absent for ZTE),
- scheduler power due logic (`_is_power_due`),
- scheduler collector check reachable/unreachable paths,
- scheduler collector check backoff due logic (`_is_snmp_check_due`),
- scheduler dispatches polling and discovery commands,
- history/report APIs (`power-report`, `alarm-clients`, `alarm-history`) and `prune_history` retention behavior, alarm-history `start_date`/`end_date` date-range filtering and invalid-date fallback, Zabbix-timeline source selection (`source=zabbix|varuna`) and interval reconstruction,
- serializer returns `unknown` disconnect reason for offline ONUs without active log,
- Fiberhome OID-column index parsing (`index_from: oid_columns` with `column_map` and byte2 onu_id extraction), status mapping (0-3), unmapped status defaults, nameless discovery (empty `onu_name_oid`), OLT Rx index translation (`olt_rx_index_formula: fiberhome_pon_onu`), and total index-parse failure guard (all-skipped preserves existing ONUs).

Files: `backend/topology/tests.py` (entrypoint) and `backend/topology/tests_zabbix_mode.py` (active suite)
