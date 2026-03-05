# Frontend Guide

## Scope
The UI remains topology-first. No dashboard page is required for current product scope.

## Ownership
- Frontend implementation ownership is `Opus`.
- `Opus` must not modify backend/infrastructure/runtime scope (`backend/`, migrations, compose/env, Zabbix runtime integration code).
- `Codex` should only touch frontend when explicitly requested for cross-cutting fixes or backend contract alignment.
- Frontend UX/navigation/search decisions should be executed in Opus sessions and then validated against backend contracts.

## Dev Runtime
- Frontend Vite dev server runs on port `4000` in Docker development mode.
- Backend API remains on port `8000` and is proxied via `/api`.
- Browser tab title is `Varuna`.

## Structure
- `frontend/src/App.jsx`: app shell, auth state, nav tabs (topology/power-report/alarm-history/settings), OLT filter persistence, polling refresh, PON highlight target state. Nav bar layout: logo → data tab buttons (Topology, Power Report, Alarm History) on left → Settings button (admin-only) + user menu on right. No global search bar — each tab has its own inline search.
- `frontend/src/components/LoginPage.jsx`: login page with token-based authentication.
- `frontend/src/components/VarunaIcon.jsx`: shared Varuna SVG icon component.
- `frontend/src/components/NetworkTopology.jsx`: topology tree and alarm/filter interactions. Has inline search in toolbar using `useUniversalSearch` hook; manages its own `selectedClient` state for tree expansion, PON selection, and row highlighting.
- `frontend/src/hooks/useUniversalSearch.jsx`: shared search hook (`normalizeSearch`, `scoreSearchMatch`, `shouldReplaceSearchSuggestion`, `renderHighlightedText`, `useUniversalSearch`) used by NetworkTopology's inline search.
- `frontend/src/components/SettingsPanel.jsx`: OLT CRUD/configuration UX.
- `frontend/src/components/PowerReport.jsx`: network-wide power levels report with sortable/filterable table. Has inline text filter (`searchText`) in toolbar that filters rows by client name or serial.
- `frontend/src/components/AlarmHistory.jsx`: client alarm/power history with disconnection bar chart, optical power line chart, and alerts table. Has self-contained search via debounced `alarm-clients` API; manages its own `selectedClient` state.
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
- Settings tab guard waits for auth bootstrap completion (`authChecked=true`) before enforcing viewer-only restrictions. This preserves the last saved tab (`varuna_active_tab`) across hard refreshes for admin users.
- Viewers (`can_modify_settings=false`):
  - Settings tab is hidden from nav.
  - Vendor profile fetch is skipped.
  - Auto-maintenance (discovery/polling/power) runs on the backend scheduler.
  - PON sidebar refresh is available and can trigger live ONU status/power refresh for the selected PON.
  - PON description is read-only text in the sidebar (no inline edit control).
  - OLT creation/editing/deletion is blocked.
- Admin/operator (`can_modify_settings=true`) can edit PON description inline from the sidebar (desktop and mobile header variants).
- Permission error responses from the backend (`'Insufficient permissions for this action.'`) are translated through the shared frontend error translator (`frontend/src/utils/apiErrorMessages.js`) and displayed as contextual errors.

## Threshold Control Logic
- The **Threshold Control** uses a single input for the "Normal Limit" (Good -> Warning boundary).
- The "Critical Limit" (Warning -> Critical boundary) is automatically derived as `Normal Limit - 3dB`.
- Input fields accept intermediate typing states (`-`, `-.`) to improve UX for negative values.
- The UI renders a visual gradient bar showing the three zones relative to the current input.

## Live Data Flow
- Topology view fetches OLTs with topology (`/api/olts/?include_topology=true`).
- Power Report, Alarm History, and Settings views use lightweight OLT fetches (`/api/olts/`) and avoid topology-tree payloads unless topology tab is active.
- When app starts on a non-topology tab, frontend performs a one-time background topology warm-up fetch so switching to Topology does not wait on first full-tree load.
- Frontend enriches topology rows with per-PON cached stats (`total/online/offline/linkLoss/dyingGasp/unknown`) once per topology refresh to reduce mount-time recomputation.
- When switching to Topology and a cached tree already exists in memory, frontend renders cached topology first and triggers full topology refresh shortly after, reducing perceived tab-switch blocking.
- Deferred full-topology refresh on tab switch is throttled (30s minimum interval) to avoid repeatedly loading large topology payloads during rapid tab changes.
- Refresh periodically.
- Production frontend Nginx preserves incoming `X-Forwarded-Proto` when proxying `/api` and `/admin` to backend so Django security middleware can correctly detect HTTPS behind host-level TLS termination.
- In production compose, frontend also serves `/static` directly from shared volume mount `/var/www/static` (populated by backend `collectstatic`).
- OLT SNMP reachability is derived from backend `snmp_reachable` and `snmp_failure_count` fields (no frontend-side SNMP checks). An OLT is shown as unreachable immediately when `snmp_reachable === false`.
- Both topology list (`/api/olts/?include_topology=true`) and topology detail (`/api/olts/{id}/topology/`) payloads expose the same SNMP health fields so gray-state logic remains consistent on fallback loads.
- Render unreachable or stale OLT nodes in gray.
- When a PON sidebar is open for an OLT in gray state (stale/unreachable), status badges, status dots, power color values, and status-colored placeholder hyphens are all forced to gray to signal that displayed data may be outdated.
- `loading` is only `true` during the initial fetch when no OLT data exists. Background refreshes silently update `olts` state without toggling `loading`, keeping the topology tree mounted. If a background refresh fails, existing data is preserved.
- `fetchOlts` uses request deduplication per request shape (`topology` vs `base`) to avoid redundant loads when multiple triggers fire simultaneously (30s poll timer + settings action + resume-on-focus).
- Auto-refresh is adaptive for recovery:
  - normal cadence: 30s;
  - when any OLT is in gray state (unreachable/stale), cadence temporarily increases to 5s for faster visual recovery after connectivity returns.
- Settings mutations (`updateOlt`, `deleteOlt`) trigger `fetchOlts` without `await`, so the success toast shows immediately and the topology refreshes silently in the background (same pattern as `createOlt`).
- Topology OLT filter (`selectedOltIds`) is lifted to `App.jsx` and persisted in `localStorage` (`varuna.selectedOltIds`). On first load with no saved selection, all OLTs are selected. Invalid IDs are pruned when the OLT list changes. The filter survives tab switches between Topology and Settings.
- Selected topology context (active PON) and selected settings context (active OLT card) are persisted in `localStorage`.
- Theme selection is persisted in `localStorage` (`varuna.theme`). On reload, the app restores the saved `light`/`dark` mode and reapplies the corresponding root `dark` class.
- **Per-Tab Search**: Each monitoring tab has its own inline search, decoupled from the others. Tab switches preserve independent search state.
  - **Topology**: inline search input in toolbar between OLT filter and alarm buttons. Uses `useUniversalSearch` hook for client-side suggestions from in-memory topology. On select, tree expands to matched ONU, PON panel opens with highlight. Locked state shows client name pill with X to clear.
  - **Power Report**: text filter input in toolbar (desktop: between pills and sort; mobile: full-width top row). Filters existing rows by client name or serial. No API call needed.
  - **Alarm History**: search input in toolbar with debounced API lookup (`GET /api/onu/alarm-clients/?search=<term>&limit=7`). On select, loads alarm/power history for that client. Client pill with X to clear.
- Search suggestions (Topology + Alarm History) use compact two-line cards:
  - line 1: ONU name (`-` when unavailable),
  - line 2: `serial · OLT · slot/pon/onu`.
- ONU name rendering in PON Status/Power tables now uses only backend name fields (`client_name`/`name`); UI no longer falls back to login-like fields that can leak numeric ONU identifiers.
- Search suggestions (Topology) are deduplicated by serial (when present) so the same ONU is shown once even if topology temporarily contains multiple rows for that serial; the UI keeps the best candidate by match score and live status (`online` > `offline` > `unknown`).
- Client search does not filter the topology tree while typing — the tree stays unchanged until a suggestion is selected. On selection, the tree pins to the exact OLT/slot/PON path containing the matched ONU.
- Alarm filtering is bypassed while a search match is selected, ensuring the searched client PON remains visible even if it fails current alarm thresholds.
- Topology expansion state respects manual operator intent during background refreshes: initial default expansion (first OLT/slot) is applied only once on first data load, and later refreshes do not re-open collapsed nodes. Explicit search selection and alarm-mode auto-expansion may still open nodes by design.
- Slot and PON child trees are rendered lazily only when their parent node is open, reducing topology tab mount/switch latency on large installations.
- ONU search highlight is unified across desktop and mobile: `inset 0 0 0 2px` emerald box-shadow for a uniform 2px stroke on all sides, plus a subtle emerald background tint. Desktop table rows drop odd/even striping when highlighted; mobile cards use `border-transparent` so only the inset shadow renders the stroke. Highlight persists across Status/Power tab switches. Scroll-to-highlight uses `querySelectorAll` + `offsetParent` visibility check to target the correct viewport (desktop or mobile), avoiding false matches on CSS-hidden elements.
- Alarm mode state propagation from topology to app shell uses a stable callback and no-op equality guard to avoid render loops (`Maximum update depth exceeded`) during topology view startup.

## Resume-on-Focus Refresh
- When the browser tab regains visibility (via `visibilitychange`, `focus`, or `pageshow` events), the app triggers a topology refresh if the tab was hidden for longer than `RESUME_REFRESH_THROTTLE_MS` (4 seconds).
- This keeps displayed data current after the user switches away from and back to the Varuna tab without requiring manual refresh.

## Freshness and Coherence Rules
- OLT health color is shared between topology and settings views.
- During bootstrap with lightweight OLT payloads (`/api/olts/` without topology tree), frontend avoids warning colors (`yellow`/`red`) from aggregate counts only; it keeps reachable OLTs green until full topology data arrives. This prevents transient false alarm flashes on hard refresh.
- Stale status data is considered unreliable and forced to gray:
  - if `last_poll_at` is missing, OLT remains gray (`status_stale`) even when SNMP sentinel is reachable.
  - if `now - last_poll_at > max(polling_interval_seconds * 3 + 90s, 390s)`.
  - OLT returns to active color only after fresh ONU status polling updates `last_poll_at`.
- OLT color semantics follow slot health:
  - `red` when all active slots are `red`,
  - `yellow` when at least one active slot is `red` but not all slots are `red`,
  - `green` when no active slot is `red`,
  - `neutral` only for transitional/no-topology fallback states,
  - `gray` when SNMP is unreachable or status is stale.
- Slot color semantics follow PON health:
  - `red` when all active PONs are `red`,
  - `yellow` when at least one active PON is `red` but not all PONs are `red`,
  - `green` when no active PON is `red`.
- PON color semantics follow ONU health:
  - `red` when all ONUs are confirmed offline (`link loss` / `dying gasp`),
  - `yellow` when at least one ONU is confirmed offline but not all ONUs are confirmed offline,
  - `green` when no ONU is confirmed offline (including unknown-only cases).
- OLT and Slot sublabels show a rose-colored alert count (always visible, no toggle needed):
  - OLT: `{slotCount} PLACAS / {redSlots}` — number of slots where all PONs are fully offline (red health).
  - Slot: `{ponCount} PONS / {redPons}` — number of PONs where all ONUs are offline (red health).
  - Alert count only appears when > 0. Gray-tree nodes are excluded.
- Both Status and Power tabs include a pinned footer bar below the scrollable area. Status footer shows colored reason dots (rose=link loss, blue=dying gasp, purple=unknown) with counts, a vertical separator, then `{total} / {offline}`. Power footer shows signal quality dots (emerald=good, amber=warning, rose=critical, violet=no reading) with counts, a vertical separator, then `{total}`. Footer uses `bg-white dark:bg-slate-900` with `border-t`. Status stats computed via `getOnuStats(selectedOnus)` memoized as `selectedPonStats`; power stats via `powerSignalCounts`.
- Footer bullets are count-aware on all breakpoints: each bullet (including green `online`) is rendered only when its count is greater than zero.
- Footer separator and total counters use boosted dark-mode contrast (`dark:text-slate-400` for `/`, `dark:text-slate-300` for total) so the `total/offline` segment remains readable in the PON footer.
- OLT interval settings are editable in Settings:
  - `discovery_interval_minutes`
  - `polling_interval_seconds`
  - `power_interval_seconds`
  - `history_days`
- Discovery, polling, and power collection are scheduled by the backend `run_scheduler` management command. The frontend no longer submits automatic maintenance requests.
- The power panel renders cached power values from topology payload immediately when opening/switching PONs.
- PON sidebar refresh is tab-aware and live-refresh capable:
  - `Status`: triggers `POST /api/onu/batch-status/` with `refresh=true` for the selected PON (`olt_id + slot_id + pon_id`) to run scoped status polling and return fresh rows.
  - `Potência`: triggers `POST /api/onu/batch-power/` with `refresh=true` for the selected PON (`olt_id + slot_id + pon_id`) to run scoped power collection and return fresh rows.
  - `refresh=true` paths request immediate Zabbix item execution before reading values, so manual PON refresh is near-real-time.
  - when collector connectivity is unavailable for targeted OLTs, scoped refresh endpoints return `503` with `detail`; frontend surfaces this as a contextual sidebar error instead of silently treating stale data as a successful refresh.
  - Both paths patch only the selected PON ONU rows in-memory (no forced full-topology reload on success).
  - OLT-wide maintenance actions (`run_polling` / `refresh_power`) remain available from settings for larger batch operations.
- Status table disconnection column is interval-aware:
  - displays a compact single timestamp (`dd/mm/yyyy hh:mm`, locale-aware) using `disconnect_window_end` with fallback to `disconnect_window_start`;
  - when transition proof is unavailable, backend now returns a detection-point window (`disconnect_window_start == disconnect_window_end == offline_since`), so the table still shows when Varuna confirmed the offline state.
  - when `—` is shown, its color follows the ONU status palette (green/online, red/link loss-offline, blue/dying gasp, purple/unknown); gray-tree rows keep neutral gray.
- Status badge classification treats `disconnect_reason=unknown` (or localized unknown text) as `Unknown` in the UI, even when backend canonical `status` is `offline`.
- PON sidebar refresh has a 5-second cooldown after each collection completes. During cooldown the button is disabled and shows a depleting SVG ring animation around the icon (CSS `cooldown-ring` keyframe in `index.css`). Cooldown resets when the selected PON changes.
- PON sidebar refresh failures are shown inside the sidebar as contextual errors and do not replace the topology tree with a global error banner.
- While power data is being collected, the power table/cards area shows a translucent overlay with a centered spinner. Existing data stays visible underneath for a smooth, non-disruptive loading experience.
- Alarm History power parsing preserves nullable backend values:
  - `null`/empty power values remain `null` in frontend state and render as `—`.
  - Missing metric values are never coerced to `0.00` (prevents false zero readings in history rows/charts).
- Power tab sorting (`Best/Worst ONU RX`, `Best/Worst OLT RX`) treats missing readings as unavailable and keeps those ONUs after rows with valid numeric dBm values.
- All columns (ONU, Name, Serial, ONU RX, OLT RX, Reading) are always shown regardless of OLT vendor capabilities. Missing values display `—`. No conditional column hiding per vendor — consistency over compactness.
- In Power tab, placeholder hyphens (`—`) follow the same status/disconnection palette as the status table in both `Potência` and `Leitura` columns/rows (green online, rose offline/link loss, blue dying gasp, purple unknown; gray when OLT is gray/stale).
- During topology reload, if an ONU temporarily arrives without power fields while `last_power_at` has not advanced, the UI keeps the last in-memory ONU power snapshot to avoid false `—` flicker from cache gaps.
- In mobile Power cards, RX lines are left-aligned as compact label/value pairs (`ONU -22.22 dBm`, `OLT -24.71 dBm`) with timestamp rendered on the next line for consistent readability.
- Mobile Power cards vertically center both left identity block and right power/timestamp block for consistent alignment regardless of value presence.
- Mobile Status and Power cards always render three left-side lines (ONU number, name, serial). When no name exists for an ONU, a dash placeholder is shown. This ensures identical left-column structure between tabs, preventing layout shift when switching between Status and Power.
- In PON detail tables (`Status` and `Potência`), the second column label is `Name`/`Nome` because it represents ONU name (not customer account/login).
- Alarm mode no longer injects hidden reason-specific sort modes into the PON table (`link_loss`/`dying_gasp`/`unknown`). Status sorting remains canonical (`Default`, `Offline`, `Online`) to keep dropdown label and row order coherent.
- When alarm mode is enabled, PON status rows default to `Offline` ordering (inactive-first). Selecting a new PON while alarm is already active also resets sort to offline ordering. If specific alarm reasons are selected, those reasons are prioritized only within the offline group; online rows stay last.
- Alarm configuration (enabled, reasons, minCount) is persisted in `localStorage` (`varuna.alarmConfig`) per browser. Defaults: enabled=true, reasons=linkLoss only, minOnus=4. Users can change settings freely; preferences survive page reloads.

## Settings Panel Design
- Settings is rendered as a modal overlay (not a nav tab). A gear icon button in the navbar opens the modal (visible only for `canManageSettings` users). The modal uses `fixed inset-0 z-[150] bg-black/40 backdrop-blur-[2px]` backdrop with a centered panel (`max-w-[700px] max-h-[85vh] overflow-y-auto rounded-2xl`). Escape key and backdrop click close it.
- Multiple OLT cards can be expanded simultaneously. Each expanded card maintains independent tab selection, form state, threshold state, and dirty detection.
- Expanded card IDs are persisted as a JSON array in localStorage (`varuna.settings.expandedOltIds`). Migration from the old single-ID key (`varuna.settings.selectedOltId`) is automatic.
- OLT cards expand to show an always-editable form — no read-only/edit mode toggle.
- Container width: `max-w-2xl` for compact, focused layout.
- **Tabs inside expanded card**: `Device`, `Intervals`, and `Thresholds`.
- Device tab uses a responsive grid (`grid-cols-2` on mobile, `grid-cols-3` on `lg+`):
  1. **Device row**: Name, Vendor, Model.
  2. **Connection row**: IP, SNMP Community, Port.
- Vendor and Model selects use a custom Radix `DropdownMenu` (`FieldSelect` component) matching the sort dropdown pattern from the topology view — portaled content, check indicator for selected item, emerald accent, keyboard navigation. Native `<select>` elements are not used.
- In PT-BR UI, model labels for vendor profiles with `vendor=FIBERHOME` or `vendor=HUAWEI` are normalized to `UNIFICADO` in Settings cards/forms while keeping the underlying `vendor_profile` ID and backend `model_name` unchanged.
- Intervals tab uses a responsive grid (`grid-cols-2` on mobile, `grid-cols-4` on `lg+`) with:
  - compound input+button fields for ONU discovery, Status collection, and Power collection,
  - a centered `History retention` field displayed as `Nd` token (for example, `7D`) and parsed/clamped to `7..30` days for per-OLT Zabbix history retention.
- Delete button is integrated inside the card header (visible when expanded), not floated outside the card boundary.
- Thresholds tab configures power color-coding:
  - **ONU RX Power**: Normal (dBm) and Critical (dBm) breakpoints.
  - **OLT RX Power**: Normal (dBm) and Critical (dBm) breakpoints (shown only when selected vendor profile supports OLT RX).
  - Color mapping: `green` (>= normal), `yellow` (between normal and critical), `red` (< critical).
  - Default thresholds: Normal = -27 dBm, Critical = -30 dBm.
  - Stored in `localStorage` with global defaults and per-OLT overrides.
  - Saved to `localStorage` on explicit Save button click (not auto-saved); `Reset to defaults` clears per-OLT overrides.
  - Color legend (dots + range text) updates live as thresholds change.
  - Threshold state is always initialized for every expanded OLT card (including cards restored from localStorage after refresh), so the Thresholds tab never renders empty due to missing per-card state.
- Action bar at card bottom keeps last-discovery timestamp (left) and Save/Cancel controls (right, shown only when form is dirty).
- Interval inputs accept **Zabbix-style durations**: bare numbers (seconds), or suffixed values (`30s`, `5m`, `1h`, `4h`, `1d`).
  - `parseDuration()` converts input string → seconds; `formatDuration()` converts seconds → human-readable string.
  - Form state stores human-readable strings; save handlers convert back to `discovery_interval_minutes` / `polling_interval_seconds` / `power_interval_seconds` for the API.
- `history_days` is sent as an integer in OLT create/update payloads and is clamped client-side to `7..30` before API submission.
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
  - When backend returns `detail` (for example, `already_running` due to another maintenance action), frontend translates known backend messages via `translateBackendMessage()` from `frontend/src/utils/apiErrorMessages.js`, preferring its own translated strings for queued action responses.
- Number input spinner arrows are hidden via CSS (`appearance: textfield`, `::-webkit-*` pseudo-elements).

## Settings API Contract Expectations
- OLT removal from Settings maps to backend soft-deactivation (not hard delete), so removed OLTs disappear from active UI while history is preserved server-side.
- Save actions can return explicit `400` validation errors for invalid runtime configuration (invalid intervals/ports, missing required fields).
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
- The toolbar row contains filter button, inline client search input, and action buttons (collapse, counters, alarm) on one line. Action buttons are pushed to the right via `ml-auto`. Search suggestions/dropdown open below the input. All toolbar icon buttons use a neutral pill container (`bg-white dark:bg-slate-800`, `border-slate-200/80 dark:border-slate-700`, `rounded-lg shadow-sm`) matching the PON sidebar button style. The background never changes. State is communicated through icon color (`text-slate-400` default, `text-slate-600` hover, semantic color when active) and a subtle tinted fill — active buttons get `bg-emerald-50 dark:bg-emerald-500/10` (filter/counters) or `bg-rose-50 dark:bg-rose-500/10` (alarm) while the border stays neutral (`border-slate-200/80`). Filter button also activates (green icon + tinted fill) when its dropdown is open.
- The toolbar is inside a sticky wrapper (`sticky top-0 z-20`). The toolbar itself uses `bg-slate-100 dark:bg-slate-950` matching the topology container surface, so white buttons pop against the tinted background (same relationship as the PON sidebar). Below the toolbar, a 32px gradient fade (`h-8 -mb-8`) goes from the surface color to transparent, creating an Apple-style scroll fade where content dissolves as it scrolls under the toolbar. Uses `from-slate-100` (light) / `from-slate-950` (dark) with `pointer-events-none`.
- Filter dropdown opens downward (`top-11`) on all breakpoints.
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
- Sort dropdown uses `w-[130px] lg:w-[136px]` with `h-7` compact styling matching Power Report controls.
- Tab buttons use `min-w-[60px] lg:min-w-[76px]` to prevent toolbar overflow on narrow (<380px) screens.
- Back arrow, X button, sort dropdown, and tab buttons all include `active:scale-[0.97]` tap feedback for consistent press response.
- PON sidebar toolbar controls (tabs, sort, refresh) use `h-7` compact styling with `rounded-md` borders, matching the Power Report toolbar for visual coherence across views.
- Mobile header uses `items-start` so the X button anchors to the breadcrumb line rather than centering against the full breadcrumb+description block.
- Mobile card left columns (status and power) use `gap-0.5` (2px) between ONU number, client name, and serial for readable spacing.
- Desktop PON table rows (Status and Power tabs) use `dark:even:bg-slate-800/50` for visible dark mode row striping against `dark:odd:bg-slate-900`. No hover highlight — rows are read-only data, not interactive targets.

## Dark Mode Border Contrast
- All dark-mode borders use `dark:border-slate-700/50` (not `slate-800`) for visible separation against `slate-900` backgrounds.
- SettingsPanel internal dividers use lighter variants (`dark:border-slate-700/40`, `dark:border-slate-700/30`) proportional to their role as section separators.
- This convention applies across App.jsx, NetworkTopology.jsx, and SettingsPanel.jsx.

## App Footer
- A slim footer is rendered below `<main>` showing the Varuna version (left) and the most recent ONU status collection timestamp (right).
- Version is injected at build time via `__APP_VERSION__`, sourced from the repository root `VERSION` file in `frontend/vite.config.js`.
- Any UI version label (footer, login, future about screens) must use `__APP_VERSION__`; hardcoded version strings are not allowed.
- The timestamp is the latest `last_poll_at` across all OLTs, formatted with `formatReadingAt` for locale awareness.
- The right side is empty when no poll has occurred yet.
- Footer respects dark mode and does not collapse in the flex layout (`shrink-0`).
- Footer includes safe-area bottom padding (`pb-[calc(0.375rem+env(safe-area-inset-bottom))]`) for notched mobile devices.

## Internationalization (i18n)
- Translation is handled by `react-i18next` configured in `frontend/src/i18n.js`.
- Supported languages: English (`en`) and Brazilian Portuguese (`pt`, default).
- All user-visible strings use `t('key')` lookups; no hardcoded display text in components.
- Generic "all" filter labels use concise PT-BR form: `All OLTs` / `All slots` / `All PONs` all translate to `Tudo`. Other generic labels: `Select all` -> `Selecionar tudo`, `All` -> `Todos`.
- Backend API messages (errors, validation, queued-action details) stay in English as stable API keys. The frontend maps known backend messages to i18n keys via `translateBackendMessage()` in `frontend/src/utils/apiErrorMessages.js`.
  - `BACKEND_MESSAGE_MAP`: exact-match lookup for known backend strings.
  - `BACKEND_PREFIX_PATTERNS`: prefix-match for parametric messages (e.g. interval-exceeds-maximum with dynamic values).
  - `BACKEND_REGEX_PATTERNS`: regex-match for dynamic transport/runtime errors (for example, `Timeout while connecting to "10.10.50.2:161".`) and stale-age messages with interpolated seconds.
  - PT-BR wording for timeout is standardized as `Tempo limite esgotado ao tentar conectar a "{{target}}".`.
  - OLT-prefixed errors are translated per segment (`OLT-NAME: <detail>`) so the OLT identifier remains intact while detail text follows selected language (`pt`/`en`).
  - Unknown backend messages pass through untranslated for operator visibility.
- `getApiErrorMessage()` accepts a `t` function and runs all extracted backend messages through `translateBackendMessage()` before returning; this is used by topology/settings actions and login failures.
- Queued settings actions (`runQueuedSettingsAction`) prefer frontend-translated messages over raw backend `detail` strings.
- Settings action messages are OLT-scoped: `settingsActionMessage` is `{ oltId, message }` (or `null`). Each OLT card only shows the message when `oltId` matches; the create card only shows messages with `oltId == null`.

## Missing Serial Highlighting
- ONUs with missing or empty serial values render a normalized placeholder glyph `—` (em dash) in all four table/card contexts: status desktop, status mobile, power desktop, power mobile.
- Missing-serial placeholder typography is kept coherent with disconnection placeholders (`text-[11px]`, `font-semibold`, tabular digits) so both hyphens have the same visual weight/size.
- The missing-serial placeholder color follows the ONU/disconnection status palette:
  - green for online,
  - rose for offline/link loss,
  - blue for dying gasp,
  - purple for unknown,
  - neutral gray for gray-tree (stale/unreachable) context.

## Adaptive Name Column
- The Name column in the PON sidebar is always rendered in both `Status` and `Potência` views (desktop and mobile), even for mixed-vendor OLT selections.
- When an ONU has no name (`client_name`, `login`, `client_login`, and `name` empty), the Name cell renders the placeholder `—` instead of synthetic labels (for example `ONU 12`).
- This applies to vendors like Fiberhome where SNMP does not expose ONU names. Column space is redistributed to Serial, Status/Power, and Desconexão/Leitura columns.

## Mobile UX
- Viewport meta includes `viewport-fit=cover, maximum-scale=1.0, user-scalable=no` to prevent zoom and handle safe areas on notched devices.
- Input, select, and textarea elements are forced to 16px font on mobile (`max-width: 1023px`) to prevent iOS Safari auto-zoom on focus.
- Universal search bar input uses `text-base md:text-[11px]` for readable font on mobile while keeping compact desktop sizing.
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
  - fresh SNMP sentinel alone does not clear stale gray when `last_poll_at` is stale/missing;
  - OLT leaves gray only after fresh ONU status polling (`last_poll_at` inside window).

## Power Report Tab
- Accessible via "Power Report" nav tab (`activeNav === 'power-report'`); available to all roles.
- Component: `frontend/src/components/PowerReport.jsx`.
- Loads flattened ONU rows from `GET /api/onu/power-report/` (latest persisted power sample per active ONU).
- Mount behavior mirrors topology warm-start: when the component has an in-memory snapshot from a previous visit, it renders rows immediately on tab switch and performs the API refresh in background (no blocking first-paint spinner).
- Signal classification uses `getPowerColor` from `powerThresholds.js` — an ONU is "critical" if any reading is red, "warning" if any is yellow, "good" if all are green.
- Default report mode is "problems first": signal filter starts with `Critical + Warning + No reading` and sort starts at `Worst ONU RX`.
- Toolbar is flat on the page surface (no wrapping card), organized in two rows:
  - Row 1: OLT dropdown (160px desktop) + Slot dropdown (80px / 72px desktop) + PON dropdown (80px / 72px desktop) grouped left, Sort dropdown pushed far right via `ml-auto`. All three location dropdowns show "Tudo" (PT) / "All" (EN) when nothing is selected. When a value is selected, the OLT shows its name; Slot/PON show their number.
  - Row 2: signal toggle pills (Good/Warning/Critical/No reading) centered, with inline count badges and a total ONU count (number only, no label) after a divider.
- Signal pills act as both filter toggles and summary indicators. Format: `● SHORT_LABEL COUNT` (e.g. `● GOOD 2963`) using a short all-caps label (`GOOD`/`WARN`/`CRIT`/`N/A` in EN; `NORMAL`/`ALERTA`/`CRITICO`/`N/A` in PT). Desktop and mobile use same `text-[10px]` size and `w-2 h-2` dots; mobile row is `justify-between` with `px-2` inset for even distribution at any count size. Each pill is colored when active, faded when inactive.
- Sort options: `ONU RX ↓`, `OLT RX ↓`, `ONU RX ↑`, `OLT RX ↑`.
- Desktop: split header/body table with 9 `colgroup` columns (OLT, Slot, PON, ONU, Name, Serial, ONU RX, OLT RX, Reading). Compact row height (h-11) with hover highlight. Infinite scroll via `IntersectionObserver` on a sentinel row with explicit `root` set to the scroll container ref — more rows load automatically as the user scrolls near the bottom. Desktop and mobile use separate sentinel/scroll refs since both DOM trees coexist (toggled by CSS).
- Mobile (<1024px): card layout with OLT/Slot/PON path, client info, power values (ONU RX + OLT RX). Same infinite scroll behavior with its own `mobileScrollRef`/`mobileSentinelRef`.
- Row rendering is windowed (initial 300 rows + incremental "Load more") to avoid mounting/unmounting extremely large DOM tables during tab switches.
- Frontend no longer strips sentinel power values (`0`, `-40`) client-side. Sentinel discard is enforced upstream (Zabbix template preprocessing + backend normalization guard), so UI only consumes collector/backend-validated readings.
- All power values are color-coded using existing `powerColorClass` utility.
- Dropdowns use Radix DropdownMenu matching the PON panel sort pattern.
- Background refresh interval remains 30s in-tab.

## Alarm History Tab
- Accessible via "History" nav tab (`activeNav === 'alarm-history'`); available to all roles. Label: "History" (en) / "Histórico" (pt).
- Component: `frontend/src/components/AlarmHistory.jsx`.
- Client selection is done inside the Alarm History toolbar search input. Suggestions are loaded from `GET /api/onu/alarm-clients/` (debounced). Selecting a client loads that client's alarm/power history.
- Empty state shown when no client is selected; vertically centered with `pb-[28vh]` optical lift so the prompt sits well above geometric center. The "Last N days" toolbar label is hidden until a client is selected.
- History window is per-OLT configurable via `OLT.history_days` (default 7, range 7–30). Frontend reads `selectedClient.history_days` (returned by `alarm-clients`) and falls back to 7. Label in toolbar: "Last {{days}} days" (localized).
- Toolbar: shows selected client name (if any) on the left, "Last N days" label on the right. No desktop tab switcher (desktop is always side-by-side).
- Mobile toolbar contains a centered STATUS/POTÊNCIA tab switcher (`flex lg:hidden`) in the middle of the toolbar row.
- Selected client/search is component-local state in AlarmHistory and resets on page reload.
- The `<section>` in App.jsx uses `overflow-hidden` (not `overflow-y-auto`) when `alarm-history` is active. This gives AlarmHistory's `h-full` root a proper viewport-bounded height so the internal grid panels can scroll independently. All other tabs retain `overflow-y-auto` for page-level scroll.
- The inner flex container (`flex-1 flex flex-col`) carries `min-h-0` — required at every flex level so that child `flex-1 min-h-0` elements can be properly bounded and scroll. Without it the container grows unbounded and scroll never triggers.
- **Desktop layout (≥1024px)**: Side-by-side `grid-cols-2 gap-3` grid within `max-w-[1400px]` container.
  - Left card: "Disconnection History" — chart on top (pinned), split header/body table (Event Type, Start, End, Duration) with scrollable body (`flex-1 overflow-y-auto`).
  - Right card: "Power History" — chart on top (pinned), split header/body table (Reading, ONU Rx, OLT Rx) with scrollable body.
  - Grid container has `overflow-hidden` so card heights are properly constrained for independent scroll.
- **Mobile layout (<1024px)**: Single tab-switched card; `activeTab` (`'status'` | `'power'`) controlled by toolbar tab switcher. Card structure: title (pinned) → chart (always rendered, even when empty — consistent with desktop) → column headers (pinned) → rows (scrollable) → footer "Last N days" (pinned). Same table structure as desktop (4 cols for status, 3 cols for power).
- **Mobile Status rows**: Table rows — Event pill | Start | End | Duration (9px font, `h-8`).
- **Mobile Power rows**: Table rows — Reading timestamp | ONU Rx | OLT Rx (9px font, `h-8`, threshold-aware color on values).
- **Disconnection History card**: Bar chart (rose=Link Loss, blue=Dying Gasp, purple=Unknown) with Y-axis step of 2, minimum top of 6. All N days always rendered on x-axis. Bar opacity 0.9 (1.0 on hover). Table shows all alarms newest first; Event badge, Start, End, Duration columns. "Total: N" count uses `t('Total')` (translated).
- **Event type pills**: Match Topology tab style — `ring-1 ring-inset` border, color dot (`w-1.5 h-1.5 rounded-full`) before label. Dying Gasp = blue-50/blue-700/ring-blue-200. Link Loss = rose-50/rose-600/ring-rose-200. Unknown = purple-50/purple-600/ring-purple-200. Mobile column header uses `t('Event')` (shorter) instead of `t('Event Type')`.
- **Power History card**: Column-based chart — each day gets an equal-width column (same geometry as DisconnectionChart), with individual readings plotted at their intra-day position within the column (`toX(ts)` = column start + time-of-day fraction * column width). Data is `sortedPowerAsc` (individual readings sorted oldest-first by timestamp, no day-bucketing/averaging). Day labels at column centers via `xForDay(i)` — visually aligned with disconnection chart labels. ONU Rx `#38bdf8` (light blue), OLT Rx `#1d4ed8` (dark blue). Fixed colors, not health-color-per-segment. Hover finds nearest data point by x-distance linear scan; tooltip shows exact `dd/mm/yy HH:mm` via `formatTimestamp`. No background quality zones — thresholds differ between ONU Rx and OLT Rx so a single-zone overlay would be misleading; health coloring remains in the table via `getPowerColor`/`powerColorClass`. When no power data exists, the chart still renders its grid/axes/legend with no data points (coherent with the disconnection chart's zero-state). Table shows individual power samples sorted newest first. Desktop power table is a clean 3-column layout (`auto | 100px | 100px`); no spacer columns. Value columns are `text-right`.
- **Chart x-axis**: Single row, `dd/mm` format (e.g. `24/02`), 8px bold. No month transition row. `padB = 34`. Label density is adaptive: all labels shown for ≤10 days, every 2nd for 11–20 days, every 4th for >20 days — prevents overlap at 30-day range.
- **Chart legend**: Bar chart (Disconnection) always shows all three reason legend items (Link Loss, Dying Gasp, Unknown) even when counts are zero — faded at `opacity-35` when zero to keep vertical alignment with the Power chart. Line chart (Power) always shows both ONU Rx and OLT Rx legend items. Both use `text-[10px]` labels with `mb-2` bottom margin.
- **Power threshold coloring**: Table values colored via `getPowerColor`/`powerColorClass`. Chart uses fixed light-blue/dark-blue lines with no background quality zones (thresholds differ per signal type so a single overlay would be misleading).
- **Chart hover tooltips**: Both charts have hover interaction. Disconnection chart: invisible full-height rect per day triggers `onMouseEnter`; tooltip shows date + per-reason counts. Power chart: `onMouseMove` on SVG finds nearest data point by x-position distance; dashed vertical cursor line drawn at `toX(data[hoveredIdx].timestamp)`; tooltip shows exact `dd/mm/yy HH:mm` timestamp + ONU/OLT readings. Tooltip position is percentage-based; flips left when `hoveredCssLeft > 60%`.
- Mobile charts fill full container width via `w-full h-auto` SVG scaling — no horizontal scroll needed for 7 days.
- ONU detail data loads from `GET /api/onu/{id}/alarm-history/` with `alarm_days=historyDays`, `power_days=historyDays`, `alarm_limit=1000`, `max_power_points=744`. `historyDays` = `selectedClient.history_days || 7`.
- `alarm-history` response now includes `source` (`zabbix` or `varuna`) so UI/debug tooling can tell whether the timeline came directly from Zabbix history or local fallback rows.
- Power normalization keeps separate `onuRx`/`oltRx` fields and trusts backend/Zabbix filtering.
- Data computed via `useMemo`: `dailyDisconnections` maps all N `lastNDays` (zeros for empty days); `dailyPower` averages `onuRx`/`oltRx` per day (for chart); `sortedPowerHistory` is individual samples sorted newest first (for table).
- Timestamps formatted as `dd/mm/yy HH:mm` (24-hour, no AM/PM) in both disconnection and power tables.

## Frontend Invariants
- Do not change visual identity without explicit product request.
- Keep API contract-driven rendering.
- Keep topology responsiveness for large ONU lists.
