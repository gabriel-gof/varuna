# Varuna Engineering Charter

This file defines how we build and evolve Varuna. It is a permanent operating guide, not a temporary phase tracker.

## Product Direction
- Varuna is topology-first monitoring for OLT/ONU operations.
- The current product scope has no dashboard module/tab.
- We prioritize fast fault localization over generic charting.

## Core Ideas (Always True)
1. Clarity over complexity.
- Prefer simple, explicit flows for discovery, polling, and topology rendering.

2. Topology is the source of operational truth.
- OLT -> Slot -> PON -> ONU hierarchy is first-class in backend and frontend.

3. Vendor behavior is data-driven.
- Vendor differences belong in `VendorProfile.oid_templates`, not hardcoded branches.

4. Fail safely.
- SNMP outages must not corrupt ONU state.
- Unreachable OLTs must be explicit and visible (gray/unreachable state).

5. Keep history, but control stale data.
- Use active/inactive lifecycle plus retention windows (Zabbix-style), not immediate hard deletes.

6. Performance is a feature.
- Bulk operations, cached hot reads, and efficient query patterns are required.

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

### Discovery
- Discovery upserts topology and ONU inventory from vendor templates.
- Missing resources follow retention policy (Zabbix-inspired):
  - `disable_lost_after_minutes`: grace period before marking missing entities inactive.
  - `delete_lost_after_minutes`: optional hard-delete window after inactivity.
- `deactivate_missing=true` controls whether missing resources are lifecycle-managed.

### Polling
- Polling updates runtime ONU status from SNMP using chunked GET operations.
- Status mapping must use canonical states: `online`, `offline`, `unknown`.
- Offline transitions create/maintain `ONULog`; online transitions close active logs.
- Full SNMP polling failure marks OLT unreachable and avoids mass false state mutation.

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
3. Validate discovery and polling commands against representative SNMP responses.
4. Update docs with vendor-specific notes and constraints.

## Operational Safety
- Always run migrations before validating topology endpoints.
- If container names/services changed, recreate stack:
  - `docker compose -f docker-compose.dev.yml down`
  - `docker compose -f docker-compose.dev.yml up -d --build --force-recreate`
- Never silently ignore repeated SNMP errors; keep them visible in logs and OLT health fields.

## Documentation Map
- `README.md`: project overview and quick start.
- `docs/ARCHITECTURE.md`: runtime and design decisions.
- `docs/BACKEND.md`: backend contracts and lifecycle rules.
- `docs/FRONTEND.md`: UI behavior and API usage.
- `docs/OPERATIONS.md`: runbooks and deployment operations.
- `docs/LLM_CONTEXT.md`: concise context for future LLM-assisted development.
