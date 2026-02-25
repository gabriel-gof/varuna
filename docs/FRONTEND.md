# Frontend Guide

## Scope
The UI remains topology-first. No dashboard page is required for current product scope.

## Dev Runtime
- Frontend Vite dev server runs on port `4000` in Docker development mode.
- Backend API remains on port `8000` and is proxied via `/api`.
- Browser tab title is `Varuna`.

## Structure
- `frontend/src/App.jsx`: app shell, auth state, topology/settings tabs, OLT filter persistence, polling refresh. Nav bar has Topology and Settings buttons grouped on the left; user menu on the right.
- `frontend/src/components/LoginPage.jsx`: login page with token-based authentication.
- `frontend/src/components/VarunaIcon.jsx`: shared Varuna SVG icon component.
- `frontend/src/components/NetworkTopology.jsx`: topology tree and alarm/search/filter interactions.
- `frontend/src/components/SettingsPanel.jsx`: OLT CRUD/configuration UX.
- `frontend/src/services/api.js`: Axios API client with auth token interceptor.
- `frontend/src/utils/stats.js`: ONU status classification helpers.

## Authentication
- App checks for stored token on mount by calling `GET /api/auth/me/`. If valid, user proceeds to the main app. If invalid/missing, the login page is shown.
- Login page sends `POST /api/auth/login/` with username/password, receives a token and user info (including `role` and `can_modify_settings`), stores token in `localStorage` as `auth_token`.
- Logout calls `POST /api/auth/logout/`, clears the token from `localStorage`, and returns to the login page.
- Axios request interceptor attaches `Authorization: Token <key>` header on every request.
- Axios response interceptor clears the stored token on 401 responses (no page reload — React state handles the transition).
- Data-fetching effects (`fetchOlts`, `fetchVendorProfiles`) are guarded with `if (!authToken) return` to prevent 401 loops on unauthenticated state.
- Login page uses the same design language as the main app: emerald accent, VarunaIcon with "VARUNA" text matching the nav header proportions.

### Role-Aware UX
- `canManageSettings` is derived from `authUser?.can_modify_settings` after login/me response.
- Viewers (`can_modify_settings=false`):
  - Settings tab is hidden from nav; if accessed directly, redirected to topology view.
  - Vendor profile fetch is skipped.
  - Auto-maintenance (discovery/polling/power) runs on the backend scheduler.
  - PON sidebar refresh buttons are hidden.
  - OLT creation/editing/deletion is blocked.
- Permission error responses from the backend (`'Insufficient permissions for this action.'`) are translated through `BACKEND_MESSAGE_MAP` and displayed as contextual errors.

## Threshold Control Logic
- The **Threshold Control** uses a single input for the "Normal Limit" (Good -> Warning boundary).
- The "Critical Limit" (Warning -> Critical boundary) is automatically derived as `Normal Limit - 3dB`.
- Input fields accept intermediate typing states (`-`, `-.`) to improve UX for negative values.
- The UI renders a visual gradient bar showing the three zones relative to the current input.

## Live Data Flow
- Topology view fetches OLTs with topology (`/api/olts/?include_topology=true`).
- Settings view prefers lightweight OLT fetches (`/api/olts/`) and preserves previously loaded topology trees in memory, reducing save/action latency on large deployments.
- Refresh periodically.
- Production frontend Nginx preserves incoming `X-Forwarded-Proto` when proxying `/api` and `/admin` to backend so Django security middleware can correctly detect HTTPS behind host-level TLS termination.
- In production compose, frontend also serves `/static` directly from shared volume mount `/var/www/static` (populated by backend `collectstatic`).
- OLT SNMP reachability is derived from backend `snmp_reachable` and `snmp_failure_count` fields (no frontend-side SNMP checks). An OLT is shown as unreachable when `snmp_reachable === false` and `snmp_failure_count >= 2`.
- Both topology list (`/api/olts/?include_topology=true`) and topology detail (`/api/olts/{id}/topology/`) payloads expose the same SNMP health fields so gray-state logic remains consistent on fallback loads.
- Render unreachable or stale OLT nodes in gray.
- When a PON sidebar is open for an OLT in gray state (stale/unreachable), status badges, status dots, power color values, and status-colored placeholder hyphens are all forced to gray to signal that displayed data may be outdated.
- `loading` is only `true` during the initial fetch when no OLT data exists. Background refreshes silently update `olts` state without toggling `loading`, keeping the topology tree mounted. If a background refresh fails, existing data is preserved.
- `fetchOlts` uses request deduplication per request shape (`topology` vs `base`) to avoid redundant loads when multiple triggers fire simultaneously (30s poll timer + settings action + resume-on-focus).
- Settings mutations (`updateOlt`, `deleteOlt`) trigger `fetchOlts` without `await`, so the success toast shows immediately and the topology refreshes silently in the background (same pattern as `createOlt`).
- Topology OLT filter (`selectedOltIds`) is lifted to `App.jsx` and persisted in `localStorage` (`varuna.selectedOltIds`). On first load with no saved selection, all OLTs are selected. Invalid IDs are pruned when the OLT list changes. The filter survives tab switches between Topology and Settings.
- Selected topology context (active PON) and selected settings context (active OLT card) are persisted in `localStorage`.
- Search match selection (ONU highlight) is persisted in `localStorage` (`varuna.searchMatch`) with full context (ponId, onuId, serial, clientName, oltId, slotId, searchTerm). On reload, the tree expands to the matched ONU, the PON panel opens, and the highlight + scroll-into-view re-apply once data loads. The local `searchTerm` state syncs with `selectedSearchMatch` changes so the input always reflects the active filter state (clearing when the match is dismissed, restoring when remounting after a tab switch).
- Topology search suggestions are deduplicated by serial (when present) so the same ONU is shown once even if backend topology temporarily contains multiple rows for that serial; the UI keeps the best candidate by match score and live status (`online` > `offline` > `unknown`).
- Client search does not filter the topology tree while typing — the tree stays unchanged until a suggestion is selected. On selection, the tree pins to the exact OLT/slot/PON path containing the matched ONU.
- Alarm filtering is bypassed while a search match is selected, ensuring the searched client PON remains visible even if it fails current alarm thresholds.
- Topology expansion state respects manual operator intent during background refreshes: initial default expansion (first OLT/slot) is applied only once on first data load, and later refreshes do not re-open collapsed nodes. Explicit search selection and alarm-mode auto-expansion may still open nodes by design.
- ONU search highlight is unified across desktop and mobile: `inset 0 0 0 2px` emerald box-shadow for a uniform 2px stroke on all sides, plus a subtle emerald background tint. Desktop table rows drop odd/even striping when highlighted; mobile cards use `border-transparent` so only the inset shadow renders the stroke. Highlight persists across Status/Power tab switches. Scroll-to-highlight uses `querySelectorAll` + `offsetParent` visibility check to target the correct viewport (desktop or mobile), avoiding false matches on CSS-hidden elements.
- Alarm mode state propagation from topology to app shell uses a stable callback and no-op equality guard to avoid render loops (`Maximum update depth exceeded`) during topology view startup.

## Resume-on-Focus Refresh
- When the browser tab regains visibility (via `visibilitychange`, `focus`, or `pageshow` events), the app triggers a topology refresh if the tab was hidden for longer than `RESUME_REFRESH_THROTTLE_MS` (4 seconds).
- This keeps displayed data current after the user switches away from and back to the Varuna tab without requiring manual refresh.

## Freshness and Coherence Rules
- OLT health color is shared between topology and settings views.
- Stale status data is considered unreliable and forced to gray:
  - if `now - last_poll_at > polling_interval_seconds` plus an additional grace window.
  - A minimum tolerance of 10 minutes is enforced so short polling intervals do not cause premature gray state.
  - transient SNMP failures that are below the unreachable threshold still converge to gray when this stale window is exceeded.
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
- OLT and Slot sublabels show a rose-colored alert count (always visible, no toggle needed):
  - OLT: `{slotCount} PLACAS / {redSlots}` — number of slots where all PONs are fully offline (red health).
  - Slot: `{ponCount} PONS / {redPons}` — number of PONs where all ONUs are offline (red health).
  - Alert count only appears when > 0. Gray-tree nodes are excluded.
- Both Status and Power tabs include a pinned footer bar below the scrollable area. Desktop and mobile both show colored reason dots (rose=link loss, blue=dying gasp, purple=unknown) with counts, a vertical separator, then `{total} / {offline}`. Reason dots and separator only appear when offline > 0. Offline count uses amber to distinguish from the rose link-loss dot. Footer uses `bg-white dark:bg-slate-900` with `border-t`. Stats are computed via `getOnuStats(selectedOnus)` memoized as `selectedPonStats`.
- Footer bullets are count-aware on all breakpoints: each bullet (including green `online`) is rendered only when its count is greater than zero.
- Footer separator and total counters use boosted dark-mode contrast (`dark:text-slate-400` for `/`, `dark:text-slate-300` for total) so the `total/offline` segment remains readable in the PON footer.
- OLT interval settings are editable in Settings:
  - `discovery_interval_minutes`
  - `polling_interval_seconds`
  - `power_interval_seconds`
- Discovery, polling, and power collection are scheduled by the backend `run_scheduler` management command. The frontend no longer submits automatic maintenance requests.
- The power panel renders cached power values from topology payload immediately when opening/switching PONs.
- PON sidebar refresh is tab-aware and snapshot-first:
  - `Status`: triggers `POST /api/onu/batch-status/` with `refresh=false` for the selected PON (`olt_id + slot_id + pon_id`) to read the latest backend snapshot without triggering live SNMP.
  - `Potência`: triggers `POST /api/onu/batch-power/` with `refresh=false` for the selected PON (`olt_id + slot_id + pon_id`) to read cached power snapshots without triggering live SNMP.
  - Both paths patch only the selected PON ONU rows in-memory (no forced full-topology reload on success).
  - Explicit collection remains a backend maintenance action (`run_polling` / `refresh_power`) from settings, keeping collection decoupled from topology panel visibility/open state.
- Status table disconnection column is interval-aware:
  - displays a compact single timestamp (`dd/mm/yyyy hh:mm`, locale-aware) using the interval upper bound (`disconnect_window_end`) when backend returns trusted `disconnect_window_start` + `disconnect_window_end`;
  - displays `—` when the exact disconnection window is unknown.
  - when `—` is shown, its color follows the ONU status palette (green/online, red/link loss-offline, blue/dying gasp, purple/unknown); gray-tree rows keep neutral gray.
- Status badge classification treats `disconnect_reason=unknown` (or localized unknown text) as `Unknown` in the UI, even when backend canonical `status` is `offline`.
- PON sidebar refresh has a 5-second cooldown after each collection completes. During cooldown the button is disabled and shows a depleting SVG ring animation around the icon (CSS `cooldown-ring` keyframe in `index.css`). Cooldown resets when the selected PON changes.
- PON sidebar refresh failures are shown inside the sidebar as contextual errors and do not replace the topology tree with a global error banner.
- While power data is being collected, the power table/cards area shows a translucent overlay with a centered spinner. Existing data stays visible underneath for a smooth, non-disruptive loading experience.
- Power tab sorting (`Best/Worst ONU RX`, `Best/Worst OLT RX`) treats missing readings as unavailable and keeps those ONUs after rows with valid numeric dBm values.
- `Best/Worst OLT RX` sort options are shown only when the selected OLT supports OLT RX (`supports_olt_rx_power=true`).
- In Power tab, placeholder hyphens (`—`) follow the same status/disconnection palette as the status table in both `Potência` and `Leitura` columns/rows (green online, rose offline/link loss, blue dying gasp, purple unknown; gray when OLT is gray/stale).
- For vendors without OLT RX support, Power tab renders only ONU RX values (no OLT RX line in desktop/mobile rows).
- During topology reload, if an ONU temporarily arrives without power fields while `last_power_at` has not advanced, the UI keeps the last in-memory ONU power snapshot to avoid false `—` flicker from cache gaps.
- In mobile Power cards, RX lines are left-aligned as compact label/value pairs (`ONU -22.22 dBm`, `OLT -24.71 dBm`) with timestamp rendered on the next line for consistent readability.
- Mobile Power cards vertically center both left identity block and right power/timestamp block for consistent alignment regardless of value presence.
- In PON detail tables (`Status` and `Potência`), the second column label is `Name`/`Nome` because it represents ONU name (not customer account/login).
- Alarm mode no longer injects hidden reason-specific sort modes into the PON table (`link_loss`/`dying_gasp`/`unknown`). Status sorting remains canonical (`Default`, `Offline`, `Online`) to keep dropdown label and row order coherent.
- When alarm mode is enabled, PON status rows default to `Offline` ordering (inactive-first). Selecting a new PON while alarm is already active also resets sort to offline ordering. If specific alarm reasons are selected, those reasons are prioritized only within the offline group; online rows stay last.
- Alarm configuration (enabled, reasons, minCount) is persisted in `localStorage` (`varuna.alarmConfig`) per browser. Defaults: enabled=true, reasons=linkLoss only, minOnus=4. Users can change settings freely; preferences survive page reloads.

## Settings Panel Design
- Multiple OLT cards can be expanded simultaneously. Each expanded card maintains independent tab selection, form state, threshold state, and dirty detection.
- Expanded card IDs are persisted as a JSON array in localStorage (`varuna.settings.expandedOltIds`). Migration from the old single-ID key (`varuna.settings.selectedOltId`) is automatic.
- OLT cards expand to show an always-editable form — no read-only/edit mode toggle.
- Container width: `max-w-2xl` for compact, focused layout.
- **Tabs inside expanded card**: `Device`, `Intervals`, and `Thresholds`.
- Device tab uses a responsive grid (`grid-cols-2` on mobile, `grid-cols-3` on `lg+`):
  1. **Device row**: Name, Vendor, Model.
  2. **Connection row**: IP, SNMP Community, Port.
- Vendor and Model selects use a custom Radix `DropdownMenu` (`FieldSelect` component) matching the sort dropdown pattern from the topology view — portaled content, check indicator for selected item, emerald accent, keyboard navigation. Native `<select>` elements are not used.
- Intervals tab uses a responsive grid (`grid-cols-2` on mobile, `grid-cols-3` on `lg+`) with compound input+button fields for ONU discovery, Status collection, and Power collection.
- Delete button is integrated inside the card header (visible when expanded), not floated outside the card boundary.
- Thresholds tab configures power color-coding:
  - **ONU RX Power**: Normal (dBm) and Critical (dBm) breakpoints.
  - **OLT RX Power**: Normal (dBm) and Critical (dBm) breakpoints (shown only when selected vendor profile supports OLT RX).
  - Color mapping: `green` (>= normal), `yellow` (between normal and critical), `red` (< critical).
  - Default thresholds: Normal = -25 dBm, Critical = -28 dBm.
  - Stored in `localStorage` with global defaults and per-OLT overrides.
  - Saved to `localStorage` on explicit Save button click (not auto-saved); `Reset to defaults` clears per-OLT overrides.
  - Color legend (dots + range text) updates live as thresholds change.
- Action bar at card bottom keeps last-discovery timestamp (left) and Save/Cancel controls (right, shown only when form is dirty).
- Interval inputs accept **Zabbix-style durations**: bare numbers (seconds), or suffixed values (`30s`, `5m`, `1h`, `4h`, `1d`).
  - `parseDuration()` converts input string → seconds; `formatDuration()` converts seconds → human-readable string.
  - Form state stores human-readable strings; save handlers convert back to `discovery_interval_minutes` / `polling_interval_seconds` / `power_interval_seconds` for the API.
- Dirty detection is per-card: each card compares its own `editForm` values against current OLT data with special duration-aware comparison, and also compares its `thresholdForm` against its original snapshot to detect threshold changes. The Save button activates for any change — device fields, intervals, or thresholds. Saving or discarding one card does not affect other expanded cards.
- Cancel/Discard resets a single card's device/interval form and threshold form to their original values without affecting other expanded cards.
- Vendor dropdown re-selection (same vendor) is guarded to prevent Radix `onSelect` from resetting `vendor_profile` and falsely marking the form dirty.
- Card header shows OLT name with metadata subtitle: `{ip}  ·  {vendor}  ·  {model}` on one line (no ONU counts in header). Full device details live in the Device tab form fields.
- OLT cards start collapsed; expanded state is persisted in `localStorage` but no auto-expand on initial load. Background topology refreshes silently update non-dirty forms; mid-edit forms are preserved.
- Error and success messages render in normal document flow between the tab content area and the action bar, with a translucent backdrop blur. They do not overlay tab content (e.g. threshold inputs). Auto-dismiss after 5 seconds.
- Manual interval action buttons (`Run` for discovery/polling/power) are non-blocking:
  - Request payload includes `{ background: true }`.
  - UI shows immediate inline acknowledgment only (queued/already-running), without job progress polling or progress bar animation.
  - Run and Save controls remain available; duplicate submissions are handled by backend `already_running` responses and surfaced as inline messages.
  - When backend returns `detail` (for example, `already_running` due to another maintenance action), frontend translates known backend messages via `translateBackendMessage()`, preferring its own translated strings for queued action responses.
- Number input spinner arrows are hidden via CSS (`appearance: textfield`, `::-webkit-*` pseudo-elements).

## Settings API Contract Expectations
- OLT removal from Settings maps to backend soft-deactivation (not hard delete), so removed OLTs disappear from active UI while history is preserved server-side.
- Save actions can return explicit `400` validation errors for invalid runtime configuration (unsupported SNMP version, invalid intervals/ports, missing required fields).
- Manual action buttons (`Run` for discovery/polling/power) can return explicit `400` errors when the vendor profile lacks required capabilities or OID templates.
- Manual action buttons (`run_discovery`, `run_polling`, `refresh_power`) are acknowledged immediately by backend `202` responses when queued in background mode.
- Frontend translates known backend errors through the i18n system; unknown messages pass through as-is for operator visibility.

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
- Removed unused frontend helpers/exports in topology/settings/power utility modules to reduce dead code without changing layout or interaction design.
- Preserved existing UI design and interaction model.

## Counters Toggle
- A toolbar toggle button (`Hash` icon) between Collapse and Alarm enables an optional `total / online / offline` counter displayed to the right of each node card (OLT, Slot, and PON).
- State is persisted in `localStorage` key `varuna.showPonCounts` (boolean, default `false`).
- When off (default): node cards show no counters.
- When on: OLT and Slot cards show a full breakdown: `online / linkLoss / dyingGasp / unknown | total / offline` (only non-zero values appear, separators inserted dynamically). PON cards show simple `total / offline` since they already display the per-reason breakdown via colored dots inside the card. OLT and Slot counters aggregate all descendant PON ONU stats. Gray-tree nodes (unreachable OLTs) do not show counters.
- Button uses emerald icon color + emerald border when active, neutral slate when off. The pill background stays neutral — only icon color and border color change.
- Mobile: icon-only; desktop: icon + "Counters"/"Contadores" label.
- This toggle is independent of the PON status table footer (which always shows its own summary).

## Toolbar Layout
- The topology area has two visual rows: a single toolbar row and the container surface below it.
- The toolbar row contains filter button, search input, and action buttons (collapse, counters, alarm) all on one line. Action buttons are pushed to the right via `ml-auto`. All toolbar icon buttons use a neutral pill container (`bg-white dark:bg-slate-800`, `border-slate-200/80 dark:border-slate-700`, `rounded-lg shadow-sm`) matching the PON sidebar button style. The background never changes. State is communicated through icon color (`text-slate-400` default, `text-slate-600` hover, semantic color when active) and a subtle tinted fill — active buttons get `bg-emerald-50 dark:bg-emerald-500/10` (filter/counters) or `bg-rose-50 dark:bg-rose-500/10` (alarm) while the border stays neutral (`border-slate-200/80`). Filter button also activates (green icon + tinted fill) when its dropdown is open.
- The toolbar is inside a sticky wrapper (`sticky top-0 z-20`). The toolbar itself uses `bg-slate-100 dark:bg-slate-950` matching the topology container surface, so white buttons pop against the tinted background (same relationship as the PON sidebar). Below the toolbar, a 32px gradient fade (`h-8 -mb-8`) goes from the surface color to transparent, creating an Apple-style scroll fade where content dissolves as it scrolls under the toolbar. Uses `from-slate-100` (light) / `from-slate-950` (dark) with `pointer-events-none`.
- The search input takes remaining space (`flex-1 min-w-0`) capped at `max-w-[200px]` on mobile and `lg:max-w-[268px]` on desktop, so action buttons have room to breathe.
- Filter and search dropdowns open downward (`top-11`) on all breakpoints.
- Toolbar vertical padding is `pt-4 pb-4`, centering the controls between the nav header and the container surface. Horizontal padding is `px-3` on mobile, `lg:px-8` on desktop — matching the container margins for coherent alignment.

## Topology Container Surface
- The tree content area (OLT/Slot/PON nodes, loading, error, and empty states) is wrapped in a container surface below the action buttons.
- The entire topology section wrapper uses `bg-slate-100 dark:bg-slate-950` so the surface is continuous from toolbar to content with no white gaps. The container div inside inherits the background (no explicit bg). In dark mode, `slate-900/40` provides a subtle lift over the `slate-950` page shell.
- The outer wrapper uses `min-h-full` (not `h-full`) so it fills the viewport at minimum but grows naturally with content. The parent `<section overflow-y-auto>` in App.jsx is the sole scroll container — no nested scroll. This keeps the scrollbar at the section edge, not floating inset.
- The container uses `px-3 lg:px-8` (padding, not margin) matching toolbar padding, so content is inset but the scrollbar stays flush with the section edge.
- Inner content padding is `p-4 lg:p-8 pb-10` (relative to the container, not the page).
- OLT trees are laid out with `flex-wrap` and split horizontal/vertical gaps (`gap-x-10 gap-y-6`).
- Child tree indentation uses `ml-4 pl-8` (16px margin + 32px padding) with a 1.5px left border-line.

## Mobile PON Panel
- The PON detail panel uses responsive CSS (`hidden lg:flex` / `lg:hidden`) to render separate desktop and mobile layouts at the `lg` (1024px) breakpoint.
- Desktop (>=1024px): breadcrumb header uses `py-3.5` with description on a second line below the breadcrumb, matching the vertical presence of the topology toolbar buttons. Table layout with `minWidth: 520px` for both Status and Power tabs.
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
- Desktop PON table rows (Status and Power tabs) use `dark:even:bg-slate-800/50` for visible dark mode row striping against `dark:odd:bg-slate-900`. No hover highlight — rows are read-only data, not interactive targets.

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
- Footer includes safe-area bottom padding (`pb-[calc(0.375rem+env(safe-area-inset-bottom))]`) for notched mobile devices.

## Internationalization (i18n)
- Translation is handled by `react-i18next` configured in `frontend/src/i18n.js`.
- Supported languages: English (`en`) and Brazilian Portuguese (`pt`, default).
- All user-visible strings use `t('key')` lookups; no hardcoded display text in components.
- Backend API messages (errors, validation, queued-action details) stay in English as stable API keys. The frontend maps known backend messages to i18n keys via `translateBackendMessage()` in `App.jsx`.
  - `BACKEND_MESSAGE_MAP`: exact-match lookup for known backend strings.
  - `BACKEND_PREFIX_PATTERNS`: prefix-match for parametric messages (e.g. interval-exceeds-maximum with dynamic values).
  - Unknown backend messages pass through untranslated for operator visibility.
- `getApiErrorMessage()` accepts a `t` function and runs all extracted backend messages through `translateBackendMessage()` before returning.
- Queued settings actions (`runQueuedSettingsAction`) prefer frontend-translated messages over raw backend `detail` strings.
- Settings action messages are OLT-scoped: `settingsActionMessage` is `{ oltId, message }` (or `null`). Each OLT card only shows the message when `oltId` matches; the create card only shows messages with `oltId == null`.

## Missing Serial Highlighting
- ONUs with missing or empty serial values render a normalized placeholder glyph `—` (em dash) in all four table/card contexts: status desktop, status mobile, power desktop, power mobile.
- The missing-serial placeholder color follows the ONU/disconnection status palette:
  - green for online,
  - rose for offline/link loss,
  - blue for dying gasp,
  - purple for unknown,
  - neutral gray for gray-tree (stale/unreachable) context.

## Adaptive Name Column
- The Name column in the PON sidebar (both status and power tabs, desktop and mobile) is automatically hidden when no ONU in the selected PON has a real name (i.e. `client_name` and `name` are both empty).
- This applies to vendors like Fiberhome where SNMP does not expose ONU names. Column space is redistributed to Serial, Status/Power, and Desconexão/Leitura columns.

## Mobile UX
- Viewport meta includes `viewport-fit=cover, maximum-scale=1.0, user-scalable=no` to prevent zoom and handle safe areas on notched devices.
- Input, select, and textarea elements are forced to 16px font on mobile (`max-width: 1023px`) to prevent iOS Safari auto-zoom on focus.
- Search input uses `text-base md:text-[11px]` for readable font on mobile while keeping compact desktop sizing.
- SettingsPanel inputs use the `text-compact` CSS class to opt out of the global 16px `!important` mobile override, keeping compact 11px/12px sizing on all viewports. The viewport meta (`maximum-scale=1.0, user-scalable=no`) already prevents iOS auto-zoom.
- Layout uses `h-[100dvh] min-h-[100dvh]` instead of `h-screen` for correct mobile viewport height on Safari.

## Status Tab Refresh
- When a status refresh is triggered from the PON sidebar, a translucent overlay with a centered spinner covers the status content area during the operation. Existing data stays visible underneath.

## Frontend Health Tests
- Deterministic unit coverage exists in `frontend/src/utils/oltHealth.test.js` (Node test runner).
- Covered behaviors:
  - gray on repeated SNMP failures (`snmp_failure_count >= 2`);
  - transient failures are not immediately gray;
  - stale polling data becomes gray by interval window;
  - `last_discovery_at` fallback drives stale detection when `last_poll_at` is absent.

## Frontend Invariants
- Do not change visual identity without explicit product request.
- Keep API contract-driven rendering.
- Keep topology responsiveness for large ONU lists.
