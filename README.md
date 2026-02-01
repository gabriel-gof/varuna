# Varuna

SNMP monitoring tool for FTTH networks with support for multiple OLT vendors.

## Features

- Multi-vendor OLT support (ZTE first)
- SNMP-based discovery and status polling
- Real-time topology display (OLT → Slots → PONs → ONUs)
- ONU status tracking (online/offline)
- Disconnect reason detection (link loss, dying gasp)
- Power level monitoring (OLT RX, ONU RX)
- Offline event logging
- Configurable polling and discovery intervals per OLT

## Getting Started

### Development

```bash
# Backend (Django)
cd backend
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser

# Frontend (Vue 3 + Vuetify)
cd frontend
npm install
npm run dev

# Start development environment
docker-compose -f docker-compose.dev.yml up
```

### Production

```bash
# Build and deploy
docker-compose -f docker-compose.prod.yml up -d --build
```

## API Documentation

### Endpoints

#### OLTs
- `GET /api/olts/` - List all OLTs
- `POST /api/olts/` - Create new OLT
- `GET /api/olts/{id}/` - Get OLT details
- `PUT /api/olts/{id}/` - Update OLT
- `DELETE /api/olts/{id}/` - Delete OLT

#### Topology
- `GET /api/olts/{id}/topology/` - Get complete topology for OLT
- `GET /api/olts/{id}/stats/` - Get OLT statistics

#### ONUs
- `GET /api/onu/` - List ONUs (read-only, filtered)
- `GET /api/onu/{id}/power/` - Get ONU power levels
- `POST /api/onu/batch-power/` - Batch power refresh

#### Vendor Profiles
- `GET /api/vendor-profiles/` - List available vendor profiles

## Color Coding

| Status | Color | Priority |
|--------|--------|----------|
| Link Loss (LOS) | 🔴 Red (#E53935) | High |
| Dying Gasp | 🟠 Orange (#FB8C00) | Medium |
| Unknown | ⚪ Gray (#757575) | Low |
| Online | 🟢 Green (#43A047) | - |

## Technology Stack

- **Backend**: Django 4.2, Django REST Framework, PostgreSQL, Redis
- **Frontend**: Vue 3, Vuetify 3, Pinia, Vue Router, Vite
- **SNMP**: PySNMP for SNMP communication
- **Deployment**: Docker Compose
- **Language**: Portuguese (Brazil) - PT-BR

## License

Private/Internal project.
