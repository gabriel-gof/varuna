import logging
import time
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from topology.models import OLT, ONU
from topology.services.olt_health_service import mark_olt_reachable, mark_olt_unreachable
from topology.services.power_service import power_service
from topology.services.snmp_service import snmp_service


logger = logging.getLogger(__name__)

SYS_DESCR_OID = '1.3.6.1.2.1.1.1.0'


def _is_power_due(olt, now):
    if olt.next_power_at:
        return olt.next_power_at <= now
    if olt.last_power_at:
        interval_seconds = max(int(olt.power_interval_seconds or 0), 1)
        return (olt.last_power_at + timedelta(seconds=interval_seconds)) <= now
    return True


def _collect_power_for_olt(olt):
    onus = list(
        ONU.objects.filter(olt=olt, is_active=True)
        .select_related('olt', 'olt__vendor_profile')
        .order_by('slot_id', 'pon_id', 'onu_id')
    )
    if not onus:
        return
    result_map = power_service.refresh_for_onus(onus, force_refresh=True)
    collected = sum(
        1 for row in result_map.values()
        if row.get('onu_rx_power') is not None or row.get('olt_rx_power') is not None
    )
    now = timezone.now()
    next_at = now + timedelta(seconds=olt.power_interval_seconds or 0)
    OLT.objects.filter(id=olt.id).update(last_power_at=now, next_power_at=next_at)
    logger.info(
        "scheduler: power collected for OLT %s (%s/%s ONUs with readings).",
        olt.id, collected, len(onus),
    )


class Command(BaseCommand):
    help = "Long-lived scheduler that dispatches polling, discovery, power collection, and SNMP checks."

    def add_arguments(self, parser):
        parser.add_argument(
            '--tick-seconds', type=int, default=30,
            help='Seconds between scheduler ticks (default: 30)',
        )
        parser.add_argument(
            '--snmp-check-seconds', type=int, default=180,
            help='Seconds between SNMP reachability checks (default: 180)',
        )

    def handle(self, *args, **options):
        tick_seconds = max(5, options['tick_seconds'])
        snmp_check_seconds = max(30, options['snmp_check_seconds'])
        last_snmp_check_at = 0

        logger.info(
            "scheduler: starting (tick=%ss, snmp_check=%ss).",
            tick_seconds, snmp_check_seconds,
        )
        self.stdout.write(
            f"Scheduler started (tick={tick_seconds}s, snmp_check={snmp_check_seconds}s)."
        )

        while True:
            try:
                close_old_connections()
                now_mono = time.monotonic()
                if now_mono - last_snmp_check_at >= snmp_check_seconds:
                    self._run_snmp_checks()
                    last_snmp_check_at = now_mono
                self._tick()
            except KeyboardInterrupt:
                logger.info("scheduler: shutting down.")
                self.stdout.write("Scheduler stopped.")
                break
            except Exception:
                logger.exception("scheduler: tick error.")

            time.sleep(tick_seconds)

    def _tick(self):
        logger.debug("scheduler: tick.")

        output = StringIO()
        try:
            call_command('poll_onu_status', stdout=output)
        except Exception:
            logger.exception("scheduler: poll_onu_status failed.")
        poll_output = output.getvalue().strip()
        if poll_output:
            logger.info("scheduler: poll_onu_status: %s", poll_output)

        output = StringIO()
        try:
            call_command('discover_onus', stdout=output)
        except Exception:
            logger.exception("scheduler: discover_onus failed.")
        discover_output = output.getvalue().strip()
        if discover_output:
            logger.info("scheduler: discover_onus: %s", discover_output)

        now = timezone.now()
        try:
            power_olts = list(
                OLT.objects.filter(
                    is_active=True,
                    vendor_profile__is_active=True,
                ).select_related('vendor_profile')
            )
            for olt in power_olts:
                if olt.snmp_reachable is False and (olt.snmp_failure_count or 0) >= 2:
                    continue
                if _is_power_due(olt, now):
                    try:
                        _collect_power_for_olt(olt)
                    except Exception:
                        logger.exception("scheduler: power collection failed for OLT %s.", olt.id)
        except Exception:
            logger.exception("scheduler: power collection query failed.")

    def _run_snmp_checks(self):
        try:
            olts = list(
                OLT.objects.filter(is_active=True)
                .select_related('vendor_profile')
            )
        except Exception:
            logger.exception("scheduler: failed to query OLTs for SNMP checks.")
            return

        for olt in olts:
            try:
                result = snmp_service.get(olt, [SYS_DESCR_OID])
                if result and SYS_DESCR_OID in result:
                    mark_olt_reachable(olt)
                else:
                    mark_olt_unreachable(olt, error='No sysDescr response')
            except Exception as exc:
                mark_olt_unreachable(olt, error=str(exc)[:500])
                logger.warning("scheduler: SNMP check failed for OLT %s: %s", olt.id, exc)
