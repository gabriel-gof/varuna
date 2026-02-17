# Frontend Guide

## Scope
The UI remains topology-first. No dashboard page is required for current product scope.

## Dev Runtime
- Frontend Vite dev server runs on port `4000` in Docker development mode.
- Backend API remains on port `8000` and is proxied via `/api`.

## Structure
- `frontend/src/App.jsx`: app shell, topology/settings tabs, polling refresh, SNMP checks.
- `frontend/src/components/NetworkTopology.jsx`: topology tree and alarm/search/filter interactions.
- `frontend/src/components/SettingsPanel.jsx`: OLT CRUD/configuration UX.
- `frontend/src/services/api.js`: Axios API client.
- `frontend/src/utils/stats.js`: ONU status classification helpers.

## Threshold Control Logic
- The **Threshold Control** uses a single input for the "Normal Limit" (Good -> Warning boundary).
- The "Critical Limit" (Warning -> Critical boundary) is automatically derived as `Normal Limit - 3dB`.
- Input fields accept intermediate typing states (`-`, `-.`) to improve UX for negative values.
- The UI renders a visual gradient bar showing the three zones relative to the current input.

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
- Container width: `max-w-2xl` for compact, focused layout.
- **Tabs inside expanded card**: `Device`, `Intervals`, and `Thresholds`.
- Device tab uses two sections with equal 3-column layout (`grid-cols-6`):
  1. **Device section** (`col-span-2 / col-span-2 / col-span-2`): Name, Vendor, Model.
  2. **Connection section** (`col-span-2 / col-span-2 / col-span-2`): IP, SNMP Community, Port.
- Intervals tab is grouped in a dedicated timer panel with:
  1. Three side-by-side timer fields (`ONU discovery`, `Status collection`, `Power collection`) with centered duration inputs.
  2. Minimal visual style (no extra icons or nested timer cards) to reduce visual noise.
  3. Three action buttons aligned in a single row under the fields, each labeled `Execute now` and mapped by position to discovery, status polling, and power refresh.
- Thresholds tab configures power color-coding:
  - **ONU RX Power**: Normal (dBm) and Critical (dBm) breakpoints.
  - **OLT RX Power**: Normal (dBm) and Critical (dBm) breakpoints.
  - Color mapping: `green` (>= normal), `yellow` (between normal and critical), `red` (< critical).
  - Default thresholds: Normal = -25 dBm, Critical = -28 dBm.
  - Stored in `localStorage` with global defaults and per-OLT overrides.
  - Auto-saves when all values are valid; `Reset to defaults` clears per-OLT overrides.
  - Color legend (dots + range text) updates live as thresholds change.
- Action bar at card bottom keeps last-discovery timestamp (left) and Save/Cancel controls (right, shown only when form is dirty).
- Interval inputs accept **Zabbix-style durations**: bare numbers (seconds), or suffixed values (`30s`, `5m`, `1h`, `4h`, `1d`).
  - `parseDuration()` converts input string → seconds; `formatDuration()` converts seconds → human-readable string.
  - Form state stores human-readable strings; save handlers convert back to `discovery_interval_minutes` / `polling_interval_seconds` / `power_interval_seconds` for the API.
- Dirty detection compares `editForm` values against current OLT data with special duration-aware comparison.
- Card header shows total ONU count with online (green) / offline (red) breakdown.
- `onRunDiscovery` prop triggers `POST /olts/:id/run_discovery/` from App.jsx.
- Number input spinner arrows are hidden via CSS (`appearance: textfield`, `::-webkit-*` pseudo-elements).

## Settings API Contract Expectations
- OLT removal from Settings maps to backend soft-deactivation (not hard delete), so removed OLTs disappear from active UI while history is preserved server-side.
- Save actions can return explicit `400` validation errors for invalid runtime configuration (unsupported SNMP version, invalid intervals/ports, missing required fields).
- Manual action buttons (`Run` for discovery/polling/power) can return explicit `400` errors when the vendor profile lacks required capabilities or OID templates.
- Frontend should continue surfacing backend `detail` errors directly so operator misconfiguration is visible and actionable.

## Power Threshold Coloring
- Utility: `frontend/src/utils/powerThresholds.js`.
- Power values in the topology power tab are color-coded per OLT thresholds.
- `getPowerColor(value, 'onu_rx'|'olt_rx', oltId)` → `'green'|'yellow'|'red'|null`.
- `powerColorClass(color)` → Tailwind CSS class string for text color.
- Frontend-only storage (`localStorage`); backend fields planned for future phase.

## Refactor Notes
- Removed test/demo topology generator path from runtime.
- Removed stale settings action call to undefined `runSnmpChecks` in component scope.
- Removed unused frontend assets and unused `motion` dependency.
- Preserved existing UI design and interaction model.

## Frontend Invariants
- Do not change visual identity without explicit product request.
- Keep API contract-driven rendering.
- Keep topology responsiveness for large ONU lists.
