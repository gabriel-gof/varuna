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


def _optional_positive_int(value):
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _is_power_due(olt, now):
    if olt.next_power_at:
        return olt.next_power_at <= now
    if olt.last_power_at:
        interval_seconds = max(int(olt.power_interval_seconds or 0), 1)
        return (olt.last_power_at + timedelta(seconds=interval_seconds)) <= now
    return True


def _snmp_check_interval_seconds(olt, base_interval_seconds: int, max_backoff_seconds: int) -> int:
    failures = max(int(olt.snmp_failure_count or 0), 0)
    if olt.snmp_reachable is False and failures > 0:
        # Exponential backoff for consistently unreachable OLTs.
        multiplier = 2 ** min(max(failures - 1, 0), 5)
        return min(max_backoff_seconds, max(base_interval_seconds * multiplier, base_interval_seconds))
    return base_interval_seconds


def _is_snmp_check_due(olt, now, base_interval_seconds: int, max_backoff_seconds: int) -> bool:
    if not olt.last_snmp_check_at:
        return True
    interval_seconds = _snmp_check_interval_seconds(
        olt,
        base_interval_seconds=base_interval_seconds,
        max_backoff_seconds=max_backoff_seconds,
    )
    return (olt.last_snmp_check_at + timedelta(seconds=interval_seconds)) <= now


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_poll_olts_per_tick = None
        self.max_discovery_olts_per_tick = None
        self.max_power_olts_per_tick = None

    def add_arguments(self, parser):
        parser.add_argument(
            '--tick-seconds', type=int, default=30,
            help='Seconds between scheduler ticks (default: 30)',
        )
        parser.add_argument(
            '--snmp-check-seconds', type=int, default=180,
            help='Seconds between SNMP reachability checks (default: 180)',
        )
        parser.add_argument(
            '--snmp-check-max-backoff-seconds', type=int, default=1800,
            help='Maximum SNMP check backoff for unreachable OLTs (default: 1800)',
        )
        parser.add_argument(
            '--max-poll-olts-per-tick',
            type=int,
            default=0,
            help='Optional cap of due OLTs processed by poll_onu_status per scheduler tick',
        )
        parser.add_argument(
            '--max-discovery-olts-per-tick',
            type=int,
            default=0,
            help='Optional cap of due OLTs processed by discover_onus per scheduler tick',
        )
        parser.add_argument(
            '--max-power-olts-per-tick',
            type=int,
            default=0,
            help='Optional cap of due OLTs processed by power collection per scheduler tick',
        )

    def handle(self, *args, **options):
        tick_seconds = max(5, options['tick_seconds'])
        snmp_check_seconds = max(30, options['snmp_check_seconds'])
        snmp_check_max_backoff_seconds = max(snmp_check_seconds, options['snmp_check_max_backoff_seconds'])
        self.max_poll_olts_per_tick = _optional_positive_int(options.get('max_poll_olts_per_tick'))
        self.max_discovery_olts_per_tick = _optional_positive_int(options.get('max_discovery_olts_per_tick'))
        self.max_power_olts_per_tick = _optional_positive_int(options.get('max_power_olts_per_tick'))
        last_snmp_check_at = 0

        logger.info(
            "scheduler: starting (tick=%ss, snmp_check=%ss, snmp_check_max_backoff=%ss, max_poll=%s, max_discovery=%s, max_power=%s).",
            tick_seconds,
            snmp_check_seconds,
            snmp_check_max_backoff_seconds,
            self.max_poll_olts_per_tick,
            self.max_discovery_olts_per_tick,
            self.max_power_olts_per_tick,
        )
        self.stdout.write(
            "Scheduler started "
            f"(tick={tick_seconds}s, snmp_check={snmp_check_seconds}s, "
            f"snmp_check_max_backoff={snmp_check_max_backoff_seconds}s, "
            f"max_poll={self.max_poll_olts_per_tick or 'all'}, "
            f"max_discovery={self.max_discovery_olts_per_tick or 'all'}, "
            f"max_power={self.max_power_olts_per_tick or 'all'})."
        )

        while True:
            try:
                close_old_connections()
                now_mono = time.monotonic()
                if now_mono - last_snmp_check_at >= snmp_check_seconds:
                    self._run_snmp_checks(
                        base_interval_seconds=snmp_check_seconds,
                        max_backoff_seconds=snmp_check_max_backoff_seconds,
                    )
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
        poll_started = time.monotonic()
        try:
            call_kwargs = {'stdout': output}
            if self.max_poll_olts_per_tick:
                call_kwargs['max_olts'] = self.max_poll_olts_per_tick
            call_command('poll_onu_status', **call_kwargs)
        except Exception:
            logger.exception("scheduler: poll_onu_status failed.")
        poll_elapsed = time.monotonic() - poll_started
        poll_output = output.getvalue().strip()
        if poll_output:
            logger.info("scheduler: poll_onu_status (%.2fs): %s", poll_elapsed, poll_output)
            self.stdout.write(f"scheduler: poll_onu_status ({poll_elapsed:.2f}s): {poll_output}")

        output = StringIO()
        discovery_started = time.monotonic()
        try:
            call_kwargs = {'stdout': output}
            if self.max_discovery_olts_per_tick:
                call_kwargs['max_olts'] = self.max_discovery_olts_per_tick
            call_command('discover_onus', **call_kwargs)
        except Exception:
            logger.exception("scheduler: discover_onus failed.")
        discovery_elapsed = time.monotonic() - discovery_started
        discover_output = output.getvalue().strip()
        if discover_output:
            logger.info("scheduler: discover_onus (%.2fs): %s", discovery_elapsed, discover_output)
            self.stdout.write(f"scheduler: discover_onus ({discovery_elapsed:.2f}s): {discover_output}")

        now = timezone.now()
        try:
            power_olts = list(
                OLT.objects.filter(
                    is_active=True,
                    vendor_profile__is_active=True,
                ).select_related('vendor_profile')
            )
            power_due = 0
            power_collected = 0
            power_elapsed_total = 0.0
            due_olts = []
            for olt in power_olts:
                if olt.snmp_reachable is False and (olt.snmp_failure_count or 0) >= 2:
                    continue
                if _is_power_due(olt, now):
                    due_olts.append(olt)

            due_olts.sort(key=lambda item: item.next_power_at or item.last_power_at or now)
            power_due = len(due_olts)
            if self.max_power_olts_per_tick and power_due > self.max_power_olts_per_tick:
                self.stdout.write(
                    "scheduler: capping power collection to "
                    f"{self.max_power_olts_per_tick} OLTs out of {power_due} due."
                )
                due_olts = due_olts[: self.max_power_olts_per_tick]

            for olt in due_olts:
                try:
                    power_started = time.monotonic()
                    _collect_power_for_olt(olt)
                    power_collected += 1
                    power_elapsed_total += time.monotonic() - power_started
                except Exception:
                    logger.exception("scheduler: power collection failed for OLT %s.", olt.id)
            if power_due:
                logger.info(
                    "scheduler: power tick summary due=%s collected=%s elapsed=%.2fs.",
                    power_due,
                    power_collected,
                    power_elapsed_total,
                )
                self.stdout.write(
                    "scheduler: power tick summary "
                    f"due={power_due} collected={power_collected} elapsed={power_elapsed_total:.2f}s."
                )
        except Exception:
            logger.exception("scheduler: power collection query failed.")

    def _run_snmp_checks(self, *, base_interval_seconds: int = 180, max_backoff_seconds: int = 1800):
        try:
            olts = list(
                OLT.objects.filter(is_active=True)
                .select_related('vendor_profile')
            )
        except Exception:
            logger.exception("scheduler: failed to query OLTs for SNMP checks.")
            return

        now = timezone.now()
        due_olts = [
            olt
            for olt in olts
            if _is_snmp_check_due(
                olt,
                now,
                base_interval_seconds=base_interval_seconds,
                max_backoff_seconds=max_backoff_seconds,
            )
        ]
        if not due_olts:
            logger.debug("scheduler: SNMP checks skipped; no OLT is due.")
            return

        total_started = time.monotonic()
        reachable_count = 0
        unreachable_count = 0
        for olt in due_olts:
            started = time.monotonic()
            try:
                result = snmp_service.get(olt, [SYS_DESCR_OID])
                if result and SYS_DESCR_OID in result:
                    mark_olt_reachable(olt)
                    reachable_count += 1
                else:
                    mark_olt_unreachable(olt, error='No sysDescr response')
                    unreachable_count += 1
            except Exception as exc:
                mark_olt_unreachable(olt, error=str(exc)[:500])
                unreachable_count += 1
                logger.warning("scheduler: SNMP check failed for OLT %s: %s", olt.id, exc)
            logger.debug("scheduler: SNMP check for OLT %s completed in %.2fs.", olt.id, time.monotonic() - started)

        logger.info(
            "scheduler: SNMP check summary checked=%s skipped_not_due=%s reachable=%s unreachable=%s elapsed=%.2fs.",
            len(due_olts),
            len(olts) - len(due_olts),
            reachable_count,
            unreachable_count,
            time.monotonic() - total_started,
        )
        self.stdout.write(
            "scheduler: SNMP check summary "
            f"checked={len(due_olts)} skipped_not_due={len(olts) - len(due_olts)} "
            f"reachable={reachable_count} unreachable={unreachable_count} "
            f"elapsed={time.monotonic() - total_started:.2f}s."
        )
