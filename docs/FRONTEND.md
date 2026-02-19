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
- Run `snmp_check` per OLT and map to `reachable/unreachable` with conservative transitions (a single failed check does not immediately flip a previously reachable OLT to unreachable).
- Render unreachable or stale OLT nodes in gray.
- When a PON sidebar is open for an OLT in gray state (stale/unreachable), status badges, status dots, power color values, and offline red-hyphen indicators are all forced to gray to signal that displayed data may be outdated.
- `loading` is only `true` during the initial fetch when no OLT data exists. Background refreshes silently update `olts` state without toggling `loading`, keeping the topology tree mounted. If a background refresh fails, existing data is preserved.
- Topology filter initializes with all OLTs selected only after first non-empty OLT payload (avoids startup state where no OLTs are shown).
- Selected topology context (active PON) and selected settings context (active OLT card) are persisted in `localStorage`.
- Search match selection (ONU highlight) is persisted in `localStorage` (`varuna.searchMatch`) with full context (ponId, onuId, serial, clientName, oltId, slotId, searchTerm). On reload, the tree expands to the matched ONU, the PON panel opens, and the highlight + scroll-into-view re-apply once data loads.
- Topology search suggestions are deduplicated by serial (when present) so the same ONU is shown once even if backend topology temporarily contains multiple rows for that serial; the UI keeps the best candidate by match score and live status (`online` > `offline` > `unknown`).
- Alarm mode state propagation from topology to app shell uses a stable callback and no-op equality guard to avoid render loops (`Maximum update depth exceeded`) during topology view startup.

## Freshness and Coherence Rules
- OLT health color is shared between topology and settings views.
- Stale status data is considered unreliable and forced to gray:
  - if `now - last_poll_at > polling_interval_seconds` plus an additional grace window.
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
- Frontend runs due discovery/polling/power actions based on configured OLT intervals to keep data current while UI is open.
- Due power collection is OLT-scoped (not per selected PON) and uses backend `last_power_at` plus each OLT `power_interval_seconds`.
- The power panel renders cached power values from topology payload immediately when opening/switching PONs.
- The power refresh button triggers a full-batch refresh (`POST /api/olts/refresh_power/`) and enforces a 10-second cooldown to prevent SNMP request bursts from rapid clicks.
- While power data is being collected, the power table/cards area shows a translucent overlay with a centered spinner. Existing data stays visible underneath for a smooth, non-disruptive loading experience.
- Power tab sorting (`Best/Worst ONU RX`, `Best/Worst OLT RX`) treats missing readings as unavailable and keeps those ONUs after rows with valid numeric dBm values.
- In Power tab, rows without power readings show only a hyphen (`—`); for offline statuses the hyphen is red in both `Potência` and `Leitura`, and for online rows it keeps the default neutral style.
- Power refresh execution follows each OLT's configured `power_interval_seconds` while the UI is open; operators can still force an immediate full batch with the refresh button.
- In mobile Power cards, RX lines are left-aligned as compact label/value pairs (`ONU -22.22 dBm`, `OLT -24.71 dBm`) with timestamp rendered on the next line for consistent readability.
- Mobile Power cards vertically center both left identity block and right power/timestamp block for consistent alignment regardless of value presence.
- In PON detail tables (`Status` and `Potência`), the second column label is `Name`/`Nome` because it represents ONU name (not customer account/login).
- Alarm mode no longer injects hidden reason-specific sort modes into the PON table (`link_loss`/`dying_gasp`/`unknown`). Status sorting remains canonical (`Default`, `Offline`, `Online`) to keep dropdown label and row order coherent.
- When alarm mode is enabled, PON status rows default to `Offline` ordering (inactive-first). If specific alarm reasons are selected, those reasons are prioritized only within the offline group; online rows stay last.

## Settings Panel Design
- OLT cards expand to show an always-editable form — no read-only/edit mode toggle.
- Container width: `max-w-2xl` for compact, focused layout.
- **Tabs inside expanded card**: `Device`, `Intervals`, and `Thresholds`.
- Device tab uses a responsive grid (`grid-cols-2` on mobile, `grid-cols-3` on `lg+`):
  1. **Device row**: Name, Vendor, Model.
  2. **Connection row**: IP, SNMP Community, Port.
- Intervals tab uses a responsive grid (`grid-cols-2` on mobile, `grid-cols-3` on `lg+`) with compound input+button fields for ONU discovery, Status collection, and Power collection.
- Delete button is integrated inside the card header (visible when expanded), not floated outside the card boundary.
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

## Mobile Toolbar Layout
- All four toolbar controls (filter, search, collapse, alarm) render in a single row on all breakpoints.
- On mobile (`<lg`), collapse and alarm buttons are icon-only (`h-9 w-9`). On desktop (`>=lg`), they expand to show labels (`lg:w-auto lg:px-3` with `hidden lg:inline` text).
- The search input takes remaining space (`flex-1 min-w-0`) on mobile, capped at `lg:max-w-[268px]` on desktop.
- Filter and search dropdowns open downward (`top-11`) on all breakpoints.
- Toolbar horizontal padding is `px-4` on mobile, `lg:px-10` on desktop, matching the topology content area.

## Mobile PON Panel
- The PON detail panel uses responsive CSS (`hidden lg:flex` / `lg:hidden`) to render separate desktop and mobile layouts at the `lg` (1024px) breakpoint.
- Desktop (>=1024px): unchanged table layout with `minWidth: 520px` for both Status and Power tabs.
- Mobile (<1024px): card-based layout with compact header (back arrow + breadcrumb + PON description nested inside breadcrumb's flex container for natural alignment).
  - Cards use `rounded-md` (6px) inside `rounded-xl` (12px) containers for geometric nesting.
  - Card spacing: `space-y-1.5` (6px) between cards, `py-1.5` vertical padding inside cards.
  - ONU number styled at `text-[12px] font-bold` for clear ID presence.
  - Status cards: offline-since timestamp only renders for non-online ONUs (no `—` on online cards).
  - Power cards right column: ONU RX / OLT RX with color coding + reading timestamp (no status dot).
  - Search highlight is preserved on mobile cards (green border + box-shadow).
  - Empty states use `py-12 text-[12px]` for centered vertical presence.
- The `onSave` handler for PON description editing is shared between desktop and mobile headers.
- Sort dropdown uses `w-[130px] lg:w-[156px]` for narrower mobile fit.
- Tab buttons use `min-w-[72px] lg:min-w-[88px]` to prevent toolbar overflow on narrow (<380px) screens.
- Back arrow, X button, sort dropdown, and tab buttons all include `active:scale-95` tap feedback for consistent press response.
- Mobile header uses `items-start` so the X button anchors to the breadcrumb line rather than centering against the full breadcrumb+description block.
- Mobile card left columns (status and power) use `gap-0.5` (2px) between ONU number, client name, and serial for readable spacing.

## Dark Mode Border Contrast
- All dark-mode borders use `dark:border-slate-700/50` (not `slate-800`) for visible separation against `slate-900` backgrounds.
- SettingsPanel internal dividers use lighter variants (`dark:border-slate-700/40`, `dark:border-slate-700/30`) proportional to their role as section separators.
- This convention applies across App.jsx, NetworkTopology.jsx, and SettingsPanel.jsx.

## App Footer
- A slim footer is rendered below `<main>` showing the Varuna version (left) and the most recent ONU status collection timestamp (right).
- Version is injected at build time via `__APP_VERSION__` (defined in `vite.config.js` from `package.json`).
- The timestamp is the latest `last_poll_at` across all OLTs, formatted with `formatReadingAt` for locale awareness.
- The right side is empty when no poll has occurred yet.
- Footer respects dark mode and does not collapse in the flex layout (`shrink-0`).

## Frontend Invariants
- Do not change visual identity without explicit product request.
- Keep API contract-driven rendering.
- Keep topology responsiveness for large ONU lists.
