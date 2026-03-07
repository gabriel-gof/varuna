# UNM Settings Panel Aesthetic Improvement

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Polish the UNM integration section in the OLT settings cards so it feels like a first-class, visually distinct integration panel rather than tacked-on fields.

**Architecture:** Pure frontend changes to `SettingsPanel.jsx` and `i18n.js`. The UNM section (both in create-OLT and edit-OLT forms) gets wrapped in a contained card with animated collapse, a proper toggle switch, and logically grouped fields. No backend changes.

**Tech Stack:** React, Tailwind CSS, existing shared components (FieldLabel, FieldInput, SectionLabel)

---

## Current Problems (from screenshot)

1. UNM section visually merges with device fields - only a thin `border-t` separates them
2. When UNM is disabled, 5 empty disabled fields still take up vertical space - visual noise
3. Toggle button is a text pill ("UNM LIGADO") - doesn't match common toggle patterns
4. All 5 fields in a flat grid with no sub-grouping - host/port feels different from user/password
5. Password hint text floats below with no visual treatment

## Design Decisions

- **Contained card**: UNM section gets its own rounded border + subtle background tint, making it visually a "sub-panel" within the General tab
- **Animated collapse**: When `unm_enabled` is false, the fields collapse away with a smooth height transition, leaving only the section header + toggle visible. This reduces noise for the majority of OLTs that don't use UNM
- **Proper toggle switch**: Replace the text pill with a small iOS-style toggle (pure CSS, no library). Keep the "UNM" label to the left and toggle to the right
- **Field grouping**: Split into two visual rows with mini-labels:
  - Row 1 (Connection): Host, Port, MNEID (3-col)
  - Row 2 (Authentication): Username, Password (2-col, password spans remaining space)
- **Password hint**: Inline muted text below password field, same pattern as current but only shown when `unm_password_configured` is true
- **Status dot**: When UNM is enabled AND MNEID is configured, show a small green dot next to the section label as a quick visual indicator

## Scope

- Both places: Create-OLT form (lines ~987-1062) and Edit-OLT form (lines ~1368-1447)
- i18n keys: remove unused ones, add any new ones
- No new components - just restructure JSX and Tailwind classes inline
- No backend changes, no API changes, no new dependencies

---

### Task 1: Build the UNM toggle switch component

**Files:**
- Modify: `frontend/src/components/SettingsPanel.jsx` (add inline toggle near top, before main panel)

**Step 1: Add the UnmToggle inline component**

Add after the `SectionLabel` component (around line 216), before `getOltHealth`:

```jsx
const UnmToggle = ({ enabled, onChange }) => (
  <button
    type="button"
    role="switch"
    aria-checked={enabled}
    onClick={onChange}
    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 ${
      enabled
        ? 'bg-emerald-500'
        : 'bg-slate-200 dark:bg-slate-700'
    }`}
  >
    <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
      enabled ? 'translate-x-[18px]' : 'translate-x-[3px]'
    }`} />
  </button>
)
```

**Step 2: Verify frontend still compiles**

Run: `docker compose -f docker-compose.dev.yml logs frontend --tail=5`
Expected: No build errors

**Step 3: Commit**

```bash
git add frontend/src/components/SettingsPanel.jsx
git commit -m "feat(settings): add UnmToggle switch component"
```

---

### Task 2: Refactor Edit-OLT UNM section with contained card + collapse + toggle

**Files:**
- Modify: `frontend/src/components/SettingsPanel.jsx` (lines ~1368-1447, the edit card's UNM section)

**Step 1: Replace the edit card's UNM section**

Find the block starting at the `<div className="w-full max-w-xl mt-5 pt-4 border-t ...">` inside the edit-OLT `cardTab === 'device'` section (around line 1368) and replace with:

```jsx
                        {/* UNM Integration */}
                        <div className="w-full max-w-xl mt-5">
                          <div className={`rounded-lg border transition-colors duration-200 ${
                            cardEditForm.unm_enabled
                              ? 'border-emerald-200/60 bg-emerald-50/30 dark:border-emerald-500/20 dark:bg-emerald-500/5'
                              : 'border-slate-200 bg-slate-50/50 dark:border-slate-700/60 dark:bg-slate-800/30'
                          }`}>
                            {/* Header row */}
                            <div className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                              <div className="flex items-center gap-2">
                                <SectionLabel>{t('UNM integration')}</SectionLabel>
                                {cardEditForm.unm_enabled && hasDisplayValue(olt.unm_mneid) && (
                                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                                )}
                              </div>
                              <UnmToggle
                                enabled={cardEditForm.unm_enabled}
                                onChange={() => setEditField(oltId, 'unm_enabled', !cardEditForm.unm_enabled)}
                              />
                            </div>

                            {/* Collapsible fields */}
                            <div className={`grid transition-all duration-200 ease-in-out ${
                              cardEditForm.unm_enabled
                                ? 'grid-rows-[1fr] opacity-100'
                                : 'grid-rows-[0fr] opacity-0'
                            }`}>
                              <div className="overflow-hidden">
                                <div className="px-3.5 pb-3.5 pt-1 space-y-3">
                                  {/* Connection: Host, Port, MNEID */}
                                  <div className="grid grid-cols-3 gap-x-3 gap-y-3">
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Host')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={cardEditForm.unm_host}
                                        onChange={(e) => setEditField(oltId, 'unm_host', e.target.value)}
                                        placeholder="192.168.30.101"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Port')}</FieldLabel>
                                      <FieldInput
                                        className="text-center px-1"
                                        type="number"
                                        min={1}
                                        max={65535}
                                        value={cardEditForm.unm_port}
                                        onChange={(e) => setEditField(oltId, 'unm_port', e.target.value)}
                                        placeholder="3306"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('MNEID')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={cardEditForm.unm_mneid}
                                        onChange={(e) => setEditField(oltId, 'unm_mneid', e.target.value)}
                                        placeholder="13172740"
                                      />
                                    </div>
                                  </div>

                                  {/* Auth: Username, Password */}
                                  <div className="grid grid-cols-2 gap-x-3 gap-y-3">
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Username')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={cardEditForm.unm_username}
                                        onChange={(e) => setEditField(oltId, 'unm_username', e.target.value)}
                                        placeholder="unm2000"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Password')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        type="password"
                                        value={cardEditForm.unm_password}
                                        onChange={(e) => setEditField(oltId, 'unm_password', e.target.value)}
                                        placeholder="••••••••"
                                      />
                                      {olt.unm_password_configured && (
                                        <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500 text-center">
                                          {t('Leave blank to keep current password')}
                                        </span>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
```

Key changes:
- Outer wrapper uses `rounded-lg border` with emerald tint when enabled, neutral when disabled
- Toggle is the new `UnmToggle` switch
- Green pulse dot appears when enabled + MNEID configured
- Fields animate in/out using CSS `grid-rows-[1fr]`/`grid-rows-[0fr]` trick
- Field labels simplified: "UNM host" -> "Host", "UNM port" -> "Port", etc. (context is obvious inside the UNM card)
- `disabled` prop removed from inputs - the collapse handles show/hide; when visible, fields are always editable
- Fields use `gap-1` instead of `gap-1.5` for tighter feel inside the sub-card

**Step 2: Verify the edit card renders correctly**

Open browser, expand an OLT card, check General tab. Toggle UNM on/off and verify:
- Smooth collapse animation
- Fields appear/disappear
- Green tint when enabled
- Green dot when MNEID is configured

**Step 3: Commit**

```bash
git add frontend/src/components/SettingsPanel.jsx
git commit -m "feat(settings): contained card + collapse for edit-OLT UNM section"
```

---

### Task 3: Refactor Create-OLT UNM section (same pattern)

**Files:**
- Modify: `frontend/src/components/SettingsPanel.jsx` (lines ~987-1062, the create card's UNM section)

**Step 1: Replace the create card's UNM section**

Find the block starting at `<div className="w-full max-w-xl mt-5 pt-4 border-t ...">` inside `createCardTab === 'device'` and replace with the same contained-card pattern as Task 2, but using `form` / `setField` instead of `cardEditForm` / `setEditField`:

```jsx
                        {/* UNM Integration */}
                        <div className="w-full max-w-xl mt-5">
                          <div className={`rounded-lg border transition-colors duration-200 ${
                            form.unm_enabled
                              ? 'border-emerald-200/60 bg-emerald-50/30 dark:border-emerald-500/20 dark:bg-emerald-500/5'
                              : 'border-slate-200 bg-slate-50/50 dark:border-slate-700/60 dark:bg-slate-800/30'
                          }`}>
                            {/* Header row */}
                            <div className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                              <div className="flex items-center gap-2">
                                <SectionLabel>{t('UNM integration')}</SectionLabel>
                              </div>
                              <UnmToggle
                                enabled={form.unm_enabled}
                                onChange={() => setField('unm_enabled', !form.unm_enabled)}
                              />
                            </div>

                            {/* Collapsible fields */}
                            <div className={`grid transition-all duration-200 ease-in-out ${
                              form.unm_enabled
                                ? 'grid-rows-[1fr] opacity-100'
                                : 'grid-rows-[0fr] opacity-0'
                            }`}>
                              <div className="overflow-hidden">
                                <div className="px-3.5 pb-3.5 pt-1 space-y-3">
                                  {/* Connection: Host, Port, MNEID */}
                                  <div className="grid grid-cols-3 gap-x-3 gap-y-3">
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Host')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={form.unm_host}
                                        onChange={(e) => setField('unm_host', e.target.value)}
                                        placeholder="192.168.30.101"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Port')}</FieldLabel>
                                      <FieldInput
                                        className="text-center px-1"
                                        type="number"
                                        min={1}
                                        max={65535}
                                        value={form.unm_port}
                                        onChange={(e) => setField('unm_port', e.target.value)}
                                        placeholder="3306"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('MNEID')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={form.unm_mneid}
                                        onChange={(e) => setField('unm_mneid', e.target.value)}
                                        placeholder="13172740"
                                      />
                                    </div>
                                  </div>

                                  {/* Auth: Username, Password */}
                                  <div className="grid grid-cols-2 gap-x-3 gap-y-3">
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Username')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={form.unm_username}
                                        onChange={(e) => setField('unm_username', e.target.value)}
                                        placeholder="unm2000"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Password')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        type="password"
                                        value={form.unm_password}
                                        onChange={(e) => setField('unm_password', e.target.value)}
                                        placeholder="••••••••"
                                      />
                                    </div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
```

Note: No green dot on create form (no saved MNEID yet). No password hint (no existing password).

**Step 2: Verify create-OLT form**

Click "+", check General tab, toggle UNM on/off, verify same collapse behavior.

**Step 3: Commit**

```bash
git add frontend/src/components/SettingsPanel.jsx
git commit -m "feat(settings): contained card + collapse for create-OLT UNM section"
```

---

### Task 4: Update i18n keys

**Files:**
- Modify: `frontend/src/i18n.js`

**Step 1: Simplify UNM field labels in both EN and PT**

Since the field labels are now contextual within the UNM card, we use shorter generic labels that already exist (`Host`, `Port`, `Username`, `Password`). Remove the UNM-prefixed duplicates that are no longer referenced:

Remove these keys from both EN and PT sections:
- `'UNM host'`
- `'UNM port'`
- `'UNM user'`
- `'UNM password'`
- `'UNM MNEID'`
- `'Enabled'`
- `'Disabled'`

Add if missing (check first):
- `'Host'` -> PT: `'Host'`
- `'Username'` -> PT: `'Usuário'`
- `'Password'` -> PT: `'Senha'`
- `'MNEID'` -> PT: `'MNEID'`

Keys that should already exist and stay: `'UNM integration'`, `'Port'`, `'Leave blank to keep current password'`

**Step 2: Verify no missing translation warnings in browser console**

Open browser, toggle UNM, check console for i18next warnings.

**Step 3: Commit**

```bash
git add frontend/src/i18n.js
git commit -m "chore(i18n): simplify UNM field labels"
```

---

### Task 5: Update documentation

**Files:**
- Modify: `docs/FRONTEND.md`

**Step 1: Update the UNM settings UI description**

Find the UNM section in FRONTEND.md and update to reflect:
- UNM section is a contained sub-card within the General tab
- Fields collapse when UNM is disabled (animated)
- Toggle is an iOS-style switch
- Green pulse dot indicates active + configured
- Simplified field labels (Host, Port, MNEID, Username, Password)

**Step 2: Commit**

```bash
git add docs/FRONTEND.md
git commit -m "docs: update UNM settings UI description"
```

---

## Version Impact

**PATCH** - visual polish only, no behavior or API changes.
