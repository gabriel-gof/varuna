# LLM Context Guide

## What Varuna Is
Varuna is an OLT/ONU monitoring platform focused on topology-first operational visibility, not dashboard-heavy analytics.

## Current Product Decisions
- No dashboard tab in current scope.
- Primary views: topology + settings.
- Unreachable OLTs must be visually gray.

## Core Data/Behavior Rules
- `ONU` is scoped to `OLT`; SNMP index uniqueness is `(olt, snmp_index)`.
- `ONU.is_active` defines whether an ONU is part of current topology.
- Discovery follows lost-resource retention windows (`disable_lost_after_minutes`, `delete_lost_after_minutes`).
- Polling should avoid false offline alarms during transient SNMP gaps.
- OLT freshness is interval-driven (`polling_interval_seconds`); stale topology must be rendered gray.
- Documentation must be updated on every code change (see `/Users/gabriel/Documents/varuna/AGENTS.md`).

## Where to Read First
1. `/Users/gabriel/Documents/varuna/docs/ARCHITECTURE.md`
2. `/Users/gabriel/Documents/varuna/docs/BACKEND.md`
3. `/Users/gabriel/Documents/varuna/docs/FRONTEND.md`
4. `/Users/gabriel/Documents/varuna/backend/dashboard/models/models.py`
5. `/Users/gabriel/Documents/varuna/backend/dashboard/management/commands/discover_onus.py`
6. `/Users/gabriel/Documents/varuna/backend/dashboard/management/commands/poll_onu_status.py`

## Safe Extension Pattern
- Add vendor support by extending `VendorProfile.oid_templates` and validating index/status parsing.
- Keep canonical statuses: `online`, `offline`, `unknown`.
- Add tests for new vendor mapping before rollout.
