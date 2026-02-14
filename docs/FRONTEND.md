# Frontend Guide

## Scope
The UI remains topology-first. No dashboard page is required for current product scope.

## Structure
- `frontend/src/App.jsx`: app shell, topology/settings tabs, polling refresh, SNMP checks.
- `frontend/src/components/NetworkTopology.jsx`: topology tree and alarm/search/filter interactions.
- `frontend/src/components/SettingsPanel.jsx`: OLT CRUD/configuration UX.
- `frontend/src/services/api.js`: Axios API client.
- `frontend/src/utils/stats.js`: ONU status classification helpers.

## Live Data Flow
- Fetch OLTs with topology (`/api/olts/?include_topology=true`).
- Refresh periodically.
- Run `snmp_check` per OLT and map to `reachable/unreachable`.
- Render unreachable or stale OLT nodes in gray.

## Freshness and Coherence Rules
- OLT health color is shared between topology and settings views.
- Stale status data is considered unreliable and forced to gray:
  - if `now - last_poll_at > polling_interval_seconds`.
- OLT color semantics follow slot health:
  - `red` when all active slots are offline (`red`),
  - `yellow` when at least one slot is offline (`red`) and at least one other slot is not offline,
  - `green` when all active slots are healthy,
  - `gray` when SNMP is unreachable or status is stale.
- Slot color semantics follow PON health:
  - `red` when all active PONs are fully offline (`red`),
  - `yellow` when at least one active PON is fully offline (`red`) and at least one PON is not fully offline,
  - `green` when all active PONs are healthy.
- PON color semantics follow ONU health:
  - `red` when all ONUs are offline,
  - `yellow` when there is a mix of online and offline ONUs,
  - `green` when all ONUs are online.
- OLT interval settings are editable in Settings:
  - `discovery_interval_minutes`
  - `polling_interval_seconds`
  - `power_interval_seconds`
- Frontend runs due discovery/polling actions based on configured OLT intervals to keep data current while UI is open.
- Power panel auto-refresh uses `power_interval_seconds` of the selected OLT.

## Settings Panel Design
- OLT cards expand to show an always-editable form — no read-only/edit mode toggle.
- Expanded card layout:
  1. **Status bar**: SNMP reachability badge, last discovery timestamp.
  2. **Connection section**: name, IP, community, port, vendor, model inputs.
  3. **Intervals section**: discovery, polling, power interval inputs.
  4. **Action bar**: Delete (left), Run Discovery (right), Save + Cancel (right, shown only when form is dirty).
- Dirty detection compares `editForm` values against current OLT data; Save button appears only when changes exist.
- Card header shows total ONU count with online (green) / offline (red) breakdown.
- `onRunDiscovery` prop triggers `POST /olts/:id/run_discovery/` from App.jsx.

## Refactor Notes
- Removed test/demo topology generator path from runtime.
- Removed stale settings action call to undefined `runSnmpChecks` in component scope.
- Removed unused frontend assets and unused `motion` dependency.
- Preserved existing UI design and interaction model.

## Frontend Invariants
- Do not change visual identity without explicit product request.
- Keep API contract-driven rendering.
- Keep topology responsiveness for large ONU lists.
