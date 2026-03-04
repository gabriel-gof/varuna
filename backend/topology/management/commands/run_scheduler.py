import logging
import time
from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from topology.models import OLT, ONUPowerSample
from topology.services.maintenance_runtime import collect_power_for_olt as collect_power_runtime
from topology.services.olt_health_service import mark_olt_reachable, mark_olt_unreachable
from topology.services.zabbix_service import zabbix_service


logger = logging.getLogger(__name__)

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
    # Keep collector checks at a fixed cadence so recovery after VPN/network
    # return is detected quickly and does not stay gray for long windows.
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
    payload = collect_power_runtime(
        olt,
        force_refresh=True,
        include_results=False,
        history_source=ONUPowerSample.SOURCE_SCHEDULER,
    )
    logger.info(
        "scheduler: power collected for OLT %s (collected=%s/%s stored=%s).",
        olt.id,
        payload.get('collected_count', 0),
        payload.get('count', 0),
        payload.get('stored_count', 0),
    )


class Command(BaseCommand):
    help = "Long-lived scheduler that dispatches polling, discovery, power collection, and collector checks."

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
            '--collector-check-seconds', '--snmp-check-seconds',
            dest='collector_check_seconds',
            type=int,
            default=int(getattr(settings, 'COLLECTOR_CHECK_SECONDS', 30) or 30),
            help='Seconds between collector reachability checks (default from COLLECTOR_CHECK_SECONDS, fallback: 30)',
        )
        parser.add_argument(
            '--collector-check-max-backoff-seconds', '--snmp-check-max-backoff-seconds',
            dest='collector_check_max_backoff_seconds',
            type=int,
            default=int(getattr(settings, 'COLLECTOR_CHECK_MAX_BACKOFF_SECONDS', 1800) or 1800),
            help='Maximum collector check backoff for unreachable OLTs (default from COLLECTOR_CHECK_MAX_BACKOFF_SECONDS, fallback: 1800)',
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
        parser.add_argument(
            '--history-prune-seconds',
            type=int,
            default=int(getattr(settings, 'HISTORY_PRUNE_INTERVAL_SECONDS', 21600) or 21600),
            help='Seconds between history prune runs (default from HISTORY_PRUNE_INTERVAL_SECONDS)',
        )

    def handle(self, *args, **options):
        tick_seconds = max(5, options['tick_seconds'])
        collector_check_seconds = max(30, options['collector_check_seconds'])
        collector_check_max_backoff_seconds = max(
            collector_check_seconds,
            options['collector_check_max_backoff_seconds'],
        )
        history_prune_seconds = _optional_positive_int(options.get('history_prune_seconds'))
        self.max_poll_olts_per_tick = _optional_positive_int(options.get('max_poll_olts_per_tick'))
        self.max_discovery_olts_per_tick = _optional_positive_int(options.get('max_discovery_olts_per_tick'))
        self.max_power_olts_per_tick = _optional_positive_int(options.get('max_power_olts_per_tick'))
        last_collector_check_at = 0
        last_history_prune_at = 0

        logger.info(
            "scheduler: starting (tick=%ss, collector_check=%ss, collector_check_max_backoff=%ss, history_prune=%ss, max_poll=%s, max_discovery=%s, max_power=%s).",
            tick_seconds,
            collector_check_seconds,
            collector_check_max_backoff_seconds,
            history_prune_seconds,
            self.max_poll_olts_per_tick,
            self.max_discovery_olts_per_tick,
            self.max_power_olts_per_tick,
        )
        self.stdout.write(
            "Scheduler started "
            f"(tick={tick_seconds}s, collector_check={collector_check_seconds}s, "
            f"collector_check_max_backoff={collector_check_max_backoff_seconds}s, "
            f"history_prune={history_prune_seconds or 'off'}s, "
            f"max_poll={self.max_poll_olts_per_tick or 'all'}, "
            f"max_discovery={self.max_discovery_olts_per_tick or 'all'}, "
            f"max_power={self.max_power_olts_per_tick or 'all'})."
        )

        while True:
            try:
                close_old_connections()
                now_mono = time.monotonic()
                if now_mono - last_collector_check_at >= collector_check_seconds:
                    self._run_snmp_checks(
                        base_interval_seconds=collector_check_seconds,
                        max_backoff_seconds=collector_check_max_backoff_seconds,
                    )
                    last_collector_check_at = now_mono
                if history_prune_seconds and (now_mono - last_history_prune_at >= history_prune_seconds):
                    self._run_history_prune()
                    last_history_prune_at = now_mono
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
            logger.exception("scheduler: failed to query OLTs for collector checks.")
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
            logger.debug("scheduler: collector checks skipped; no OLT is due.")
            return

        total_started = time.monotonic()
        reachable_count = 0
        unreachable_count = 0
        for olt in due_olts:
            started = time.monotonic()
            try:
                was_unreachable = bool(olt.snmp_reachable is False)
                reachable, detail = zabbix_service.check_olt_reachability(
                    olt,
                    freshness_seconds=max(int(olt.polling_interval_seconds or 0) + 90, 390),
                )
                if reachable:
                    mark_olt_reachable(olt)
                    if was_unreachable:
                        OLT.objects.filter(id=olt.id).update(next_poll_at=timezone.now())
                    reachable_count += 1
                else:
                    mark_olt_unreachable(olt, error=detail or "Zabbix reported OLT unreachable")
                    unreachable_count += 1
            except Exception as exc:
                mark_olt_unreachable(olt, error=str(exc)[:500])
                unreachable_count += 1
                logger.warning("scheduler: collector check failed for OLT %s: %s", olt.id, exc)
            logger.debug("scheduler: collector check for OLT %s completed in %.2fs.", olt.id, time.monotonic() - started)

        logger.info(
            "scheduler: collector check summary checked=%s skipped_not_due=%s reachable=%s unreachable=%s elapsed=%.2fs.",
            len(due_olts),
            len(olts) - len(due_olts),
            reachable_count,
            unreachable_count,
            time.monotonic() - total_started,
        )
        self.stdout.write(
            "scheduler: collector check summary "
            f"checked={len(due_olts)} skipped_not_due={len(olts) - len(due_olts)} "
            f"reachable={reachable_count} unreachable={unreachable_count} "
            f"elapsed={time.monotonic() - total_started:.2f}s."
        )

    def _run_history_prune(self):
        output = StringIO()
        started = time.monotonic()
        try:
            call_command('prune_history', stdout=output)
        except Exception:
            logger.exception("scheduler: prune_history failed.")
            return

        elapsed = time.monotonic() - started
        summary = output.getvalue().strip()
        if summary:
            logger.info("scheduler: prune_history (%.2fs): %s", elapsed, summary)
            self.stdout.write(f"scheduler: prune_history ({elapsed:.2f}s): {summary}")
