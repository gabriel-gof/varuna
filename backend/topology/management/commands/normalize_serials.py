import logging

from django.core.management.base import BaseCommand

from topology.management.commands.discover_onus import _normalize_serial
from topology.models import OLT, ONU


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Normalize malformed ONU serials in-place (fixes Huawei hex-encoded serials that were stored before recovery logic existed)."

    def add_arguments(self, parser):
        parser.add_argument("--olt-id", type=int, help="Limit to a specific OLT id")
        parser.add_argument("--dry-run", action="store_true", help="Report changes without writing to the database")

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run", False))
        olt_id = options.get("olt_id")

        onu_qs = ONU.objects.filter(is_active=True).exclude(serial="").exclude(serial__isnull=True)
        if olt_id:
            onu_qs = onu_qs.filter(olt_id=olt_id)

        onus = list(onu_qs.only("id", "olt_id", "serial"))
        fixed = []
        for onu in onus:
            normalized = _normalize_serial(onu.serial)
            if normalized != onu.serial:
                fixed.append((onu, onu.serial, normalized))

        if not fixed:
            self.stdout.write("No serials need normalization.")
            return

        if dry_run:
            self.stdout.write(f"Dry-run: {len(fixed)} serial(s) would be normalized:")
            for onu, old, new in fixed:
                self.stdout.write(f"  ONU {onu.id} (OLT {onu.olt_id}): {old!r} → {new!r}")
            return

        to_update = []
        for onu, old, new in fixed:
            onu.serial = new
            to_update.append(onu)

        ONU.objects.bulk_update(to_update, ["serial"], batch_size=500)
        self.stdout.write(f"Normalized {len(fixed)} serial(s):")
        for onu, old, new in fixed:
            self.stdout.write(f"  ONU {onu.id} (OLT {onu.olt_id}): {old!r} → {new!r}")
        logger.info("normalize_serials: updated %d ONU serial(s)", len(fixed))
