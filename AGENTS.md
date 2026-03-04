# Varuna Engineering Charter

This file defines how we build and evolve Varuna. It is a permanent operating guide, not a temporary phase tracker.

## Product Direction
- Varuna is topology-first monitoring for OLT/ONU operations.
- The current product scope has no dashboard module/tab.
- We prioritize fast fault localization over generic charting.

## Naming Contract
- Project/runtime name: `varuna`.
- Backend domain app name: `topology`.
- Do not introduce new backend modules, labels, migrations, or table prefixes named `dashboard`.

## Core Ideas (Always True)
1. Clarity over complexity.
- Prefer simple, explicit flows for discovery, polling, and topology rendering.

2. Topology is the source of operational truth.
- OLT -> Slot -> PON -> ONU hierarchy is first-class in backend and frontend.

3. Vendor behavior is data-driven.
- Vendor differences belong in `VendorProfile.oid_templates`, not hardcoded branches.

4. Fail safely.
- Collector outages must not corrupt ONU state.
- Unreachable OLTs must be explicit and visible (gray/unreachable state).

5. Keep history, but control stale data.
- Use active/inactive lifecycle plus retention windows (Zabbix-style), not immediate hard deletes.

6. Performance is a feature.
- Bulk operations, cached hot reads, and efficient query patterns are required.

7. Zabbix first.
- Always prefer Zabbix as the source for discovery, status, power, timestamps, and retention.
- Add new runtime state in Varuna only when strictly necessary for UI semantics, cache acceleration, or lifecycle guarantees that Zabbix alone cannot provide.
- Do not reimplement SNMP collection logic in Varuna when an equivalent Zabbix path exists.

## Non-Negotiable Rule: Documentation Freshness
Every code change must update documentation in the same work session.

Required updates per change:
- Backend behavior changes: update `docs/BACKEND.md`.
- Architecture/container/runtime changes: update `docs/ARCHITECTURE.md` and `docs/OPERATIONS.md`.
- Frontend behavior/API contract changes: update `docs/FRONTEND.md`.
- Cross-cutting product/LLM context changes: update `docs/LLM_CONTEXT.md`.
- Entry-point or high-level scope changes: update `README.md`.

If no documentation file was changed, the task is not done.

## Runtime Architecture Baseline
Default deployment uses four services:
- `frontend`
- `backend`
- `db` (PostgreSQL)
- `redis`

Notes:
- Discovery and polling run as backend-managed jobs/commands.
- Separate `discovery`/`poller` containers are optional scale optimizations, not default architecture.

## Discovery and Polling Standards

### Zabbix Template-First Data Hygiene (Mandatory)
- Vendor data cleanup must happen in Zabbix templates first (LLD preprocessing + item preprocessing), not in frontend rendering.
- Required Zabbix-side handling for OLT/ONU data:
  - serial normalization (for example comma-suffixed values, bracket/punctuation artifacts),
  - status code mapping (`online` / `link_loss` / `dying_gasp` / `unknown`),
  - power sentinel discard (`0` and `-40` or equivalent invalid ranges),
  - vendor index decomposition needed for `{#SLOT}`, `{#PON}`, `{#ONU_ID}`.
- Frontend rule: only format for display (`—`, localization, typography). Never add vendor-specific parsing/repair logic in UI.
- Backend rule: fallback normalization is allowed only as defensive guard for legacy/missing Zabbix payloads; source of truth remains template output.

### Zabbix Change Workflow (Mandatory)
1. Edit the template YAML in repo root (`fiberhome-template.yaml`, `huawei-template.yaml`, `zte-template.yaml`, `vsol-like-template.yaml`, `snmp-avail-template.yaml`).
2. Import/update the template in Zabbix (same version used by runtime).
3. Run `Execute now` on discovery for the target host and confirm latest LLD rows/macros are correct (`{#SERIAL}`, `{#ONU_NAME}`, `{#ONU_PATH}`).
4. Run status/power item checks and confirm preprocessing outputs expected canonical values.
5. Re-run Varuna discovery/polling and verify topology + power tabs with real data.
6. Add/adjust backend tests for fallback parsing only (not as substitute for template preprocessing).
7. Update docs in the same session (`docs/BACKEND.md`, `docs/OPERATIONS.md`, `docs/LLM_CONTEXT.md` when applicable).
- If LLD item prototypes changed and existing host items keep stale behavior, rebuild affected hosts (delete host in Zabbix, resync from Varuna, rediscover) to force clean item recreation.

### Discovery
- Discovery upserts topology and ONU inventory from vendor templates.
- Missing resources follow retention policy (Zabbix-inspired):
  - `disable_lost_after_minutes`: grace period before marking missing entities inactive.
  - `delete_lost_after_minutes`: optional hard-delete window after inactivity.
- `deactivate_missing=true` controls whether missing resources are lifecycle-managed.

### Polling
- Polling updates runtime ONU status from Zabbix item data.
- Status mapping must use canonical states: `online`, `offline`, `unknown`.
- Offline transitions create/maintain `ONULog`; online transitions close active logs.
- Full collector polling failure marks OLT unreachable and avoids mass false state mutation.

### Unreachable OLT Contract
- Persist runtime health on OLT:
  - `snmp_reachable`
  - `last_snmp_check_at`
  - `snmp_failure_count`
  - `last_snmp_error`
- Frontend must render unreachable OLTs clearly as unavailable.

## Change Checklist (Definition of Done)
A task is complete only when all applicable items are done:
- Code implemented and locally validated.
- Database migrations created/applied if model/schema changed.
- Backend tests added/updated for new behavior.
- Frontend build passes when frontend is touched.
- Documentation updated (mandatory, see above).
- No unused or dead code introduced.

## Extending to New OLT Vendors
1. Add/adjust `VendorProfile.oid_templates` (indexing, discovery, status, power).
2. Validate index parsing and status mapping with tests.
3. Validate discovery and polling commands against representative Zabbix item/discovery outputs.
4. Update docs with vendor-specific notes and constraints.

## Operational Safety
- Always run migrations before validating topology endpoints.
- If container names/services changed, recreate stack:
  - `docker compose -f docker-compose.dev.yml down`
  - `docker compose -f docker-compose.dev.yml up -d --build --force-recreate`
- Never silently ignore repeated collector errors; keep them visible in logs and OLT health fields.

## Versioning and Rollout Policy
- We use Semantic Versioning for delivery tracking: `MAJOR.MINOR.PATCH`.
- Version source of truth is a root `VERSION` file (single line, e.g. `1.4.2`).
- Every code change must include a version impact decision in the same session:
  - `PATCH`: bug fixes, internal hardening, non-breaking behavior corrections.
  - `MINOR`: backward-compatible features, new optional settings, non-breaking API additions.
  - `MAJOR`: breaking API/contract/runtime changes or required operator action that is not backward-compatible.
- Record the impact decision in the work output/PR description (for traceability), even when `VERSION` is not changed yet.
- `VERSION` must be updated only when cutting a release commit, not on every intermediate commit.
- If multiple unreleased changes are grouped, release version bump follows the highest impact among them (`MAJOR` > `MINOR` > `PATCH`).
- Release commits must include the target version in the message (example: `release: v1.4.2`) and should be taggable with the same version.
- Rollout order is canary-first:
  - Deploy and validate on `VIANET` first.
  - Promote the same version to other stacks only after VIANET validation succeeds.
- Do not deploy unversioned code to production stacks.

## Documentation Map
- `README.md`: project overview and quick start.
- `docs/ARCHITECTURE.md`: runtime and design decisions.
- `docs/BACKEND.md`: backend contracts and lifecycle rules.
- `docs/FRONTEND.md`: UI behavior and API usage.
- `docs/OPERATIONS.md`: runbooks and deployment operations.
- `docs/LLM_CONTEXT.md`: concise context for future LLM-assisted development.
