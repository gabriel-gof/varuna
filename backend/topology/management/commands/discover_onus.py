import logging
import re
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from topology.models import OLT, OLTSlot, OLTPON, ONU, ONULog
from topology.services.cache_service import cache_service
from topology.services.olt_health_service import mark_olt_reachable, mark_olt_unreachable
from topology.services.topology_counter_service import topology_counter_service
from topology.services.unm_service import UNMServiceError, unm_service
from topology.services.vendor_profile import parse_onu_index
from topology.services.zabbix_service import zabbix_service


logger = logging.getLogger(__name__)

_SERIAL_SENTINEL_VALUES = frozenset({"N/A", "NA", "NONE", "NULL", "--", "-"})
_SERIAL_LIKE_RE = re.compile(r"^[A-Z]{4}[A-Z0-9-]{4,28}$")
_GENERIC_SERIAL_LIKE_RE = re.compile(r"^(?=(?:.*\d){4,})[A-Z0-9-]{8,32}$")
_PLACEHOLDER_ONU_NAME_RE = re.compile(r"^\d{1,3}$")


def _is_serial_like(value: str) -> bool:
    return bool(_SERIAL_LIKE_RE.fullmatch(value) or _GENERIC_SERIAL_LIKE_RE.fullmatch(value))


def _decode_hex_serial(hex_str: str) -> Optional[str]:
    body = hex_str[2:]
    if len(body) < 10 or len(body) % 2 != 0:
        return None
    if not all(c in "0123456789ABCDEF" for c in body):
        return None
    try:
        vendor_bytes = bytes.fromhex(body[:8])
        if all(0x20 <= b <= 0x7E for b in vendor_bytes):
            return vendor_bytes.decode("ascii") + body[8:].upper()
    except (ValueError, UnicodeDecodeError):
        return None
    return None


def _recover_mangled_serial(serial: str) -> Optional[str]:
    if len(serial) < 5 or len(serial) > 8:
        return None
    vendor = serial[:4]
    suffix = serial[4:]
    if not vendor.isalpha():
        return None
    if all((c.isascii() and c.isalnum()) or c in "-_" for c in suffix):
        return None
    try:
        raw_bytes = serial.encode("latin-1")
    except UnicodeEncodeError:
        try:
            raw_bytes = serial.encode("utf-8")
        except UnicodeEncodeError:
            return None
    if len(raw_bytes) > 8:
        return None
    if len(raw_bytes) < 8:
        raw_bytes = raw_bytes + b"\x00" * (8 - len(raw_bytes))
    hex_str = "0X" + raw_bytes.hex().upper()
    return _decode_hex_serial(hex_str)


def _normalize_serial_candidate(raw: str, *, strict: bool) -> str:
    normalized = str(raw or "").strip().upper().strip("[](){}").strip(",;:")
    if not normalized:
        return ""
    if normalized in _SERIAL_SENTINEL_VALUES:
        return ""
    if "," in normalized:
        parts = [part.strip() for part in normalized.split(",") if part.strip()]
        if strict:
            for part in parts:
                parsed = _normalize_serial_candidate(part, strict=True)
                if parsed:
                    return parsed
            return ""
        if parts:
            normalized = parts[0]
            if normalized in _SERIAL_SENTINEL_VALUES:
                return ""
    if "=" in normalized:
        _, rhs = normalized.rsplit("=", 1)
        rhs = rhs.strip()
        if rhs:
            rhs_normalized = _normalize_serial_candidate(rhs, strict=strict)
            if rhs_normalized:
                return rhs_normalized
    if normalized.startswith("0X"):
        decoded = _decode_hex_serial(normalized)
        if decoded:
            return decoded
    compact_hex = normalized.replace(" ", "")
    if re.fullmatch(r"[0-9A-F]{10,64}", compact_hex) and len(compact_hex) % 2 == 0:
        decoded = _decode_hex_serial(f"0X{compact_hex}")
        if decoded:
            return decoded
    recovered = _recover_mangled_serial(normalized)
    if recovered:
        return recovered
    if strict and not _is_serial_like(normalized):
        return ""
    if _is_serial_like(normalized):
        return normalized.replace("-", "")
    if re.fullmatch(r"\d{1,4}", normalized):
        return ""
    return normalized


def _normalize_serial(raw: str) -> str:
    if not raw:
        return ""
    raw_value = str(raw).strip()
    if not raw_value:
        return ""
    if "," in raw_value:
        parts = [part.strip() for part in raw_value.split(",") if part.strip()]
        for part in parts:
            parsed = _normalize_serial_candidate(part, strict=True)
            if parsed:
                return parsed
        return _normalize_serial_candidate(raw_value, strict=False)
    return _normalize_serial_candidate(raw_value, strict=False)


def _parse_optional_non_negative_int(value) -> Optional[int]:
    if value in (None, ""):
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
    help = "Discover ONUs on OLTs using Zabbix item templates."

    def add_arguments(self, parser):
        parser.add_argument("--olt-id", type=int, help="Run discovery for a specific OLT id")
        parser.add_argument("--dry-run", action="store_true", help="Run without writing to the database")
        parser.add_argument("--force", action="store_true", help="Ignore discovery_enabled for the selected OLT(s)")
        parser.add_argument(
            "--refresh-upstream",
            action="store_true",
            help="Ask Zabbix to execute ONU discovery item/rule before reading rows.",
        )
        parser.add_argument(
            "--max-olts",
            type=int,
            help="Process at most this many eligible OLTs in one command run",
        )

    def _is_due(self, olt: OLT, now) -> bool:
        if olt.next_discovery_at:
            return olt.next_discovery_at <= now
        if olt.last_discovery_at:
            interval_minutes = max(int(olt.discovery_interval_minutes or 0), 1)
            return (olt.last_discovery_at + timedelta(minutes=interval_minutes)) <= now
        return True

    def _due_at(self, olt: OLT, now):
        if olt.next_discovery_at:
            return olt.next_discovery_at
        if olt.last_discovery_at:
            interval_minutes = max(int(olt.discovery_interval_minutes or 0), 1)
            return olt.last_discovery_at + timedelta(minutes=interval_minutes)
        return now - timedelta(days=36500)

    @staticmethod
    def _resolve_zabbix_discovery_key(olt: OLT) -> str:
        templates = (olt.vendor_profile.oid_templates or {}) if isinstance(olt.vendor_profile.oid_templates, dict) else {}
        zabbix_cfg = templates.get("zabbix", {}) if isinstance(templates.get("zabbix", {}), dict) else {}
        return str(zabbix_cfg.get("discovery_item_key") or "onuDiscovery").strip()

    @staticmethod
    def _discovery_macro(row: Dict[str, Any], name: str) -> str:
        if not isinstance(row, dict):
            return ""
        candidates = [name, name.upper(), name.lower()]
        for candidate in candidates:
            value = row.get(candidate)
            if value not in (None, ""):
                return str(value).strip()
        return ""

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

        now = timezone.now()
        olts = list(olt_qs)
        if not olts:
            self.stdout.write("No OLTs eligible for discovery.")
            return

        if not force and not olt_id:
            due_olts = [
                olt
                for olt in olts
                if self._is_due(olt, now)
                and not (olt.snmp_reachable is False and (olt.snmp_failure_count or 0) >= 2)
            ]
        else:
            due_olts = olts

        if not due_olts:
            self.stdout.write("No OLTs due for discovery.")
            return

        due_olts.sort(key=lambda candidate: self._due_at(candidate, now))
        max_olts = options.get("max_olts")
        if max_olts is not None and int(max_olts) > 0 and len(due_olts) > int(max_olts):
            self.stdout.write(
                f"Capping discovery run to {int(max_olts)} OLTs out of {len(due_olts)} due."
            )
            due_olts = due_olts[: int(max_olts)]

        for olt in due_olts:
            self._discover_for_olt(
                olt,
                dry_run=options.get("dry_run", False),
                refresh_upstream=bool(options.get("refresh_upstream", False)),
            )

    def _discover_for_olt(self, olt: OLT, dry_run: bool = False, refresh_upstream: bool = False) -> None:
        self._discover_for_olt_zabbix(olt, dry_run=dry_run, refresh_upstream=refresh_upstream)

    def _discover_for_olt_zabbix(
        self,
        olt: OLT,
        dry_run: bool = False,
        refresh_upstream: bool = False,
    ) -> None:
        oid_templates = olt.vendor_profile.oid_templates or {}
        indexing_cfg = oid_templates.get("indexing", {})
        discovery_key = self._resolve_zabbix_discovery_key(olt)

        # Auto-trigger upstream LLD on first-ever discovery so newly created
        # OLTs don't have to wait for the Zabbix LLD schedule.
        never_discovered = not ONU.objects.filter(olt=olt).exists()
        if never_discovered and not refresh_upstream:
            refresh_upstream = True

        if refresh_upstream:
            try:
                hostid = zabbix_service.get_hostid(olt)
                if hostid:
                    executed = zabbix_service.execute_item_now_by_key(hostid, discovery_key)
                    logger.info(
                        "Discovery OLT %s: requested immediate Zabbix execution for discovery key %s (executed=%s).",
                        olt.id,
                        discovery_key,
                        executed,
                    )
                    if executed:
                        time.sleep(1.0)
            except Exception:
                logger.exception("Discovery OLT %s: failed to request immediate Zabbix discovery execution.", olt.id)

        wait_seconds = max(0, int(getattr(settings, "ZABBIX_DISCOVERY_REFRESH_WAIT_SECONDS", 15) or 15))
        wait_step_seconds = max(1, int(getattr(settings, "ZABBIX_DISCOVERY_REFRESH_WAIT_STEP_SECONDS", 2) or 2))

        rows: List[Dict[str, Any]] = []
        fetch_error: Optional[Exception] = None
        fetch_attempts = 1
        if refresh_upstream and wait_seconds > 0:
            fetch_attempts = max(1, int(wait_seconds // wait_step_seconds) + 1)

        for attempt in range(fetch_attempts):
            try:
                rows, _ = zabbix_service.fetch_discovery_rows(olt, discovery_key)
                fetch_error = None
            except Exception as exc:
                fetch_error = exc
                rows = []

            if rows:
                break
            if attempt < fetch_attempts - 1:
                time.sleep(wait_step_seconds)

        if fetch_error is not None and not rows:
            self._mark_discovery_result(olt, success=False, dry_run=dry_run)
            if not dry_run:
                mark_olt_unreachable(olt, error=str(fetch_error))
            self.stdout.write(f"OLT {olt.id}: zabbix discovery request failed ({fetch_error}).")
            return

        if not rows:
            self._mark_discovery_result(olt, success=False, dry_run=dry_run)
            self.stdout.write(f"OLT {olt.id}: no Zabbix discovery data returned.")
            return

        normalized_entries: List[Dict[str, Any]] = []
        for row in rows:
            raw_index = self._discovery_macro(row, "{#SNMPINDEX}")
            slot_raw = self._discovery_macro(row, "{#SLOT}")
            pon_raw = self._discovery_macro(row, "{#PON}")
            onu_raw = self._discovery_macro(row, "{#ONU_ID}")
            pon_numeric_raw = self._discovery_macro(row, "{#PON_ID}")

            parsed_slot = _parse_optional_non_negative_int(slot_raw)
            parsed_pon = _parse_optional_non_negative_int(pon_raw)
            parsed_onu = _parse_optional_non_negative_int(onu_raw)
            parsed_pon_numeric = _parse_optional_non_negative_int(pon_numeric_raw)

            if parsed_slot is None or parsed_pon is None or parsed_onu is None:
                if raw_index:
                    parsed = parse_onu_index(raw_index, indexing_cfg)
                    if parsed:
                        parsed_slot = parsed.get("slot_id")
                        parsed_pon = parsed.get("pon_id")
                        parsed_onu = parsed.get("onu_id")
                        parsed_pon_numeric = parsed.get("pon_numeric")
                if parsed_slot is None or parsed_pon is None or parsed_onu is None:
                    continue

            if not raw_index:
                if parsed_pon_numeric is not None and parsed_onu is not None:
                    raw_index = f"{parsed_pon_numeric}.{parsed_onu}"
                elif parsed_pon is not None and parsed_onu is not None:
                    raw_index = f"{parsed_pon}.{parsed_onu}"

            serial_value = _normalize_serial(
                self._discovery_macro(row, "{#SERIAL}") or self._discovery_macro(row, "{#ONU_SERIAL}")
            )
            name_value = self._discovery_macro(row, "{#ONU_NAME}")
            normalized_entries.append(
                {
                    "slot_id": int(parsed_slot),
                    "pon_id": int(parsed_pon),
                    "pon_index": int(parsed_pon_numeric) if parsed_pon_numeric is not None else None,
                    "onu_id": int(parsed_onu),
                    "snmp_index": raw_index,
                    "name": name_value,
                    "serial": serial_value,
                }
            )

        unm_name_hits = 0
        if normalized_entries and unm_service.is_enabled_for_olt(olt):
            try:
                unm_inventory_map = unm_service.fetch_onu_inventory_map(olt)
            except UNMServiceError as exc:
                logger.warning("Discovery OLT %s: UNM inventory enrichment skipped (%s).", olt.id, exc)
            except Exception:
                logger.exception("Discovery OLT %s: unexpected UNM inventory enrichment failure.", olt.id)
            else:
                for entry in normalized_entries:
                    unm_row = unm_inventory_map.get(
                        (int(entry["slot_id"]), int(entry["pon_id"]), int(entry["onu_id"]))
                    )
                    if not unm_row:
                        continue
                    unm_name = str(unm_row.get("name") or "").strip()
                    if unm_name:
                        if entry.get("name") != unm_name:
                            entry["name"] = unm_name
                        unm_name_hits += 1
                    unm_serial = str(unm_row.get("serial") or "").strip()
                    if unm_serial and not entry.get("serial"):
                        entry["serial"] = unm_serial

        if not normalized_entries:
            self._mark_discovery_result(olt, success=False, dry_run=dry_run)
            if not dry_run:
                mark_olt_unreachable(olt, error="Zabbix discovery returned no parseable ONU entries")
            self.stdout.write(f"OLT {olt.id}: no parseable ONU entries in Zabbix discovery payload.")
            return

        created = 0
        updated = 0
        seen_onu_keys: Set[Tuple[int, int, int]] = set()
        seen_slot_keys: Set[str] = set()
        seen_pon_keys: Set[Tuple[str, str]] = set()
        now = timezone.now()

        if not dry_run:
            slot_specs: Dict[str, Dict[str, Any]] = {}
            pon_specs: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for entry in normalized_entries:
                slot_identity = {
                    "slot_id": entry["slot_id"],
                    "rack_id": None,
                    "shelf_id": None,
                }
                slot_key = _slot_key(slot_identity)
                seen_slot_keys.add(slot_key)
                slot_specs.setdefault(slot_key, slot_identity)

                pon_identity = {
                    "slot_id": entry["slot_id"],
                    "pon_id": entry["pon_id"],
                    "rack_id": None,
                    "shelf_id": None,
                    "port_id": None,
                }
                pon_key = _pon_key(pon_identity)
                seen_pon_keys.add((slot_key, pon_key))
                pon_specs.setdefault(
                    (slot_key, pon_key),
                    {
                        "slot_key": slot_key,
                        "pon_id": entry["pon_id"],
                        "pon_index": entry["pon_index"],
                        "rack_id": None,
                        "shelf_id": None,
                        "port_id": None,
                        "pon_key": pon_key,
                    },
                )

            with transaction.atomic():
                slot_map: Dict[str, OLTSlot] = {
                    slot.slot_key: slot for slot in OLTSlot.objects.filter(olt=olt)
                }
                slots_to_create: List[OLTSlot] = []
                slots_to_update: List[OLTSlot] = []
                for slot_key, slot_identity in slot_specs.items():
                    slot_obj = slot_map.get(slot_key)
                    if slot_obj is None:
                        slots_to_create.append(
                            OLTSlot(
                                olt=olt,
                                slot_id=slot_identity["slot_id"],
                                rack_id=slot_identity["rack_id"],
                                shelf_id=slot_identity["shelf_id"],
                                slot_key=slot_key,
                                is_active=True,
                            )
                        )
                        continue
                    if not slot_obj.is_active:
                        slot_obj.is_active = True
                        slot_obj.last_discovered_at = now
                        slots_to_update.append(slot_obj)
                if slots_to_create:
                    OLTSlot.objects.bulk_create(slots_to_create)
                if slots_to_update:
                    OLTSlot.objects.bulk_update(slots_to_update, ["is_active", "last_discovered_at"])
                slot_map = {
                    slot.slot_key: slot for slot in OLTSlot.objects.filter(olt=olt)
                }

                existing_pons = list(
                    OLTPON.objects.filter(olt=olt)
                    .select_related("slot")
                    .order_by("-is_active", "-last_discovered_at", "-id")
                )
                description_by_pon_index: Dict[int, str] = {}
                description_by_pon_key: Dict[str, str] = {}
                description_by_slot_pon: Dict[Tuple[int, int], str] = {}
                for existing_pon in existing_pons:
                    description = str(existing_pon.description or "").strip()
                    if not description:
                        continue
                    if existing_pon.pon_index is not None and existing_pon.pon_index not in description_by_pon_index:
                        description_by_pon_index[int(existing_pon.pon_index)] = description
                    if existing_pon.pon_key and existing_pon.pon_key not in description_by_pon_key:
                        description_by_pon_key[str(existing_pon.pon_key)] = description
                    slot_pon_key = (int(existing_pon.slot.slot_id), int(existing_pon.pon_id))
                    if slot_pon_key not in description_by_slot_pon:
                        description_by_slot_pon[slot_pon_key] = description

                pon_map: Dict[Tuple[str, str], OLTPON] = {
                    (pon.slot.slot_key, pon.pon_key): pon
                    for pon in existing_pons
                }
                pons_to_create: List[OLTPON] = []
                pons_to_update: List[OLTPON] = []
                for pon_lookup, pon_spec in pon_specs.items():
                    slot_key = pon_spec["slot_key"]
                    slot_obj = slot_map[slot_key]
                    pon_obj = pon_map.get(pon_lookup)
                    if pon_obj is None:
                        inherited_description = ""
                        if pon_spec["pon_index"] is not None:
                            inherited_description = description_by_pon_index.get(int(pon_spec["pon_index"]), "")
                        if not inherited_description:
                            inherited_description = description_by_pon_key.get(str(pon_spec["pon_key"]), "")
                        if not inherited_description:
                            inherited_description = description_by_slot_pon.get(
                                (int(slot_obj.slot_id), int(pon_spec["pon_id"])),
                                "",
                            )
                        pons_to_create.append(
                            OLTPON(
                                olt=olt,
                                slot=slot_obj,
                                pon_id=pon_spec["pon_id"],
                                pon_index=pon_spec["pon_index"],
                                rack_id=pon_spec["rack_id"],
                                shelf_id=pon_spec["shelf_id"],
                                port_id=pon_spec["port_id"],
                                pon_key=pon_spec["pon_key"],
                                description=inherited_description,
                                is_active=True,
                            )
                        )
                        continue
                    if (not pon_obj.is_active) or pon_obj.slot_id != slot_obj.id:
                        pon_obj.slot = slot_obj
                        pon_obj.is_active = True
                        pon_obj.last_discovered_at = now
                        pons_to_update.append(pon_obj)
                if pons_to_create:
                    OLTPON.objects.bulk_create(pons_to_create)
                if pons_to_update:
                    OLTPON.objects.bulk_update(pons_to_update, ["slot", "is_active", "last_discovered_at"])
                pon_map = {
                    (pon.slot.slot_key, pon.pon_key): pon
                    for pon in OLTPON.objects.filter(olt=olt).select_related("slot")
                }

                onu_map: Dict[Tuple[int, int, int], ONU] = {
                    (onu.slot_id, onu.pon_id, onu.onu_id): onu for onu in ONU.objects.filter(olt=olt)
                }
                onus_to_create: List[ONU] = []
                onus_to_update: List[ONU] = []

                for entry in normalized_entries:
                    slot_key = _slot_key(
                        {
                            "slot_id": entry["slot_id"],
                            "rack_id": None,
                            "shelf_id": None,
                        }
                    )
                    pon_key = _pon_key(
                        {
                            "slot_id": entry["slot_id"],
                            "pon_id": entry["pon_id"],
                            "rack_id": None,
                            "shelf_id": None,
                            "port_id": None,
                        }
                    )
                    slot_obj = slot_map[slot_key]
                    pon_obj = pon_map[(slot_key, pon_key)]

                    onu_key = (entry["slot_id"], entry["pon_id"], entry["onu_id"])
                    seen_onu_keys.add(onu_key)
                    existing = onu_map.get(onu_key)
                    if existing is None:
                        created += 1
                        onus_to_create.append(
                            ONU(
                                olt=olt,
                                slot_ref=slot_obj,
                                pon_ref=pon_obj,
                                slot_id=entry["slot_id"],
                                pon_id=entry["pon_id"],
                                onu_id=entry["onu_id"],
                                snmp_index=entry["snmp_index"] or f"{entry['pon_id']}.{entry['onu_id']}",
                                name=entry["name"] or "",
                                serial=entry["serial"] or "",
                                status=ONU.STATUS_UNKNOWN,
                                is_active=True,
                            )
                        )
                    else:
                        dirty = False
                        if existing.slot_ref_id != slot_obj.id:
                            existing.slot_ref = slot_obj
                            dirty = True
                        if existing.pon_ref_id != pon_obj.id:
                            existing.pon_ref = pon_obj
                            dirty = True
                        if existing.snmp_index != (entry["snmp_index"] or existing.snmp_index):
                            existing.snmp_index = entry["snmp_index"] or existing.snmp_index
                            dirty = True
                        if entry["name"] and existing.name != entry["name"]:
                            existing.name = entry["name"]
                            dirty = True
                        elif (
                            not entry["name"]
                            and existing.name
                            and _PLACEHOLDER_ONU_NAME_RE.fullmatch(existing.name.strip())
                        ):
                            existing.name = ""
                            dirty = True
                        if entry["serial"] and existing.serial != entry["serial"]:
                            existing.serial = entry["serial"]
                            dirty = True
                        if not existing.is_active:
                            existing.is_active = True
                            dirty = True
                        if dirty:
                            updated += 1
                            onus_to_update.append(existing)

                if onus_to_create:
                    ONU.objects.bulk_create(onus_to_create)
                if onus_to_update:
                    ONU.objects.bulk_update(
                        onus_to_update,
                        ["slot_ref", "pon_ref", "snmp_index", "name", "serial", "is_active"],
                    )

                stale_onus = [
                    onu_id
                    for onu_id, slot_id, pon_id, onu_number in ONU.objects.filter(olt=olt, is_active=True).values_list(
                        "id",
                        "slot_id",
                        "pon_id",
                        "onu_id",
                    )
                    if (slot_id, pon_id, onu_number) not in seen_onu_keys
                ]
                if stale_onus:
                    ONU.objects.filter(id__in=stale_onus).update(is_active=False, status=ONU.STATUS_UNKNOWN)
                    ONULog.objects.filter(onu_id__in=stale_onus, offline_until__isnull=True).update(offline_until=now)

                stale_pons_qs = OLTPON.objects.filter(olt=olt, is_active=True)
                if seen_pon_keys:
                    seen_pon_ids = [pon_map[key].id for key in seen_pon_keys if key in pon_map]
                    stale_pons_qs = stale_pons_qs.exclude(id__in=seen_pon_ids)
                stale_pons_qs.update(is_active=False)

                stale_slots_qs = OLTSlot.objects.filter(olt=olt, is_active=True)
                if seen_slot_keys:
                    seen_slot_ids = [slot_map[key].id for key in seen_slot_keys if key in slot_map]
                    stale_slots_qs = stale_slots_qs.exclude(id__in=seen_slot_ids)
                stale_slots_qs.update(is_active=False)

            mark_olt_reachable(olt)
            try:
                topology_counter_service.refresh_olt(olt.id)
            except Exception:
                logger.exception("OLT %s zabbix discovery: failed to refresh cached topology counters.", olt.id)
            cache_service.invalidate_topology_structure_cache(olt.id)

        self._mark_discovery_result(olt, success=True, dry_run=dry_run)
        self.stdout.write(
            f"OLT {olt.id}: discovered {len(normalized_entries)} ONUs via Zabbix "
            f"(created={created}, updated={updated}, unm_name_hits={unm_name_hits})."
        )

    _DISCOVERY_RETRY_MINUTES = 2

    def _mark_discovery_result(self, olt: OLT, success: bool, dry_run: bool) -> None:
        if dry_run:
            return
        now = timezone.now()
        if success:
            retry_minutes = olt.discovery_interval_minutes or 0
        else:
            retry_minutes = min(self._DISCOVERY_RETRY_MINUTES, olt.discovery_interval_minutes or 0)
        next_at = now + timedelta(minutes=retry_minutes)
        olt.last_discovery_at = now
        olt.next_discovery_at = next_at
        olt.discovery_healthy = success
        update_fields = ["last_discovery_at", "next_discovery_at", "discovery_healthy"]

        # Discovery may add/reactivate ONUs with unknown status. Schedule status polling
        # immediately so topology health converges quickly after discovery.
        if success and olt.polling_enabled and (olt.next_poll_at is None or olt.next_poll_at > now):
            olt.next_poll_at = now
            update_fields.append("next_poll_at")

        olt.save(update_fields=update_fields)
