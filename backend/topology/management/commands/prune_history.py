from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from topology.models import ONULog, ONUPowerSample


def _positive_int_or_default(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)


class Command(BaseCommand):
    help = "Prune historical power samples and resolved ONU alarm logs."

    def add_arguments(self, parser):
        parser.add_argument(
            '--power-days',
            type=int,
            help='Retention window in days for ONU power history.',
        )
        parser.add_argument(
            '--alarm-days',
            type=int,
            help='Retention window in days for resolved ONU alarm history.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only report what would be deleted.',
        )

    def handle(self, *args, **options):
        power_days = _positive_int_or_default(
            options.get('power_days'),
            getattr(settings, 'POWER_HISTORY_RETENTION_DAYS', 30),
        )
        alarm_days = _positive_int_or_default(
            options.get('alarm_days'),
            getattr(settings, 'ALARM_HISTORY_RETENTION_DAYS', 90),
        )
        dry_run = bool(options.get('dry_run', False))

        now = timezone.now()
        power_cutoff = now - timedelta(days=power_days)
        alarm_cutoff = now - timedelta(days=alarm_days)

        power_qs = ONUPowerSample.objects.filter(read_at__lt=power_cutoff)
        alarm_qs = ONULog.objects.filter(
            offline_until__isnull=False,
            offline_until__lt=alarm_cutoff,
        )

        power_count = power_qs.count()
        alarm_count = alarm_qs.count()

        if dry_run:
            self.stdout.write(
                "History prune dry-run: "
                f"power_samples={power_count} alarms={alarm_count} "
                f"(power_days={power_days}, alarm_days={alarm_days})"
            )
            return

        deleted_power = 0
        deleted_alarm = 0
        if power_count:
            deleted_power, _ = power_qs.delete()
        if alarm_count:
            deleted_alarm, _ = alarm_qs.delete()

        self.stdout.write(
            "History prune completed: "
            f"power_samples={deleted_power} alarms={deleted_alarm} "
            f"(power_days={power_days}, alarm_days={alarm_days})"
        )
