# Varuna Development Progress Tracking

## 📝 Status: Phase 3.5 Complete + SNMP Fix Applied

---

## 🔧 Critical Fix Applied: pysnmp 7.x Compatibility

### Issue
- pysnmp 7.x changed the `UdpTransportTarget` API
- Old API: `UdpTransportTarget((host, port), timeout=2, retries=1)` 
- New API: `await UdpTransportTarget.create((host, port), timeout=2, retries=1)`

### Fix
- Updated [snmp_service.py](backend/dashboard/services/snmp_service.py) `get()` and `walk()` methods
- Now uses the async `create()` factory method for pysnmp 7.x

### Verified Working
- **574 ONUs polled successfully**
- Status Distribution:
  - Online: 406 ONUs
  - Offline: 11 ONUs  
  - Unknown: 157 ONUs
- API returns correct `online_count` and `offline_count`

---

## ✅ Phase 1: Foundation - COMPLETED

### Backend (Django)
- [x] Django project structure created
- [x] PostgreSQL + Redis configuration
- [x] Models: VendorProfile, OLT, ONU, ONULog, UserProfile
- [x] Services: CacheService, SNMPService, TopologyService
- [x] API: OLTViewSet, ONUViewSet, VendorProfileViewSet
- [x] Admin: Ready for management
- [x] Requirements.txt: Django, DRF, PostgreSQL, Redis, PySNMP, mod_wsgi
- [x] **Migrations created** (dashboard/migrations/0001_initial.py)
- [x] Models file: Complete with all 5 models
- [x] Dockerfile: Complete (Python + Apache + mod_wsgi)
- [x] Settings: dev.env, prod.env with all configuration
- [x] Admin: Fixed with explicit imports

### Frontend (Vue 3)
- [x] Vue 3 + Vuetify project structure
- [x] Dependencies: Vue, Router, Pinia, Axios, Vuetify, Vite - **INSTALLED**
- [x] API service: Axios with base URL
- [x] Main App component: Created
- [x] Vite config: Vue Router, Vuetify, proxy
- [x] Dockerfile: Multi-stage (dev, build, nginx production)
- [x] Nginx config: Production configuration
- [x] .dockerignore: Configured

### Docker Configuration
- [x] docker-compose.yml: Base services (db, redis)
- [x] docker-compose.dev.yml: 5 services (db, redis, web, frontend, discovery, poller)
- [x] docker-compose.prod.yml: 3 services (db, redis, web, frontend) with resources
- [x] Apache configs: dev.conf (HTTP), prod.conf (SSL)
- [x] Entrypoint.sh: Complete (PostgreSQL wait, migrations, static, Apache)
- [x] Environment templates: dev.env, prod.env
- [x] Frontend nginx config: Production configuration

---

## ✅ Phase 2: Discovery - COMPLETED

### Priority 1: ZTE Vendor Profile
- [x] Extract OIDs from `olt-zte.yaml` in root directory
- [x] Parse and map ZTE OIDs to JSON structure
- [x] Create ZTE C300 VendorProfile in database
- [x] Test OID template structure with sample data

### Priority 2: Discovery Command
- [x] Create `dashboard/management/commands/discover_onus.py`
- [x] Implement SNMP walk using VendorProfile OID templates
- [x] Parse discovery results
- [x] Update/create ONU records in database
- [x] Update OLT discovery timestamps
- [x] Log discovery results

### Priority 3: Status Polling Command
- [x] Create `dashboard/management/commands/poll_onu_status.py`
- [x] Query active OLTs with polling_enabled=True
- [x] SNMP GET for ONU status
- [x] Detect transitions (online→offline, offline→online)
- [x] Create ONULog entries for offline events
- [x] Cache status in Redis (180s TTL)
- [x] Update ONU status in database
- [x] Update OLT polling timestamps

---

## ✅ Phase 3: Dashboard - COMPLETED

### Priority 1: Frontend Views
- [x] Create `Dashboard.vue`
  - OLT cards summary view
  - Real-time topology display placeholder
  - Statistics overview
- [x] Create `OLTManagement.vue`
  - OLT list with search/filter
  - Add/Edit OLT form
  - Discovery and polling settings
  - Permission check

### Priority 2: Components
- [x] Create `OLTCard.vue`
  - Summary card (name, vendor, status, stats)
  - Online/offline count badges
  - Click to drill down to topology
- [x] Create `SlotPanel.vue`
  - Collapsible expansion panel for slots
- [x] Create `PONPanel.vue`
  - Collapsible expansion panel for PONs
- [x] Create `ONUTable.vue`
  - Table view of ONUs with pagination
  - Status indicators (colors)
  - Sort/filter options
- [x] Create `ONURow.vue`
  - Single ONU row with name, serial, status
  - Disconnect reason badge
  - Power level placeholders

### Priority 3: Services
- [x] Create `dashboard/services/topology.js`
  - Fetch topology from API
  - Build reactive state
  - Provide computed properties for stats
- [x] Create real-time polling (30s interval)

---

## ✅ Phase 3.5: UI Redesign & i18n - COMPLETED

### Priority 1: Internationalization (i18n)
- [x] Create `/src/i18n/index.js` with translation system
- [x] Portuguese (Brazil) as default language
- [x] English translations available
- [x] Language switcher in sidebar footer
- [x] LocalStorage persistence for language preference
- [x] Relative time formatting in both languages

### Priority 2: Hierarchical Topology Components
- [x] Create `TopologyTree.vue` - Main tree container with toolbar
  - Search by ONU name/serial
  - Filter by OLT
  - Show offline only toggle
  - Expand/Collapse all buttons
  - Auto-refresh (30s)
- [x] Create `OltNode.vue` - OLT expandable node
  - Status gradient backgrounds (online/offline/partial/neutral)
  - IP address and vendor display
  - Online/Offline badges with counts
  - Power refresh action button
- [x] Create `SlotNode.vue` - Slot expandable node
  - Slot number and status indicator
  - PON count display
  - Nested PON rendering
- [x] Create `PonNode.vue` - PON expandable node
  - PON number and status indicator
  - ONU count display
  - ONU chip grid rendering
- [x] Create `OnuChip.vue` - ONU display chip
  - Color-coded by status (green=online, red=offline)
  - Disconnect reason colors (purple=dying_gasp, orange=link_loss)
  - Tooltip with full details
  - Search highlight support
- [x] Create `StatusBadge.vue` - Reusable status badge component

### Priority 3: Simplified Navigation
- [x] Remove separate OLT Management page
- [x] Remove Offline ONUs page from nav
- [x] Sidebar with only: "Painel" (Dashboard) + "Configurações" (Settings)
- [x] Settings page with:
  - Language selector
  - Theme toggle (light/dark)
  - Auto-refresh interval slider
  - OLT management table (CRUD)
  - Vendor profiles list
- [x] Legacy route redirects for backward compatibility

### Priority 4: Visual Polish
- [x] Custom Vuetify theme (light and dark modes)
- [x] App bar with theme toggle, notifications, user menu
- [x] Beautiful drawer with brand logo
- [x] Footer with version and live clock
- [x] Card styling with rounded corners and subtle borders
- [x] Responsive design

### Priority 5: Backend API Updates
- [x] `OLTTopologySerializer` - Nested topology serializer
- [x] `ONUNestedSerializer` - ONU with disconnect_reason
- [x] `PONNestedSerializer` - PON with nested ONUs
- [x] `SlotNestedSerializer` - Slot with nested PONs
- [x] `include_topology=true` query parameter support
- [x] Prefetch related objects for performance
- [x] Refresh power endpoint placeholder

---

## 🚧 Phase 4: Power Refresh - PENDING

### Priority 1: Power Service
- [ ] Create `dashboard/services/power.js`
- [ ] Create endpoint: `GET /api/onu/{id}/power/`
- [ ] Create endpoint: `POST /api/onu/batch-power/`
- [ ] Implement SNMP GET for OLT RX and ONU RX power
- [ ] Apply JavaScript normalization from vendor profile
- [ ] Cache power in Redis (60s TTL)
- [ ] Return power values with timestamp

### Priority 2: Frontend Integration
- [ ] Add "Refresh Power" button to ONU table
- [ ] Show power values in ONU row
- [ ] Create `PowerIndicator.vue` component
- [ ] Handle loading states and errors

---

## 🚧 Phase 5: Offline View - IN PROGRESS

### Priority 1: Backend
- [ ] Create endpoint: `GET /api/offline-onus/`
- [ ] Query ONULog for active offline events
- - Sort by disconnect_reason priority
- - Sort by offline_since (most recent first)
- [ ] Add filters: OLT, PON, reason type

### Priority 2: Frontend
- [x] Create `OfflineONUs.vue`
- [x] Table with sorting by reason
- [x] Offline since timestamp
- [x] Disconnect time badge
- [x] Filters (OLT dropdown, reason dropdown)
- [x] Auto-refresh (30s or manual)

---

## 🚧 Phase 6: Polish - PENDING

### Priority 1: Optimization
- [ ] Implement lazy loading
- [ ] Add pagination for large ONUs lists
- [ ] Optimize database queries (select_related, prefetch_related)
- [ ] Add caching for OLT list (Redis, 5min TTL)

### Priority 2: UX Features
- [ ] Global search (ONU name/serial across all OLTs)
- [ ] Show/hide offline ONUs toggle
- [ ] Loading states and error handling (toast notifications)
- [ ] Responsive design improvements

### Priority 3: Production
- [ ] SSL configuration with Let's Encrypt
- [ ] Resource limits testing
- [ ] Health checks validation

---

## 📋 ZTE Status Mapping

From `olt-zte.yaml` ZTE ONU status values:
- **online**: status = 4 (working)
- **offline**: status = 2 (los/link_loss), 5 (dying_gasp)
- **unknown**: status = 1 (logging), 3 (sync_mib), 6 (auth_failed), 7 (offline)

---

## 🐛 Known Issues

### ✅ RESOLVED: pysnmp 7.x API Change
- **Issue**: SNMP polling failed with "AbstractTransportTarget.__init__() got multiple values for argument 'timeout'"
- **Cause**: pysnmp 7.x changed `UdpTransportTarget` to require `await .create()` factory method
- **Fix**: Updated `snmp_service.py` to use `await UdpTransportTarget.create((host, port))` pattern
- **Status**: RESOLVED - Polling now works correctly

### LSP Errors
- **Ignore**: LSP errors are from **Horus** project (not Varuna) and can be safely ignored
- **Ignore**: LSP errors in new files are expected for Django/LSP tools
- **Ignore**: Django default parameter warnings are expected and handled by Django
- **Ignore**: Import path errors are configuration issues, not runtime issues

### npm Vulnerability
- **Moderate**: vite has a moderate vulnerability
- **Fix**: Run `npm audit fix` before production deployment

### Docker Build Issue
- **Status**: Dockerfile exists but Docker can't read it ("open Dockerfile: no such file or directory")
- **Cause**: Likely Docker BuildKit cache or file system issue on macOS
- **Workaround**: Use direct Python server for development (see below)

---

## 🚀 Manual Dev Environment Setup

Since Docker has file system issues, here's how to run the dev environment manually:

### Option 1: Backend Only (Recommended for Development)

```bash
# Terminal 1: Start PostgreSQL with Docker
docker run --name varuna-db -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres -e POSTGRES_DB=varuna_dev -v postgres_data:/var/lib/postgresql/data postgres:15-alpine

# Terminal 2: Start Redis with Docker
docker run --name varuna-redis -p 6379:6379 redis:7-alpine

# Terminal 3: Run Backend
cd /Users/gabriel/Documents/varuna/backend

# Activate virtual environment
source venv/bin/activate

# Install missing dependencies if needed
pip install python-decouple django-cors-headers redis psycopg2-binary

# Create migrations (already done)
python manage.py makemigrations dashboard

# Run migrations
export DJANGO_SETTINGS_MODULE=varuna.settings
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=varuna_dev
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=postgres
export REDIS_URL=redis://localhost:6379/0
export DEBUG=True
export SECRET_KEY=dev-secret-key

python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Start Django development server
python manage.py runserver 0.0.0.0:8000
```

Backend will be available at: http://localhost:8000

### Option 2: Frontend (Vue 3 + Vuetify)

```bash
# Terminal: Start Frontend
cd /Users/gabriel/Documents/varuna/frontend

# Start Vite dev server
npm run dev
```

Frontend will be available at: http://localhost:3000 (with API proxy to backend)

### Option 3: Full Docker (If File System Issue Resolved)

```bash
# Start all services with Docker Compose
cd /Users/gabriel/Documents/varuna

# Build and start services
docker-compose -f docker-compose.dev.yml up -d --build

# View logs
docker-compose -f docker-compose.dev.yml logs -f
```

---

## 📊 Project Stats

- **Backend Files**: 30+
- **Frontend Files**: 12+
- **Docker Configs**: 10+
- **Total Lines of Code**: 2500+
- **Migrations Created**: Yes (dashboard/0001_initial.py)
- **Dependencies Installed**: Yes (frontend)
- **Estimated Completion**:
  - Phase 1: ✅ 100%
  - Phase 2: 100%
  - Phase 3: 100%
  - Phase 4: 0%
  - Phase 5: 50%
  - Phase 6: 0%

---

## 📝 Next Immediate Actions

1. **Begin Phase 4**: Implement power refresh backend service and endpoints
2. **Phase 5 Backend**: Add `/api/offline-onus/` with filters and sorting
3. **Wire Offline View**: Point `OfflineONUs.vue` to new backend endpoint

---

## 🔧 Troubleshooting

### Django Migrations
- **Error**: "Apps aren't loaded yet"
- **Fix**: Removed model imports from `dashboard/__init__.py`, used explicit imports in `admin.py`

### Docker Dev Compose
- **Symptom**: `/api/*` returns 404/500 when using Apache + mod_wsgi
- **Fix**: Run Django dev server in `docker-compose.dev.yml` and proxy Vite to `http://web:8000`

### PostgreSQL Connection
- **Error**: "Connection refused on localhost:5432"
- **Fix**: Use `docker run` command in Option 1 or use Docker Compose

### Redis Import
- **Error**: "Import redis could not be resolved"
- **Fix**: Use `from redis import Redis` instead of `import redis` in cache_service.py

---

## 📌 Notes

- **Language**: All code is in English, comments are bilingual (English/Portuguese)
- **Database**: PostgreSQL 15
- **Cache**: Redis 7
- **Python**: 3.14
- **Vue**: 3.5.27
- **Timezone**: America/Sao_Paulo
- **Locale**: pt-BR

---

**Status**: Phase 3 COMPLETE - READY FOR PHASE 4 🚀
