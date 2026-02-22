import logging
import re
from contextlib import nullcontext
from datetime import timedelta
from typing import Any, Dict, Optional, Set

from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
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


def _normalize_serial(raw: str) -> str:
    if not raw:
        return ""
    if "," in raw:
        raw = raw.split(",", 1)[1]
    return raw.strip()


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

        for olt in olt_qs:
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
                    identity = {
                        "slot_id": shelf_id,
                        "pon_id": port_id,
                        "pon_numeric": None,
                        "rack_id": rack_id,
                        "shelf_id": shelf_id,
                        "port_id": port_id,
                    }
                    slot = ensure_slot(identity)
                    ensure_pon(identity, slot, pon_name=iface_name)

        name_rows = snmp_service.walk(olt, name_oid)
        serial_rows = snmp_service.walk(olt, serial_oid)
        status_rows = snmp_service.walk(olt, status_oid) if status_oid else []

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

        indices = set(names.keys()) | set(serials.keys())
        created = updated = skipped = 0

        write_context = transaction.atomic() if not dry_run else nullcontext()
        with write_context:
            for index in sorted(indices):
                identity = parse_onu_index(index, indexing_cfg)
                if not identity:
                    skipped += 1
                    continue

                slot = None
                pon = None
                if not dry_run:
                    slot = ensure_slot(identity)
                    pon = ensure_pon(identity, slot)

                name = names.get(index, "").strip()
                serial = _normalize_serial(serials.get(index, ""))

                status_code = statuses.get(index)
                mapped = map_status_code(status_code, status_map)
                new_status = mapped["status"]

                if dry_run:
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
                    continue

                try:
                    onu, was_created = ONU.objects.update_or_create(
                        olt=olt,
                        slot_id=identity["slot_id"],
                        pon_id=identity["pon_id"],
                        onu_id=identity["onu_id"],
                        defaults={
                            "snmp_index": index,
                            "name": name,
                            "serial": serial,
                            "status": new_status,
                            "slot_ref": slot,
                            "pon_ref": pon,
                            "is_active": True,
                        },
                    )
                except IntegrityError as exc:
                    logger.warning("ONU upsert failed for index %s on OLT %s: %s", index, olt.id, exc)
                    skipped += 1
                    continue

                seen_onu_ids.add(onu.id)
                if was_created:
                    created += 1
                else:
                    updated += 1

            deactivate_missing = bool(discovery_cfg.get('deactivate_missing', True))
            stale_onus = stale_slots = stale_pons = 0
            waiting_onus = waiting_slots = waiting_pons = 0
            deleted_onus = 0
            if not dry_run and deactivate_missing:
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
