"""
Management command to create test data for development and testing.
Creates OLTs, Slots, PONs, and ONUs with various statuses.
"""
import random
from django.core.management.base import BaseCommand
from django.utils import timezone

from dashboard.models import VendorProfile, OLT, OLTSlot, OLTPON, ONU, ONULog


class Command(BaseCommand):
    help = "Create test OLT, Slots, PONs, and ONUs for development"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing test data before creating new data",
        )
        parser.add_argument(
            "--onus-per-pon",
            type=int,
            default=8,
            help="Number of ONUs per PON (default: 8)",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            self.stdout.write("Clearing existing data...")
            ONULog.objects.all().delete()
            ONU.objects.all().delete()
            OLTPON.objects.all().delete()
            OLTSlot.objects.all().delete()
            OLT.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("Cleared all existing data."))

        # Get vendor profile
        vendor_profile = VendorProfile.objects.filter(vendor="zte", is_active=True).first()
        if not vendor_profile:
            self.stdout.write(self.style.ERROR("No ZTE vendor profile found!"))
            return

        onus_per_pon = options["onus_per_pon"]
        
        # Create test OLTs
        olts_data = [
            {"name": "OLT-CENTRO", "ip_address": "10.0.1.1"},
            {"name": "OLT-NORTE", "ip_address": "10.0.2.1"},
        ]

        for olt_data in olts_data:
            olt, created = OLT.objects.get_or_create(
                name=olt_data["name"],
                defaults={
                    "vendor_profile": vendor_profile,
                    "ip_address": olt_data["ip_address"],
                    "snmp_community": "public",
                    "snmp_port": 161,
                    "snmp_version": "2c",
                    "discovery_enabled": True,
                    "polling_enabled": True,
                    "is_active": True,
                }
            )
            
            if created:
                self.stdout.write(f"Created OLT: {olt.name}")
            else:
                self.stdout.write(f"OLT exists: {olt.name}")

            # Create slots (2 slots per OLT)
            for slot_num in range(1, 3):
                slot_key = f"1/{slot_num}"
                slot, _ = OLTSlot.objects.get_or_create(
                    olt=olt,
                    slot_id=slot_num,
                    defaults={
                        "slot_key": slot_key,
                        "name": f"Slot {slot_num}",
                        "rack_id": 1,
                        "shelf_id": slot_num,
                        "is_active": True,
                    }
                )

                # Create PONs (4 PONs per slot)
                for pon_num in range(1, 5):
                    pon_key = f"1/{slot_num}/{pon_num}"
                    # PON index as ZTE encoding: 0x11RRSSPP (rack/shelf/port)
                    pon_index = (0x11 << 24) | (1 << 16) | (slot_num << 8) | pon_num
                    
                    pon, _ = OLTPON.objects.get_or_create(
                        olt=olt,
                        slot=slot,
                        pon_id=pon_num,
                        defaults={
                            "pon_key": pon_key,
                            "pon_index": pon_index,
                            "name": f"PON {slot_num}/{pon_num}",
                            "rack_id": 1,
                            "shelf_id": slot_num,
                            "port_id": pon_num,
                            "is_active": True,
                        }
                    )

                    # Create ONUs
                    for onu_num in range(1, onus_per_pon + 1):
                        # Include OLT ID in snmp_index to make it globally unique
                        snmp_index = f"{olt.id}.{pon_index}.{onu_num}"
                        
                        # Determine status distribution:
                        # 70% online, 15% offline (link_loss), 10% offline (dying_gasp), 5% unknown
                        rand = random.random()
                        if rand < 0.70:
                            status = ONU.STATUS_ONLINE
                            disconnect_reason = None
                        elif rand < 0.85:
                            status = ONU.STATUS_OFFLINE
                            disconnect_reason = ONULog.REASON_LINK_LOSS
                        elif rand < 0.95:
                            status = ONU.STATUS_OFFLINE
                            disconnect_reason = ONULog.REASON_DYING_GASP
                        else:
                            status = ONU.STATUS_UNKNOWN
                            disconnect_reason = ONULog.REASON_UNKNOWN

                        serial = f"ZTEG{random.randint(10000000, 99999999)}"
                        name = f"Cliente_{olt.name.split('-')[1]}_{slot_num}_{pon_num}_{onu_num:02d}"

                        onu, onu_created = ONU.objects.get_or_create(
                            olt=olt,
                            slot_id=slot_num,
                            pon_id=pon_num,
                            onu_id=onu_num,
                            defaults={
                                "slot_ref": slot,
                                "pon_ref": pon,
                                "snmp_index": snmp_index,
                                "name": name,
                                "serial": serial,
                                "status": status,
                            }
                        )

                        if onu_created:
                            # Update status if ONU already existed
                            if onu.status != status:
                                onu.status = status
                                onu.save(update_fields=["status"])
                            
                            # Create ONULog for offline ONUs
                            if status == ONU.STATUS_OFFLINE and disconnect_reason:
                                # Random offline time (1 minute to 2 hours ago)
                                offline_since = timezone.now() - timezone.timedelta(
                                    minutes=random.randint(1, 120)
                                )
                                ONULog.objects.create(
                                    onu=onu,
                                    offline_since=offline_since,
                                    disconnect_reason=disconnect_reason,
                                )

        # Summary
        total_onus = ONU.objects.count()
        online = ONU.objects.filter(status=ONU.STATUS_ONLINE).count()
        offline = ONU.objects.filter(status=ONU.STATUS_OFFLINE).count()
        unknown = ONU.objects.filter(status=ONU.STATUS_UNKNOWN).count()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Test Data Created ==="))
        self.stdout.write(f"OLTs: {OLT.objects.count()}")
        self.stdout.write(f"Slots: {OLTSlot.objects.count()}")
        self.stdout.write(f"PONs: {OLTPON.objects.count()}")
        self.stdout.write(f"ONUs: {total_onus}")
        self.stdout.write(f"  - Online: {online} ({online/total_onus*100:.1f}%)")
        self.stdout.write(f"  - Offline: {offline} ({offline/total_onus*100:.1f}%)")
        self.stdout.write(f"  - Unknown: {unknown} ({unknown/total_onus*100:.1f}%)")
