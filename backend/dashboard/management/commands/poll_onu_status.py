import logging
from datetime import timedelta
from typing import Dict, Optional, Iterator, List

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from dashboard.models import OLT, ONU, ONULog
from dashboard.services.cache_service import cache_service
from dashboard.services.snmp_service import snmp_service


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


def _chunked(values: List[str], size: int) -> Iterator[List[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _map_status(status_code: Optional[str], status_map: Dict[str, dict]) -> Dict[str, str]:
    if not status_code:
        return {"status": ONU.STATUS_UNKNOWN, "reason": ONULog.REASON_UNKNOWN}
    info = status_map.get(str(status_code), {})
    status = info.get("status", ONU.STATUS_UNKNOWN)
    reason = info.get("reason", ONULog.REASON_UNKNOWN)
    if status == ONU.STATUS_ONLINE:
        reason = ""
    return {"status": status, "reason": reason}


class Command(BaseCommand):
    help = "Poll ONU status via SNMP and update status/logs."

    def add_arguments(self, parser):
        parser.add_argument("--olt-id", type=int, help="Run polling for a specific OLT id")
        parser.add_argument("--dry-run", action="store_true", help="Run without writing to the database")

    def handle(self, *args, **options):
        olt_qs = OLT.objects.filter(
            is_active=True,
            polling_enabled=True,
            vendor_profile__is_active=True,
        ).select_related("vendor_profile")

        olt_id = options.get("olt_id")
        if olt_id:
            olt_qs = olt_qs.filter(id=olt_id)

        if not olt_qs.exists():
            self.stdout.write("No OLTs eligible for polling.")
            return

        for olt in olt_qs:
            self._poll_for_olt(olt, dry_run=options.get("dry_run", False))

    def _poll_for_olt(self, olt: OLT, dry_run: bool = False) -> None:
        oid_templates = olt.vendor_profile.oid_templates or {}
        status_cfg = oid_templates.get("status", {})
        status_oid = status_cfg.get("onu_status_oid")
        status_map = status_cfg.get("status_map", {})

        if not status_oid:
            self.stdout.write(f"OLT {olt.id} missing status OID, skipping.")
            return

        onus = list(ONU.objects.filter(olt=olt))
        status_oids = [f"{status_oid}.{onu.snmp_index}" for onu in onus if onu.snmp_index]
        if not status_oids:
            self.stdout.write(f"OLT {olt.id}: no ONUs with SNMP index.")
            return

        statuses: Dict[str, str] = {}
        chunk_size = int(status_cfg.get("get_chunk_size", 20))
        for chunk in _chunked(status_oids, max(chunk_size, 1)):
            response = snmp_service.get(olt, chunk)
            if not response:
                continue
            for oid, value in response.items():
                index = _extract_index(oid, status_oid)
                if not index:
                    continue
                statuses[index] = "" if value is None else str(value).strip()

        if not statuses:
            self.stdout.write(f"OLT {olt.id}: no status data returned.")
            return

        now = timezone.now()
        ttl = getattr(settings, "STATUS_CACHE_TTL", 180)

        updated = online = offline = unknown = missing = 0

        for onu in onus:
            status_code = statuses.get(onu.snmp_index)
            if status_code is None:
                missing += 1
                continue

            mapped = _map_status(status_code, status_map)
            new_status = mapped["status"]
            reason = mapped["reason"] if new_status != ONU.STATUS_ONLINE else ""

            if new_status == ONU.STATUS_ONLINE:
                online += 1
            elif new_status == ONU.STATUS_OFFLINE:
                offline += 1
            else:
                unknown += 1

            if dry_run:
                updated += 1
                continue

            current_online = onu.status == ONU.STATUS_ONLINE
            open_log = ONULog.objects.filter(onu=onu, offline_until__isnull=True).order_by("-offline_since").first()
            active_log = open_log

            if new_status == ONU.STATUS_ONLINE:
                if open_log:
                    open_log.offline_until = now
                    open_log.save(update_fields=["offline_until"])
                active_log = None
            else:
                if current_online or not open_log:
                    active_log = ONULog.objects.create(
                        onu=onu,
                        offline_since=now,
                        disconnect_reason=reason or ONULog.REASON_UNKNOWN,
                    )
                elif reason and open_log and open_log.disconnect_reason != reason:
                    open_log.disconnect_reason = reason
                    open_log.save(update_fields=["disconnect_reason"])

            if onu.status != new_status:
                onu.status = new_status
                onu.save(update_fields=["status"])

            offline_since = ""
            if new_status != ONU.STATUS_ONLINE and active_log:
                offline_since = active_log.offline_since.isoformat()

            cache_service.set_onu_status(
                olt.id,
                onu.id,
                {
                    "status": new_status,
                    "disconnect_reason": reason,
                    "offline_since": offline_since,
                },
                ttl=ttl,
            )

            updated += 1

        if not dry_run:
            next_at = now + timedelta(seconds=olt.polling_interval_seconds or 0)
            olt.last_poll_at = now
            olt.next_poll_at = next_at
            olt.save(update_fields=["last_poll_at", "next_poll_at"])

        self.stdout.write(
            f"OLT {olt.id}: polled {updated} ONUs "
            f"(online={online}, offline={offline}, unknown={unknown}, missing={missing})."
        )
