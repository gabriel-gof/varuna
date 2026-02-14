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
- Render unreachable OLT nodes in gray.

## Refactor Notes
- Removed test/demo topology generator path from runtime.
- Removed stale settings action call to undefined `runSnmpChecks` in component scope.
- Removed unused frontend assets and unused `motion` dependency.
- Preserved existing UI design and interaction model.

## Frontend Invariants
- Do not change visual identity without explicit product request.
- Keep API contract-driven rendering.
- Keep topology responsiveness for large ONU lists.
