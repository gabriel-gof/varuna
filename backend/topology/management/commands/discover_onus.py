import logging
import re
import time
from contextlib import nullcontext
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from topology.models import OLT, OLTSlot, OLTPON, ONU
from topology.services.olt_health_service import mark_olt_reachable, mark_olt_unreachable
from topology.services.snmp_service import snmp_service
from topology.services.vendor_profile import map_status_code, parse_onu_index


logger = logging.getLogger(__name__)


def _extract_index(oid: str, base_oid: str) -> Optional[str]:
    if not oid or not base_oid:
        return None
    prefix = f"{base_oid}."
    if oid.startswith(prefix):
        return oid[len(prefix):]
    return None


def _rows_to_index_map(rows: list, base_oid: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for row in rows:
        oid = row.get("oid")
        value = row.get("value")
        index = _extract_index(oid, base_oid)
        if index is None:
            continue
        values[index] = "" if value is None else str(value).strip()
    return values


_SERIAL_SENTINEL_VALUES = frozenset({"N/A", "NA", "NONE", "NULL", "--", "-"})


def _decode_hex_serial(hex_str: str) -> Optional[str]:
    """Decode hex-encoded serial (e.g. '0X434D535A3B0699E9' → 'CMSZ3B0699E9').

    Huawei returns ONU serials as raw hex where the first 4 bytes are the
    ASCII vendor ID and the remaining bytes are the serial number.
    """
    body = hex_str[2:]  # strip '0X'
    if len(body) < 10 or len(body) % 2 != 0:
        return None
    if not all(c in '0123456789ABCDEF' for c in body):
        return None
    try:
        vendor_bytes = bytes.fromhex(body[:8])
        if all(0x20 <= b <= 0x7E for b in vendor_bytes):
            return vendor_bytes.decode('ascii') + body[8:].upper()
    except (ValueError, UnicodeDecodeError):
        pass
    return None


def _normalize_serial(raw: str) -> str:
    if not raw:
        return ""
    if "," in raw:
        raw = raw.split(",", 1)[1]
    normalized = raw.strip().upper()
    if normalized in _SERIAL_SENTINEL_VALUES:
        return ""
    if normalized.startswith('0X'):
        decoded = _decode_hex_serial(normalized)
        if decoded:
            return decoded
    return normalized


def _parse_non_negative_int(value, default: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default
    return max(parsed, 0)


def _parse_optional_non_negative_int(value) -> Optional[int]:
    if value in (None, ''):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return max(parsed, 0)


def _slot_key(identity: Dict[str, Any]) -> str:
    rack_id = identity.get("rack_id")
    shelf_id = identity.get("shelf_id")
    if rack_id is not None and shelf_id is not None:
        return f"{rack_id}/{shelf_id}"
    return str(identity["slot_id"])


def _pon_key(identity: Dict[str, Any]) -> str:
    rack_id = identity.get("rack_id")
    shelf_id = identity.get("shelf_id")
    port_id = identity.get("port_id")
    if rack_id is not None and shelf_id is not None and port_id is not None:
        return f"{rack_id}/{shelf_id}/{port_id}"
    return f"{identity['slot_id']}/{identity['pon_id']}"


class Command(BaseCommand):
    help = "Discover ONUs on OLTs using SNMP OID templates."

    def add_arguments(self, parser):
        parser.add_argument("--olt-id", type=int, help="Run discovery for a specific OLT id")
        parser.add_argument("--dry-run", action="store_true", help="Run without writing to the database")
        parser.add_argument("--force", action="store_true", help="Ignore discovery_enabled for the selected OLT(s)")

    def _is_due(self, olt: OLT, now) -> bool:
        if olt.next_discovery_at:
            return olt.next_discovery_at <= now
        if olt.last_discovery_at:
            interval_minutes = max(int(olt.discovery_interval_minutes or 0), 1)
            return (olt.last_discovery_at + timedelta(minutes=interval_minutes)) <= now
        return True

    def handle(self, *args, **options):
        force = bool(options.get("force", False))
        if force:
            olt_qs = OLT.objects.filter(
                is_active=True,
                vendor_profile__is_active=True,
            ).select_related("vendor_profile")
        else:
            olt_qs = OLT.objects.filter(
                is_active=True,
                discovery_enabled=True,
                vendor_profile__is_active=True,
            ).select_related("vendor_profile")

        olt_id = options.get("olt_id")
        if olt_id:
            olt_qs = olt_qs.filter(id=olt_id)

        if not olt_qs.exists():
            self.stdout.write("No OLTs eligible for discovery.")
            return

        now = timezone.now()
        olts = list(olt_qs)
        if not force and not olt_id:
            due_olts = [olt for olt in olts if self._is_due(olt, now)]
        else:
            due_olts = olts

        if not due_olts:
            self.stdout.write("No OLTs due for discovery.")
            return

        for olt in due_olts:
            self._discover_for_olt(olt, dry_run=options.get("dry_run", False))

    def _discover_for_olt(self, olt: OLT, dry_run: bool = False) -> None:
        oid_templates = olt.vendor_profile.oid_templates or {}
        discovery_cfg = oid_templates.get("discovery", {})
        status_cfg = oid_templates.get("status", {})
        indexing_cfg = oid_templates.get("indexing", {})

        configured_disable_lost_after_minutes = _parse_non_negative_int(
            discovery_cfg.get('disable_lost_after_minutes', discovery_cfg.get('keep_lost_minutes', 0)),
            default=0,
        )
        # Product policy: missing ONUs must leave active topology immediately after a discovery pass.
        disable_lost_after_minutes = 0
        if configured_disable_lost_after_minutes > 0:
            logger.info(
                "OLT %s discovery config disable_lost_after_minutes=%s is ignored; global policy is immediate deactivation.",
                olt.id,
                configured_disable_lost_after_minutes,
            )
        delete_lost_after_minutes = _parse_optional_non_negative_int(discovery_cfg.get('delete_lost_after_minutes'))
        if (
            delete_lost_after_minutes is not None
            and delete_lost_after_minutes > 0
            and delete_lost_after_minutes <= disable_lost_after_minutes
        ):
            logger.warning(
                "OLT %s has delete_lost_after_minutes (%s) <= disable_lost_after_minutes (%s); clamping delete window.",
                olt.id,
                delete_lost_after_minutes,
                disable_lost_after_minutes,
            )
            delete_lost_after_minutes = disable_lost_after_minutes + 1

        name_oid = discovery_cfg.get("onu_name_oid")
        serial_oid = discovery_cfg.get("onu_serial_oid")
        status_oid = discovery_cfg.get("onu_status_oid") or status_cfg.get("onu_status_oid")
        status_map = status_cfg.get("status_map", {})

        if not name_oid or not serial_oid:
            self._mark_discovery_result(olt, success=False, dry_run=dry_run)
            logger.warning("OLT %s missing discovery OIDs", olt.id)
            self.stdout.write(f"OLT {olt.id} missing discovery OIDs, skipping.")
            return

        slot_cache: Dict[str, OLTSlot] = {}
        pon_cache: Dict[tuple, OLTPON] = {}

        seen_slot_ids: Set[int] = set()
        seen_pon_ids: Set[int] = set()
        seen_onu_ids: Set[int] = set()

        def ensure_slot(identity: Dict[str, Any]) -> OLTSlot:
            key = _slot_key(identity)
            slot = slot_cache.get(key)
            if slot:
                seen_slot_ids.add(slot.id)
                return slot
            slot, _ = OLTSlot.objects.update_or_create(
                olt=olt,
                slot_key=key,
                defaults={
                    "slot_id": identity["slot_id"],
                    "rack_id": identity.get("rack_id"),
                    "shelf_id": identity.get("shelf_id"),
                    "is_active": True,
                },
            )
            slot_cache[key] = slot
            seen_slot_ids.add(slot.id)
            return slot

        def ensure_pon(identity: Dict[str, Any], slot: OLTSlot, pon_name: str = "") -> OLTPON:
            cache_key = (slot.id, identity["pon_id"])
            pon = pon_cache.get(cache_key)
            if pon:
                seen_pon_ids.add(pon.id)
                return pon
            pon, _ = OLTPON.objects.update_or_create(
                slot=slot,
                pon_id=identity["pon_id"],
                defaults={
                    "olt": olt,
                    "pon_key": _pon_key(identity),
                    "pon_index": identity.get("pon_numeric"),
                    "rack_id": identity.get("rack_id"),
                    "shelf_id": identity.get("shelf_id"),
                    "port_id": identity.get("port_id"),
                    "name": pon_name,
                    "is_active": True,
                },
            )
            pon_cache[cache_key] = pon
            seen_pon_ids.add(pon.id)
            return pon

        interfaces_cfg = oid_templates.get("pon_interfaces", {})
        iface_rows_total = 0
        pon_map: Dict[int, Dict[str, Any]] = {}

        if interfaces_cfg and not dry_run:
            iface_name_oid = interfaces_cfg.get("name_oid")
            if iface_name_oid:
                iface_status_oid = interfaces_cfg.get("status_oid")
                name_regex = interfaces_cfg.get("name_regex", r"^gpon_(\d+)/(\d+)/(\d+)$")
                status_up = str(interfaces_cfg.get("status_up", "1"))
                regex = re.compile(name_regex)

                name_rows = snmp_service.walk(olt, iface_name_oid)
                status_rows = snmp_service.walk(olt, iface_status_oid) if iface_status_oid else []
                iface_rows_total = len(name_rows) + len(status_rows)
                names = _rows_to_index_map(name_rows, iface_name_oid)
                statuses = _rows_to_index_map(status_rows, iface_status_oid) if iface_status_oid else {}

                for index, iface_name in names.items():
                    if not iface_name:
                        continue
                    match = regex.match(iface_name)
                    if not match:
                        continue
                    if iface_status_oid and statuses.get(index) != status_up:
                        continue
                    try:
                        rack_id = int(match.group(1))
                        shelf_id = int(match.group(2))
                        port_id = int(match.group(3))
                    except (IndexError, ValueError):
                        continue
                    slot_from = indexing_cfg.get("slot_from", "shelf")
                    pon_from = indexing_cfg.get("pon_from", "port")
                    location = {"rack": rack_id, "shelf": shelf_id, "port": port_id}
                    identity = {
                        "slot_id": location.get(slot_from, shelf_id),
                        "pon_id": location.get(pon_from, port_id),
                        "pon_numeric": None,
                        "rack_id": rack_id,
                        "shelf_id": shelf_id,
                        "port_id": port_id,
                    }
                    pon_map[int(index)] = {
                        'slot_id': location.get(slot_from, shelf_id),
                        'pon_id': location.get(pon_from, port_id),
                        'rack_id': rack_id,
                        'shelf_id': shelf_id,
                        'port_id': port_id,
                    }
                    slot = ensure_slot(identity)
                    ensure_pon(identity, slot, pon_name=iface_name)

        pause_between_walks = float(discovery_cfg.get('pause_between_walks_seconds', 0.5))
        pause_between_walks = max(0.0, min(pause_between_walks, 5.0))

        walk_timeout = float(discovery_cfg.get('walk_timeout_seconds', 30.0))
        walk_timeout = max(5.0, min(walk_timeout, 120.0))

        name_rows = snmp_service.walk(olt, name_oid, timeout=walk_timeout)
        if pause_between_walks > 0:
            time.sleep(pause_between_walks)
        serial_rows = snmp_service.walk(olt, serial_oid, timeout=walk_timeout)
        if pause_between_walks > 0 and status_oid:
            time.sleep(pause_between_walks)
        status_rows = snmp_service.walk(olt, status_oid, timeout=walk_timeout) if status_oid else []

        snmp_returned = bool(name_rows or serial_rows or status_rows or iface_rows_total)
        if not snmp_returned:
            if not dry_run:
                mark_olt_unreachable(olt, error='No SNMP discovery data returned')
            self._mark_discovery_result(olt, success=False, dry_run=dry_run)
            self.stdout.write(f"OLT {olt.id}: no SNMP discovery data returned.")
            logger.warning("OLT %s discovery: no SNMP data returned", olt.id)
            return

        names = _rows_to_index_map(name_rows, name_oid)
        serials = _rows_to_index_map(serial_rows, serial_oid)
        statuses = _rows_to_index_map(status_rows, status_oid) if status_oid else {}

        raw_indices = set(names.keys()) | set(serials.keys())
        indices = set()
        ghost_count = 0
        for idx in raw_indices:
            name_val = names.get(idx, "").strip()
            serial_val = serials.get(idx, "").strip()
            if not name_val and not serial_val:
                ghost_count += 1
                continue
            indices.add(idx)
        if ghost_count:
            logger.info(
                "OLT %s discovery: filtered %d ghost indices (empty name and serial)",
                olt.id, ghost_count,
            )
        created = updated = skipped = 0

        # Partial walk deactivation guard
        active_count = ONU.objects.filter(olt=olt, is_active=True).count()
        min_safe_ratio = float(discovery_cfg.get('min_safe_ratio', 0.3))
        min_safe_ratio = max(0.0, min(min_safe_ratio, 1.0))
        skip_deactivation = False
        if active_count > 0 and len(indices) < active_count * min_safe_ratio:
            logger.critical(
                "OLT %s discovery returned %s ONUs but %s are active (%.0f%%). "
                "Skipping deactivation to avoid mass false removal.",
                olt.id,
                len(indices),
                active_count,
                (len(indices) / active_count) * 100 if active_count else 0,
            )
            skip_deactivation = True

        # Parse all discovered ONUs into a list
        parsed_onus: List[Dict[str, Any]] = []
        for index in sorted(indices):
            identity = parse_onu_index(index, indexing_cfg, pon_map=pon_map)
            if not identity:
                skipped += 1
                continue
            name = names.get(index, "").strip()
            serial = _normalize_serial(serials.get(index, ""))
            status_code = statuses.get(index)
            mapped = map_status_code(status_code, status_map)
            parsed_onus.append({
                "index": index,
                "identity": identity,
                "name": name,
                "serial": serial,
                "status": mapped["status"],
            })

        write_context = transaction.atomic() if not dry_run else nullcontext()
        with write_context:
            if dry_run:
                for entry in parsed_onus:
                    identity = entry["identity"]
                    exists = ONU.objects.filter(
                        olt=olt,
                        slot_id=identity["slot_id"],
                        pon_id=identity["pon_id"],
                        onu_id=identity["onu_id"],
                    ).exists()
                    if exists:
                        updated += 1
                    else:
                        created += 1
            else:
                # Ensure slots and PONs for all discovered ONUs (cached, few upserts)
                for entry in parsed_onus:
                    identity = entry["identity"]
                    slot = ensure_slot(identity)
                    pon = ensure_pon(identity, slot)
                    entry["_slot"] = slot
                    entry["_pon"] = pon

                # Fetch all existing ONUs for this OLT in one query
                existing_onus_qs = ONU.objects.filter(olt=olt).values_list(
                    "id", "slot_id", "pon_id", "onu_id", "serial",
                )
                existing_lookup: Dict[tuple, Dict[str, Any]] = {}
                for onu_id, s_id, p_id, o_id, existing_serial in existing_onus_qs:
                    existing_lookup[(s_id, p_id, o_id)] = {
                        "id": onu_id,
                        "serial": existing_serial or "",
                    }

                to_create: List[ONU] = []
                to_update: List[ONU] = []
                for entry in parsed_onus:
                    identity = entry["identity"]
                    key = (identity["slot_id"], identity["pon_id"], identity["onu_id"])
                    existing = existing_lookup.get(key)

                    serial = entry["serial"]
                    if existing:
                        # Preserve previously discovered serial on partial serial-walk gaps
                        if not serial:
                            serial = existing["serial"]
                        onu = ONU(
                            id=existing["id"],
                            olt=olt,
                            slot_id=identity["slot_id"],
                            pon_id=identity["pon_id"],
                            onu_id=identity["onu_id"],
                            snmp_index=entry["index"],
                            name=entry["name"],
                            serial=serial,
                            status=entry["status"],
                            slot_ref=entry["_slot"],
                            pon_ref=entry["_pon"],
                            is_active=True,
                        )
                        to_update.append(onu)
                        seen_onu_ids.add(existing["id"])
                        updated += 1
                    else:
                        onu = ONU(
                            olt=olt,
                            slot_id=identity["slot_id"],
                            pon_id=identity["pon_id"],
                            onu_id=identity["onu_id"],
                            snmp_index=entry["index"],
                            name=entry["name"],
                            serial=serial,
                            status=entry["status"],
                            slot_ref=entry["_slot"],
                            pon_ref=entry["_pon"],
                            is_active=True,
                        )
                        to_create.append(onu)
                        created += 1

                if to_create:
                    created_onus = ONU.objects.bulk_create(to_create)
                    for onu in created_onus:
                        seen_onu_ids.add(onu.id)
                if to_update:
                    ONU.objects.bulk_update(
                        to_update,
                        ["snmp_index", "name", "serial", "status", "slot_ref", "pon_ref", "is_active"],
                        batch_size=500,
                    )

            deactivate_missing = bool(discovery_cfg.get('deactivate_missing', True))
            stale_onus = stale_slots = stale_pons = 0
            waiting_onus = waiting_slots = waiting_pons = 0
            deleted_onus = 0
            if not dry_run and deactivate_missing and not skip_deactivation:
                now = timezone.now()
                disable_cutoff = now - timedelta(minutes=disable_lost_after_minutes)

                stale_onus_qs = ONU.objects.filter(olt=olt, is_active=True)
                if seen_onu_ids:
                    stale_onus_qs = stale_onus_qs.exclude(id__in=seen_onu_ids)
                if disable_lost_after_minutes > 0:
                    waiting_onus = stale_onus_qs.filter(last_discovered_at__gt=disable_cutoff).count()
                    stale_onus_qs = stale_onus_qs.filter(last_discovered_at__lte=disable_cutoff)
                stale_onus = stale_onus_qs.update(is_active=False, status=ONU.STATUS_UNKNOWN)

                stale_pons_qs = OLTPON.objects.filter(olt=olt, is_active=True)
                if seen_pon_ids:
                    stale_pons_qs = stale_pons_qs.exclude(id__in=seen_pon_ids)
                if disable_lost_after_minutes > 0:
                    waiting_pons = stale_pons_qs.filter(last_discovered_at__gt=disable_cutoff).count()
                    stale_pons_qs = stale_pons_qs.filter(last_discovered_at__lte=disable_cutoff)
                stale_pons = stale_pons_qs.update(is_active=False)

                stale_slots_qs = OLTSlot.objects.filter(olt=olt, is_active=True)
                if seen_slot_ids:
                    stale_slots_qs = stale_slots_qs.exclude(id__in=seen_slot_ids)
                if disable_lost_after_minutes > 0:
                    waiting_slots = stale_slots_qs.filter(last_discovered_at__gt=disable_cutoff).count()
                    stale_slots_qs = stale_slots_qs.filter(last_discovered_at__lte=disable_cutoff)
                stale_slots = stale_slots_qs.update(is_active=False)

                if delete_lost_after_minutes is not None and delete_lost_after_minutes > 0:
                    delete_cutoff = now - timedelta(minutes=delete_lost_after_minutes)
                    delete_onus_qs = ONU.objects.filter(
                        olt=olt,
                        is_active=False,
                        last_discovered_at__lte=delete_cutoff,
                    )
                    if seen_onu_ids:
                        delete_onus_qs = delete_onus_qs.exclude(id__in=seen_onu_ids)
                    deleted_onus = delete_onus_qs.count()
                    if deleted_onus:
                        delete_onus_qs.delete()

        if not dry_run:
            mark_olt_reachable(olt)

        self._mark_discovery_result(olt, success=True, dry_run=dry_run)
        self.stdout.write(
            f"OLT {olt.id}: discovered {len(indices)} ONUs "
            f"(created={created}, updated={updated}, skipped={skipped})."
        )
        if not dry_run:
            logger.info(
                "OLT %s discovery: total=%s created=%s updated=%s skipped=%s stale_onus=%s stale_pons=%s stale_slots=%s",
                olt.id,
                len(indices),
                created,
                updated,
                skipped,
                stale_onus,
                stale_pons,
                stale_slots,
            )
            if deactivate_missing:
                logger.info(
                    "OLT %s lost-resource policy: disable_after=%sm delete_after=%s; waiting(onu=%s pon=%s slot=%s) deleted_onus=%s",
                    olt.id,
                    disable_lost_after_minutes,
                    f"{delete_lost_after_minutes}m" if delete_lost_after_minutes is not None else "never",
                    waiting_onus,
                    waiting_pons,
                    waiting_slots,
                    deleted_onus,
                )

    def _mark_discovery_result(self, olt: OLT, success: bool, dry_run: bool) -> None:
        if dry_run:
            return
        now = timezone.now()
        next_at = now + timedelta(minutes=olt.discovery_interval_minutes or 0)
        olt.last_discovery_at = now
        olt.next_discovery_at = next_at
        olt.discovery_healthy = success
        olt.save(update_fields=["last_discovery_at", "next_discovery_at", "discovery_healthy"])
