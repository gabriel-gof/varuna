import logging
from datetime import timedelta
from typing import Dict, Iterator, List, Optional

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from dashboard.models import OLT, ONU, ONULog
from dashboard.services.cache_service import cache_service
from dashboard.services.olt_health_service import mark_olt_reachable, mark_olt_unreachable
from dashboard.services.snmp_service import snmp_service
from dashboard.services.vendor_profile import map_status_code


logger = logging.getLogger(__name__)


def _extract_index(oid: str, base_oid: str) -> Optional[str]:
    if not oid or not base_oid:
        return None
    prefix = f"{base_oid}."
    if oid.startswith(prefix):
        return oid[len(prefix):]
    return None


def _chunked(values: List[str], size: int) -> Iterator[List[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


class Command(BaseCommand):
    help = "Poll ONU status via SNMP and update status/logs."

    def add_arguments(self, parser):
        parser.add_argument("--olt-id", type=int, help="Run polling for a specific OLT id")
        parser.add_argument("--dry-run", action="store_true", help="Run without writing to the database")
        parser.add_argument("--force", action="store_true", help="Ignore polling_enabled for the selected OLT(s)")

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

        onus = list(ONU.objects.filter(olt=olt, is_active=True))
        status_oids = [f"{status_oid}.{onu.snmp_index}" for onu in onus if onu.snmp_index]
        if not status_oids:
            self.stdout.write(f"OLT {olt.id}: no active ONUs with SNMP index.")
            return

        statuses: Dict[str, str] = {}
        chunk_size = int(status_cfg.get("get_chunk_size", 20))
        failed_chunks = 0

        for chunk in _chunked(status_oids, max(chunk_size, 1)):
            response = snmp_service.get(olt, chunk)
            if not response:
                failed_chunks += 1
                continue
            for oid, value in response.items():
                index = _extract_index(oid, status_oid)
                if not index:
                    continue
                statuses[index] = "" if value is None else str(value).strip()

        now = timezone.now()
        ttl = getattr(settings, "STATUS_CACHE_TTL", 180)

        if not statuses:
            if not dry_run:
                mark_olt_unreachable(
                    olt,
                    error=f"No status data returned (failed_chunks={failed_chunks}, requested={len(status_oids)})",
                )
                self._mark_poll_result(olt, now)
            self.stdout.write(f"OLT {olt.id}: no status data returned.")
            return

        if not dry_run:
            mark_olt_reachable(olt)

        open_logs_by_onu: Dict[int, ONULog] = {}
        open_logs = ONULog.objects.filter(
            onu__olt=olt,
            onu__is_active=True,
            offline_until__isnull=True,
        ).order_by('-offline_since')
        for log in open_logs:
            open_logs_by_onu.setdefault(log.onu_id, log)

        updated = online = offline = unknown = missing = 0
        onus_to_update: List[ONU] = []
        logs_to_close: List[ONULog] = []
        logs_to_reason_update: List[ONULog] = []
        new_logs: List[ONULog] = []

        for onu in onus:
            status_code = statuses.get(onu.snmp_index)
            if status_code is None:
                missing += 1
                if dry_run:
                    continue
                if onu.status != ONU.STATUS_UNKNOWN:
                    onu.status = ONU.STATUS_UNKNOWN
                    onus_to_update.append(onu)
                cache_service.set_onu_status(
                    olt.id,
                    onu.id,
                    {
                        "status": ONU.STATUS_UNKNOWN,
                        "disconnect_reason": ONULog.REASON_UNKNOWN,
                        "offline_since": "",
                    },
                    ttl=ttl,
                )
                continue

            mapped = map_status_code(status_code, status_map)
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

            open_log = open_logs_by_onu.get(onu.id)
            active_log = open_log

            if new_status == ONU.STATUS_ONLINE:
                if open_log and open_log.offline_until is None:
                    open_log.offline_until = now
                    logs_to_close.append(open_log)
                active_log = None
            elif new_status == ONU.STATUS_OFFLINE:
                if onu.status == ONU.STATUS_ONLINE or not open_log:
                    active_log = ONULog(
                        onu=onu,
                        offline_since=now,
                        disconnect_reason=reason or ONULog.REASON_UNKNOWN,
                    )
                    new_logs.append(active_log)
                    open_logs_by_onu[onu.id] = active_log
                elif reason and open_log.disconnect_reason != reason:
                    open_log.disconnect_reason = reason
                    logs_to_reason_update.append(open_log)
            else:
                # Keep existing offline log untouched when state is unknown.
                active_log = open_log

            if onu.status != new_status:
                onu.status = new_status
                onus_to_update.append(onu)

            offline_since = ""
            if new_status == ONU.STATUS_OFFLINE and active_log:
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
            with transaction.atomic():
                if new_logs:
                    ONULog.objects.bulk_create(new_logs)
                if logs_to_close:
                    ONULog.objects.bulk_update(logs_to_close, ['offline_until'])
                if logs_to_reason_update:
                    ONULog.objects.bulk_update(logs_to_reason_update, ['disconnect_reason'])
                if onus_to_update:
                    ONU.objects.bulk_update(onus_to_update, ['status'])
                self._mark_poll_result(olt, now)

        self.stdout.write(
            f"OLT {olt.id}: polled {updated} ONUs "
            f"(online={online}, offline={offline}, unknown={unknown}, missing={missing})."
        )

    def _mark_poll_result(self, olt: OLT, now):
        next_at = now + timedelta(seconds=olt.polling_interval_seconds or 0)
        olt.last_poll_at = now
        olt.next_poll_at = next_at
        olt.save(update_fields=['last_poll_at', 'next_poll_at'])
