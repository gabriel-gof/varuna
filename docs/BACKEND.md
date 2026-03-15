# Backend Guide

## Stack
- Django + DRF
- PostgreSQL
- Redis
- Collector backend:
  - `zabbix`: Zabbix API (`api_jsonrpc.php`) for standard SNMP/Zabbix-backed vendors.
  - `fit_telnet`: direct FIT collector for `FIT / FNCS4000`, with HTTP web UI scraping as the default transport and Telnet CLI as an explicit fallback.
- Zabbix-backed latest status/power reads use a read-only PostgreSQL path (`DATABASES['zabbix']`) for `items` latest-value fields and power history when `ZABBIX_DB_ENABLED=1`. There is no JSON-RPC API fallback for these paths; if the DB read fails, the method logs an error and returns empty results.

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
- `backend/topology/services/zabbix_service.py`: Zabbix integration (API host/discovery/manual actions plus optional direct PostgreSQL latest-item reads for status/power hot paths).
- `backend/topology/services/fit_collector_service.py`: FIT `FNCS4000` direct collector (HTTP web UI default, Telnet fallback) for reachability, discovery/status rows, and per-ONU power.
- `backend/topology/services/collector_service.py`: collector-mode dispatcher (Zabbix vs FIT transport-aware direct collector). `get_collector_transport()` returns transport values (`http` / `telnet`) only; collector type remains a separate decision.
- `backend/topology/services/vendor_profile.py`: vendor index/status parsing helpers.
- `backend/topology/services/olt_health_service.py`: OLT collector health persistence.
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
- `collector`: collector-mode metadata (`type`, fixed interface list, transport-specific hints).
- `indexing`: how SNMP index maps to `(slot_id, pon_id, onu_id)`.
- `status`: canonical status mapping metadata (`status_map`) kept for compatibility.
- `power`: metadata used by report/signal contracts.
- `zabbix`: key patterns used by the runtime collector:
  - used only when `collector.type` resolves to `zabbix`
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
    - if the Zabbix host is missing, Varuna auto-creates it and links the vendor template (`OLT Fiberhome Unified`, `OLT Huawei Unified`, `OLT ZTE C300`, `OLT ZTE C600`, `OLT VSOL GPON 8P`) plus shared sentinel template (`Varuna SNMP Availability`) with legacy-name fallback support.
    - host cache safety: if a cached Zabbix host id becomes stale (for example after host delete/recreate), runtime resolution validates cached hostid and automatically re-resolves by host name/IP before reading items.
    - Zabbix host technical name (`host`) and visible name (`name`) <- `ZABBIX_HOST_NAME_PREFIX + OLT.name` (prefix optional; default empty).
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
  - Template naming convention in Zabbix is Title Case with preserved acronyms and spaces (for example `OLT Huawei Unified`, `OLT Fiberhome Unified`, `OLT ZTE C300`, `OLT ZTE C600`, `OLT VSOL GPON 8P`).
  - Vendor model naming policy: active Huawei and Fiberhome profiles are standardized to `UNIFICADO` (migration `0021`) for Varuna UI compatibility, but exported to Zabbix host tag `model=unified` (English normalization).
  - Trends are disabled in Varuna templates (`trends: 0`) for status/power.
  - Power item preprocessing in Huawei/Fiberhome/VSOL-like templates accepts only realistic optical RX values (`-40 dBm < value < 0 dBm`) and discards sentinel/out-of-range readings.
  - Backend applies a second guard (`normalize_power_value`) on Zabbix fetch, cache fallback, history persistence, and API serialization with the same strict range (`-40 < dBm < 0`) so legacy/template-fallback sentinels never surface in UI responses.
  - History retention policy for template items is macroized (`{$VARUNA.HISTORY_DAYS}`), default `7d`.
  - ONU item prototypes in vendor templates include `slot={#SLOT}` and `pon={#PON}` tags (plus existing `collector`/`metric`) to support Zabbix-side filtering/debug by slot and PON.
  - Varuna persists power snapshots (`ONUPowerSample`) for history/trend APIs and retained refresh history.
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
  - Discovery identity normalization is defensive on both LLD JSON and status-item fallback rows: serial-like names are cleared, placeholder sentinels (`N/A`, `NULL`, `--`) are discarded, and malformed ZTE-style suffixes such as `alexandre.silva 1` are trimmed back to the base ONU name when no valid serial is present.
  - ZTE C300/C600 power conversion is template-specific (not generic float scaling):
    - `ONU Rx`: raw 16-bit register converted with vendor formula (`<=32767: raw*0.002-30`, `>32767: (raw-65535)*0.002-30`).
    - `OLT Rx`: raw thousandths-of-dBm converted by `/1000` with compatibility fallback when already in dBm.
    - invalid/sentinel raw values are mapped to out-of-range fallback (`-80`) in template preprocessing to keep items supported; Varuna backend strict range guard (`-40 < dBm < 0`) discards them from API/runtime payloads.
  - ZTE C600/C620 status mapping differs from ZTE C300:
    - live validation against `192.168.7.151` (`sysName=ZTE-PONTAL`) plus CLI `show gpon onu state` output showed `3/4 -> online`, `2 -> link_loss (LOS)`, `5 -> dying_gasp`, and `7 -> generic offline`;
    - Varuna's `OLT ZTE C600` template therefore maps `1/2 -> link_loss`, `5 -> dying_gasp`, `6/7 -> offline`, and leaves unknown/unseen codes unmapped.
  - ZTE C600/C620 ONU name OID remains `1.3.6.1.4.1.3902.1082.500.10.2.3.3.1.2`. On nameless ONUs that branch legitimately returns an empty string; Varuna should keep the blank name instead of inventing a placeholder.
  - ZTE discovery preprocessing must treat ONU names as names unless they positively match a serial-like token. A previous template bug returned arbitrary non-empty strings from `normalizeSerial()`, which caused real dotted names like `ct25473.thiago` to be erased from `{#ONU_NAME}` during LLD preprocessing.
  - C600 serials can arrive comma-prefixed (for example `1,DD72E68F39E5`). Template preprocessing and backend discovery fallback normalize those rows to the real serial token and discard the numeric prefix.
  - When Zabbix LLD history returns malformed identity rows (for example blank serial plus a suffixed name like `cliente 1`), Varuna now reconciles those rows against current per-ONU status items before persisting inventory. This prevents stale bad names/blank serials from surviving until the next manual rediscovery.
  - Fiberhome can omit `reason_item_key_pattern` (empty string) because status values can directly encode offline reason (`link_loss` / `dying_gasp`) and Zabbix status parsing maps those to `status=offline` with canonical reason.
  - FIT `FNCS4000` uses `collector.type=fit_telnet` plus a fixed EPON interface list (`0/1..0/4`); it does not require Zabbix key metadata.

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
- `topology.0027_seed_zte_c600_profile`: seeds `ZTE / C600` with the C600/C620-specific status map and Zabbix template linkage (`OLT ZTE C600`).
- `topology.0028_update_zte_c600_status_reason_map`: refines the seeded C600 map so Zabbix/Varuna distinguish `link_loss` and `dying_gasp` from generic offline rows.
- `topology.0030_add_fit_telnet_support`: adds Telnet OLT fields and seeds `FIT / FNCS4000` with direct Telnet collection metadata and a slower default power interval (`1800s`).
- `topology.0031_add_blade_ips_to_olt`: adds `OLT.blade_ips` JSONField for multi-blade chassis support (FIT FNCS4000).

Parser supports:
- regex-based index extraction,
- explicit part-position mapping,
- fixed index values (for single-slot models, e.g. `fixed.slot_id=1`),
- legacy ZTE fallback (`pon_numeric.onu_id` with `0x11rrsspp`),
- `pon_resolve: interface_map` — resolves `pon_numeric` (opaque ifIndex) to slot/pon via a PON interface name map built during discovery (used by Huawei, where ONU SNMP index is `{pon_ifindex}.{onu_id}`),
- `index_from: oid_columns` — slot/pon resolved from separate SNMP OID columns (`discovery.onu_slot_oid`/`discovery.onu_pon_oid`), onu_id extracted from flat integer index via configurable method (`onu_id_extract: byte2`). Used by Fiberhome where the SNMP index is a flat integer with byte layout `[slot_enc, pon_enc, onu_id, 0]`.

## OLT Availability State
`OLT` now tracks runtime connectivity:
- `collector_reachable`
- `last_collector_check_at`
- `last_collector_error`
- `collector_failure_count`
- Legacy API compatibility aliases still expose the same values under `snmp_reachable`, `last_snmp_check_at`, `last_snmp_error`, and `snmp_failure_count`.
- `polling_interval_seconds`
- `power_interval_seconds`
- `discovery_interval_minutes`
- `last_power_at`
- `next_power_at`
- `history_days` (default 7, range 7–30): configurable history retention in days. It drives:
  - frontend Alarm History default window for ONUs of this OLT,
  - Zabbix host macro `{$VARUNA.HISTORY_DAYS}` used by template item `history`.
  Exposed by `OLTSerializer` (read/write), validated by `validate_history_days` plus model validators, and included in `alarm-clients` response per row.

Updated from:
- `collector_check` API action (with legacy `snmp_check` alias),
- discovery command,
- polling command,
- `run_scheduler` periodic collector checks.
- connectivity state is overlaid at response time on cached topology payloads (no topology-cache flush required for pure runtime collector reachability updates).

`collector_check` is mode-aware:
- implementation dispatches to the configured collector (Zabbix sentinel or FIT direct collector) and updates the same OLT health fields.
- `snmp_check` remains as a compatibility alias for older callers.

## Cached Topology Counters
To remove repeated heavy aggregate queries from the configuration/topology APIs, topology counters are persisted on:
- `OLT`: `cached_slot_count`, `cached_pon_count`, `cached_onu_count`, `cached_online_count`, `cached_offline_count`, `cached_counts_at`.
- `OLTSlot`: `cached_pon_count`, `cached_onu_count`, `cached_online_count`, `cached_offline_count`.
- `OLTPON`: `cached_onu_count`, `cached_online_count`, `cached_offline_count`.

Counter lifecycle contract:
- Migration `0017_backfill_topology_cached_counts` backfills existing runtime data.
- Discovery and polling commands rebuild counters at the end of each successful non-dry-run OLT pass.
- API serializers read cached counters first and safely fall back to live counts when cache fields are null.
- If a counter rebuild fails after discovery/polling already mutated topology state, Varuna clears cached OLT/slot/PON counters back to `null` so API reads fall back to live aggregates instead of serving stale cached totals.

This keeps API responses consistent while making `/api/olts/` and `include_topology=true` reads cheaper under high ONU volume.

`include_topology=true` serialization path is optimized for high ONU counts:
- ONU nested payload generation is built in a single pass per ONU (`ONUNestedSerializer.to_representation`) instead of multiple per-field method calls.
- Vendor capability checks (for optional OLT RX power) are cached per OLT during serialization.
- This keeps refresh latency stable when topology payloads include thousands of ONUs.

## Settings API Guardrails
The OLT configuration API now enforces strict runtime-safe validation:
- `protocol` is vendor-driven:
  - Zabbix-backed profiles require `snmp`.
  - FIT `FNCS4000` requires `telnet`.
- `snmp_version` is `v2c`-only for SNMP/Zabbix-backed vendors. That restriction now exists in both serializer validation and the model field choices; v3 credentials are not represented in the current runtime contract.
- `snmp_port` must be in `[1, 65535]`.
- Telnet port is per-blade inside `blade_ips` entries (each `{"ip": ..., "port": ...}`); there is no global `telnet_port`.
- `name` is normalized and cannot be empty.
- SNMP vendors require non-empty `snmp_community`.
- FIT `FNCS4000` requires non-empty `telnet_username` and `telnet_password`; those fields are reused as the direct collector credentials for HTTP Basic auth and for Telnet fallback.
- FIT `FNCS4000` also requires at least one explicit `blade_ips` entry, and each blade entry must include both `ip` and `port`. Varuna no longer invents a legacy fallback like `ip_address:23` when blade configuration is missing.
- Duplicate FIT `blade_ips` entries (`same ip + port`) are rejected at the API boundary.
- `blade_ips` is a JSON list of per-blade objects (`{"ip": "...", "port": 55523}`). For FIT, `ip_address` is derived from the first blade only for compatibility/display; runtime collection uses blade IPs directly, and the stored per-blade port remains for explicit Telnet fallback. Changing `blade_ips` resets runtime connectivity state.
- Blank password updates preserve the currently stored Telnet/UNM secret instead of clearing it.
- Intervals must be positive and bounded:
  - `discovery_interval_minutes` <= `10080` (7 days)
  - `polling_interval_seconds` <= `604800` (7 days)
  - `power_interval_seconds` <= `604800` (7 days)

Create semantics were also hardened:
- Creating an OLT with the same name as an inactive OLT reactivates that record instead of failing or creating duplicates.
- Reactivation resets runtime health/scheduling fields so discovery/polling restarts from a clean state.

## Collector Runtime
- Direct backend SNMP polling/walk code was removed from runtime services and commands.
- Runtime is collector-aware:
  - Zabbix vendors use Zabbix API for discovery, history lookups, and manual upstream actions.
  - Zabbix vendors use direct PostgreSQL latest-item reads and power history reads when `ZABBIX_DB_ENABLED=1`; there is no JSON-RPC API fallback -- if the DB read fails, the service logs an error and returns empty results.
  - FIT `FNCS4000` uses direct HTTP reads from the device web UI for discovery/status and per-ONU power by default. Explicit `collector.transport=telnet` keeps the legacy CLI path available.
- Manual and scoped refresh paths request immediate upstream execution only on Zabbix-backed vendors.
- First-ever discovery (no ONUs exist yet) automatically triggers upstream Zabbix LLD execution (`refresh_upstream`) on Zabbix-backed vendors so newly created OLTs don't wait for the Zabbix LLD schedule.
- Failed discovery retries in 2 minutes (not the full discovery interval) so transient upstream delays don't block topology for hours.
- Empty discovery rows no longer mark the OLT unreachable; reachability is determined solely by the active collector check.
- Zabbix discovery upstream refresh (`discover_onus --refresh-upstream`) uses a short retry window before failing empty:
  - `ZABBIX_DISCOVERY_REFRESH_WAIT_SECONDS` (default `15`)
  - `ZABBIX_DISCOVERY_REFRESH_WAIT_STEP_SECONDS` (default `2`)
  This reduces false "no ONUs discovered" results when Zabbix LLD item creation lags a few seconds after execution request.
- FIT `FNCS4000` collector contract:
  - discovery queries the configured EPON interfaces (`0/1..0/4` by default) on each blade IP, but only materializes slots/PONs that actually return ONUs. Empty branches are not kept active in topology.
  - default HTTP discovery/status source is `onuOverview.asp?oltponno=0/x`; when a blade exposes `onuAllPonOnuList.asp`, Varuna uses that blade-wide page first because it includes status plus inline optics for all ONUs at once. Detail/power fallback source is `onuConfig.asp?onuno=0/x:y&oltponno=0/x`.
  - discovery keeps only **authorized** ONUs from FIT HTTP overview rows (`Activate` column). Unauthorized rows are ignored and therefore do not keep PON/slot branches active.
  - multi-blade: each blade IP opens a separate HTTP session (or Telnet session when transport is overridden). Blade index+1 becomes `slot_id`. Slots are named `"Blade N"` when multi-blade and only appear when at least one ONU is discovered on that blade.
  - `snmp_index` format: `"{slot_id}/{interface}:{onu_id}"` (e.g. `"2/0/3:7"`). Includes slot_id prefix to avoid UniqueConstraint collision across blades.
  - status polling reads only the interfaces (PONs) that currently have active ONUs in Varuna topology, reducing routine polling latency.
  - ONU identity: `OLT + slot_id + PON + ONU ID`;
  - name comes from CLI/HTTP when present and is allowed to stay blank;
  - FIT discovery may clear a stored ONU name only when the current collector row is explicitly blank; an unchanged non-empty incoming name must never be wiped during rediscovery;
  - serial is normalized from the FIT MAC address when available, but only as an internal identity surrogate because the device does not expose a proper serial contract in these pages;
  - FIT-facing API payloads (`topology`, `power-report`, `alarm-clients`, `alarm-history`) must not expose that MAC surrogate as `serial`; they serialize it as empty so the frontend can render a plain `-` placeholder instead of a misleading pseudo-serial;
  - power source prefers inline optical values from the HTTP “All ONU” page when the blade firmware exposes it, then falls back to per-PON overview tables and finally per-ONU detail pages for missing optics. Telnet fallback continues to use `show onu optical-ddm epon 0/x <onu_id>`.
  - only ONU RX is collected; OLT RX is always `null`;
  - power collection tolerates partial blade failures: if some blades succeed but others fail, the collected results are returned with a warning log. Only when ALL blades fail does the power collection raise `FITCollectorError`. This prevents a single flaky blade from hiding power data for the entire chassis.
  - reachability check tests all blade IPs against a known-good HTTP overview path; any failure marks OLT unreachable;
  - collector failures preserve blade context in error text (`Blade <ip>: ...`) for reachability, discovery, polling, and power reads so multi-blade faults are visible in `last_collector_error` and maintenance output;
  - scoped status/power reads fail closed when a request references a slot without a configured blade entry; Varuna must never silently reuse blade 1 for another slot;
  - an empty FIT status snapshot after a successful collector session fails polling, but it does not mark the OLT unreachable; collector reachability stays `true` and `last_poll_at` remains stale until a usable snapshot lands;
  - offline disconnect reason remains `unknown`.
  - when `collector.transport=telnet`, legacy CLI behavior still applies: login reaches `EPON>` first, the collector must issue `enable`, `show onu info` output paginates with `--- Enter Key To Continue ----`, and telnet power reads remain limited to ONU IDs `<= 64`.

## ONU Lifecycle
`ONU.is_active` is used to keep history without polluting live topology.
- Seen in discovery: `is_active=True`.
- Missing in discovery (when enabled): deactivated immediately from active topology (`disable_lost_after_minutes` is forced to `0` by discovery runtime policy).
- `deactivate_missing` remains enabled.
- `delete_lost_after_minutes` remains optional hard-delete for already inactive ONUs.
- Zabbix-side ONU LLD item prototypes are deleted immediately when no longer discovered (template `lifetime_type: DELETE_IMMEDIATELY`), preventing stale per-ONU item carry-over in collector reads.
- Serial normalization: `_normalize_serial` forces all serials to uppercase and strips sentinel values (`N/A`, `NA`, `NONE`, `NULL`, `--`, `-`) to empty string. This ensures consistent display (no mixed-case hex) and prevents firmware-specific placeholder strings from being stored as real serials. Combined with serial preservation, an ONU returning `"N/A"` keeps its previously discovered real serial.
- Serial normalization now handles comma-suffixed/multi-fragment payloads (common on some ZTE/VSOL returns). Discovery selects the fragment that matches a serial pattern (for example `TPLG-D22D7400,` -> `TPLGD22D7400`; `client.name,TPLG-D22D7400` -> `TPLGD22D7400`) instead of blindly keeping the right side of the comma.
- ONU discovery name normalization also strips malformed trailing numeric suffixes from ZTE-style rows when no valid serial exists, so a broken payload like `alexandre.silva 1` does not become persisted ONU identity.
- Mangled serial recovery: some OLTs return 8-byte raw serials (4 ASCII vendor + 4 binary) that get UTF-8 decoded into garbage text (e.g. `MONU&BY` instead of `MONU26425900`). `_normalize_serial` detects 5-8 char strings with a 4-letter vendor prefix and non-ASCII/non-alphanumeric suffix artifacts, re-encodes them to bytes (with null-byte padding to 8), and delegates to `_decode_hex_serial` for proper conversion. All-ASCII-alphanumeric and length >8 serials are never touched.
- Mangled serial recovery is resilient to non-latin-1 Unicode suffix characters (from UTF-8 decoded raw bytes): fallback UTF-8 re-encoding prevents normalization crashes and still recovers the serial when the byte payload maps to the Huawei vendor+hex format.
- SNMP byte parsing preserves exact byte payload on binary fallback (`0x...`), including trailing `00` bytes; this prevents Huawei serial truncation like `TPLGD22D74` (missing `00`) when OCTET STRING values are not UTF-8 text.
- Discovery serial safety: when a discovery run receives partial/empty serial rows (SNMP walk timeout gaps), existing ONU serial values are preserved instead of being overwritten with blank strings.
- Discovery serial self-healing on partial gaps: the preserved existing serial also passes through `_normalize_serial`, so legacy malformed values (for example Huawei mangled text like `MONU&BY`/`CMSZ; 0`) are repaired during discovery even when the current serial walk row is missing.
- Ghost index filtering: SNMP indices where both name and serial are empty/whitespace are filtered out before the `min_safe_ratio` check. This prevents ghost SNMP entries (deregistered ONUs that still appear in walks with empty fields) from inflating the discovered count or being created as phantom ONUs.
- Discovery DB operations use bulk create/update for slot, PON, and ONU upserts to reduce query overhead on large OLTs.
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
- Successful/failed polling runs write directly to `ONU.status` and `ONULog`; topology/status read paths consume those persisted rows directly without a response-cache invalidation step.
- FIT polling optimization: routine status polling scopes Telnet `show onu info` commands to only PONs that have active ONUs in the selected OLT/scope.

## OLT Deletion Contract
`DELETE /api/olts/{id}/` is a soft-deactivation flow:
- OLT is marked `is_active=False`.
- Discovery/polling are disabled.
- Related active slots/PONs/ONUs are deactivated.
- Active ONU offline logs are closed (`offline_until` set).
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
  - discovery/polling jobs do not trust command exit alone; after command execution the runner inspects the resulting OLT health state and marks the job `failed` when the collector/discovery actually failed.
- Timeout and stale-job safety:
  - discovery and polling background jobs run in subprocesses with hard timeouts (`MAINTENANCE_DISCOVERY_TIMEOUT_SECONDS`, `MAINTENANCE_POLLING_TIMEOUT_SECONDS`) so blocked SNMP calls cannot stall the maintenance runner forever.
  - active `running` jobs older than the configured timeout window are auto-expired as `failed` during enqueue/status checks, unblocking new jobs for the same OLT.
  - timeout failures set job detail/error to explicit timeout guidance so operators can fix SNMP parameters and retry.
- `GET /api/olts/{id}/maintenance_status/` returns active/latest job state for frontend progress polling.
- Without `background=true`, actions remain synchronous, but discovery/polling now return `503` when the collector run itself failed even if the management command exited normally.

## Authentication
API uses Django REST Framework `TokenAuthentication`. All endpoints require authentication by default (`DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]`).

Auth endpoints (all under `/api/`):
- `POST /api/auth/login/` — accepts `{username, password}`, returns `{token, user: {id, username, role, can_modify_settings, can_operate_topology}}`. Public (AllowAny).
- `POST /api/auth/logout/` — deletes the user's token. Requires auth.
- `GET /api/auth/me/` — returns `{id, username, role, can_modify_settings, can_operate_topology}` for the authenticated user.
- `POST /api/auth/change-password/` — accepts `{current_password, new_password}`, validates current password, enforces Django password policy, rotates token. Returns new `{token}`.

Frontend stores the token in `localStorage` as `auth_token` and sends it as `Authorization: Token <key>` on every request via an Axios interceptor. On 401 responses, the interceptor clears the stored token, emits the shared auth-clear browser event, and the app returns to the login screen immediately.

Auth views: `backend/topology/api/auth_views.py`.
Auth helpers: `backend/topology/api/auth_utils.py` (`resolve_user_role`, `can_modify_settings`, `can_operate_topology`).
URL routing: `backend/topology/urls.py` (auth paths registered before API includes).

### Role-Based Access Control
Runtime policy is three-role (`admin`, `operator`, `viewer`).
- `admin`: full read/write access to settings, OLT management, OLT maintenance actions, and topology live-refresh actions.
- `operator`: no settings access, but can patch PON descriptions and trigger scoped live status/power refresh from topology view (`batch-status`, `batch-power`, single-ONU refresh with `refresh=true`).
- `viewer`: read-only topology/runtime profile; cannot create/update/delete OLTs, run OLT maintenance actions, patch PON descriptions, or trigger live status/power refresh.

Role resolution (`resolve_user_role`):
1. Superusers always resolve to `admin`.
2. Users with a `UserProfile` use their stored role.
3. Users without a profile default to `viewer`.

Permission enforcement:
- `VendorProfileViewSet` is `ReadOnlyModelViewSet` (no create/update/delete).
- `OLTViewSet` guards `create`, `update`, `destroy`, and all maintenance actions (`run_discovery`, `run_polling`, `collector_check`/`snmp_check`, `refresh_power`, `refresh_power_all`) with `can_modify_settings` (admin-only).
- PON `partial_update` (description editing) requires `can_operate_topology` (admin/operator).
- ONU status/power endpoints keep snapshot reads available to all authenticated users, but `refresh=true` on single/scoped refresh actions requires `can_operate_topology` (admin/operator).
- Successful PON description patch is reflected on the next topology read because topology responses are built directly from current DB rows.
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

Flags: `--username`, `--password`, `--role` (`admin`/`operator`/`viewer`), `--superuser`, `--force-password`.
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
- `POST /api/olts/{id}/collector_check/`
- `POST /api/olts/{id}/snmp_check/` (legacy alias)
- `POST /api/olts/{id}/refresh_power/`
- `GET /api/olts/{id}/maintenance_status/`
- `POST /api/olts/refresh_power/`
- `GET /api/onu/`
- `POST /api/onu/{id}/refresh-status/`
- `GET /api/onu/{id}/power/`
- `POST /api/onu/batch-status/`
- `POST /api/onu/batch-power/`

## Topology, Power, and History Read Contract
Operational read paths now use a hybrid model: structure cache for slow-changing inventory, live reads for status and scoped power.
- `GET /api/olts/` remains a direct PostgreSQL read and uses denormalized `cached_*` counter columns when available for OLT summary counts.
- `GET /api/olts/?include_topology=true` and `GET /api/olts/{id}/topology/` reuse a per-OLT Redis structure cache for static inventory only:
  - OLT identity/capability metadata used by topology surfaces,
  - slot/PON ids, keys, names, descriptions,
  - ONU ids, ONU numbers, names, serials, `last_discovered_at`.
- Dynamic topology fields are never served from Redis:
  - `ONU.status`,
  - disconnect reason/window metadata from the active `ONULog`,
  - per-ONU power values/timestamps,
  - live online/offline aggregates.
- Structure cache contract:
  - Redis key namespace is per OLT (`varuna:topology:structure:{olt_id}`).
  - TTL is controlled by `TOPOLOGY_STRUCTURE_CACHE_TTL` (default `43200` seconds).
  - Cache is invalidated on successful discovery, OLT create/update/delete/reactivation, and PON description edits.
  - Cache misses/corrupt entries/Redis outages fail open to live DB rebuilds; topology reads never hard-fail on cache availability.
  - Only populated branches are materialized: PONs with `0` active ONUs and slots with `0` populated PONs are omitted from the payload for every collector type.
- Topology `status` fields exposed by `GET /api/olts/?include_topology=true` and `GET /api/olts/{id}/topology/` follow the same roll-up contract as the UI:
  - PON `status=partial` only when that PON itself mixes online and non-online ONUs;
  - slot/OLT `status=partial` only when at least one direct child is fully offline (`status=offline`);
  - mixed-but-not-fully-offline child branches do not escalate parent status by themselves.
- Topology power fields (`onu_rx_power`, `olt_rx_power`, `power_read_at`) stay present for payload compatibility but default to `null` in topology responses. The topology read path no longer loads full-tree power snapshots.
- `GET /api/onu/{id}/power/`, `POST /api/onu/batch-power/`, and `GET /api/onu/power-report/` are split by mode:
  - `refresh=true`: manual live collection path (same as before) with upstream execution + persistence.
  - `refresh=false`: latest-value read path.
- Latest-value power read path is collector-aware:
  - Zabbix-backed OLTs automatically read the latest power already stored in Zabbix whenever the read-only Zabbix DB alias is enabled (`ZABBIX_DB_ENABLED=1`);
  - otherwise the backend falls back to the fast local `ONU.latest_*` snapshot read;
  - the legacy `POWER_LATEST_READS_USE_ZABBIX=true` switch remains available only as an explicit opt-in for non-DB environments and should not be used as the primary production mode.
- The Zabbix latest-value path is DB-first:
  - current item values come through `ZabbixService.get_items_by_keys()` (`items` + `item_rtdata` + `history_*` on the read-only Zabbix DB alias),
  - if the current power value is invalid/sentinel, Varuna can look for the latest valid history sample through the same read-only Zabbix DB path first and falls back to JSON-RPC history only if the DB read fails.
- Large live latest-power reads can cap history fallback fanout:
  - `POWER_LATEST_READS_HISTORY_FALLBACK_MAX_ITEMS` (default `256`) keeps history fallback enabled for small selections like single ONU / typical PON reads,
  - larger reads skip history fallback and use only current latest values from Zabbix to keep report latency bounded.
  - This means a large `power-report` read can legitimately differ from the latest valid history-backed sample when the current item is invalid but a recent valid history sample exists.
- FIT/Telnet OLTs do not use the Zabbix latest-value path; `refresh=false` continues to read the local snapshot for those OLTs.
- `GET /api/onu/{id}/alarm-history/` remains uncached and Zabbix-first for timelines, with local DB fallback (`ONULog` + `ONUPowerSample`) when Zabbix history is unavailable.
  - Zabbix-backed `power_history` merges nearby ONU Rx and OLT Rx samples into one reading row. Default merge window is derived from `OLT.power_interval_seconds` and capped at `60s`.
  - `ALARM_HISTORY_POWER_MERGE_WINDOW_SECONDS` overrides that pairing window per instance when a wider history-row merge is needed.
  - when `OLT.unm_enabled=true`, alarm history resolves the ONU in UNM inventory by `slot_id + pon_id + onu_id` to obtain `cobjectid`; ONU `name` and `serial` are not used as UNM alarm query keys.
  - UNM alarm `coccurutctime` / `cclearutctime` are treated as UTC columns and then serialized in the UNM source timezone offset, not coerced into the Varuna application timezone.
  - UNM history table layout is schema-dependent: Varuna prefers `alarmdb.t_alarmloghist_merge` when available, otherwise it discovers `alarmdb.t_alarmloghist` / `alarmdb.t_alarmloghist_*` tables at runtime and merges them by `clogid`.
  - UNM alarm table reads intentionally avoid SQL `ORDER BY coccurutctime` and sort in Python after fetch, because some deployed UNM schemas index by `cneid`/time but not `cobjectid`, causing ordered per-ONU queries to time out.
  - if the direct per-ONU UNM query still times out on a given schema, Varuna falls back to bounded recent-window reads (`coccurutctime` range) plus a bounded active-current slice (`cclearutctime IS NULL` on `t_alarmlogcur`) and filters the target `cobjectid` in Python before returning alarm history.
- UNM-backed topology current state is a separate path from UNM history:
  - polling still takes online/offline state from Zabbix status items;
  - for offline ONUs on `OLT.unm_enabled=true`, polling bulk-reads `alarmdb.t_alarmlogcur` (current alarms only) and materializes the newest active ONU alarm into the open `ONULog`;
  - current UNM alarm code `2400` maps to `link_loss`, `2340` maps to `dying_gasp`, and any other current UNM alarm maps to topology `unknown`;
  - when the current UNM alarm has a usable occurrence timestamp, `offline_since`, `disconnect_window_start`, and `disconnect_window_end` are all anchored to that UNM UTC timestamp converted into the UNM source timezone so topology shows the same clock the operator expects from UNM;
  - topology list/detail payloads and `POST /api/onu/batch-status/` serialize those UNM-backed disconnect timestamps in the UNM source offset (not plain UTC) so the frontend can preserve the UNM clock across refreshes;
  - when no usable current UNM alarm is available, topology falls back to Varuna-local offline detection timestamps and `disconnect_reason='unknown'`;
  - `GET /api/onu/{id}/alarm-history/` remains the historical timeline path and does not drive topology current-state timestamps.

`GET /api/olts/?include_topology=true` now also returns:
- protocol/telnet settings needed by the settings panel (`protocol`, `blade_ips`, `telnet_username`, password-configured flags),
- `discovery_interval_minutes`
- `polling_interval_seconds`
- `power_interval_seconds`
- `last_power_at`
- `next_power_at`
- collector health metadata required for gray-state derivation on topology surfaces:
  - `collector_reachable`
  - `last_collector_check_at`
  - `collector_failure_count`
  - `last_collector_error`
  - legacy `snmp_*` aliases remain in the payload for compatibility
- per-ONU disconnection window fields:
  - `disconnect_window_start`
  - `disconnect_window_end`
- per-OLT power capability:
  - `supports_olt_rx_power` (`true` only when vendor template has `power.olt_rx_oid`)

These fields are used by the frontend for stale-data validation and interval-driven refresh behavior.

`GET /api/olts/{id}/topology/` includes the same collector health metadata under `olt`, so fallback detail fetches and list fetches remain behaviorally consistent.

Power refresh contract:
- Power readings displayed in topology sidebar/latest-report surfaces come from the latest synced `ONU` snapshot (`latest_onu_rx_power`, `latest_olt_rx_power`, `latest_power_read_at`), not from historical `ONUPowerSample`.
- Scheduler latest-power sync now respects each OLT `power_interval_seconds` directly for every collector type, including Zabbix-backed OLTs. Varuna no longer overrides a slower configured power cadence with a separate fast-sync cap.
- Scheduled Zabbix power sync is intentionally a light current-item read only. It uses `item.get` latest values to update the local `ONU` snapshot and does not walk Zabbix history in the normal scheduler path.
- Manual/scoped power refresh may still use history fallback for invalid current Zabbix power items (`0`, `-80`, unsupported, or missing clock) after an explicit upstream refresh request, because that is an operator-driven live-read path rather than routine background sync.
- Scheduler snapshot sync preserves an existing online ONU power snapshot when the current Zabbix power item is empty/invalid, so a bad current item does not wipe a previously good snapshot during routine sync. Offline/unknown ONUs still clear their latest snapshot.
- Manual PON power refresh remains a live `refresh=true` read path; it should improve freshness by collecting and persisting a newer sample, not by repopulating cache.
- `POST /api/olts/{id}/refresh_power/` refreshes one OLT collection cycle and updates `last_power_at`/`next_power_at`.
- `POST /api/olts/refresh_power/` executes a full batch refresh across active OLTs and updates schedule fields per OLT.
- Power collection is status-driven:
  - if usable status snapshot is missing (`last_poll_at` absent, stale poll timestamp, `collector_reachable=false`, or ONUs only `unknown`), backend runs `poll_onu_status` before collecting power;
  - the pre-power snapshot check uses the same stale-age rule as polling (`polling_interval_seconds * 3 + ZABBIX_STATUS_STALE_MARGIN_SECONDS`, with the 390-second minimum window);
  - only ONUs with `status=online` are queried for power through Zabbix item keys;
  - ONUs `offline`/`unknown` are intentionally skipped and returned with empty power values plus `skipped_reason`.
- Power refresh responses expose collection accounting:
  - single OLT: `count`, `attempted_count`, `skipped_not_online_count`, `skipped_offline_count`, `skipped_unknown_count`, `collected_count`, `synced_count`, `stored_count`;
  - bulk all OLTs: `total_onu_count`, `total_attempted_count`, `total_skipped_not_online_count`, `total_skipped_offline_count`, `total_skipped_unknown_count`, `total_collected_count`.
- `synced_count` counts ONU rows whose fast latest-power snapshot changed during the run.
- `stored_count` reflects rows that are actually new for `ONUPowerSample` after `(onu, read_at)` dedupe, not just the number of candidate payload rows.
- Power collection reads key-patterned item values from Zabbix (`oid_templates.zabbix.onu_rx_item_key_pattern` / `olt_rx_item_key_pattern`).
- Stale power safety: samples older than `power_interval_seconds * 3 + ZABBIX_POWER_STALE_MARGIN_SECONDS` (default margin `90s`) are discarded for refresh responses.
- Frontend power views do not apply a separate stale classification. Operators infer sample age from the `Leitura` timestamp, while backend discard rules for accepted samples still use the margin-bearing threshold above.
- Power refresh shares the same upstream execution cap (`ZABBIX_REFRESH_UPSTREAM_MAX_ITEMS`) and supports forced bypass via `force_upstream=True` in maintenance runtime for explicit manual refresh flows.
- Scoped/manual topology power refreshes (`GET /api/onu/{id}/power/?refresh=true`, `POST /api/onu/batch-power/` with `refresh=true`) also force upstream execution, so operator-triggered live reads do not silently fall back to stale snapshots on large selections.
- For `refresh_upstream=true`, power refresh now uses the same short upstream wait window as status refresh (`ZABBIX_REFRESH_UPSTREAM_WAIT_SECONDS`, `ZABBIX_REFRESH_UPSTREAM_WAIT_STEP_SECONDS`, `ZABBIX_REFRESH_CLOCK_GRACE_SECONDS`) and retries Zabbix reads before returning.
  This reduces false “online without power” gaps caused by immediate read-after-execute timing races.
- Power history persistence max age is derived from the OLT's `power_interval_seconds` (`max(180, power_interval_seconds * 3 + ZABBIX_POWER_STALE_MARGIN_SECONDS) // 60 + 1` minutes), aligning with the staleness threshold in `power_service.py` so that readings accepted by the staleness check are never silently dropped by the persistence layer. For short intervals (<=30 min) this remains 180 minutes; for daily collection (86400s) it extends to ~4322 minutes.
- Alarm-history Zabbix power timeline merges ONU RX and OLT RX samples inside a short clock window (`5..60s`, derived from OLT power interval) instead of exact-second equality.
  This prevents split/alternating rows when Zabbix stores both metrics a few seconds apart in the same collection cycle.
- Fiberhome Zabbix template payloads are intentionally compacted (`by_onu` short codes and flat `power_data` values) to keep master JSON under Zabbix text item limits (65,535 bytes) on high-density OLTs.
- Fresh power rows are persisted to PostgreSQL (`ONUPowerSample`) during scheduler/manual/scoped power refresh flows for trend/history queries and retained refresh history.
- OLT RX is optional by vendor:
- when `power.olt_rx_oid` is absent, backend collects only ONU RX;
- `olt_rx_power` is returned as `null` and no OLT RX SNMP requests are executed;
- ONU RX parser supports both legacy integer formats and string values like `-27.214(dBm)`.

ONU batch refresh default behavior:
- `POST /api/onu/batch-status/` now defaults to persisted DB/log snapshot reads (`refresh=false` unless explicitly set).
- `POST /api/onu/batch-power/` now defaults to fast local latest-snapshot reads without forced upstream execution or persistence (`refresh=false` unless explicitly set).
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
When `disconnect_reason_oid` is configured (Huawei), both status and disconnect reason are collected before any writes. DB writes include all ONU data in single atomic operations. The serializer also ensures offline ONUs without an active `ONULog` return `disconnect_reason='unknown'` instead of `null`, preventing the frontend from showing a bare "Offline" label.

## Backend Scheduler
The `run_scheduler` management command (`backend/topology/management/commands/run_scheduler.py`) is a long-lived process that periodically dispatches:
- **Collector reachability checks** (run first): every `--collector-check-seconds` (default `30s`), checks OLT availability from the dedicated Zabbix sentinel item and calls `mark_olt_reachable`/`mark_olt_unreachable`.
  - checks are due-aware per OLT (`last_collector_check_at`) and run with a fixed cadence to speed recovery detection when connectivity returns (no exponential backoff delay in runtime loop).
  - reachability logic is sentinel-only:
    - uses `zabbix.availability_item_key` (default `varunaSnmpAvailability`);
    - item must be present, enabled, supported, and fresh (`lastclock` <= `ZABBIX_AVAILABILITY_STALE_SECONDS`, default `45s`);
    - stale sentinel clocks are fail-closed by default; Varuna can force an immediate sentinel execution (`task.create`) and re-check once for fast recovery.
  - scheduler emits per-cycle summary (`checked`, `skipped_not_due`, `reachable`, `unreachable`, elapsed time).
- **Discovery**: `call_command('discover_onus')` — respects per-OLT `_is_due()` logic; skips OLTs with `collector_reachable=False` and `collector_failure_count >= 2`; supports scheduler cap `--max-discovery-olts-per-tick`.
- **Polling**: `call_command('poll_onu_status')` — runs after discovery in the same tick, respects per-OLT `_is_due()` logic, skips OLTs with `collector_reachable=False` and `collector_failure_count >= 2`; supports scheduler cap `--max-poll-olts-per-tick`.
- **Power collection**: checks `next_power_at` or the effective latest-power sync cadence per OLT and collects via `power_service` for due OLTs; skips unreachable OLTs; supports scheduler cap `--max-power-olts-per-tick`.
  - all collector types use the configured `power_interval_seconds` as the scheduler cadence;
  - Zabbix-backed scheduler sync reads only current power items and updates the local latest snapshot without a normal-path history walk;
  - manual/scoped power refresh remains the only path that asks Zabbix for immediate execution and can fall back to recent history when current items are invalid.
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
- Reads power directly from Zabbix item keys for the requested ONU set.
- Discards stale samples beyond the configured freshness window instead of masking them with cached values.
- Keeps empty/stale reads empty, including upstream-forced refreshes, so operators can distinguish “no fresh sample” from a successful live collection.
- Shared hot-path primitive: `ZabbixService.get_items_by_keys()` is used by both `fetch_status_by_index()` and `fetch_power_by_index()`. It reads latest rows exclusively from the read-only Zabbix DB alias in configurable chunks (`ZABBIX_DB_LATEST_ITEMS_CHUNK_SIZE`, default `1000`). There is no JSON-RPC `item.get` fallback -- if the DB read fails, the method logs an error and returns an empty map.

## History Retention and Reporting APIs
- Fast latest-power snapshot model still lives on `ONU` (`latest_onu_rx_power`, `latest_olt_rx_power`, `latest_power_read_at`), but it is no longer the only latest-value source.
- With `POWER_LATEST_READS_USE_ZABBIX=true`, latest-power UI surfaces read current Zabbix latest values directly and use the `ONU.latest_*` snapshot only as a fail-safe fallback on live-read errors or for non-Zabbix collectors.
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
  - `GET /api/onu/power-report/?search=<term>`: flattened power rows per active ONU, with optional backend search on ONU name/serial/OLT name.
    - row contract includes `status` (ONU runtime status), `power_interval_seconds` (OLT power cadence), and topology references (`slot_ref_id`, `pon_ref_id`) so frontend can preserve exact topology path/context for each reading and still expose collection cadence metadata when needed.
    - source contract for `refresh=false` follows the latest-power mode above: snapshot by default, live Zabbix latest values when `POWER_LATEST_READS_USE_ZABBIX=true`.
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
- discovery total index-parse failure guard (when all indices fail `parse_onu_index`, deactivation is skipped, `discovery_healthy` is set to `False`, and OLT stays `collector_reachable` since collector read itself worked),
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
- topology latest-power read paths use the synced `ONU` latest-power snapshot and ignore Redis/runtime cache artifacts,
- power refresh leaves stale/empty reads empty instead of reviving old cached values,
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
- scheduled power sync respects configured `power_interval_seconds`,
- scheduled power sync preserves existing online snapshots when current items are empty/invalid,
- scheduler collector check reachable/unreachable paths,
- scheduler collector check backoff due logic (`_is_collector_check_due`),
- scheduler dispatches polling and discovery commands,
- history/report APIs (`power-report`, `alarm-clients`, `alarm-history`) and `prune_history` retention behavior, alarm-history `start_date`/`end_date` date-range filtering and invalid-date fallback, Zabbix-timeline source selection (`source=zabbix|varuna`) and interval reconstruction,
- serializer returns `unknown` disconnect reason for offline ONUs without active log,
- Fiberhome OID-column index parsing (`index_from: oid_columns` with `column_map` and byte2 onu_id extraction), status mapping (0-3), unmapped status defaults, nameless discovery (empty `onu_name_oid`), OLT Rx index translation (`olt_rx_index_formula: fiberhome_pon_onu`), and total index-parse failure guard (all-skipped preserves existing ONUs).

Files: `backend/topology/tests.py` (entrypoint) and `backend/topology/tests_zabbix_mode.py` (active suite)
